#!/usr/bin/env python3
"""
get_sentences_from_local_sources.py

Scan local text sources (TXT and EPUB) to find a natural English sentence
containing each English_Term (case-insensitive) from a CSV and write it into
the English_Sentence column. Basic quality filters ensure sentences are
complete and not too short/long.

Usage example:
  python3 get_sentences_from_local_sources.py \
      --sources-dir "/path/to/English Sources" \
      --input-csv   "Frank's Core CEFR Word List - Sentences.csv" \
      --output-csv  "Frank's Core CEFR Word List - Sentences.filled.csv" \
      --min-words 6 \
      --max-words 22
"""

import argparse
import csv
import html
import os
import re
import sys
import zipfile
import time
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Dict

# ---------------------------
# Sentence utilities
# ---------------------------

_SENT_SPLIT_RE = re.compile(
    r"(?<=[.!?])[\"'”’\)\]]*\s+"
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
QUOTE_RE = re.compile(r"[\"“”‘’]")

# Common abbreviations that often end with a period and should NOT end a sentence
_ABBREV_TOKENS = {
    # titles
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "mt.", "rev.", "fr.",
    # common refs
    "no.", "vol.", "fig.", "ch.", "pp.", "pg.", "dept.", "dept", "inc.", "co.", "corp.",
    # months (short)
    "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.", "oct.", "nov.", "dec.",
    # misc short
    "vs."
}
_SHORT_ABBR_RE = re.compile(r"\b[A-Za-z]{1,3}\.$")

FUNC_WORDS = set("""
the a an to and of in that for with on at as by from is are was were be been being
have has had do does did can could will would shall should may might must
i you he she we they it this that these those
""".split())

VERB_LIKE = set("""
is are was were be been being have has had do does did can could will would shall should may might must
go goes went gone make makes made say says said see sees saw seen know knows knew known think thinks thought
come comes came come take takes took taken give gives gave given tell tells told ask asks asked want wants wanted
""".split())

def _alpha_ratio(s: str) -> float:
    letters = sum(ch.isalpha() for ch in s)
    total = max(1, len(s))
    return letters / total

def _tokenize_simple(s: str) -> list:
    return re.findall(r"[A-Za-z']+|[0-9]+|[^\sA-Za-z0-9]", s)

def _looks_wordy(s: str) -> bool:
    # Reject if too many non-letters
    if _alpha_ratio(s) < 0.7:
        return False
    # Reject if contains underscores or bullet/section glyphs
    if any(ch in s for ch in ["_", "•", "§", "¶"]):
        return False
    # Reject if many digits
    if sum(ch.isdigit() for ch in s) >= 3:
        return False
    # Reject if too many ALL-CAPS tokens (headings)
    tokens = [t for t in re.findall(r"[A-Za-z]+", s)]
    caps_tokens = sum(1 for t in tokens if len(t) > 2 and t.isupper())
    if caps_tokens >= 2:
        return False
    # Reject if abbreviation density is high (e.g., "Aug.", "Vol.", "No.")
    abbr = re.findall(r"\b[A-Za-z]{1,3}\.\b", s)
    if len(abbr) >= 2:
        return False
    return True

def _has_function_words_and_verb(s: str) -> bool:
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", s)]
    func_count = sum(1 for w in words if w in FUNC_WORDS)
    verb_hit = any(w in VERB_LIKE for w in words)
    return func_count >= 2 and verb_hit


def normalize_ws(text: str) -> str:
    return WS_RE.sub(" ", text).strip()


# Helper to remove leading/trailing quotes (straight or curly)
def strip_outer_quotes(s: str) -> str:
    # Remove leading and trailing straight or curly quotes if present
    return s.strip(" '\"“”‘’")



def strip_html(text: str) -> str:
    # unescape first to turn &lt; into <, then remove tags
    text = html.unescape(text)
    text = TAG_RE.sub(" ", text)
    return normalize_ws(text)

# Helper: get only the first sentence (with optional trailing quote/paren)
def first_sentence(text: str) -> str:
    """Return only the first sentence from text, allowing a closing quote/paren after the terminator."""
    text = normalize_ws(text)
    m = re.search(r"([\s\S]*?[.!?])[\"'”’\)\]]*(?:\s|$)", text)
    if m:
        return m.group(1).strip()
    return text


def split_sentences(text: str) -> List[str]:
    # Normalize linebreaks, collapse whitespace
    text = normalize_ws(text.replace("\n", " "))
    if not text:
        return []
    # First pass split on ., !, ? followed by whitespace
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    if not parts:
        return []
    merged: List[str] = []
    buffer = parts[0]
    def _looks_like_abbrev_end(s: str) -> bool:
        # Check last token of s; if it's an abbreviation, we should merge with the next chunk
        tokens = re.findall(r"[A-Za-z]+\.?|[0-9]+|[^\sA-Za-z0-9]", s)
        if not tokens:
            return False
        last = tokens[-1].lower()
        # Normalize things like "Aug." / "Mr."
        if last in _ABBREV_TOKENS:
            return True
        # Generic very short abbr like "A." / "Co." which are often not sentence ends
        if _SHORT_ABBR_RE.search(last):
            return True
        return False
    for piece in parts[1:]:
        if _looks_like_abbrev_end(buffer):
            buffer = f"{buffer} {piece}"
        else:
            merged.append(buffer)
            buffer = piece
    merged.append(buffer)
    return merged


def seems_complete_sentence(s: str, min_words: int, max_words: int) -> bool:
    # length bounds
    words = s.split()
    if not (min_words <= len(words) <= max_words):
        return False
    # must end with ., !, or ?
    if not s.endswith((".", "!", "?")):
        return False
    # must start with a capital letter (simple heuristic)
    first_char = next((c for c in s if c.isalpha()), "")
    if not first_char or not first_char.isupper():
        return False
    # Avoid boilerplate or licensing noise
    lowered = s.lower()
    noisy = ("project gutenberg" in lowered or
             "all rights reserved" in lowered or
             "copyright" in lowered or
             "ebook" in lowered or
             "http://" in lowered or "https://" in lowered)
    if noisy:
        return False
    # Avoid sentences with excessive quotes or headings
    if s.isupper() or s.count("...") > 1:
        return False
    # Avoid very shouty / odd punctuation density
    if s.count("!") > 2 or s.count("?") > 2:
        return False

    # New: prefer real, prose-like sentences and avoid metadata/headers
    if not _looks_wordy(s):
        return False
    if not _has_function_words_and_verb(s):
        return False

    return True


def build_match_regex(term: str) -> re.Pattern:
    """
    Create a regex that prefers exact word matches, but also tolerates
    simple morphological tails like 's, s, es.
    """
    # Handle simple punctuation inside the term (e.g., "it's")
    escaped = re.escape(term)
    # Prefer exact, but allow "'s", "s", or "es" (common simple forms)
    pattern = rf"\b{escaped}\b|\b{escaped}(?:'s|s|es)\b"
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------
# Tatoeba API Fallback
# ---------------------------

def fetch_tatoeba_sentence(term: str, *, lang_from: str = "eng", min_words: int = 6, max_words: int = 28,
                            user_agent: str, timeout: float = 10.0) -> Optional[str]:
    """Query Tatoeba for an English sentence containing `term`. A caller-provided User-Agent is required. Returns a single sentence or None.
    Uses the v0 search endpoint. We filter client-side to ensure quality similar to local criteria.
    """
    if requests is None:
        print("[TATOEBA] 'requests' not available; pip install requests to enable API fallback.", file=sys.stderr)
        return None

    # Tatoeba API v0 search endpoint
    url = "https://tatoeba.org/en/api_v0/search"
    headers = {"User-Agent": user_agent}
    params = {
        "query": term,
        "from": lang_from,   # source language
        "orphans": "no",
        "unapproved": "no",
        "has_audio": "no",
        "sort": "random",
        "trans_filter": "limit" ,
        "trans_to": "",      # don't filter by translation language
        "limit": 50,
        "page": 1,
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            print(f"[TATOEBA] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return None
        data = resp.json()
    except Exception as e:  # pragma: no cover
        print(f"[TATOEBA] Request error: {e}", file=sys.stderr)
        return None

    # Expected shape: {"results": [{"text": "...", ...}, ...]}
    results = data.get("results") or data.get("sentences") or []
    if not isinstance(results, list):
        return None

    term_re = build_match_regex(term)
    best: Optional[str] = None
    best_len = 10**9
    for item in results:
        txt = item.get("text") if isinstance(item, dict) else None
        if not txt:
            continue
        s = first_sentence(strip_outer_quotes(normalize_ws(txt)))
        if not term_re.search(s):
            continue
        if not seems_complete_sentence(s, min_words=min_words, max_words=max_words):
            continue
        n = len(s.split())
        if n < best_len:
            best, best_len = s, n
    return best


# ---------------------------
# File readers
# ---------------------------

def read_txt(path: Path) -> str:
    # Try utf-8, fallback to latin-1 if needed
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="ignore")
        except Exception:
            continue
    return ""


def read_epub(path: Path) -> str:
    """
    Minimal-dependency EPUB text extractor:
    - Opens the EPUB as a zip
    - Reads all .xhtml/.html/.htm files
    - Concatenates their stripped text
    """
    text_parts: List[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Heuristic: read content files in the container order if possible
            names = [n for n in zf.namelist()
                     if n.lower().endswith((".xhtml", ".html", ".htm"))]
            for name in names:
                try:
                    raw = zf.read(name)
                except KeyError:
                    continue
                try:
                    chunk = raw.decode("utf-8", errors="ignore")
                except Exception:
                    chunk = raw.decode("latin-1", errors="ignore")
                text_parts.append(strip_html(chunk))
    except zipfile.BadZipFile:
        return ""
    return normalize_ws(" ".join(text_parts))


def extract_text_from_file(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith(".txt"):
        return read_txt(path)
    if lower.endswith(".epub"):
        return read_epub(path)
    # (Optional) PDF support could be added with PyPDF2 if installed.
    return ""


def iter_source_files(root: Path) -> Iterable[Path]:
    exts = {".txt", ".epub"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


# ---------------------------
# Helper for morphological variants
# ---------------------------

def _term_variants(term: str) -> List[str]:
    t = term.lower()
    # Basic surface forms: exact, possessive, plural, plural-es
    variants = {t, t + "'s", t + "s", t + "es"}
    return list(variants)


# ---------------------------
# Search logic
# ---------------------------

def search_sources_for_term(
    term: str,
    file_sentences: Dict[Path, List[str]],
    min_words: int,
    max_words: int,
    prefer_shorter: bool = True,
    inverted: Optional[Dict[str, List[Tuple[Path, int]]]] = None,
) -> Optional[str]:
    """
    Search using an inverted index when available to avoid scanning all sentences.
    Fallback to full scan if the term has no postings.
    """
    term_re = build_match_regex(term)
    best: Optional[str] = None
    best_len: int = 10**9
    candidates: List[Tuple[Path, int]] = []

    if inverted is not None:
        seen: set = set()
        for v in _term_variants(term):
            for ref in inverted.get(v, []):
                if ref not in seen:
                    seen.add(ref)
                    candidates.append(ref)

    if candidates:
        # Iterate only candidate sentences
        for fp, si in candidates:
            s = file_sentences.get(fp, [])
            if si >= len(s):
                continue
            s2 = strip_outer_quotes(s[si])
            s2 = first_sentence(s2)
            if term_re.search(s2) and seems_complete_sentence(s2, min_words, max_words):
                if prefer_shorter:
                    n = len(s2.split())
                    if n < best_len:
                        best, best_len = s2, n
                else:
                    return s2
        return best

    # Fallback: rare word or OOV — scan all sentences
    for fp, sentences in file_sentences.items():
        if not sentences:
            continue
        for s in sentences:
            s2 = strip_outer_quotes(s)
            s2 = first_sentence(s2)
            if term_re.search(s2) and seems_complete_sentence(s2, min_words, max_words):
                if prefer_shorter:
                    n = len(s2.split())
                    if n < best_len:
                        best, best_len = s2, n
                else:
                    return s2
    return best


# ---------------------------
# CSV processing
# ---------------------------

def load_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """
    Read CSV and return (header, rows), while defensively handling:
      - Byte Order Mark (BOM) on the first header cell
      - Extra columns present in some rows (DictReader stores these under the `restkey`,
        or under `None` if restkey is not set)
      - Stray/unknown keys not present in the header
    """
    with path.open("r", encoding="utf-8", newline="") as f:
        # Capture unexpected extra columns in a temporary key to avoid `None` keys.
        reader = csv.DictReader(f, restkey="_EXTRA", restval="")
        raw_header = reader.fieldnames or []

        # Normalize header cells (strip whitespace and BOM)
        header: List[str] = []
        for h in raw_header:
            h2 = (h or "").strip().lstrip("\ufeff")
            header.append(h2)

        rows: List[Dict[str, str]] = []
        extras_seen = 0

        for row in reader:
            # Remove legacy None key if present (older csv versions may still use it)
            if None in row:
                row.pop(None, None)

            # Drop the temporary restkey bucket
            if "_EXTRA" in row:
                if row["_EXTRA"]:
                    extras_seen += 1
                row.pop("_EXTRA", None)

            # Remove any keys not present in the normalized header
            for k in list(row.keys()):
                if k not in header:
                    row.pop(k, None)

            rows.append(row)

    if extras_seen:
        print(f"[WARN] {extras_seen} row(s) had extra columns beyond the header and were ignored.", file=sys.stderr)

    return header, rows



def write_csv_rows(path: Path, header: List[str], rows: List[Dict[str, str]]) -> None:
    """
    Write rows using exactly the provided header. Any keys not in the header are ignored,
    and missing keys are filled with empty strings to keep the output well-formed.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe_row = {h: row.get(h, "") for h in header}
            writer.writerow(safe_row)


# ---------------------------
# Streaming/append-safe output helper
# ---------------------------
def _prepare_output_writer(path: Path, header: List[str]) -> Tuple[csv.DictWriter, int]:
    """
    Open `path` for streaming writes. If the file already exists with the same header,
    resume by appending new rows and return (writer, rows_already_written).
    If it doesn't exist (or is empty), create it and write the header.
    """
    rows_written = 0
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if exists else "w"
    f = path.open(mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")

    if exists:
        # Verify/consume existing header and count already written rows to enable resume.
        # We can't easily reuse the same file handle for reading and writing,
        # so do a quick separate read to count rows.
        with path.open("r", encoding="utf-8", newline="") as rf:
            r = csv.reader(rf)
            try:
                existing_header = next(r)
            except StopIteration:
                existing_header = []
            # Normalize for BOM/whitespace
            existing_header = [(h or "").strip().lstrip("\ufeff") for h in existing_header]
            norm_header = [(h or "").strip().lstrip("\ufeff") for h in header]
        if existing_header != norm_header:
            f.close()
            raise SystemExit(f"[ERROR] Output CSV exists with a different header. "
                             f"Expected {norm_header}, found {existing_header}")
        # Count existing data rows (exclude header)
        with path.open("r", encoding="utf-8", newline="") as rf2:
            rows_written = sum(1 for _ in rf2) - 1
    else:
        writer.writeheader()
    return writer, rows_written


def main():
    ap = argparse.ArgumentParser(description="Fill English_Sentence from local sources.")
    ap.add_argument("--sources-dir", required=True, help="Directory containing .txt/.epub sources (searched recursively).")
    ap.add_argument("--input-csv", required=True, help="Input CSV with columns including English_Term and English_Sentence.")
    ap.add_argument("--output-csv", required=True, help="Where to write the updated CSV.")
    ap.add_argument("--min-words", type=int, default=6, help="Minimum words per sentence.")
    ap.add_argument("--max-words", type=int, default=28, help="Maximum words per sentence.")
    ap.add_argument("--prefer-shorter", action="store_true", help="Prefer the shortest acceptable sentence if multiple are found.")
    ap.add_argument("--dry-run", action="store_true", help="Scan and report matches without writing the CSV.")
    ap.add_argument("--start-index", type=int, default=0, help="Row index to start from (0-based after header).")
    ap.add_argument("--max-rows", type=int, default=None, help="Only process up to this many rows.")
    ap.add_argument("--overwrite", choices=["yes", "no"], default="no",
                    help="Overwrite existing English_Sentence values (yes/no). Default: no")
    ap.add_argument("--tatoeba-fallback", action="store_true",
                    help="If no local sentence is found, query Tatoeba API for an English sentence.")
    ap.add_argument("--tatoeba-lang", default="eng",
                    help="Source language code for Tatoeba search (default: eng for English).")
    ap.add_argument("--tatoeba-interval", type=float, default=1.0,
                    help="Seconds to sleep between Tatoeba API calls (polite rate limit).")
    ap.add_argument("--user-agent",
                    help="User-Agent header for API requests. If omitted, uses the TATOEBA_USER_AGENT env var. Required when --tatoeba-fallback is set.")
    args = ap.parse_args()

    # Resolve user-agent (CLI overrides env). Required if API fallback is enabled.
    effective_user_agent = args.user_agent or os.environ.get("TATOEBA_USER_AGENT")
    if args.tatoeba_fallback and not effective_user_agent:
        print("[ERROR] --tatoeba-fallback requires a User-Agent. Pass --user-agent or set TATOEBA_USER_AGENT.", file=sys.stderr)
        sys.exit(1)

    sources_dir = Path(args.sources_dir).expanduser()
    input_csv = Path(args.input_csv).expanduser()
    output_csv = Path(args.output_csv).expanduser()

    if not sources_dir.exists():
        print(f"[ERROR] Sources directory not found: {sources_dir}", file=sys.stderr)
        sys.exit(1)
    if not input_csv.exists():
        print(f"[ERROR] Input CSV not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    header, rows = load_csv_rows(input_csv)

    # Create/append output CSV and possibly resume from prior progress.
    out_writer, already_written = _prepare_output_writer(output_csv, header)

    # Ensure required columns exist
    required_cols = {"English_Term", "English_Sentence"}
    missing = required_cols - set(h or "" for h in header)
    if missing:
        print(f"[ERROR] Missing required columns in CSV: {missing}", file=sys.stderr)
        sys.exit(1)

    files = list(iter_source_files(sources_dir))
    if not files:
        print(f"[WARN] No .txt or .epub files found in {sources_dir}", file=sys.stderr)

    # Preload and pre-split each source once (major speedup)
    print(f"[INIT] Preloading {len(files)} files…")
    file_sentences: Dict[Path, List[str]] = {}
    total_sentences = 0
    for fp in files:
        text = extract_text_from_file(fp)
        if not text:
            file_sentences[fp] = []
            continue
        sents = split_sentences(text)
        file_sentences[fp] = sents
        total_sentences += len(sents)
    print(f"[INIT] Ready. {total_sentences} sentences indexed across {len(files)} files.")

    # Build a lightweight inverted index: token (lowercased) -> list of (Path, sentence_index)
    print("[INIT] Building inverted index…")
    from collections import defaultdict
    inverted: Dict[str, List[Tuple[Path, int]]] = defaultdict(list)
    for fp, sents in file_sentences.items():
        for si, s in enumerate(sents):
            # use unique tokens per sentence to keep postings lists smaller
            toks = set(t.lower() for t in _tokenize_simple(s) if re.match(r"[A-Za-z']+$", t))
            for t in toks:
                inverted[t].append((fp, si))
    print(f"[INIT] Indexed {len(inverted)} unique tokens.")

    # Cache to avoid repeated searches for the same term
    cache: Dict[str, Optional[str]] = {}

    updated = 0
    skipped = 0

    for i, row in enumerate(rows, 0):
        # If resuming, skip rows already written to the output file
        if i < already_written:
            continue
        if i < args.start_index:
            continue
        if args.max_rows is not None and (i - args.start_index) >= args.max_rows:
            break

        term = (row.get("English_Term") or "").strip()
        if not term:
            skipped += 1
            continue

        already = (row.get("English_Sentence") or "").strip()
        if already and args.overwrite == "no":
            skipped += 1
            continue

        if (i % 200) == 0:
            print(f"[PROGRESS] row {i}/{len(rows)} (updated={updated}, skipped={skipped})")

        key = term.lower()
        if key in cache:
            sentence = cache[key]
        else:
            sentence = search_sources_for_term(
                term=term,
                file_sentences=file_sentences,
                min_words=args.min_words,
                max_words=args.max_words,
                prefer_shorter=args.prefer_shorter,
                inverted=inverted,
            )
            cache[key] = sentence

        if not sentence and args.tatoeba_fallback:
            # Be polite to the remote API
            if args.tatoeba_interval > 0:
                time.sleep(args.tatoeba_interval)
            sentence = fetch_tatoeba_sentence(
                term,
                lang_from=args.tatoeba_lang,
                min_words=args.min_words,
                max_words=args.max_words,
                user_agent=effective_user_agent,
            )
            if sentence:
                cache[key] = sentence  # cache the API hit too
                print(f"[TATOEBA] {term!r} → {sentence[:100]}{'...' if len(sentence)>100 else ''}")

        # We will write the row for this index immediately after deciding whether to update.
        if sentence:
            action = "OVERWRITE" if already and args.overwrite == "yes" else "OK"
            row["English_Sentence"] = sentence
            updated += 1
            print(f"[{action}] {term!r} → {sentence[:100]}{'...' if len(sentence)>100 else ''}")
        else:
            if args.tatoeba_fallback:
                print(f"[MISS] {term!r} (no acceptable sentence found locally or via Tatoeba)")
            else:
                print(f"[MISS] {term!r} (no acceptable sentence found locally)")

        # Always write the current row (updated or original) immediately to the output CSV.
        safe_row = {h: row.get(h, "") for h in header}
        out_writer.writerow(safe_row)

    if args.dry_run:
        print(f"\n[DRY RUN] Would update {updated} rows; skipped {skipped}.")
        return

    # Stream out any remaining rows that were not iterated (shouldn't happen, but safe)
    # (No-op by design: we wrote each row as we processed it.)

    # Close the underlying file handle of the writer if present
    try:
        out_file = out_writer.writer.writerows.__self__  # type: ignore[attr-defined]
    except Exception:
        out_file = None
    if hasattr(out_file, "close"):
        out_file.close()

    print(f"\n[DONE] Updated {updated} rows; skipped {skipped}.")
    print(f"[OUT] {output_csv}")

if __name__ == "__main__":
    main()