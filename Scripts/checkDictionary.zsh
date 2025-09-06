#!/usr/bin/env zsh
# checkDictionary.zsh — manage Dictionary.csv entries and finalize the file
#
# Features:
#  - Check existence of words in English_Translation (case-insensitive exact match)
#  - Append new rows to English_Translation or Italian_Term (skip if already exists)
#  - Accept lists from files
#  - Optional finalization step to dedupe/merge and sort deterministically
#
# Usage examples:
#   ./Scripts/checkDictionary.zsh --word apple
#   ./Scripts/checkDictionary.zsh --dict "Data Sources/Dictionary.csv" --word banana --word "green tea"
#   ./Scripts/checkDictionary.zsh --word-list "Data Sources/new_words.txt"
#   ./Scripts/checkDictionary.zsh --add-english "green tea" --add-italian "tè verde"
#   ./Scripts/checkDictionary.zsh --finalize --dict "Data Sources/Dictionary.csv"
#
# Columns expected:
#   English_Translation, Italian_Term (plus any others you maintain)

set -euo pipefail

# --- Defaults ---
csv_file="Data Sources/Dictionary.csv"
word_list=""
typeset -a words add_en add_it
finalize=false

print_usage() {
  cat >&2 <<USAGE
Usage:
  $0 [--dict <csv_file>] --word <word> [--word <word> ...]
  $0 [--dict <csv_file>] --word-list <path_to_txt>
  $0 [--dict <csv_file>] --add-english <term> [--add-english <term> ...]
  $0 [--dict <csv_file>] --add-italian  <term> [--add-italian  <term> ...]
  $0 [--dict <csv_file>] --finalize

Options:
  --dict         Path to the CSV dictionary file (default: $csv_file)
  --word         A word to check in English_Translation (repeatable)
  --word-list    File containing one word per line to check in English_Translation
  --add-english  Append value(s) to English_Translation (accepts file paths as values too)
  --add-italian  Append value(s) to Italian_Term (accepts file paths as values too)
  --finalize     Dedupe & sort the CSV once at the end (safe for CI)
  -h, --help     Show this help

Notes:
  * Matching is case-insensitive and exact (no trimming beyond whitespace).
  * Appends create a new row with only the target column set; all other columns remain empty.
  * Finalize removes exact-duplicate rows, merges duplicates by EN/IT keys (preferring filled counterparts), then sorts by English_Translation.
USAGE
}

# --- Helpers ---
trim_line() {
  local s="$1"
  s="${s##[[:space:]]}"
  s="${s%%[[:space:]]}"
  print -- "$s"
}

