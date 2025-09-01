#!/usr/bin/env python3
# file: deepl_fill_italian_translations.py
# Usage:
#   python3 deepl_fill_italian_translations.py -i "Frank's Master A1 CEFR Word List.csv"
#   python3 deepl_fill_italian_translations.py -i input.csv -o output.csv --overwrite
#
# Assumes columns: English_Term, Italian_Translation, Italian_IPA, CEFR_Level, Notes, Tag
# Talks to DeepL API (free or paid). Does NOT store the API key in the script; it
# asks for it at runtime (or uses the DEEPL_API_KEY env var if present).

import argparse
import csv
import os
import sys
import time
from typing import Optional
from getpass import getpass

import requests

# Default to the DeepL Free endpoint. You can override with --url to use the paid endpoint.
DEFAULT_URL = "https://api-free.deepl.com/v2/translate"


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


def translate_en_to_it(text: str, url: str, auth_key: str, timeout: float = 20.0, retries: int = 3) -> Optional[str]:
    """Translate English -> Italian via DeepL. Returns None on failure."""
    # DeepL expects 'text' (can be repeated), 'source_lang', 'target_lang'.
    # Response looks like: {"translations":[{"detected_source_language":"EN","text":"..."}]}
    params = {
        "text": text,
        "source_lang": "EN",
        "target_lang": "IT",
        # We send plain text (not HTML)
        "formality": "default",
    }

    delay = 1.5
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, data=params, headers={"Authorization": f"DeepL-Auth-Key {auth_key}"}, timeout=timeout)
            # Some legacy DeepL clients send auth via form field 'auth_key'. Support either.
            if r.status_code == 403 or r.status_code == 400:
                # Try again with form-data auth_key (some proxies/regions prefer this)
                r = requests.post(url, data={**params, "auth_key": auth_key}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if translations and isinstance(translations, list):
                return translations[0].get("text")
            return None
        except Exception as e:
            if attempt == retries:
                sys.stderr.write(f"[ERROR] Failed to translate '{text}': {e}\n")
                return None
            time.sleep(delay)
            delay *= 1.7  # simple backoff


def main():
    ap = argparse.ArgumentParser(description="Fill Italian_Translation using DeepL API (prompts for API key).")
    ap.add_argument("-i", "--input", required=True, help="Input CSV path")
    ap.add_argument("-o", "--output", help="Output CSV path (default: in-place with .out suffix)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"DeepL endpoint (default: {DEFAULT_URL}; paid: https://api.deepl.com/v2/translate)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing Italian_Translation values")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing file")
    args = ap.parse_args()

    in_path = args.input
    if not os.path.exists(in_path):
        sys.exit(f"Input not found: {in_path}")

    out_path = args.output or os.path.splitext(in_path)[0] + ".out.csv"

    # Get API key interactively (or from env var)
    auth_key = get_deepl_key_interactive()

    # Read all rows
    with open(in_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        required = {"English_Term", "Italian_Translation"}
        missing = required - set(fieldnames)
        if missing:
            sys.exit(f"CSV is missing required column(s): {', '.join(sorted(missing))}")

        rows = list(reader)

    updated = 0
    skipped = 0
    for row in rows:
        eng = (row.get("English_Term") or "").strip()
        current_it = (row.get("Italian_Translation") or "").strip()

        if not eng:
            skipped += 1
            continue

        if current_it and not args.overwrite:
            skipped += 1
            continue

        translated = translate_en_to_it(eng, url=args.url, auth_key=auth_key)
        if translated:
            row["Italian_Translation"] = translated
            updated += 1
            print(f"[OK] {eng} -> {translated}")
        else:
            # leave as-is on failure
            print(f"[WARN] no translation for: {eng}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would update {updated} row(s), skipped {skipped}. No file written.")
        return

    # Write output
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Updated {updated} row(s), skipped {skipped}.")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()