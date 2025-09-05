caffeinate python3 "/Users/frank/Documents/Tech/Code/LearningItalian/Scripts/taxonomy_tagger.py" \
  --input "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.csv" \
  --output "/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Dictionary.tagged.csv" \
  --english-column English_Translation \
  --category-column Taxonomy \
  --wordnet-cache "/Users/frank/Documents/Tech/Code/LearningItalian/wordnet_cache.json" \
  --confidence 0.6 \
  --margin 0.2 \
  --taxonomy-table "/Users/frank/Documents/Tech/Code/LearningItalian/Scripts/taxonomy_table.json" \
  --overwrite yes
