"""Deterministic, mechanical backstop for `voice_core`'s "HARD MECHANICAL
RULES" -- closes the Phase 0 investigation's F3 gap ("nothing in the current
pipeline can catch or repair a voice/structure violation after the fact").

Root cause of the 2026-08 spaced-em-dash incident (63 rows / 52 tools,
mostly `agent_taxonomy_note`): `voice_core`'s em-dash rule ("no surrounding
spaces... never violate") IS correctly present and interpolated into every
one of the four AI-drafted-field generation prompts
(`generate_tool_description`, `generate_tool_agent_taxonomy`,
`generate_tool_differentiation`, `generate_community_profile` -- all four
call `_resolve_voice_core()` and interpolate `{voice_core}`, confirmed by
direct code read, not assumed). The rule was never missing from any prompt
path. The violations happened anyway because a natural-language "never" is
an instruction, not an invariant -- an LLM's per-token compliance with a
prose rule is probabilistic, not guaranteed, however emphatically the rule
is worded. Prompt wording can reduce the violation rate; it cannot make it
zero. So "zero spaced em dashes, permanently" is only actually achievable
with a deterministic, code-level correction applied after generation and
before persistence -- this module is that correction.

This is intentionally NOT a text-generation fix and NOT specific to any one
field or entity type. It's called from `Library`'s own write methods (the
one place every save path -- the bulk regen script, an admin Generate-then-save
AJAX route, a hand-edit save, a future script -- is forced to funnel through),
so it applies regardless of what produced the text or what will produce text
in the future.
"""

from __future__ import annotations

import re

# A "spaced em dash": a real em dash (U+2014) with at least one space
# (regular ASCII space or a non-breaking space, U+00A0) on both sides.
# Collapses to an unspaced em dash, matching voice_core's own rule
# ("Emdashes have NO surrounding spaces"). Also catches the same shape
# typed with an ASCII double-hyphen ("--") as a stand-in for an em dash,
# converting it to a real em dash in the same pass -- a model or a
# hand-edit reaching for "--" as a dash is making the identical mechanical
# mistake this rule exists to catch, just with a different character.
_EM_DASH = "—"
_SPACE_CLASS = "[  ]"
# Bounded quantifier ({1,N}, not unbounded +): a plain `+` here is
# quadratic on a long non-matching whitespace/nbsp run — finditer retries
# the match at every position in the run, and each attempt walks the full
# remaining run before failing. A possessive quantifier (`++`) only
# partially helps (it still re-attempts at every position, just without
# intra-attempt backtracking); bounding the repeat count actually caps the
# per-attempt work. See linklib/voice_review.py's `_SPACED_MDASH_ENTITY`
# comment for the measured ~2.1s (unbounded) -> ~1.5s (possessive) ->
# ~260ms (bounded) progression on the identical pattern shape. This module
# runs on every Library write (`Library._vf`), so the same hardening
# applies here even though no single production text field has hit the
# pathological case yet.
# One-sided-spacing gap, found and fixed (2026-09): the original pattern
# above required a space on BOTH sides (`SPACE{1,80} DASH SPACE{1,80}`), so
# "pass— auto-corrected" (space only after) or "pass —auto" (space only
# before) never matched at all — confirmed live against this exact review
# queue page's own intro copy, which had this precise one-sided shape.
# Fixed with an alternation: either side alone having 1-80 spaces is
# sufficient, with the other side allowed 0-80 — every quantifier stays
# individually bounded ({1,80}/{0,80}, never unbounded `+`), so this keeps
# the same per-attempt complexity ceiling the bounding comment above
# documents, just tried twice instead of once at each position.
_SPACED_EM_DASH = re.compile(
    r"(?:" + _SPACE_CLASS + r"{1,80}" + _EM_DASH + _SPACE_CLASS + r"{0,80})"
    r"|"
    r"(?:" + _SPACE_CLASS + r"{0,80}" + _EM_DASH + _SPACE_CLASS + r"{1,80})"
)
_SPACED_DOUBLE_HYPHEN_DASH = re.compile(
    r"(?<=\w)" + _SPACE_CLASS + r"{1,80}--" + _SPACE_CLASS + r"{1,80}(?=\w)"
)

