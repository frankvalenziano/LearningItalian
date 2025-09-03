
#!/usr/bin/env zsh
set -euo pipefail

# Ensure consistent UTF-8 behavior
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export PYTHONIOENCODING=UTF-8

# Resolve script directory and operate from there so outputs land in this folder
script_dir=${0:A:h}
cd "$script_dir"

# Paths
tagger_py="/Users/frank/Documents/Tech/Code/LearningItalian/Scripts/taxonomy_tagger.py"
input_csv="/Users/frank/Documents/Tech/Code/LearningItalian/Data Sources/Frank's Core CEFR English-Italian Dictionary.csv"
cache_path="$script_dir/wordnet_cache.json"

# Helpful logging
echo "[rebuild_taxonomy_caches] Working directory: $PWD"
echo "[rebuild_taxonomy_caches] Building WordNet cache → $cache_path"

# Build a WordNet lemma -> lexnames cache for fast taxonomy tagging
# We write the cache to $cache_path and only download corpora if missing.
CACHE_PATH="$cache_path" python3 - <<'PY'
import json, os, sys

# NLTK setup
try:
    import nltk
    from nltk.corpus import wordnet as wn
    from nltk.stem import WordNetLemmatizer
except Exception as e:
    print(f"[ERROR] NLTK import failed: {e}", file=sys.stderr)
    raise

# Ensure required corpora exist (quietly)
try:
    nltk.data.find('corpora/wordnet')
except LookupError:
    nltk.download('wordnet', quiet=True)
# Some environments need omw-1.4 for expanded lemmas
try:
    nltk.data.find('corpora/omw-1.4')
except LookupError:
    try:
        nltk.download('omw-1.4', quiet=True)
    except Exception:
        pass  # non-fatal

lemmatizer = WordNetLemmatizer()
cache = {}
for syn in wn.all_synsets():
    ln = syn.lexname()
    for lemma in syn.lemma_names():
        key = lemmatizer.lemmatize(lemma.lower())
        cache.setdefault(key, set()).add(ln)

# Convert sets to sorted lists for JSON serialization
cache = {k: sorted(v) for k, v in cache.items()}

out_path = os.environ.get('CACHE_PATH', 'wordnet_cache.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(cache, f, indent=2, ensure_ascii=False)
print(f"Wrote {len(cache)} lemmas to {out_path}")
PY

# Verify cache exists
if [[ ! -f "$cache_path" ]]; then
  echo "[rebuild_taxonomy_caches] ERROR: Cache not found at $cache_path" >&2
  exit 1
fi

# Run the taxonomy tagger using the prebuilt WordNet cache
# Pass through any extra CLI flags the user provides to this script ("$@")
echo "[rebuild_taxonomy_caches] Running taxonomy_tagger.py with WordNet cache"
python3 "$tagger_py" \
  --input "$input_csv" \
  --english-column English_Term \
  --wordnet-cache "$cache_path" \
  --rate-limit 0.1 \
  "$@"
