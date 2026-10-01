"""/admin/checks "Profile fields over their limit": software and communities,
Type column, blocking items first, honest copy, wider Check column."""
import os
import re
import tempfile

import pytest


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def _seed(appmod, db):
    import sqlite3
    lib = appmod._lib()
    try:
        tid = lib.add_tool("Over Tool", "ok", "https://overtool.example.com", ["FP&A"], approved=1)
        cid = lib.add_community("Over Comm", "https://overcomm.example.com", "CFOs", "Free", [], approved=1)
        lib.upsert_community_profile(cid, ideal_member="short")
        tool = lib.get_tool(tid)
        comm = lib.get_community(cid)
    finally:
        lib.close()
    conn = sqlite3.connect(db)
    # Direct writes: the save-time guard would refuse these, which is the point.
    conn.execute("UPDATE tools SET description=? WHERE id=?",
                 ("d" * (appmod.Library.TOOL_DESCRIPTION_MAX + 5), tid))
    conn.execute("UPDATE community_profiles SET cpe_eligible=? WHERE community_id=?",
                 ("Yes (" + "n" * 45 + ")", cid))
    conn.commit()
    conn.close()
    return tool, comm


def test_row_lists_software_with_type_and_edit_link(env):
    client, appmod, db = env
    tool, comm = _seed(appmod, db)
    html = client.get("/admin/checks").text
    assert "<th>Type</th>" in html
    assert f'href="/tools/software/{tool["slug"]}/edit"' in html
    assert f'href="/tools/communities/{comm["slug"]}/edit"' in html
    assert ">Software<" in html and ">Community<" in html


def test_software_limit_comes_from_the_library_constant(env):
    client, appmod, db = env
    _seed(appmod, db)
    items = appmod._profile_fields_over_limit(appmod._lib())
    soft = [i for i in items if i["type"] == "Software"]
    assert soft and soft[0]["limit"] == appmod.Library.TOOL_DESCRIPTION_MAX


def test_blocking_items_sort_before_over_target(env):
    client, appmod, db = env
    _seed(appmod, db)
    kinds = [i["kind"] for i in appmod._profile_fields_over_limit(appmod._lib())]
    assert kinds == sorted(kinds, key=lambda k: k != "over the limit")
    assert kinds[0] == "over the limit" and kinds[-1] == "over the target"


def test_copy_says_text_stays_visible_but_cannot_be_saved(env):
    client, appmod, db = env
    html = client.get("/admin/checks").text
    assert "Nothing is changed or blocked" not in html
    assert "stays visible, but the field can't be saved until it is trimmed" in html


def test_check_column_is_wide_enough_for_the_long_label(env):
    client, appmod, db = env
    assert appmod._SUMMARY_COL_WIDTH_CHECK == "260px"
    html = client.get("/admin/checks").text
    assert re.search(r'<col style="width:260px;">', html)
