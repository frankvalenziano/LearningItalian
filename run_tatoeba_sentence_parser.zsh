#!/bin/zsh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

CSV_FILE="$1"
shift
caffeinate python3 "$SCRIPT_DIR/scrape_english_sentences.py" "$CSV_FILE" \
  --sources tatoeba \
  --user-agent "Frank's Italian Flashcards/1.0 (contact: frank.valenziano@proton.me)" \
  --tatoeba-interval 1.0 \
  "$@"
