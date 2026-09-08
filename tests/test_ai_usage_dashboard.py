"""/admin/system/ai-usage — the AI usage/config dashboard.

A read-only index of which Claude/Exa/OpenAI surface uses which model or
mechanism, whether each is live-editable or code-only, and links out to
wherever it's actually changed (/admin/system/model, /admin/exa-settings,
/admin/checks). Built on a completed investigation (Step 0, approved) plus
#508 (model-config consolidation)/#509 (Exa pricing freshness)/#510 (Exa
cost tracking + settings-copy fix), which closed every gap that
investigation found — this page reads already-accurate data, it doesn't
compute anything new. No editing surface of its own.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.get("/admin/system/ai-usage", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


def test_renders_for_admin(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    assert r.status_code == 200
    assert "AI usage" in r.text


def test_claude_section_shows_all_three_surfaces_and_their_current_models(admin_client):
    """Enrichment reflects the live DB setting (default claude-opus-5);
    FP&A Buddy shows all three EFFORT_SETTINGS tier models; Matchmaker
    shows the shared DEFAULT_CHAT_MODEL — matching the confirmed facts
    from Step 0's drift-check, not hardcoded stale copies of them."""
    client, appmod, db = admin_client
    from linklib.agent import EFFORT_SETTINGS
    from linklib.models import DEFAULT_CHAT_MODEL

    r = client.get("/admin/system/ai-usage")
    html = r.text
    assert "claude-opus-5" in html  # default enrichment model
    assert EFFORT_SETTINGS["quick"]["model"] in html
    assert EFFORT_SETTINGS["standard"]["model"] in html
    assert EFFORT_SETTINGS["deep"]["model"] in html
    assert DEFAULT_CHAT_MODEL in html
    assert "Live" in html  # enrichment is live-editable
    assert "Code-only" in html  # Buddy/Matchmaker are not


def test_enrichment_model_reflects_a_changed_setting(admin_client):
    """This page reads the DB setting live, not a hardcoded default."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.set_enrich_model("claude-sonnet-5")
    lib.close()
    r = client.get("/admin/system/ai-usage")
    assert "claude-sonnet-5" in r.text


def test_exa_section_lists_all_four_call_sites(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    html = r.text
    assert "FP&amp;A Buddy web tier" in html
    assert "domain migration" in html.lower()
    assert "Medium-platform" in html
    assert "Feature Taxonomy vendor research" in html


def test_exa_feature_scan_call_site_is_explicitly_not_a_persistent_ledger(admin_client):
    """Approved instruction from the build brief: don't let the page imply
    all four Exa call sites are tracked identically — the feature-scan
    vendor-research call site's cost is per-run script output only, unlike
    the other three (which #510 wired into real DB ledgers)."""
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    html = r.text
    assert "Not a persistent ledger like the three above" in html
    assert "research_vendor_domain" in html
    # The other three call sites should each name a real, persistent table.
    assert "ask_questions" in html
    assert "content_refetch_log" in html


def test_exa_toggle_status_reflects_live_setting(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.set_exa_enabled(False)
    lib.close()
    r = client.get("/admin/system/ai-usage")
    assert "Off" in r.text


def test_openai_section_present(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    html = r.text
    assert "text-embedding-3-small" in html
    assert "article_embeddings.cost_usd" in html
    assert "ask_questions.embed_cost_usd" in html


def test_links_out_to_the_three_editable_pages_and_overhead_spend(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    html = r.text
    assert '/admin/system/model' in html
    assert '/admin/exa-settings' in html
    assert '/admin/checks#pricing-freshness' in html
    assert '/admin/checks#new-model-awareness' in html
    assert '/admin/checks#exa-pricing-freshness' in html
    assert '/admin/overhead-spend' in html


def test_no_editing_forms_on_this_page(admin_client):
    """Read-only per Step 0 decision 3 — no <form>/POST action anywhere on
    this page; every mutation happens on the pages it links to."""
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    assert "<form" not in r.text


def test_freshness_dots_link_to_checks_page_anchors(admin_client):
    """/admin/checks itself gets id attributes on its three freshness
    <h2> headings so this page's compact status dots can deep-link to the
    real banner/action, per Step 0 decision 2 (status glance only, no
    duplicated 'Mark reviewed' button on this page)."""
    client, appmod, db = admin_client
    checks_html = client.get("/admin/checks").text
    assert 'id="pricing-freshness"' in checks_html
    assert 'id="new-model-awareness"' in checks_html
    assert 'id="exa-pricing-freshness"' in checks_html


def test_freshness_dot_shows_never_reviewed_by_default(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/system/ai-usage")
    assert "never reviewed" in r.text.lower()


def test_freshness_dot_reflects_a_recent_mark_reviewed(admin_client):
    """Marking Pricing reviewed on /admin/checks (the only place that
    action lives) is picked up live by this page's own read of the same
    settings value — no separate/stale copy of the state."""
    client, appmod, db = admin_client
    r_before = client.get("/admin/system/ai-usage").text
    assert "Pricing: never reviewed" in r_before

    client.post("/admin/checks/mark-pricing-reviewed", follow_redirects=False)

    r_after = client.get("/admin/system/ai-usage").text
    assert "Pricing: never reviewed" not in r_after
    assert "reviewed just now" in r_after.lower()


def test_admin_nav_links_to_the_new_page(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin")
    assert "/admin/system/ai-usage" in r.text
