"""Automated voice check — the deterministic half of the voice QA (see BRAND.md §9).

Brian's voice has mechanical rules (banned buzzwords, filler, performative phrases) that
can be checked deterministically, and holistic tone that can't. This test enforces the
mechanical rules over the site's authored/prompt copy in ``VOICE_SCANNED_FILES``.

Scanning that source is correct: the banned-word rulebook lives natively in
``linklib/voice_review.py`` and is only pulled in at runtime, so the scanned files
contain the site's own prose, not the rulebook. Holistic tone is handled on demand by
``linklib.voice_review.review_text`` (Claude), not here.

**`BANNED_WORDS`/`FILLER_PHRASES`/`PERFORMATIVE` stay in source, permanently** —
decided and closed (2026-09 voice-enforcement PR), not to be re-litigated: moving them
to the DB is what would let ``voice_review.py`` contradict ``/admin/voice``, since a
committed CI mirror can drift from a live settings row and nothing in the running
container can close that gap automatically. `/admin/voice` mirrors the three lists
read-only for visibility, marked "changing them is a code change" — see that page's own
section. Keeping one copy means there's nothing to diverge.

**``_hits()`` retired (2026-09) — the sweep now runs `mechanical_findings()` itself,
not a second, parallel reimplementation.** Before this, the "no banned words in
webapp/app.py" tests below drove a hand-rolled ``_hits()`` regex scanner, separate from
``mechanical_findings()`` (which only the live ``/admin/checks`` dashboard actually
called) — meaning the test, the dashboard, and any future caller could in principle
disagree about what counts as a violation, and nothing would catch it. Concrete
before/after:

* **Scope widened, 1 file → 3.** ``_hits()`` only ever scanned ``webapp/app.py``.
  ``mechanical_findings()`` now sweeps the same ``VOICE_SCANNED_FILES`` list
  ``typography_findings()`` already used (``webapp/app.py``, ``linklib/enrich.py``,
  ``linklib/feature_scan.py``) — a banned word in the latter two was invisible to CI
  before this change.
* **One real false positive found and fixed by the new mask, not by the old scanner.**
  ``enrich.py``'s own "No marketing language: no 'powerful,' 'seamless,' ..." rule text
  cites ``BANNED_WORDS`` members as examples of what NOT to write. ``_hits()`` never saw
  it (wrong file); a naive file-list-only widening would have flagged it as a violation
  of the very rule it's stating. ``voice_review._mask_rubric_enumerations`` masks the
  enumeration itself (not the whole literal), so a real violation elsewhere in the same
  string still gets caught — see the mask-specific tests below.
* **Matching logic is unchanged for genuine hits.** Both ``_hits()`` and
  ``mechanical_findings()`` use the identical ``\\bword\\b`` regex for ``BANNED_WORDS``
  and identical substring containment for ``FILLER_PHRASES``/``PERFORMATIVE`` — a word
  that was a real violation under the old scanner is still one under the new.

The second half of this file covers the two TYPOGRAPHIC rules added in PR 9
(2026-09) — bare ampersands and spaced em dashes. Those take Python source
rather than plain text, so they get their own ``typography_findings`` entry
point; see that function's docstring for why the scope is string literals with
embedded code comments stripped, and never database content.

The final section covers the semantic-contradiction check added in the same
2026-09 PR: does ``VOICE_CORE_DEFAULT``'s own prose name a word or phrase, in
quotes, that the mechanical lists don't actually enforce.
"""
import pathlib

import pytest

from linklib.agent import VOICE_CORE_DEFAULT
from linklib.voice_review import (
    AMPERSAND_ACRONYMS,
    AMPERSAND_NAMES,
    mechanical_findings,
    typography_findings,
    voice_core_gap_problems,
)
from webapp.checks import VOICE_SCANNED_FILES

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "webapp" / "app.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", VOICE_SCANNED_FILES, ids=lambda p: p.name)
def test_every_scanned_file_has_no_mechanical_violations(path):
    src = path.read_text(encoding="utf-8")
    findings = mechanical_findings(src)
    detail = "\n".join(f"  {rule}: “{phrase}”" for rule, phrase in findings)
    assert not findings, (
        f"{len(findings)} mechanical voice violation(s) in {path}:\n{detail}\n"
        "Banned buzzwords, filler, and performative phrases aren't allowed in the scanned "
        "source's UI/prompt copy — see linklib/voice_review.py's BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE."
    )