ingest_list_file() {
  local path="$1" aname="$2"
  [[ -f "$path" ]] || { print -u2 -- "Error: word list not found: $path"; return 1; }
  local line w
  while IFS= read -r line || [[ -n "$line" ]]; do
    w=$(trim_line "$line")
    [[ -z "$w" || "$w" == \#* ]] && continue
    eval "$aname+=(\"$w\")"
  done < "$path"
}

expand_add_args_from_files() {
  local aname="$1"; local -a original expanded
  eval "original=(\"\${${aname}[@]:-}\")"
  local item line w
  for item in "${original[@]}"; do
    if [[ -f "$item" ]]; then
      while IFS= read -r line || [[ -n "$line" ]]; do
        w=$(trim_line "$line")
        [[ -z "$w" || "$w" == \#* ]] && continue
        expanded+=("$w")
      done < "$item"
    else
      expanded+=("$item")
    fi
  done
  eval "$aname=(\"\${expanded[@]}\")"
}

# Python helpers for robust CSV ops
py_has_value_in_col() {
  local col="$1" val="$2" file="$3"
  python3 - "$file" "$col" "$val" <<'PY'
import sys, csv
path, col, val = sys.argv[1], sys.argv[2], sys.argv[3]
needle = (val or '').strip().lower()
with open(path, newline='', encoding='utf-8-sig') as f:
    r = csv.DictReader(f)
    for row in r:
        cell = (row.get(col) or '').strip().lower()
        if cell == needle:
            sys.exit(0)
sys.exit(1)
PY
}

py_append_in_col() {
  local col="$1" val="$2" file="$3"
  python3 - "$file" "$col" "$val" <<'PY'
import sys, csv
path, col, val = sys.argv[1], sys.argv[2], sys.argv[3]
# Read header to preserve field order
with open(path, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    fieldnames = list(reader.fieldnames or [])
if not fieldnames:
    print(f"Error: CSV has no header: {path}", file=sys.stderr)
    sys.exit(2)
# Build a blank row and set desired column
row = {h: '' for h in fieldnames}
if col not in row:
    print(f"Error: column not found: {col}", file=sys.stderr)
    sys.exit(3)
row[col] = val
# Append row
with open(path, 'a', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator='\n')
    w.writerow(row)
PY
}

finalize_csv() {
  local file="$1" tmp
  tmp=$(mktemp)
  python3 - "$file" > "$tmp" <<'PY'
import sys, csv
from collections import OrderedDict
path = sys.argv[1]
with open(path, newline='', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
    fieldnames = rows[0].keys() if rows else []
# 1) remove exact duplicate rows
seen = set(); uniq = []
for r in rows:
    key = '\0'.join((r.get(h,'') or '') for h in fieldnames)
    if key in seen: continue
    seen.add(key); uniq.append(r)
rows = uniq
# 2) dedupe by English_Translation (prefer rows with Italian_Term)
by_en = OrderedDict()
for r in rows:
    en = (r.get('English_Translation') or '').strip().lower()
    if not en:
        by_en[(None, id(r))] = r; continue
    cur = by_en.get(en)
    if cur is None:
        by_en[en] = r
    else:
        it_new = (r.get('Italian_Term') or '').strip()
        it_old = (cur.get('Italian_Term') or '').strip()
        if it_new and not it_old:
            by_en[en] = r
        else:
            for h in fieldnames:
                if not (cur.get(h) or '').strip() and (r.get(h) or '').strip():
                    cur[h] = r[h]
rows = [v for k,v in by_en.items() if not (isinstance(k, tuple) and k[0] is None)]
rows += [v for k,v in by_en.items() if isinstance(k, tuple) and k[0] is None]
# 3) dedupe by Italian_Term (prefer rows with English_Translation)
by_it = OrderedDict()
for r in rows:
    it = (r.get('Italian_Term') or '').strip().lower()
    key = ('IT', it) if it else (None, id(r))
    cur = by_it.get(key)
    if cur is None:
        by_it[key] = r
    else:
        en_new = (r.get('English_Translation') or '').strip()
        en_old = (cur.get('English_Translation') or '').strip()
        if en_new and not en_old:
            by_it[key] = r
        else:
            for h in fieldnames:
                if not (cur.get(h) or '').strip() and (r.get(h) or '').strip():
                    cur[h] = r[h]
rows = [v for k,v in by_it.items()]
# 4) sort by English_Translation (case-insensitive)
rows.sort(key=lambda r: (r.get('English_Translation') or '').lower())
w = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator='\n')
w.writeheader()
for r in rows:
    w.writerow({h: r.get(h, '') for h in fieldnames})
PY
  mv "$tmp" "$file"
}

# --- Parse CLI ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dict)
      [[ $# -ge 2 ]] || { echo "Error: --dict requires a value" >&2; exit 1; }
      csv_file="$2"; shift 2 ;;
    --word)
      [[ $# -ge 2 ]] || { echo "Error: --word requires a value" >&2; exit 1; }
      words+=("$2"); shift 2 ;;
    --word-list)
      [[ $# -ge 2 ]] || { echo "Error: --word-list requires a path" >&2; exit 1; }
      word_list="$2"; shift 2 ;;
    --add-english)
      [[ $# -ge 2 ]] || { echo "Error: --add-english requires a value" >&2; exit 1; }
      add_en+=("$2"); shift 2 ;;
    --add-italian)
      [[ $# -ge 2 ]] || { echo "Error: --add-italian requires a value" >&2; exit 1; }
      add_it+=("$2"); shift 2 ;;
    --finalize)
      finalize=true; shift ;;
    -h|--help)
      print_usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; print_usage; exit 1 ;;
  esac
done

# --- Validate CSV exists ---
if [[ ! -f "$csv_file" ]]; then
  echo "Error: CSV dictionary not found: $csv_file" >&2
  exit 1
fi

# Expand list inputs
[[ -n "$word_list" ]] && ingest_list_file "$word_list" words
expand_add_args_from_files add_en
expand_add_args_from_files add_it

# --- Perform additions first (skip if exists) ---
for e in "${add_en[@]:-}"; do
  [[ -z "$e" ]] && continue
  if py_has_value_in_col "English_Translation" "$e" "$csv_file"; then
    echo "English already exists: $e"
  else
    py_append_in_col "English_Translation" "$e" "$csv_file"
    echo "English added: $e"
  fi
done

for iword in "${add_it[@]:-}"; do
  [[ -z "$iword" ]] && continue
  if py_has_value_in_col "Italian_Term" "$iword" "$csv_file"; then
    echo "Italian already exists: $iword"
  else
    py_append_in_col "Italian_Term" "$iword" "$csv_file"
    echo "Italian added: $iword"
  fi
done

# --- Check mode ---
single_mode=false
if [[ ${#words[@]} -eq 1 && -z "${word_list}" ]]; then
  single_mode=true
fi

for w in "${words[@]:-}"; do
  [[ -z "$w" ]] && continue
  if py_has_value_in_col "English_Translation" "$w" "$csv_file"; then
    $single_mode && echo yes || echo "$w: yes"
  else
    $single_mode && echo no  || echo "$w: no"
  fi
done

# --- Finalize (optional) ---
if $finalize; then
  echo "Finalizing (dedupe + sort)..."
  finalize_csv "$csv_file"
  echo "Finalized: $csv_file"
fi