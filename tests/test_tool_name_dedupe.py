"""Name-based duplicate detection for the Software directory: exact-match
normalize_tool_name() (case, parentheticals, entity suffixes only — no
fuzzy/spelling/spacing matching), the non-blocking warn on save, and the
/admin/tools/software/name-duplicates review+resolve mechanism (tool_name_dedupe_decisions,
keyed on the sorted tool id pair so a later rename can't re-open a resolved pair)."""
import os
import tempfile

import pytest

from linklib.db import Library, normalize_tool_name


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


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


# --- normalize_tool_name() ---------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Dealhub", "dealhub"),
    ("DealHub", "dealhub"),
    ("DEALHUB", "dealhub"),
    ("Dealhub Inc", "dealhub"),
    ("Dealhub, Inc.", "dealhub"),
    ("Dealhub LLC", "dealhub"),
    ("Dealhub Ltd", "dealhub"),
    ("Dealhub Corp", "dealhub"),
    ("Dealhub Co", "dealhub"),
    ("Dealhub (acquired by Insightsoftware)", "dealhub"),
    ("  Dealhub  ", "dealhub"),
])
def test_normalize_tool_name_variants(raw, expected):
    assert normalize_tool_name(raw) == expected


def test_normalize_tool_name_does_not_fuzzy_match():
    # Spelling/spacing variants are explicitly out of scope for this pass.
    assert normalize_tool_name("Deal Hub") != normalize_tool_name("Dealhub")
    assert normalize_tool_name("Dealhb") != normalize_tool_name("Dealhub")


def test_normalize_tool_name_blank():
    assert normalize_tool_name("") == ""
    assert normalize_tool_name("   ") == ""


# --- Library.find_tool_name_duplicate (save-time warn) -----------------------

def test_find_tool_name_duplicate_matches_normalized(lib):
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    dup = lib.find_tool_name_duplicate("Dealhub Inc")
    assert dup is not None
    assert dup["name"] == "Dealhub"


def test_find_tool_name_duplicate_none_for_distinct_names(lib):
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    assert lib.find_tool_name_duplicate("Coefficient") is None


def test_find_tool_name_duplicate_excludes_self_on_edit(lib):
    tool_id = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    assert lib.find_tool_name_duplicate("Dealhub", exclude_id=tool_id) is None


def test_find_tool_name_duplicate_does_not_block_add(lib):
    """Unlike the URL check, this never raises — add_tool succeeds even with
    a matching name; the caller decides what to do with the warning."""
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    tool_id = lib.add_tool("Dealhub Inc", "desc2", "https://dealhub-other.example", [], approved=1)
    assert lib.get_tool(tool_id)["name"] == "Dealhub Inc"


# --- candidate scan + resolve mechanism ---------------------------------------

def test_find_tool_name_duplicate_candidates_finds_pair(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    candidates = lib.find_tool_name_duplicate_candidates()
    assert len(candidates) == 1
    ids = {candidates[0]["tool_a"]["id"], candidates[0]["tool_b"]["id"]}
    assert ids == {a, b}


def test_find_tool_name_duplicate_candidates_empty_for_distinct_tools(lib):
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.add_tool("Coefficient", "desc", "https://coefficient.io", [], approved=1)
    assert lib.find_tool_name_duplicate_candidates() == []


def test_resolved_pair_stops_resurfacing(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "dismissed")
    assert lib.find_tool_name_duplicate_candidates() == []


def test_resolve_order_independent(lib):
    """Recording (b, a) resolves the same pair as (a, b)."""
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(b, a, "duplicate")
    assert lib.find_tool_name_duplicate_candidates() == []
    decisions = lib.tool_name_dedupe_decisions()
    assert len(decisions) == 1
    assert decisions[0]["verdict"] == "duplicate"


def test_redeciding_a_pair_overwrites_verdict(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "dismissed")
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    decisions = lib.tool_name_dedupe_decisions()
    assert len(decisions) == 1
    assert decisions[0]["verdict"] == "duplicate"


def test_rename_does_not_reopen_a_resolved_pair(lib):
    """Dismissal is keyed on the tool id pair, not the normalized name — a
    later rename of one side must not un-dismiss it."""
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "dismissed")
    lib.update_tool(b, "Dealhub", "desc", "https://dealhub-other.example", [])
    assert lib.find_tool_name_duplicate_candidates() == []


