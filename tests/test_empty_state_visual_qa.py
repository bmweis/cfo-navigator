"""Empty-state visual QA fast-follow (PR A.1) — covers the six items from
Brian's post-deploy review of the live site, on top of PR A's radical-
transparency review standard (which `test_review_state_publish_gates.py`
already covers for the review-state badge behavior itself):

1. Key features: the "Suggest one" footer link only renders once at least
   one feature is actually listed — it made no sense against an empty list.
2. One empty-state visual treatment everywhere: every empty profile-page
   section now renders as its own normal `.tp-card` + `<h2>` header, holding
   a single muted italic placeholder line — the old dashed floating box
   (`_profile_admin_nudge`) is retired entirely, and a Community
   profile-group card now shows its own group title even when empty
   (previously it showed nothing at all — the one site of the seven where a
   visitor couldn't tell WHICH section was missing).
3. The Key features card's phantom blank row/double separator (an invisible
   flag button wrapping onto its own flex line) is gone — the button is
   taken out of the flex flow entirely.
4. Equal vertical spacing above and below the "Bottom line" seafoam callout
   on the tool profile page, so the category chips row above it doesn't sit
   flush against its top edge.
5. The Community profile's Description card's "No description yet." joins
   the approved cross-entity empty-state string family
   ("Description coming soon." / "...Add one from the edit page.").
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


# -- Item 1: suggest link only renders when a feature is listed ------------------

def test_suggest_link_absent_when_no_features(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Ramp", "Spend", "https://ramp.com", ["Spend"], approved=1, summary="s")
    lib.close()

    r = _client(env).get("/tools/software/ramp")
    assert "Coming soon" in r.text
    assert "openFeatureSuggest('new_feature'" not in r.text


def test_suggest_link_present_once_a_feature_is_listed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.close()

    r = _client(env).get("/tools/software/rillet")
    assert "openFeatureSuggest('new_feature'" in r.text


def test_key_features_empty_prose_is_italic(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Ramp", "Spend", "https://ramp.com", ["Spend"], approved=1, summary="s")
    lib.close()

    r = _client(env).get("/tools/software/ramp")
    idx = r.text.index("curated feature taxonomy")
    preceding = r.text[max(0, idx - 200):idx]
    assert "font-style:italic" in preceding


# -- Item 2: one card-with-header empty-state treatment, dashed box retired ------

def test_dashed_box_style_no_longer_rendered_anywhere(env):
    """The old `_profile_admin_nudge` dashed floating box is retired
    entirely — no profile-page empty state should render `border:1px
    dashed var(--line-strong)` any more."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Bare Tool", "d", "https://baretool.example.com", ["ERP"], approved=1)
    c = lib.add_community("Bare Community", "https://barecommunity.example.com", "d",
                           "$", ["FP&A"], approved=1)
    slug_a = lib.get_tool(a)["slug"]
    slug_c = lib.get_community(c)["slug"]
    lib.close()

    for url in (f"/tools/software/{slug_a}", f"/tools/communities/{slug_c}"):
        r = _client(env).get(url)
        assert "border:1px dashed var(--line-strong)" not in r.text, url


def test_tool_empty_sections_render_as_card_with_header(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Bare Tool", "", "https://baretool.example.com", ["ERP"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    for title, text in [
        ("Competitors", "Competitors not available."),
        ("Bottom line", "Bottom line not available."),
        ("AI agent capabilities", "How autonomous this tool's AI is hasn't been documented."),
        ("Description", "Description coming soon."),
    ]:
        idx = r.text.index(text)
        preceding = r.text[max(0, idx - 250):idx]
        assert '<div class="tp-card">' in preceding, title
        assert f'<h2 class="tp-card-h">{title}' in preceding, title
        assert "font-style:italic" in preceding, title