# --- Detector self-tests: prove mechanical_findings still fires, so the -----
# --- sweep above isn't vacuously passing because nothing can ever fail it --
def test_mechanical_findings_still_flags_a_banned_word():
    assert mechanical_findings("This is a robust integration.") == [("buzzword", "robust")]


def test_mechanical_findings_still_flags_a_filler_phrase():
    assert mechanical_findings("At the end of the day, it works.") == [("filler", "at the end of the day")]


def test_mechanical_findings_still_flags_a_performative_phrase():
    assert mechanical_findings("I'm excited to share this update.") == [("performative", "i'm excited to share")]


# --- Invisible/zero-width Unicode characters (2026-09 follow-up) -----------
# Deferred from the voice-review-queue PR, described there as "cheap and
# deterministic" — a real live example (category_features id 104's own
# `definition`, ending with a zero-width space) is what motivated it: em
# dashes, bare ampersands, and every BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE
# entry are all blind to a character with no visible glyph at all.
def test_mechanical_findings_flags_a_zero_width_space():
    """Reproduces the real production shape (category_features id 104) —
    a definition that reads as perfectly clean copy, with an invisible
    zero-width space pasted onto the end."""
    text = "Patterns that don't look right—not necessarily a balance change​"
    assert mechanical_findings(text) == [("invisible-character", "U+200B (zero-width space)")]


@pytest.mark.parametrize("ch, label", [
    ("​", "zero-width space"),
    ("‌", "zero-width non-joiner"),
    ("‍", "zero-width joiner"),
    ("﻿", "zero-width no-break space (BOM)"),
    ("⁠", "word joiner"),
    ("‎", "left-to-right mark"),
    ("‏", "right-to-left mark"),
    ("­", "soft hyphen"),
])
def test_mechanical_findings_flags_every_registered_invisible_character(ch, label):
    findings = mechanical_findings(f"Clean copy with a hidden character{ch} in it.")
    assert findings == [("invisible-character", f"U+{ord(ch):04X} ({label})")]


def test_mechanical_findings_ignores_ordinary_whitespace():
    """A real space, tab, or newline is not paste garbage — only a
    character with no visible glyph at all is in scope."""
    assert mechanical_findings("Ordinary copy\twith a tab\nand a newline.") == []


def test_mechanical_findings_ignores_clean_copy_with_no_invisible_characters():
    assert mechanical_findings("This is perfectly ordinary, on-voice copy.") == []


# --- The rubric-enumeration mask: a rubric citing its own banned words as --
# --- "don't write this" examples is not itself a violation -----------------
def test_mask_suppresses_the_real_enrich_py_false_positive():
    """The exact shape found in linklib/enrich.py: quoted BANNED_WORDS
    members cited after "No marketing language:", which used to trip
    mechanical_findings before the mask existed."""
    text = ('3. No marketing language: no "powerful," "seamless," "game-changing," '
            '"best-in-class," or similar adjective stacking. No exclamation points.')
    assert mechanical_findings(text) == []


def test_mask_suppresses_the_avoid_colon_shape():
    """The same shape recurs in linklib.agent.VOICE_CORE_DEFAULT's own
    "- Avoid: ... delve, robust, seamless, ..." line — not a scanned file
    today, but the mask is general, not a line-number exclusion, so this
    shape is covered too."""
    text = "- Avoid: genuinely, honestly, delve, robust, seamless, synergy, transformative, game-changer."
    assert mechanical_findings(text) == []


