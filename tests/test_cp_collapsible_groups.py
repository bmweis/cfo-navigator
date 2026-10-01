"""Community profile edit page: collapsible field groups.

Five native <details> groups, all collapsed on load; an over-limit flag on each
group header; a refused save re-opens only the groups holding an over-max
field; a required field in a closed group opens it and takes focus; the
disabled "Over limit" buttons show not-allowed with a visible reason beside
them; the CPE control gives the note input the width. Live-browser tests skip
cleanly where no Chromium is available.
"""
import glob
import importlib
import os
import pathlib
import re
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import compare
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
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    return c


def _community(over_max_field=None, over_target_field=None):
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(name="Chief", url="https://chief.com", demographic="", cost_band="Paid",
                            categories=[], approved=1, sponsorship_type="Independent", sponsor_name="",
                            access="Application", format="Hybrid", reach="National", featured=0, advisor=0)
    lib.upsert_community_profile(cid, ideal_member="Senior operators.", verdict_summary="Solid.",
                                 cpe_eligible="Yes (NASBA sponsor)")
    slug = lib.get_community(cid)["slug"]
    lib.close()
    conn = sqlite3.connect(os.environ["LINKLIB_DB"])
    if over_max_field:
        conn.execute(f"UPDATE community_profiles SET {over_max_field}=? WHERE community_id=?", ("x" * 900, cid))
    if over_target_field:
        conn.execute(f"UPDATE community_profiles SET {over_target_field}=? WHERE community_id=?", ("y" * 650, cid))
    conn.commit()
    conn.close()
    return cid, slug


def _details(html):
    return re.findall(r'<details class="cp-group"[^>]*>', html)


def test_all_five_groups_load_collapsed_even_with_an_over_limit_field(admin):
    _cid, slug = _community(over_max_field="value_prop")
    html = admin.get(f"/tools/communities/{slug}/edit").text
    tags = _details(html)
    assert len(tags) == 5
    assert all(" open" not in t for t in tags), tags


def test_group_titles_are_the_public_names(admin):
    _cid, slug = _community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    titles = re.findall(r'<h2 class="cp-group-title">([^<]+)</h2>', html)
    assert titles == [t for t, _f in compare.community_admin_groups()]


def test_group_header_flags_over_max_and_over_target_with_counts(admin):
    _cid, slug = _community(over_max_field="value_prop", over_target_field="anti_fit")
    html = admin.get(f"/tools/communities/{slug}/edit").text
    first = html.split('<details class="cp-group"')[1]
    summary = first.split("</summary>")[0]
    assert "1 over limit" in summary and "1 over target" in summary
    # a clean group carries an empty flag container, no chips
    other = html.split('<details class="cp-group"')[2].split("</summary>")[0]
    assert "over limit" not in other and "over target" not in other


def test_header_has_an_empty_actions_slot_for_a_per_group_button(admin):
    _cid, slug = _community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    assert html.count('<span class="cp-group-actions"></span>') == 5


def test_refused_save_opens_only_the_group_with_the_over_max_field(admin):
    _cid, slug = _community()
    form = {"name": "Chief", "ideal_member": "ok", "verdict_summary": "ok", "value_prop": "x" * 900}
    r = admin.post(f"/tools/communities/{slug}/edit", data=form)
    assert r.status_code == 400
    tags = _details(r.text)
    opens = [" open" in t for t in tags]
    assert opens == [True, False, False, False, False]
    assert "1 over limit" in r.text


def test_over_limit_cursor_rule_cannot_lose_to_btn(app_module):
    css = app_module._CSS
    rule = re.search(r"\[data-char-budget-label\]\{[^}]*\}", css).group(0)
    assert "cursor:not-allowed!important" in rule


def test_visible_reason_sits_beside_the_save_buttons(admin):
    _cid, slug = _community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    footer = html.split('class="edit-footer-actions"')[1].split("</div>")[0]
    assert 'data-char-reason-for="comm-edit-form"' in footer and "hidden" in footer
    assert "over its limit" in footer


def test_cpe_control_uses_named_width_constants(app_module):
    html = app_module._community_cpe_control_html("Yes (NASBA sponsor)")
    assert f"flex:0 0 {app_module._CPE_SELECT_WIDTH_PX}px" in html
    assert f"flex:1 1 {app_module._CPE_NOTE_MIN_PX}px" in html
    assert "grid-template-columns" not in html


# --- live browser -----------------------------------------------------------

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


@pytest.fixture
def live(app_module, tmp_path):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    yield browser, tmp_path
    browser.close()
    pw.stop()


def _render(admin, tmp_path, slug):
    p = tmp_path / "edit.html"
    p.write_text(admin.get(f"/tools/communities/{slug}/edit").text, encoding="utf-8")
    return p.as_uri()


def test_required_field_in_a_closed_group_opens_it_and_takes_focus(admin, live):
    browser, tmp_path = live
    _cid, slug = _community()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(_render(admin, tmp_path, slug))
    page.evaluate("document.getElementById('cp-ideal_member').value=''")
    assert page.evaluate("document.querySelector('details.cp-group').open") is False
    page.evaluate("document.getElementById('comm-edit-form').requestSubmit()")
    assert page.evaluate("document.querySelector('details.cp-group').open") is True
    assert page.evaluate("document.activeElement.id") == "cp-ideal_member"
    page.close()


def test_enter_and_space_toggle_a_group_and_flag_updates_live(admin, live):
    browser, tmp_path = live
    _cid, slug = _community(over_max_field="value_prop")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(_render(admin, tmp_path, slug))
    page.locator("details.cp-group > summary").first.focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.querySelector('details.cp-group').open") is True
    page.keyboard.press("Space")
    assert page.evaluate("document.querySelector('details.cp-group').open") is False
    assert "1 over limit" in page.inner_text("[data-cp-flag]")
    page.evaluate("document.getElementById('cp-value_prop').value='short';"
                  "document.getElementById('cp-value_prop').dispatchEvent(new Event('input',{bubbles:true}))")
    assert page.inner_text("[data-cp-flag]").strip() == ""
    page.close()


def test_over_limit_buttons_show_not_allowed_and_reason_is_visible(admin, live):
    browser, tmp_path = live
    _cid, slug = _community(over_max_field="value_prop")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(_render(admin, tmp_path, slug))
    cursors = page.evaluate("[...document.querySelectorAll('button[data-char-budget-label]')]"
                            ".map(b=>getComputedStyle(b).cursor)")
    assert cursors and set(cursors) == {"not-allowed"}
    assert page.is_visible(".char-budget-reason")
    page.close()
