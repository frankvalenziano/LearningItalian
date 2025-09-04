#!/bin/zsh
# Simple check: return "yes" if the word exists in the English_Translation column, else "no".
# Usage: ./checkDictionary.zsh <csv_file> <word>
# Requires: csvkit (csvgrep, csvcut)

set -euo pipefail

# Usage:
#   ./checkDictionary.zsh <word>
#   ./checkDictionary.zsh <csv_file> <word>
if [[ $# -eq 1 ]]; then
  csv_file="/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.csv"
  word="$1"
elif [[ $# -eq 2 ]]; then
  csv_file="$1"
  word="$2"
else
  echo "Usage: $0 [<csv_file>] <word>" >&2
  exit 1
fi

# Ensure dependencies
for bin in csvcut csvgrep; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "Error: $bin (csvkit) is required." >&2
    exit 1
  fi
done

# Verify the column exists
if ! csvcut -n "$csv_file" | awk -F: '$2 ~ /English_Translation/ {found=1} END{exit(!found)}'; then
  echo "Error: Column 'English_Translation' not found in $csv_file" >&2
  exit 1
fi

# Use csvgrep exact match (-m) on the specific column, then see if any data rows matched
matches=$(csvgrep -c English_Translation -r "(?i)^${word}\$" "$csv_file" | tail -n +2 | wc -l | tr -d ' ')
if (( matches > 0 )); then
  echo yes
else
  # No match: append a new row with the word (lowercased) in the English_Translation column.
  lower_word="${word:l}"

  # Determine the index of English_Translation and total column count
  et_col_idx=$(csvcut -n "$csv_file" | awk -F: '$2 ~ /English_Translation/ {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$1); print $1}')
  col_count=$(csvcut -n "$csv_file" | wc -l | tr -d ' ')

  if [[ -z "$et_col_idx" || -z "$col_count" || "$col_count" -lt 1 ]]; then
    echo "Error: Could not determine columns for $csv_file" >&2
    exit 1
  fi

  # Build a CSV row where only the English_Translation column has the lowercased word
  new_row=""
  i=1
  while (( i <= col_count )); do
    if (( i == et_col_idx )); then
      val="$lower_word"
    else
      val=""
    fi
    if (( i == 1 )); then
      new_row="$val"
    else
      new_row="$new_row,$val"
    fi
    (( i++ ))
  done

  printf '%s\n' "$new_row" >> "$csv_file"
  echo no
fi
