"""Emergency detection. Layer 1 is a normalized phrase and regex list; layer 2 is an optional hook.

detect(text, keywords, layer2=None) -> {"emergency": bool, "matched": [...], "layer": "1"|"2"|"unsure"|None}
"""
from __future__ import annotations

import difflib
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

def _near(a: str, b: str, gap: int = 40) -> str:
    """Unordered proximity rule: a word from group a and a word from group b within `gap` characters."""
    return rf"re:\b(?:{a})\b.{{0,{gap}}}\b(?:{b})\b|\b(?:{b})\b.{{0,{gap}}}\b(?:{a})\b"


# Proximity rules catch how people actually describe things ("breathing really fast", "belly is swollen
# and hard", "blood in her urine"), which exact phrases miss. Over-matching is the safe direction.
RX_BREATH = _near(r"breathing|breathe|breath|breathes", r"fast|rapid|hard|heavy|heavily|laboured|labored|struggl\w*|mouth open|open mouth|noisy|wheez\w*|weird|strange|shallow")
RX_BLOOD = _near(r"blood|bloody|bleeding|bleeds", r"urine|pee|peeing|diarrh\w*|stool|poop|vomit\w*|throw\w* up|nose|wont stop|will not stop|not stop\w*|a lot|lots|everywhere|dripping|pouring|keeps")
RX_BELLY = _near(r"belly|stomach|abdomen|tummy", r"swollen|swelling|hard|bloated|distended|tight|huge|big")
RX_GUMS = _near(r"gums|tongue|lips", r"blue|white|pale|grey|gray|purple", 20)
RX_RETCH = _near(r"vomit\w*|retch\w*|throw\w* up|heav\w*|gag\w*", r"nothing (?:comes|coming|came) (?:up|out)|but nothing|cant|cannot|unproductive")
RX_ATTACK = _near(r"attacked|bitten|mauled|fight|bit", r"dog|coyote|deep|bleeding|wound|puncture|bad", 30)
RX_URINE = _near(r"pee|peeing|urinat\w*|litter box|litter", r"cant|cannot|straining|strain\w*|nothing comes|blocked|no urine|not peeing|unable", 30)
RX_EYE = _near(r"eye|eyeball", r"popped|out of (?:the )?socket|bulging|hanging", 20)

# Descriptive rules, added after an independent test showed that people mostly DESCRIBE emergencies
# ("tongue is huge and bright red", "cut opened up and I can see stuff") instead of naming them.
RX_HEAT = r"re:\bleft\b.{0,40}\bin (?:a |the |my |our )?(?:hot )?(?:car|truck|van|vehicle)\b|\b(?:heat ?stroke|overheat\w*|too hot)\b"
RX_TONGUE = _near(r"tongue", r"huge|swollen|bright red|purple|blue|dark|hanging|big", 30)
RX_DROOL = _near(r"drool\w*|salivat\w*", r"thick|ropey|ropy|nonstop|non stop|sudden\w*|burn\w*|pain\w*|weak|cant|panting|foam\w*|mouth", 40)
RX_FOAM = r"re:\b(?:foaming|frothing|foam(?:ing)? at the mouth)\b"
RX_STUMBLE = r"re:\b(?:stumbl\w*|stagger\w*|wobbl\w*|dragging (?:his|her|its) (?:back |hind )?legs?|paralys\w*|paraliz\w*|paralyz\w*|cant (?:stand|walk)|wont (?:stand|get up))\b"
RX_BIRTH = _near(r"pushing|labou?r|contractions|whelp\w*|giving birth|delivering|straining", r"stuck|halfway|half way|half out|not coming|wont come|cant deliver|green discharge|for (?:over |more than )?\w+ hours?|over \w+ hours?", 60)
RX_BIRTH2 = r"re:\b(?:puppy|pup|kitten|baby)\b.{0,30}\b(?:stuck|halfway|half way|half out)\b|\b(?:stuck|halfway|half way)\b.{0,30}\b(?:puppy|pup|kitten|baby)\b"
RX_STING = _near(r"stung|stings?|bees?|wasps?|hornets?|snake|spider|bitten by a (?:snake|spider)", r"swell\w*|swollen|huge|face|hives|wheez\w*|throat|lips|eyes", 50)
RX_BAIT = (r"re:\b(?:rat|mouse|mice|rodent|slug|snail)s? (?:bait|poison|block|pellet|killer)s?\b"
           r"|\b(?:bait|rodenticide|poison\w*|pesticide|insecticide|fertili[sz]er|weed ?killer|herbicide)\b.{0,40}\b(?:ate|eaten|mouth|licked|swallow\w*|chew\w*|got into|drank|some)\b"
           r"|\b(?:ate|eaten|swallow\w*|chewed|licked|drank|got into)\b.{0,40}\b(?:bait|pesticide|insecticide|fertili[sz]er|weed ?killer|herbicide|bleach|detergent|pool chemicals?|chemicals?)\b")
