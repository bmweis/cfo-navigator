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
_SPACED_EM_DASH = re.compile(_SPACE_CLASS + r"{1,80}" + _EM_DASH + _SPACE_CLASS + r"{1,80}")
_SPACED_DOUBLE_HYPHEN_DASH = re.compile(
    r"(?<=\w)" + _SPACE_CLASS + r"{1,80}--" + _SPACE_CLASS + r"{1,80}(?=\w)"
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


def normalize_voice_mechanics(text: str) -> str:
    """The single entry point every Library write method calls on a
    prose-capable field before it's persisted. Currently applies
    `fix_spaced_em_dashes` only; extend this function (not each call site)
    if a future HARD MECHANICAL RULES violation also turns out to need a
    deterministic backstop, so every existing call site picks up the fix
    for free."""
    return fix_spaced_em_dashes(text)
