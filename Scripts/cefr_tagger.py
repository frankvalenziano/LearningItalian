#!/usr/bin/env python3
"""
CEFR tagger that merges a Kaggle CEFR wordlist (or any lemma→CEFR CSV) into your
own dictionary CSV. It reads words from a chosen column (default:
English_Translation) and writes the CEFR level into CEFR_Level. Levels are
normalized to uppercase A1–C2.

Supports either:
  • Local mapping files via --map (repeatable), or
  • Downloading from Kaggle via --kaggle-dataset (requires Kaggle CLI configured)

Examples:
  # Use a local Kaggle CSV you already downloaded
  python3 Scripts/cefr_tagger.py -i "Data Sources/Dictionary.csv" \
      --map "Data Sources/kaggle_cefr_wordlist.csv" --overwrite

  # Pull a dataset from Kaggle (requires ~/.kaggle/kaggle.json)
  python3 Scripts/cefr_tagger.py -i "Data Sources/Dictionary.csv" \
      --kaggle-dataset someuser/english-cefr-words \
      --kaggle-file words_cefr.csv --overwrite

Notes:
  • If multiple maps provide a level, the script prefers the *lower* level
    (A1 < A2 < … < C2).
  • Input CSV is updated in-place unless --output is provided (atomic write).
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Iterable, Tuple

# Order for CEFR levels; lower index = easier level
CEFR_ORDER = {"A1":0, "A2":1, "B1":2, "B2":3, "C1":4, "C2":5}
CEFR_VALID = set(CEFR_ORDER.keys())

# Accept some common variants seen in community datasets
CANON_MAP = {
    "a1":"A1","a2":"A2","b1":"B1","b2":"B2","c1":"C1","c2":"C2",
    "A1 ":"A1","A2 ":"A2","B1 ":"B1","B2 ":"B2","C1 ":"C1","C2 ":"C2",
}

POSSIBLE_WORD_KEYS = ("lemma","word","headword","token","English","English_Translation")
POSSIBLE_LEVEL_KEYS = ("CEFR","level","cefr","CEFR_Level")


def canonical_level(val: str) -> str:
    if not val:
        return ""
    v = val.strip()
    v = CANON_MAP.get(v, v)
    v = v.upper()
    return v if v in CEFR_VALID else ""


def load_map_csv(path: str, case_insensitive: bool = True) -> Dict[str,str]:
    """Load a lemma→CEFR map from a CSV file with flexible headers.
       Returns dict of {lemma_lower: CEFR}.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CEFR map not found: {path}")
    mapping: Dict[str,str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        # figure out columns
        headers = {h.lower():h for h in (r.fieldnames or [])}
        word_key = next((headers[k] for k in headers if k in [x.lower() for x in POSSIBLE_WORD_KEYS]), None)
        level_key = next((headers[k] for k in headers if k in [x.lower() for x in POSSIBLE_LEVEL_KEYS]), None)
        if not word_key or not level_key:
            raise ValueError(
                f"{path}: could not find word/level columns. Expected one of word keys {POSSIBLE_WORD_KEYS} "
                f"and level keys {POSSIBLE_LEVEL_KEYS}. Found: {r.fieldnames}"
            )
        for row in r:
            lemma = (row.get(word_key) or "").strip()
            level = canonical_level(row.get(level_key, ""))
            if not lemma or not level:
                continue
            key = lemma.lower() if case_insensitive else lemma
            prev = mapping.get(key)
            if prev is None or CEFR_ORDER[level] < CEFR_ORDER[prev]:
                mapping[key] = level
    return mapping


def merge_maps(paths: Iterable[str]) -> Dict[str,str]:
    merged: Dict[str,str] = {}
    for p in paths:
        sub = load_map_csv(p)
        for k,v in sub.items():
            old = merged.get(k)
            if old is None or CEFR_ORDER[v] < CEFR_ORDER[old]:
                merged[k] = v
    return merged


def ensure_kaggle_download(dataset: str, out_dir: str, file_name: str | None) -> str:
    """Download a Kaggle dataset (requires Kaggle CLI). Returns path to the CSV to use.
       If file_name is provided, returns that file inside out_dir; otherwise the first CSV found.
    """
    os.makedirs(out_dir, exist_ok=True)
    # Try to list files to verify access
    try:
        subprocess.run(["kaggle", "datasets", "files", "-d", dataset], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        raise SystemExit(
            "Kaggle CLI not available or not authenticated.\n"
            "Install with: pipx install kaggle  (or pip install kaggle)\n"
            "Then create ~/.kaggle/kaggle.json and set your credentials."
        )

    # Download and unzip
    subprocess.run(["kaggle", "datasets", "download", "-d", dataset, "-p", out_dir, "--unzip"], check=True)

    if file_name:
        target = os.path.join(out_dir, file_name)
        if not os.path.isfile(target):
            raise SystemExit(f"Kaggle file not found after download: {target}")
        return target

    # else: pick the first CSV present
    for root,_,files in os.walk(out_dir):
        for f in files:
            if f.lower().endswith(".csv"):
                return os.path.join(root,f)
    raise SystemExit("No CSV found in the downloaded Kaggle dataset. Specify --kaggle-file.")


def tag_csv(input_csv: str, output_csv: str | None, word_field: str, level_field: str,
            maps: Iterable[str], kaggle_dataset: str | None, kaggle_file: str | None,
            overwrite: bool) -> None:
    # Load mapping from either local maps or Kaggle
    map_paths = list(maps)
    if kaggle_dataset:
        cache_dir = os.path.join(os.path.dirname(input_csv) or ".", ".kaggle_cache")
        csv_path = ensure_kaggle_download(kaggle_dataset, cache_dir, kaggle_file)
        map_paths.append(csv_path)
    if not map_paths:
        raise SystemExit("Provide at least one CEFR source via --map or --kaggle-dataset.")

    mapping = merge_maps(map_paths)

    # Prepare IO
    inplace = output_csv is None
    out_path = input_csv if inplace else output_csv
    if not inplace and os.path.exists(out_path) and not overwrite:
        raise SystemExit(f"Output exists: {out_path}. Use --overwrite to replace.")

    with open(input_csv, newline="", encoding="utf-8") as inf:
        r = csv.DictReader(inf)
        fields = list(r.fieldnames or [])
        if level_field not in fields:
            fields.append(level_field)
        rows = list(r)

    for row in rows:
        term = (row.get(word_field) or "").strip()
        key = term.lower()
        row[level_field] = mapping.get(key, "")

    tmp = out_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as outf:
        w = csv.DictWriter(outf, fieldnames=fields)
        w.writeheader()
        for row in rows:
            for f in fields:
                row.setdefault(f, "")
            # Ensure CEFR is uppercase if present
            lvl = row.get(level_field, "")
            row[level_field] = canonical_level(lvl) if lvl else ""
            w.writerow(row)
    os.replace(tmp, out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill CEFR levels using a Kaggle CEFR wordlist or local maps")
    ap.add_argument("-i","--input", required=True, help="Input CSV path")
    ap.add_argument("-o","--output", help="Output CSV (omit for in-place update)")
    ap.add_argument("--word-field", default="English_Translation", help="Column containing the lemma to look up (default: English_Translation)")
    ap.add_argument("--level-field", default="CEFR_Level", help="Column to write CEFR level into (default: CEFR_Level)")
    ap.add_argument("--map", action="append", default=[], help="Path to lemma→CEFR CSV (repeatable)")
    ap.add_argument("--kaggle-dataset", help="Kaggle dataset slug, e.g. user/dataset-name")
    ap.add_argument("--kaggle-file", help="Specific CSV filename inside the Kaggle dataset (optional)")
    ap.add_argument("--overwrite", action="store_true", help="Allow overwriting --output if it exists")
    args = ap.parse_args()

    try:
        tag_csv(
            input_csv=args.input,
            output_csv=args.output,
            word_field=args.word_field,
            level_field=args.level_field,
            maps=args.map,
            kaggle_dataset=args.kaggle_dataset,
            kaggle_file=args.kaggle_file,
            overwrite=args.overwrite,
        )
    except Exception as e:
        raise SystemExit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
