#!/usr/bin/env python3
import csv, json, re, time, pathlib, requests, sys
from collections import deque

CSV_PATH = "Frank's Master CEFR Word List.csv"
CACHE_DIR = pathlib.Path(".cache_examples"); CACHE_DIR.mkdir(exist_ok=True)

# --------- Wiktionary via wiktextract (local JSON or HTTP) ----------
# Option A (recommended): download the prebuilt English dump once:
#   pip install wiktextract
#   python -c "import wiktextract; wiktextract.wxt_main(['en'])"
# This creates JSON lines under ./wikt/ with senses+examples.
# If you don't want the full dump, Option B scrapes the page (slower).

def load_wiktextract_index():
    """Return a dict word -> list of example sentences (lowercase keys)."""
    idx = {}
    wdir = pathlib.Path("wikt/en")
    if not wdir.exists():
        return idx
    for jf in wdir.glob("*.json"):
        with open(jf, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                w = obj.get("word")
                if not w: continue
                exs = []
                for s in obj.get("senses", []):
                    for ex in s.get("examples", []):
                        if isinstance(ex, dict):
                            txt = ex.get("text")
                        else:
                            txt = ex
                        if txt:
                            exs.append(txt.strip())
                if exs:
                    idx.setdefault(w.lower(), []).extend(exs)
    return idx

WIKT_INDEX = load_wiktextract_index()

def wiktionary_examples(word):
    # Option A: local index (fast, offline)
    exs = WIKT_INDEX.get(word.lower(), [])
    if exs:
        return exs, "wiktionary(wiktextract)", f"https://en.wiktionary.org/wiki/{word}"
    # Option B: minimal live scrape (only if you didn't build the dump)
    url = f"https://en.wiktionary.org/api/rest_v1/page/plain/{word}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            lines = r.text.splitlines()
            # crude pull of lines that look like examples (starts with “#:” in wikitext)
            cand = [re.sub(r"^\#:\s*", "", ln).strip() for ln in lines if ln.startswith("#:")]
            cand = [re.sub(r"\[\[(.*?)\]\]", r"\1", c) for c in cand]
            if cand:
                return cand, "wiktionary(scrape)", f"https://en.wiktionary.org/wiki/{word}"
    except Exception:
        pass
    return [], None, None

# --------- Tatoeba (fallback) ----------
# API docs: https://tatoeba.org/eng/api_v0
def tatoeba_examples(word, max_n=3):
    url = "https://tatoeba.org/eng/api_v0/search"
    params = {"query": word, "from": "eng", "to": "", "orphans": "no", "unapproved": "no", "native": "", "sort": "relevance"}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            js = r.json()
            sents = []
            for it in js.get("results", []):
                txt = it.get("text")
                if txt and re.search(rf"\b{re.escape(word)}\b", txt, re.IGNORECASE):
                    sents.append(txt.strip())
                if len(sents) >= max_n:
                    break
            if sents:
                return sents, "tatoeba", f"{url}?query={word}&from=eng"
    except Exception:
        pass
    return [], None, None

# --------- POS-aware template fallback (last resort) ----------
try:
    import nltk
    from nltk.corpus import wordnet as wn
except Exception:
    nltk = None
    wn = None

def simple_template(word):
    # Minimal POS guess using suffix heuristics + WordNet if available
    pos = None
    if wn:
        syns = wn.synsets(word)
        if syns:
            posmap = {"n":"noun","v":"verb","a":"adj","s":"adj","r":"adv"}
            pos = posmap.get(syns[0].pos())
    if not pos:
        if re.match(r".*ly$", word): pos = "adv"
        elif re.match(r".*ing$|.*ed$", word): pos = "verb"
        else: pos = "noun"
    templates = {
        "noun": [
            f"I saw a {word} on the table.",
            f"This {word} is exactly what we needed.",
            f"Please put the {word} back when you’re done."
        ],
        "verb": [
            f"Let’s {word} before it gets too late.",
            f"They {word} every morning after breakfast.",
            f"Please {word} carefully to avoid mistakes."
        ],
        "adj": [
            f"The view is incredibly {word} from up here.",
            f"That’s a very {word} idea.",
            f"She felt {word} after the news."
        ],
        "adv": [
            f"He spoke {word} to make sure we understood.",
            f"The project progressed {word}.",
            f"Please proceed {word}."
        ],
    }
    return templates.get(pos, templates["noun"])[0], "template", None

# --------- Main pipeline ----------
def best_example(word):
    # 1) Wiktionary
    exs, src, url = wiktionary_examples(word)
    if exs:
        return exs[0], src, url
    # 2) Tatoeba
    exs, src, url = tatoeba_examples(word)
    if exs:
        return exs[0], src, url
    # 3) Template
    return simple_template(word)

def process_csv():
    tmp_path = CSV_PATH + ".tmp"
    with open(CSV_PATH, newline="", encoding="utf-8") as fin, \
         open(tmp_path, "w", newline="", encoding="utf-8") as fout:
        r = csv.DictReader(fin)
        fieldnames = r.fieldnames
        needed = {"English_Sentence"}
        for col in needed:
            if col not in fieldnames:
                fieldnames.insert(fieldnames.index("Italian_Sentence_Translation")+1, col)  # place after the English_Sentence area
        w = csv.DictWriter(fout, fieldnames=fieldnames)
        w.writeheader()

        for i, row in enumerate(r, 1):
            if not row.get("English_Sentence"):
                word = (row.get("English_Term") or "").strip()
                if word:
                    sent, src, url = best_example(word)
                    row["English_Sentence"] = sent
                    # be polite to APIs
                    time.sleep(0.5)
            w.writerow(row)
    pathlib.Path(CSV_PATH).unlink()
    pathlib.Path(tmp_path).rename(CSV_PATH)
    print("Done.")

if __name__ == "__main__":
    print("Filling English_Sentence from Wiktionary/Tatoeba with template fallback…")
    process_csv()