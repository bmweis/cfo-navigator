"""One button height: every button-like control renders at --control-h (28px), measured in Chromium.

Decision (Brian, 2026-11-03): one height everywhere, icon-only included, 28px visible with a 44px touch
target from an invisible ::after. Selects and text inputs inside admin tables share the token so a row aligns.
The tests measure rendered boxes, not source strings; they skip where no Chromium exists (as in CI).
"""
import glob
import importlib
import os
import tempfile

import pytest

from linklib.db import Library

TOKEN_PX = 28


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
    for path in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") + ["/opt/pw-browsers/chromium"]:
        try:
            return pw, pw.chromium.launch(executable_path=path)
        except Exception:
            continue
    pw.stop()
    return None


@pytest.fixture
def browser():
    got = _launch()
    if not got:
        pytest.skip("no Chromium available")
    pw, b = got
    yield b
    b.close()
    pw.stop()


def _heights(browser, html, selector, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 800})
    page.set_content(html)
    out = page.evaluate("""(sel) => Array.from(document.querySelectorAll(sel)).map(e => {
        const r = e.getBoundingClientRect(); return [e.className || e.tagName, Math.round(r.height * 10) / 10, Math.round(r.width)];})""", selector)
    page.close()
    return out


def _sheet_page(body):
    from webapp.app import _CSS, _ASK_CTL_CSS
    return f"<html><head><style>{_CSS}{_ASK_CTL_CSS}</style></head><body><main class='site-main'>{body}</main></body></html>"


PLANTS = """
<button class="btn">Primary</button>
<a class="btn" href="#">Link primary</a>
<button class="btn btn-ghost" style="padding:20px 40px;font-size:22px;">Ghost with a rogue padding</button>
<button class="btn btn-ghost" style="padding:3px 10px;font-size:12px;">Ghost small</button>
<a class="btn btn-ghost" href="#" style="padding:14px 24px;">Ghost link</a>
<button class="admin-only">Admin</button>
<a class="admin-only admin-only-page" href="#">Admin page</a>
<button class="ask-ctl">Ask ctl</button>
<button class="ask-ctl ask-ctl-sm ask-ctl-w">Small</button>
<button class="ask-dd-btn">Standard</button>
<button class="fu-send"><span class="fu-send-dot">&#8593;</span></button>
<button class="tcat-btn">Chip</button>
<button class="ger-mode">Mode</button>
<button class="ctl" aria-label="Move up">&uarr;</button>
<button class="ctl" style="padding:40px;height:90px;">Planted</button>
"""


def test_every_shared_button_class_is_the_token_height(browser):
    rows = _heights(browser, _sheet_page(PLANTS), "button, a.btn, a.admin-only")
    assert len(rows) >= 14
    bad = [r for r in rows if abs(r[1] - TOKEN_PX) > 0.6]
    assert not bad, bad


def test_same_at_phone_width(browser):
    rows = _heights(browser, _sheet_page(PLANTS), "button, a.btn, a.admin-only", width=390)
    bad = [r for r in rows if abs(r[1] - TOKEN_PX) > 0.6]
    assert not bad, bad


def test_icon_only_buttons_keep_a_44px_touch_target(browser):
    html = _sheet_page('<div style="padding:40px"><button class="ctl" id="a" aria-label="Up">&uarr;</button></div>')
    page = browser.new_page(viewport={"width": 390, "height": 800})
    page.set_content(html)
    box = page.evaluate("""() => { const e = document.getElementById('a'); const cs = getComputedStyle(e, '::after');
        return [parseFloat(cs.top), parseFloat(cs.bottom), e.getBoundingClientRect().height]; }""")
    page.close()
    assert box[2] == pytest.approx(TOKEN_PX, abs=0.6)
    assert abs(box[0]) + abs(box[1]) + box[2] - 2 >= 44 - 1   # pseudo-element extends the hit area to 44px


def test_table_selects_and_inputs_share_the_height(browser):
    body = ('<div class="table-frame"><table><tr><td><select><option>Hidden</option></select></td>'
            '<td><input type="text" value="News"></td><td><input type="number" value="3"></td>'
            '<td><button class="ctl">&uarr;</button></td></tr></table></div>')
    rows = _heights(browser, _sheet_page(body), "td select, td input, td button")
    assert len(rows) == 4
    assert all(abs(r[1] - TOKEN_PX) <= 0.6 for r in rows), rows


@pytest.fixture
def feeds_html(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    sec = lib.add_feed_section("Blogs")
    for i in range(3):
        lib.add_feed(sec, "Example Blog %d" % i, "https://example%d.test/feed" % i, "https://example%d.test" % i)
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "boss", "password": "supersecret"},
                  follow_redirects=False).status_code in (302, 303)
    r = c.get("/admin/reader/feeds")
    assert r.status_code == 200
    yield r.text
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def test_feeds_page_row_controls_are_one_height(browser, feeds_html):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_content(feeds_html)
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('.ff-row button, .ff-row a.btn, .ff-row select, .fs-row input[type=text], .fs-row button'))
        .map(e => [e.tagName + '.' + (e.className||''), Math.round(e.getBoundingClientRect().height*10)/10])""")
    page.close()
    assert len(rows) >= 15, rows
    bad = [r for r in rows if abs(r[1] - TOKEN_PX) > 0.6]
    assert not bad, bad[:6]
