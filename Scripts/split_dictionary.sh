#!/usr/bin/env bash
set -euo pipefail

# Defaults
INPUT="Data Sources/Dictionary.csv"
OUTDIR="Data Sources"
WITH_TAXONOMY=0

# Usage helper
usage() {
  cat <<EOF
Usage: $(basename "$0") [--input <path/to/Dictionary.csv>] [--outdir <dir>] [--with-taxonomy]

Splits Dictionary.csv into multiple CSVs:
  (default) by CEFR_Level only -> "A1 Dictionary.csv", "B2 Dictionary.csv", ...
  (--with-taxonomy) by CEFR_Level and Taxonomy -> "A1 Food Dictionary.csv", ...

Options:
  --input           Path to source CSV (default: \$INPUT)
  --outdir          Output directory (default: \$OUTDIR)
  --with-taxonomy   Also split by Taxonomy within each CEFR_Level
  -h, --help        Show this help
EOF
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      [[ $# -ge 2 ]] || { echo "Missing value for --input" >&2; exit 2; }
      INPUT="$2"; shift 2;;
    --outdir)
      [[ $# -ge 2 ]] || { echo "Missing value for --outdir" >&2; exit 2; }
      OUTDIR="$2"; shift 2;;
    --with-taxonomy|--level-and-taxonomy|--by-taxonomy)
      WITH_TAXONOMY=1; shift;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown option: $1" >&2
      usage; exit 2;;
  esac
done

mkdir -p "$OUTDIR"

# Small helpers
trim() {
  # trims leading/trailing whitespace
  awk '{$1=$1; print}'
}
sanitize_component() {
  # Keep spaces; replace slashes and ampersands for filenames
  local s="$1"
  s="${s//\//-}"
  s="${s//&/and}"
  printf '%s' "$s"
}

if [[ "$WITH_TAXONOMY" -eq 0 ]]; then
  # ---------- Split by CEFR_Level only ----------
  # Get distinct CEFR levels, skip header
  mlr --csv cut -f CEFR_Level then uniq -a "$INPUT" \
  | tail -n +2 \
  | while IFS= read -r raw_level; do
      # remove surrounding quotes and trim whitespace
      level="${raw_level%\"}"; level="${level#\"}"
      level="$(printf "%s" "$level" | trim)"

      [[ -z "$level" ]] && continue

      safe_level="$(sanitize_component "$level")"
      out_file="${OUTDIR}/${safe_level} Dictionary.csv"

      mlr --csv filter '$CEFR_Level == "'"$level"'"' "$INPUT" > "$out_file"
      echo "wrote: $out_file"
    done
else
  # ---------- Split by CEFR_Level and Taxonomy ----------
  # Use TSV for robust field splitting, then uniq. Skip header.
  mlr --icsv --otsv cut -f CEFR_Level,Taxonomy then uniq -a "$INPUT" \
  | tail -n +2 \
  | while IFS=$'\t' read -r raw_level raw_tax; do
      # Clean up both fields
      level="${raw_level%\"}"; level="${level#\"}"
      level="$(printf "%s" "$level" | trim)"
      tax="${raw_tax%\"}"; tax="${tax#\"}"
      tax="$(printf "%s" "$tax" | trim)"

      # Skip if missing either component
      [[ -z "$level" ]] && continue
      [[ -z "$tax" ]] && continue

      safe_level="$(sanitize_component "$level")"
      safe_tax="$(sanitize_component "$tax")"
      out_file="${OUTDIR}/${safe_level} ${safe_tax} Dictionary.csv"

      # Filter matching both fields
      mlr --csv filter '$CEFR_Level == "'"$level"'" && $Taxonomy == "'"$tax"'"' "$INPUT" > "$out_file"
      echo "wrote: $out_file"
    done
fi
