"""The shared character budget (webapp.app._char_budget): a live count under
a capped text field, and no HTML maxlength, so a paste over the limit is kept
in the field and refused by the server instead of silently cut.

The first users are category_features.definition/pointer_note on the Manage
Features editor, its Add form, and the review-queue approve card. The browser
tests run against a real Chromium when one is available and skip otherwise
(qa.yml installs no browser)."""
import glob
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

LIMIT = 10_000


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _seed(db, definition="Short definition.", pointer_note=""):
    from linklib.db import Library
    lib = Library(db)
    try:
        cat = lib.add_tool_category("Close Management")
        fid = lib.add_category_feature(cat, "Continuous reconciliation", definition, pointer_note)
        tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["Close Management"], approved=1, summary="s")
        lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature", category_id=cat,
            payload={"category_id": cat,
                     "feature": {"name": "Proposed", "definition": "Proposed definition.", "pointer_note": ""},
                     "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0,
                                "verified_as_of": "2026-09-01", "note": "", "source_url": ""}]},
        )
        return cat, fid
    finally:
        lib.close()


def _stored_definition(db, fid):
    from linklib.db import Library
    lib = Library(db)
    try:
        return lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (fid,)).fetchone()[0]
    finally:
        lib.close()


# --- the helper itself ------------------------------------------------------

def test_counter_renders_the_limit_and_the_count(env):
    appmod, _db = env
    attrs, counter = appmod._char_budget(LIMIT, "a" * 1470, "f-1")
    assert 'data-char-limit="10000"' in attrs and 'aria-describedby="f-1-budget"' in attrs
    assert "maxlength" not in attrs
    assert "Limited to 10,000 characters." in counter
    assert "1,470 characters" in counter
    assert "char-budget-over" not in counter


def test_counter_over_the_limit_says_how_far_over(env):
    appmod, _db = env
    _attrs, counter = appmod._char_budget(LIMIT, "a" * (LIMIT + 250), "f-1")
    assert 'class="char-budget char-budget-over"' in counter
    assert "10,250 characters, 250 over. This save will be refused." in counter


def test_over_limit_color_is_the_alert_token_never_coral(env):
    appmod, _db = env
    rule = re.search(r"\.char-budget-over \.char-budget-count\{([^}]*)\}", appmod._CSS)
    assert rule, "no over-limit rule for the counter"
    assert "color:var(--alert)" in rule.group(1)
    assert "coral" not in rule.group(1)


def test_crlf_counts_once_on_both_sides(env):
    """A browser submits a textarea's line breaks as CRLF but counts them as
    one character. The server must count the same way, or a value the counter
    shows as under the limit would be refused."""
    from linklib.db import Library
    assert Library.text_budget_length("a\r\nb") == 3
    value = ("x" * 99 + "\r\n") * 100          # 10,000 as counted, 10,100 raw
    assert Library.text_budget_length(value) == LIMIT
    Library._check_category_feature_text(value, "")  # does not raise


# --- the three surfaces -----------------------------------------------------

def _budgeted_textareas(page_html):
    return re.findall(r'<textarea name="(definition|pointer_note)"([^>]*)>', page_html)


@pytest.mark.parametrize("path", ["/admin/tools/software/features?open_ids=1",
                                  "/admin/tools/software/feature-review-queue"])
def test_no_maxlength_remains_and_every_field_has_a_counter(env, path):
    appmod, db = env
    _seed(db)
    page = _client(appmod).get(path).text
    fields = _budgeted_textareas(page)
    assert fields, "no definition/pointer_note textareas rendered"
    for name, attrs in fields:
        assert "maxlength" not in attrs, f"{name} still carries a maxlength"
        assert 'data-char-limit="10000"' in attrs
        counter_id = re.search(r'data-char-budget="([^"]+)"', attrs).group(1)
        assert re.search(rf'<div id="{counter_id}" class="char-budget[^"]*">Limited to 10,000 characters\.', page)
    assert "<script>(function(){" in page and "data-char-limit" in page


