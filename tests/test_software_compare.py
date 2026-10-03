"""Software comparison matrix (/tools/software/compare, Phase 5) and the
agent-taxonomy free-text field (a Phase 0 decision never actually shipped
until now, since the matrix is the first thing that needed it rendered).
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


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


# -- agent_taxonomy_note ------------------------------------------------------

def test_agent_taxonomy_saved_via_admin_edit(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "agent_taxonomy_note": "Fully independent AI agent, not a bolted-on feature.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["agent_taxonomy_note"] == "Fully independent AI agent, not a bolted-on feature."
    lib.close()


def test_agent_taxonomy_shown_on_profile_and_searchable_on_card(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Agent-assisted, not fully autonomous.")
    lib.close()

    client = _client(env)
    r = client.get("/tools/software/runway")
    assert r.status_code == 200
    assert "What its agents do" in r.text
    assert "AI agent capabilities" in r.text
    assert "Agent-assisted, not fully autonomous." in r.text

    r = client.get("/tools/software")
    assert "Agent-assisted, not fully autonomous." in r.text   # present in the embedded ALL_TOOLS JSON


def test_agent_taxonomy_shows_placeholder_when_empty(env):
    """Radical-transparency review standard: an empty section used to be
    omitted from the page entirely for every viewer. It now shows an honest
    placeholder to every viewer instead of vanishing."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Solo Co", "No agent taxonomy set.", "https://solo.example", [], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "What this tool's agents do hasn't been documented." in r.text


# -- compare route ------------------------------------------------------------
#
# Compare Redesign Phase 1 (2026-09) rebuilt this page on a shared
# linklib/compare.py serializer, with grouped section headers (fixing the
# old orphaned-header bug — only "What its agents do" used to get a
# .cc-section band), a Key facts band with shared/unique tag chips, working
# citation chips (reusing entity_citations + _citations_list_html, exactly
# as the profile page does), and full, unclamped narrative text
# (.cmp-text, pre-wrap so a bulleted "- " field keeps its
# own lines instead of flattening into run-on prose — the objective bug
# Brian's review flagged). See linklib/compare.py's module docstring and
# ARCHITECTURE.md's Compare section for the full write-up.

def test_compare_route_not_swallowed_by_slug_route(env):
    """Regression: /tools/software/compare must resolve to the compare view,
    not 404 as if "compare" were a slug — route registration order matters."""
    r = _client(env).get("/tools/software/compare")
    assert r.status_code == 200
    assert "Compare software" in r.text
    assert "Pick at least two tools" in r.text


