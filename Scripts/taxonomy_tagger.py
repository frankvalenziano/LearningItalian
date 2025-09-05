#!/usr/bin/env python3
"""
Taxonomy tagger for CEFR word lists using **WordNet supersenses**, plus exact term overrides.

Usage examples:
  python3 taxonomy_tagger.py --input "Frank's Master CEFR Word List.csv" --output tagged.csv
  python3 taxonomy_tagger.py --input words.csv --output words_tagged.csv --english-column English --overwrite yes
  python3 taxonomy_tagger.py --input words.csv --dry-run
  python3 taxonomy_tagger.py --input words.csv --overrides-file overrides.json
  python3 taxonomy_tagger.py --input words.csv --taxonomy-table taxonomy_table.json
  python3 taxonomy_tagger.py --input words.csv --build-wordnet-cache wordnet_cache.json
  python3 taxonomy_tagger.py --input words.csv --wordnet-cache wordnet_cache.json --output words_tagged.csv
  python3 taxonomy_tagger.py --input words.csv --wordnet-cache wordnet_cache.json --confidence 0.6 --margin 0.2 --use-cefr-priors --output words_tagged.csv

Notes:
- This script uses only NLTK WordNet lexnames (aka supersenses) mapped to your taxonomy via `taxonomy_table.json` (the `wordnet_supersenses` list in each category's `mappings`).
- If NLTK WordNet data is missing, this script will download it on first run.
- Output column defaults to 'Taxonomy' (override with --category-column).
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Tuple, Set, List, Dict, Any
import time

# Optional but helpful
try:
    from tqdm import tqdm  # progress bar
except Exception:  # pragma: no cover
    tqdm = lambda x, **k: x

# NLTK imports & lazy data setup
try:
    import nltk
    from nltk.corpus import wordnet as wn
    from nltk.stem import WordNetLemmatizer
except ImportError as e:
    print("This script requires nltk. Install with: pip install nltk", file=sys.stderr)
    raise

def _ensure_wordnet_downloaded():
    """Ensure required NLTK corpora are available, download if missing."""
    try:
        wn.ensure_loaded()
    except LookupError:
        nltk.download('wordnet', quiet=True)
        nltk.download('omw-1.4', quiet=True)  # multilingual glosses
        wn.ensure_loaded()

# ------------------------
# Legacy -> New taxonomy mapping and finalizer
# ------------------------
LEGACY_TO_TAXONOMY = {
    'Greetings': 'Social',
    'Numbers & Quantities': 'Time & Quantity',
    'Calendar & Time': 'Time & Quantity',
    'Food': 'Food',
    'Social': 'Social',
    'Shopping': 'Shopping',
    'Travel': 'Travel',
    'Work & School': 'Professional',
    'Medical & Health': 'Health',
    'Opinions & Communication': 'Social',
    'Feelings & Emotions': 'Emotions',
    'Events & Activities': 'Activities',
    'Idioms & Abstract': 'Abstract',
}

def finalize_category(raw: Optional[str], taxonomy_keys: Set[str]) -> Optional[str]:
    if not raw:
        return None
    mapped = LEGACY_TO_TAXONOMY.get(raw, raw)
    mapped = mapped.strip()
    return mapped if mapped in taxonomy_keys else None

# ------------------------
# Taxonomy table loader & reverse maps
# ------------------------
def load_taxonomy_table(path: str) -> Tuple[dict, dict, list]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"taxonomy table not found: {path}")
    with p.open('r', encoding='utf-8') as f:
        table = json.load(f)
    priority = []
    wn_super_map = {}
    for cat, spec in table.items():
        # Only treat objects that look like category specs (have a 'mappings' key)
        if not isinstance(spec, dict) or 'mappings' not in spec:
            continue
        priority.append(cat)
        maps = spec.get('mappings', {})
        for ss in maps.get('wordnet_supersenses', []) or []:
            wn_super_map.setdefault(ss.lower(), set()).add(cat)
    return table, wn_super_map, priority

def load_json_cache(path: Optional[str]) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"Cache not found (optional): {path}", file=sys.stderr)
        return {}
    with p.open('r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"Cache JSON error in {path}: {e}", file=sys.stderr)
            return {}



# ------------------------
# WordNet lexname -> category heuristic map
# ------------------------
LEXNAME_TO_CATEGORY = {
    # Nouns
    'noun.food': 'Food',
    'noun.person': None,          # handled with kinship overrides first, else often Social/Work
    'noun.body': 'Medical & Health',
    'noun.time': 'Time & Quantity',
    'noun.quantity': 'Time & Quantity',
    'noun.feeling': 'Feelings & Emotions',
    'noun.event': 'Events & Activities',
    'noun.act': 'Events & Activities',
    'noun.communication': 'Opinions & Communication',
    'noun.location': 'Travel',    # weak but useful signal
    'noun.artifact': None,        # could be Shopping (clothes) or Travel (vehicles) — use keywords
    'noun.group': 'Social',       # people groups (family, teams)
    # Verbs
    'verb.communication': 'Opinions & Communication',
    'verb.motion': 'Travel',
    'verb.social': 'Social',
    'verb.cognition': 'Opinions & Communication',
    'verb.body': 'Medical & Health',
    'verb.consumption': 'Food',
    'verb.contact': None,
    'verb.creation': None,
}

# Clothing & money keywords to disambiguate Shopping
SHOPPING_HINTS = set(
    [
        'clothes','clothing','garment','shirt','tshirt','t-shirt','pants','trousers','jeans','dress','skirt','shoe','sneaker','boot','sock','hat','coat','jacket','scarf','glove',
        'buy','sell','price','cost','pay','cash','credit','debit','receipt','refund','discount','sale','shopping','market','store','shop','mall','brand','size','fit'
    ]
)

# Kinship/person terms strongly indicate Social
KINSHIP_HINTS = set([
    'mother','father','mom','dad','parent','sister','brother','son','daughter','wife','husband','aunt','uncle','grandmother','grandfather','grandparent','cousin','family','friend','neighbor','colleague','coworker','boss','partner','relative','child','baby','people','person','man','woman'
])

# Additional lightweight heuristics to reduce unknowns
TITLES = {"sir","maam","ma'am","madam","mrs","mr","ms","miss","maestro","doctor","dr","prof","professor","captain","chief"}
RELIGION_HINTS = {"buddha","buddhist","monk","nun","temple","church","priest","imam","rabbi"}
ROOM_OBJECT_HINTS = {"apartment","house","home","bathroom","bedroom","kitchen","living room","dining room","hallway","garage","room","window","door","floor","ceiling","wall","roof","downstairs","upstairs","balcony"}
RELATIONSHIP_HINTS = {"boyfriend","girlfriend","partner","customer","cop","classmate","neighbor"}

_SUFFIX_CATEGORY_RULES = [
    (re.compile(r"(?i).*(ist|ian)$"), "Work & School"),   # artist, historian, musician, etc.
    (re.compile(r"(?i).*(hood|ship)$"), "Social"),        # childhood, friendship
]

STOPWORD_LIKE = {"and","or","but","because","between","during","both","again","also","anybody","anyone","anything","across"}


def classify_with_rules(term: str) -> Optional[str]:
    t = normalize_text(term)
    t = t.replace("'", "")  # normalize apostrophes, e.g., ma'am -> maam

    # Titles/honorifics -> Social
    if t in TITLES:
        return "Social"

    # Religion-related vocabulary -> Social (people groups) unless already captured
    if t in RELIGION_HINTS:
        return "Social"

    # Relationship terms that are not in KINSHIP_HINTS
    if t in RELATIONSHIP_HINTS:
        return "Social"

    # Common rooms/household/learning locations -> Home (except classroom/desk -> Professional)
    if t in ROOM_OBJECT_HINTS:
        return "Professional" if t in {"classroom","desk"} else "Home"

    # Suffix-based fallbacks
    for rx, cat in _SUFFIX_CATEGORY_RULES:
        if rx.match(t):
            return cat

    # High-frequency function words: assign to Opinions & Communication so they are not left unknown
    if t in STOPWORD_LIKE:
        return "Opinions & Communication"

    # Gendered profession mapping
    if t == "actress":
        return "Work & School"

    if t == "adult" or t == "boy" or t == "girl":
        return "Social"

    return None


def load_seeds(seeds_file: Optional[str]):
    if seeds_file:
        with open(seeds_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    return DEFAULT_SEEDS


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def compile_seed_patterns(seeds: dict):
    """Prepare regex patterns; longer multi-word phrases first to avoid shadowing by single tokens."""
    items = []
    for cat, words in seeds.items():
        for w in words:
            w_norm = normalize_text(w)
            if ' ' in w_norm:
                # phrase: match as a substring on word boundaries around ends when sensible
                pat = re.compile(rf"(?<!\w){re.escape(w_norm)}(?!\w)")
            else:
                pat = re.compile(rf"\b{re.escape(w_norm)}\b")
            items.append((cat, w_norm, pat))
    # Sort by length of seed (desc) so longer phrases win
    items.sort(key=lambda t: len(t[1]), reverse=True)
    return items


def classify_with_seeds(term: str, seed_patterns) -> Optional[str]:
    txt = normalize_text(term)
    for cat, seed, pat in seed_patterns:
        if pat.search(txt):
            return cat
    return None


def wn_lexname_votes(term: str, lemmatizer: WordNetLemmatizer, cache: Optional[dict] = None) -> Counter:
    if cache:
        entry = cache.get(normalize_text(term)) or cache.get(term)
        if isinstance(entry, dict) and isinstance(entry.get('votes'), dict):
            return Counter({k: int(v) for k, v in entry['votes'].items()})
    votes = Counter()
    txt = normalize_text(term)
    pos_tags = [('n','n'), ('v','v'), ('a','a'), ('r','r')]
    for _, pos in pos_tags:
        lemma = lemmatizer.lemmatize(txt, pos=pos)
        synsets = wn.synsets(lemma, pos=pos)
        for s in synsets:
            lex = s.lexname()
            mapped = LEXNAME_TO_CATEGORY.get(lex)
            if mapped:
                votes[mapped] += 3
            gloss = (s.definition() or '').lower()
            examples = ' '.join(s.examples()).lower() if s.examples() else ''
            hypernyms = ' '.join([h.name().split('.')[0] for h in s.hypernyms()]).lower()
            text_blob = ' '.join([gloss, examples, hypernyms])

            if lex in ('noun.artifact', 'noun.possession', 'noun.communication'):
                if any(k in text_blob for k in SHOPPING_HINTS):
                    votes['Shopping'] += 2
            if lex in ('noun.person','verb.social'):
                if any(k in (lemma, txt, text_blob) for k in KINSHIP_HINTS):
                    votes['Social'] += 3
            if any(k in text_blob for k in ['vehicle','car','bus','train','plane','airport','station','ticket','passport','luggage','journey','travel','trip','hotel']):
                votes['Travel'] += 2
            if any(k in text_blob for k in ['food','drink','beverage','ingredient','dish','meal','eat','drink','restaurant','menu']):
                votes['Food'] += 2
            if any(k in text_blob for k in ['disease','illness','medicine','medical','doctor','pain','fever','injury','symptom','hospital','clinic','pharmacy','vaccine']):
                votes['Medical & Health'] += 2
            if any(k in text_blob for k in ['emotion','feeling','mood','happy','sad','angry','fear','anxiety','joy']):
                votes['Feelings & Emotions'] += 1
            if any(k in text_blob for k in ['time','day','month','year','season','hour','minute','second','calendar']):
                votes['Time & Quantity'] += 1
            if any(k in text_blob for k in ['number','quantity','amount','measure','percent','ratio']):
                votes['Time & Quantity'] += 1
            if any(k in text_blob for k in ['event','activity','festival','party','sport','game','competition','ceremony']):
                votes['Events & Activities'] += 1
            if any(k in text_blob for k in ['say','tell','speak','discussion','argue','explain','opinion','belief','think']):
                votes['Opinions & Communication'] += 1
    return votes



# ------------------------
# WordNet lexname collector and priority chooser
# ------------------------
def wn_lexnames_raw(term: str, lemmatizer: WordNetLemmatizer, cache: Optional[dict] = None) -> Set[str]:
    if cache:
        entry = cache.get(normalize_text(term)) or cache.get(term)
        if isinstance(entry, dict) and 'lexnames' in entry:
            return set([str(x).lower() for x in entry['lexnames']])
        if isinstance(entry, list):  # backward-compat
            return set([str(x).lower() for x in entry])
    names = set()
    txt = normalize_text(term)
    for _, pos in [('n','n'),('v','v'),('a','a'),('r','r')]:
        lemma = lemmatizer.lemmatize(txt, pos=pos)
        for s in wn.synsets(lemma, pos=pos):
            names.add(s.lexname().lower())
    return names

PRIORITY_ORDER = []  # will be filled from taxonomy_table order

def priors_for_cefr(level: str) -> Counter:
    lvl = (level or '').strip().upper()
    # conservative, gentle nudges only
    if lvl.startswith('A1'):
        return Counter({'Social': 1, 'Time & Quantity': 1})
    if lvl.startswith('A2'):
        return Counter({'Shopping': 1, 'Travel': 1})
    if lvl.startswith('B1'):
        return Counter({'Activities': 1, 'Professional': 1})
    # B2/C1/C2: no priors by default
    return Counter()

# CEFR -> taxonomy category priority lists (from most to least typical)
CEFR_TAXONOMY_PRIORITY = {
    'A1': ['Social', 'Time & Quantity', 'Food', 'Home', 'Shopping'],
    'A2': ['Shopping', 'Travel', 'Home', 'Health', 'Activities'],
    'B1': ['Activities', 'Travel', 'Professional', 'Opinions & Communication'],
    'B2': ['Professional', 'Opinions & Communication', 'Abstract', 'Health'],
    'C1': ['Abstract', 'Opinions & Communication', 'Professional', 'Activities'],
    'C2': ['Abstract', 'Professional', 'Social', 'Emotions'],
}

def category_from_cefr(level: str, taxonomy_keys: Set[str]) -> Optional[str]:
    if not level:
        return None
    key = level.strip().upper()
    prefs = CEFR_TAXONOMY_PRIORITY.get(key)
    if not prefs:
        return None
    # Return the first preferred category that exists in the current taxonomy table
    for cat in prefs:
        mapped = finalize_category(cat, taxonomy_keys)
        if mapped and mapped in taxonomy_keys:
            return mapped
    return None

def choose_by_priority(cands: Set[str]) -> Optional[str]:
    if not cands:
        return None
    # Global tie-break: prefer Social over Food when both are present
    if 'Social' in cands and 'Food' in cands:
        return 'Social'
    ranked = sorted(cands, key=lambda c: PRIORITY_ORDER.index(c) if c in PRIORITY_ORDER else 999)
    return ranked[0]


def resolve_category(seed_cat: Optional[str], wn_votes: Counter) -> Tuple[Optional[str], dict]:
    debug = {}
    if seed_cat:
        debug['method'] = 'seed'
        debug['seed_category'] = seed_cat
        return seed_cat, debug
    if wn_votes:
        # pick highest score; break ties by PRIORITY_ORDER
        max_score = max(wn_votes.values())
        tied = [c for c, v in wn_votes.items() if v == max_score]
        if len(tied) == 1:
            choice = tied[0]
        else:
            # Global tie-break: prefer Social over Food when both are present
            if 'Social' in tied and 'Food' in tied:
                choice = 'Social'
            else:
                # prefer the one earliest in priority
                ranked = sorted(tied, key=lambda c: PRIORITY_ORDER.index(c) if c in PRIORITY_ORDER else 999)
                choice = ranked[0]
        debug['method'] = 'wordnet'
        debug['votes'] = dict(wn_votes)
        debug['chosen'] = choice
        return choice, debug
    return None, {'method':'none'}


def main():
    parser = argparse.ArgumentParser(description="Assign taxonomy tags (subject categories) to a CEFR word list using WordNet supersenses mapped via taxonomy_table.json.")
    parser.add_argument('--input', required=True, help='Input CSV path')
    parser.add_argument('--output', help='Output CSV path (default: adds _tagged before extension)')
    parser.add_argument('--english-column', default='English_Term', help='Column name containing the English term (default: English_Term)')
    parser.add_argument('--category-column', default='Taxonomy', help='Column name to write the taxonomy/category into (default: Taxonomy)')
    parser.add_argument('--seeds-file', help='Optional JSON file with taxonomy seeds (overrides embedded)')
    parser.add_argument('--overrides-file', help='JSON file mapping specific terms to categories (exact match).')
    parser.add_argument('--overwrite', choices=['yes','no'], default='no', help='Overwrite existing category values? (default: no)')
    parser.add_argument('--dry-run', action='store_true', help='Do not write output, just report stats')
    parser.add_argument('--log-unknowns', default='unknowns.csv', help='CSV path to write terms with no category (default: unknowns.csv)')
    parser.add_argument('--log-conflicts', default='conflicts.csv', help='CSV path to write items with multiple strong signals (optional)')
    parser.add_argument('--taxonomy-table', default=str(Path(__file__).with_name('taxonomy_table.json')), help='Path to taxonomy_table.json used to map sources to final categories.')
    # --wiktionary-cache, --wikidata-cache, --build-wiktionary-cache, --build-wikidata-cache, --wordnet-only, --rate-limit removed
    parser.add_argument('--wordnet-cache', help='Optional JSON cache mapping term -> precomputed WordNet data (lexnames and/or votes).')
    parser.add_argument('--build-wordnet-cache', metavar='OUT_JSON', help='Build a WordNet cache (term -> {lexnames, votes}) from the input CSV and exit.')
    parser.add_argument('--confidence', type=float, default=0.60, help='Minimum confidence threshold to auto-accept winner (default: 0.60).')
    parser.add_argument('--margin', type=float, default=0.20, help='Minimum margin threshold to auto-accept winner (default: 0.20).')
    parser.add_argument('--use-cefr-priors', action='store_true', help='Add small priors based on CEFR level to stabilize ambiguous terms.')
    parser.add_argument('--cefr-column', default='CEFR_Level', help='Column name with CEFR level when --use-cefr-priors is set (default: CEFR_Level).')
    parser.add_argument('--set-cefr-level', help='If provided, write this CEFR level string into the CEFR column (default column name from --cefr-column).')
    parser.add_argument('--cefr-overwrite', choices=['yes','no'], default='no', help='If using --set-cefr-level, choose whether to overwrite existing non-empty CEFR values (default: no)')
    parser.add_argument('--map-from-cefr', choices=['yes','no'], default='no',
                        help='If yes, set taxonomy purely from CEFR level mapping (A1..C2) rather than WordNet.')

    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    # If cache-building is requested, build and exit early
    if args.build_wordnet_cache:
        terms = []
        with in_path.open('r', encoding='utf-8', newline='') as f:
            sample = f.read(65536); f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            if args.english_column not in reader.fieldnames:
                print(f"English column '{args.english_column}' not found in input.", file=sys.stderr)
                sys.exit(2)
            for row in reader:
                val = (row.get(args.english_column) or '').strip()
                if val:
                    terms.append(val)
        out_json = Path(args.build_wordnet_cache)
        build_wordnet_cache(terms, out_json)
        print(f"WordNet cache written to: {out_json}")
        return

    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + '_tagged' + in_path.suffix)

    # (Seeds and patterns are not used in Wiktionary-only mode)

    # Load overrides if specified
    overrides = {}
    if args.overrides_file:
        try:
            with open(args.overrides_file, 'r', encoding='utf-8') as of:
                overrides = json.load(of)
        except FileNotFoundError:
            print(f"Overrides file not found: {args.overrides_file}", file=sys.stderr)
        except json.JSONDecodeError as e:
            print(f"Overrides file JSON error: {e}", file=sys.stderr)

    # Load taxonomy
    taxonomy_table, wn_super_map, taxonomy_priority = load_taxonomy_table(args.taxonomy_table)
    taxonomy_keys = set(taxonomy_priority)
    global PRIORITY_ORDER
    PRIORITY_ORDER = taxonomy_priority[:]

    # Ensure WordNet data is available and prep lemmatizer
    _ensure_wordnet_downloaded()
    lemmatizer = WordNetLemmatizer()
    wordnet_cache = load_json_cache(args.wordnet_cache)

    # Only WordNet is used; Wiktionary/Wikidata caches are not needed.

    # Merge in taxonomy-level term overrides (from taxonomy_table.json -> term_overrides)
    taxonomy_overrides_raw = taxonomy_table.get('term_overrides', {}) or {}
    taxonomy_overrides: Dict[str, str] = {}
    for k, v in taxonomy_overrides_raw.items():
        key_norm = normalize_text(str(k))
        val = str(v).strip()
        if val in taxonomy_keys:  # only accept valid categories defined in the table
            taxonomy_overrides[key_norm] = val
        else:
            # ignore invalid categories quietly
            pass

    # Normalize keys for CLI overrides (if provided) and merge (CLI overrides take precedence)
    if overrides:
        overrides = {normalize_text(str(k)): str(v).strip() for k, v in overrides.items()}
    else:
        overrides = {}

    # Final override map used by the classifier
    overrides = {**taxonomy_overrides, **overrides}

    # (WordNet setup not used in Wiktionary-only mode)

    # IO setup
    unknowns = []
    conflicts = []
    updated = 0
    skipped_existing = 0
    total = 0

    # Sniff dialect and fieldnames
    with in_path.open('r', encoding='utf-8', newline='') as f:
        sample = f.read(65536)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else []

    if args.category_column not in fieldnames:
        fieldnames.append(args.category_column)
    # Ensure CEFR column is present in output even if missing in input
    if args.cefr_column not in fieldnames:
        fieldnames.append(args.cefr_column)

    def _row_iter():
        with in_path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, fieldnames=fieldnames, dialect=dialect)
            # If we manually provided fieldnames (added category), skip header row reading duplication
            for i, row in enumerate(reader):
                if i == 0 and reader.fieldnames == fieldnames:
                    # already correct
                    pass
                yield row

    rows_out = []

    for row in tqdm(_row_iter(), desc='Tagging'):
        total += 1
        term = (row.get(args.english_column) or '').strip()
        if not term:
            rows_out.append(row)
            continue

        # Optionally set CEFR level into the CEFR column
        if args.set_cefr_level:
            current_cefr = (row.get(args.cefr_column) or '').strip()
            if args.cefr_overwrite == 'yes' or not current_cefr:
                row[args.cefr_column] = args.set_cefr_level

        # Respect taxonomy overwrite flag
        existing = (row.get(args.category_column) or '').strip()
        if existing and args.overwrite == 'no':
            skipped_existing += 1
            rows_out.append(row)
            continue

        # If requested, map taxonomy directly from CEFR level and skip other methods
        if args.map_from_cefr == 'yes':
            cefr_val = (row.get(args.cefr_column) or '').strip()
            cefr_choice = category_from_cefr(cefr_val, taxonomy_keys)
            if cefr_choice:
                row[args.category_column] = cefr_choice
                updated += 1
                rows_out.append(row)
                continue

        # Reset per-row diagnostics to avoid leaking from previous iterations
        conflict_flag = False

        # Exact-match override takes absolute precedence
        if term and overrides:
            ov_cat = overrides.get(normalize_text(term))
        else:
            ov_cat = None

        if ov_cat:
            choice, debug = ov_cat, {'method': 'override'}
        else:
            # WordNet-based decision pipeline
            # 1) Collect WordNet lexnames (supersenses) for the term
            names = wn_lexnames_raw(term, lemmatizer, wordnet_cache)

            # 2) Map lexnames to taxonomy categories via taxonomy_table.json
            mapped = set()
            for name in names:
                for cat in wn_super_map.get(name, []) or []:
                    mapped.add(cat)

            # 3) Choose by taxonomy priority when multiple candidates are present
            choice = choose_by_priority(mapped)
            if choice:
                debug = {'method': 'wordnet_supersense', 'lexnames': sorted(list(names))}

            # 4) If still unresolved, fall back to heuristic WordNet voting
            if not choice:
                wn_votes = wn_lexname_votes(term, lemmatizer, wordnet_cache)

                votes = wn_votes.copy()
                # Optional CEFR priors to stabilize common beginner domains
                if args.use_cefr_priors:
                    cefr_val = (row.get(args.cefr_column) or '').strip()
                    pri = priors_for_cefr(cefr_val)
                    for k, v in pri.items():
                        votes[k] += v

                # Decide winner and compute confidence/margin
                if votes:
                    total_votes = sum(votes.values()) or 1
                    top2 = votes.most_common(2)
                    winner, wv = top2[0]
                    runner_up_v = top2[1][1] if len(top2) > 1 else 0
                    confidence = wv / total_votes
                    margin = (wv - runner_up_v) / total_votes

                    # Use existing tie-break preferences on exact ties
                    if len([c for c, v in votes.items() if v == wv]) > 1:
                        # apply your priority tie-break (and Social>Food rule)
                        tb_choice, _ = resolve_category(None, votes)
                        winner = tb_choice or winner

                    # Accept winner if thresholds met; otherwise log as conflict (but still output winner)
                    if confidence >= args.confidence and margin >= args.margin:
                        choice = winner
                        debug = {'method': 'wordnet_votes', 'confidence': round(confidence, 4), 'margin': round(margin, 4), 'votes': dict(votes)}
                    else:
                        choice = winner
                        debug = {'method': 'wordnet_votes_lowmargin', 'confidence': round(confidence, 4), 'margin': round(margin, 4), 'votes': dict(votes)}
                        conflict_flag = True
                else:
                    choice, debug = resolve_category(None, votes)

            # Mark conflicts if multiple categories were possible and no override was used
            if not ov_cat and len(mapped) > 1:
                conflict_flag = True

        # Fallback: if no choice yet, try CEFR-based mapping
        if not choice:
            cefr_val_fb = (row.get(args.cefr_column) or '').strip()
            choice = category_from_cefr(cefr_val_fb, taxonomy_keys)

        # If conflict flag, log
        if not ov_cat and conflict_flag:
            # Try to include diagnostic stats if present
            conf = None
            marg = None
            if isinstance(debug, dict):
                conf = debug.get('confidence')
                marg = debug.get('margin')
            conflicts.append({
                'term': term,
                'votes': json.dumps({'wordnet_lexnames': sorted(list(names)), 'mapped_categories': sorted(list(mapped)), 'vote_detail': debug.get('votes') if isinstance(debug, dict) else {} }),
                'chosen': choice or '',
                'confidence': conf if conf is not None else '',
                'margin': marg if marg is not None else ''
            })

        # Map legacy-style labels (e.g., 'Medical & Health') to final taxonomy keys
        if choice:
            normalized_choice = finalize_category(choice, taxonomy_keys)
            if normalized_choice:
                choice = normalized_choice

        if choice:
            row[args.category_column] = choice
            updated += 1
        else:
            row[args.category_column] = ''
            raw_suggest = classify_with_rules(term)
            suggest = finalize_category(raw_suggest, taxonomy_keys) or ''
            unknowns.append({'term': term, 'suggested': suggest})

        rows_out.append(row)

    # Write outputs
    if args.dry_run:
        print(f"Processed: {total} | Updated: {updated} | Skipped existing: {skipped_existing} | Unknowns: {len(unknowns)}")
    else:
        with out_path.open('w', encoding='utf-8', newline='') as wf:
            writer = csv.DictWriter(wf, fieldnames=fieldnames, dialect=dialect)
            writer.writeheader()
            for r in rows_out:
                writer.writerow(r)
        print(f"Wrote: {out_path}")
        print(f"Processed: {total} | Updated: {updated} | Skipped existing: {skipped_existing} | Unknowns: {len(unknowns)}")

        if unknowns:
            with open(args.log_unknowns, 'w', encoding='utf-8', newline='') as uf:
                uw = csv.DictWriter(uf, fieldnames=['term','suggested'])
                uw.writeheader()
                uw.writerows(unknowns)
            print(f"Unknowns logged to: {args.log_unknowns}")
        if conflicts:
            with open(args.log_conflicts, 'w', encoding='utf-8', newline='') as cf:
                cw = csv.DictWriter(cf, fieldnames=['term','votes','chosen','confidence','margin'])
                cw.writeheader()
                cw.writerows(conflicts)
            print(f"Conflicts logged to: {args.log_conflicts}")


if __name__ == '__main__':
    main()
