"""Community profile edit page layout pass (2026-08 follow-up to the
confidence-indicator work) — grouping the 23 fields into 5 labeled
sections, a consistent narrative-full-width vs. short-factual-2-column
width rule applied uniformly, and moving the confidence indicator from a
block-level paragraph under each textarea to a compact inline badge beside
the field label. Covers structure (section order/membership, width
placement) via TestClient; the actual visual no-crowding claim (confidence
badge next to a required-field asterisk on mobile) is verified separately
with a real Playwright render, not asserted here.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _matching_div_close(html: str, open_tag_start: int) -> int:
    """Index of the `</div>` that closes the `<div` beginning at or before
    `open_tag_start` (i.e. `open_tag_start` sits somewhere inside that div's
    opening tag, such as within a style="..." attribute) — found by walking
    forward and tracking nesting depth, not by grabbing the first `</div>`
    after some unrelated later position. A plain `html.index("</div>", x)`
    only ever finds the closest FOLLOWING close tag, which is frequently a
    child div's own close, not the outer div's — exactly the bug that let
    an earlier version of this file's grid-boundary check silently stop
    verifying anything once a field moved outside the grid but still
    rendered after another field's own (much earlier-closing) div."""
    tag_open = html.rindex("<div", 0, open_tag_start + 1)
    pos = html.index(">", tag_open) + 1
    depth = 1
    while depth > 0:
        next_open = html.find("<div", pos)
        next_close = html.find("</div>", pos)
        assert next_close != -1, "unbalanced <div> in rendered HTML"
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = html.index(">", next_open) + 1
        else:
            depth -= 1
            pos = next_close + len("</div>")
    return next_close


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


SECTION_HEADERS = [
    "Who it's for",
    "The member experience",
    "Business and sponsorship",
    "Reputation and verdict",
    "Quick facts",
]

# (field name attribute, expected section header it should fall after)
FIELD_SECTIONS = [
    ("ideal_member", "Who it's for"),
    ("anti_fit", "Who it's for"),
    ("value_prop", "Who it's for"),
    ("format_reality", "The member experience"),
    ("engagement_level", "The member experience"),
    ("application_friction", "The member experience"),
    ("business_model", "Business and sponsorship"),
    ("sponsor_relationship_note", "Business and sponsorship"),
    ("cost_value_verdict", "Business and sponsorship"),
    ("notable_members", "Reputation and verdict"),
    ("public_criticism", "Reputation and verdict"),
    ("verdict_summary", "Reputation and verdict"),
    ("resources_included", "Quick facts"),
    ("founded_year", "Quick facts"),
    ("primary_purpose", "Quick facts"),
    ("cpe_eligible", "Quick facts"),
    ("platform_type", "Quick facts"),
    ("meeting_format", "Quick facts"),
    ("event_style", "Quick facts"),
    ("seniority_band", "Quick facts"),
    ("stage_focus", "Quick facts"),
    ("jobs_program", "Quick facts"),
    ("team_or_individual", "Quick facts"),
]


@pytest.fixture
def html(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.close()
    client = _client(env)
    _login(client)
    r = client.get(f"/admin/tools/communities/{cid}/profile")
    assert r.status_code == 200
    return r.text


def test_all_five_section_headers_present_once_each(html):
    for header in SECTION_HEADERS:
        assert html.count(f">{header}<") == 1, f"expected exactly one {header!r} header"


def test_section_headers_appear_in_order(html):
    positions = [html.index(f">{h}<") for h in SECTION_HEADERS]
    assert positions == sorted(positions)


def test_every_field_falls_under_its_expected_section(html):
    """Each field's name="..." attribute must appear after its section
    header and before the next one (or end of form for the last section)."""
    header_positions = {h: html.index(f">{h}<") for h in SECTION_HEADERS}
    ordered_headers = SECTION_HEADERS
    for field, section in FIELD_SECTIONS:
        field_idx = html.index(f'name="{field}"')
        start = header_positions[section]
        assert field_idx > start, f"{field} should appear after {section!r} header"
        pos_in_order = ordered_headers.index(section)
        if pos_in_order + 1 < len(ordered_headers):
            next_header = ordered_headers[pos_in_order + 1]
            end = header_positions[next_header]
            assert field_idx < end, f"{field} should appear before {next_header!r} header"


def test_all_23_fields_still_present(html):
    assert len(FIELD_SECTIONS) == 23
    for field, _ in FIELD_SECTIONS:
        assert f'name="{field}"' in html


NARRATIVE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
    "application_friction", "business_model", "sponsor_relationship_note",
    "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary",
]
# The seven genuinely short/enum-like Quick facts fields — no character
# budget, no counter, stay in the compact multi-column grid.
SHORT_GRID_FIELDS = [
    "founded_year", "primary_purpose", "cpe_eligible", "platform_type",
    "meeting_format", "event_style", "seniority_band",
]
# stage_focus/jobs_program/team_or_individual moved OUT of the compact grid
# (community quick-facts width fix, 2026-09) — they carry the
# COMMUNITY_SHORT_FIELD_TARGET/MAX (300/800) character budget and its live
# counter, which never fit the grid's ~230px-wide cells (the counter text
# wrapped to two lines there, and a real ~440-char saved value truncated
# invisibly inside a single-line input). They're now full-width textareas,
# matching the software edit page's own roomier narrative-field treatment.
BUDGETED_FULL_WIDTH_FIELDS = ["stage_focus", "jobs_program", "team_or_individual"]


