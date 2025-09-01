#!/usr/bin/env python3
import csv, json, re, time, pathlib, requests, sys
from collections import deque
import argparse
from urllib.parse import quote as _urlquote
from time import monotonic as _now

CACHE_DIR = pathlib.Path(".cache_examples"); CACHE_DIR.mkdir(exist_ok=True)

class _RateLimiter:
    def __init__(self, min_interval_sec: float):
        self.min_interval = float(min_interval_sec)
        self._last = 0.0
    def wait(self):
        elapsed = _now() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = _now()

# Polite defaults per Wikimedia guidance: identify your client and keep a low request rate.
DEFAULT_USER_AGENT = "ExampleSentenceFiller/1.0 (non-commercial; contact: N/A)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": DEFAULT_USER_AGENT})
WIKTIONARY_LIMITER = _RateLimiter(1.0)  # at most ~1 req/sec to Wiktionary
TATOEBA_LIMITER = _RateLimiter(1.0)     # keep it gentle for Tatoeba as well

def _cache_path(prefix, key):
    safe = re.sub(r"[^a-z0-9_-]+", "_", key.lower())
    return CACHE_DIR / f"{prefix}_{safe}.json"
def _cache_get(prefix, key):
    p = _cache_path(prefix, key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
def _cache_set(prefix, key, value):
    try:
        _cache_path(prefix, key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

# --------- Wiktionary via live REST API ----------
def wiktionary_examples(word):
    cached = _cache_get("wikt", word)
    if cached:
        return cached, "wiktionary(api)", f"https://en.wiktionary.org/wiki/{word}"
    # Use raw wikitext so we can reliably parse example markers ("#:")
    url = f"https://en.wiktionary.org/api/rest_v1/page/wikitext/{_urlquote(word, safe='')}"
    attempts = 0
    while attempts < 3:
        attempts += 1
        WIKTIONARY_LIMITER.wait()
        try:
            r = SESSION.get(url, timeout=15)
            if r.status_code == 200:
                try:
                    js = r.json()
                    wikitext = js.get("wikitext", "")
                except ValueError:
                    wikitext = r.text  # fallback, just in case
                lines = wikitext.splitlines()
                cand = [re.sub(r"^\#:\s*", "", ln).strip() for ln in lines if ln.lstrip().startswith("#:")]
                # Strip common wiki markup like [[link]] -> link
                cand = [re.sub(r"\[\[(.*?)\]\]", r"\1", c) for c in cand]
                if cand:
                    _cache_set("wikt", word, cand)
                    return cand, "wiktionary(api)", f"https://en.wiktionary.org/wiki/{_urlquote(word, safe='')}"
                # no examples present
                print(f"[wiktionary] 200 OK but no examples for '{word}'", flush=True)
                _cache_set("wikt", word, [])
                return [], None, None
            elif r.status_code in (429, 503):
                retry_after = r.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.0
                if delay:
                    delay = min(delay, 3.0)
                    print(f"[wiktionary] {r.status_code} for '{word}', waiting {delay}s then giving up.", flush=True)
                    time.sleep(delay)
                else:
                    print(f"[wiktionary] {r.status_code} for '{word}', giving up without retry.", flush=True)
                break
            else:
                print(f"[wiktionary] unexpected status {r.status_code} for '{word}'", flush=True)
                break
        except requests.RequestException as e:
            print(f"[wiktionary] network error for '{word}': {e.__class__.__name__}: {e}", flush=True)
            break
    return [], None, None

# --------- Tatoeba (fallback) ----------
# API docs: https://tatoeba.org/eng/api_v0
def tatoeba_examples(word, max_n=3):
    cached = _cache_get("tat", word)
    if cached:
        sents = [s for s in cached if re.search(rf"\b{re.escape(word)}\b", s, re.IGNORECASE)]
        sents = sents[:max_n]
        if sents:
            return sents, "tatoeba", f"https://tatoeba.org/eng/api_v0/search?query={word}&from=eng"
        return [], None, None
    url = "https://tatoeba.org/eng/api_v0/search"
    params = {"query": word, "from": "eng", "to": "", "orphans": "no", "unapproved": "no", "native": "", "sort": "relevance"}
    attempts = 0
    while attempts < 3:
        attempts += 1
        TATOEBA_LIMITER.wait()
        try:
            r = SESSION.get(url, params=params, timeout=15)
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
                    _cache_set("tat", word, sents)
                    return sents, "tatoeba", f"{url}?query={word}&from=eng"
                _cache_set("tat", word, [])
                return [], None, None
            elif r.status_code in (429, 503):
                retry_after = r.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.0
                if delay:
                    delay = min(delay, 3.0)
                    print(f"[tatoeba] {r.status_code} for '{word}', waiting {delay}s then giving up.", flush=True)
                    time.sleep(delay)
                else:
                    print(f"[tatoeba] {r.status_code} for '{word}', giving up without retry.", flush=True)
                break
            else:
                break
        except requests.RequestException as e:
            print(f"[tatoeba] network error for '{word}': {e.__class__.__name__}: {e}", flush=True)
            break
    return [], None, None

# --------- Main pipeline ----------
SOURCE_CHOICE = 2  # default: tatoeba
CSV_PATH = None
OVERWRITE = False

def best_example(word):
    global SOURCE_CHOICE
    # 1: Wiktionary only
    # 2: Tatoeba only
    # 3: Both (Wiktionary first, then Tatoeba)
    if SOURCE_CHOICE == 1:
        exs, src, url = wiktionary_examples(word)
        if exs:
            return exs[0], src, url
    elif SOURCE_CHOICE == 2:
        exs, src, url = tatoeba_examples(word)
        if exs:
            return exs[0], src, url
    elif SOURCE_CHOICE == 3:
        exs, src, url = wiktionary_examples(word)
        if exs:
            return exs[0], src, url
        exs, src, url = tatoeba_examples(word)
        if exs:
            return exs[0], src, url
    return "", None, None

def process_csv(limit=None):
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
            process_this = (limit is None or i <= limit)
            # Progress output per row
            word_for_log = (row.get("English_Term") or "").strip()

            if not process_this:
                # Beyond the --limit: copy row through unchanged
                # (so the output file preserves all remaining rows)
                # Optional: minimal log for clarity
                # print(f"[{i}] Passing through (beyond limit): {word_for_log}", flush=True)
                w.writerow(row)
                continue

            if row.get("English_Sentence") and not OVERWRITE:
                print(f"[{i}] Skipping (already has sentence): {word_for_log}", flush=True)
            else:
                print(f"[{i}] Looking up: {word_for_log}…", flush=True)
                word = (row.get("English_Term") or "").strip()
                if word:
                    sent, src, url = best_example(word)
                    row["English_Sentence"] = sent
                    if sent:
                        print(f"    ↳ filled from {src}: {url}", flush=True)
                    else:
                        print(f"    ↳ no example found", flush=True)
                    time.sleep(0.5)
            w.writerow(row)
    pathlib.Path(CSV_PATH).unlink()
    pathlib.Path(tmp_path).rename(CSV_PATH)
    print("Done.")

def main():
    parser = argparse.ArgumentParser(description="Fill English_Sentence in CSV from example sources.")
    parser.add_argument("csv_path", help="Path to the CSV file to process")
    parser.add_argument("--user-agent", dest="user_agent", help="Custom User-Agent to identify your script to remote services.")
    parser.add_argument("--wiktionary-interval", type=float, default=1.0, help="Minimum seconds between Wiktionary requests (default: 1.0)")
    parser.add_argument("--tatoeba-interval", type=float, default=1.0, help="Minimum seconds between Tatoeba requests (default: 1.0)")
    parser.add_argument("--sources", choices=["wiktionary","tatoeba","both"], default="tatoeba",
                        help="Which source(s) to use for examples. Defaults to 'tatoeba'.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of rows to process (default: all rows).")
    parser.add_argument("--overwrite", choices=["yes","no"], default="no",
                        help="Overwrite existing English_Sentence values if set to yes (default: no).")
    args = parser.parse_args()

    global CSV_PATH
    CSV_PATH = args.csv_path

    if args.user_agent:
        SESSION.headers.update({"User-Agent": args.user_agent})
    WIKTIONARY_LIMITER.min_interval = float(args.wiktionary_interval)
    TATOEBA_LIMITER.min_interval = float(args.tatoeba_interval)

    global SOURCE_CHOICE
    if args.sources == "wiktionary":
        SOURCE_CHOICE = 1
    elif args.sources == "tatoeba":
        SOURCE_CHOICE = 2
    else:
        SOURCE_CHOICE = 3

    global OVERWRITE
    OVERWRITE = (args.overwrite == "yes")

    src_label = {"1":"wiktionary","2":"tatoeba","3":"both"}[str(SOURCE_CHOICE)]
    print(f"Filling English_Sentence from selected sources ({src_label})…")
    process_csv(limit=args.limit)

if __name__ == "__main__":
    main()