def test_compare_requires_at_least_two(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software/compare?ids=1")
    assert "Pick at least two tools" in r.text


def test_compare_renders_directory_fields_side_by_side(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Financial planning for high-growth teams.", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "Financial planning inside Excel.", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Fully independent agent.")
    lib.update_tool_differentiation(b, "Keeps teams in Excel.")
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "Runway" in r.text and "Datarails" in r.text
    assert "Financial planning for high-growth teams." in r.text
    assert "Financial planning inside Excel." in r.text
    assert "Fully independent agent." in r.text
    assert "Keeps teams in Excel." in r.text


def test_compare_gives_agent_involvement_its_own_section(env):
    """AI/agent involvement is a dedicated section (same visual weight as
    every other section now, not just this one) rather than sitting
    alongside Description as just another text field — this is a
    comparison dimension buyers increasingly ask about first."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Concourse", "AI agents for finance.", "https://concourse.co", ["FP&A"], approved=1)
    b = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Fully independent agent that runs the whole workflow.")
    # Runway has no agent_taxonomy_note set — must read as "not documented",
    # never as "this tool has no agent capability."
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "What its agents do" in r.text
    assert "Fully independent agent that runs the whole workflow." in r.text
    assert "Not documented." in r.text
    assert "not have" not in r.text.lower() and "no agent" not in r.text.lower()


def test_compare_every_section_gets_a_real_header_band(env):
    """Regression for the orphaned-header bug: previously only "AI / Agent
    involvement" got a .cc-section teal band; Description/Bottom line/
    Competitors floated with no visual hierarchy. Now every section shares
    the identical band treatment. (Key facts is Software's tags-only band
    from the initial Compare Redesign Phase 1 build — it was retired once
    tags moved into the header row; see test_compare_no_key_facts_band.)"""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    # Software has one field per section, so each is a labelled row (field
    # name in the first column) and there are no navy bands at all.
    for title in ("Short summary", "What its agents do", "Bottom line", "Competitors"):
        assert '<td class="cc-cell cc-label' in r.text and f'>{title}</td>' in r.text, title
    assert 'class="cc-cell cc-section"' not in r.text
    body = r.text[r.text.index("<tbody>"):]
    assert body.index(">Bottom line</td>") < body.index(">Short summary</td>")   # Bottom line leads, seafoam
    assert 'cc-label cc-bl">Bottom line</td>' in body


def test_compare_no_key_facts_band(env):
    """Compare Redesign Phase 1 follow-up: once tags moved into the header
    row, Software had nothing left for a Key facts band, so it's retired
    outright — unlike Communities, which keeps its own (Region/Access/
    Sponsor/Cost), Software's page has no "Key facts" section at all."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert 'cc-section" colspan="3"><span class="cmp-sticky-label">Key facts</span></td>' not in r.text


def test_compare_tags_render_under_entity_name_in_header(env):
    """Tags moved out of Key facts and into the header row, directly under
    each entity's name — same shared/unique chip treatment, new location."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A", "ERP"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    name_idx = r.text.index('>Runway</a>')
    tag_idx = r.text.index('<span class="cmp-tag cmp-tag-shared">FP&amp;A</span>')
    # The shared tag chip must appear in the DOM shortly after Runway's own
    # name link (inside the same header <th>), not down in a separate band.
    assert name_idx < tag_idx < name_idx + 400
    assert '<span class="cmp-tag cmp-tag-unique">ERP</span>' in r.text


def test_compare_no_tags_no_empty_tag_row(env):
    """An entity with no tags at all renders no .cmp-tag-row markup —
    _cmp_tag_chips_html returns "" rather than an empty wrapper div."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert '<div class="cmp-tag-row">' not in r.text


def test_compare_mobile_sticky_section_label_css(env):
    """Mobile follow-up: the .cc-label column (every row's field name, plus
    the blank corner cell) gets position:sticky below the 700px breakpoint,
    so swiping to a second/third entity never loses the row's own label."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "@media (max-width:700px)" in r.text
    assert ".cc-label{position:sticky;left:0;" in r.text
    assert "table.cc-table.cc-table{overflow:visible!important;}" in r.text


def test_compare_swipe_hint_present_and_not_styled_like_a_link(env):
    """The swipe hint is a passive affordance, deliberately not styled like
    this page's own "Full profile →" link (navy, bold) — muted text, a
    two-directional icon, no href, no cmp-full-link class anywhere near it."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert '<div class="cmp-swipe-hint" id="cmp-swipe-hint">' in r.text
    assert "Swipe to compare" in r.text
    assert "<a " not in r.text[r.text.index('id="cmp-swipe-hint"'):r.text.index("Swipe to compare")]
    assert 'id="cmp-scroll-wrap"' in r.text


def test_compare_swipe_hint_dismiss_js_uses_localstorage(env):
    """The dismiss-on-first-scroll logic follows this codebase's existing
    plain-localStorage convention (reader-fs, cfo_admin_cols_*, the play
    high-score keys) — no new persistence mechanism invented."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "cmp_swipe_hint_seen" in r.text
    assert "localStorage.getItem(KEY)" in r.text
    assert "localStorage.setItem(KEY, '1')" in r.text
    assert "addEventListener('scroll'" in r.text


def test_compare_differentiation_renamed_to_bottom_line(env):
    """Renamed from "How this differs" to "Bottom line," matching the exact
    heading the tool's own profile page uses for this field — both the
    section header and gates.EMPTY_COPY/COMPARE_EMPTY_LABELS were
    previously mismatched (row label "How this differs" vs. copy text
    "Bottom line not available.")."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "Keeps teams in a native workflow.")
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Bottom line" in r.text
    assert "How this differs" not in r.text
    assert "Keeps teams in a native workflow." in r.text


def test_compare_narrative_field_preserves_line_breaks(env):
    """The flattened-markdown bug: previously Software's compare cells had
    no white-space:pre-wrap at all, so a "- " bulleted agent_taxonomy_note
    ran together into one line. Now every narrative cell wraps its text in
    .cmp-text (white-space:pre-wrap in the page's own <style>), so
    the newline-separated bullets stay on their own lines."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Concourse", "FP&A", "https://concourse.co", ["FP&A"], approved=1)
    b = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    bullets = "- Contract Review Agent—extracts key terms.\n- Close Agent—drafts the memo."
    lib.update_tool_agent_taxonomy(a, bullets)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert '<div class="cmp-text">' in r.text
    assert "- Contract Review Agent—extracts key terms.\n- Close Agent—drafts the memo." in r.text
    assert "white-space:pre-wrap" in r.text  # .cmp-text's own rule, in the page's <style>


def test_compare_shows_working_citation_chips(env):
    """Dead citation markers fix: [1]/[2] used to render as inert escaped
    text with no Sources list. Compare now fetches entity_citations the
    same way the profile page does and renders the same _citations_list_html
    "Sources" chip list right under the field."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Fully independent agent[1].")
    lib.set_entity_citations("tool", a, "agent_taxonomy",
                              [{"n": 1, "title": "Runway product page", "url": "https://runway.com/product"}])
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Fully independent agent[1]." in r.text
    assert "Sources" in r.text
    assert 'href="https://runway.com/product"' in r.text
    assert "Runway product page" in r.text


