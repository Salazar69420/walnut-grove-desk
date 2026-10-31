"""Find the caller's name and pet in a call transcript, using only what the caller says about themselves.

Rule based, runs on this server, sends nothing anywhere. Built for precision: it would rather miss a person than invent or merge one.

* The staff speaker is the one who says the clinic greeting first. Only the other speaker's own words are used for the caller.
* A caller name comes from "my name is X Y", "this is X Y", "it's X Y", "X Y calling", "last name is Y" (with a first name from the same call).
* A pet comes from "my dog/cat/... X", "X is a/my dog/cat", "for X" right after a pet word, "X's appointment/vaccines/surgery".
* Staff names (anyone who introduces themselves in the clinic greeting often enough) and common non-name words are removed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

CAP = r"[A-Z][a-z]{1,19}(?:-[A-Z][a-z]{1,19})?"
NAME = rf"({CAP})(?:\s+(?:(?:van|de|der|la|le|di|al|el|mac|mc)\s+)?({CAP}))?"
GREETING = re.compile(r"walnut grove|animal hospital|thank you for calling|thanks for calling", re.I)
SELF_INTRO = [
    re.compile(rf"\b(?:my name is|my name's|name is|name's)\s+{NAME}"),
    re.compile(rf"\bthis is\s+{NAME}"),
    # "it's" and "I'm" are riskier ("it's Monday", "I'm Canadian"), so they need a clear ending
    re.compile(rf"\b(?:it's|it is|its|I am|I'm)\s+{NAME}\s*(?:calling|here|from|speaking|with|,|\.|$)"),
    re.compile(rf"\b{NAME}\s+(?:calling|speaking|here)\b"),
]
LAST_NAME = re.compile(rf"\b(?:last name is|last name's|surname is|under the name|under)\s+({CAP})")
PET_WORDS = {"dog": "dog", "puppy": "dog", "pup": "dog", "cat": "cat", "kitten": "cat", "kitty": "cat", "rabbit": "rabbit", "bunny": "rabbit",
             "bird": "bird", "parrot": "bird", "hamster": "hamster", "ferret": "ferret", "guinea pig": "guinea pig"}
_PW = "|".join(sorted(PET_WORDS, key=len, reverse=True))
PET_PATTERNS = [
    re.compile(rf"\b(?i:my|our|the)\s+(?i:little\s+|old\s+|new\s+|senior\s+)?((?i:{_PW}))(?i:,|\s+named|\s+called|\s+is\s+called|\s+is|'s name is)?\s+({CAP})\b"),
    re.compile(rf"\b({CAP})\s+is\s+(?:a|my|our)\s+(?:\w+\s+){{0,2}}({_PW})\b"),
    re.compile(rf"\bfor\s+my\s+((?i:{_PW}))\s+({CAP})\b"),
    re.compile(rf"\b({CAP})'s\s+(?:appointment|vaccines?|shots?|surgery|bloodwork|blood work|medication|prescription|food|dental|results?)\b"),
]
# Words that look like names but are not: days, months, places, clinic words, fillers, common sentence starters.
STOP = set("""
walnut grove animal hospital clinic langley surrey vancouver abbotsford aldergrove fraser valley british columbia canada
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december
hello hi hey thanks thank okay ok yes yeah no sure sorry please good morning afternoon evening bye goodbye welcome right well just actually maybe
doctor dr doc vet nurse tech technician receptionist front desk office phone call calling line voicemail message messages
trupanion pethealth petsecure apoquel metacam gabapentin prednisone royal canin hills purina costco walmart amazon google facebook
canadian canadians english french hindi punjabi spanish ontario alberta toronto calgary
mom dad mommy daddy mister miss missus mrs mr ms sir madam ma'am
this that there here what when where why how who which
""".split())
GENERIC_PET_NAMES = set("one two the and for with him her his she not you".split())


@dataclass
class Found:
    first: str | None = None
    last: str | None = None
    pet: str | None = None
    species: str | None = None
    notes: list[str] = field(default_factory=list)


def _clean(tok: str | None) -> str | None:
    if not tok:
        return None
    if tok.lower() in STOP or len(tok) < 2:
        return None
    return tok


def split_turns(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*Speaker\s+(\d+)\s*:\s*(.*)", line)
        if m:
            out.append((m.group(1), m.group(2)))
        elif out and line.strip():
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
    return out


def staff_speaker(turns: list[tuple[str, str]]) -> str | None:
    for sp, line in turns[:4]:
        if GREETING.search(line):
            return sp
    return None


def staff_names_in_greeting(text: str) -> list[str]:
    """First names staff give in the clinic greeting ("Walnut Grove, this is Kristen")."""
    turns = split_turns(text)
    sp = staff_speaker(turns)
    if sp is None:
        return []
    names = []
    for s, line in turns[:3]:
        if s == sp:
            for m in re.finditer(rf"\b(?:this is|my name is|it's|speaking)\s+({CAP})", line):
                names.append(m.group(1))
    return names


def extract(text: str, staff: set[str]) -> Found | None:
    """The caller's identity for one call, or None. `staff` are lower-cased staff first names to exclude."""
    turns = split_turns(text)
    sp = staff_speaker(turns)
    if sp is None:
        return None  # not clearly a call answered by the clinic: do not guess who is who
    client = " ".join(line for s, line in turns if s != sp)
    f = Found()
    for rx in SELF_INTRO:
        for m in rx.finditer(client):
            first, last = _clean(m.group(1)), _clean(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
            if first and first.lower() not in staff:
                f.first, f.last = first, last
                break
        if f.first:
            break
    if f.first and not f.last:
        m = LAST_NAME.search(client)
        if m and _clean(m.group(1)) and m.group(1) != f.first:
            f.last = m.group(1)
    for rx in PET_PATTERNS:
        m = rx.search(client)
        if not m:
            continue
        g = m.groups()
        a, b = (g[0], g[1]) if len(g) == 2 else (None, g[0])
        if len(g) == 2:
            word, name = (a, b) if a.lower() in PET_WORDS else (b, a)
        else:
            word, name = None, b
        name = _clean(name)
        if name and name.lower() not in GENERIC_PET_NAMES and name.lower() not in staff and name != f.first and name != f.last:
            f.pet = name
            f.species = PET_WORDS.get((word or "").lower())
            break
    if not f.first and not f.pet:
        return None
    return f


def staff_from_corpus(texts) -> set[str]:
    """First names that introduce themselves in the clinic greeting at least three times are staff."""
    c: Counter = Counter()
    for t in texts:
        for n in set(staff_names_in_greeting(t)):
            c[n.lower()] += 1
    return {n for n, k in c.items() if k >= 3}
