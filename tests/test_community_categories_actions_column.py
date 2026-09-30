"""Community categories admin page: Save/Delete/Add live in their own
Actions column, and the Communities cell holds only the count."""
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
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Treasury")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _page(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c.get("/admin/tools/communities/categories").text


def _cells(row_html):
    return re.findall(r"<td\b.*?</td>", row_html, flags=re.S)


def test_actions_column_holds_save_delete_add(env):
    html = _page(env)
    heads = re.findall(r"<th\b[^>]*>(.*?)</th>", html, flags=re.S)
    assert [h.strip() for h in heads] == ["Name", "Description", "Communities", "Actions"]
    rows = re.findall(r"<tr\b.*?</tr>", html.split("<tbody>")[1], flags=re.S)
    add_row, cat_row = rows[0], rows[1]
    add_cells = _cells(add_row)
    assert len(add_cells) == 4
    assert "+ Add category" in add_cells[3]
    cells = _cells(cat_row)
    assert len(cells) == 4
    assert "communit" in cells[2] and "<button" not in cells[2] and "<form" not in cells[2]
    assert ">Save<" in cells[3] and ">Delete<" in cells[3]
    assert cells[3].index(">Save<") < cells[3].index(">Delete<")
