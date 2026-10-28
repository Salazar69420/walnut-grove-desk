"""Reject model-written text that contains a number that is not in the record."""
from __future__ import annotations

import re
from typing import Iterable

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_RX = re.compile(
    r"\b(?:(?:" + "|".join(_TENS) + r")(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r"|hundred)\b",
    re.I,
)
_TIME_RX = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_NUM_RX = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?|(?<![\w])\.\d+")


def _word_value(tok: str) -> float:
    t = tok.lower().replace("-", " ").split()
    if t == ["hundred"]:
        return 100
    return sum(_TENS.get(w, _UNITS.get(w, 0)) for w in t)


def extract(text: str) -> tuple[list[float], list[str]]:
    """Return (numbers, clock times) found in text. Times like 9:30 are not also counted as 9 and 30."""
    times = [f"{int(h)}:{m}" for h, m in _TIME_RX.findall(text)]
    rest = _TIME_RX.sub(" ", text)
    nums: list[float] = []
    for m in _NUM_RX.finditer(rest):
        raw = m.group(0).replace(",", "")
        try:
            nums.append(float(raw))
        except ValueError:
            continue
    rest2 = _NUM_RX.sub(" ", rest)
    for m in _WORD_RX.finditer(rest2):
        nums.append(_word_value(m.group(0)))
    return nums, times


def expand(allowed: Iterable[float | int | str]) -> tuple[set[float], set[str]]:
    nums: set[float] = set()
    times: set[str] = set()
    for a in allowed:
        if isinstance(a, str):
            if _TIME_RX.fullmatch(a.strip()):
                h, m = a.strip().split(":")
                times.add(f"{int(h)}:{m}")
            else:
                try:
                    a = float(a)
                except ValueError:
                    continue
            if isinstance(a, str):
                continue
        v = float(a)
        nums.update({v, float(int(v)), float(round(v)), round(v, 1), round(v, 2)})
    return nums, times


def check(text: str, allowed_numbers: Iterable[float | int | str]) -> tuple[bool, list[str]]:
    """(ok, bad_numbers). Every number in text must be in the allowed set."""
    nums, times = extract(text)
    ok_nums, ok_times = expand(allowed_numbers)
    bad: list[str] = []
    for n in nums:
        if not any(abs(n - a) < 1e-9 for a in ok_nums):
            bad.append(str(int(n)) if n == int(n) else str(n))
    for t in times:
        if t not in ok_times:
            bad.append(t)
    return (not bad), bad
