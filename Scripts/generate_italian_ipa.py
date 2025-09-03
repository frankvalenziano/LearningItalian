#!/usr/bin/env python3
# Fill missing Italian IPA in a CSV using eSpeak NG via phonemizer.
#
# - Keeps existing IPA values as-is.
# - Only fills empty cells in the IPA column (either 'IPA' or 'Italian_IPA') using the Italian text column (either 'Italian' or 'Italian_Translation').
# - Wraps the generated IPA with slashes, to match your sheet (e.g., /aˈmo.re/).
# - Works entirely offline once dependencies are installed.
#
# Dependencies (install locally on your Mac):
#   brew install espeak-ng
#   pip install phonemizer pandas
#
# Usage:
#   python fill_italian_ipa.py --input "/path/to/General Vocabulary.csv" --backup
#   python fill_italian_ipa.py --input "/path/to/General Vocabulary.csv" --output updated.csv
#   python fill_italian_ipa.py --input "/path/to/file.csv" --dry-run --debug
#
# Notes:
# - This script supports two schemas:
#     Legacy: English, Italian, IPA, Tags, Notes
#     New:    English_Term, Italian_Translation, English_Sentence, Italian_Sentence_Translation, Italian_IPA, CEFR_Level, Notes, Tags
# - If your file is huge, you can speed up by increasing batch size.

import argparse
import sys
import subprocess
from typing import List, Optional

import pandas as pd

# Try to import phonemizer; if not available, we can fall back to espeak-ng CLI.
try:
    from phonemizer import phonemize
    HAS_PHONEMIZER = True
except Exception:
    HAS_PHONEMIZER = False

def espeak_cli(words: List[str]) -> List[str]:
    """Fallback: call espeak-ng CLI to get IPA for a list of words.
    Requires `espeak-ng` in PATH (brew install espeak-ng)."""
    proc = subprocess.run(
        ["espeak-ng", "-v", "it", "--ipa", "--quiet"],
        input=("\n".join(words)).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"espeak-ng failed: {proc.stderr.decode('utf-8', 'ignore')}" )
    lines = proc.stdout.decode("utf-8").splitlines()
    return [line.strip() for line in lines]

def get_ipa_batch(words: List[str]) -> List[str]:
    """Return IPA (without surrounding slashes) for each word/phrase in Italian."""
    if not words:
        return []
    if HAS_PHONEMIZER:
        ipa = phonemize(
            words,
            language="it",
            backend="espeak",
            strip=True,
            separator=None,  # let backend decide
            preserve_punctuation=True,
            njobs=1,  # deterministic order
        )
        if isinstance(ipa, str):
            ipa = [ipa]
        return [s.strip() for s in ipa]
    return espeak_cli(words)