RX_CHEM_BURN = _near(r"mouth|tongue|lips|paws?|skin|eyes?", r"burn\w*|blister\w*|red raw|chemical\w*", 40)
RX_WOUND = _near(r"cut|incision|stitch\w*|suture\w*|staples?|surgery site|wound|spay|neuter", r"opened up|open up|came open|came apart|burst|split open|gaping|coming out|see inside|guts|intestin\w*|organs?|fell out|bulging", 60)
RX_GUMS2 = _near(r"gu?mm?s?", r"paper|chalk|ghost|yellow|porcelain|snow|white|pale|grey|gray|blue|purple", 25)
RX_SQUAT = _near(r"squat\w*|strain\w*|posturing|keeps trying to (?:go|pee|poop|urinate)", r"nothing|no urine|just (?:a )?few drops|drops of|red|blood|whimper\w*|cry\w*", 50)
RX_FALL = r"re:\b(?:fell|fall|fallen|jumped|thrown|dropped)\b.{0,6}\b(?:off|from|out of|down|over)\b.{0,40}\b(?:balcony|window|stairs|staircase|roof|deck|porch|ledge|cliff|storey|story|floor)\b|\blying on (?:her|his|its|their) side\b"
RX_SWEET = r"re:\b(?:sugar ?less|sugar ?free|birch (?:sugar|bark)|artificial sweetener)\b"
RX_BELLYPAIN = _near(r"belly|tummy|stomach|abdomen|neck", r"howl\w*|scream\w*|yelp\w*|crying out|wont let (?:anyone|anybody|me|us) (?:touch|near|go near)|flinch\w*|hunched|arched back|praying position", 60)
RX_FOREIGN = r"re:\b(?:swallow\w*|ate|eaten|eating|chewed)\b.{0,12}\b(?:sock|socks|toy|bone|bones|string|ribbon|corn ?cob|rock|stone|needle|hook|battery|batteries|magnet|coin|penny|underwear|knickers|stocking|hair ?tie|rubber band|tinsel|skewer)\b"
RX_WEAK = (r"re:\b(?:super|very|so|extremely|really) (?:weak|limp|floppy|lethargic|listless)\b|\b(?:limp|floppy)\b"
           r"|\bwont (?:lift|raise|hold up) (?:his|her|its) head\b|\bwont (?:wake|respond|react)\b|\bnot responding\b|\bglassy eyes?\b"
           r"|\bcant keep (?:anything|water|food) down\b|\bvomit\w* (?:everything|even water|nonstop|non stop|constantly|repeatedly|all night)\b")
RX_ATTACK2 = r"re:\b(?:grabbed|bit|bitten|mauled|attacked|savaged|shook)\b.{0,40}\b(?:neck|throat|chest|belly|stomach|head|face)\b|\bblood on (?:her|his|its|the) (?:chest|neck|belly|face|fur|coat)\b"
RX_BREATH2 = r"re:\bwheez\w*\b|\bbreathing (?:funny|weird|strange|odd|noisy|loud)\b|\bmouth open\b.{0,30}\b(?:breath\w*|gasp\w*|pant\w*)\b|\bpanting (?:non ?stop|constantly|and shaking|heavily)\b"

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
    RX_BREATH, RX_BLOOD, RX_BELLY, RX_GUMS, RX_RETCH, RX_ATTACK, RX_URINE, RX_EYE,
    RX_HEAT, RX_TONGUE, RX_DROOL, RX_FOAM, RX_STUMBLE, RX_BIRTH, RX_BIRTH2, RX_STING, RX_BAIT, RX_CHEM_BURN, RX_WOUND,
    RX_GUMS2, RX_SQUAT, RX_FALL, RX_SWEET, RX_BELLYPAIN, RX_FOREIGN, RX_WEAK, RX_ATTACK2, RX_BREATH2,
]

