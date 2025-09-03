#!/usr/bin/env python3
"""
Taxonomy tagger for CEFR word lists using a hybrid approach:
1) Seed-list phrase/keyword matching (high precision)
2) WordNet-based supersense heuristics (broad coverage)

Usage examples:
  python3 taxonomy_tagger.py --input "Frank's Master CEFR Word List.csv" --output tagged.csv
  python3 taxonomy_tagger.py --input words.csv --output words_tagged.csv --english-column English --overwrite yes
  python3 taxonomy_tagger.py --input words.csv --dry-run
  python3 taxonomy_tagger.py --input words.csv --overrides-file overrides.json
  python3 taxonomy_tagger.py --input words.csv --taxonomy-table taxonomy_table.json --wiktionary-cache wiktionary_cache.json --wikidata-cache wikidata_cache.json

Notes:
- If NLTK WordNet data is missing, this script will download it on first run.
- You can optionally provide an external JSON seeds file via --seeds-file; otherwise an embedded default is used.
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
import requests

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
    'Numbers & Quantities': 'Numbers',
    'Calendar & Time': 'Numbers',
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


#
# ------------------------
# Embedded default seed taxonomy (can be overridden by --seeds-file)
# ------------------------
DEFAULT_SEEDS = {
  "Greetings": [
    "hello","hi","hey","good morning","good afternoon","good evening","good night",
    "how are you","nice to meet you","pleased to meet you","please","thank you",
    "thanks","you’re welcome","you're welcome","excuse me","sorry","goodbye","bye","see you"
  ],
  "Numbers & Quantities": [
    "zero","one","two","three","ten","hundred","thousand","million","first","second",
    "third","half","quarter","dozen","pair","couple","several","many","few",
    "more","less","most","least","enough","some","all","none","percent","per"
  ],
  "Calendar & Time": [
    "today","tomorrow","yesterday","day","week","month","year","decade","century",
    "monday","tuesday","wednesday","thursday","friday","saturday","sunday",
    "january","february","march","april","may","june","july","august","september",
    "october","november","december","spring","summer","autumn","fall","winter",
    "morning","noon","afternoon","evening","night","hour","minute","second","o’clock","oclock"
  ],
  "Food": [
    "apple","banana","orange","grape","strawberry","tomato","potato","onion","garlic","carrot",
    "bread","rice","pasta","noodles","flour","sugar","salt","oil","butter","cheese",
    "milk","yogurt","egg","fish","chicken","beef","pork","tofu","beans","lentils",
    "water","tea","coffee","juice","wine","beer","breakfast","lunch","dinner","snack",
    "salad","soup","pizza","sandwich","dessert","menu","bill","tip"
  ],
  "Social": [
    "mother","father","parent","sister","brother","child","baby","son","daughter","family",
    "friend","neighbor","coworker","colleague","boss","wife","husband","partner","couple","relative",
    "woman","man","person","people","guest","host"
  ],
  "Shopping": [
    "shop","store","market","mall","cart","basket","cashier","receipt","refund","discount",
    "sale","price","cost","cheap","expensive","bargain","exchange","return","brand","size",
    "small","medium","large","fit","try on","credit","debit","cash","euro","dollar"
  ],
  "Travel": [
    "travel","trip","journey","tour","ticket","reservation","passport","visa","luggage","baggage",
    "bag","suitcase","map","guide","direction","station","platform","stop","schedule","delay",
    "bus","train","tram","metro","subway","taxi","car","rental","bicycle","plane","airport",
    "hotel","hostel","check in","check out","boarding","gate"
  ],
  "Work & School": [
    "job","work","career","profession","employee","employer","office","meeting","project","deadline",
    "salary","resume","interview","promotion","computer","email","report","task","manager","team",
    "school","class","lesson","homework","exam","test","teacher","student","university","lecture"
  ],
  "Medical & Health": [
    "doctor","nurse","dentist","pharmacist","hospital","clinic","pharmacy","appointment","prescription","medicine",
    "pill","tablet","vaccine","symptom","diagnosis","treatment","emergency","pain","fever","headache",
    "cough","cold","flu","allergy","injury","wound","blood","heart","stomach","back"
  ],
  "Opinions & Communication": [
    "yes","no","maybe","think","believe","guess","agree","disagree","prefer","recommend",
    "suggest","argue","claim","explain","describe","discuss","ask","answer","say","tell",
    "true","false","good","bad","better","worse","best","worst","compare","contrast"
  ],
  "Feelings & Emotions": [
    "happy","sad","angry","afraid","scared","worried","nervous","anxious","excited","relieved",
    "surprised","bored","tired","sleepy","hungry","thirsty","lonely","jealous","proud","ashamed",
    "hopeful","grateful","frustrated","calm"
  ],
  "Events & Activities": [
    "event","activity","holiday","festival","party","birthday","wedding","anniversary","picnic","celebration",
    "meeting","conference","ceremony","concert","exhibition","game","match","sport","hobby","exercise",
    "run","walk","swim","dance","sing","read","write","draw","cook","camp"
  ],
  "Idioms & Abstract": [
    "by the way","in the long run","sooner or later","in the meantime","on the other hand",
    "as a matter of fact","rule of thumb","break the ice","piece of cake","hit the road",
    "under the weather","once in a while","back to square one","call it a day","out of the blue",
    "hit the nail on the head","the ball is in your court","bark up the wrong tree","let the cat out of the bag","spill the beans",
    "beat around the bush","cost an arm and a leg","pull someone’s leg","pull someone's leg","take it with a grain of salt","think outside the box",
    "the tip of the iceberg","burn the midnight oil","cut to the chase","on the same page","keep an eye on"
  ]
}

# ------------------------
# Taxonomy table loader & reverse maps
# ------------------------
def load_taxonomy_table(path: str) -> Tuple[dict, dict, dict, dict, list]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"taxonomy table not found: {path}")
    with p.open('r', encoding='utf-8') as f:
        table = json.load(f)
    priority = list(table.keys())
    wn_super_map, wiktionary_map, wikidata_map = {}, {}, {}
    for cat, spec in table.items():
        maps = (spec or {}).get('mappings', {})
        for ss in maps.get('wordnet_supersenses', []) or []:
            wn_super_map.setdefault(ss.lower(), set()).add(cat)
        for wt in maps.get('wiktionary_categories', []) or []:
            wiktionary_map.setdefault(wt.lower(), set()).add(cat)
        for qid in maps.get('wikidata_classes', []) or []:
            wikidata_map.setdefault(str(qid).lower(), set()).add(cat)
    return table, wn_super_map, wiktionary_map, wikidata_map, priority

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

def lookup_wiktionary(term: str, cache: dict) -> List[str]:
    if not cache:
        return []
    key = normalize_text(term)
    cats = cache.get(key) or cache.get(term) or []
    return [str(c).lower() for c in (cats if isinstance(cats, list) else [cats])]

# ------------------------
# Online fetchers (free sources) and cache builders
# ------------------------
WIKTIONARY_API = "https://en.wiktionary.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def fetch_wiktionary_categories(term: str, session: requests.Session, delay: float = 0.1) -> List[str]:
    """Fetch category titles from en.wiktionary for a term. Returns normalized category names (lowercased)."""
    params = {
        'action': 'query',
        'format': 'json',
        'prop': 'categories',
        'cllimit': 'max',
        'redirects': 1,
        'titles': term,
    }
    try:
        r = session.get(WIKTIONARY_API, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        pages = data.get('query', {}).get('pages', {})
        cats = []
        for page in pages.values():
            for c in page.get('categories', []) or []:
                title = c.get('title', '')
                # Keep only topical-style categories like 'Category:en:Food and drink' or 'Category:en:Fruits'
                if title.lower().startswith('category:en:'):
                    cats.append(title)
        time.sleep(delay)
        return [c.lower() for c in cats]
    except Exception:
        return []


def fetch_wikidata_qids(term: str, session: requests.Session, delay: float = 0.1) -> List[str]:
    """Fetch Wikidata QIDs for direct 'instance of' (P31) of the best-matching entity label in English."""
    try:
        # 1) search entity
        params = {
            'action': 'wbsearchentities',
            'search': term,
            'language': 'en',
            'uselang': 'en',
            'format': 'json',
            'limit': 1,
        }
        r = session.get(WIKIDATA_API, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get('search'):
            return []
        qid = data['search'][0]['id']

        # 2) get claims
        params2 = {
            'action': 'wbgetentities',
            'ids': qid,
            'languages': 'en',
            'props': 'claims',
            'format': 'json'
        }
        r2 = session.get(WIKIDATA_API, params=params2, timeout=10)
        r2.raise_for_status()
        data2 = r2.json()
        claims = data2.get('entities', {}).get(qid, {}).get('claims', {})
        p31 = claims.get('P31', [])
        qids = []
        for snak in p31:
            try:
                val = snak['mainsnak']['datavalue']['value']
                qids.append(val['id'])
            except Exception:
                continue
        time.sleep(delay)
        return [q.lower() for q in qids]
    except Exception:
        return []


def build_wiktionary_cache(terms: List[str], out_path: Path, delay: float = 0.1):
    sess = requests.Session()
    cache = {}
    for t in tqdm(sorted(set(terms))):
        cats = fetch_wiktionary_categories(t, sess, delay=delay)
        if cats:
            cache[normalize_text(t)] = cats
    out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')


def build_wikidata_cache(terms: List[str], out_path: Path, delay: float = 0.1):
    sess = requests.Session()
    cache = {}
    for t in tqdm(sorted(set(terms))):
        qids = fetch_wikidata_qids(t, sess, delay=delay)
        if qids:
            cache[normalize_text(t)] = qids
    out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')


def lookup_wikidata(term: str, cache: dict) -> List[str]:
    if not cache:
        return []
    key = normalize_text(term)
    qids = cache.get(key) or cache.get(term) or []
    return [str(q).lower() for q in (qids if isinstance(qids, list) else [qids])]

# ------------------------
# WordNet lexname -> category heuristic map
# ------------------------
LEXNAME_TO_CATEGORY = {
    # Nouns
    'noun.food': 'Food',
    'noun.person': None,          # handled with kinship overrides first, else often Social/Work
    'noun.body': 'Medical & Health',
    'noun.time': 'Calendar & Time',
    'noun.quantity': 'Numbers & Quantities',
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
ROOM_OBJECT_HINTS = {"bathroom","bedroom","kitchen","desk","classroom","downstairs","upstairs","apartment"}
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

    # Common rooms/household/learning locations -> Work & School (classroom/desk) or Events & Activities
    if t in ROOM_OBJECT_HINTS:
        return "Work & School" if t in {"classroom","desk"} else "Events & Activities"

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


def wn_lexname_votes(term: str, lemmatizer: WordNetLemmatizer) -> Counter:
    votes = Counter()
    txt = normalize_text(term)
    # Try multiple POS; lemmatize per POS
    pos_tags = [('n','n'), ('v','v'), ('a','a'), ('r','r')]
    for _, pos in pos_tags:
        lemma = lemmatizer.lemmatize(txt, pos=pos)
        synsets = wn.synsets(lemma, pos=pos)
        for s in synsets:
            lex = s.lexname()
            mapped = LEXNAME_TO_CATEGORY.get(lex)
            if mapped:
                votes[mapped] += 3  # strong signal
            # Disambiguation for artifact/person
            gloss = (s.definition() or '').lower()
            examples = ' '.join(s.examples()).lower() if s.examples() else ''
            hypernyms = ' '.join([h.name().split('.')[0] for h in s.hypernyms()]).lower()
            text_blob = ' '.join([gloss, examples, hypernyms])

            # Shopping hints for noun.artifact or commerce language
            if lex in ('noun.artifact', 'noun.possession', 'noun.communication'):
                if any(k in text_blob for k in SHOPPING_HINTS):
                    votes['Shopping'] += 2

            # Person terms towards Social
            if lex in ('noun.person','verb.social'):
                if any(k in (lemma, txt, text_blob) for k in KINSHIP_HINTS):
                    votes['Social'] += 3

            # Travel hints: vehicles/transport/places
            if any(k in text_blob for k in ['vehicle','car','bus','train','plane','airport','station','ticket','passport','luggage','journey','travel','trip','hotel']):
                votes['Travel'] += 2

            # Food hints: dish/ingredient/eat/drink
            if any(k in text_blob for k in ['food','drink','beverage','ingredient','dish','meal','eat','drink','restaurant','menu']):
                votes['Food'] += 2

            # Medical hints
            if any(k in text_blob for k in ['disease','illness','medicine','medical','doctor','pain','fever','injury','symptom','hospital','clinic','pharmacy','vaccine']):
                votes['Medical & Health'] += 2

            # Feelings
            if any(k in text_blob for k in ['emotion','feeling','mood','happy','sad','angry','fear','anxiety','joy']):
                votes['Feelings & Emotions'] += 1

            # Calendar & Time keywords
            if any(k in text_blob for k in ['time','day','month','year','season','hour','minute','second','calendar']):
                votes['Calendar & Time'] += 1

            # Numbers & Quantities
            if any(k in text_blob for k in ['number','quantity','amount','measure','percent','ratio']):
                votes['Numbers & Quantities'] += 1

            # Events & Activities
            if any(k in text_blob for k in ['event','activity','festival','party','sport','game','competition','ceremony']):
                votes['Events & Activities'] += 1

            # Opinions & Communication
            if any(k in text_blob for k in ['say','tell','speak','discussion','argue','explain','opinion','belief','think']):
                votes['Opinions & Communication'] += 1
    return votes



# ------------------------
# WordNet lexname collector and priority chooser
# ------------------------
def wn_lexnames_raw(term: str, lemmatizer: WordNetLemmatizer) -> Set[str]:
    names = set()
    txt = normalize_text(term)
    for _, pos in [('n','n'),('v','v'),('a','a'),('r','r')]:
        lemma = lemmatizer.lemmatize(txt, pos=pos)
        for s in wn.synsets(lemma, pos=pos):
            names.add(s.lexname().lower())
    return names

PRIORITY_ORDER = []  # will be filled from taxonomy_table order

def choose_by_priority(cands: Set[str]) -> Optional[str]:
    if not cands:
        return None
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
            # prefer the one earliest in priority
            ranked = sorted(tied, key=lambda c: PRIORITY_ORDER.index(c) if c in PRIORITY_ORDER else 999)
            choice = ranked[0]
        debug['method'] = 'wordnet'
        debug['votes'] = dict(wn_votes)
        debug['chosen'] = choice
        return choice, debug
    return None, {'method':'none'}


def main():
    parser = argparse.ArgumentParser(description="Assign taxonomy tags (subject categories) to a CEFR word list using seeds + WordNet.")
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
    parser.add_argument('--wiktionary-cache', help='Optional JSON cache mapping term -> [wiktionary category labels].')
    parser.add_argument('--wikidata-cache', help='Optional JSON cache mapping term -> [Wikidata QIDs].')
    parser.add_argument('--build-wiktionary-cache', metavar='OUT_JSON', help='Build Wiktionary category cache for terms and write to OUT_JSON.')
    parser.add_argument('--build-wikidata-cache', metavar='OUT_JSON', help='Build Wikidata P31 (instance-of) cache for terms and write to OUT_JSON.')
    parser.add_argument('--rate-limit', type=float, default=0.1, help='Delay seconds between API calls when building caches (default: 0.1).')

    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    # If cache-building is requested, build and exit early
    if args.build_wiktionary_cache or args.build_wikidata_cache:
        # Gather terms from the input CSV using the selected english column
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
        if args.build_wiktionary_cache:
            out_json = Path(args.build_wiktionary_cache)
            build_wiktionary_cache(terms, out_json, delay=args.rate_limit)
            print(f"Wiktionary cache written to: {out_json}")
        if args.build_wikidata_cache:
            out_json = Path(args.build_wikidata_cache)
            build_wikidata_cache(terms, out_json, delay=args.rate_limit)
            print(f"Wikidata cache written to: {out_json}")
        return

    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + '_tagged' + in_path.suffix)

    # Load seeds & compile patterns
    seeds = load_seeds(args.seeds_file)
    seed_patterns = compile_seed_patterns(seeds)

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

    # Load taxonomy and external caches
    taxonomy_table, wn_super_map, wiktionary_map, wikidata_map, taxonomy_priority = load_taxonomy_table(args.taxonomy_table)
    taxonomy_keys = set(taxonomy_priority)
    global PRIORITY_ORDER
    PRIORITY_ORDER = taxonomy_priority[:]

    wiktionary_cache = load_json_cache(args.wiktionary_cache)
    wikidata_cache = load_json_cache(args.wikidata_cache)

    # WordNet setup
    _ensure_wordnet_downloaded()
    lemmatizer = WordNetLemmatizer()

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

        existing = (row.get(args.category_column) or '').strip()
        if existing and args.overwrite == 'no':
            skipped_existing += 1
            rows_out.append(row)
            continue

        # Exact-match override takes absolute precedence
        if term and overrides:
            ov_cat = overrides.get(normalize_text(term)) or overrides.get(term)
        else:
            ov_cat = None

        if ov_cat:
            choice, debug = ov_cat, {'method': 'override'}
        else:
            # 1) Seeds / handcrafted rules (legacy) -> finalize into new taxonomy
            seed_cat = classify_with_seeds(term, seed_patterns)
            rule_cat = classify_with_rules(term)
            pref = seed_cat or rule_cat
            choice = finalize_category(pref, taxonomy_keys)
            debug = {'method': 'seed/rule', 'raw': pref} if choice else {}

            # 2) Wiktionary cache -> taxonomy via table mappings
            if not choice and wiktionary_cache:
                wt_cats = lookup_wiktionary(term, wiktionary_cache)
                mapped = set()
                for wt in wt_cats:
                    for cat in wiktionary_map.get(wt, []):
                        mapped.add(cat)
                choice = choose_by_priority(mapped)
                if choice:
                    debug = {'method': 'wiktionary', 'source': wt_cats}

            # 3) Wikidata cache (QIDs) -> taxonomy via table mappings
            if not choice and wikidata_cache:
                qids = lookup_wikidata(term, wikidata_cache)
                mapped = set()
                for q in qids:
                    for cat in wikidata_map.get(q, []):
                        mapped.add(cat)
                choice = choose_by_priority(mapped)
                if choice:
                    debug = {'method': 'wikidata', 'source': qids}

            # 4) WordNet supersenses (raw lexnames) -> taxonomy via table mappings
            if not choice:
                names = wn_lexnames_raw(term, lemmatizer)
                mapped = set()
                for nm in names:
                    for cat in wn_super_map.get(nm, []):
                        mapped.add(cat)
                choice = choose_by_priority(mapped)
                if choice:
                    debug = {'method': 'wordnet_supersense', 'lexnames': list(names)}

            # 5) Fallback: legacy WordNet vote heuristic -> finalize to new taxonomy
            if not choice:
                wn_votes = wn_lexname_votes(term, lemmatizer)
                conflict_flag = False
                if wn_votes:
                    top_two = wn_votes.most_common(2)
                    if len(top_two) == 2 and top_two[1][1] >= max(1, top_two[0][1] - 1):
                        conflict_flag = True
                raw_choice, _dbg = resolve_category(None, wn_votes)
                choice = finalize_category(raw_choice, taxonomy_keys)
                if choice:
                    debug = {'method': 'wordnet_legacy', 'votes': dict(wn_votes)}

        # If conflict flag, log
        if not ov_cat:
            if 'conflict_flag' in locals() and conflict_flag:
                conflicts.append({
                    'term': term,
                    'votes': json.dumps(dict(wn_votes)),
                    'chosen': choice or ''
                })
        else:
            conflict_flag = False

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
                cw = csv.DictWriter(cf, fieldnames=['term','votes','chosen'])
                cw.writeheader()
                cw.writerows(conflicts)
            print(f"Conflicts logged to: {args.log_conflicts}")


if __name__ == '__main__':
    main()
