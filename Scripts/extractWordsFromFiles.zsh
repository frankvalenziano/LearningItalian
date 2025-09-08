#!/usr/bin/env zsh
# Extract unique words from one or more PDF/TXT/EPUB files and write to a single deduplicated list.
# Requires: pdftotext (poppler), ebook-convert (Calibre) or epub2txt (optional), coreutils (optional), standard BSD tools on macOS.
#
# Usage:
#   ./extractWordsFromPDF.zsh [options] <file1> [file2 ...]
#
# Options:
#   -o, --output <file>     Output file for unique words (default: unique_words.txt)
#       --freq <file>       Also write frequency counts "count word" to this file
#       --minlen <N>        Minimum word length to keep (default: 3)
#       --stopwords <file>  File containing words to exclude (one per line, case-insensitive)
#       --append            Append (default): merge with existing output, sort & deduplicate (in-place).
#       --overwrite         Replace existing output with only the newly extracted words.
#       # Before writing, a ".bak" backup of the target output file is created if it exists.
#   -h, --help              Show help
#
# Notes:
#   * Words are normalized to lowercase, extracted with POSIX character class [:alpha:].
#   * Accented letters are preserved if your locale is UTF-8 (default on macOS).
#   * Dependencies are conditional: 'pdftotext' only if processing PDFs; EPUBs require 'ebook-convert' (Calibre) or 'epub2txt' at runtime.
#
set -euo pipefail
IFS=$'\n\t'

print_help() {
  cat <<'EOS'
Extract unique words from one or more PDF/TXT/EPUB files and write to a single deduplicated list.

Usage:
  extractWordsFromPDF.zsh [options]

Options:
  --input-files <list>   Comma- or space-separated list of file paths (PDF/TXT/EPUB)
  --input-dir <dir>      Directory to scan for files (recursively)
  -o, --output <file>    Output file for unique words (default: unique_words.txt)
      --freq <file>      Also write frequency counts "count word" to this file
      --minlen <N>       Minimum word length to keep (default: 3)
      --stopwords <file> File containing words to exclude (one per line, case-insensitive)
      --append           Append (default): merge with existing output, sort & deduplicate (in-place).
      --overwrite        Replace existing output with only the newly extracted words.
        # Before writing, a ".bak" backup of the target output file is created if it exists.
  -h, --help             Show help
EOS
}

# Defaults
output_file=""
freq_file=""
minlen=3
stopwords_file=""
append_mode=true
input_files=()
input_dir=""

# Parse args
while (( $# > 0 )); do
  case "$1" in
    -h|--help)
      print_help; exit 0 ;;
    -o|--output|--out)
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
    --overwrite)
      append_mode=false; shift ;;
    --input-files)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      # Support comma- or whitespace-separated lists in a single value, and allow repeated flag usage
      local _val="$2"
      local IFS=$', \t\n '
      read -rA _tmp_list <<< "$_val"
      for f in "${_tmp_list[@]}"; do
        [[ -n "$f" ]] && input_files+=( "$f" )
      done
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

# Build list of files from --input-files and/or --input-dir
files=()
# From explicit files
if (( ${#input_files[@]} > 0 )); then
  for p in "${input_files[@]}"; do
    if [[ -d "$p" ]]; then
      echo "Warning: '$p' is a directory; pass it via --input-dir" >&2
      continue
    fi
    files+=( "$p" )
  done
fi
# From directory (recursive)
if [[ -n "$input_dir" ]]; then
  if [[ -d "$input_dir" ]]; then
    while IFS= read -r -d '' f; do
      files+=( "$f" )
    done < <(find "$input_dir" -type f \( -iname '*.pdf' -o -iname '*.txt' -o -iname '*.epub' \) -print0)
  else
    echo "Error: --input-dir path not found: $input_dir" >&2
    exit 2
  fi
fi

if (( ${#files[@]} == 0 )); then
  echo "Error: Provide at least one PDF/TXT/EPUB file via --input-files or --input-dir" >&2
  print_help
  exit 2
fi

# Determine which converters are needed based on file extensions
needs_pdf=false
for f in "${files[@]}"; do
  [[ "${f##*.}" = "pdf" || "${f##*.}" = "PDF" ]] && needs_pdf=true
done

# Check deps
if $needs_pdf; then
  if ! command -v pdftotext >/dev/null 2>&1; then
    echo "Error: pdftotext not found but required for PDF inputs. Install with: brew install poppler" >&2
    exit 1
  fi
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

# Extract text from all files and normalize to one word per line
: > "$combined"
for file in "${files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Warning: skipping missing file $file" >&2
    continue
  fi
  ext="${file##*.}"
  ext="${ext:l}"
  # Append a newline between files to avoid word boundary issues
  {
    if [[ "$ext" == "pdf" ]]; then
      pdftotext "$file" - 2>/dev/null || {
        echo "Warning: pdftotext failed on $file" >&2
        continue
      }
    elif [[ "$ext" == "txt" ]]; then
      cat "$file"
    elif [[ "$ext" == "epub" ]]; then
      if command -v ebook-convert >/dev/null 2>&1; then
        ebook-convert "$file" "$tmpdir/out.txt" --txt-output-encoding=UTF-8
        cat "$tmpdir/out.txt"
      elif command -v epub2txt >/dev/null 2>&1; then
        epub2txt "$file"
      else
        echo "Error: no EPUB converter found (install Calibre or epub2txt)" >&2
        continue
      fi
    else
      echo "Warning: unsupported file type for $file, skipping" >&2
      continue
    fi
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

# Create a .bak backup of the target output file if it exists
if [[ -f "$output_file" ]]; then
  ts=$(date +"%Y%m%d-%H%M%S")
  cp -p "$output_file" "${output_file}.bak-${ts}"
fi

# Write results
if $append_mode; then
  # Append mode (default): merge with existing output in-place (after creating .bak above)
  if [[ -f "$output_file" ]]; then
    tmp_merge="$tmpdir/merge.txt"
    cat "$output_file" "$dedup" | sort | uniq > "$tmp_merge"
    mkdir -p "${output_file:h}"
    mv "$tmp_merge" "$output_file"
    echo "Append mode: merged into existing file (backup created): $output_file"
  else
    mkdir -p "${output_file:h}"
    mv "$dedup" "$output_file"
    echo "Append mode: created new output file: $output_file"
  fi
else
  # Overwrite mode: replace the output file with only the new unique words (backup created above if existed)
  mkdir -p "${output_file:h}"
  mv "$dedup" "$output_file"
  echo "Overwrite mode: replaced contents of $output_file"
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