#!/usr/bin/env zsh
# Extract unique words from one or more PDFs and write to a single deduplicated list.
# Requires: pdftotext (poppler), coreutils (optional), standard BSD tools on macOS.
#
# Usage:
#   ./extractWordsFromPDF.zsh [options] <pdf1.pdf> [pdf2.pdf ...]
#
# Options:
#   -o, --out <file>        Output file for unique words (default: unique_words.txt)
#       --freq <file>       Also write frequency counts "count word" to this file
#       --minlen <N>        Minimum word length to keep (default: 1)
#       --stopwords <file>  File containing words to exclude (one per line, case-insensitive)
#       --append            Append new words to existing output then re-deduplicate (atomic)
#   -h, --help              Show help
#
# Notes:
#   * Words are normalized to lowercase, extracted with POSIX character class [:alpha:].
#   * Accented letters are preserved if your locale is UTF-8 (default on macOS).
#
set -euo pipefail
IFS=$'\n\t'

print_help() {
  cat <<'EOS'
Extract unique words from one or more PDFs and write to a single deduplicated list.

Usage:
  extractWordsFromPDF.zsh [options]

Options:
  --input-files <list>   Comma- or space-separated list of PDF paths
  --input-dir <dir>      Directory to scan for PDFs (recursively)
  -o, --output <file>    Output file for unique words (default: unique_words.txt)
      --freq <file>      Also write frequency counts "count word" to this file
      --minlen <N>       Minimum word length to keep (default: 1)
      --stopwords <file> File containing words to exclude (one per line, case-insensitive)
      --append           Append new words to existing output then re-deduplicate (atomic)
  -h, --help             Show help
EOS
}

# Defaults
output_file="unique_words.txt"
freq_file=""
minlen=1
stopwords_file=""
append_mode=false
input_files=()
input_dir=""

# Parse args
while (( $# > 0 )); do
  case "$1" in
    -h|--help)
      print_help; exit 0 ;;
    -o|--output)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      output_file="$2"; shift 2 ;;
    --freq)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      freq_file="$2"; shift 2 ;;
    --minlen)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      minlen="$2"; shift 2 ;;
    --stopwords)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      stopwords_file="$2"; shift 2 ;;
    --append)
      append_mode=true; shift ;;
    --input-files)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      # Support comma- or space-separated lists
      IFS=',' read -rA _tmp_list <<< "$2"
      if (( ${#_tmp_list[@]} > 1 )); then
        for f in "${_tmp_list[@]}"; do
          [[ -n "$f" ]] && input_files+=( "$f" )
        done
      else
        # If no commas, treat the whole value as a single path which may include spaces; also allow repeated flag usage
        input_files+=( "$2" )
      fi
      shift 2 ;;
    --input-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      input_dir="$2"; shift 2 ;;
    --)
      shift; break ;;
    -*)
      echo "Unknown option: $1" >&2; exit 2 ;;
    *)
      # Ignore stray positionals to avoid confusion; user should use flags
      echo "Warning: ignoring positional argument '$1' (use --input-files / --input-dir)" >&2
      shift ;;
  esac
done

# Build list of PDFs from --input-files and/or --input-dir
pdfs=()
# From explicit files
if (( ${#input_files[@]} > 0 )); then
  for p in "${input_files[@]}"; do
    if [[ -d "$p" ]]; then
      echo "Warning: '$p' is a directory; pass it via --input-dir" >&2
      continue
    fi
    pdfs+=( "$p" )
  done
fi
# From directory (recursive)
if [[ -n "$input_dir" ]]; then
  if [[ -d "$input_dir" ]]; then
    while IFS= read -r -d '' f; do
      pdfs+=( "$f" )
    done < <(find "$input_dir" -type f \( -iname '*.pdf' \) -print0)
  else
    echo "Error: --input-dir path not found: $input_dir" >&2
    exit 2
  fi
fi

if (( ${#pdfs[@]} == 0 )); then
  echo "Error: Provide at least one PDF via --input-files or --input-dir" >&2
  print_help
  exit 2
fi

# Check deps
if ! command -v pdftotext >/dev/null 2>&1; then
  echo "Error: pdftotext not found. Install with: brew install poppler" >&2
  exit 1
fi

# Prepare temp files
tmpdir=$(mktemp -d 2>/dev/null || mktemp -d -t extractwords)
trap 'rm -rf "$tmpdir"' EXIT
combined="$tmpdir/combined.txt"
words="$tmpdir/words.txt"
dedup="$tmpdir/dedup.txt"

# Build stopwords regex if provided (case-insensitive match per-line)
# We'll use grep -Fvi -f stopwords to filter them out.
if [[ -n "$stopwords_file" && ! -f "$stopwords_file" ]]; then
  echo "Error: stopwords file not found: $stopwords_file" >&2
  exit 2
fi

# Extract text from all PDFs and normalize to one word per line
: > "$combined"
for pdf in "${pdfs[@]}"; do
  if [[ ! -f "$pdf" ]]; then
    echo "Warning: skipping missing file $pdf" >&2
    continue
  fi
  # Append a newline between files to avoid word boundary issues
  {
    pdftotext "$pdf" - 2>/dev/null || {
      echo "Warning: pdftotext failed on $pdf" >&2
      continue
    }
    echo
  } >> "$combined"
done

# Normalize: keep alphabetic letters, lowercase, one word per line
cat "$combined" \
  | tr -cs '[:alpha:]' '\n' \
  | tr '[:upper:]' '[:lower:]' \
  | awk -v mlen="$minlen" 'length($0) >= mlen' \
  > "$words"

# Apply stopwords if provided
if [[ -n "$stopwords_file" ]]; then
  # Case-insensitive fixed-string filtering
  # Convert stopwords to lowercase to match our lowered words
  sw_lower="$tmpdir/stopwords_lower.txt"
  tr '[:upper:]' '[:lower:]' < "$stopwords_file" > "$sw_lower"
  grep -Fv -f "$sw_lower" "$words" > "$words.filtered" || true
  mv "$words.filtered" "$words"
fi

# Deduplicate
sort "$words" | uniq > "$dedup"

# If append mode, merge with existing output then re-deduplicate atomically
if $append_mode && [[ -f "$output_file" ]]; then
  tmp_merge="$tmpdir/merge.txt"
  cat "$output_file" "$dedup" | sort | uniq > "$tmp_merge"
  mv "$tmp_merge" "$output_file"
else
  # Write fresh output
  mkdir -p "${output_file:h}"
  mv "$dedup" "$output_file"
fi

# Frequency file if requested (from the final set)
if [[ -n "$freq_file" ]]; then
  # Reconstruct counts from all words prior to dedup & filters, but only include words present in output_file
  # Faster approach: recount from combined normalized list and filter by set in output_file
  # Build a lookup set for final words
  awk 'NR==FNR {a[$0]=1; next} a[$0]++ { }' /dev/null /dev/null > /dev/null 2>&1  # noop to placate awk on some systems
  sort "$words" | awk 'NF' | uniq -c | sed -E 's/^ +//; s/ +/\t/' > "$tmpdir/all_counts.tsv"
  # Keep counts only for the final unique set
  awk 'NR==FNR {set[$0]=1; next} ($2 in set){print $0}' "$output_file" "$tmpdir/all_counts.tsv" \
    | sort -nr -k1,1 \
    | sed $'s/\t/ /' > "$freq_file"
fi

echo "Wrote unique words to: $output_file"
if [[ -n "$freq_file" ]]; then
  echo "Wrote frequencies to: $freq_file"
fi