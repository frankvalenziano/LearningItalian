#!/usr/bin/env python3
# file: deepl_fill_italian_translations.py
# Usage examples:
#   python3 deepl_fill_italian_translations.py -i "Frank's Core CEFR Word List.csv"
#   python3 deepl_fill_italian_translations.py -i input.csv -o output.csv
#   # Non-interactive examples (optional flags):
#   python3 deepl_fill_italian_translations.py -i input.csv --mode vocab --overwrite
#   python3 deepl_fill_italian_translations.py -i input.csv --mode sentence
#   python3 deepl_fill_italian_translations.py -i input.csv --mode both
#
# Columns supported (case-sensitive):
#   Vocabulary: English_Term  -> Italian_Translation
#   Sentences:  English_Sentence -> Italian_Sentence_Translation
#
# Notes:
# - Talks to DeepL API (free or paid). Does NOT store the API key in the script; it
#   asks for it at runtime (or uses the DEEPL_API_KEY env var if present).
# - By default this script will PROMPT you for (1) what to translate and (2) whether
#   to only fill missing values or overwrite existing ones. You can also pass flags
#   to skip prompts for automation.

import argparse
import csv
import os
import sys
import time
from typing import Optional, List, Dict
from getpass import getpass

import requests

# Default to the DeepL Free endpoint. You can override with --url to use the paid endpoint.
DEFAULT_URL = "https://api-free.deepl.com/v2/translate"


# --------------------------- API Key Handling ---------------------------

def get_deepl_key_interactive() -> str:
    """Return a DeepL API key, preferring env var DEEPL_API_KEY, else prompt securely."""
    key = os.environ.get("DEEPL_API_KEY", "").strip()
    if key:
        return key
    # Prompt without echoing to terminal
    while True:
        key = getpass("Enter your DeepL API key: ").strip()
        if key:
            return key
        print("API key cannot be empty. Please try again.")


# ----------------------------- Translation -----------------------------

def translate(text: str, url: str, auth_key: str, source_lang: str = "EN", target_lang: str = "IT",
              timeout: float = 20.0, retries: int = 3) -> Optional[str]:
    """Translate text via DeepL. Returns None on failure."""
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
                # Fallback to form-data auth_key (rarely needed but harmless)
                r = requests.post(url, data={**params, "auth_key": auth_key}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if translations and isinstance(translations, list):
                return translations[0].get("text")
            return None
        except Exception as e:
            if attempt == retries:
                sys.stderr.write(f"[ERROR] Failed to translate: {e}\n")
                return None
            time.sleep(delay)
            delay *= 1.7  # simple backoff


# ------------------------------ Prompts --------------------------------

def prompt_mode() -> str:
    """Prompt the user for translation mode: vocab, sentence, both."""
    print("\nWhat would you like to translate?")
    print("  1) English → Italian Vocabulary (English_Term → Italian_Translation)")
    print("  2) English → Italian Sentence   (English_Sentence → Italian_Sentence_Translation)")
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
                 overwrite: bool, url: str, auth_key: str) -> Dict[str, int]:
    updated_vocab = updated_sent = skipped = 0

    for row in rows:
        # Vocab path
        if do_vocab:
            eng = (row.get("English_Term") or "").strip()
            cur_it = (row.get("Italian_Translation") or "").strip()
            if eng and (overwrite or not cur_it):
                tr = translate(eng, url=url, auth_key=auth_key)
                if tr:
                    row["Italian_Translation"] = tr
                    updated_vocab += 1
                    print(f"[OK] vocab: {eng} -> {tr}")
                else:
                    print(f"[WARN] vocab no translation for: {eng}")
            else:
                skipped += 1

        # Sentence path
        if do_sentence:
            en_sent = (row.get("English_Sentence") or "").strip()
            cur_it_sent = (row.get("Italian_Sentence_Translation") or "").strip()
            if en_sent and (overwrite or not cur_it_sent):
                tr = translate(en_sent, url=url, auth_key=auth_key)
                if tr:
                    row["Italian_Sentence_Translation"] = tr
                    updated_sent += 1
                    print(f"[OK] sentence: {en_sent[:60]}{'...' if len(en_sent)>60 else ''} -> {tr[:60]}{'...' if len(tr)>60 else ''}")
                else:
                    print(f"[WARN] sentence no translation for: {en_sent[:80]}{'...' if len(en_sent)>80 else ''}")
            else:
                skipped += 1

    return {"updated_vocab": updated_vocab, "updated_sent": updated_sent, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description="Fill Italian translations using DeepL API (prompts for mode and overwrite).")
    ap.add_argument("-i", "--input", required=True, help="Input CSV path")
    ap.add_argument("-o", "--output", help="Output CSV path (default: input basename + .out.csv)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"DeepL endpoint (default: {DEFAULT_URL}; paid: https://api.deepl.com/v2/translate)")
    # Optional non-interactive flags (if omitted, prompts will be shown)
    ap.add_argument("--mode", choices=["vocab", "sentence", "both"], help="What to translate (default: prompt)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing values (default: prompt)")
    ap.add_argument("--only-missing", action="store_true", help="Only update missing values (default: prompt)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing file")
    args = ap.parse_args()

    in_path = args.input
    if not os.path.exists(in_path):
        sys.exit(f"Input not found: {in_path}")

    out_path = args.output or os.path.splitext(in_path)[0] + ".out.csv"

    # Determine mode (prompt if not provided)
    if args.mode:
        mode = args.mode
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
    auth_key = get_deepl_key_interactive()

    # Read all rows
    with open(in_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        needed: List[str] = []
        if do_vocab:
            needed += ["English_Term", "Italian_Translation"]
        if do_sentence:
            needed += ["English_Sentence", "Italian_Sentence_Translation"]
        ensure_columns(fieldnames, needed)

        rows = list(reader)

    # Process
    stats = process_rows(rows, do_vocab, do_sentence, overwrite=overwrite, url=args.url, auth_key=auth_key)

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