def test_compare_shows_competitors_chip_list(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_competitor(a, b)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Competitors" in r.text
    assert '<a href="/tools/software/datarails" class="cmp-chip"' in r.text


def test_compare_competitors_empty_state_when_none_curated(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Not curated." in r.text


def test_compare_full_profile_link_per_entity(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert '<a href="/tools/software/runway" target="_blank" rel="noopener" class="cmp-full-link">Full profile' in r.text
    assert '<a href="/tools/software/datarails" target="_blank" rel="noopener" class="cmp-full-link">Full profile' in r.text


def test_agent_taxonomy_unverified_shown_under_review_on_public_profile(env):
    """Radical-transparency review standard (supersedes the old Abacum-
    fabrication-finding publish gate, which hid a drafted/unconfirmed note
    from a public visitor entirely): the note now always renders, labeled
    "under review" for a visitor rather than hidden."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_agent_taxonomy_unverified_visible_to_admin_labeled_unverified(env):
    """The same unconfirmed note is also visible to a signed-in admin,
    labeled "unverified, visible to visitors" — truthful under the
    radical-transparency standard, since it's now shown to everyone."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." in r.text
    assert "unverified, visible to visitors" in r.text
    assert ".tp-verify{" in r.text   # profile page has its own <style> block


def test_agent_taxonomy_no_flag_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.mark_tool_agent_taxonomy_verified(a)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." in r.text
    assert '<span class="tp-verify">unverified' not in r.text


def test_compare_shows_unverified_agent_taxonomy_under_review_to_public(env):
    """Radical-transparency review standard: an unverified note on the
    compare matrix always renders now, labeled "under review" for a public
    visitor rather than collapsed into the empty-tool "Not documented."
    cell — the third, previously-missing distinguishable state."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Drafted agent note for Runway.", 0.5)
    lib.update_tool_agent_taxonomy(b, "Confirmed agent note for Datarails.")
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted agent note for Runway." in r.text
    assert "Confirmed agent note for Datarails." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_compare_shows_agent_taxonomy_verification_flag_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Drafted agent note for Runway.", 0.5)
    lib.update_tool_agent_taxonomy(b, "Confirmed agent note for Datarails.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert '<span class="cc-verify">unverified' in r.text
    assert "Drafted agent note for Runway." in r.text
    assert "Confirmed agent note for Datarails." in r.text
    # The confirmed tool's note must not itself carry the flag.
    confirmed_idx = r.text.index("Confirmed agent note for Datarails.")
    assert "cc-verify" not in r.text[confirmed_idx:confirmed_idx + 80]


def test_compare_caps_at_four_tools(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = [lib.add_tool(f"Tool {i}", "d", f"https://tool{i}.com", ["FP&A"], approved=1) for i in range(6)]
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={','.join(str(i) for i in ids)}")
    assert r.status_code == 200
    for i in range(4):
        assert f"Tool {i}" in r.text
    for i in range(4, 6):
        assert f"Tool {i}" not in r.text


def test_compare_excludes_unapproved_tools(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Pending Co", "Not approved", "https://pending.example", [], approved=0)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Pick at least two tools" in r.text   # only 1 approved tool made it in, falls below the minimum


def test_directory_card_has_compare_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software")
    assert "toggleToolCompareSelect" in r.text
    assert "TOOL_COMPARE_MAX = 4" in r.text
