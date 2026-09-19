"""Phase 4 of the Exa integration: a page explaining FP&A Buddy's mechanism
in plain language. Originally admin-only at /admin/system/how-fpa-buddy-works;
made public and moved to /tools/fpa-buddy/how-it-works in the explainer-page
follow-up round (public showcase content, reachable by anyone with the link,
not required to be admin-gated). Docs/UI-only — no retrieval or citation
logic changes.

Rewritten (2026-09) for the site's actual standing audience — CFOs and
finance leaders — cutting the page roughly in half and dropping the Mermaid
retrieval-flow diagram, the "Which engine handled this answer?" callout
(Exa-vs-native-fallback now belongs to /how-this-is-built/web-search), and
the "Behind the archive" tool inventory. See the route's own docstring for
the full account. Tests below were updated to match; the diagram/callout
tests from the earlier round are replaced with tests confirming they're
gone.
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
    assert "Where the answers come from" in body
    assert "Why the citations hold up" in body
    assert "How much effort to spend" in body
    assert "What it costs" in body
    assert "What it won&#x27;t do" in body or "What it won't do" in body
    # The old Title Case heading is gone — sentence case now, per BRAND.md §3.2.
    assert "Quick, Standard, Deep" not in body


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


# --- CFO-audience rewrite (2026-09): diagram and engine-detail callout gone -

def test_no_mermaid_flowchart_on_the_rewritten_page(env):
    """The Phase 5 concept-level Mermaid flowchart (question -> library/
    feed/web -> synthesis -> cited answer) was cut in the CFO-audience
    rewrite — a pipeline diagram is exactly the kind of mechanism-forward
    element the rewrite exists to remove. No Mermaid script, no flowchart
    source, no diagram frame should be reachable on this page."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "flowchart LR" not in body
    assert "mermaid" not in body.lower()
    assert "fpa-flow-diagram-frame" not in body
    assert "-.-> L" not in body


def test_which_engine_callout_moved_to_the_web_search_explainer(env):
    """Exa-vs-native-fallback detail is no longer duplicated here — it
    belongs to /how-this-is-built/web-search, which covers all four Exa
    call sites, not just this one. This page links there instead of
    re-explaining it."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "Which engine handled this answer?" not in body
    assert "Exa is the default." not in body
    assert '<a href="/how-this-is-built/web-search"' in body


def test_behind_the_archive_tool_inventory_is_gone(env):
    """The OpenAI-embedding/Wayback-Machine/structured-extraction/
    self-checking inventory was real mechanism detail, not what a CFO
    reader is on this page to learn — cut in the rewrite."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "Behind the archive" not in body
    assert "text-embedding-3-small" not in body
    assert "Wayback Machine" not in body


def test_citations_section_still_names_the_mechanism(env):
    """The rewrite folds the old two-callout citations explanation into one
    "Why the citations hold up" section — still real, still verifiable,
    just shorter."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "Why the citations hold up" in body
    assert "Anthropic" in body
    assert "citations turned on" in body


def test_what_it_wont_do_section_states_real_limits(env):
    """New in the rewrite: an explicit boundary section, replacing the
    implied guarantee of the old copy — the model is instructed to say so
    when sources don't cover a question, and can't cite outside the three
    listed sources."""
    c = _client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "What it won" in body  # tolerate either quoting/escaping of the apostrophe
    assert "sources don" in body
