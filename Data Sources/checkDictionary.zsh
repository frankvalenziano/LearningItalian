#!/bin/zsh
# Simple check: return "yes" if the word exists in the English_Translation column, else "no".
# Usage: ./checkDictionary.zsh <csv_file> <word>
# Requires: csvkit (csvgrep, csvcut)

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <csv_file> <word>" >&2
  exit 1
fi

csv_file=$1
word=$2

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
matches=$(csvgrep -c English_Translation -m "$word" "$csv_file" | tail -n +2 | wc -l | tr -d ' ')
if (( matches > 0 )); then
  echo yes
else
  echo no
fi