# Invisible/zero-width Unicode characters safe to silently strip at write
# time — a curated SUBSET of `linklib.voice_review.INVISIBLE_CHARS` (kept as
# a separate, smaller literal here rather than importing it: voice_review
# already imports FROM this module, so importing back would be circular).
# Per-character verdict (see CLAUDE.md's Part 5 writeup for the full
# reasoning): these three never carry meaning in a plain-text field, so
# stripping them can never lose information a reader could see —
# zero-width space and the zero-width no-break space/BOM are pure paste
# garbage with no legitimate use inside a saved field value, and a word
# joiner conveys no information in plain text either (its one real job,
# suppressing a line break at a specific point, isn't something this
# codebase's rendering ever depends on). Everything else in voice_review's
# INVISIBLE_CHARS stays flag-only, on purpose: the zero-width joiner is
# load-bearing inside real emoji sequences; the zero-width non-joiner and
# both bidi marks (LTR/RTL) can be genuinely load-bearing for correct
# rendering of non-Latin/mixed-direction text; and a soft hyphen is a real,
# if rare, intentional hyphenation hint a word processor can produce on
# purpose. None of those four is safe to silently delete.
_AUTO_STRIP_INVISIBLE_CHARS: tuple[str, ...] = (
    "​",  # zero-width space
    "﻿",  # zero-width no-break space (BOM)
    "⁠",  # word joiner
)


def fix_spaced_em_dashes(text: str) -> str:
    """Collapse every spaced em dash (or spaced '--' used as a dash) in
    `text` to an unspaced real em dash. Idempotent, safe on empty/None-ish
    input, and a no-op on text that already follows the rule."""
    if not text:
        return text
    text = _SPACED_EM_DASH.sub(_EM_DASH, text)
    text = _SPACED_DOUBLE_HYPHEN_DASH.sub(_EM_DASH, text)
    return text


def strip_safe_invisible_chars(text: str) -> str:
    """Remove every character in `_AUTO_STRIP_INVISIBLE_CHARS` from `text`.
    Idempotent, safe on empty/None-ish input, a no-op when none are
    present."""
    if not text:
        return text
    for ch in _AUTO_STRIP_INVISIBLE_CHARS:
        if ch in text:
            text = text.replace(ch, "")
    return text


def correction_rule_for(before: str) -> str:
    """Which `voice_review_queue.rule` an auto-correction of `before`
    should be logged under. `normalize_voice_mechanics` can fix more than
    one kind of violation in a single call; this inspects the PRE-fix text
    to say which one actually fired, so `Library._vf`'s logged row names
    the real rule instead of always assuming spaced-em-dash. Checked in a
    fixed order (invisible-character first) since the two are independent
    signals — a real production row could in principle carry both, and
    which one gets reported first doesn't change what got fixed."""
    if any(ch in before for ch in _AUTO_STRIP_INVISIBLE_CHARS):
        return "invisible-character"
    return "spaced-em-dash"


def normalize_voice_mechanics(text: str) -> str:
    """The single entry point every Library write method calls on a
    prose-capable field before it's persisted. Applies
    `fix_spaced_em_dashes` and `strip_safe_invisible_chars`; extend this
    function (not each call site) if a future HARD MECHANICAL RULES
    violation also turns out to need a deterministic backstop, so every
    existing call site picks up the fix for free."""
    text = fix_spaced_em_dashes(text)
    text = strip_safe_invisible_chars(text)
    return text


def norm_for_compare(text) -> str:
    """Canonical form for asking "did this field's text actually change?"
    between a stored value and a just-submitted one. None and "" are the
    same empty string; CRLF (what a browser submits for a textarea) and LF
    (what may be stored) are the same; any whitespace-only difference
    (leading/trailing, runs, line/paragraph breaks) is ignored; and
    `normalize_voice_mechanics` is applied to both sides, since every
    Library write path runs it before storing (so a raw submitted value
    with a spaced em dash would otherwise always differ from what it was
    stored as). Used to decide whether a save invalidates a field's
    entity_citations."""
    if text is None:
        return ""
    return " ".join(normalize_voice_mechanics(str(text)).split())
