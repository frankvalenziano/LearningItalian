#!/bin/zsh
caffeinate python3 "/Users/frank/Documents/Tech/Code/LearningItalian/Scripts/get_sentences.py" \
      --sources-dir "/Users/frank/Library/Mobile Documents/com~apple~CloudDocs/Family/Languages/English Sources" \
      --input-csv  "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.csv" \
      --output-csv  "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.sentences.csv" \
      --min-words 3 \
      --max-words 28 \
      --prefer-shorter \
      --overwrite no