def test_community_group_card_shows_its_own_title_even_when_empty(env):
    """Previously the one site of the seven dashed-box call sites where the
    section's own heading wasn't shown at all when empty — a visitor
    couldn't tell WHICH card was missing. Now it uses the real group_title
    as the card header, same as every other empty state."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Bare Community", "https://barecommunity.example.com", "d",
                           "$", ["FP&A"], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    for title in ("Who it's for", "What you get", "How it works", "Cost and structure"):
        idx = r.text.index(f'<h2 class="tp-card-h">{title}')
        following = r.text[idx:idx + 400]
        assert "This section hasn&#x27;t been researched yet." in following or \
            "This section hasn't been researched." in following


def test_community_empty_sections_use_generic_card_not_dashed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Bare Community", "https://barecommunity.example.com", "d",
                           "$", ["FP&A"], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    for title, text in [
        ("Bottom line", "Bottom line not available."),
        ("Similar communities", "Similar communities not available."),
    ]:
        idx = r.text.index(text)
        preceding = r.text[max(0, idx - 250):idx]
        assert '<div class="tp-card">' in preceding, title
        assert f'<h2 class="tp-card-h">{title}' in preceding, title


def test_admin_gets_appended_prompt_inside_the_same_card(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Bare Tool", "d", "https://baretool.example.com", ["ERP"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    idx = r.text.index("Competitors not available. Curate them from the edit page.")
    preceding = r.text[max(0, idx - 200):idx]
    assert '<h2 class="tp-card-h">Competitors' in preceding


# -- Item 3: phantom blank row on the Key features card --------------------------

def test_feature_flag_button_taken_out_of_flex_flow(env):
    """Regression guard for the phantom blank row: `.tp-feature-flag-btn`
    used to be a plain flex item pushed right via margin-left:auto, which
    let it wrap onto its own invisible line inside a flex-wrap row. Now
    it's position:absolute (out of flow entirely), with the <li> itself
    reserving room via padding-right. See that CSS rule's own comment for
    the full mechanism and how it was verified live (Playwright,
    bounding-box height comparison)."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("Close Management")
    tool_id = lib.add_tool("Numeric Test", "d", "https://numeric.example.com",
                            ["Close Management"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Continuous Reconciliation Monitoring")
    lib.upsert_tool_feature_link(tool_id, fid, "add_on", 0, "2026-08-19")
    lib.close()

    r = _client(env).get("/tools/software/numeric")
    idx = r.text.index(".tp-feature-flag-btn{")
    rule = r.text[idx:r.text.index("}", idx)]
    assert "position:absolute" in rule
    assert "margin-left:auto" not in rule

    idx = r.text.index(".tp-feature-list li{")
    li_rule = r.text[idx:r.text.index("}", idx)]
    assert "position:relative" in li_rule
    assert "padding:8px 28px 8px 0" in li_rule


# -- Item 4: equal spacing above/below the Bottom line callout -------------------
#
# Sidebar Consolidation pass (2026-09): the Bottom line callout moved out of
# the hero into the main column's tp-col-stack (see tools_software_profile's
# main_col comment), where the stack's own `gap:22px` now produces equal
# spacing between it and its neighbors instead of a self-margin — so these
# two tests now assert the callout sits inside that stack (no more
# self-margin string to look for) rather than checking a specific inline
# style. The spacing itself is exercised live via screenshots, not text
# assertions — a flex `gap` can't be observed from response text alone.

def test_bottom_line_callout_is_first_in_main_column_stack(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "A confirmed differentiation note.", needs_verification=0)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    stack_idx = r.text.index('<div class="tp-col-stack">')
    diff_idx = r.text.index("A confirmed differentiation note.")
    desc_idx = r.text.index('<h2 class="tp-card-h">Description')
    assert stack_idx < diff_idx < desc_idx


def test_bottom_line_callout_empty_is_first_in_main_column_stack(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    stack_idx = r.text.index('<div class="tp-col-stack">')
    diff_idx = r.text.index("Bottom line not available.")
    desc_idx = r.text.index('<h2 class="tp-card-h">Description')
    assert stack_idx < diff_idx < desc_idx


# -- Item 5: Community Description joins the approved string family -------------

def test_community_description_uses_approved_string_family(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Bare Community", "https://barecommunity.example.com", "d",
                           "$", ["FP&A"], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "No description yet." not in r.text
    assert "Description coming soon." in r.text

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert "Description coming soon. Add one from the edit page." in r.text


def test_community_description_populated_unaffected(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1, notes="A real description.")
    slug = lib.get_community(c)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "A real description." in r.text
    assert "Description coming soon." not in r.text
