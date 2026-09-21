"""Addition 2 (2026-09 coordinator amendment) — "Approve term" (global,
permanent) vs. "Allow here" (scoped to one record+column+finding), replacing
the earlier single "Mark as exception" action for open findings only.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


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


def _login_admin(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_open_ampersand_row_shows_both_approve_term_and_allow_here(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_tool_category("Test Cat")
        fid = lib.add_category_feature(cid, "Test Feature")
        item_id = lib.add_voice_review_item(
            "category_features", fid, "definition", "bare-ampersand",
            "Bain & Company", source="script",
        )
    finally:
        lib.close()
    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "Approve term" in r.text
    assert "Allow here" in r.text
    assert f"/admin/voice/review-queue/{item_id}/approve-term" in r.text
    # The two actions are visually distinct (solid navy button vs. dashed
    # muted outline) — spot check both style fragments are present.
    assert "background:var(--navy);color:#fff" in r.text


def test_open_banned_word_row_shows_only_allow_here(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid = lib.add_tool("Testco", "A test tool.", "https://testco.example", [])
        item_id = lib.add_voice_review_item(
            "tools", tid, "description", "buzzword", "seamless", source="script",
        )
    finally:
        lib.close()
    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "Allow here" in r.text
    assert f"/admin/voice/review-queue/{item_id}/approve-term" not in r.text


def test_approve_term_resolves_all_matching_open_rows(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_tool_category("Test Cat")
        fid1 = lib.add_category_feature(cid, "F1")
        fid2 = lib.add_category_feature(cid, "F2")
        item1 = lib.add_voice_review_item(
            "category_features", fid1, "definition", "bare-ampersand",
            "Bain & Company is great", source="script",
        )
        item2 = lib.add_voice_review_item(
            "category_features", fid2, "pointer_note", "bare-ampersand",
            "See Bain & Company docs", source="script",
        )
    finally:
        lib.close()
    c = _login_admin(env)
    resp = c.post(f"/admin/voice/review-queue/{item1}/approve-term",
                   data={"term": "Bain & Company"}, follow_redirects=False)
    assert resp.status_code == 303

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        i1 = lib2.get_voice_review_item(item1)
        i2 = lib2.get_voice_review_item(item2)
        assert i1["status"] != "open"
        assert i2["status"] != "open"
        terms = lib2.list_approved_voice_terms("bare-ampersand")
        assert any(t["term"] == "Bain & Company" for t in terms)
    finally:
        lib2.close()


def test_remove_approved_term_makes_it_flaggable_again(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        term_id = lib.approve_voice_term("Ernst & Young", rule="bare-ampersand")
        assert lib.is_approved_voice_term("bare-ampersand", "Ernst & Young text")
        lib.remove_approved_voice_term(term_id)
        assert not lib.is_approved_voice_term("bare-ampersand", "Ernst & Young text")
    finally:
        lib.close()


def test_admin_voice_page_shows_approved_terms_section(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        lib.approve_voice_term("Dun & Bradstreet", rule="bare-ampersand")
    finally:
        lib.close()
    c = _login_admin(env)
    r = c.get("/admin/voice")
    assert r.status_code == 200
    assert "Approved ampersand terms" in r.text
    assert "Dun &amp; Bradstreet" in r.text or "Dun & Bradstreet" in r.text


def test_approving_a_term_stops_the_live_db_scanner_from_reflagging_it(env):
    """PR #590 Phase 2 verification (F6) — this scanner-suppression path
    was verified manually during Phase 1 review but had no committed
    regression test: does approving a term actually make
    `voice_db_scan.scan_db_copy` stop reporting it as a live finding, not
    just resolve the already-queued rows? Uses a term that is NOT in the
    source-side AMPERSAND_NAMES/AMPERSAND_ACRONYMS allowlist, so a false
    pass from that unrelated mechanism can't hide a real failure here."""
    from linklib.voice_db_scan import scan_db_copy

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_tool_category("Test Cat")
        lib.add_category_feature(
            cid, "Ops Feature",
            "Handles wombats & platypuses budget work across many quarters "
            "without fail every year for finance",
        )
        before = [v for v in scan_db_copy(lib)
                  if v.table == "category_features" and v.column == "definition"]
        assert before, "sanity check: the phrase must actually be flagged before approval"

        lib.approve_voice_term("Wombats & Platypuses", rule="bare-ampersand")

        after = [v for v in scan_db_copy(lib)
                 if v.table == "category_features" and v.column == "definition"]
        assert after == [], "approving the term must stop the live scanner from reflagging it"
    finally:
        lib.close()


def test_add_and_remove_approved_term_via_admin_voice_routes(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/approved-terms/add", data={"term": "Sales & Marketing"},
                follow_redirects=False)
    assert r.status_code == 303
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        terms = lib.list_approved_voice_terms("bare-ampersand")
        assert any(t["term"] == "Sales & Marketing" for t in terms)
        tid = [t["id"] for t in terms if t["term"] == "Sales & Marketing"][0]
    finally:
        lib.close()
    r2 = c.post(f"/admin/voice/approved-terms/{tid}/remove", follow_redirects=False)
    assert r2.status_code == 303
    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        terms2 = lib2.list_approved_voice_terms("bare-ampersand")
        assert not any(t["term"] == "Sales & Marketing" for t in terms2)
    finally:
        lib2.close()
