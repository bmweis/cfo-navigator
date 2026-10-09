"""PR 2a.2: the Software edit and add pages refuse an over-max save the same
way the Community edit page does (2a.1).

Before this PR an over-max Software save returned a bare JSON 400, named only
the first field, lost what was typed, and (the data-integrity bug) said
"Nothing was saved" after Description and Short summary HAD been written,
because `update_tool` ran before the Bottom line and Agent taxonomy limits were
checked. These tests pin the fixed contract:

  * all four limits are checked before any write, one 400, one banner naming
    every over-limit field with length, limit and overage;
  * the stored row is unchanged on refusal (asserted on the row, not just the
    status code);
  * the page is re-rendered from the submitted values, hidden drafted-this-
    session state included;
  * a visible reason sits under the disabled Save button;
  * Mark reviewed / Flag for review stay usable on an over-limit vendor.
"""
import glob
import importlib
import os
import sqlite3
import sys
import pathlib
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import tool_labels
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
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _lib():
    return Library(os.environ["LINKLIB_DB"])


def _tool(**kw):
    lib = _lib()
    tid = lib.add_tool("Acme", kw.get("description", "orig desc"), "https://acme.example", [],
                       approved=1, summary=kw.get("summary", "orig sum"))
    slug = lib.get_tool(tid)["slug"]
    lib.close()
    return tid, slug


def _row(tid):
    lib = _lib()
    try:
        return lib.get_tool(tid)
    finally:
        lib.close()


def _post(admin, slug, **over):
    data = dict(name="Acme", url="https://acme.example", description="NEW DESC", summary="NEW SUM", primary_category="FP&A")
    data.update(over)
    return admin.post(f"/tools/software/{slug}/edit", data=data, follow_redirects=False)


# --- nothing is written on refusal ------------------------------------------

@pytest.mark.parametrize("field,limit_attr", [
    ("competitive_differentiation", "TOOL_DIFFERENTIATION_MAX"),
    ("agent_taxonomy_note", "TOOL_AGENT_TAXONOMY_MAX"),
])
def test_over_max_later_field_writes_nothing(admin, field, limit_attr):
    """The data-integrity bug: description and summary were saved before the
    later fields' limits were checked."""
    tid, slug = _tool()
    r = _post(admin, slug, **{field: "x" * (getattr(Library, limit_attr) + 100)})
    assert r.status_code == 400
    after = _row(tid)
    assert after["description"] == "orig desc"
    assert after["summary"] == "orig sum"
    assert not (after.get(field) or "").strip()


def test_over_max_description_writes_nothing(admin):
    tid, slug = _tool()
    r = _post(admin, slug, description="x" * (Library.TOOL_DESCRIPTION_MAX + 1),
              competitive_differentiation="new bottom line")
    assert r.status_code == 400
    after = _row(tid)
    assert after["description"] == "orig desc"
    assert not (after.get("competitive_differentiation") or "").strip()


def test_refusal_is_an_html_page_not_bare_json(admin):
    _tid, slug = _tool()
    r = _post(admin, slug, competitive_differentiation="x" * 1300)
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]
    assert 'id="tool-edit-form"' in r.text


def test_banner_lists_every_over_limit_field_with_overage(admin):
    _tid, slug = _tool()
    r = _post(admin, slug,
              description="d" * (Library.TOOL_DESCRIPTION_MAX + 5),
              summary="s" * (Library.TOOL_SUMMARY_MAX + 7),
              agent_taxonomy_note="a" * (Library.TOOL_AGENT_TAXONOMY_MAX + 11),
              competitive_differentiation="b" * (Library.TOOL_DIFFERENTIATION_MAX + 13))
    assert r.status_code == 400
    html = r.text
    assert "Nothing was saved." in html
    for label, limit, over in (
        (tool_labels.DESCRIPTION, Library.TOOL_DESCRIPTION_MAX, 5),
        (tool_labels.SHORT_SUMMARY, Library.TOOL_SUMMARY_MAX, 7),
        (tool_labels.AGENT, Library.TOOL_AGENT_TAXONOMY_MAX, 11),
        (tool_labels.BOTTOM_LINE, Library.TOOL_DIFFERENTIATION_MAX, 13),
    ):
        assert f"<strong>{label}</strong>: {limit + over:,} characters, limit {limit:,} ({over:,} over)" in html