def test_script_only_ships_on_pages_with_a_budgeted_field(env):
    appmod, _db = env
    c = _client(appmod)
    assert "function isOver(el)" not in c.get("/admin").text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_budget_js_is_valid_javascript(env, tmp_path):
    appmod, _db = env
    f = tmp_path / "budget.js"
    f.write_text(appmod._CHAR_BUDGET_JS, encoding="utf-8")
    subprocess.run(["node", "--check", str(f)], check=True)


def test_server_refuses_an_over_limit_save_and_keeps_the_stored_value(env):
    appmod, db = env
    cat, fid = _seed(db, definition="Keep me.")
    r = _client(appmod).post(f"/admin/tools/software/features/{fid}/edit", data={
        "category_id": str(cat), "name": "Continuous reconciliation",
        "definition": "p" * (LIMIT + 1), "pointer_note": "", "sort_order": "10",
    }, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert _stored_definition(db, fid) == "Keep me."


# --- live behavior in a real browser ---------------------------------------

def _launch_chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    candidates = [None] + sorted(glob.glob("/opt/pw-browsers/chromium*/chrome-linux/chrome"))
    for exe in candidates:
        try:
            return pw, pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception:
            continue
    pw.stop()
    return None


@pytest.fixture
def browser_page(env, tmp_path):
    launched = _launch_chromium()
    if launched is None:
        pytest.skip("Chromium not available in this environment")
    pw, browser = launched
    appmod, db = env
    cat, fid = _seed(db)
    html_path = tmp_path / "features.html"
    html_path.write_text(_client(appmod).get(f"/admin/tools/software/features?open_ids={cat}").text,
                         encoding="utf-8")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(html_path.as_uri(), wait_until="domcontentloaded")
    try:
        yield page, fid
    finally:
        page.close()
        browser.close()
        pw.stop()


def test_count_updates_as_you_type(browser_page):
    page, fid = browser_page
    ta = page.locator(f'textarea[name="definition"][form="feat-edit-{fid}"]')
    count = page.locator(f"#feat-{fid}-definition-budget .char-budget-count")
    assert count.inner_text() == "17 characters"   # "Short definition."
    ta.click()
    page.keyboard.press("End")
    page.keyboard.type(" More")
    assert count.inner_text() == "22 characters"


def test_paste_over_the_limit_is_kept_and_flagged(browser_page):
    page, fid = browser_page
    ta = page.locator(f'textarea[name="definition"][form="feat-edit-{fid}"]')
    counter = page.locator(f"#feat-{fid}-definition-budget")
    save = page.locator(f'button[form="feat-edit-{fid}"]')
    big = "z" * (LIMIT + 250)

    # Control: the same paste into a maxlength field is cut, so this method of
    # inserting text would have caught the old bug.
    ta.evaluate("el => { el.setAttribute('maxlength', '10000'); el.value = ''; }")
    ta.focus()
    page.keyboard.insert_text(big)
    assert len(ta.input_value()) == LIMIT
    ta.evaluate("el => { el.removeAttribute('maxlength'); el.value = ''; }")

    ta.focus()
    page.keyboard.insert_text(big)
    assert ta.input_value() == big                     # nothing cut
    assert counter.locator(".char-budget-count").inner_text() == \
        "10,250 characters, 250 over. This save will be refused."
    alert = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--alert').trim()")
    assert alert.lower() == "#9e3b30"
    color = counter.locator(".char-budget-count").evaluate("el => getComputedStyle(el).color")
    assert color == "rgb(158, 59, 48)"                 # --alert
    assert save.is_disabled() and save.inner_text() == "Over limit"

    # Back under the limit restores the button.
    ta.evaluate("el => { el.value = el.value.slice(0, 9000); el.dispatchEvent(new Event('input', {bubbles: true})); }")
    assert counter.locator(".char-budget-count").inner_text() == "9,000 characters"
    assert not save.is_disabled() and save.inner_text() == "Save"
