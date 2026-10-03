"""The words for a Software vendor's profile fields, defined once (PR 2a.2).

Standing rule: the name of a field on the edit page is the name a visitor sees
on the profile, in Compare and over MCP. The admin edit page, the public
profile, Compare, the MCP `get_software` tool and the over-limit refusal
banner all read these constants, and `tests/test_software_labels.py` fails if
any of them drifts.

Labels only. No column, key, parameter or MCP field name (`agent_taxonomy`,
`summary`, `competitive_differentiation`) is derived from anything here, so
stored data, `matchmaker.py` and MCP clients are untouched.

A leaf module on purpose (no imports): `linklib/db.py`, `linklib/compare.py`
and `webapp/` all import it, and a leaf cannot start an import cycle.
"""

SHORT_SUMMARY = "Short summary"
DESCRIPTION = "Description"
# The agent field. Edit label, profile heading, MCP label.
AGENT = "How autonomous is it?"
BOTTOM_LINE = "Bottom line"
COMPETITORS = "Competitors"

# Grouping names. They name a section or an eyebrow, not the field, so they
# are kept as they are and listed in the PR's copy table for review.
SECTION_AGENT = "AI / Agent involvement"
EYEBROW_AGENT = "AI agent capabilities"
