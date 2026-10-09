""""Save and continue" on the Software edit page (tool-edit-consistency
Phase 0, item #7 — approved: a second button placed right after Description,
not just at the very bottom of a long page). The mechanism itself
(save_action=continue -> redirect back to the same edit page instead of the
admin list) already existed and was already wired up at the bottom of the
page alongside "Save changes"; a live check on Abacum's edit page found no
second button near Description, and no PR summary mentioning it shipped
there — this covers the fix: a second submit button, same form/name/value,
placed immediately after the Description field so a multi-field editing
pass near the top of a long page doesn't require scrolling to the bottom
just to save without losing place.
"""
import os
import pathlib
import sys
import tempfile

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


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_save_and_continue_button_appears_right_after_description(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Abacum", "A tool.", "https://abacum.co", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/abacum/edit")
    html = r.text

    # Two "Save and continue" buttons now: one right after Description, one
    # in the page-bottom action row alongside "Save changes" (unchanged).
    assert html.count("Save and continue") == 2

    desc_idx = html.index('name="description"')
    first_continue_idx = html.index("Save and continue")
    save_changes_idx = html.index("Save changes")
    # The near-Description button comes before "Save changes" at the
    # bottom — i.e. it's genuinely placed right after the field, not just
    # a second copy of the bottom row.
    assert desc_idx < first_continue_idx < save_changes_idx


def test_save_and_continue_redirects_back_to_same_edit_page(env):
    """Whichever button posts it, save_action=continue keeps the admin on
    the same tool's edit page (fresh data) rather than bouncing to the
    admin list — the existing route behavior, now with real test coverage."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Abacum", "A tool.", "https://abacum.co", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post("/tools/software/abacum/edit", data={"primary_category": "FP&A", 
        "name": "Abacum", "url": "https://abacum.co", "description": "Updated description.",
        "summary": "Updated short.", "categories": ["FP&A"],
        "save_action": "continue",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tools/software/abacum/edit"


def test_save_changes_without_continue_redirects_to_list(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Abacum", "A tool.", "https://abacum.co", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post("/tools/software/abacum/edit", data={"primary_category": "FP&A", 
        "name": "Abacum", "url": "https://abacum.co", "description": "Updated description.",
        "summary": "Updated short.", "categories": ["FP&A"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tools/software"
