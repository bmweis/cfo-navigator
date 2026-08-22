"""Phase 4 of the Exa integration: a page explaining FP&A Buddy's mechanism
in plain language. Originally admin-only at /admin/system/how-fpa-buddy-works;
made public and moved to /tools/fpa-buddy/how-it-works in the explainer-page
follow-up round (public showcase content, reachable by anyone with the link,
not required to be admin-gated). Docs/UI-only — no retrieval or citation
logic changes.
"""
import pathlib
import sys
import tempfile
import os

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
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def test_page_is_public_no_login_required(env):
    """Anonymous, member, and admin visitors all get the real page — this
    is a public showcase page now, not an admin-gated one."""
    for c in (_client(env), _member_client(env), _admin_client(env)):
        resp = c.get("/tools/fpa-buddy/how-it-works", follow_redirects=False)
        assert resp.status_code == 200
        assert "How FP&amp;A Buddy works" in resp.text or "How FP&A Buddy works" in resp.text


def test_old_admin_url_no_longer_serves_the_page(env):
    """The page moved off /admin/* outright — no route left behind there."""
    c = _admin_client(env)
    resp = c.get("/admin/system/how-fpa-buddy-works")
    assert resp.status_code == 404


def test_loads_with_expected_sections(env):
    c = _client(env)
    resp = c.get("/tools/fpa-buddy/how-it-works")
    assert resp.status_code == 200
    body = resp.text
    assert "Where an answer&#x27;s sources come from" in body or "Where an answer's sources come from" in body
    assert "Quick, Standard, Deep" in body
    assert "Every claim traces to a citation" in body
    assert "What it costs" in body
    assert "Web search powered by Exa" in body


def test_public_audience_content_fixes(env):
    """The content audit before this page went public: no admin breadcrumb,
    no linked-but-gated /admin/exa-settings or /admin/users references, and
    no link to the private repo's ARCHITECTURE.md (which 404s for anyone
    outside the repo)."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "&larr; FP&amp;A Buddy" in body or "&larr; FP&A Buddy" in body
    assert '<a href="/admin"' not in body
    assert 'href="/admin/exa-settings"' not in body
    assert 'href="/admin/users"' not in body
    assert "ARCHITECTURE.md" not in body


def test_effort_tier_counts_are_read_live_from_effort_settings(env):
    """Per-tier source counts must come from EFFORT_SETTINGS itself, not a
    hand-typed copy that could drift out of sync with it."""
    from linklib.agent import EFFORT_SETTINGS
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    for tier, settings in EFFORT_SETTINGS.items():
        assert f'>{tier}<' in body.lower() or tier in body.lower()
        assert f">{settings['max_library']}<" in body
        assert f">{settings['max_feed']}<" in body
        assert f">{settings['max_web']}<" in body


def test_default_ask_cap_is_read_live_not_hardcoded(env, monkeypatch):
    """A custom default cap set via Library.set_default_ask_cap must show up
    verbatim on the page — proof this isn't a hardcoded $5.00."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_ask_cap(12.34)
    lib.close()

    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "$12.34" in body


def test_admin_hub_card_links_to_the_public_page(env):
    # The admin dashboard's FP&A Buddy card now points at the same public
    # URL rather than hosting a separate admin-only copy of this page.
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    assert "/tools/fpa-buddy/how-it-works" in resp.text
    assert "How FP&amp;A Buddy works" in resp.text


# --- Phase 5: simplified concept-level Mermaid flowchart ---------------------

def test_page_ships_a_simplified_flowchart_not_the_sequence_diagram(env):
    """A concept-level flowchart (not the developer-grade sequence diagram
    already in ARCHITECTURE.md) — no token counts, API names, or cost-guard
    branches belong here; those stay in the prose/ARCHITECTURE.md."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "flowchart LR" in body
    assert "sequenceDiagram" not in body
    for label in ("Your question", "Library", "Feed", "Web", "synthesizes an answer", "numbered citations"):
        assert label in body
    # No implementation detail that belongs to the prose/ARCHITECTURE.md instead.
    for leaky_term in ("claude-haiku", "claude-sonnet", "claude-opus", "input_tokens", "cache_read"):
        assert leaky_term not in body.lower()


def test_flowchart_mermaid_js_loads_on_this_page(env):
    """Mermaid renders client-side via the same CDN script already used on
    /admin/system/database — not a new dependency, just reused."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "cdnjs.cloudflare.com/ajax/libs/mermaid" in body
    assert "mermaid.initialize(" in body
    assert 'class="mermaid"' in body


# --- Follow-up round: diagram tightened, T reconnected to the main flow -----

def test_tier_annotation_connects_into_the_main_flow_not_just_claude(env):
    """The Quick/Standard/Deep annotation node used to dangle off Claude
    alone (T -.-> C), which Mermaid's layout rendered as a disconnected box
    below the main flow. It now fans into Library/Feed/Web — the same three
    tiers its label describes — so it lays out as a peer of the question
    node instead of an orphan."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "-.-> L" in body
    assert "-.-> F" in body
    assert "-.-> W" in body
    assert "-.-> C" not in body


def test_engine_and_citations_callouts_use_bullet_lists(env):
    """Reformatted to match "Where an answer's sources come from" directly
    above them — bold-lead-in bullets, not a dense paragraph."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "Which engine handled this answer?" in body
    assert "Why the citations can be trusted" in body
    assert "<strong>Exa is the default.</strong>" in body
    assert "<strong>Documents, not pasted text.</strong>" in body
