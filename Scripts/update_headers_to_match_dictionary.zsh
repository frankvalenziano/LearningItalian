#!/usr/bin/env zsh
# normalize_calendar_csv.zsh
# Normalizes a CSV that already uses the 10-column header:
# English_Translation,Italian_Term,Italian_IPA,English_Sentence,Italian_Sentence,Italian_Sentence_IPA,CEFR_Level,Taxonomy,Tags,Notes
# - Pads/trims every row to the header length
# - Moves English_Sentence -> Notes and clears English_Sentence
# Requires: Miller (mlr) v6+

set -euo pipefail

# --- Default values
INPUT=""
OUTPUT=""

# --- Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      INPUT="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 --input INPUT.csv --output OUTPUT.csv"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
  echo "Error: --input and --output are required." >&2
  echo "Usage: $0 --input INPUT.csv --output OUTPUT.csv" >&2
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Error: input file not found: $INPUT" >&2
  exit 1
fi

# --- Expected header
EXPECTED_HDR="English_Translation,Italian_Term,Italian_IPA,English_Sentence,Italian_Sentence,Italian_Sentence_IPA,CEFR_Level,Taxonomy,Tags,Notes"

# Read the actual header
ACTUAL_HDR=$(head -1 "$INPUT")

# Verify header matches (fail if different)
if [[ "$ACTUAL_HDR" != "$EXPECTED_HDR" ]]; then
  echo "Error: header mismatch." >&2
  echo "Expected: $EXPECTED_HDR" >&2
  echo "Found:    $ACTUAL_HDR" >&2
  exit 1
fi

# Count columns
N_COLS=$(head -1 "$INPUT" | awk -F, '{print NF}')

# --- Normalize and move field ---
tail -n +2 "$INPUT" \
| mlr --icsv --ocsv --implicit-csv-header --allow-ragged-csv-input \
    put "while (NF < $N_COLS) { \$[(NF+1)] = \"\" }" \
    then label $EXPECTED_HDR \
    then put '$Notes=$English_Sentence; $English_Sentence=""' \
> "$OUTPUT"

echo "✅ Wrote normalized file to: $OUTPUT"