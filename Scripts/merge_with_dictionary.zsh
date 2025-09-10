#!/usr/bin/env zsh
set -euo pipefail
IFS=$'\n\t'

# Defaults (relative to repo layout: Scripts/../Data Sources)
script_dir="${0:A:h}"
repo_root="${script_dir:h}"
DICT_DEFAULT=""
NEW_DEFAULT=""

dict="$DICT_DEFAULT"
new_words="$NEW_DEFAULT"

print_help() {
  cat <<'EOS'
Usage: merge_with_dictionary.zsh [--dict <Dictionary.csv>] [--new <new_words.txt>]
Appends new words into Dictionary.csv using header schema, backs up, de-dupes, sorts.
EOS
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) print_help; exit 0 ;;
    --dict) [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
            dict="$2"; shift 2 ;;
    --new)  [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
            new_words="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; print_help; exit 2 ;;
  esac
done

# Normalize to absolute paths (handles ../ and spaces)
 dict_abs="${dict:A}"
 new_words_abs="${new_words:A}"

[[ -f "$dict_abs" ]] || { echo "Dictionary not found: $dict_abs (from --dict '$dict'; CWD: $(pwd))" >&2; exit 1; }
[[ -f "$new_words_abs" ]] || { echo "new_words.txt not found: $new_words_abs (from --new '$new_words'; CWD: $(pwd))" >&2; exit 1; }

tmpdir="$(mktemp -d -t merge_dict_XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

header="$(head -n 1 "$dict_abs")"
(( ${#header} > 0 )) || { echo "Dictionary header is empty" >&2; exit 1; }

# Determine field count and English column index (supports English_Translation or English_Term)
ncols=$(echo "$header" | awk -F',' '{print NF}')
eng_idx=$(echo "$header" | awk -F',' '
  BEGIN{IGNORECASE=1}
  {
    for(i=1;i<=NF;i++){
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i);
      if($i=="English_Translation" || $i=="English_Term"){print i; exit}
    }
  }')
[[ -n "$eng_idx" ]] || { echo "Could not find English column (English_Translation or English_Term) in header." >&2; exit 1; }

# Make a timestamped backup
ts=$(date +"%Y%m%d-%H%M%S")
cp -p "$dict_abs" "${dict_abs}.bak-${ts}"
echo "Backup written: ${dict_abs}.bak-${ts}"

# Prepare lines to append, one CSV row per word.
# Words are expected to be plain tokens (no commas). We trim and skip blanks.
# We do minimal CSV safety by quoting and escaping if a word contains a comma or a quote.
pad_commas() {
  # Produce ncols-1 commas
  local pad=""
  local i=2
  while (( i <= ncols )); do
    pad="${pad},"
    (( i++ ))
  done
  print -r -- "$pad"
}
padding="$(pad_commas)"

new_rows="$tmpdir/new_rows.csv"
: > "$new_rows"

# Split dictionary into header + body
dict_body="$tmpdir/dict_body.csv"
tail -n +2 "$dict_abs" > "$dict_body" || true

# Build associative array of existing English words (lowercase)
typeset -A existing_words
while IFS= read -r line || [[ -n "$line" ]]; do
  # Extract English column
  fields=("${(s:,:)line}")
  word="${fields[$eng_idx]}"
  # Remove surrounding quotes if any
  if [[ "$word" =~ ^\".*\"$ ]]; then
    word="${word:1:-1}"
    # Unescape double quotes
    word="${word//\"\"/\"}"
  fi
  key="${word:l}"
  existing_words["$key"]=1
done < "$dict_body"

while IFS= read -r word || [[ -n "$word" ]]; do
  # trim
  word="${word#"${word%%[![:space:]]*}"}"
  word="${word%"${word##*[![:space:]]}"}"
  [[ -z "$word" ]] && continue
  key="${word:l}"
  # Skip if already exists
  if [[ -n "${existing_words[$key]-}" ]]; then
    continue
  fi
  existing_words["$key"]=1
  # Escape if needed
  if [[ "$word" == *","* || "$word" == *"\""* ]]; then
    esc="${word//\"/\"\"}"
    word="\"$esc\""
  fi
  print -r -- "${word}${padding}" >> "$new_rows"
done < "$new_words_abs"

# Combine body + new rows
combined="$tmpdir/combined.csv"
cat "$dict_body" "$new_rows" > "$combined"

# Sort combined by English column (case-insensitive), then de-duplicate by English column, keep first occurrence
deduped="$tmpdir/deduped.csv"
sort -t, -k${eng_idx},${eng_idx} -f "$combined" | awk -F',' -v OFS=',' -v K="$eng_idx" '
  {
    key = tolower($K)
    if (!(key in seen)) {
      seen[key]=1
      print $0
    }
  }
' > "$deduped"

# Write back with original header
{
  print -r -- "$header"
  cat "$deduped"
} > "$dict_abs"

echo "Merged $(wc -l < "$new_rows" | tr -d ' ') new rows (before de-dup)."
echo "Dictionary updated: $dict_abs"