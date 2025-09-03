#!/usr/bin/env bash
set -euo pipefail

INPUT="Data Sources/Dictionary.csv"
OUTDIR="Data Sources/splits"
mkdir -p "$OUTDIR"

# List distinct (CEFR_Level, Taxonomy) pairs, then make a file for each
mlr --csv cut -f CEFR_Level,Taxonomy then uniq -a "$INPUT" | tail -n +2 | while IFS=, read -r level tax; do
  # Drop any quotes in the pair
  level=${level//\"/}
  tax=${tax//\"/}

  # Skip blanks
  [[ -z "$level" || -z "$tax" ]] && continue

  # Safe-ish filename
  safe_level=${level//\//-}
  safe_tax=${tax//\//-}
  safe_tax=${safe_tax//&/and}
  fname="$OUTDIR/${safe_level} ${safe_tax} Dictionary.csv"

  # Write the subset (header included)
  mlr --csv filter "\$CEFR_Level==\"$level\" && \$Taxonomy==\"$tax\"" "$INPUT" > "$fname"
  echo "wrote: $fname"
done
