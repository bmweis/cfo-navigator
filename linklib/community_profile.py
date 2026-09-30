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
# it is refused whole, never shortened. As of 2a.1 the maxes are DELIBERATELY
# below some stored text (production read 2026-09-29: ideal_member 1053,
# anti_fit 724, value_prop 1017, ..., see PRODUCTION_LONGEST): existing
# over-limit profiles stay as they are, are listed on /admin/checks ("Profile
# fields over their limit") and clear as they are trimmed by hand.
PROFILE_LIMITS: dict[str, tuple[int, int]] = {
    # The 11 narrative fields in the four groups share one budget (2a.1).
    "ideal_member": (600, 800),
    "anti_fit": (600, 800),
    "value_prop": (600, 800),
    "format_reality": (600, 800),
    "engagement_level": (600, 800),
    "application_friction": (600, 800),
    "business_model": (600, 800),
    "sponsor_relationship_note": (600, 800),
    "cost_value_verdict": (600, 800),
    "notable_members": (600, 800),
    "public_criticism": (600, 800),
    "verdict_summary": (250, 400),
    "resources_included": (300, 600),
    "jobs_program": (300, 600),
}

# Longest value stored in production per limited field (same read as above).
PRODUCTION_LONGEST: dict[str, int] = {
    "ideal_member": 1053, "anti_fit": 724, "value_prop": 1017, "format_reality": 775,
    "engagement_level": 820, "sponsor_relationship_note": 804, "application_friction": 733,
    "cost_value_verdict": 748, "notable_members": 925, "public_criticism": 670,
    "verdict_summary": 214, "business_model": 790, "resources_included": 131,
    "jobs_program": 266,
}

# CPE eligible has four states (2a.1). Not assessed means nobody has researched
# it: the default for a new profile and for any empty stored value (read as Not
# assessed on load, written as "Not assessed" on the next save). Unclear means it
# was researched and the evidence does not settle it. Yes, No and Unclear may
# carry a short note; Not assessed never does. A generation run can only write
# CPE_OPTIONS (Yes, No, Unclear), never Not assessed.
NOT_ASSESSED = "Not assessed"
CPE_OPTIONS = ("Yes", "No", "Unclear")
CPE_STATES = (NOT_ASSESSED,) + CPE_OPTIONS
_CPE_FULL = re.compile(r"^(Not assessed|(Yes|No|Unclear)( \(.{1,120}\))?)$", re.DOTALL)
_CPE_LEAD = re.compile(r"^\s*(Not assessed|Yes|No|Unclear)\b", re.IGNORECASE)


def cpe_token(value: str | None) -> str:
    """The leading word of a stored CPE value: Not assessed, Yes, No or Unclear
    ("" when it has none)."""
    m = _CPE_LEAD.match(value or "")
    return m.group(1).capitalize() if m else ""


def cpe_state(value: str | None) -> str:
    """The state a stored value shows as: its leading word, or Not assessed
    when it is empty or has none. Every surface reads CPE through this."""
    return cpe_token(value) or NOT_ASSESSED


def generated_cpe(value: str | None) -> str:
    """What a generation run may write: Yes, No or Unclear, plus a note of 40
    characters or fewer. A missing, garbled or Not assessed answer becomes
    Unclear, because this is only reached after the page was retrieved but did
    not settle it; a longer note is dropped rather than cut mid-claim. A run
    never writes Not assessed."""
    v = coerce_cpe_eligible(value)
    tok = cpe_token(v)
    if tok not in CPE_OPTIONS:
        return "Unclear"
    note = cpe_note(v)
    if len(note) > CPE_NOTE_LIMITS[0]:
        note = ""
    return assemble_cpe(tok, note)


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


def cpe_for_generation_run(stored: str | None, drafted: str | None) -> tuple[str, bool]:
    """(value to write, kept_stored) for a run that persists a draft (the two
    bulk scripts). A run never downgrades or overwrites an answer a human or an
    earlier run already gave: it writes its draft only while the stored value is
    empty or Not assessed, and otherwise keeps the stored one."""
    if cpe_state(stored) != NOT_ASSESSED:
        return (stored or "").strip(), True
    return drafted or "", False


# The short note beside the CPE answer (2a.1). It is not a column of its own:
# the stored string already encodes it as "Yes (note)", so the form parses that
# string on load and assembles it on save. Same two-tier pattern as the prose
# fields: the target only turns the live counter amber, the max refuses a save.
CPE_NOTE_LIMITS = (40, 60)
_CPE_NOTE_PAREN = re.compile(r"^\s*(?:Yes|No|Unclear)\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL)
_CPE_NOTE_REST = re.compile(r"^\s*(?:Yes|No|Unclear)\b[\s:;,.\-\u2013\u2014]*", re.IGNORECASE)


def _clean_note(note: str | None) -> str:
    n = re.sub(r"\s+", " ", (note or "")).strip()
    if n.startswith("(") and n.endswith(")"):
        inner, depth = n[1:-1], 0
        for ch in inner:
            depth += (ch == "(") - (ch == ")")
            if depth < 0:
                break
        if depth == 0:
            n = inner.strip()
    return n


def cpe_note(value: str | None) -> str:
    """The note part of a stored CPE value: the parenthetical of "Yes (note)".
    A legacy value with prose after the word instead of a parenthetical (for
    example "Yes - NASBA sponsor") returns that prose, so nothing stored is
    hidden from the admin or lost when the page is saved."""
    v = (value or "").strip()
    if cpe_token(v) not in CPE_OPTIONS:
        return ""
    m = _CPE_NOTE_PAREN.match(v)
    if m:
        return _clean_note(m.group(1))
    return _clean_note(_CPE_NOTE_REST.sub("", v, count=1))


def assemble_cpe(token: str | None, note: str | None) -> str:
    """Build the stored value from the dropdown word and the note: a bare word,
    or "Word (note)". Not assessed (or no valid word) stores "Not assessed" and
    never a note."""
    t = (token or "").strip()
    if t not in CPE_OPTIONS:
        return NOT_ASSESSED
    n = _clean_note(note)
    return f"{t} ({n})" if n else t


def resolve_cpe_submission(posted_token: str | None, posted_note: str | None) -> str:
    """The CPE value to store after a form submit (assembled, not merged)."""
    return assemble_cpe(posted_token, posted_note)
