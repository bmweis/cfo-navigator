"""Feature Taxonomy (2026-08, Phase 0 + 1 — docs/FEATURE_TAXONOMY.md is canon):
linklib.db's category_features / tool_feature_links / feature_review_queue
tables and helpers, the admin CRUD pages under /admin/tools/*, the per-tool
checklist on /tools/software/{slug}/edit, and scripts/seed_feature_taxonomy.py.
"""
import csv
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def lib(tmp_path):
    from linklib.db import Library
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


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


# -- linklib.db: category_features -------------------------------------------

def test_add_and_list_category_features(lib):
    cat_id = lib.add_tool_category("ERP")
    lib.add_category_feature(cat_id, "Real-Time Ledger", sort_order=10)
    lib.add_category_feature(cat_id, "Anomaly Detection", definition="Unusual patterns.", sort_order=5)
    features = lib.list_category_features(cat_id)
    assert [f["name"] for f in features] == ["Anomaly Detection", "Real-Time Ledger"]


def test_category_feature_name_unique_per_category_not_globally(lib):
    erp = lib.add_tool_category("ERP")
    close_mgmt = lib.add_tool_category("Close Management")
    lib.add_category_feature(erp, "Anomaly Detection")
    lib.add_category_feature(close_mgmt, "Anomaly Detection")  # same name, different category: fine
    with pytest.raises(ValueError):
        lib.add_category_feature(erp, "anomaly detection")  # case-insensitive dup within same category


