"""Radical-transparency review-state gate — the single source of truth for
the verified / populated-pending-review / empty three-state display
standard (PR A, 2026-09) that governs every AI-drafted narrative field
across the Software directory and Communities, plus the FP&A Buddy
Matchmaker's unverified-content disclosure.

    | State     | Visitor                          | Admin                                    |
    |-----------|-----------------------------------|-------------------------------------------|
    | Verified  | content                           | content                                    |
    | Pending   | content + "under review"          | content + "unverified, visible to visitors"|
    | Empty     | placeholder                       | placeholder + a "go fill this in" prompt   |

Pure data only — no HTML, no FastAPI, no request objects. HTML rendering
(the actual `<span>`/`<div>` markup) lives webapp-side, in `webapp/app.py`,
built from the state/text this module returns. That split is deliberate and
enforced by import path rather than convention: MCP Phase 3's Toolbox/
Communities content tools return structured data, so they import only from
here, never from webapp/app.py — there is nothing importable from this
module that could leak a `<span class="tp-verify">` fragment into a tool
result by accident.

See ARCHITECTURE.md's "Review-state gate module" section for the full
write-up and CLAUDE.md's pointer note. Gate-Extraction PR B (2026-09)
extracted this from ~9 hand-assembled call sites in webapp/app.py and
linklib/matchmaker.py; behavior is unchanged from the post-PR-A baseline —
see that PR's own admin+visitor equivalence tests.
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import NamedTuple


class GateState(str, Enum):
    """One field's (or one whole profile's) radical-transparency review
    state, per the three-state table above."""
    VERIFIED = "verified"
    PENDING = "pending"
    EMPTY = "empty"


def state_for(unverified: bool) -> GateState:
    """The verified/pending half of the decision, for content a caller has
    ALREADY confirmed is non-blank (every current call site checks
    emptiness itself before deciding whether to show a badge at all — e.g.
    `if (tool.get("description") or "").strip():`). Use `field_state`
    below instead when presence hasn't been checked yet."""
    return GateState.PENDING if unverified else GateState.VERIFIED


def field_state(content: str | None, unverified: bool) -> GateState:
    """The full three-state decision for one field: EMPTY when there's no
    content at all, else PENDING/VERIFIED per `unverified`. `unverified` is
    a tool's own per-field `*_needs_verification` flag, or (for a
    Community) the single whole-profile `needs_review` flag reused across
    every field in that profile — the one permitted cross-entity
    divergence (N independent flags vs. one flag driving N badges), never
    a different code path."""
    if not (content or "").strip():
        return GateState.EMPTY
    return state_for(unverified)


# ---------------------------------------------------------------------------
# Badge copy — profile pages and compare matrices. PR A-approved, zero
# changes made in this extraction.
# ---------------------------------------------------------------------------

BADGE_TEXT_ADMIN = "unverified, visible to visitors"
BADGE_TEXT_VISITOR = "under review"

# The Software directory card's client-side JS badge (webapp/app.py's
# tools_directory route, rendered entirely in a <script> block since it's
# built from client-fetched JSON) has always used a capitalized variant of
# this same copy — a genuine, pre-existing inconsistency with the
# profile-page/compare-matrix badge above, confirmed still present as of
# PR A and NOT something this extraction is licensed to unify ("zero copy
# changes" per the Gate-Extraction PR B brief). Kept here, named
# separately, specifically so the JS literal is generated from a single
# Python-side constant instead of carrying its own independent hardcoded
# string — see webapp/app.py's tools_directory route for where this is
# interpolated into the <script> text.
DIRECTORY_JS_BADGE_TEXT_ADMIN = "Unverified, visible to visitors"
DIRECTORY_JS_BADGE_TEXT_VISITOR = "Under review"


def badge_text(state: GateState, authed: bool) -> str | None:
    """The badge word for a PENDING field, by viewer. None for
    VERIFIED (a populated field with no badge IS the verified signal) and
    for EMPTY (an empty field gets its own placeholder, never a badge)."""
    if state != GateState.PENDING:
        return None
    return BADGE_TEXT_ADMIN if authed else BADGE_TEXT_VISITOR


