"""Follow-up to PR 2a.2: /admin/checks result column and the archive backup
log table use named widths, sized to their content.

  * checks: the result column is a fixed `_CHK_STATUS_COL_WIDTH`, not a third
    of the row, and the "(over the limit)" cell never wraps;
  * backup: Filename and Location are wide enough that a backup row is one
    line, short tokens do not wrap, Notes takes the rest, and the desktop
    min-width does not pin the stacked card layout under 700px.
"""
import glob
import importlib
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


@pytest.fixture
def admin(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    return c


def _over_limit_tool():
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Acme", "d", "https://acme.example", [], approved=1, summary="s")
    lib.close()
    c = sqlite3.connect(os.environ["LINKLIB_DB"])
    c.execute("UPDATE tools SET description=? WHERE id=?", ("x" * (Library.TOOL_DESCRIPTION_MAX + 50), tid))
    c.commit()
    c.close()


def _seed_backups():
    lib = Library(os.environ["LINKLIB_DB"])
    lib.record_backup_attempt("success", "library-20261002-000224.db", "1AbCdEfGhIjKlMnOp", 90_123_456, 4800)
    lib.close()


def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pass
    for path in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        try:
            return pw, pw.chromium.launch(executable_path=path)
        except Exception:
            continue
    pw.stop()
    return None


# --- /admin/checks -----------------------------------------------------------

def test_checks_result_column_is_a_named_fixed_width(app_module, admin):
    w = app_module._CHK_STATUS_COL_WIDTH
    assert w == 200
    html = admin.get("/admin/checks").text
    assert f"grid-template-columns:minmax(0,1fr) {w}px" in html
    assert ".chk-row{display:grid;grid-template-columns:minmax(0,2fr)" not in html
    # Below 760px the row still stacks to one column.
    assert "@media(max-width:760px){.chk-row{grid-template-columns:minmax(0,1fr);" in html


def test_over_limit_cell_does_not_wrap(app_module, admin):
    _over_limit_tool()
    html = admin.get("/admin/checks").text
    assert '<td style="white-space:nowrap;">3,500 (over the limit)</td>' in html


def test_checks_result_column_measures_200px_in_chromium(app_module, admin):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        _over_limit_tool()
        import tempfile as _t
        p = pathlib.Path(_t.mkdtemp()) / "checks.html"
        p.write_text(admin.get("/admin/checks").text, encoding="utf-8")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(p.as_uri())
        widths = page.evaluate(
            "()=>[...document.querySelectorAll('.chk-status')].map(e=>Math.round(e.getBoundingClientRect().width))")
        assert widths and set(widths) == {200}
        page.close()
    finally:
        browser.close()
        pw.stop()


# --- /admin/library-backup ---------------------------------------------------

def test_backup_columns_are_named_constants(app_module, admin):
    a = app_module
    _seed_backups()
    html = admin.get("/admin/library-backup").text
    assert f".backup-log-table .col-filename{{width:{a._BACKUP_COL_WIDTH_FILENAME}px;}}" in html
    assert f".backup-log-table .col-location{{width:{a._BACKUP_COL_WIDTH_LOCATION}px;}}" in html
    assert f"min-width:{a._BACKUP_TABLE_MIN_WIDTH}px" in html
    assert a._BACKUP_COL_WIDTH_FILENAME > 200 and a._BACKUP_COL_WIDTH_LOCATION > 120   # both grew
    for label in ("Filename", "Location", "Status"):
        assert f'data-label="{label}" class="nw"' in html
    # The stacked card layout drops the desktop floor so the card is not pinned wide.
    assert ".backup-log-table{min-width:0 !important;}" in html


def test_backup_row_is_one_line_in_chromium(app_module, admin):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        _seed_backups()
        import tempfile as _t
        p = pathlib.Path(_t.mkdtemp()) / "backup.html"
        p.write_text(admin.get("/admin/library-backup").text, encoding="utf-8")
        for width, one_line in ((1280, True), (390, None)):
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(p.as_uri())
            res = page.evaluate("""()=>{const t=document.querySelector('.backup-log-table');
              const tr=t.querySelector('tbody tr'); const tds=[...tr.children];
              return {h:Math.round(tr.getBoundingClientRect().height),
                      fnW:tds[1].getBoundingClientRect().width, fnScroll:tds[1].scrollWidth,
                      docOverflow:document.documentElement.scrollWidth>window.innerWidth}}""")
            assert not res["docOverflow"]
            if one_line:
                assert res["h"] < 50                      # single line, was 60 with the wrapped filename
                assert res["fnScroll"] <= res["fnW"] + 1  # the filename fits its column
            page.close()
    finally:
        browser.close()
        pw.stop()
