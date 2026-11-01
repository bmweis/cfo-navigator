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
    ("forecast or advice", re.compile(
        r"\b(?:expect|you should|would likely|will likely|likely to|confirm whether)\b"
        # "will sit/be/run/see", with up to two adverbs between ("will almost certainly sit", "will typically be").
        r"|\bwill(?:\s+(?:\w+ly|almost|just|still|also|generally)){0,2}\s+(?:sit|be|run|see)\b", re.I)),
    ("characterization", re.compile(
        r"\b(?:striking|dramatic|dramatically|remarkable|notably|is the floor for|attractive to investors)\b", re.I)),
)
# A flagged uncited sentence that carries a figure and a "because of" / "due to": a cause stated
# with numbers and no source. Still removed (or cut back to its figure clause), recorded under this
# name so the admin report shows how often it happens.
UNCITED_CLAIM = "uncited claim with figures"
_CAUSE_WITH_FIGURE = re.compile(r"\b(?:because of|due to)\b", re.I)
_DIGIT = re.compile(r"\d")
# Where a sentence can be cut back to an earlier clause: an em dash, a semicolon or a comma.
_SEP = re.compile(r"—|;|,(?=\s)")

_MARKER = re.compile(r"\[\d{1,2}\]")
_SENT = re.compile(r"(?<=[.!?])(?:\[\d{1,2}\])*\s+(?=[A-Z\"'(\[$0-9])")
_BULLET = re.compile(r"^(\s*(?:[-*]|\d+[.)])\s+)")


def _first_pattern(sentence: str) -> str:
    for name, rx in PATTERNS:
        if rx.search(sentence):
            return name
    return ""


def _first_hit(sentence: str) -> int:
    """Start of the earliest pattern match in the sentence, or -1."""
    starts = [m.start() for _, rx in PATTERNS if (m := rx.search(sentence))]
    return min(starts) if starts else -1


def split_clause(sentence: str) -> tuple[str, str] | None:
    """(kept, removed_clause) when the flagged words sit in a later clause and an earlier clause
    carries a figure: the sentence is cut back to that clause and closed with a period. None when
    there is no such clause (the whole sentence then goes, as before). The removed clause runs from
    the separator before the flagged words to the end of the sentence."""
    pos = _first_hit(sentence)
    if pos < 0:
        return None
    seps = [m for m in _SEP.finditer(sentence) if m.start() < pos]
    if not seps:
        return None
    cut = seps[-1].start()
    prefix = sentence[:cut].rstrip()
    clause = sentence[cut:].lstrip("—;, ").strip()
    if (not clause or not _DIGIT.search(prefix) or len(prefix.split()) < 4 or _first_pattern(prefix)
            or _MARKER.search(prefix)):
        return None
    return (prefix if prefix[-1] in ".!?" else prefix + "."), clause


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def strip_answer(text: str) -> tuple[str, list[dict]]:
    """(clean text, record). Each record item: {sentence, pattern, action}, action
    "removed", "clause_removed" (only the flagged clause went; the figure clause before it
    stays, also in `kept`) or "kept_short" (answer too short to strip; text returned unchanged)."""
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
                if pat == "causal" and _DIGIT.search(sent) and _CAUSE_WITH_FIGURE.search(sent):
                    pat = UNCITED_CLAIM
                cut = split_clause(sent)
                if cut:
                    record.append({"sentence": cut[1], "pattern": pat, "action": "clause_removed", "kept": cut[0]})
                    kept.append(cut[0])
                else:
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
