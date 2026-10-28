"""Emergency detection. Layer 1 is a normalized phrase and regex list; layer 2 is an optional hook.

detect(text, keywords, layer2=None) -> {"emergency": bool, "matched": [...], "layer": "1"|"2"|"unsure"|None}
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Callable

# Plain phrases are matched on normalized text (lowercase, apostrophes removed, other punctuation
# turned to spaces, spaces collapsed) with simple variants (plural, -ed, -ing). Entries starting with
# `re:` are regular expressions run on the normalized text.
RX_INGEST = r"re:\b(ate|eaten|eating|eats|swallowed|chewed|got into|licked)\b.{0,30}\b(chocolate|cocoa|xylitol|sugar free|sugarfree|rat poison|rodenticide|mouse poison|antifreeze|grapes|raisins|ibuprofen|tylenol|acetaminophen|medication|pills|tablets)\b"
RX_LILY = r"re:\b(lily|lilies)\b.{0,25}\b(cat|kitten)s?\b|\b(cat|kitten)s?\b.{0,25}\b(lily|lilies)\b|\b(ate|eaten|eating|chewed|licked|swallowed|nibbled)\b.{0,15}\b(lily|lilies)\b"
RX_NOT_BREATHING = r"re:\b(not|isnt|isn t|stopped|cant|cannot|wont)\b.{0,12}\b(breathing|breathe)\b"
RX_HIT = r"re:\bhit by (a |an |the )?(car|truck|vehicle|bike)\b"
RX_LABOUR = r"re:\blabou?r\b.{0,25}\b(more than|over|longer than|for)\b.{0,10}\b(\d+|two|three|four|five|six)\b.{0,10}\b(hour|hours|hr|hrs)\b"

DEFAULT_KEYWORDS: list[str] = [
    # breathing
    "trouble breathing", "cant breathe", "can't breathe", "cannot breathe", "struggling to breathe",
    "difficulty breathing", "hard to breathe", "gasping", "gasping for air", "not breathing", "blue gums",
    "blue tongue", "pale gums", "choking",
    # bleeding
    "bleeding that will not stop", "bleeding a lot", "wont stop bleeding", "won't stop bleeding",
    "bleeding badly", "lots of blood", "vomiting blood", "blood in vomit", "coughing blood", "bloody diarrhea",
    # neurological
    "seizure", "seizing", "fitting", "convulsions", "convulsing", "collapse", "collapsed", "passed out",
    "fainted", "unresponsive", "will not wake up", "wont wake up", "cant stand", "cant walk",
    "very sleepy", "very lethargic", "very lethargic after surgery", "collapse or very sleepy",
    # trauma
    "hit by a car", "struck by a car", "run over", "fell from a window", "hit by a vehicle", "attacked by a dog",
    "broken leg", "heatstroke", "heat stroke", "overheated", "eye popped out", "eye out of socket",
    # toxins
    "ate chocolate", "ate a chocolate bar", "ate some chocolate", "eaten chocolate", "xylitol", "sugar free gum",
    "sugar-free gum", "rat poison", "rodenticide", "mouse poison", "antifreeze", "ate my medication",
    "ate my pills", "ate human medication", "ibuprofen", "tylenol", "acetaminophen", "grapes", "raisins",
    "ate chocolate, xylitol or rat poison", "poisoned", "poison",
    # urinary
    "cannot pass urine", "straining to pee", "straining to urinate", "cant pee", "cant urinate",
    "not able to pee", "blocked", "blocked cat",
    # abdomen
    "swollen, hard belly", "swollen belly", "hard belly", "bloated", "bloat", "retching nothing comes up",
    "retching but nothing comes up", "unproductive retching", "vomiting repeatedly", "vomiting and cant stop",
    # post surgery, labour, other
    "not eating after surgery", "bleeding after surgery", "stitches came open", "labour more than",
    "labor more than", "in labour for hours", "in labor for hours", "stuck puppy", "stuck kitten",
    "bleeding that will not stop", "snake bite", "bee sting swelling", "swollen face", "seizures",
    # regex entries
    RX_INGEST,
    RX_LILY,
    RX_NOT_BREATHING,
    RX_HIT,
    RX_LABOUR,
]

# One example sentence for every `re:` entry above (a test makes sure each regex has one).
EMERGENCY_REGEX_SAMPLES: dict[str, list[str]] = {
    RX_INGEST: ["Oscar just ate a whole bar of dark chocolate", "he got into the rat poison", "she swallowed a bunch of raisins"],
    RX_LILY: ["my cat ate a lily", "the cat chewed on lilies in the vase", "lilies are near my cat"],
    RX_NOT_BREATHING: ["he is not breathing", "she stopped breathing", "he cant breathe at all"],
    RX_HIT: ["my puppy was hit by a car", "she got hit by a truck"],
    RX_LABOUR: ["she has been in labour for 3 hours", "labor more than 2 hours now"],
}

_APOS = re.compile(r"['‘’`´]")
_NONWORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    t = _APOS.sub("", (text or "").lower())
    t = _NONWORD.sub(" ", t)
    return t.strip()


def _word_pattern(w: str) -> str:
    if len(w) < 3 or not w.isalpha():
        return re.escape(w)
    base = w[:-1] if w.endswith("e") else w
    return re.escape(base) + r"(?:e|es|ed|ing|s)?"


@lru_cache(maxsize=2048)
def compile_entry(entry: str) -> re.Pattern | None:
    entry = (entry or "").strip()
    if not entry:
        return None
    if entry.startswith("re:"):
        try:
            return re.compile(entry[3:], re.I)
        except re.error:
            return None
    words = normalize(entry).split()
    if not words:
        return None
    return re.compile(r"\b" + r"\s+".join(_word_pattern(w) for w in words) + r"\b")


def check_regex(entry: str) -> str | None:
    """Return an error text for a bad `re:` entry, else None."""
    if not entry.startswith("re:"):
        return None
    if len(entry) > 400:
        return "that pattern is too long"
    try:
        re.compile(entry[3:], re.I)
    except re.error as e:
        return f"bad pattern: {e}"
    if re.search(r"\([^)]*[+*][^)]*\)[+*]", entry[3:]):
        return "that pattern could be very slow, please simplify it"
    return None


def layer1(text: str, keywords: list[str]) -> list[str]:
    norm = normalize(text)
    if not norm:
        return []
    matched: list[str] = []
    for kw in keywords:
        pat = compile_entry(kw)
        if pat is None:
            continue
        if pat.search(norm):
            matched.append(kw)
    return matched


def detect(text: str, keywords: list[str], layer2: Callable[[str], str] | None = None) -> dict:
    matched = layer1(text, keywords)
    if matched:
        return {"emergency": True, "matched": matched, "layer": "1"}
    if layer2 is not None:
        try:
            verdict = (layer2(text) or "").strip().lower()
        except Exception:  # noqa: BLE001 - any failure of the second check counts as unsure
            verdict = "unsure"
        if verdict == "emergency":
            return {"emergency": True, "matched": [], "layer": "2"}
        if verdict != "not":
            return {"emergency": True, "matched": [], "layer": "unsure"}
    return {"emergency": False, "matched": [], "layer": None}


def sample_text(entry: str) -> list[str]:
    """Texts that a given keyword entry must match (used by tests)."""
    if entry.startswith("re:"):
        return EMERGENCY_REGEX_SAMPLES.get(entry, [])
    words = entry.split()
    out = [entry, f"my dog is {entry} right now", entry.upper() + "!!"]
    # a plural/-ing variant of the last word
    last = words[-1]
    if last.isalpha() and len(last) > 3 and not last.endswith("s"):
        out.append(" ".join(words[:-1] + [last + "s"]))
    return out
