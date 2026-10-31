"""High-precision strip of unsourced causes, forecasts and characterization (2026-10).

The answer rules (linklib.answer_rules) forbid them, and the model still writes them.
After the answer is produced, a sentence that matches a PATTERN below and has no [n]
citation marker is removed, and the removal is recorded (ask_questions.stripped_json,
shown on the admin report). Nothing changes quietly.

Narrow on purpose: a cited sentence is never stripped (it is flagged by the broader
lint instead); table rows and headings are never touched; and if stripping would leave
fewer than MIN_WORDS_LEFT words, nothing is stripped and the record says so.
"""
from __future__ import annotations

import re

MIN_WORDS_LEFT = 40

# (name, regex). Every pattern is a whole word or phrase; see tests/test_answer_strip.py
# for the false positives each one has on invented text.
PATTERNS = (
    ("causal", re.compile(r"\b(?:driven by|due to|because of|as a result of|reflects|reflecting)\b", re.I)),
    ("forecast or advice", re.compile(r"\b(?:expect|you should|would likely|will likely|likely to)\b", re.I)),
    ("characterization", re.compile(r"\b(?:striking|dramatic|dramatically|remarkable|notably)\b", re.I)),
)

_MARKER = re.compile(r"\[\d{1,2}\]")
_SENT = re.compile(r"(?<=[.!?])(?:\[\d{1,2}\])*\s+(?=[A-Z\"'(\[$0-9])")
_BULLET = re.compile(r"^(\s*(?:[-*]|\d+[.)])\s+)")


def _first_pattern(sentence: str) -> str:
    for name, rx in PATTERNS:
        if rx.search(sentence):
            return name
    return ""


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def strip_answer(text: str) -> tuple[str, list[dict]]:
    """(clean text, record). Each record item: {sentence, pattern, action}, action
    "removed" or "kept_short" (answer too short to strip; text returned unchanged)."""
    if not text:
        return text or "", []
    out_lines: list[str] = []
    record: list[dict] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("#") or s.startswith("---"):
            out_lines.append(line)
            continue
        m = _BULLET.match(line)
        prefix = m.group(1) if m else ""
        body = line[len(prefix):]
        kept: list[str] = []
        for sent in _SENT.split(body):
            pat = _first_pattern(sent)
            if pat and not _MARKER.search(sent):
                record.append({"sentence": sent.strip(), "pattern": pat, "action": "removed"})
            else:
                kept.append(sent)
        if kept:
            out_lines.append(prefix + " ".join(k.strip() for k in kept))
        # all sentences removed: drop the line (a paragraph or bullet never left empty)
    if not record:
        return text, []
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    if _words(clean) < MIN_WORDS_LEFT:
        return text, [dict(r, action="kept_short") for r in record]
    return clean, record
