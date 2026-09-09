"""Real Markdown/List Rendering for Narrative Fields (2026-09) — integration
coverage across every affected surface: tool profile (Description/Agent
taxonomy/Bottom line), community profile (group fields + Bottom line), and
the Compare pages' deliberate exclusion (narrative excerpts stay plain
`_esc()` text — see the Step 0 investigation's line-clamp finding).
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


LIST_TEXT = (
    "Runway ships a roster of purpose-built agents.\n\n"
    "- Contract Review Agent—extracts key terms\n"
    "- Close Assistant—flags anomalies\n\n"
    "Each agent runs inside existing workflows."
)
XSS_TEXT = "Uses agents.\n\n<script>alert(1)</script>\n\n- A real bullet"


# ---------------------------------------------------------------------------
# Tool profile page
# ---------------------------------------------------------------------------

def test_tool_description_renders_real_list(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", LIST_TEXT, "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert r.status_code == 200
    assert "<ul>" in r.text
    assert "<li>Contract Review Agent—extracts key terms</li>" in r.text
    # The rendered Description card itself has no literal "- " left (the
    # literal-dash bug this PR fixes) — a raw copy of the same text also
    # legitimately lives in the page's client-side search JSON payload
    # (ALL_TOOLS), a separate, untouched feature, so this checks the
    # rendered card specifically rather than the whole page.
    card_start = r.text.index('<h2 class="tp-card-h">Description')
    card_end = r.text.index("</div>", card_start)
    assert "- Contract Review Agent" not in r.text[card_start:card_end]


def test_tool_agent_taxonomy_renders_real_list_and_escapes_html(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tid, XSS_TEXT, needs_verification=0)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "<li>A real bullet</li>" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_tool_bottom_line_renders_markdown(env):
    """competitive_differentiation ("Bottom line") — no live bug (its
    generation prompt says "plain prose only," see Step 0 §4) but rendered
    through the same shared helper for consistency, per Brian's approval."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tid, "Runway wins on **breadth** of integrations.", needs_verification=0)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "<strong>breadth</strong>" in r.text
    # Typography preserved: still navy/16px/1.5, now via a wrapping div
    # instead of the single <p> it replaced (Bottom-line typography check).
    assert 'class="narrative-md" style="color:var(--navy);font-size:16px;line-height:1.5' in r.text


# ---------------------------------------------------------------------------
# Community profile page
# ---------------------------------------------------------------------------

def test_community_profile_group_field_renders_real_list(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community("CFO Alliance", "https://cfoalliance.com", "d", "Free", ["Peer group"], approved=1)
    lib.upsert_community_profile(cid, ideal_member=LIST_TEXT, needs_review=0)
    slug = lib.get_community(cid)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "<ul>" in r.text
    assert "<li>Contract Review Agent—extracts key terms</li>" in r.text


def test_community_bottom_line_renders_markdown_and_escapes_html(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community("CFO Alliance", "https://cfoalliance.com", "d", "Free", ["Peer group"], approved=1)
    lib.upsert_community_profile(cid, verdict_summary=XSS_TEXT, needs_review=0)
    slug = lib.get_community(cid)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "<li>A real bullet</li>" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ---------------------------------------------------------------------------
# Compare pages — deliberately EXCLUDED from markdown rendering (Step 0 §3):
# the clamped excerpt stays plain _esc() text so -webkit-line-clamp keeps
# working; the real rendering only happens via the "Full profile →" link.
# ---------------------------------------------------------------------------

def test_software_compare_excerpt_stays_plain_text_not_markdown(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", LIST_TEXT, "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    # The raw "- " dash literally survives in the clamped excerpt — no <ul>.
    assert "- Contract Review Agent" in r.text
    assert "<li>Contract Review Agent" not in r.text


def test_communities_compare_excerpt_stays_plain_text_not_markdown(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("CFO Alliance", "https://cfoalliance.com", "d", "Free", ["Peer group"], approved=1)
    b = lib.add_community("The F Suite", "https://fsuite.com", "d", "Free", ["Peer group"], approved=1)
    lib.upsert_community_profile(a, verdict_summary=LIST_TEXT, needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "- Contract Review Agent" in r.text
    assert "<li>Contract Review Agent" not in r.text


def test_original_content_markdown_rendering_unaffected(env):
    """Sanity check that this PR didn't touch _render_original_content_markdown
    at all — it keeps allowing raw HTML passthrough (trusted, admin-authored
    body_md), unlike the new restricted narrative-field renderer."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_original_content(
        slug="md-check", title="Check", teaser="t", tag_label="Playbook",
        link_label="Read", body_md="<div class=\"custom\">raw html stays raw</div>\n\n- a list item",
        status="live",
    )
    lib.close()

    r = _client(env).get("/thought-leadership/md-check")
    assert r.status_code == 200
    assert '<div class="custom">raw html stays raw</div>' in r.text
    assert "<li>a list item</li>" in r.text