def test_retire_category_feature_is_soft_and_keeps_links(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.retire_category_feature(fid)
    assert lib.list_category_features(cat_id) == []  # hidden from the live list
    assert lib.list_category_features(cat_id, include_retired=True)[0]["retired_at"]
    assert lib.list_tool_feature_links(tool_id)  # link survives untouched


def test_update_category_feature_rejects_dup_name(lib):
    cat_id = lib.add_tool_category("ERP")
    lib.add_category_feature(cat_id, "Real-Time Ledger")
    fid2 = lib.add_category_feature(cat_id, "Anomaly Detection")
    with pytest.raises(ValueError):
        lib.update_category_feature(fid2, "real-time ledger", "", "", 20)


# -- linklib.db: tool_feature_links -------------------------------------------

def test_upsert_tool_feature_link_insert_then_update(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    assert len(lib.list_tool_feature_links(tool_id)) == 1
    lib.upsert_tool_feature_link(tool_id, fid, "add_on", 1, "2026-08-20", note="tier-gated")
    links = lib.list_tool_feature_links(tool_id)
    assert len(links) == 1  # upsert, not a second row
    assert links[0]["availability"] == "add_on"
    assert links[0]["ai_enabled"] == 1
    assert links[0]["note"] == "tier-gated"


def test_upsert_tool_feature_link_rejects_bad_availability(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    with pytest.raises(ValueError):
        lib.upsert_tool_feature_link(tool_id, fid, "sort-of", 0, "2026-08-19")


def test_delete_tool_feature_link(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.delete_tool_feature_link(tool_id, fid)
    assert lib.list_tool_feature_links(tool_id) == []


def test_category_has_features_drives_legacy_coexistence(lib):
    erp = lib.add_tool_category("ERP")
    fpa = lib.add_tool_category("FP&A")
    lib.add_category_feature(erp, "Real-Time Ledger")
    assert lib.category_has_features(erp) is True
    assert lib.category_has_features(fpa) is False  # unseeded category: legacy Features card still renders


# -- linklib.db: additive category tagging (Close Management resolution) -----

def test_add_category_to_tool_is_additive_only(lib):
    lib.add_tool_category("Accounting")
    lib.add_tool_category("Close Management")
    tool_id = lib.add_tool("FloQast", "d", "https://floqast.com", ["Accounting"], approved=1, summary="s")
    added = lib.add_category_to_tool(tool_id, "Close Management")
    assert added is True
    assert lib.get_tool(tool_id)["categories"] == ["Accounting", "Close Management"]
    added_again = lib.add_category_to_tool(tool_id, "Close Management")
    assert added_again is False  # already tagged — idempotent, no duplicate


# -- linklib.db: feature_review_queue (rules doc §9) --------------------------

def test_review_queue_add_and_list_pending(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature+link",
        {"category_id": cat_id, "feature": {"name": "AI Bill Capture"},
         "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 1, "verified_as_of": "2026-08-19"}]},
        category_id=cat_id, tool_id=tool_id, articulation="Source: vendor site.",
    )
    pending = lib.list_feature_review_queue(status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == qid
    assert pending[0]["source"] == "scan"


def test_review_queue_rejects_bad_source(lib):
    with pytest.raises(ValueError):
        lib.add_feature_review_queue_item("robot", "new_feature", {})


def test_review_queue_approve_creates_feature_and_link(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature+link",
        {"category_id": cat_id, "feature": {"name": "AI Bill Capture"},
         "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 1, "verified_as_of": "2026-08-19"}]},
        category_id=cat_id, tool_id=tool_id,
    )
    result = lib.approve_feature_review_queue_item(qid)
    assert lib.get_category_feature(result["feature_id"])["name"] == "AI Bill Capture"
    assert lib.list_tool_feature_links(tool_id)[0]["ai_enabled"] == 1
    item = lib.get_feature_review_queue_item(qid)
    assert item["status"] == "approved"
    assert item["resolved_at"]


def test_review_queue_approve_with_override_marks_edited(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature+link",
        {"category_id": cat_id, "feature": {"name": "AI Bill Capture"},
         "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0, "verified_as_of": "2026-08-19"}]},
        category_id=cat_id, tool_id=tool_id,
    )
    override = {
        "category_id": cat_id, "feature": {"name": "AI Bill Capture (edited)"},
        "links": [{"tool_id": tool_id, "availability": "add_on", "ai_enabled": 1, "verified_as_of": "2026-08-20"}],
    }
    lib.approve_feature_review_queue_item(qid, override_payload=override)
    assert lib.get_feature_review_queue_item(qid)["status"] == "edited"
    links = lib.list_tool_feature_links(tool_id)
    assert links[0]["availability"] == "add_on"


def test_review_queue_deny_records_resolution_note(lib):
    cat_id = lib.add_tool_category("ERP")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature", {"category_id": cat_id, "feature": {"name": "X"}, "links": []},
        category_id=cat_id,
    )
    lib.deny_feature_review_queue_item(qid, "Not a real differentiator.")
    item = lib.get_feature_review_queue_item(qid)
    assert item["status"] == "denied"
    assert item["resolution_note"] == "Not a real differentiator."


def test_review_queue_cannot_resolve_twice(lib):
    cat_id = lib.add_tool_category("ERP")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature", {"category_id": cat_id, "feature": {"name": "X"}, "links": []},
        category_id=cat_id,
    )
    lib.deny_feature_review_queue_item(qid)
    with pytest.raises(ValueError):
        lib.deny_feature_review_queue_item(qid)
    with pytest.raises(ValueError):
        lib.approve_feature_review_queue_item(qid)


def test_approve_reuses_existing_feature_by_name_instead_of_duplicating(lib):
    cat_id = lib.add_tool_category("ERP")
    tool_a = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    tool_b = lib.add_tool("Campfire", "d", "https://campfire.ai", ["ERP"], approved=1, summary="s")
    lib.add_category_feature(cat_id, "Anomaly Detection")
    qid = lib.add_feature_review_queue_item(
        "scan", "new_feature+link",
        {"category_id": cat_id, "feature": {"name": "anomaly detection"},  # case-differs, same feature
         "links": [{"tool_id": tool_a, "availability": "native", "ai_enabled": 1, "verified_as_of": "2026-08-19"}]},
        category_id=cat_id,
    )
    result = lib.approve_feature_review_queue_item(qid)
    assert len(lib.list_category_features(cat_id)) == 1  # not duplicated
    lib.upsert_tool_feature_link(tool_b, result["feature_id"], "native", 1, "2026-08-19")
    assert len(lib.list_category_features(cat_id)) == 1


# -- admin routes --------------------------------------------------------------

def test_manage_features_admin_pages_require_auth(env):
    client = _client(env)
    r = client.get("/admin/tools/software/features", follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.get("/admin/tools/software/feature-review-queue", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_old_categories_url_is_gone_no_redirect(env):
    """The admin URL convention's Phase 1b PR 1 removed the interim
    /admin/tools/categories redirect (shipped in the Feature Taxonomy PR)
    outright, per Brian's explicit call — no legacy /admin/tools/* admin
    URL survives after that PR. The old URL is now a plain 404, not a
    redirect."""
    client = _client(env)
    r = client.get("/admin/tools/categories", follow_redirects=False)
    assert r.status_code == 404


def test_old_per_category_features_subpage_is_gone_no_redirect(env):
    """Phase 1c replaced the category-index + per-category-subpage pattern
    with one pivot table at the same base URL — the old
    /admin/tools/software/features/{category_id} subpage URL was never
    bookmarked/linked externally (Brian's explicit call), so this is a hard
    cutover with no redirect, same precedent as the categories-URL removal
    above."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("ERP")
    finally:
        lib.close()
    r = client.get(f"/admin/tools/software/features/{cat_id}", follow_redirects=False)
    assert r.status_code == 404


def test_manage_features_pivot_add_edit_retire_flow(env):
    """The pivot page (Phase 1c): one table for every category, a single add
    form with a category selector, per-row Save/Retire — replacing the old
    category-index + per-category-subpage flow this test used to cover."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("ERP")
    finally:
        lib.close()

    r = client.get("/admin/tools/software/features", follow_redirects=False)
    assert r.status_code == 200
    assert "ERP" in r.text
    assert "Add a feature" in r.text

    r = client.post("/admin/tools/software/features/new",
                     data={"category_id": str(cat_id), "name": "Real-Time Ledger",
                           "definition": "", "pointer_note": ""},
                     follow_redirects=False)
    assert r.status_code == 303
    # The redirect carries the new feature's category through open_ids so its
    # group re-opens on the pivot page, and preserves any filter state.
    assert f"open_ids={cat_id}" in r.headers["location"]

    lib = Library(env.DB_PATH)
    try:
        features = lib.list_category_features(cat_id)
        assert len(features) == 1
        fid = features[0]["id"]
    finally:
        lib.close()

    r = client.post(f"/admin/tools/software/features/{fid}/edit",
                     data={"category_id": str(cat_id), "name": "Real-Time Ledger", "definition": "d",
                           "pointer_note": "", "sort_order": "5"},
                     follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/admin/tools/software/features/{fid}/retire",
                     data={"category_id": str(cat_id)}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        assert lib.list_category_features(cat_id) == []
    finally:
        lib.close()


def test_manage_features_pivot_shows_pending_row_and_filter_controls(env):
    """Pending review-queue items surface as read-only rows inside their
    category's group (Phase 0 item 5c, approved as low-cost), and the page
    ships the filter/expand-all/collapse-all controls."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("ERP")
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": "Proposed Thing"}, "links": []},
            category_id=cat_id,
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/features", follow_redirects=False)
    assert r.status_code == 200
    assert "Proposed Thing" in r.text
    assert "Pending" in r.text
    assert "1 proposal" in r.text
    assert 'id="feat-cat-filter"' in r.text
    assert "featureTaxExpandAll" in r.text
    assert "featureTaxCollapseAll" in r.text


def test_tool_edit_page_shows_governed_checklist_only_for_seeded_categories(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        erp = lib.add_tool_category("ERP")
        lib.add_tool_category("FP&A")
        lib.add_category_feature(erp, "Real-Time Ledger")
        tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP", "FP&A"], approved=1, summary="s")
        slug = lib.get_tool(tool_id)["slug"]
    finally:
        lib.close()

    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert "Feature Taxonomy" in r.text
    assert "Real-Time Ledger" in r.text
    # FP&A has no curated features yet — no governed section for it, only ERP's.
    assert r.text.count('<h3 style="font-size:14px;font-weight:600;margin:0 0 4px;color:var(--navy);">') == 1


def test_tool_edit_page_save_feature_links_toggles_link(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        erp = lib.add_tool_category("ERP")
        fid = lib.add_category_feature(erp, "Real-Time Ledger")
        tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    finally:
        lib.close()

    r = client.post(f"/admin/tools/software/{tool_id}/feature-links/save", data={
        "feature_ids": [str(fid)],
        f"feature_{fid}_enabled": "1",
        f"feature_{fid}_availability": "native",
        f"feature_{fid}_ai_enabled": "1",
        f"feature_{fid}_verified_as_of": "2026-08-19",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        links = lib.list_tool_feature_links(tool_id)
        assert len(links) == 1 and links[0]["ai_enabled"] == 1
    finally:
        lib.close()

    # Un-checking removes the link.
    r = client.post(f"/admin/tools/software/{tool_id}/feature-links/save", data={
        "feature_ids": [str(fid)],
    }, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(env.DB_PATH)
    try:
        assert lib.list_tool_feature_links(tool_id) == []
    finally:
        lib.close()


def test_feature_review_queue_page_approve_and_deny(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("ERP")
        tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
        qid = lib.add_feature_review_queue_item(
            "scan", "new_feature+link",
            {"category_id": cat_id, "feature": {"name": "AI Bill Capture"},
             "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 1, "verified_as_of": "2026-08-19"}]},
            category_id=cat_id, tool_id=tool_id, articulation="Source: vendor site.",
        )
        qid2 = lib.add_feature_review_queue_item(
            "scan", "new_feature", {"category_id": cat_id, "feature": {"name": "Junk Proposal"}, "links": []},
            category_id=cat_id,
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "AI Bill Capture" in r.text
    assert "Junk Proposal" in r.text

    r = client.post(f"/admin/tools/software/feature-review-queue/{qid}/approve", data={
        "n_links": "1", "feature_name": "AI Bill Capture", "pointer_note": "",
        "link_0_tool_id": str(tool_id), "link_0_availability": "native",
        "link_0_ai_enabled": "1", "link_0_verified_as_of": "2026-08-19",
        "link_0_note": "", "link_0_source_url": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/admin/tools/software/feature-review-queue/{qid2}/deny",
                     data={"resolution_note": "Not curated-worthy."}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        assert lib.get_feature_review_queue_item(qid)["status"] == "approved"
        assert lib.get_feature_review_queue_item(qid2)["status"] == "denied"
        assert lib.list_category_features(cat_id)[0]["name"] == "AI Bill Capture"
    finally:
        lib.close()


def test_articulation_tool_coverage_splits_mentioned_and_unmentioned():
    import webapp.app as appmod
    mentioned, unmentioned = appmod._articulation_tool_coverage(
        "Merged across 5 tools. meow and lili both offer bookkeeping sync.",
        ["meow", "lili", "Mercury", "Pipe", "Novo"],
    )
    assert mentioned == ["meow", "lili"]
    assert unmentioned == ["Mercury", "Pipe", "Novo"]


def test_articulation_tool_coverage_case_insensitive_and_full_coverage():
    import webapp.app as appmod
    mentioned, unmentioned = appmod._articulation_tool_coverage(
        "MERCURY and rho both support this.", ["Mercury", "Rho"],
    )
    assert mentioned == ["Mercury", "Rho"]
    assert unmentioned == []


def test_feature_review_queue_page_flags_articulation_covering_only_some_linked_tools(env):
    """The real Neobanking case this was built for: a merge articulation
    named only 2 of 5 linked tools, with the other 3 silently unexplained
    — which turned out to be a genuine mismerge on manual review. This
    must now render as a visible warning before an admin approves it."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("Neobanking")
        tool_ids = {
            name: lib.add_tool(name, "d", f"https://{name.lower()}.example", ["Neobanking"], approved=1, summary="s")
            for name in ["meow", "lili", "Mercury", "Pipe", "Novo"]
        }
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+5 links",
            payload={"category_id": cat_id, "feature": {"name": "Financing services"},
                     "links": [{"tool_id": tid, "availability": "native", "ai_enabled": 0,
                                "verified_as_of": "2026-08-19"} for tid in tool_ids.values()]},
            category_id=cat_id, articulation="Merged across 5 tools. meow and lili both offer this.",
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Articulation names only 2 of 5 linked tools" in r.text
    assert "meow, lili" in r.text
    assert "Mercury, Pipe, Novo" in r.text
    assert "mismerge" in r.text


def test_feature_review_queue_page_no_coverage_warning_when_articulation_covers_all_tools(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("Neobanking")
        tool_a = lib.add_tool("Mercury", "d", "https://mercury.com", ["Neobanking"], approved=1, summary="s")
        tool_b = lib.add_tool("Rho", "d", "https://rho.co", ["Neobanking"], approved=1, summary="s")
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+2 links",
            payload={"category_id": cat_id, "feature": {"name": "Business bank accounts"},
                     "links": [{"tool_id": tool_a, "availability": "native", "ai_enabled": 0, "verified_as_of": "2026-08-19"},
                               {"tool_id": tool_b, "availability": "native", "ai_enabled": 0, "verified_as_of": "2026-08-19"}]},
            category_id=cat_id, articulation="Merged Mercury and Rho — same job, different mechanism.",
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Articulation names only" not in r.text


def test_feature_review_queue_card_labels_new_feature_fields(env):
    """The card's "Feature (NEW)" section used to stack the feature-name and
    pointer-note text inputs with no visible labels — distinguishable only
    by position. Both fields must now carry a real <label>, and neither
    input should still be full-width (narrowed per the request that
    prompted this)."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("Neobanking")
        tool_id = lib.add_tool("Mercury", "d", "https://mercury.com", ["Neobanking"], approved=1, summary="s")
        lib.add_feature_review_queue_item(
            "scan", "new_feature+link",
            {"category_id": cat_id, "feature": {"name": "Business bank accounts", "pointer_note": "core"},
             "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0, "verified_as_of": "2026-08-24"}]},
            category_id=cat_id, tool_id=tool_id,
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert ">Name</label>" in r.text
    assert "Pointer note" in r.text and "(optional)</span></label>" in r.text
    # Both inputs are narrowed, not full-width, per the request this fixed.
    assert 'name="feature_name" value="Business bank accounts" maxlength="500" style="width:320px' in r.text
    assert 'name="pointer_note" value="core" maxlength="500" style="width:320px' in r.text
    assert 'name="feature_name" value="Business bank accounts" maxlength="500" style="width:100%' not in r.text


def test_feature_review_queue_approve_shows_merge_confirmation_before_executing(env):
    """Phase 1c: approving a new_feature+link proposal whose name matches an
    existing feature in the category no longer merges silently — it shows a
    confirmation step first (no DB write yet), and only merges once that
    confirmation is submitted back with confirm_merge=1."""
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("ERP")
        tool_a = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
        tool_b = lib.add_tool("Campfire", "d", "https://campfire.ai", ["ERP"], approved=1, summary="s")
        existing_fid = lib.add_category_feature(cat_id, "Anomaly Detection")
        lib.upsert_tool_feature_link(tool_a, existing_fid, "native", 1, "2026-08-19")
        qid = lib.add_feature_review_queue_item(
            "scan", "new_feature+link",
            {"category_id": cat_id, "feature": {"name": "anomaly detection"},  # case-differs, same feature
             "links": [{"tool_id": tool_b, "availability": "native", "ai_enabled": 1, "verified_as_of": "2026-08-20"}]},
            category_id=cat_id,
        )
    finally:
        lib.close()

    approve_data = {
        "n_links": "1", "feature_name": "anomaly detection", "pointer_note": "",
        "link_0_tool_id": str(tool_b), "link_0_availability": "native",
        "link_0_ai_enabled": "1", "link_0_verified_as_of": "2026-08-20",
        "link_0_note": "", "link_0_source_url": "",
    }

    # First submit: no confirm_merge -> a confirmation page, not a merge.
    r = client.post(f"/admin/tools/software/feature-review-queue/{qid}/approve",
                     data=approve_data, follow_redirects=False)
    assert r.status_code == 200
    assert "Confirm merge" in r.text
    assert "Rillet" in r.text  # the vendor list naming who's already linked
    assert "confirm_merge" in r.text

    lib = Library(env.DB_PATH)
    try:
        assert lib.get_feature_review_queue_item(qid)["status"] == "pending"  # nothing executed yet
        assert len(lib.list_category_features(cat_id)) == 1  # no duplicate created either
    finally:
        lib.close()

    # Resubmit with confirm_merge=1 (what the confirmation page's "Merge"
    # button does) -> now it actually merges.
    r = client.post(f"/admin/tools/software/feature-review-queue/{qid}/approve",
                     data={**approve_data, "confirm_merge": "1"}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        assert lib.get_feature_review_queue_item(qid)["status"] in ("approved", "edited")
        assert len(lib.list_category_features(cat_id)) == 1  # still just one — merged, not duplicated
        assert lib.get_tool_feature_link(tool_b, existing_fid) is not None
    finally:
        lib.close()


def test_admin_dashboard_has_software_subgroup_with_renamed_cards(env):
    """Phase 1c: the four software-directory cards nest as their own
    collapsible "Software" sub-group inside CFO Toolbox (same mechanism as
    the pre-existing FP&A Buddy sub-group), with sentence-case card titles."""
    client = _client(env)
    _login(client)
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Software vendors" in r.text
    assert "Software categories" in r.text
    assert "Software features" in r.text
    assert "Feature review queue" in r.text
    # Old Title Case names are gone.
    assert "Toolbox categories" not in r.text
    assert ">Manage Features<" not in r.text
    assert ">Feature Review Queue<" not in r.text


# -- scripts.seed_feature_taxonomy: idempotency against the real CSVs --------

def test_seed_feature_taxonomy_idempotent_and_resolves_close_management(lib):
    """Full end-to-end run against the actual shipped seed CSVs, on a DB that
    mirrors production shape: the 15-tag tool_categories vocabulary plus the
    nine pilot tools under their real live names (including NetSuite's
    "(acquired by Oracle)" suffix). Verifies: categories resolve correctly
    (ERP/FP&A reused, Close Management created), row counts match the CSVs
    exactly, the additive-only Close Management tagging lands on exactly the
    three named tools without touching their existing tags, and a second run
    adds nothing new.
    """
    for name in ("Accounting", "ERP", "FP&A", "Procurement/Spend"):
        lib.add_tool_category(name)
    pilot_tools = {
        "Rillet": "ERP", "Campfire": "ERP", "NetSuite (acquired by Oracle)": "ERP",
        "Runway": "FP&A", "Abacum": "FP&A", "Aleph": "FP&A",
        "FloQast": "Accounting", "Numeric": "Accounting", "Ledge": "Accounting",
    }
    for name, cat in pilot_tools.items():
        lib.add_tool(name, "desc", f"https://{name.split(' ')[0].lower()}.example.com",
                     [cat], approved=1, summary="s")

    import scripts.seed_feature_taxonomy as seed_mod
    all_names = set(pilot_tools.values()) | {
        r["category"].strip() for r in seed_mod._read_csv(seed_mod._CATEGORY_FEATURES_CSV)
    } | {
        r["category"].strip() for r in seed_mod._read_csv(seed_mod._TOOL_FEATURE_LINKS_CSV)
    } | {
        r["category"].strip() for r in seed_mod._read_csv(seed_mod._REVIEW_QUEUE_CSV)
    }
    categories = seed_mod._resolve_categories(lib, all_names)
    assert "Close Management" in categories  # created, not aborted
    assert categories["ERP"] == lib.get_tool_category_id("ERP")  # reused, not duplicated

    cf_added, cf_skipped = seed_mod.seed_category_features(lib, categories)
    link_rows = seed_mod._read_csv(seed_mod._TOOL_FEATURE_LINKS_CSV)
    tools_by_category = {}
    for r in link_rows:
        tools_by_category.setdefault(r["category"].strip(), [])
        if r["tool"].strip() not in tools_by_category[r["category"].strip()]:
            tools_by_category[r["category"].strip()].append(r["tool"].strip())
    tfl_added, tfl_updated, tagged = seed_mod.seed_tool_feature_links(lib, categories)
    rq_added, rq_skipped = seed_mod.seed_review_queue(lib, categories, tools_by_category)

    with open(seed_mod._CATEGORY_FEATURES_CSV, newline="", encoding="utf-8") as f:
        expected_cf = sum(1 for _ in csv.DictReader(f))
    with open(seed_mod._TOOL_FEATURE_LINKS_CSV, newline="", encoding="utf-8") as f:
        expected_links = sum(1 for _ in csv.DictReader(f))
    with open(seed_mod._REVIEW_QUEUE_CSV, newline="", encoding="utf-8") as f:
        expected_rq = sum(1 for _ in csv.DictReader(f))
    assert cf_added == expected_cf
    assert tfl_added == expected_links
    assert rq_added == expected_rq
    assert tagged == 3  # FloQast, Numeric, Ledge

    for name in ("FloQast", "Numeric", "Ledge"):
        cats = lib.get_tool_by_name(name)["categories"]
        assert "Close Management" in cats
        assert "Accounting" in cats  # additive — existing tag kept

    # suite_note: NetSuite gets the placeholder; every other pilot tool is untouched.
    suite_note_set = seed_mod.seed_suite_notes(lib)
    assert suite_note_set is True
    assert lib.get_tool_by_name("NetSuite (acquired by Oracle)")["suite_note"] == seed_mod._NETSUITE_SUITE_NOTE
    for name in pilot_tools:
        if name != "NetSuite (acquired by Oracle)":
            assert lib.get_tool_by_name(name)["suite_note"] == ""

    # Re-run: fully idempotent, nothing new.
    cf_added2, cf_skipped2 = seed_mod.seed_category_features(lib, categories)
    tfl_added2, tfl_updated2, tagged2 = seed_mod.seed_tool_feature_links(lib, categories)
    rq_added2, rq_skipped2 = seed_mod.seed_review_queue(lib, categories, tools_by_category)
    suite_note_set2 = seed_mod.seed_suite_notes(lib)
    assert cf_added2 == 0 and cf_skipped2 == expected_cf
    assert tfl_added2 == 0 and tagged2 == 0
    assert rq_added2 == 0 and rq_skipped2 == expected_rq
    assert suite_note_set2 is False  # already set — never clobbers a since-edited note


def test_seed_suite_notes_never_clobbers_a_manually_edited_note(lib):
    """A re-run after Brian finalizes real copy through the admin edit form
    (set_tool_suite_note) must never stomp it back to the seed placeholder."""
    import scripts.seed_feature_taxonomy as seed_mod
    tool_id = lib.add_tool("NetSuite (acquired by Oracle)", "desc", "https://netsuite.example.com",
                            ["ERP"], approved=1, summary="s")
    lib.set_tool_suite_note(tool_id, "Brian's real, hand-edited copy.")
    assert seed_mod.seed_suite_notes(lib) is False
    assert lib.get_tool(tool_id)["suite_note"] == "Brian's real, hand-edited copy."


def test_seed_feature_taxonomy_aborts_on_unresolved_category(lib):
    import scripts.seed_feature_taxonomy as seed_mod
    with pytest.raises(SystemExit):
        seed_mod._resolve_categories(lib, {"ERP", "FP&A", "Close Management"})


# -- Feature Taxonomy scan tool, Phase 3 complement: near-duplicate nudge ----
# (webapp.app._find_near_duplicate_queue_items / the review-queue page's
# own warning badge) — source-agnostic: catches near-dupes whether they
# came from the scan's own conservative split, a second scan run, or an
# admin's manual entry, with no LLM call and no dependency on the scan
# having run at all.

def test_find_near_duplicate_queue_items_flags_similar_names_same_category(env):
    import webapp.app as appmod
    pending = [
        {"id": 1, "category_id": 7, "payload": {"feature": {"name": "Automated transaction categorization"}}},
        {"id": 2, "category_id": 7, "payload": {"feature": {"name": "Automatic transaction categorisation"}}},
    ]
    result = appmod._find_near_duplicate_queue_items(pending)
    assert 2 in [d["id"] for d in result.get(1, [])]
    assert 1 in [d["id"] for d in result.get(2, [])]


def test_find_near_duplicate_queue_items_ignores_different_categories(env):
    import webapp.app as appmod
    pending = [
        {"id": 1, "category_id": 7, "payload": {"feature": {"name": "Automated transaction categorization"}}},
        {"id": 2, "category_id": 8, "payload": {"feature": {"name": "Automated transaction categorization"}}},
    ]
    result = appmod._find_near_duplicate_queue_items(pending)
    assert result == {}


def test_find_near_duplicate_queue_items_ignores_dissimilar_names(env):
    import webapp.app as appmod
    pending = [
        {"id": 1, "category_id": 7, "payload": {"feature": {"name": "Checking accounts"}}},
        {"id": 2, "category_id": 7, "payload": {"feature": {"name": "Virtual card issuance"}}},
    ]
    result = appmod._find_near_duplicate_queue_items(pending)
    assert result == {}


def test_find_near_duplicate_queue_items_ignores_existing_feature_links():
    import webapp.app as appmod
    # A link-to-an-existing-feature proposal has no "feature" name of its
    # own to compare — must not crash, must not be treated as a match.
    pending = [
        {"id": 1, "category_id": 7, "payload": {"feature_id": 5, "links": []}},
        {"id": 2, "category_id": 7, "payload": {"feature": {"name": "Checking accounts"}}},
    ]
    result = appmod._find_near_duplicate_queue_items(pending)
    assert result == {}


def test_feature_review_queue_page_renders_near_duplicate_warning(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("Neobanking")
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": "Automated transaction categorization"}, "links": []},
            category_id=cat_id,
        )
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": "Automatic transaction categorisation"}, "links": []},
            category_id=cat_id,
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Possible near-duplicate of" in r.text


def test_feature_review_queue_page_no_warning_for_distinct_features(env):
    client = _client(env)
    _login(client)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        cat_id = lib.add_tool_category("Neobanking")
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": "Checking accounts"}, "links": []},
            category_id=cat_id,
        )
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": "Virtual card issuance"}, "links": []},
            category_id=cat_id,
        )
    finally:
        lib.close()

    r = client.get("/admin/tools/software/feature-review-queue")
    assert r.status_code == 200
    assert "Possible near-duplicate of" not in r.text
