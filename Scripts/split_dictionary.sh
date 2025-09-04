#!/usr/bin/env bash
set -euo pipefail

# Inputs/outputs
INPUT="Data Sources/Dictionary.csv"
OUTDIR="Data Sources"
mkdir -p "$OUTDIR"

# Get distinct CEFR levels, skip header
mlr --csv cut -f CEFR_Level then uniq -a "$INPUT" | tail -n +2 | while IFS= read -r raw_level; do
  # Remove any surrounding quotes and trim whitespace
  level="${raw_level%\"}"; level="${level#\"}"
  level="$(printf "%s" "$level" | awk '{$1=$1; print}')"

  # Skip blanks
  [[ -z "${level}" ]] && continue

  # Make a safe filename component (keep spaces; sanitize slashes/&)
  safe_level="${level//\//-}"
  safe_level="${safe_level//&/and}"

  out_file="${OUTDIR}/${safe_level} Dictionary.csv"

  # Write rows matching this level (header included)
  mlr --csv filter '$CEFR_Level == "'"$level"'"' "$INPUT" > "$out_file"

  echo "wrote: $out_file"
done
