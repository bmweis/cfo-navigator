"""Models check as a diff (#629 Piece 2): live Anthropic list vs registry,
MODEL_PRICING and the FP&A Buddy tiers. Fail-first coverage."""
import importlib
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import models as models_mod
from linklib.lineup import lineup_findings

LIVE = {"claude-new-9": {"name": "New 9", "created": None},
        "claude-a": {"name": "A", "created": None}}
BASE = dict(registry_ids={"claude-a"}, tier_ids=set(), priced_ids={"claude-a"})


def _kinds(r):
    return {(f["id"], f["kind"]) for f in r["findings"]}


def test_live_model_unknown_everywhere_is_a_finding_that_names_the_missing_pricing():
    r = lineup_findings(LIVE, **BASE)
    assert r["compared"] and _kinds(r) == {("claude-new-9", "unknown")}
    assert "has no MODEL_PRICING row" in r["findings"][0]["message"]


def test_registry_model_without_pricing_is_a_non_ignorable_finding():
    r = lineup_findings({"claude-a": {"name": "A"}}, registry_ids={"claude-a"}, tier_ids=set(),
                        priced_ids=set(), ignored_ids={"claude-a"})
    assert _kinds(r) == {("claude-a", "no-pricing")}
    assert r["findings"][0]["ignorable"] is False  # ignoring cannot hide a silent fallback


def test_tier_model_without_pricing_or_not_listed_live_is_a_finding():
    r = lineup_findings({"claude-a": {"name": "A"}}, registry_ids={"claude-a"},
                        tier_ids={"claude-tier"}, priced_ids={"claude-a"})
    assert _kinds(r) == {("claude-tier", "no-pricing"), ("claude-tier", "not-live")}


def test_registry_model_not_in_a_tier_is_not_a_gap():
    assert lineup_findings({"claude-a": {"name": "A"}}, **BASE)["findings"] == []


def test_ignored_model_produces_no_finding():
    r = lineup_findings(LIVE, **BASE, ignored_ids={"claude-new-9"})
    assert r["compared"] and r["findings"] == []


def test_unreachable_api_says_it_could_not_compare_not_a_clean_pass():
    for live in (None, {}):
        r = lineup_findings(live, **BASE)
        assert r["compared"] is False and "could not be reached" in r["reason"]


@pytest.fixture
def admin(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    models_mod._cache.update({"data": None, "at": 0.0})   # module-level cache: reset per test
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield c, db
    models_mod._cache.update({"data": None, "at": 0.0})
    if os.path.exists(db):
        os.remove(db)


def _section(html):
    return html.split('id="new-model-awareness"')[1].split('id="exa-pricing-freshness"')[0]


def test_checks_page_shows_a_finding_then_the_not_using_decision_persists(admin, monkeypatch):
    c, db = admin
    monkeypatch.setattr(models_mod, "_live_models", lambda: dict(LIVE, **{"claude-sonnet-4-6": {"name": "S"}}))
    sec = _section(c.get("/admin/checks").text)
    assert "New 9 (claude-new-9) is live and has no MODEL_PRICING row" in sec
    # a blank reason is refused, and nothing is recorded
    assert c.post("/admin/checks/models-not-using/add", data={"model_id": "claude-new-9", "reason": " "},
                  follow_redirects=False).status_code == 400
    assert c.post("/admin/checks/models-not-using/add",
                  data={"model_id": "claude-new-9", "reason": "Too pricey for enrichment"},
                  follow_redirects=False).status_code == 303
    sec = _section(c.get("/admin/checks").text)
    assert "New 9 (claude-new-9) is live" not in sec           # finding gone
    assert "Too pricey for enrichment" in sec                  # but the record is visible
    c.post("/admin/checks/models-not-using/remove", data={"model_id": "claude-new-9"}, follow_redirects=False)
    assert "New 9 (claude-new-9) is live" in _section(c.get("/admin/checks").text)  # comes back


def test_checks_page_says_could_not_compare_when_the_api_is_unreachable(admin, monkeypatch):
    c, db = admin
    monkeypatch.setattr(models_mod, "_live_models", lambda: None)
    sec = _section(c.get("/admin/checks").text)
    assert "Could not compare" in sec


def test_not_using_routes_require_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    for path in ("add", "remove"):
        r = c.post(f"/admin/checks/models-not-using/{path}", data={"model_id": "x", "reason": "y"}, follow_redirects=False)
        assert r.status_code in (302, 303) and "/login" in r.headers["location"]
