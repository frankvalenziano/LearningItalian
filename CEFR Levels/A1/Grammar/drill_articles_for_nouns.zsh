#!/usr/bin/env zsh
# Drill Italian indefinite articles from a CSV of nouns.
# Usage: ./drill_articles.zsh nouns.csv [--say] [--curly]
#   --say   : (macOS) speak prompts/answers with `say`
#   --curly : display feminine elision as “un’” (curly) instead of "un'"

set -euo pipefail

if (( $# < 1 )); then
  echo "Usage: $0 <csv_file> [--say] [--curly]" >&2
  exit 1
fi

CSV="$1"; shift
USE_SAY=false
USE_CURLY=false
for arg in "$@"; do
  case "$arg" in
    --say)   USE_SAY=true ;;
    --curly) USE_CURLY=true ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

[[ -r "$CSV" ]] || { echo "Cannot read: $CSV" >&2; exit 1; }

# Normalize apostrophe preference
apostrophe="'"
$USE_CURLY && apostrophe=$'\u2019'  # right single quotation mark

# -------- Helpers --------
lower() { print -r -- "${1:l}"; }    # zsh lowercase
trim()  { local s="$1"; s="${s##[[:space:]]}"; s="${s%%[[:space:]]}"; print -r -- "$s"; }

is_vowel_start() {
  # Italian: vowels a e i o u (treat 'h' as consonant)
  [[ "$1" =~ '^[AaEeIiOoUu]' ]]
}

is_special_cluster() {
  # s+consonant, z, gn, ps, pn, x, y (case-insensitive)
  local w="${1:l}"
  [[ "$w" =~ '^(s[^aeiouh])' ]] || [[ "$w" =~ '^(z|gn|ps|pn|x|y)' ]]
}

expected_article_for() {
  # Args: gender, noun -> prints article (un/uno/una/un')
  local g="$(lower "$(trim "$1")")"
  local n="$(trim "$2")"
  if [[ "$g" != "m" && "$g" != "f" ]]; then
    print -r -- "??" ; return
  fi
  if [[ "$g" == "m" ]]; then
    if is_special_cluster "$n"; then
      print -r -- "uno"
    else
      print -r -- "un"
    fi
  else
    if is_vowel_start "$n"; then
      print -r -- "un${apostrophe}"
    else
      print -r -- "una"
    fi
  fi
}

normalize_article() {
  # Accept both straight and curly apostrophes from user
  local a="$(trim "$1")"
  a="${a//’/$apostrophe}"
  a="${a//\'/$apostrophe}"
  print -r -- "$a"
}

speak() { $USE_SAY && command -v say >/dev/null 2>&1 && say "$*"; }

# -------- Load CSV --------
typeset -a NOUNS GENDERS OVERRIDES HINTS
NOUNS=() GENDERS=() OVERRIDES=() HINTS=()

{
  IFS=,
  local line=0 noun gender override hint
  while read -r noun gender override hint || [[ -n "${noun:-}" ]]; do
    (( line++ ))
    # Skip empty lines
    [[ -z "${noun// }" ]] && continue
    # Skip header if it looks like one
    if (( line == 1 )) && [[ "${noun:l}" == "noun" ]]; then
      continue
    fi
    noun=$(trim "$noun")
    gender=$(trim "${gender:-}")
    override=$(trim "${override:-}")
    hint=$(trim "${hint:-}")

    NOUNS+=("$noun")
    GENDERS+=("$gender")
    OVERRIDES+=("$override")
    HINTS+=("$hint")
  done < "$CSV"
}

(( ${#NOUNS} > 0 )) || { echo "No rows found in $CSV" >&2; exit 1; }

# Shuffle indices for random order
typeset -a IDX
IDX=({1..${#NOUNS}})
# Fisher–Yates
for (( i=${#IDX}; i>1; i-- )); do
  j=$(( RANDOM % i + 1 ))
  tmp=${IDX[i]}; IDX[i]=${IDX[j]}; IDX[j]=$tmp
done

echo "👉 Indefinite Article Drill"
echo "File: $CSV"
echo "Type: un / uno / una / un'"
echo "Commands: h=hint, s=show, q=quit, enter=skip"
echo "--------------------------------------------------"

correct=0
total=0

for i in "${IDX[@]}"; do
  idx=$(( i - 1 ))
  noun="${NOUNS[idx]}"
  gender="${GENDERS[idx]}"
  override="${OVERRIDES[idx]}"
  hint="${HINTS[idx]}"

  # Choose expected (override beats rule if provided)
  expected=""
  if [[ -n "$override" ]]; then
    expected="$(normalize_article "$override")"
  else
    expected="$(expected_article_for "$gender" "$noun")"
  fi

  # If gender missing and no override, we can’t compute—skip gracefully
  if [[ "$expected" == "??" ]]; then
    echo "Skipping '${noun}' (missing/invalid gender)."
    continue
  fi

  while true; do
    printf "• %-20s (gender: %s)  → article? " "$noun" "$gender"
    read -r answer
    answer="$(normalize_article "${answer:-}")"

    if [[ -z "$answer" ]]; then
      echo "  ↳ Skipped. Correct: $expected $noun"
      speak "$expected $noun"
      (( total++ ))
      break
    fi
    case "$answer" in
      q|Q)
        echo
        goto_end=true
        break
        ;;
      h|H)
        if [[ -n "$hint" ]]; then
          echo "  💡 Hint: $hint"
          speak "$hint"
        else
          # Auto-hint based on rules
          if is_special_cluster "$noun"; then
            echo "  💡 Hint: masculine special cluster (s+consonant, z, gn, ps, pn, x, y) → uno"
          elif is_vowel_start "$noun" && [[ "${gender:l}" == "f" ]]; then
            echo "  💡 Hint: feminine + vowel start → un'"
          else
            echo "  💡 Hint: default patterns — m→un, f→una"
          fi
        fi
        continue
        ;;
      s|S)
        echo "  ✅ $expected $noun"
        speak "$expected $noun"
        (( total++ ))
        break
        ;;
      un|uno|una|un\'|un’|UN|UNO|UNA)
        # Normalize 'un\'' and 'un’' already handled
        user="$answer"
        # Uppercase variants normalize
        user="${user:l}"
        # unify straight/curly
        user="${user//’/$apostrophe}"
        user="${user//\'/$apostrophe}"

        if [[ "$user" == "${expected:l}" ]]; then
          echo "  ✅ Correct!"
          speak "Corretto"
          (( correct++ ))
        else
          echo "  ❌ Not quite. Correct: $expected $noun"
          speak "$expected $noun"
        fi
        (( total++ ))
        break
        ;;
      *)
        echo "  Enter one of: un, uno, una, un'   (or h/s/q)"
        ;;
    esac
  done

  if [[ "${goto_end:-false}" == true ]]; then
    break
  fi
done

echo "--------------------------------------------------"
if (( total > 0 )); then
  pct=$(( 100 * correct / total ))
else
  pct=0
fi
echo "Score: $correct / $total  (${pct}%)"
$USE_SAY && speak "Punteggio: $correct su $total"