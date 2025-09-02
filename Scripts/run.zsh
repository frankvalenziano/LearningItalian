#!/bin/zsh
caffeinate python3 get_sentences_from_local_sources.py \
      --sources-dir "/Users/frank/Library/Mobile Documents/com~apple~CloudDocs/Family/Languages/English Sources" \
      --input-csv   "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Frank's Core CEFR English-Italian.csv" \
      --output-csv  "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Frank's Core CEFR English-Italian.updated.csv" \
      --min-words 5 \
      --max-words 18 \
      --prefer-shorter \
      --overwrite no \
      --user-agent "Frank's Italian Flashcards/1.0 (contact:frank.valenziano@proton.me)"
