"""/tools/resources cards show every character (no clamp, no "Show more").
A card name and description are never cut off: no -webkit-line-clamp,
overflow:hidden or max-height on them, so scrollHeight equals clientHeight.
Cards in a row stretch to equal height (grid default). Measured in real
Chromium at 1280px and 390px (skips where none exists, as in CI). WebKit is
unverified here."""
import importlib
import os
import tempfile

import pytest

LONG = ("A very long description of a benchmarking source. " * 60)[:3000]


def _browser():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except Exception:
        return None
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pw.stop()
        return None


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    yield TestClient(appmod.app), appmod, db
    if os.path.exists(db):
        os.remove(db)


def _html(env):
    client, appmod, db = env
    lib = appmod.Library(db)
    lib.add_benchmark("Long " + "name words " * 20, "https://long.example", LONG,
                      "Private", "free", "benchmarking")
    lib.add_benchmark("Short", "https://short.example", "Short.", "Private", "free", "benchmarking")
    lib.add_benchmark("Book", "https://bk.example", LONG, "Private", "free", "books")
    lib.close()
    return client.get("/tools/resources").text


def test_no_clamp_rules_in_the_rendered_css(env):
    html = _html(env)
    css = html[html.index("<style>", html.index("bench-card")):]
    for sel in (".bench-name{", ".bench-desc{"):
        rule = css[css.index(sel):].split("}}", 1)[0]
        assert "line-clamp" not in rule and "overflow:hidden" not in rule and "max-height" not in rule, rule


@pytest.mark.parametrize("width", [1280, 390])
def test_long_text_is_fully_shown(env, tmp_path, width):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        f = tmp_path / "r.html"
        f.write_text(_html(env), encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        res = page.evaluate("""() => [...document.querySelectorAll('.bench-name,.bench-desc')].map(e => {
          const s = getComputedStyle(e);
          return {cls: e.className, sh: e.scrollHeight, ch: e.clientHeight,
                  clamp: s.webkitLineClamp, ov: s.overflow, mh: s.maxHeight};})""")
        rows = page.evaluate("""() => [...document.querySelectorAll('div[style*="display:grid"]')]
          .filter(g => g.querySelector('.bench-card')).map(g =>
            [...g.querySelectorAll('.bench-card')].map(c => Math.round(c.getBoundingClientRect().height)))""")
        page.close()
        assert res
        for r in res:
            assert r["sh"] == r["ch"], r
            assert r["clamp"] in ("none", ""), r
            assert r["ov"] != "hidden" and r["mh"] == "none", r
        # the long description is long enough to have wrapped past 3 lines
        assert max(r["ch"] for r in res if "desc" in r["cls"]) > 3 * 20
        print("card heights per grid:", rows)
    finally:
        browser.close()
        pw.stop()
