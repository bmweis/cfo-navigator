"""/admin/fpa-buddy/report layout follow-up to 797: landscape phones keep stacked cards, chips are rounded
rectangles, cells are top-aligned, the Question column has a named width, and an expanded conversation's turn
rows are nested under their parent. Fixture shapes follow ids 84 and 85 (invented wording)."""
import importlib
import os
import re
import tempfile

import pytest

from tests.test_admin_report_turns import _prestep, _browser

Q = "What does the typical organization structure and spend profile look like for a $20-30M ARR SaaS business?"


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    lib = Library(db)
    uid = lib.create_user("asker1", "supersecret", role="user")
    first = lib.record_ask_question(uid, Q, "a1", "claude-sonnet-4-6", "standard", True, False, False,
                                    cost_usd=0.11, prestep=_prestep(placed=2))
    lib.record_ask_question(uid, "Which of those lines is growing fastest?", "a2", "claude-sonnet-4-6", "standard",
                            True, False, False, conversation_id=str(first), turn_index=1, cost_usd=0.09,
                            prestep=_prestep())
    lib.close()
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield c, appmod, first
    if os.path.exists(db):
        os.remove(db)


def _html(env):
    return env[0].get("/admin/fpa-buddy/report").text


def _turn_rows(html, cid):
    return [seg for seg in html.split("<tr ") if f'data-convo="{cid}"' in seg]


def test_turn_rows_carry_the_nesting_class_and_the_parent_row_does_not(env):
    html = _html(env)
    rows = _turn_rows(html, env[2])
    assert len(rows) == 2 and all("rp-turn" in r.split(">")[0] for r in rows)
    parent = [seg for seg in html.split("<tr ") if "toggleConvo" in seg][0]
    assert "rp-turn" not in parent.split(">")[0]
    css = html.split("<style>")[-1]
    assert "tr.rp-turn" in css and "var(--navy-light)" in css and "var(--accent-light)" in css


def test_turn_one_does_not_repeat_an_identical_parent_question(env):
    rows = _turn_rows(_html(env), env[2])
    assert "Same as above" in rows[0] and Q[:40] not in rows[0]
    assert "Which of those lines is growing fastest?" in rows[1] and "Same as above" not in rows[1]


def test_chip_is_a_rounded_rectangle_not_a_pill(env):
    css = _html(env).split("<style>")[-1]
    chip = re.search(r"\.rp-chip\{\{?([^}]*)\}", css).group(1)
    assert "border-radius:6px" in chip and "999px" not in chip and "white-space:normal" in chip


def test_question_column_has_the_named_width_and_cells_are_top_aligned(env):
    appmod = env[1]
    html = _html(env)
    assert f"min-width:{appmod._COL_WIDTH_REPORT_TITLE}px;\">Question</th>" in html
    assert "td.admin-table-cell{vertical-align:top;}" in html


def test_card_breakpoint_covers_landscape_phones_and_the_sitewide_rule_matches(env):
    appmod = env[1]
    assert appmod._REPORT_CARD_BREAKPOINT >= 900
    assert f"@media(max-width:{appmod._REPORT_CARD_BREAKPOINT}px)" in _html(env)
    assert re.search(rf"@media\(max-width:{appmod._REPORT_CARD_BREAKPOINT}px\)\{{\s*\.site-main\.site-main "
                     r"table\.rp-cards\.rp-cards td", appmod._CSS)


def test_nesting_rule_has_no_important_background(env):
    css = _html(env).split("<style>")[-1]
    for line in css.splitlines():
        if "rp-turn" in line and "background" in line:
            assert "!important" not in line


@pytest.mark.parametrize("width,cards", [(390, True), (844, True), (1280, False)])
def test_rendered_layout_in_chromium(env, tmp_path, width, cards):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        f = tmp_path / "r.html"
        f.write_text(_html(env), encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        page.evaluate("document.querySelectorAll('tr[data-convo]').forEach(r=>r.style.display='')")
        r = page.evaluate("""() => { const row = document.querySelector('tr.rp-turn'); const chip = document.querySelector('.rp-chip');
            return {display: getComputedStyle(row.children[0]).display, radius: getComputedStyle(chip).borderRadius,
              valign: getComputedStyle(row.children[0]).verticalAlign, q: row.children[2].getBoundingClientRect().width,
              chipH: chip.getBoundingClientRect().height, chipW: chip.getBoundingClientRect().width,
              bg: getComputedStyle(row).backgroundColor,
              hair: getComputedStyle(row.children[1]).borderTopWidth,
              shadow: getComputedStyle(row).boxShadow + '|' + getComputedStyle(row.children[0]).boxShadow,
              doc: document.documentElement.scrollWidth, win: window.innerWidth}}""")
        page.close()
        assert r["radius"] == "6px" and r["valign"] == "top" and r["bg"] == "rgb(238, 241, 247)", r
        assert r["doc"] <= r["win"], r
        assert r["chipH"] < r["chipW"], r          # a pill that wraps becomes a circle; this must stay wider than tall
        assert "63, 92, 154" in r["shadow"], r      # the navy-light left rule, on the row (cards) or the first cell (table)
        if cards:
            assert r["display"] == "block" and r["hair"] == "0px", r
        else:
            assert r["display"] == "table-cell" and r["q"] >= 420, r
    finally:
        browser.close()
        pw.stop()
