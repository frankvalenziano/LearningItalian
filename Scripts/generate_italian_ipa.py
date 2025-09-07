#!/usr/bin/env python3
# Fill missing Italian IPA in a CSV using eSpeak NG via phonemizer.
#
# - Keeps existing IPA values as-is.
# - Only fills empty cells in the IPA column (either 'IPA' or 'Italian_IPA') using the Italian text column (either 'Italian' or 'Italian_Translation').
# - Wraps the generated IPA with slashes, to match your sheet (e.g., /aˈmo.re/).
# - Works entirely offline once dependencies are installed.
# - Auto mode: if you do not pass --italian-col/--ipa-col, the script will attempt to fill (Italian_Term -> Italian_IPA) and then (Italian_Sentence -> Italian_Sentence_IPA) if those columns exist.
#
# Dependencies (install locally on your Mac):
#   brew install espeak-ng
#   pip install phonemizer pandas
#
# Usage:
#   python fill_italian_ipa.py --input "/path/to/General Vocabulary.csv" --backup
#   python fill_italian_ipa.py --input "/path/to/General Vocabulary.csv" --output updated.csv
#   python fill_italian_ipa.py --input "/path/to/file.csv" --dry-run --debug
#   python fill_italian_ipa.py --input "/path/to/General Vocabulary.csv"  # auto: fills Italian_IPA then Italian_Sentence_IPA
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

def fill_pair(df: pd.DataFrame, text_col: str, ipa_col: str, batch_size: int, debug: bool) -> int:
    """Fill empty IPA cells in ipa_col using phonemized values from text_col.
    Returns the number of rows changed. Creates ipa_col if missing.
    """
    # Ensure columns exist
    if text_col not in df.columns:
        if debug:
            print(f"SKIP: Text column '{text_col}' not found.")
        return 0
    if ipa_col not in df.columns:
        df[ipa_col] = ""

    # Normalize
    ipa_raw = df[ipa_col]
    ita_raw = df[text_col]
    ipa_col_series = ipa_raw.fillna("").astype(str).str.replace("\u00A0", " ", regex=False).str.strip()
    ipa_col_series = ipa_col_series.where(~ipa_col_series.str.lower().isin(["nan", "none", "null"]), "")
    ita_col_series = ita_raw.fillna("").astype(str).str.replace("\u00A0", " ", regex=False).str.strip()

    # Determine TODO
    mask_missing = (ipa_col_series == "") & (ita_col_series != "")
    todo = df[mask_missing].copy()

    if debug:
        print(f"\n>>> Pair: {text_col} -> {ipa_col}")
        print("Total rows:", len(df))
        print("Rows needing IPA:", len(todo))
        print("First 5 indices needing IPA:", todo.index[:5].tolist())

    if todo.empty:
        return 0

    indices: List[int] = todo.index.tolist()
    italian_terms: List[str] = todo[text_col].astype(str).tolist()

    changed = 0
    for i in range(0, len(italian_terms), batch_size):
        chunk = italian_terms[i:i+batch_size]
        try:
            out = get_ipa_batch(chunk)
        except Exception as e:
            print(f"Batch {i}-{i+len(chunk)} failed for pair {text_col}->{ipa_col}: {e}", file=sys.stderr)
            sys.exit(2)
        if len(out) != len(chunk):
            print("Mismatch between inputs and outputs length; aborting.", file=sys.stderr)
            sys.exit(3)
        # Apply this chunk
        for j, val in enumerate(out):
            idx = indices[i + j]
            if val is None:
                continue
            df.at[idx, ipa_col] = f"/{val}/"
            changed += 1

    return changed

def main():
    ap = argparse.ArgumentParser(description="Fill missing Italian IPA in CSV using eSpeak NG.")
    ap.add_argument("--input", required=True, help="Path to the CSV (will be updated in-place unless --output or --dry-run)." )
    ap.add_argument("--output", help="Write to a new CSV instead of overwriting input.")
    ap.add_argument("--backup", action="store_true", help="Write a .bak file next to the input before overwriting.")
    ap.add_argument("--dry-run", action="store_true", help="Do not write anything; just print what would change.")
    ap.add_argument("--batch-size", type=int, default=128, help="How many rows to send to G2P at once.")
    ap.add_argument("--italian-col", help="Name of the Italian text column to phonemize. If omitted, auto mode is used.")
    ap.add_argument("--ipa-col", help="Name of the IPA output column to write/fill. If omitted, auto mode is used.")
    ap.add_argument("--debug", action="store_true", help="Print diagnostics about column names and empties.")
    args = ap.parse_args()

    df = pd.read_csv(args.input)

    # Normalize column names (trim stray spaces, weird unicode spaces)
    df.columns = [str(c).strip() for c in df.columns]

    # Determine which pairs to process
    pairs: List[tuple[str, str]] = []
    if args.italian_col and args.ipa_col:
        IT_COL = args.italian_col.strip()
        IPA_COL = args.ipa_col.strip()
        pairs.append((IT_COL, IPA_COL))
    else:
        # Auto mode: try known pairs
        default_pairs = [("Italian_Term", "Italian_IPA"), ("Italian_Sentence", "Italian_Sentence_IPA")]
        for tcol, ipacol in default_pairs:
            if tcol in df.columns:
                if ipacol not in df.columns:
                    df[ipacol] = ""
                pairs.append((tcol, ipacol))

    if not pairs:
        print(
            "ERROR: No valid column pairs found. Provide --italian-col and --ipa-col, or include one of the default pairs (Italian_Term->Italian_IPA, Italian_Sentence->Italian_Sentence_IPA).\n" \
            f"Columns present: {list(df.columns)}",
            file=sys.stderr,
        )
        sys.exit(1)

    total_changed = 0
    previews = []
    for (tcol, ipacol) in pairs:
        before_mask = (df[ipacol].fillna("").astype(str).str.strip() == "") & (df[tcol].fillna("").astype(str).str.strip() != "") if ipacol in df.columns else (df[tcol].fillna("").astype(str).str.strip() != "")
        # Capture preview indices before filling
        preview_indices = df[before_mask].index[:10].tolist()

        changed = fill_pair(df, tcol, ipacol, args.batch_size, args.debug)
        total_changed += changed

        # Build preview after filling
        if args.dry_run and preview_indices:
            previews.append((tcol, ipacol, df.loc[preview_indices, [tcol, ipacol]].copy()))

    if args.dry_run:
        if previews:
            for (tcol, ipacol, pv) in previews:
                print(f"\nPreview for {tcol} -> {ipacol} (up to 10 rows):")
                print(pv.to_string(index=False))
        print("\nDry run complete. No files were written.")
        return

    if total_changed == 0:
        print("No empty IPA cells found. Nothing to do.")
        sys.exit(0)

    # Write output
    if args.output:
        df.to_csv(args.output, index=False)
        print(f"Wrote updated CSV to: {args.output}")
    else:
        if args.backup:
            from pathlib import Path
            p = Path(args.input)
            backup_path = p.with_suffix(p.suffix + ".bak")
            backup_path.write_bytes(p.read_bytes())
            print(f"Backup written to: {backup_path}")
        df.to_csv(args.input, index=False)
        print(f"Updated CSV written in place: {args.input}")

if __name__ == "__main__":
    main()
