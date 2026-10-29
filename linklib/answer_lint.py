"""Flag-only lint for an answer's claims (retest fixes, Part C3, 2026-10).

The answer rules (linklib.answer_rules) say: no cause unless a cited source states it,
no advice, no "practical read". They are instructions, so a breach is possible; this
finds the likely ones afterwards. It flags and never rewrites: the flags are stored in
ask_questions.lint_flags and shown on the admin report row.

A flag is a trigger phrase ("driven by", "reflects", "likely", "almost certainly",
"good threshold", "should") within about 12 words of a number, in a sentence that
carries no [n] citation marker. "practical read" is flagged wherever it appears (the
rules ban it outright). Known false positives are listed in tests/test_answer_lint.py.
"""
from __future__ import annotations

import re

WINDOW_WORDS = 12
TRIGGERS = ("driven by", "reflects", "reflecting", "likely", "almost certainly", "good threshold", "should")
ALWAYS = ("practical read",)

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKER = re.compile(r"\[\d+\]")
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|\d+x")
_WORD = re.compile(r"\S+")


def _numbers_near(words: list[str], start: int, end: int) -> bool:
    lo, hi = max(0, start - WINDOW_WORDS), min(len(words), end + WINDOW_WORDS)
    return any(_NUMBER.search(w) for w in words[lo:start] + words[end:hi])


def lint_answer(text: str) -> list[dict]:
    """[{phrase, snippet}] for each likely breach; [] for a clean answer."""
    flags: list[dict] = []
    for sent in _SENTENCE.split(text or ""):
        sent = sent.strip()
        if not sent:
            continue
        words = _WORD.findall(sent)
        low_words = [w.lower().strip(".,;:()\"'") for w in words]
        joined = " ".join(low_words)
        for phrase in ALWAYS:
            if phrase in joined:
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        if _MARKER.search(sent):
            continue
        for phrase in TRIGGERS:
            n = len(phrase.split())
            for i in range(len(low_words) - n + 1):
                if low_words[i:i + n] == phrase.split() and _numbers_near(words, i, i + n):
                    flags.append({"phrase": phrase, "snippet": sent[:200]})
                    break
    return flags