def test_mask_does_not_hide_a_real_violation_elsewhere_in_the_same_string():
    """The mask is scoped to the enumeration itself (marker through the next
    period), never the whole literal — a genuine violation before or after
    the masked span still has to be caught."""
    text = ('This description is robust. No marketing language: no "powerful," '
            '"seamless," or similar. It is also synergy-driven.')
    findings = mechanical_findings(text)
    assert ("buzzword", "robust") in findings
    assert ("buzzword", "synergy") in findings
    assert ("buzzword", "powerful") not in findings   # "powerful" isn't a banned word anyway
    assert ("buzzword", "seamless") not in findings   # masked — inside the enumeration


def test_mask_spans_a_line_wrapped_enumeration():
    """enrich.py's own rule text line-wraps the enumeration across two
    physical lines inside one triple-quoted string — `[^.]` already matches
    a newline (DOTALL only affects a bare `.`), so this needs no special
    handling, but it's worth a direct test."""
    text = '3. No marketing language: no "powerful," "seamless,"\n   "best-in-class," or similar. No exclamation points.'
    assert mechanical_findings(text) == []


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
# own comment on VOICE_SCANNED_FILES for the full reasoning.

@pytest.mark.parametrize("path", VOICE_SCANNED_FILES, ids=lambda p: p.name)
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


# --- Semantic contradiction: does VOICE_CORE_DEFAULT's own prose name -------
# --- something the mechanical lists don't actually enforce? ----------------
# One direction only: a list entry the prose doesn't mention is fine.

def test_voice_core_default_has_no_gap():
    """The real rubric, checked as-is — proves the check is clean on day one,
    not just that it CAN find something. (It wasn't, before this PR: the
    rubric's own "don't pad the gap with generic hedging ('there are many
    factors to consider')" example wasn't in FILLER_PHRASES — found by this
    exact check, fixed by adding the phrase, not by editing the rubric.)"""
    assert voice_core_gap_problems(VOICE_CORE_DEFAULT) == []


def test_voice_core_gap_detects_a_real_gap():
    # Deliberately not "game changer" — that phrase is already a BANNED_WORDS
    # entry, so it wouldn't demonstrate a gap at all (it would demonstrate
    # coverage). This phrase is genuinely absent from every list.
    synthetic = 'Never write "knock it out of the park" in copy.'
    problems = voice_core_gap_problems(synthetic)
    assert len(problems) == 1
    assert "knock it out of the park" in problems[0]


def test_voice_core_gap_ignores_single_word_quotes():
    """Checked against the real rubric before shipping: a blind quoted-span
    scan over VOICE_CORE_DEFAULT flags `"&"`, `"and"`, and `"to"` — single-
    word/character asides quoted for an unrelated reason (the
    ampersand-spelling rule, an arrow-notation replacement), not "avoid
    this phrase" examples. The 2+-word floor removes exactly these three
    false positives and none of the real signal (see
    test_voice_core_default_has_no_gap)."""
    assert voice_core_gap_problems('Spell out "and"; never use "&" as a stand-in.') == []


def test_voice_core_gap_recognizes_an_already_covered_phrase():
    """A phrase the rubric quotes that IS already in FILLER_PHRASES (exact
    match) is not flagged."""
    assert voice_core_gap_problems('Don\'t write "at the end of the day" in copy.') == []


def test_voice_core_gap_recognizes_coverage_via_substring_containment():
    """FILLER_PHRASES has "it's worth noting" (no trailing "that"); the real
    rubric quotes "it's worth noting that" — reusing mechanical_findings as
    the coverage oracle means substring containment covers this
    automatically, without the two strings needing to match exactly."""
    assert voice_core_gap_problems('Cut "it\'s worth noting that" from copy.') == []


def test_voice_core_gap_one_direction_only():
    """A BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE entry the prose never
    mentions at all is fine — the lists are allowed to be more specific
    than the rubric. Nothing in this text is quoted, so there's nothing to
    check, and that's not itself a problem."""
    assert voice_core_gap_problems("Write directly. Be specific. Stop.") == []