# One example sentence for every `re:` entry above (a test makes sure each regex has one).
EMERGENCY_REGEX_SAMPLES: dict[str, list[str]] = {
    RX_INGEST: ["Oscar just ate a whole bar of dark chocolate", "he got into the rat poison", "she swallowed a bunch of raisins"],
    RX_LILY: ["my cat ate a lily", "the cat chewed on lilies in the vase", "lilies are near my cat"],
    RX_NOT_BREATHING: ["he is not breathing", "she stopped breathing", "he cant breathe at all"],
    RX_HIT: ["my puppy was hit by a car", "she got hit by a truck"],
    RX_LABOUR: ["she has been in labour for 3 hours", "labor more than 2 hours now"],
    RX_BREATH: ["he is panting and breathing really fast", "her breathing is laboured", "breathing with her mouth open"],
    RX_BLOOD: ["there is blood in her urine", "liquid diarrhea with blood in it", "the bleeding will not stop"],
    RX_BELLY: ["her belly is swollen and hard", "his stomach looks bloated"],
    RX_GUMS: ["her gums look blue", "his gums are white"],
    RX_RETCH: ["he keeps trying to vomit but nothing comes up", "retching but nothing"],
    RX_ATTACK: ["attacked by another dog and has a deep bite", "bitten by a coyote"],
    RX_URINE: ["straining in the litter box", "he cant pee"],
    RX_EYE: ["her eye popped out", "eyeball is bulging"],
    RX_HEAT: ["we left the dog in the hot car for an hour", "I think he has heatstroke"],
    RX_TONGUE: ["his tongue is huge and bright red", "her tongue looks purple"],
    RX_DROOL: ["the drool is thick and nonstop", "she is drooling and her mouth looks sore"],
    RX_FOAM: ["he is foaming at the mouth", "frothing and shaking"],
    RX_STUMBLE: ["he is stumbling around the kitchen", "she is dragging her back legs", "he cant stand up"],
    RX_BIRTH: ["she has been pushing for two hours and nothing", "in labour for hours and not coming"],
    RX_BIRTH2: ["a puppy is halfway out and stuck", "the kitten is stuck"],
    RX_STING: ["stung by bees and her face is swollen", "bitten by a snake and swelling fast"],
    RX_BAIT: ["I think he ate some mouse bait", "she got into the rat poison", "he licked the weed killer"],
    RX_CHEM_BURN: ["her mouth looks burned", "chemical on her paws and skin is blistering"],
    RX_WOUND: ["the incision opened up and I can see inside", "his stitches burst and something is coming out"],
    RX_GUMS2: ["her gumms are white as paper", "gums look like chalk"],
    RX_SQUAT: ["he keeps squatting and nothing comes out", "straining with just a few drops of blood"],
    RX_FALL: ["she fell off the balcony", "the kitten fell from the window"],
    RX_SWEET: ["he ate sugarless gum", "birch sugar candy was in her bag"],
    RX_BELLYPAIN: ["howling and will not let anybody touch his tummy", "screaming when I press her belly"],
    RX_FOREIGN: ["he swallowed a sock", "she ate a corn cob", "ate a hair tie"],
    RX_WEAK: ["he is super weak", "she is limp", "wont lift his head", "vomiting everything even water"],
    RX_ATTACK2: ["a big dog grabbed her by the neck", "bitten on the chest and bleeding"],
    RX_BREATH2: ["he is wheezing", "breathing funny", "panting nonstop"],
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


_FUZZY_VOCAB = ["gasping", "breathing", "breathe", "bleeding", "seizure", "seizures", "seizing", "convulsing", "collapsed",
                "unresponsive", "poison", "poisoned", "chocolate", "vomiting", "swollen", "lethargic", "paralyzed", "choking",
                "drooling", "stumbling", "xylitol", "antifreeze", "wheezing", "unconscious", "gums", "belly", "stomach"]
_FUZZY_SET = set(_FUZZY_VOCAB)
_FUZZY_SKIP = {"breeding", "feeding", "speeding", "weeding", "reading", "leading", "bedding", "belief", "believe"}


def canonicalize(norm: str) -> str:
    """Repair typos of the few words that matter most ("gaspin", "gumms", "seizeure") before matching.
    Over-correcting is the safe direction here: a false emergency costs a trip, a miss can cost a pet."""
    out = []
    for tok in norm.split():
        if len(tok) >= 5 and tok not in _FUZZY_SET and tok not in _FUZZY_SKIP:
            hit = difflib.get_close_matches(tok, _FUZZY_VOCAB, n=1, cutoff=0.86)
            if hit and hit[0][0] == tok[0]:
                tok = hit[0]
        out.append(tok)
    return " ".join(out)


def layer1(text: str, keywords: list[str]) -> list[str]:
    norm = normalize(text)
    if not norm:
        return []
    forms = [norm]
    fixed = canonicalize(norm)
    if fixed != norm:
        forms.append(fixed)
    matched: list[str] = []
    for kw in keywords:
        pat = compile_entry(kw)
        if pat is None:
            continue
        if any(pat.search(f) for f in forms):
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
