#!/usr/bin/env python3
import csv, sys, pathlib

# ---------------- number words (0–59) ----------------
ONES = ["zero","uno","due","tre","quattro","cinque","sei","sette","otto","nove"]
TEENS = ["dieci","undici","dodici","tredici","quattordici","quindici","sedici","diciassette","diciotto","diciannove"]
TENS  = ["","", "venti","trenta","quaranta","cinquanta"]

def it_number(n: int) -> str:
    if n < 10: return ONES[n]
    if 10 <= n < 20: return TEENS[n-10]
    t,u = divmod(n,10)
    base = TENS[t]
    # elide final vowel before 1 or 8: ventuno/ventotto, trentuno/… etc.
    if u in (1,8):
        base = base[:-1]
    return base if u == 0 else base + ONES[u]

# ---------------- spoken/idiomatic ----------------
def hour_spoken(h: int) -> str:
    if h == 0 or h == 24: return "mezzanotte"
    if h == 12: return "mezzogiorno"
    h12 = h % 12
    if h12 == 1: return "l'una"
    return f"le {it_number(h12)}"

def minute_spoken(m: int) -> str:
    if m == 0: return ""
    if m == 15: return " e un quarto"
    if m == 30: return " e mezza"
    if m == 45: return " meno un quarto"
    if 1 <= m < 30:
        return " e un minuto" if m == 1 else f" e {it_number(m)}"
    rem = 60 - m
    return " meno un minuto" if rem == 1 else f" meno {it_number(rem)}"

def spoken_time(hhmm: str) -> str:
    hh, mm = map(int, hhmm.split(":"))
    if mm <= 30:
        return hour_spoken(hh) + minute_spoken(mm)
    # 31–59 → “meno …” with next hour
    next_h = (hh + 1) % 24
    return hour_spoken(next_h) + minute_spoken(mm)

# ---------------- exact phrasing (24h) ----------------
def exact_time(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    if h == 0 and m == 0: return "mezzanotte"
    if h == 12 and m == 0: return "mezzogiorno"
    if m == 0:
        return it_number(h)  # e.g., "ventitré"
    return f"{it_number(h)} e {it_number(m)}"  # e.g., "ventitré e cinquantanove"

# ---------------- CSV I/O with row limits ----------------
if len(sys.argv) < 3:
    print("Usage: times_to_italian.py --input INPUT.csv --output OUTPUT.csv", file=sys.stderr)
    sys.exit(1)

args = {sys.argv[i]: sys.argv[i+1] for i in range(1, len(sys.argv)-1, 2)}
inp, outp = args.get("--input"), args.get("--output")
pathlib.Path(outp).parent.mkdir(parents=True, exist_ok=True)

with open(inp, newline="", encoding="utf-8") as f_in, open(outp, "w", newline="", encoding="utf-8") as f_out:
    r = csv.DictReader(f_in)
    if "English_Translation" not in r.fieldnames:
        raise SystemExit("Missing 'English_Translation' column.")
    fieldnames = list(r.fieldnames)
    if "Italian_Term" not in fieldnames: fieldnames.append("Italian_Term")
    if "Italian_Sentence" not in fieldnames: fieldnames.append("Italian_Sentence")
    w = csv.DictWriter(f_out, fieldnames=fieldnames)
    w.writeheader()

    for i, row in enumerate(r, start=2):  # row 1 is header; first data row = 2
        s = (row.get("English_Translation") or "").strip()
        in_range = 2 <= i <= 1441
        if in_range and s and ":" in s:
            row["Italian_Term"] = exact_time(s)
            row["Italian_Sentence"] = spoken_time(s)
        else:
            # copy through unchanged (preserve existing values if any)
            row["Italian_Term"] = row.get("Italian_Term", "")
            row["Italian_Sentence"] = row.get("Italian_Sentence", "")
        w.writerow(row)