def test_three_way_normalized_match_produces_all_pairs(lib):
    a = lib.add_tool("Dealhub", "desc", "https://a.example", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://b.example", [], approved=1)
    c = lib.add_tool("DEALHUB", "desc", "https://c.example", [], approved=1)
    candidates = lib.find_tool_name_duplicate_candidates()
    assert len(candidates) == 3  # C(3,2)
    pair_ids = {frozenset([cand["tool_a"]["id"], cand["tool_b"]["id"]]) for cand in candidates}
    assert pair_ids == {frozenset([a, b]), frozenset([a, c]), frozenset([b, c])}


def test_delete_tool_cascades_dedupe_decision_cleanup(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    assert len(lib.tool_name_dedupe_decisions()) == 1
    lib.delete_tool(b)
    assert lib.tool_name_dedupe_decisions() == []


# --- Route-level: save-time warn ----------------------------------------------

def test_admin_new_tool_warns_on_name_duplicate_but_saves(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/new", data={"primary_category": "FP&A", 
        "name": "Dealhub Inc", "url": "https://dealhub-other.example",
        "description": "desc", "summary": "desc",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "warn=" in r.headers["location"]

    lib = Library(db)
    names = {t["name"] for t in lib.list_tools(approved_only=False)}
    lib.close()
    assert "Dealhub Inc" in names  # the save was not blocked


def test_admin_new_tool_no_warn_for_distinct_name(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/new", data={"primary_category": "FP&A", 
        "name": "Coefficient", "url": "https://coefficient.io",
        "description": "desc", "summary": "desc",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "warn=" not in r.headers["location"]


def test_admin_edit_tool_warns_on_name_duplicate_but_saves(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    tool_b = lib.add_tool("Tool B", "desc", "https://b.example", [], approved=1)
    tool_b_slug = lib.get_tool(tool_b)["slug"]
    lib.close()

    r = client.post(f"/tools/software/{tool_b_slug}/edit", data={"primary_category": "FP&A", 
        "name": "Dealhub Inc", "url": "https://b.example", "description": "desc", "summary": "desc",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "warn=" in r.headers["location"]

    lib = Library(db)
    assert lib.get_tool(tool_b)["name"] == "Dealhub Inc"  # the save was not blocked
    lib.close()


def test_admin_edit_tool_no_warn_against_itself(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_id = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    r = client.post(f"/tools/software/{tool_slug}/edit", data={"primary_category": "FP&A", 
        "name": "Dealhub Inc", "url": "https://dealhub.io", "description": "new desc", "summary": "new desc",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "warn=" not in r.headers["location"]


# --- Library.pending_tool_name_merges (legacy 'duplicate' rows) --------------

def test_pending_tool_name_merges_surfaces_legacy_duplicate_verdict(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    pending = lib.pending_tool_name_merges()
    assert len(pending) == 1
    ids = {pending[0]["tool_a"]["id"], pending[0]["tool_b"]["id"]}
    assert ids == {a, b}


def test_pending_tool_name_merges_excludes_dismissed(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "dismissed")
    assert lib.pending_tool_name_merges() == []


def test_pending_tool_name_merges_empty_once_deleted(lib):
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    lib.delete_tool(b)
    assert lib.pending_tool_name_merges() == []


# --- Route-level: admin review + resolve --------------------------------------

def test_name_duplicates_page_lists_candidate(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.close()

    r = client.get("/admin/tools/software/name-duplicates")
    assert r.status_code == 200
    assert "Dealhub" in r.text
    assert 'Keep &quot;Dealhub&quot;' in r.text
    assert 'Keep &quot;Dealhub Inc&quot;' in r.text
    assert "Not a duplicate, dismiss" in r.text


def test_name_duplicates_page_shows_pending_merges_section(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    lib.close()

    r = client.get("/admin/tools/software/name-duplicates")
    assert r.status_code == 200
    assert "Confirmed duplicates awaiting cleanup" in r.text


def test_name_duplicates_page_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.get("/admin/tools/software/name-duplicates", follow_redirects=False)
    assert r.status_code == 303
    if os.path.exists(db):
        os.remove(db)


def test_resolve_dismiss_removes_candidate_from_list(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/name-duplicates/resolve", data={
        "tool_id_a": a, "tool_id_b": b,
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    assert lib.find_tool_name_duplicate_candidates() == []
    decisions = lib.tool_name_dedupe_decisions()
    assert decisions[0]["verdict"] == "dismissed"
    lib.close()


def test_resolve_rejects_invalid_tool_ids(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/tools/software/name-duplicates/resolve", data={
        "tool_id_a": "not-a-number", "tool_id_b": "1",
    })
    assert r.status_code == 400


def test_resolve_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.post("/admin/tools/software/name-duplicates/resolve", data={"tool_id_a": 1, "tool_id_b": 2})
    assert r.status_code == 401
    if os.path.exists(db):
        os.remove(db)


# --- Route-level: admin merge (confirm-a-duplicate deletes immediately) ------

def test_merge_keeps_one_and_deletes_the_other(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/name-duplicates/merge", data={
        "keep_id": a, "delete_id": b,
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]

    lib = Library(db)
    assert lib.get_tool(a) is not None
    assert lib.get_tool(b) is None
    lib.close()


def test_merge_removes_the_pair_from_candidates(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.close()

    client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": a, "delete_id": b})

    lib = Library(db)
    assert lib.find_tool_name_duplicate_candidates() == []
    assert lib.pending_tool_name_merges() == []
    lib.close()


def test_merge_resolves_a_pending_legacy_merge(admin_client):
    """A legacy 'duplicate'-verdict row (confirmed before this action deleted
    anything) is cleaned up like any other merge."""
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    b = lib.add_tool("Dealhub Inc", "desc", "https://dealhub-other.example", [], approved=1)
    lib.record_tool_name_dedupe_decision(a, b, "duplicate")
    lib.close()

    r = client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": a, "delete_id": b}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    assert lib.pending_tool_name_merges() == []
    assert lib.get_tool(a) is not None
    assert lib.get_tool(b) is None
    lib.close()


def test_merge_rejects_same_keep_and_delete_id(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": a, "delete_id": a})
    assert r.status_code == 400


def test_merge_rejects_nonexistent_tool(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    a = lib.add_tool("Dealhub", "desc", "https://dealhub.io", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": a, "delete_id": 999999})
    assert r.status_code == 404


def test_merge_rejects_invalid_ids(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": "x", "delete_id": "1"})
    assert r.status_code == 400


def test_merge_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.post("/admin/tools/software/name-duplicates/merge", data={"keep_id": 1, "delete_id": 2})
    assert r.status_code == 401
    if os.path.exists(db):
        os.remove(db)
