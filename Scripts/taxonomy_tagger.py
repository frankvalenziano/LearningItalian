#!/usr/bin/env python3
"""
Taxonomy tagger for CSVs using the Homebrew WordNet data files (no NLTK required).

It reads a CSV, looks up the WordNet *lexicographer categories* (e.g.,
`noun.animal`, `verb.motion`) for each word and writes them into/over the
`Taxonomy` column. If the column doesn't exist, it will be created.

By default it uses the `English_Translation` column for the lookup term. You can
change this with `--word-field`.

It does **not** call `wn` (which on macOS lacks lexnames); instead it parses the
WordNet index files installed by Homebrew and maps `lex_filenum` to the standard
lexname strings.

Usage examples:
  taxonomy_tagger.py --input Dictionary.csv --inplace
  taxonomy_tagger.py -i Dictionary.csv -o Dictionary.tagged.csv
  taxonomy_tagger.py -i Dictionary.csv --word-field English_Term --join "; "

Exit codes: 0 on success, non‑zero on errors.
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from typing import List, Optional, Tuple

# 45 lexnames in standard WordNet order (index = lex_filenum)
LEXNAMES = [
    "adj.all", "adj.pert", "adv.all",
    "noun.Tops", "noun.act", "noun.animal", "noun.artifact", "noun.attribute",
    "noun.body", "noun.cognition", "noun.communication", "noun.event",
    "noun.feeling", "noun.food", "noun.group", "noun.location", "noun.motive",
    "noun.object", "noun.person", "noun.phenomenon", "noun.plant",
    "noun.possession", "noun.process", "noun.quantity", "noun.relation",
    "noun.shape", "noun.state", "noun.substance", "noun.time",
    "verb.body", "verb.change", "verb.cognition", "verb.communication",
    "verb.competition", "verb.consumption", "verb.contact", "verb.creation",
    "verb.emotion", "verb.motion", "verb.perception", "verb.possession",
    "verb.social", "verb.stative", "verb.weather", "adj.ppl"
]

POS_DIGIT = {"1": "noun", "2": "verb", "3": "adj", "4": "adv"}

# Regex for a WordNet sense key prefix: lemma%pos:filenum:...
SENSE_PREFIX_RE = re.compile(r"^(?P<lemma>[^%]+)%(?P<pos>[1-4]):(?P<filenum>\d{2})")


def find_wordnet_share_dir() -> str:
    """Locate the WordNet share directory as installed by Homebrew."""
    # Try `brew --prefix wordnet`
    try:
        prefix = subprocess.check_output(["brew", "--prefix", "wordnet"], text=True).strip()
        candidate = os.path.join(prefix, "share", "wordnet")
        if os.path.isdir(candidate):
            return candidate
    except Exception:
        pass

    # Common fallback path on Apple Silicon
    fallback = "/opt/homebrew/opt/wordnet/share/wordnet"
    if os.path.isdir(fallback):
        return fallback

    # Intel fallback
    fallback2 = "/usr/local/opt/wordnet/share/wordnet"
    if os.path.isdir(fallback2):
        return fallback2

    raise FileNotFoundError(
        "Could not find the WordNet data directory. Ensure Homebrew wordnet is installed.\n"
        "Try: brew install wordnet"
    )


def load_index_sense(wn_share_dir: str) -> List[str]:
    path = os.path.join(wn_share_dir, "index.sense")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"index.sense not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().splitlines()


def lexnames_for_lemma(lines: List[str], lemma: str) -> List[Tuple[str, str]]:
    """
    Return list of (pos, lexname) tuples for the given lemma across senses.

    We scan `index.sense` for keys beginning with `lemma%`, extract POS digit and
    lex_filenum, then map to lexname.
    """
    lemma_norm = lemma.strip().lower().replace(" ", "_")
    results: List[Tuple[str, str]] = []
    # A simple scan is fine; index.sense is ~10–15MB and this is fast enough.
    prefix = f"{lemma_norm}%"
    for line in lines:
        if not line or not line.startswith(prefix):
            continue
        m = SENSE_PREFIX_RE.match(line)
        if not m:
            continue
        pos_digit = m.group("pos")
        pos = POS_DIGIT.get(pos_digit, "?")
        filenum = int(m.group("filenum"))
        if 0 <= filenum < len(LEXNAMES):
            results.append((pos, LEXNAMES[filenum]))
    return results


def choose_category(pairs: List[Tuple[str, str]], strategy: str = "mode") -> str:
    """Collapse multiple (pos, lexname) pairs to a single category string.

    Strategies:
      - "mode": choose the most frequent lexname across senses; ties broken by a
        stable order (noun > verb > adj > adv) and then alphabetically.
      - "first": take the first unique lexname encountered.
      - "all": return all unique lexnames joined with a delimiter (handled by caller).
    """
    if not pairs:
        return ""
    if strategy == "first":
        return pairs[0][1]
    if strategy == "mode":
        counts = Counter([lx for _, lx in pairs])
        # tie‑breakers
        def rank(item):
            lx, c = item
            # prefer nouns, then verbs, then adjectives, then adverbs
            pos_order = {"noun": 0, "verb": 1, "adj": 2, "adv": 3}
            # find a representative pos for this lexname from pairs
            poss = [p for (p, l) in pairs if l == lx]
            pos = sorted(poss, key=lambda p: pos_order.get(p, 99))[0] if poss else "zz"
            return (-c, pos_order.get(pos, 99), lx)
        best = sorted(counts.items(), key=rank)[0][0]
        return best
    # default
    return pairs[0][1]


def process_csv(input_path: str,
                output_path: Optional[str],
                word_field: str,
                strategy: str,
                join: str,
                overwrite: bool) -> None:
    wn_dir = find_wordnet_share_dir()
    lines = load_index_sense(wn_dir)

    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    with open(input_path, newline="", encoding="utf-8") as inf:
        reader = csv.DictReader(inf)
        fieldnames = list(reader.fieldnames or [])
        if "Taxonomy" not in fieldnames:
            fieldnames.append("Taxonomy")
        rows = list(reader)

    # Build an index to avoid repeated scans for duplicate words
    cache: dict[str, List[Tuple[str, str]]] = {}

    for row in rows:
        term = (row.get(word_field) or "").strip()
        if not term:
            row["Taxonomy"] = ""
            continue
        pairs = cache.get(term)
        if pairs is None:
            pairs = lexnames_for_lemma(lines, term)
            cache[term] = pairs
        if strategy == "all":
            cats = sorted({lx for _, lx in pairs})
            row["Taxonomy"] = join.join(cats) if cats else ""
        else:
            row["Taxonomy"] = choose_category(pairs, strategy=strategy) if pairs else ""

    # Determine output path
    if output_path:
        out_path = output_path
        if os.path.exists(out_path) and not overwrite:
            raise FileExistsError(f"Output already exists: {out_path}. Use --overwrite to replace.")
    else:
        # in‑place write via temporary file for safety
        out_path = input_path

    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as outf:
        writer = csv.DictWriter(outf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            # Ensure all fields are present
            for fn in fieldnames:
                row.setdefault(fn, "")
            writer.writerow(row)

    # Atomic replace
    if out_path == input_path:
        os.replace(tmp_path, input_path)
    else:
        os.replace(tmp_path, out_path)


def main():
    p = argparse.ArgumentParser(description="Fill the Taxonomy column using WordNet lexnames")
    p.add_argument("-i", "--input", required=True, help="Input CSV path")
    p.add_argument("-o", "--output", default=None, help="Output CSV path (omit to modify input in-place)")
    p.add_argument("--word-field", default="English_Translation",
                   help="CSV column to query for the lemma (default: English_Translation)")
    p.add_argument("--strategy", choices=["mode", "first", "all"], default="mode",
                   help="How to choose category when multiple senses exist")
    p.add_argument("--join", default=", ", help="Delimiter when --strategy=all (default: ', ')")
    p.add_argument("--overwrite", action="store_true", help="Allow overwriting the --output file if it exists")
    args = p.parse_args()

    try:
        process_csv(
            input_path=args.input,
            output_path=args.output,
            word_field=args.word_field,
            strategy=args.strategy,
            join=args.join,
            overwrite=args.overwrite,
        )
    except Exception as e:
        raise SystemExit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