def test_refusal_rerenders_from_submitted_values(admin):
    _tid, slug = _tool()
    r = _post(admin, slug, description="MY TYPED DESCRIPTION", summary="MY TYPED SUMMARY",
              competitive_differentiation="y" * 1300,
              ai_drafted_fields="description,summary", ai_drafted_citations='[{"n":1}]',
              confirm_verified_fields="description")
    html = r.text
    assert "MY TYPED DESCRIPTION" in html and "MY TYPED SUMMARY" in html
    assert "orig desc" not in html
    assert 'name="ai_drafted_fields" value="description,summary"' in html
    assert "confirm-verified-fields" in html and 'value="description"' in html


def test_reason_line_sits_with_the_save_buttons(admin):
    tid, slug = _tool()
    html = admin.get(f"/tools/software/{slug}/edit").text
    assert 'data-char-reason-for="tool-edit-form"' in html


def test_under_limit_save_still_works(admin):
    tid, slug = _tool()
    r = _post(admin, slug)
    assert r.status_code == 303
    assert _row(tid)["description"] == "NEW DESC"


# --- /admin/tools/software/new ----------------------------------------------

def test_new_over_max_creates_nothing_and_shows_banner(admin):
    r = admin.post("/admin/tools/software/new", data=dict(
        name="Newco", url="https://newco.example", primary_category="FP&A",
        description="d" * (Library.TOOL_DESCRIPTION_MAX + 2),
        summary="s" * (Library.TOOL_SUMMARY_MAX + 3)), follow_redirects=False)
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]
    assert "Nothing was saved." in r.text
    assert f"<strong>Description</strong>: {Library.TOOL_DESCRIPTION_MAX + 2:,} characters" in r.text
    assert f"<strong>Short summary</strong>: {Library.TOOL_SUMMARY_MAX + 3:,} characters" in r.text
    lib = _lib()
    try:
        assert lib.find_tool_name_duplicate("Newco") is None
    finally:
        lib.close()


def test_new_page_has_reason_line(admin):
    html = admin.get("/admin/tools/software/new").text
    assert 'data-char-reason-for="tool-new-form"' in html


# --- Mark reviewed / Flag for review stay usable (regression pin) -----------

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


def test_review_buttons_stay_usable_on_an_over_limit_vendor(admin, tmp_path):
    """A vendor already stored over a limit disables Save, but the Verification
    status actions (separate forms bound with form=) must stay clickable. The
    shared buttonsFor fix covers this; this pins it for Software."""
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        tid, slug = _tool()
        c = sqlite3.connect(os.environ["LINKLIB_DB"])
        c.execute("UPDATE tools SET description=? WHERE id=?", ("x" * (Library.TOOL_DESCRIPTION_MAX + 50), tid))
        c.commit()
        c.close()
        p = tmp_path / "edit.html"
        p.write_text(admin.get(f"/tools/software/{slug}/edit").text, encoding="utf-8")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(p.as_uri())
        save = page.locator('button[form="tool-edit-form"]:has-text("Over limit")')
        assert save.count() >= 1
        assert all(save.nth(i).is_disabled() for i in range(save.count()))
        review = page.locator('button:has-text("Mark reviewed"), button:has-text("Flag for review")')
        assert review.count() >= 1
        assert all(review.nth(i).is_enabled() for i in range(review.count()))
        page.close()
    finally:
        browser.close()
        pw.stop()


# --- directory Quick edit shows the server's reason --------------------------

def test_quick_edit_over_max_returns_the_shared_message(admin):
    tid, _slug = _tool()
    r = admin.post(f"/admin/tools/software/{tid}/quick-edit", json={
        "description": "d" * (Library.TOOL_DESCRIPTION_MAX + 1), "summary": "ok"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert "Description is" in err and f"the limit is {Library.TOOL_DESCRIPTION_MAX:,}" in err
    assert _row(tid)["description"] == "orig desc"


def test_directory_quick_edit_js_shows_the_server_message(admin):
    """The old catch block replaced every server error with "Save failed, try
    again." The page script must surface the server's own message instead,
    and still parse (checked with node when it is installed)."""
    import re
    import shutil
    import subprocess
    html = admin.get("/tools/software").text
    assert "e.fromServer" in html and "err.fromServer = !!d.error" in html
    if shutil.which("node"):
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        js = max(scripts, key=len)
        res = subprocess.run(["node", "--check", "-"], input=js, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr
