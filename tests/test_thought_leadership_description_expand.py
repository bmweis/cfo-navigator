"""Click-to-expand descriptions on /thought-leadership (see CLAUDE.md):
`description` was saved via the admin CRUD but never rendered on the public
page at all, in this build or the pre-migration thought_leadership_data.py
version. This adds a small per-entry toggle that reveals it inline, without
changing the page's default look.
"""
import pathlib
import sys
import tempfile
import os

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


def test_entry_with_description_gets_a_hidden_toggle(env):
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        lib.add_thought_leadership("press", "Has A Synopsis", url="https://example.com/x",
                                   date_label="Jan 2027", description="The real synopsis text.")
    finally:
        lib.close()

    c = _client(env)
    resp = c.get("/thought-leadership")
    assert resp.status_code == 200
    body = resp.text
    idx = body.index("Has A Synopsis")
    snippet = body[idx:idx + 700]

    # Toggle affordance present, description present but hidden by default —
    # no layout shift, no visible change until interaction.
    assert 'class="tl-col-item-toggle"' in snippet
    assert 'aria-expanded="false"' in snippet
    assert "The real synopsis text." in snippet
    desc_start = snippet.index('class="tl-col-item-desc"')
    assert "hidden" in snippet[desc_start:desc_start + 60]


def test_entry_without_description_has_no_toggle(env):
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        lib.add_thought_leadership("press", "No Synopsis Here", date_label="Feb 2027", description="")
    finally:
        lib.close()

    c = _client(env)
    resp = c.get("/thought-leadership")
    body = resp.text
    idx = body.index("No Synopsis Here")
    snippet = body[idx:idx + 400]
    assert "tl-col-item-toggle" not in snippet


def test_needs_synopsis_entry_has_no_toggle_even_with_stray_text(env):
    """needs_synopsis is a deliberate placeholder, not real content — no
    affordance should invite a click that reveals nothing meaningful, even
    in the edge case where description isn't blank."""
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        lib.add_thought_leadership("press", "Pending Synopsis Entry", date_label="Mar 2027",
                                   description="stray draft text", needs_synopsis=True)
    finally:
        lib.close()

    c = _client(env)
    resp = c.get("/thought-leadership")
    body = resp.text
    idx = body.index("Pending Synopsis Entry")
    snippet = body[idx:idx + 400]
    assert "tl-col-item-toggle" not in snippet


def test_multiple_entries_get_independent_toggle_ids(env):
    """Each entry's toggle/description pair must have a unique id so
    expanding one doesn't affect another — independent, simultaneous
    expand/collapse per entry."""
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        lib.add_thought_leadership("press", "First Entry", date_label="Jan 2027", description="First synopsis.")
        lib.add_thought_leadership("press", "Second Entry", date_label="Feb 2027", description="Second synopsis.")
    finally:
        lib.close()

    c = _client(env)
    resp = c.get("/thought-leadership")
    body = resp.text
    import re
    ids = re.findall(r'class="tl-col-item-desc" id="([^"]+)"', body)
    assert len(ids) == len(set(ids)), "duplicate description toggle ids found"
    assert len(ids) >= 2


def test_show_all_expand_collapse_unaffected(env):
    """The pre-existing 'Show all N' cap/expand mechanism for Speaking &
    Events / Podcasts must keep working unchanged alongside the new
    per-entry description toggles."""
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        for i in range(8):
            lib.add_thought_leadership("press", f"Press Item {i}", date_label="Jan 2027",
                                       description=f"Synopsis {i}.")
    finally:
        lib.close()

    c = _client(env)
    resp = c.get("/thought-leadership")
    body = resp.text
    assert "Show all 8" in body
    assert "toggleTLCol" in body
    assert "toggleTLDesc" in body


def test_thought_leadership_script_is_valid_js(env):
    """The inline <script> block (toggleTLCol + toggleTLDesc) must parse as
    valid JavaScript — mirrors the standing script-syntax-validation
    discipline (see CLAUDE.md) even though this route's script isn't one of
    the shared module-level *_JS constants tests/test_admin_js_syntax.py
    already covers."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    from webapp.checks import _node_check

    c = _client(env)
    resp = c.get("/thought-leadership")
    body = resp.text
    start = body.index("<script>", body.index("tl-cols"))
    end = body.index("</script>", start) + len("</script>")
    script_tag = body[start:end]
    js = script_tag[len("<script>"):-len("</script>")]

    err = _node_check(js)
    assert err is None, err
