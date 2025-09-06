#!/bin/zsh
# Check one or more words against the English_Translation column of a CSV dictionary.
# - Case-insensitive exact match
# - If a word is missing, append a new row placing the *lowercased* word in the
#   English_Translation column and leaving other columns empty.
#
# Usage examples:
#   ./checkDictionary.zsh --word apple
#   ./checkDictionary.zsh --dict "/path/to/Dictionary.csv" --word banana --word "green tea"
#   ./checkDictionary.zsh --dict /path/to/Dictionary.csv --word-list words.txt
#
# Notes:
# - When a single --word is given (no --word-list), output is just `yes` or `no` for
#   backward compatibility.
# - When multiple words are processed (via repeated --word or --word-list), output lines
#   are in the form: "<word>: yes" or "<word>: no".
#
# Requires: csvkit (csvcut, csvgrep)

set -euo pipefail

# Defaults
csv_file=""
word_list=""
typeset -a words

print_usage() {
  cat >&2 <<USAGE
Usage:
  $0 [--dict <csv_file>] [--word <word> ...]
  $0 [--dict <csv_file>] --word-list <path_to_txt>

Options:
  --dict       Path to the CSV dictionary file (default: \$csv_file)
  --word       A word to check (may be repeated)
  --word-list  Path to a text file containing one word per line to check
  -h, --help   Show this help
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
    -h|--help)
      print_usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; print_usage; exit 1 ;;
  esac
done

# Backward compat: allow positional forms
if [[ ${#words[@]} -eq 0 && -z "$word_list" ]]; then
  # positional: <word>  OR  <csv_file> <word>
  if [[ $# -eq 1 ]]; then
    words+=("$1")
  elif [[ $# -eq 2 ]]; then
    csv_file="$1"; words+=("$2")
  elif [[ $# -gt 2 ]]; then
    print_usage; exit 1
  fi
fi

# Ingest word list file if provided
if [[ -n "$word_list" ]]; then
  if [[ ! -f "$word_list" ]]; then
    echo "Error: word list not found: $word_list" >&2; exit 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Trim leading/trailing whitespace
    local trimmed="$line"
    trimmed="${trimmed##[[:space:]]*}"
    trimmed="${line%%[[:space:]]*}" # reset; zsh trim via parameter expansion below instead
  done < /dev/null  # no-op; keep shellcheck quiet
  # Proper trim and ingest
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Remove leading/trailing spaces and skip blanks/comments
    local w="$line"
    w="${w##[[:space:]]}"
    w="${w%%[[:space:]]}"
    [[ -z "$w" || "$w" == \#* ]] && continue
    words+=("$w")
  done < "$word_list"
fi

# Ensure we have words to process
if [[ ${#words[@]} -eq 0 ]]; then
  echo "Error: no words provided. Use --word or --word-list." >&2
  print_usage
  exit 1
fi

# Ensure dependencies
for bin in csvcut csvgrep; do
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

# Verify the column exists
if ! csvcut -n "$csv_file" | awk -F: '$2 ~ /English_Translation/ {found=1} END{exit(!found)}'; then
  echo "Error: Column 'English_Translation' not found in $csv_file" >&2
  exit 1
fi

# Determine column positions once
et_col_idx=$(csvcut -n "$csv_file" | awk -F: '$2 ~ /English_Translation/ {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$1); print $1}')
col_count=$(csvcut -n "$csv_file" | wc -l | tr -d ' ')
if [[ -z "$et_col_idx" || -z "$col_count" || "$col_count" -lt 1 ]]; then
  echo "Error: Could not determine columns for $csv_file" >&2
  exit 1
fi

# Helper: does CSV contain word (case-insensitive exact)?
has_word() {
  local needle="$1"
  # Use csvcut to safely extract the column (handles quoting), then awk for case-insensitive exact match
  csvcut -c English_Translation "$csv_file" \
    | awk -v w="$needle" 'BEGIN{IGNORECASE=1} NR==1{next} {if (tolower($0)==tolower(w)) {found=1; exit}} END{exit(found?0:1)}'
}

# Helper: append a new empty row with the word in English_Translation
append_word() {
  local word_lower="$1"
  local new_row=""
  local i=1
  while (( i <= col_count )); do
    local val=""
    if (( i == et_col_idx )); then
      val="$word_lower"
    fi
    if (( i == 1 )); then
      new_row="$val"
    else
      new_row="$new_row,$val"
    fi
    (( i++ ))
  done
  printf '%s\n' "$new_row" >> "$csv_file"
}

# Process words
single_mode=false
if [[ ${#words[@]} -eq 1 && -z "${word_list}" ]]; then
  single_mode=true
fi

for w in "${words[@]}"; do
  if has_word "$w"; then
    if $single_mode; then
      echo yes
    else
      echo "$w: yes"
    fi
  else
    lower_w="${w:l}"
    append_word "$lower_w"
    if $single_mode; then
      echo no
    else
      echo "$w: no"
    fi
  fi
done
