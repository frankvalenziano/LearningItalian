#!/usr/bin/env python3
# file: DeepL_Translator.py
# Usage examples:
#   python3 DeepL_Translator.py -i input.csv --mode-vocabulary --translate-to-italian
#   python3 DeepL_Translator.py -i input.csv --mode-sentence   --translate-to-italian
#   python3 DeepL_Translator.py -i input.csv --mode-vocabulary --translate-to-english
#   python3 DeepL_Translator.py -i input.csv --mode-sentence   --translate-to-english
#   # You can enable both vocab+sentence by passing both mode flags:
#   python3 DeepL_Translator.py -i input.csv --mode-vocabulary --mode-sentence
#   # Non-interactive examples (optional flags):
#   python3 DeepL_Translator.py -i input.csv --mode-vocabulary --only-missing --translate-to-italian --api-key $DEEPL_API_KEY

import argparse
import csv
import os
import sys
import time
from typing import Optional, List, Dict
from getpass import getpass

import requests
import os, sys
from getpass import getpass

# --------------------------- Custom Exceptions --------------------------
# Custom exceptions for specific DeepL API errors
class QuotaExceededError(Exception):
    """DeepL quota exhausted (HTTP 456)."""

class RateLimitedError(Exception):
    """DeepL rate limited (HTTP 429)."""

    
# Resolve API key safely for CI/local
def resolve_auth_key(args) -> str:
    if getattr(args, 'api_key', None):
        return args.api_key.strip()
    env_key = os.getenv('DEEPL_API_KEY') or os.getenv('DEEPL_AUTH_KEY')
    if env_key:
        return env_key.strip()
    # In non‑TTY (e.g., GitHub Actions) do not prompt; fail clearly
    if not sys.stdin.isatty():
        sys.exit('DEEPL_API_KEY not found and --api-key not provided. Set it in the workflow env or pass --api-key.')
    return getpass('Enter your DeepL API key: ').strip()


# ------------------------- Configuration --------------------------
# Default to the DeepL Free endpoint. You can override with --url to use the paid endpoint.
DEFAULT_URL = "https://api-free.deepl.com/v2/translate"


# ----------------------------- Translation -----------------------------

def translate(text: str, url: str, auth_key: str, source_lang: str = "EN", target_lang: str = "IT",
              timeout: float = 20.0, retries: int = 3) -> Optional[str]:
    """Translate text via DeepL. Returns None on generic failure.
    Raises QuotaExceededError on HTTP 456 to allow graceful CI handling."""
    if not text:
        return None

    params = {
        "text": text,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "formality": "default",
    }

    delay = 1.5
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, data=params, headers={"Authorization": f"DeepL-Auth-Key {auth_key}"}, timeout=timeout)
            if r.status_code in (400, 403):
                # Some accounts expect auth_key in form body; harmless retry
                r = requests.post(url, data={**params, "auth_key": auth_key}, timeout=timeout)

            if r.status_code == 456:
                # Quota exhausted -> do not retry
                raise QuotaExceededError("DeepL quota exhausted (HTTP 456)")

            if r.status_code == 429:
                # Rate limited -> backoff & retry
                if attempt == retries:
                    raise RateLimitedError("DeepL rate limited (HTTP 429)")
                time.sleep(delay)
                delay *= 1.7
                continue

            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if translations and isinstance(translations, list):
                return translations[0].get("text")
            return None
        except QuotaExceededError:
            raise
        except RateLimitedError:
            raise
        except Exception as e:
            if attempt == retries:
                sys.stderr.write(f"[ERROR] Failed to translate: {e}\n")
                return None
            time.sleep(delay)
            delay *= 1.7


# ------------------------------ Prompts --------------------------------

def prompt_mode() -> str:
    """Prompt the user for translation mode: vocab, sentence, both."""
    print("\nWhat would you like to translate?")
    print("  1) English → Italian Vocabulary (English_Translation → Italian_Term)")
    print("  2) English → Italian Sentence   (English_Sentence → Italian_Sentence)")
    print("  3) Both")
    while True:
        choice = input("Choose 1, 2, or 3: ").strip()
        if choice == "1":
            return "vocab"
        if choice == "2":
            return "sentence"
        if choice == "3":
            return "both"
        print("Invalid choice. Please enter 1, 2, or 3.")


