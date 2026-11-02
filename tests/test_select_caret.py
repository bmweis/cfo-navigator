"""Two arrow indicators, one size: the shared rules, the Buddy buttons, and the check that keeps it so."""
from __future__ import annotations

import glob
import pathlib

import pytest

from linklib import brand_check

APP_SRC = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text(encoding="utf-8")

# A source snippet that contains the shared rule, so planted-violation tests isolate the planted part.
SHARED = ("select{appearance:none!important;background-image:var(--indicator-chevron-img)!important;}"
          ".disclosure-caret,.ask-pq-rc{width:var(--indicator-size)!important;background:var(--indicator-triangle-img);}"
          ":root{--indicator-size:14px;}")


def test_real_source_is_clean():
    assert brand_check.select_caret_problems(APP_SRC) == []


def test_shared_rule_and_token_in_css():
    from webapp.app import _CSS
    assert "--indicator-size:14px" in _CSS
    assert "select{-webkit-appearance:none!important;appearance:none!important;" in _CSS
    assert "background-size:var(--indicator-size) var(--indicator-size)!important" in _CSS
    assert "padding-right:calc(var(--indicator-size) + 20px)!important;line-height:1.4!important" in _CSS
    assert "width:var(--indicator-size)!important;height:var(--indicator-size)!important" in _CSS


def test_buddy_buttons_use_the_shared_image_not_a_glyph():
    assert "&#9662;" not in APP_SRC
    assert APP_SRC.count('class="ask-dd-caret" aria-hidden="true"></span>') == 2
    assert "background:var(--indicator-chevron-img) no-repeat center/contain" in APP_SRC


def test_no_leftover_glyph_triangles_in_markup():
    for glyph in ("&#9654;", "&#9656;", "&#9662;", "\\u25be"):
        assert glyph not in APP_SRC


def test_planted_css_rule_is_flagged():
    bad = SHARED + "\n.my-form select{background-image:url(x.svg);padding-right:30px;}"
    problems = brand_check.select_caret_problems(bad)
    assert len(problems) == 1 and "my-form select" in problems[0]


def test_planted_appearance_rule_in_fstring_braces_is_flagged():
    bad = SHARED + "\n.row select{{appearance:auto;}}"
    assert any("row select" in p for p in brand_check.select_caret_problems(bad))


def test_planted_inline_style_is_flagged():
    bad = SHARED + '\n<select name="x" style="width:100%;-webkit-appearance:auto;"></select>'
    assert any("inline <select>" in p for p in brand_check.select_caret_problems(bad))
    bad2 = SHARED + "\n<select style=\"background:#fff url(a.svg) no-repeat;\"></select>"
    assert any("inline <select>" in p for p in brand_check.select_caret_problems(bad2))


def test_ordinary_select_styles_and_date_inputs_pass():
    ok = SHARED + ('\n.intro-field select{width:100%;padding:9px 13px;border:1px solid red;}'
                   '\n<select style="width:100%;padding:10px 14px;background:#fff;"></select>'
                   '\ninput[type=date]{appearance:none;}')
    assert brand_check.select_caret_problems(ok) == []


def test_missing_shared_rule_or_token_is_flagged():
    assert any("shared" in p for p in brand_check.select_caret_problems("a{color:red}"))
    assert any("token" in p for p in brand_check.select_caret_problems(
        "select{appearance:none;background-image:var(--indicator-chevron-img);}"))


@pytest.mark.parametrize("planted,needle", [
    (".x::after{content:'\\25B6';}", "text glyph"),
    ("<span>&#9660;</span>", "&#9660;"),
    ("<span>\u25b8</span>", "text glyph"),
    (".t{width:0;height:0;border-style:solid;border-width:4px 0 4px 6px;}", "CSS-border triangle"),
    (".c{border-right:2px solid red;border-bottom:2px solid red;transform:rotate(45deg);}", "border chevron"),
    ('<svg><polyline points="6 9 12 15 18 9"/></svg>', "SVG chevron"),
    (".disclosure-caret{font-size:12px;}", "own indicator size"),
    (".disclosure-caret{transform:rotate(180deg);}", "rotates an indicator"),
    (".ask-dd-btn[aria-expanded=true] .ask-dd-caret{transform:rotate(180deg);}", "rotates an indicator"),
    ('<span class="disclosure-caret" style="font-size:11px;"></span>', "own size"),
    ('<span class="disclosure-caret">&gt;</span>', "carries text"),
])
def test_planted_rogue_indicator_is_flagged(planted, needle):
    problems = brand_check.select_caret_problems(SHARED + "\n" + planted)
    assert any(needle in p for p in problems), problems


