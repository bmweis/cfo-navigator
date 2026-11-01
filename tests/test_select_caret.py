"""One select caret, one size: the shared rule, the Buddy buttons, and the check that keeps it so."""
from __future__ import annotations

import glob
import pathlib

import pytest

from linklib import brand_check

APP_SRC = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text(encoding="utf-8")

# A source snippet that contains the shared rule, so planted-violation tests isolate the planted part.
SHARED = ("select{appearance:none!important;background-image:var(--select-caret-img)!important;}"
          ":root{--select-caret-size:16px;}")


def test_real_source_is_clean():
    assert brand_check.select_caret_problems(APP_SRC) == []


def test_shared_rule_and_token_in_css():
    from webapp.app import _CSS
    assert "--select-caret-size:16px" in _CSS
    assert "select{-webkit-appearance:none!important;appearance:none!important;" in _CSS
    assert "background-size:var(--select-caret-size) var(--select-caret-size)!important" in _CSS
    # text never runs under the caret: padding-right is the caret plus its margins
    assert "padding-right:calc(var(--select-caret-size) + 20px)!important;line-height:1.4!important" in _CSS


def test_buddy_buttons_use_the_shared_image_not_a_glyph():
    assert "&#9662;" not in APP_SRC and "▾" not in APP_SRC.replace("# \"Columns ▾\"", "")
    assert APP_SRC.count('class="ask-dd-caret" aria-hidden="true"></span>') == 2
    assert "background:var(--select-caret-img) no-repeat center/contain" in APP_SRC


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
        "select{appearance:none;background-image:var(--select-caret-img);}"))


def test_comments_do_not_count():
    ok = SHARED + "\n/* select{background-image:url(x);} */"
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


def test_rendered_select_has_no_native_arrow_and_text_clears_the_caret():
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
                '<button class="ask-dd-btn"><span class="ask-dd-caret" id="c"></span></button></body></html>')
        for width in (390, 1280):
            page = browser.new_page(viewport={"width": width, "height": 600})
            page.set_content(html)
            got = page.evaluate("""() => { const s = getComputedStyle(document.getElementById('s'));
                const c = document.getElementById('c').getBoundingClientRect();
                return {app: s.appearance, size: s.backgroundSize, pr: s.paddingRight, bg: s.backgroundColor,
                        img: s.backgroundImage.slice(0, 22), cw: c.width, ch: c.height,
                        dimg: getComputedStyle(document.getElementById('d')).backgroundImage.includes('6F6A60')}; }""")
            assert got["app"] == "none"
            assert got["size"] == "16px 16px" and got["pr"] == "36px"
            assert got["bg"] == "rgb(255, 255, 255)"      # the inline background colour survives
            assert got["img"].startswith('url("data:image/svg')
            assert (got["cw"], got["ch"]) == (16, 16)       # Buddy button caret is the same size
            assert got["dimg"] is True                      # disabled select uses the muted caret
            page.close()
        browser.close()
    finally:
        pw.stop()