def prompt_overwrite() -> bool:
    """Prompt whether to only update missing values or overwrite existing."""
    print("\nUpdate behavior:")
    print("  1) Only update missing entries")
    print("  2) Overwrite existing entries")
    while True:
        choice = input("Choose 1 or 2: ").strip()
        if choice == "1":
            return False  # don't overwrite
        if choice == "2":
            return True   # overwrite
        print("Invalid choice. Please enter 1 or 2.")


# ------------------------------ Main Logic -----------------------------

def ensure_columns(fieldnames: List[str], needed: List[str]):
    missing = [c for c in needed if c not in fieldnames]
    if missing:
        sys.exit(f"CSV is missing required column(s): {', '.join(missing)}")


def process_rows(rows: List[Dict[str, str]], do_vocab: bool, do_sentence: bool, *,
                 overwrite: bool, url: str, auth_key: str,
                 COL_SRC_TERM: str, COL_DST_TERM: str,
                 COL_SRC_SENT: str, COL_DST_SENT: str,
                 source_lang: str, target_lang: str) -> Dict[str, int]:
    updated_vocab = updated_sent = skipped = 0

    for row in rows:
        # Vocab path
        if do_vocab:
            src = (row.get(COL_SRC_TERM) or "").strip()
            cur_dst = (row.get(COL_DST_TERM) or "").strip()
            if src and (overwrite or not cur_dst):
                tr = translate(src, url=url, auth_key=auth_key, source_lang=source_lang, target_lang=target_lang)
                if tr:
                    row[COL_DST_TERM] = tr
                    updated_vocab += 1
                    print(f"[OK] vocab: {src} -> {tr}")
                else:
                    print(f"[WARN] vocab no translation for: {src}")
            else:
                skipped += 1

        # Sentence path
        if do_sentence:
            src_sent = (row.get(COL_SRC_SENT) or "").strip()
            cur_dst_sent = (row.get(COL_DST_SENT) or "").strip()
            if src_sent and (overwrite or not cur_dst_sent):
                tr = translate(src_sent, url=url, auth_key=auth_key, source_lang=source_lang, target_lang=target_lang)
                if tr:
                    row[COL_DST_SENT] = tr
                    updated_sent += 1
                    short_src = src_sent[:60] + ('...' if len(src_sent)>60 else '')
                    short_tr  = tr[:60] + ('...' if len(tr)>60 else '')
                    print(f"[OK] sentence: {short_src} -> {short_tr}")
                else:
                    preview = src_sent[:80] + ('...' if len(src_sent)>80 else '')
                    print(f"[WARN] sentence no translation for: {preview}")
            else:
                skipped += 1

    return {"updated_vocab": updated_vocab, "updated_sent": updated_sent, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description="Fill Italian translations using DeepL API (prompts for mode and overwrite).")
    ap.add_argument("-i", "--input", required=True, help="Input CSV path")
    ap.add_argument("-o", "--output", help="Output CSV path (default: input basename + .out.csv)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"DeepL endpoint (default: {DEFAULT_URL}; paid: https://api.deepl.com/v2/translate)")
    # Optional non-interactive flags (if omitted, prompts will be shown)
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing values (default: prompt)")
    ap.add_argument("--only-missing", action="store_true", help="Only update missing values (default: prompt)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing file")
    ap.add_argument("--mode-sentence", action="store_true", help="Translate sentences (English_Sentence ↔ Italian_Sentence).")
    ap.add_argument("--mode-vocabulary", action="store_true", help="Translate vocabulary (English_Translation ↔ Italian_Term).")
    ap.add_argument("--translate-to-english", action="store_true", help="Set target to English (source Italian).")
    ap.add_argument("--translate-to-italian", action="store_true", help="Set target to Italian (source English). Default if neither is provided.")
    ap.add_argument("--api-key", dest="api_key", default=None, help="DeepL API key (overrides $DEEPL_API_KEY).")
    ap.add_argument("--soft-fail-on-quota", action="store_true", help="Exit 0 if quota exhausted (HTTP 456) after writing partial progress.")
    args = ap.parse_args()

    in_path = args.input
    if not os.path.exists(in_path):
        sys.exit(f"Input not found: {in_path}")

    out_path = args.output or os.path.splitext(in_path)[0] + ".out.csv"

    # Determine translation direction
    if args.translate_to_english and args.translate_to_italian:
        sys.exit("Specify at most one of --translate-to-english or --translate-to-italian.")
    if args.translate_to_english:
        source_lang, target_lang = "IT", "EN"
        COL_SRC_TERM = "Italian_Term";       COL_DST_TERM = "English_Translation"
        COL_SRC_SENT = "Italian_Sentence";   COL_DST_SENT = "English_Sentence"
    else:
        # default: to Italian
        source_lang, target_lang = "EN", "IT"
        COL_SRC_TERM = "English_Translation"; COL_DST_TERM = "Italian_Term"
        COL_SRC_SENT = "English_Sentence";   COL_DST_SENT = "Italian_Sentence"

    # Determine mode (new flags preferred)
    if args.mode_sentence or args.mode_vocabulary:
        do_sentence = args.mode_sentence
        do_vocab = args.mode_vocabulary
    else:
        mode = prompt_mode()
        do_vocab = mode in ("vocab", "both")
        do_sentence = mode in ("sentence", "both")

    # Determine overwrite behavior (prompt unless one of the flags was given)
    if args.overwrite and args.only_missing:
        sys.exit("Specify at most one of --overwrite or --only-missing.")
    if args.overwrite:
        overwrite = True
    elif args.only_missing:
        overwrite = False
    else:
        overwrite = prompt_overwrite()

    # Get API key interactively (or from env var)
    auth_key = resolve_auth_key(args)

    # Read all rows
    with open(in_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        needed: List[str] = []
        if do_vocab:
            needed += [COL_SRC_TERM, COL_DST_TERM]
        if do_sentence:
            needed += [COL_SRC_SENT, COL_DST_SENT]
        ensure_columns(fieldnames, needed)
        rows = list(reader)

    print(f"[INFO] Direction: {source_lang} -> {target_lang}")
    print(f"[INFO] Using columns — vocab: {COL_SRC_TERM} -> {COL_DST_TERM}; sentences: {COL_SRC_SENT} -> {COL_DST_SENT}")

    # Process
    try:
        stats = process_rows(
            rows, do_vocab, do_sentence,
            overwrite=overwrite, url=args.url, auth_key=auth_key,
            COL_SRC_TERM=COL_SRC_TERM, COL_DST_TERM=COL_DST_TERM,
            COL_SRC_SENT=COL_SRC_SENT, COL_DST_SENT=COL_DST_SENT,
            source_lang=source_lang, target_lang=target_lang,
        )
    except QuotaExceededError as e:
        print(f"[FATAL] {e}. Writing partial progress and exiting.", file=sys.stderr)
        # Write whatever was updated so far
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        soft = args.soft_fail_on_quota or os.getenv("DEEPL_SOFT_FAIL_ON_QUOTA") == "1"
        sys.exit(0 if soft else 2)

    # Dry run summary
    if args.dry_run:
        print("\n[DRY RUN] Summary:")
        if do_vocab:
            print(f"  Vocab updated:   {stats['updated_vocab']}")
        if do_sentence:
            print(f"  Sentences updated:{stats['updated_sent']}")
        print(f"  Skipped rows:     {stats['skipped']}")
        print("No file written.")
        return

    # Write output
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("\nDone.")
    if do_vocab:
        print(f"  Vocab updated:    {stats['updated_vocab']}")
    if do_sentence:
        print(f"  Sentences updated:{stats['updated_sent']}")
    print(f"  Skipped rows:     {stats['skipped']}")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