def test_narrative_fields_are_textareas_not_in_the_paired_grid(html):
    for field in NARRATIVE_FIELDS:
        assert f'<textarea id="cp-{field}"' in html


_SHORT_GRID_SELECTOR = "grid-template-columns:repeat(auto-fit,minmax(200px,1fr))"


def test_short_factual_fields_are_paired_inputs_inside_one_grid(html):
    """The seven genuinely short/categorical fields (Founded year included)
    live in one shared responsive grid.

    PR 28 (2026-09) switched this grid from a hardcoded `1fr 1fr` (which
    truncated real saved values on a phone-width viewport — two ~170px
    columns can't fit a value like "VPs and directors in SaaS finance") to
    `repeat(auto-fit,minmax(200px,1fr))`, the same responsive pattern
    already used two sections lower on this same page.

    The grid's real closing `</div>` is found by depth-counting nested
    divs from the grid's own opening tag (_matching_div_close), not by
    grabbing the next `</div>` after some field's `name=` attribute — that
    cheaper approach only ever finds the nearest FOLLOWING close tag, which
    is usually a child field's own div, not the grid's. An earlier version
    of this test anchored on team_or_individual's own div that way, which
    silently stopped verifying anything once team_or_individual moved
    outside the grid: its div still closes after the grid starts, so the
    (wrong) boundary check kept "passing" without ever checking the grid's
    real extent."""
    grid_start = html.index(_SHORT_GRID_SELECTOR)
    grid_end = _matching_div_close(html, grid_start)
    for field in SHORT_GRID_FIELDS:
        idx = html.index(f'name="{field}"')
        assert grid_start < idx < grid_end, f"{field} should be inside the paired grid"
    # The three budgeted fields must NOT be inside the grid's own boundary.
    for field in BUDGETED_FULL_WIDTH_FIELDS:
        idx = html.index(f'name="{field}"')
        assert not (grid_start < idx < grid_end), (
            f"{field} should have moved out of the compact grid onto its own full-width field")
    # Only one such grid on the page (the old layout had two).
    assert html.count(_SHORT_GRID_SELECTOR) == 1


def test_resources_included_is_full_width_textarea_not_in_grid(html):
    """Resources included is longer free text, not a categorical value, so
    it stays a full-width textarea (Quick facts' intro field) rather than
    being squeezed into the paired grid alongside truly short fields."""
    assert '<textarea id="cp-resources_included"' in html
    grid_start = html.index(_SHORT_GRID_SELECTOR)
    resources_idx = html.index('name="resources_included"')
    assert resources_idx < grid_start


def test_budgeted_fields_are_full_width_textareas_with_a_character_counter(html):
    """stage_focus/jobs_program/team_or_individual are full-width
    textareas (matching NARRATIVE_FIELDS' own treatment, not the compact
    grid's single-line <input>), each carrying its own live character-
    budget counter — same mechanism PR 600 gave every other AI-drafted
    field, just relocated to a layout that can actually hold it."""
    grid_end = _matching_div_close(html, html.index(_SHORT_GRID_SELECTOR))
    for field in BUDGETED_FULL_WIDTH_FIELDS:
        assert f'<textarea id="cp-{field}"' in html
        idx = html.index(f'name="{field}"')
        assert idx > grid_end, f"{field} should render after the compact grid closes"
        counter_idx = html.index(f'id="cp-{field}-budget"')
        assert counter_idx > idx
        assert "Limited to 800 characters." in html[counter_idx:counter_idx + 200]


def test_confidence_badge_is_inline_next_to_label_not_below_textarea(html):
    """2026-08 layout pass: the confidence badge moved inline beside the
    label instead of sitting in its own paragraph after the textarea."""
    label_idx = html.index('for="cp-ideal_member"')
    textarea_idx = html.index('<textarea id="cp-ideal_member"')
    badge_idx = html.index("Claude confidence:", label_idx)
    assert label_idx < badge_idx < textarea_idx, (
        "confidence badge should appear between the label and the textarea, not after it"
    )


def test_confidence_badge_still_shows_all_three_states(html):
    # No signal ever reported on a brand-new profile -> "Not yet assessed"
    # for all 12 confidence-bearing fields.
    assert html.count("Claude confidence: Not yet assessed") == 12