def test_documented_sort_arrows_are_the_only_allowed_glyphs():
    assert set(brand_check.SELECT_CARET_ALLOWLIST) == {"u25b2", "u25bc"}
    assert all(v.strip() for v in brand_check.SELECT_CARET_ALLOWLIST.values())
    assert brand_check.select_caret_problems(SHARED + "\nx = '\\u25B2'") == []


def test_missing_disclosure_rule_is_flagged():
    only_select = "select{appearance:none;background-image:var(--indicator-chevron-img);}:root{--indicator-size:14px;}"
    assert any("disclosure triangle rule" in p for p in brand_check.select_caret_problems(only_select))


def test_comments_do_not_count():
    ok = SHARED + "\n/* select{background-image:url(x);} &#9654; */"
    assert brand_check.select_caret_problems(ok) == []


def test_check_is_in_run_all_and_themed():
    from webapp import checks
    from webapp.app import _LIVE_CHECK_THEMES
    row = [r for r in checks.run_all() if r["name"] == "Select caret is the shared style"]
    assert row and row[0]["ok"] is True
    assert any("Select caret is the shared style" in names for _t, names in _LIVE_CHECK_THEMES)


def _chromium_path():
    hits = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    return hits[0] if hits else None


def test_rendered_indicators_share_one_size_and_directions():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(executable_path=_chromium_path(), args=["--no-sandbox"]) \
                if _chromium_path() else pw.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed in this environment")
        from webapp.app import _CSS
        html = ("<html><head><style>" + _CSS + "</style></head><body>"
                '<select id="s" style="width:300px;padding:10px 14px;border:1px solid #E4E0D6;border-radius:10px;'
                'font:inherit;font-size:15px;background:#fff;"><option>A fairly long option label here</option></select>'
                '<select id="d" disabled style="padding:6px 10px;background:#fff;"><option>x</option></select>'
                '<button class="ask-dd-btn"><span class="ask-dd-caret" id="c"></span></button>'
                '<details id="cl"><summary><span class="disclosure-caret" id="t1" style="font-size:30px;"></span>Closed</summary>x</details>'
                '<details id="op" open><summary><span class="disclosure-caret" id="t2"></span>Open</summary>x</details>'
                '</body></html>')
        for width in (390, 1280):
            page = browser.new_page(viewport={"width": width, "height": 600})
            page.set_content(html)
            got = page.evaluate("""() => { const s = getComputedStyle(document.getElementById('s'));
                const r = id => document.getElementById(id).getBoundingClientRect();
                return {app: s.appearance, size: s.backgroundSize, pr: s.paddingRight, bg: s.backgroundColor,
                        img: s.backgroundImage.slice(0, 22), c: [r('c').width, r('c').height],
                        t1: [r('t1').width, r('t1').height], t2: [r('t2').width, r('t2').height],
                        tr1: getComputedStyle(document.getElementById('t1')).transform,
                        tr2: getComputedStyle(document.getElementById('t2')).transform,
                        dimg: getComputedStyle(document.getElementById('d')).backgroundImage.includes('6F6A60')}; }""")
            assert got["app"] == "none"
            assert got["size"] == "14px 14px" and got["pr"] == "34px"
            assert got["bg"] == "rgb(255, 255, 255)"      # the inline background colour survives
            assert got["img"].startswith('url("data:image/svg')
            assert got["c"] == [14, 14] and got["t1"] == [14, 14] and got["t2"] == [14, 14]
            assert got["tr1"] == "none"                    # closed: east
            assert got["tr2"] != "none"                    # open: turned 90 degrees, south
            assert got["dimg"] is True                     # disabled select uses the muted chevron
            page.close()
        browser.close()
    finally:
        pw.stop()