# ---------------------------------------------------------------------------
# Empty-state placeholder copy — the three-variant family PR A.1 approved
# verbatim (default "{Field} not available.", plus the two deliberate
# contextual variants: Description's "coming soon" and a Community profile
# group's "hasn't been researched" — the character-budget-limits-targets PR
# dropped the standing "yet" from both this and the compare-matrix labels
# below, since "yet" implies Brian will eventually fill it in and often he
# won't), one entry per field/section this extraction found gated.
# `admin_suffix` is only ever appended for an authed viewer — see
# webapp-side `empty_text`.
# ---------------------------------------------------------------------------

class EmptyCopy(NamedTuple):
    visitor_text: str
    admin_suffix: str = ""


EMPTY_COPY: dict[str, EmptyCopy] = {
    "tool_description": EmptyCopy(
        "Description coming soon.", "Add one from the edit page."),
    "tool_agent_taxonomy": EmptyCopy(
        "How autonomous this tool's AI is hasn't been documented.",
        "Generate a draft from the edit page."),
    "tool_differentiation": EmptyCopy(
        "Bottom line not available.",
        "This field is written by hand, not auto-drafted. Add one from the edit page."),
    "tool_competitors": EmptyCopy(
        "Competitors not available.", "Curate them from the edit page."),
    "community_bottom_line": EmptyCopy(
        "Bottom line not available.", "Generate a draft from the edit page."),
    "community_profile_group": EmptyCopy(
        "This section hasn't been researched.", "Generate a draft from the edit page."),
    "community_similar_communities": EmptyCopy(
        "Similar communities not available.", "Curate them from the edit page."),
    # One curated feature's category-level definition, rendered per row in
    # the Software profile's Key features card (feature-definitions PR). A
    # definition is admin-curated reference text with no verification
    # flag, so it only ever has two states: populated or empty.
    "feature_definition": EmptyCopy(
        "Definition not available.", "Add one from Software features."),
}

# Compare-matrix per-column empty-cell labels — a shorter, table-cell-scoped
# family, distinct from the profile-page placeholders above: no admin
# suffix, since a compare-matrix cell never carries a "go fill this in"
# prompt (that only ever appears on the field's own profile/edit page).
# Compare Redesign Phase 1 (2026-09) added the four new keys below —
# "community_profile_field" (the old flat-per-field key) is retired, not
# replaced: Compare now groups Community profile fields into the same 4
# themed cards the profile page uses (COMMUNITY_PROFILE_GROUPS, see
# linklib/compare.py), so an empty CELL is either a whole empty GROUP
# ("community_profile_group", same key EMPTY_COPY already uses for this)
# or, inside a populated group, the profile page's own Tier-2 "No details
# available." literal — rendered directly by the HTML layer, not looked up
# here, since it's a fixed string with no admin-suffix variant at all.
COMPARE_EMPTY_LABELS: dict[str, str] = {
    "tool_agent_taxonomy": "Not documented.",
    "tool_description": "Not available.",
    "tool_differentiation": "Not available.",
    "tool_competitors": "Not curated.",
    "community_bottom_line": "Not available.",
    "community_profile_group": "Not documented.",
    "community_similar_communities": "Not curated.",
}


# ---------------------------------------------------------------------------
# Row-existence — shared by both compare matrices' row-level "does ANY
# compared entity have this field at all" check. Deliberately kept separate
# from each cell's own verified/pending/empty content gate (Phase 0
# inventory item #10): a row is omitted entirely from the table only when
# NO compared entity has any content for it; once a row exists, an
# individual entity with no content for it still gets its own empty cell,
# not a missing row.
# ---------------------------------------------------------------------------

def any_populated(values: Iterable[str | None]) -> bool:
    """True if at least one value is non-blank text."""
    return any((v or "").strip() for v in values)


# ---------------------------------------------------------------------------
# Matchmaker (linklib/matchmaker.py) — single-sourced so this copy can't
# drift from the profile-page/compare-matrix badge language above.
# ---------------------------------------------------------------------------

# Per-field inline marker (Software: 3 independent flags, one marker per
# field that's actually unverified).
MATCHMAKER_FIELD_SUFFIX = " (unverified)"

# Per-community leading note (Communities: one whole-profile flag, one note
# for the whole block rather than marking all nine profile lines).
MATCHMAKER_COMMUNITY_NOTE = (
    "Note: this community's profile is unverified; treat the following details as provisional.\n"
)

# Standing system-prompt disclaimer, appended only when at least one marker
# above is actually present in that turn's directory context.
MATCHMAKER_DISCLAIMER = (
    "\n\nSome catalog details above are marked unverified. Treat them as "
    "provisional, and say so if you reference them in your answer."
)
