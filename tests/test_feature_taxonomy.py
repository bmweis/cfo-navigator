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


def test_manage_features_add_edit_retire_flow(env):
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

    r = client.get(f"/admin/tools/software/features/{cat_id}", follow_redirects=False)
    assert r.status_code == 200

    r = client.post(f"/admin/tools/software/features/{cat_id}/new",
                     data={"name": "Real-Time Ledger", "definition": "", "pointer_note": ""},
                     follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        features = lib.list_category_features(cat_id)
        assert len(features) == 1
        fid = features[0]["id"]
    finally:
        lib.close()

    r = client.post(f"/admin/tools/software/features/{fid}/edit",
                     data={"return_to": str(cat_id), "name": "Real-Time Ledger", "definition": "d",
                           "pointer_note": "", "sort_order": "5"},
                     follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/admin/tools/software/features/{fid}/retire",
                     data={"return_to": str(cat_id)}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(env.DB_PATH)
    try:
        assert lib.list_category_features(cat_id) == []
    finally:
        lib.close()


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
