#!/usr/bin/env python3
"""
Taxonomy tagger for CEFR word lists using a hybrid approach:
1) Seed-list phrase/keyword matching (high precision)
2) WordNet-based supersense heuristics (broad coverage)

Usage examples:
  python3 taxonomy_tagger.py --input "Frank's Master CEFR Word List.csv" --output tagged.csv
  python3 taxonomy_tagger.py --input words.csv --output words_tagged.csv --english-column English --overwrite yes
  python3 taxonomy_tagger.py --input words.csv --dry-run

Notes:
- If NLTK WordNet data is missing, this script will download it on first run.
- You can optionally provide an external JSON seeds file via --seeds-file; otherwise an embedded default is used.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

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


def load_seeds(seeds_file: str | None):
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


def classify_with_seeds(term: str, seed_patterns) -> str | None:
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


PRIORITY_ORDER = [
    # When ties occur, prefer seeds/commonsense order
    'Greetings','Numbers & Quantities','Calendar & Time','Food','Social','Shopping','Travel',
    'Work & School','Medical & Health','Opinions & Communication','Feelings & Emotions','Events & Activities','Idioms & Abstract'
]


def resolve_category(seed_cat: str | None, wn_votes: Counter) -> tuple[str | None, dict]:
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
    parser = argparse.ArgumentParser(description="Assign subject categories to a CEFR word list using seeds + WordNet.")
    parser.add_argument('--input', required=True, help='Input CSV path')
    parser.add_argument('--output', help='Output CSV path (default: adds _tagged before extension)')
    parser.add_argument('--english-column', default='English_Term', help='Column name containing the English term (default: English_Term)')
    parser.add_argument('--category-column', default='Category', help='Column name to write the category into (default: Category)')
    parser.add_argument('--seeds-file', help='Optional JSON file with taxonomy seeds (overrides embedded)')
    parser.add_argument('--overwrite', choices=['yes','no'], default='no', help='Overwrite existing category values? (default: no)')
    parser.add_argument('--dry-run', action='store_true', help='Do not write output, just report stats')
    parser.add_argument('--log-unknowns', default='unknowns.csv', help='CSV path to write terms with no category (default: unknowns.csv)')
    parser.add_argument('--log-conflicts', default='conflicts.csv', help='CSV path to write items with multiple strong signals (optional)')

    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + '_tagged' + in_path.suffix)

    # Load seeds & compile patterns
    seeds = load_seeds(args.seeds_file)
    seed_patterns = compile_seed_patterns(seeds)

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

        seed_cat = classify_with_seeds(term, seed_patterns)
        wn_votes = wn_lexname_votes(term, lemmatizer)

        # Conflict heuristic: if there are 2+ vote leaders with close scores
        conflict_flag = False
        if wn_votes:
            top_two = wn_votes.most_common(2)
            if len(top_two) == 2 and top_two[1][1] >= max(1, top_two[0][1] - 1):
                conflict_flag = True

        choice, debug = resolve_category(seed_cat, wn_votes)

        if conflict_flag:
            conflicts.append({
                'term': term,
                'votes': json.dumps(dict(wn_votes)),
                'chosen': choice or ''
            })

        if choice:
            row[args.category_column] = choice
            updated += 1
        else:
            row[args.category_column] = ''
            unknowns.append({'term': term})

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
                uw = csv.DictWriter(uf, fieldnames=['term'])
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
