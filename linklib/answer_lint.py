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
# An inference bolted on after a figure, flagged even in a sentence that carries an [n]: the
# citation covers the number, not the conclusion drawn from it. Flag only, never stripped.
INFERENCE = (
    ("meaning", re.compile(r"(?:—|,)\s*meaning\b", re.I)),
    ("which means", re.compile(r"\bwhich means\b", re.I)),
    ("attractive to investors", re.compile(r"\battractive to investors\b", re.I)),
    ("is the floor for", re.compile(r"\bis the floor for\b", re.I)),
    # A prediction about what stored medians cover or what a kind of company spends, after a figure.
    ("will understate", re.compile(r"\bwill (?:understate|overstate)\b", re.I)),
    ("typically spends", re.compile(r"\btypically spends?\b", re.I)),
    # Characterization the rules ban. Any sentence, cited or not; "material" also matches the noun
    # ("source material"), a known false positive.
    ("meaningful", re.compile(r"\bmeaningful\b", re.I)),
    ("material", re.compile(r"\bmaterial\b", re.I)),
    ("significant", re.compile(r"\bsignificant\b", re.I)),
    ("sharp", re.compile(r"\bsharp\b", re.I)),
    ("steep", re.compile(r"\bsteep\b", re.I)),
)
# Characterization that still gets past the strip. Flag only, any sentence, cited or not; never stripped.
# Known false positives: "stable" in "a stable value", "toward the" in a spatial or procedural sentence,
# "healthy" in a quoted metric name ("healthy ARR growth" a report prints), "attractive" in a report title.
CHARACTERIZATION = (
    ("high end of", re.compile(r"\bhigh end of\b", re.I)),
    ("low end of", re.compile(r"\blow end of\b", re.I)),
    ("relatively stable", re.compile(r"\brelatively stable\b", re.I)),
    ("stable", re.compile(r"\bstable\b(?<!relatively stable)", re.I)),
    ("structurally", re.compile(r"\bstructurally\b", re.I)),
    ("toward the", re.compile(r"\btoward the\b", re.I)),
    ("healthy", re.compile(r"\bhealthy\b", re.I)),
    ("attractive", re.compile(r"\battractive\b(?! to investors)", re.I)),
    ("rule of thumb", re.compile(r"\brule[- ]of[- ]thumb\b", re.I)),
)
# A trend claim in a sentence with no figure: nothing shown supports it. Flag only. Stripping is not
# safe here (the same words open true sentences with a number in the next clause), so the admin row shows it.
TREND = (
    ("broad-based", re.compile(r"\bbroad-based\b", re.I)),
    ("across all", re.compile(r"\bacross all\b", re.I)),
    ("compression is", re.compile(r"\bcompression is\b", re.I)),
)
# A change worked out in the answer ("a drop of 61", "down 5 points", "12% higher") with no
# "derived, not in report" in the sentence. A change a report states, with a citation, is flagged
# too (known false positive): the lint cannot tell who computed it.
DERIVED_CHANGE = re.compile(
    r"\b(?:a\s+)?(?:drop|decline|fall|decrease|increase|rise|gain)\s+of\s+\$?\d"
    r"|\b(?:down|up)\s+\$?\d[\d.,]*\s*(?:%|points?|pts|pp|percentage points)?"
    r"|\b\d[\d.,]*%\s+(?:higher|lower|more|less|above|below)\b", re.I)
DERIVED_LABEL = "derived, not in report"

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
        for phrase, rx in INFERENCE:
            if rx.search(sent):
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        for phrase, rx in CHARACTERIZATION:
            if rx.search(sent) and not (phrase == "stable" and re.search(r"relatively stable", sent, re.I)):
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        if not _NUMBER.search(sent):
            for phrase, rx in TREND:
                if rx.search(sent):
                    flags.append({"phrase": phrase, "snippet": sent[:200]})
        if DERIVED_CHANGE.search(sent) and DERIVED_LABEL not in sent.lower() and not sent.lstrip().startswith("|"):
            flags.append({"phrase": "derived change without label", "snippet": sent[:200]})
        if _MARKER.search(sent):
            continue
        for phrase in TRIGGERS:
            n = len(phrase.split())
            for i in range(len(low_words) - n + 1):
                if low_words[i:i + n] == phrase.split() and _numbers_near(words, i, i + n):
                    flags.append({"phrase": phrase, "snippet": sent[:200]})
                    break
    return flags
