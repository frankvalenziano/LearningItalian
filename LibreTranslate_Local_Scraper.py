#!/usr/bin/env python3
# file: fill_italian_translations.py
# Usage:
#   python3 fill_italian_translations.py -i "Frank's Master A1 CEFR Word List.csv"
#   python3 fill_italian_translations.py -i input.csv -o output.csv --overwrite
#
# Assumes columns: English_Term, Italian_Translation, Italian_IPA, CEFR_Level, Notes, Tag
# Talks to LibreTranslate at http://localhost:8042/translate

import argparse
import csv
import os
import sys
import time
from typing import Optional

import requests

DEFAULT_URL = "http://localhost:8042/translate"

def translate_en_to_it(text: str, url: str = DEFAULT_URL, timeout: float = 20.0, retries: int = 3) -> Optional[str]:
    """Translate English -> Italian via local LibreTranslate. Returns None on failure."""
    payload = {
        "q": text,
        "source": "en",
        "target": "it",
        "format": "text",
    }
    delay = 1.5
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            # LibreTranslate returns: {"translatedText": "..."}
            return data.get("translatedText")
        except Exception as e:
            if attempt == retries:
                sys.stderr.write(f"[ERROR] Failed to translate '{text}': {e}\n")
                return None
            time.sleep(delay)
            delay *= 1.7  # backoff

def main():
    ap = argparse.ArgumentParser(description="Fill Italian_Translation using local LibreTranslate.")
    ap.add_argument("-i", "--input", required=True, help="Input CSV path")
    ap.add_argument("-o", "--output", help="Output CSV path (default: in-place with .out suffix)")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"LibreTranslate endpoint (default: {DEFAULT_URL})")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing Italian_Translation values")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing file")
    args = ap.parse_args()

    in_path = args.input
    if not os.path.exists(in_path):
        sys.exit(f"Input not found: {in_path}")

    out_path = args.output or os.path.splitext(in_path)[0] + ".out.csv"

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

        translated = translate_en_to_it(eng, url=args.url)
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