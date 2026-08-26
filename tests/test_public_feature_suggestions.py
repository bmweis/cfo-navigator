"""Public Feature Taxonomy suggestion channel (FEATURE_TAXONOMY.md §9) —
flag/question, designation-change, and new-feature proposals from a
signed-out visitor on the Software profile page, landing in
feature_review_queue with source='public'. Every submission goes through
/contact's exact spam-hardening shape (own rate-limit state, honeypot,
time-trap, keyword auto-reject) via a dedicated endpoint.
"""
import os
import pathlib
import sys
import tempfile
import time

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


def _past_ts():
    return str(time.time() - 10)


def _seed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.close()
    return cat_id, tool_id, fid


def test_profile_page_renders_suggest_entry_points(env):
    _seed(env)
    r = _client(env).get("/tools/software/rillet")
    assert r.status_code == 200
    assert "openFeatureSuggest" in r.text
    assert "Suggest one" in r.text
    assert 'id="feature-suggest-overlay"' in r.text


def test_flag_submission_requires_no_articulation_but_requires_name_and_email(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)

    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
        "note": "This looks stale for this vendor.",
        "submitter_name": "Jane Smith", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "suggested=1" in r.headers["location"]

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert len(pending) == 1
    item = pending[0]
    assert item["source"] == "public"
    assert item["proposal_type"] == "flag"
    assert item["tool_id"] == tool_id
    assert item["category_id"] == cat_id
    assert item["submitter_name"] == "Jane Smith"
    assert item["submitter_email"] == "jane@example.com"
    assert "stale" in item["articulation"]
    assert item["payload"]["links"] == []


def test_flag_submission_missing_email_rejected(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
        "submitter_name": "Jane Smith", "submitter_email": "",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_designation_change_requires_articulation(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "designation_change",
        "proposed_availability": "add_on", "proposed_ai_enabled": "1",
        "articulation": "", "submitter_name": "Jane Smith", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_designation_change_lands_in_queue_with_proposed_link(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "designation_change",
        "proposed_availability": "add_on", "proposed_ai_enabled": "1",
        "articulation": "This is actually a paid add-on now, not built in.",
        "submitter_name": "Jane Smith", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert len(pending) == 1
    item = pending[0]
    assert item["proposal_type"] == "designation_change"
    assert item["payload"]["feature_id"] == fid
    link = item["payload"]["links"][0]
    assert link["tool_id"] == tool_id
    assert link["availability"] == "add_on"
    assert link["ai_enabled"] == 1


def test_new_feature_proposal_requires_category_name_and_articulation(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": "0", "mode": "new_feature",
        "category_id": str(cat_id), "new_feature_name": "", "articulation": "why",
        "submitter_name": "Jane Smith", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_new_feature_proposal_lands_in_queue(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": "0", "mode": "new_feature",
        "category_id": str(cat_id), "new_feature_name": "Multi-Entity Consolidation",
        "new_feature_definition": "Rolls up financials across entities.",
        "articulation": "Every ERP tool I've compared has this and it's a differentiator.",
        "submitter_name": "Jane Smith", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert len(pending) == 1
    item = pending[0]
    assert item["proposal_type"] == "new_feature"
    assert item["payload"]["feature"]["name"] == "Multi-Entity Consolidation"
    assert item["payload"]["links"][0]["tool_id"] == tool_id


def test_honeypot_silently_drops_submission(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
        "note": "spam", "submitter_name": "Bot", "submitter_email": "bot@example.com",
        "ts": _past_ts(), "website": "http://spam.example",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "suggested=1" in r.headers["location"]

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert pending == []


def test_time_trap_silently_drops_submission(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
        "note": "too fast", "submitter_name": "Bot", "submitter_email": "bot@example.com",
        "ts": str(time.time()), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert pending == []


def test_spam_keyword_in_articulation_silently_dropped(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "designation_change",
        "proposed_availability": "add_on",
        "articulation": "improve your seo and boost your rankings",
        "submitter_name": "Bot", "submitter_email": "bot@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    pending = lib.list_feature_review_queue(status="pending")
    lib.close()
    assert pending == []


def test_rate_limit_blocks_after_threshold(env):
    cat_id, tool_id, fid = _seed(env)
    client = _client(env)
    limit = env.FEATURE_SUGGESTION_RATE_LIMIT_PER_HOUR
    for _ in range(limit):
        r = client.post("/tools/software/rillet/suggest-feature", data={
            "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
            "note": "x", "submitter_name": "Jane", "submitter_email": "jane@example.com",
            "ts": _past_ts(), "website": "",
        }, follow_redirects=False)
        assert r.status_code == 303
    r = client.post("/tools/software/rillet/suggest-feature", data={
        "tool_id": str(tool_id), "feature_id": str(fid), "mode": "flag",
        "note": "one too many", "submitter_name": "Jane", "submitter_email": "jane@example.com",
        "ts": _past_ts(), "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "suggest_error" in r.headers["location"]


def test_admin_queue_renders_public_flag_with_dismiss_not_approve(env):
    cat_id, tool_id, fid = _seed(env)
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_feature_review_queue_item(
        source="public", proposal_type="flag",
        payload={"category_id": cat_id, "feature_id": fid, "links": []},
        category_id=cat_id, tool_id=tool_id,
        articulation="This doesn't look right anymore.",
        submitter_name="Jane Smith", submitter_email="jane@example.com",
    )
    lib.close()

    client = _client(env)
    client.post("/login", data={"username": "admin", "password": "adminpass"})
    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Public suggestions" in r.text
    assert "Flag/question" in r.text
    assert "This doesn&#x27;t look right anymore." in r.text or "doesn&rsquo;t look right" in r.text or "doesn't look right" in r.text
    assert "Jane Smith" in r.text
    assert "Dismiss" in r.text


def test_admin_queue_renders_public_new_feature_with_approve(env):
    cat_id, tool_id, fid = _seed(env)
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_feature_review_queue_item(
        source="public", proposal_type="new_feature",
        payload={
            "category_id": cat_id,
            "feature": {"name": "Multi-Entity Consolidation", "definition": "", "pointer_note": ""},
            "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0,
                       "verified_as_of": "", "note": "", "source_url": ""}],
        },
        category_id=cat_id, tool_id=tool_id,
        articulation="Every ERP tool has this.",
        submitter_name="Jane Smith", submitter_email="jane@example.com",
    )
    lib.close()

    client = _client(env)
    client.post("/login", data={"username": "admin", "password": "adminpass"})
    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Multi-Entity Consolidation" in r.text
    assert "Approve" in r.text
