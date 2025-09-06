#!/bin/zsh
# Check or add words in Dictionary.csv
#
# Features
# - Check words for exact, case-insensitive match in English_Translation (existing behavior)
# - Append new rows with a value in either English_Translation or Italian_Term via flags
# - Proper CSV quoting is applied when adding (commas/quotes handled)
#
# Examples
#   ./checkDictionary.zsh --word apple
#   ./checkDictionary.zsh --dict "Data Sources/Dictionary.csv" --word banana --word "green tea"
#   ./checkDictionary.zsh --word-list "Data Sources/new_words.txt"
#   ./checkDictionary.zsh --add-english "green tea"
#   ./checkDictionary.zsh --add-italian "tè verde"
#   ./checkDictionary.zsh --dict "Data Sources/Dictionary.csv" --add-english "green tea" --add-italian "tè verde"
#
# Requires: csvkit (csvcut)

set -euo pipefail

# Defaults
csv_file="/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.csv"
word_list=""
typeset -a words add_en add_it

print_usage() {
  cat >&2 <<USAGE
Usage:
  $0 [--dict <csv_file>] [--word <word> ...]
  $0 [--dict <csv_file>] --word-list <path_to_txt>
  $0 [--dict <csv_file>] --add-english <term> [--add-english <term> ...]
  $0 [--dict <csv_file>] --add-italian  <term> [--add-italian  <term> ...]

Options:
  --dict         Path to the CSV dictionary file (default: \$csv_file)
  --word         A word to check in English_Translation (may be repeated)
  --word-list    File containing one word per line to check in English_Translation
  --add-english  Append a new CSV row with value in English_Translation
  --add-italian  Append a new CSV row with value in Italian_Term
  -h, --help     Show this help

Notes:
  * When adding, proper CSV quoting is applied. If the value already exists in
    the target column (case-insensitive exact match), the row will not be added.
  * When a single --word is given (no list), output is just \`yes\` or \`no\`.
USAGE
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
    -h|--help)
      print_usage; exit 0 ;;
    *)
      # Back-compat positional: <word>  or  <csv> <word>
      if [[ ${#words[@]} -eq 0 && -z "$word_list" && ${#add_en[@]} -eq 0 && ${#add_it[@]} -eq 0 ]]; then
        if [[ $# -eq 1 ]]; then
          words+=("$1"); shift; continue
        elif [[ $# -ge 2 ]]; then
          csv_file="$1"; words+=("$2"); shift 2; continue
        fi
      fi
      echo "Unknown argument: $1" >&2; print_usage; exit 1 ;;
  esac
done


# --- List ingestion helpers ---
trim_line() {
  # usage: trim_line "str" -> echoes trimmed version
  typeset s="$1"
  # trim leading spaces
  s="${s##[[:space:]]}"
  # trim trailing spaces
  s="${s%%[[:space:]]}"
  print -- "$s"
}

ingest_list_file() {
  # usage: ingest_list_file <path> <array-name-to-append>
  typeset path="$1" aname="$2"
  if [[ ! -f "$path" ]]; then
    print -u2 -- "Error: word list not found: $path"
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    typeset w
    w=$(trim_line "$line")
    [[ -z "$w" || "$w" == \#* ]] && continue
    eval "$aname+=(\"$w\")"
  done < "$path"
}

expand_add_args_from_files() {
  # usage: expand_add_args_from_files <array-name>
  typeset aname="$1"
  typeset -a original expanded
  eval "original=(\"\${${aname}[@]:-}\")"
  for item in "${original[@]}"; do
    if [[ -f "$item" ]]; then
      # treat item as a file and ingest each non-empty/non-comment line
      while IFS= read -r line || [[ -n "$line" ]]; do
        typeset w
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

# Ingest word list (for checks)
if [[ -n "$word_list" ]]; then
  ingest_list_file "$word_list" words || exit 1
fi

# Allow --add-english/--add-italian to accept a filepath whose lines are terms
expand_add_args_from_files add_en
expand_add_args_from_files add_it

# Ensure dependencies
for bin in csvcut; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "Error: $bin (csvkit) is required." >&2
    exit 1
  fi
done

# Validate CSV exists
if [[ ! -f "$csv_file" ]]; then
  echo "Error: CSV dictionary not found: $csv_file" >&2
  exit 1
fi

# Column indices & count
get_col_idx() { # name -> index
  local name="$1"
  csvcut -n "$csv_file" | awk -F: -v n="$name" 'tolower($2) ~ tolower(n) {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1); print $1}'
}

et_col_idx=$(get_col_idx "English_Translation" || true)
it_col_idx=$(get_col_idx "Italian_Term" || true)
col_count=$(csvcut -n "$csv_file" | wc -l | tr -d ' ')

if [[ -z "$et_col_idx" || -z "$it_col_idx" || -z "$col_count" || "$col_count" -lt 1 ]]; then
  echo "Error: Required columns not found or invalid CSV header." >&2
  echo "       Need columns: English_Translation, Italian_Term" >&2
  exit 1
fi

# Helpers
csv_escape() {
  local s="$1"
  local needs=0
  [[ "$s" == *","* || "$s" == *"\""* || "$s" == *$'\n'* ]] && needs=1
  s="${s//\"/\"\"}"
  if (( needs )); then
    printf '"%s"' "$s"
  else
    printf '%s' "$s"
  fi
}

has_value_in_col() { # name value -> exit 0 if exists
  local col_name="$1"; shift
  local needle="$*"
  csvcut -c "$col_name" "$csv_file" \
    | awk -v w="$needle" 'BEGIN{IGNORECASE=1} NR==1{next} {if (tolower($0)==tolower(w)) {found=1; exit}} END{exit(found?0:1)}'
}

append_value_to_idx() { # idx value -> append one row with value placed in idx
  local idx="$1"; shift
  local val="$*"
  local row=""
  local i=1
  local esc
  esc=$(csv_escape "$val")
  while (( i <= col_count )); do
    local cell=""
    (( i == idx )) && cell="$esc"
    if (( i == 1 )); then
      row="$cell"
    else
      row="$row,$cell"
    fi
    (( i++ ))
  done
  printf '%s\n' "$row" >> "$csv_file"
}

# Original check helpers (English_Translation only)
has_word() { has_value_in_col "English_Translation" "$1"; }

# --- Perform additions first (if requested) ---
for e in "${add_en[@]:-}"; do
  if has_value_in_col "English_Translation" "$e"; then
    echo "English already exists: $e"
  else
    append_value_to_idx "$et_col_idx" "$e"
    echo "English added: $e"
  fi
done

for iword in "${add_it[@]:-}"; do
  if has_value_in_col "Italian_Term" "$iword"; then
    echo "Italian already exists: $iword"
  else
    append_value_to_idx "$it_col_idx" "$iword"
    echo "Italian added: $iword"
  fi
done

# --- If there are check requests, process them ---
single_mode=false
if [[ ${#words[@]} -eq 1 && -z "${word_list}" ]]; then
  single_mode=true
fi

for w in "${words[@]:-}"; do
  [[ -z "$w" ]] && continue
  if has_word "$w"; then
    if $single_mode; then
      echo yes
    else
      echo "$w: yes"
    fi
  else
    lower_w="${w:l}"
    append_value_to_idx "$et_col_idx" "$lower_w"
    if $single_mode; then
      echo no
    else
      echo "$w: no"
    fi
  fi
done