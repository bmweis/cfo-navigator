"""Automated voice check — the deterministic half of the voice QA (see BRAND.md §9).

Brian's voice has mechanical rules (banned buzzwords, filler, performative phrases) that
can be checked deterministically, and holistic tone that can't. This test enforces the
mechanical rules over the site's authored copy in ``webapp/app.py``.

Scanning that file's source is correct: the banned-word rulebook lives natively in
``linklib/voice_review.py`` and is only pulled in at runtime, so the source of
``app.py`` contains the site's own prose, not the rulebook. Holistic tone is
handled on demand by ``linklib.voice_review.review_text`` (Claude), not here.
"""
import pathlib
import re

import pytest

from linklib.voice_review import BANNED_WORDS, FILLER_PHRASES, PERFORMATIVE

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "webapp" / "app.py").read_text(encoding="utf-8")


def _hits(needle: str, *, word: bool) -> list[int]:
    """Line numbers where `needle` appears (whole-word or substring), case-insensitive."""
    pat = (r"\b" + re.escape(needle) + r"\b") if word else re.escape(needle)
    rx = re.compile(pat, re.IGNORECASE)
    return [i for i, line in enumerate(APP_SRC.splitlines(), 1) if rx.search(line)]


@pytest.mark.parametrize("word", BANNED_WORDS)
def test_no_banned_buzzwords(word):
    lines = _hits(word, word=True)
    assert not lines, f"Banned buzzword {word!r} in webapp/app.py at line(s) {lines} — reword in Brian's voice."


@pytest.mark.parametrize("phrase", FILLER_PHRASES)
def test_no_filler_phrases(phrase):
    lines = _hits(phrase, word=False)
    assert not lines, f"Filler phrase {phrase!r} in webapp/app.py at line(s) {lines} — cut it."


@pytest.mark.parametrize("phrase", PERFORMATIVE)
def test_no_performative_phrases(phrase):
    lines = _hits(phrase, word=False)
    assert not lines, f"Performative phrase {phrase!r} in webapp/app.py at line(s) {lines} — drop the cheerleading."