def main():
    ap = argparse.ArgumentParser(description="Fill missing Italian IPA in CSV using eSpeak NG.")
    ap.add_argument("--input", required=True, help="Path to the CSV (will be updated in-place unless --output or --dry-run)." )
    ap.add_argument("--output", help="Write to a new CSV instead of overwriting input.")
    ap.add_argument("--backup", action="store_true", help="Write a .bak file next to the input before overwriting.")
    ap.add_argument("--dry-run", action="store_true", help="Do not write anything; just print what would change.")
    ap.add_argument("--batch-size", type=int, default=128, help="How many rows to send to G2P at once.")
    ap.add_argument("--debug", action="store_true", help="Print diagnostics about column names and empties.")
    args = ap.parse_args()

    df = pd.read_csv(args.input)

    # Normalize column names (trim stray spaces, weird unicode spaces)
    df.columns = [str(c).strip() for c in df.columns]

    # --- Schema detection: support legacy and new headers ---
    legacy_cols = {"it": "Italian", "ipa": "IPA"}
    new_cols    = {"it": "Italian_Translation", "ipa": "Italian_IPA"}

    if new_cols["it"] in df.columns and new_cols["ipa"] in df.columns:
        IT_COL  = new_cols["it"]
        IPA_COL = new_cols["ipa"]
        schema  = "new"
    elif legacy_cols["it"] in df.columns and legacy_cols["ipa"] in df.columns:
        IT_COL  = legacy_cols["it"]
        IPA_COL = legacy_cols["ipa"]
        schema  = "legacy"
    else:
        print("ERROR: could not find expected Italian/IPA columns.\n"
              f"Columns present: {list(df.columns)}\n"
              "Expected either ['Italian','IPA'] or ['Italian_Translation','Italian_IPA'].", file=sys.stderr)
        sys.exit(1)

    # Treat empties/NaN/placeholder strings as empty
    ipa_raw = df[IPA_COL]
    ita_raw = df[IT_COL]
    ipa_col = ipa_raw.fillna("")       # fill NaN first
    ipa_col = ipa_col.astype(str).str.replace("\u00A0", " ", regex=False).str.strip()  # replace NBSP, trim
    # Consider literal strings that mean 'empty'
    ipa_col = ipa_col.where(~ipa_col.str.lower().isin(["nan", "none", "null"]), "")

    ita_col = ita_raw.fillna("").astype(str).str.replace("\u00A0", " ", regex=False).str.strip()

    # Determine which rows need IPA
    mask_missing = (ipa_col == "") & (ita_col != "")
    todo = df[mask_missing].copy()

    if args.debug:
        print(f"Using schema={schema} | IT_COL='{IT_COL}' | IPA_COL='{IPA_COL}'")
        print("Columns:", list(df.columns))
        print("Total rows:", len(df))
        print("IPA empty candidates:", int((ipa_col == '').sum()))
        print("Italian non-empty:", int((ita_col != '').sum()))
        print("Rows needing IPA:", len(todo))
        # Show a couple sample rows (indices) that are missing
        print("First 5 indices needing IPA:", todo.index[:5].tolist())

    if todo.empty:
        print("No empty IPA cells found. Nothing to do.")
        sys.exit(0)

    print(f"Found {len(todo)} rows with empty IPA. Generating IPA using {'phonemizer+eSpeak' if HAS_PHONEMIZER else 'eSpeak NG CLI'}...")

    # Process in batches
    indices: List[int] = todo.index.tolist()
    italian_terms: List[str] = todo[IT_COL].astype(str).tolist()
    ipa_results: List[Optional[str]] = [None] * len(italian_terms)

    for i in range(0, len(italian_terms), args.batch_size):
        chunk = italian_terms[i:i+args.batch_size]
        try:
            out = get_ipa_batch(chunk)
        except Exception as e:
            print(f"Batch {i}-{i+len(chunk)} failed: {e}", file=sys.stderr)
            sys.exit(2)
        if len(out) != len(chunk):
            print("Mismatch between inputs and outputs length; aborting.", file=sys.stderr)
            sys.exit(3)
        for j, val in enumerate(out):
            ipa_results[i + j] = val

    # Apply results to dataframe, wrapping with slashes
    changed = 0
    for idx, ipa in zip(indices, ipa_results):
        if ipa is None:
            continue
        ipa_wrapped = f"/{ipa}/"
        df.at[idx, IPA_COL] = ipa_wrapped
        changed += 1

    print(f"Prepared IPA for {changed} rows.")

    if args.dry_run:
        preview = df.loc[indices[:10], [IT_COL, IPA_COL]]
        print(preview.to_string(index=False))
        print("\nDry run complete. No files were written.")
        return

    # Write output
    if args.output:
        df.to_csv(args.output, index=False)
        print(f"Wrote updated CSV to: {args.output}")
    else:
        if args.backup:
            from pathlib import Path
            backup_path = Path(args.input).with_suffix(Path(args.input).suffix + ".bak")
            backup_path.write_bytes(Path(args.input).read_bytes())
            print(f"Backup written to: {backup_path}")
        df.to_csv(args.input, index=False)
        print(f"Updated CSV written in place: {args.input}")

if __name__ == "__main__":
    main()
