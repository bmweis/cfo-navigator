"""Shared constants and helpers for the Community profile (PR 2a, 2026-09).

One place for three facts that the DB layer, the generator, the admin form and
the tests all need to agree on, so none of them keeps its own copy:

  * which profile columns are RETIRED (frozen in the schema, never rendered,
    collected, generated or read again),
  * the target/max character limits for every live prose field, and
  * the CPE eligible vocabulary and how a stored value is coerced to it.

Pure functions and constants only; no I/O, no imports from the rest of linklib,
so anything can import it without a cycle.
"""
from __future__ import annotations

import re

# Retired in PR 2a. The first five are on Brian's list; the last three were
# added by the no-redundancy rule (primary_purpose overlaps value_prop,
# meeting_format restates format_reality and the format enum, stage_focus
# restates ideal_member). Frozen, not dropped: the column stays in the schema
# with whatever value each row last had, and `Library.upsert_community_profile`
# never writes it unless a caller passes it explicitly.
RETIRED_PROFILE_FIELDS = (
    "founded_year", "event_style", "seniority_band", "platform_type",
    "team_or_individual", "primary_purpose", "meeting_format", "stage_focus",
)

# Retired columns on the `communities` table: demographic (covered by
# ideal_member), cost_note (covered by cost_value_verdict), notes (the "Short
# description", covered by the Bottom line). Same frozen-not-dropped rule.
RETIRED_COMMUNITY_FIELDS = ("demographic", "cost_note", "notes")

# (soft target, hard max) in characters, per live prose field. The target only
# turns the live counter amber; the max is enforced server-side and a save over
# it is refused whole, never shortened. Every max is above the longest value
# stored in production when this table was written (read 2026-09-29 from all
# 40 community_profiles rows: ideal_member 1053, anti_fit 724, value_prop 1017,
# format_reality 775, engagement_level 820, sponsor_relationship_note 804,
# application_friction 733, cost_value_verdict 748, notable_members 925,
# public_criticism 670, verdict_summary 214, business_model 790,
# resources_included 131, jobs_program 266). tests/test_community_profile_fields.py
# pins that ceiling so a future edit can't quietly land a max below stored text.
PROFILE_LIMITS: dict[str, tuple[int, int]] = {
    "ideal_member": (900, 1500),
    "anti_fit": (600, 1000),
    "value_prop": (800, 1500),
    "format_reality": (700, 1200),
    "engagement_level": (700, 1200),
    "application_friction": (600, 1200),
    "business_model": (700, 1300),
    "sponsor_relationship_note": (700, 1300),
    "cost_value_verdict": (700, 1300),
    "notable_members": (700, 1200),
    "public_criticism": (600, 1000),
    "verdict_summary": (250, 400),
    "resources_included": (300, 600),
    "jobs_program": (300, 800),
}

# Longest value stored in production per limited field (same read as above).
PRODUCTION_LONGEST: dict[str, int] = {
    "ideal_member": 1053, "anti_fit": 724, "value_prop": 1017, "format_reality": 775,
    "engagement_level": 820, "sponsor_relationship_note": 804, "application_friction": 733,
    "cost_value_verdict": 748, "notable_members": 925, "public_criticism": 670,
    "verdict_summary": 214, "business_model": 790, "resources_included": 131,
    "jobs_program": 266,
}

CPE_OPTIONS = ("Yes", "No", "Unclear")
_CPE_FULL = re.compile(r"^(Yes|No|Unclear)( \(.{1,120}\))?$")
_CPE_LEAD = re.compile(r"^\s*(Yes|No|Unclear)\b", re.IGNORECASE)


def cpe_token(value: str | None) -> str:
    """The leading Yes/No/Unclear of a stored CPE value ("" when it has none)."""
    m = _CPE_LEAD.match(value or "")
    return m.group(1).capitalize() if m else ""


def coerce_cpe_eligible(value: str | None) -> str:
    """Coerce a CPE value to the allowed vocabulary: a bare Yes/No/Unclear, or
    that word plus a short parenthesized qualifier. Anything else keeps only its
    leading word when it has one (a legacy outlier such as "No evidence of CPE
    credit offered; assume no" becomes "No") and is blank otherwise."""
    v = (value or "").strip()
    if not v:
        return ""
    if _CPE_FULL.match(v):
        return v
    return cpe_token(v)


def resolve_cpe_submission(posted_token: str | None, posted_full: str | None,
                           stored: str | None) -> str:
    """The CPE value to store after a form submit.

    The dropdown only carries the leading word, so on its own a save would erase
    a stored qualifier such as "Yes (NASBA-approved sponsor)". The qualifier is
    kept when the posted word still matches it: taken from the freshly generated
    value the page sends along (`posted_full`), else from what is already stored.
    Picking a different word by hand drops the qualifier, since it described the
    old answer."""
    token = (posted_token or "").strip()
    if token not in CPE_OPTIONS:
        return ""
    full = coerce_cpe_eligible(posted_full)
    if full and cpe_token(full) == token:
        return full
    kept = coerce_cpe_eligible(stored)
    if kept and cpe_token(kept) == token:
        return kept
    return token
