#!/bin/zsh
caffeinate python3 "/Users/frank/Documents/Tech/Code/LearningItalian/Scripts/get_sentences.py" \
      --sources-dir "/Users/frank/Library/Mobile Documents/com~apple~CloudDocs/Family/Languages/English Sources" \
      --input-csv  "/Users/frank/Downloads/"
      --output-csv  "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Frank's Core CEFR English-Italian.updated.csv" \
      --min-words 6 \
      --max-words 28 \
      --prefer-shorter \
      --overwrite no
