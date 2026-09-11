"""Automated voice check — the deterministic half of the voice QA (see BRAND.md §9).

Brian's voice has mechanical rules (banned buzzwords, filler, performative phrases) that
can be checked deterministically, and holistic tone that can't. This test enforces the
mechanical rules over the site's authored copy in ``webapp/app.py``.

Scanning that file's source is correct: the banned-word rulebook lives natively in
``linklib/voice_review.py`` and is only pulled in at runtime, so the source of
``app.py`` contains the site's own prose, not the rulebook. Holistic tone is
handled on demand by ``linklib.voice_review.review_text`` (Claude), not here.

The second half of this file covers the two TYPOGRAPHIC rules added in PR 9
(2026-09) — bare ampersands and spaced em dashes. Those take Python source
rather than plain text, so they get their own ``typography_findings`` entry
point; see that function's docstring for why the scope is string literals with
embedded code comments stripped, and never database content.
"""
import pathlib
import re

import pytest

from linklib.voice_review import (
    AMPERSAND_ACRONYMS,
    AMPERSAND_NAMES,
    BANNED_WORDS,
    FILLER_PHRASES,
    PERFORMATIVE,
    typography_findings,
)
from webapp.checks import TYPOGRAPHY_SCANNED_FILES

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


# --- Typography: bare ampersands and spaced em dashes (PR 9, 2026-09) -------
# The tests below are deliberately two-sided. Asserting only that the live
# source passes would be satisfied by a lint that finds nothing ever, so each
# rule also has a matching test proving it FAILS on a real violation of exactly
# the shape it exists to catch, and PASSES on the legitimate near-miss beside
# it (FP&A, an unspaced em dash).


def test_site_copy_has_no_bare_ampersands_or_spaced_em_dashes():
    findings = typography_findings(APP_SRC)
    detail = "\n".join(f"  {rule} (line {line}): {excerpt}" for rule, line, excerpt in findings)
    assert not findings, (
        f"{len(findings)} typographic violation(s) in webapp/app.py's UI copy:\n{detail}\n"
        "Spell out 'and' (FP&A and friends are allowlisted), and never space an em dash."
    )


def test_lint_fails_on_a_bare_ampersand():
    findings = typography_findings('LABEL = "Tag cleanup &amp; style"')
    assert [r for r, _, _ in findings] == ["bare-ampersand"]


def test_lint_fails_on_a_bare_ampersand_written_without_the_entity():
    findings = typography_findings('LABEL = "Users & auth"')
    assert [r for r, _, _ in findings] == ["bare-ampersand"]


def test_lint_fails_on_a_spaced_em_dash():
    findings = typography_findings('COPY = "Reader content backfill — resumable and stoppable."')
    assert [r for r, _, _ in findings] == ["spaced-em-dash"]


def test_lint_fails_on_a_spaced_em_dash_written_as_an_html_entity():
    findings = typography_findings('COPY = "Reader content backfill &mdash; resumable."')
    assert [r for r, _, _ in findings] == ["spaced-em-dash"]


def test_lint_passes_an_unspaced_em_dash():
    assert typography_findings('COPY = "Resumable—and stoppable."') == []
    assert typography_findings('COPY = "Resumable&mdash;and stoppable."') == []


@pytest.mark.parametrize("term", AMPERSAND_ACRONYMS)
def test_lint_passes_every_allowlisted_acronym(term):
    entity = term.replace("&", "&amp;")
    assert typography_findings(f'COPY = "Ask a real {term} question."') == []
    assert typography_findings(f'COPY = "Ask a real {entity} question."') == []


@pytest.mark.parametrize("term", AMPERSAND_NAMES)
def test_lint_passes_every_allowlisted_name(term):
    entity = term.replace("&", "&amp;")
    assert typography_findings(f'COPY = "e.g. {term} at Series B+ SaaS companies."') == []
    assert typography_findings(f'COPY = "e.g. {entity} at Series B+ SaaS companies."') == []


def test_lint_passes_an_allowlisted_name_that_line_wraps_mid_term():
    """A triple-quoted prompt string can line-wrap between the term's own
    words (a real PR 15 case: "Flux Analysis\\n  & Summaries" in
    linklib/feature_scan.py's few-shot excerpt) with no change in rendered
    meaning — the allowlist match has to tolerate that, not just a single
    literal space between words."""
    src = 'COPY = """Worked example: Automated Flux Analysis\n  & Summaries is one feature."""'
    assert typography_findings(src) == []


# --- PR 15: enrich.py and feature_scan.py joined the scanned-file list ------
# These are LLM prompt-assembly modules, not rendered HTML — but the model
# reads and imitates this text, so a spaced em dash inside a prompt
# demonstrates the exact thing the prompt forbids. See webapp/checks.py's
# own comment on TYPOGRAPHY_SCANNED_FILES for the full reasoning.

@pytest.mark.parametrize("path", TYPOGRAPHY_SCANNED_FILES, ids=lambda p: p.name)
def test_every_scanned_file_has_no_typography_violations(path):
    src = path.read_text(encoding="utf-8")
    findings = typography_findings(src)
    detail = "\n".join(f"  {rule} (line {line}): {excerpt}" for rule, line, excerpt in findings)
    assert not findings, (
        f"{len(findings)} typographic violation(s) in {path}:\n{detail}\n"
        "Spell out 'and' (FP&A and friends are allowlisted), and never space an em dash."
    )


def test_lint_ignores_comments_embedded_in_a_string_literal():
    """CSS/JS/HTML comments inside an inline <style>/<script> block are code,
    not copy — webapp/app.py's :root token table alone carries hundreds of
    ` — ` spans in CSS comments."""
    assert typography_findings('CSS = """/* soft navy fill — chip & ghost hovers */"""') == []
    assert typography_findings('JS = """\n// matches no route at all — a plain 404\n"""') == []


def test_lint_ignores_an_escape_map_value():
    """`_esc()`'s own `.replace("&", "&amp;")` is code. At the AST level that
    literal really is just the four characters `&amp;`, with no context."""
    assert typography_findings('def _esc(s): return s.replace("&", "&amp;")') == []


def test_lint_ignores_a_url_query_separator():
    """`?a=1&amp;b=2` separates parameters; it isn't the word "and"."""
    assert typography_findings('LINK = "<a href=\\"/admin/x?filter=all&amp;page=2\\">Next</a>"') == []
    assert typography_findings('LINK = "/tools?cat=erp&amp;sort=name"') == []


def test_lint_ignores_javascript_and_operators():
    assert typography_findings("JS = \"if (a && b) { go(); }\"") == []
    assert typography_findings('ATTR = "onclick=\\"a &amp;&amp; b\\""') == []


def test_lint_ignores_docstrings():
    """A module or function docstring is commentary about the code, not copy
    a reader ever sees."""
    src = 'def f():\n    """Notes on Sales & Marketing — see above."""\n    return 1\n'
    assert typography_findings(src) == []
