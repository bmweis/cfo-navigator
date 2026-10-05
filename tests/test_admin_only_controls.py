"""Admin-only controls: one outlined `.admin-only` class (BRAND.md, Admin-only controls)."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library

SRC = open(os.path.join(os.path.dirname(__file__), "..", "webapp", "app.py"), encoding="utf-8").read()


def test_old_admin_button_classes_are_gone():
    for old in ("tool-admin-btn", "tp-admin-btn", "ask-ctl-admin"):
        assert old not in SRC, old


def _rule(name):
    m = re.search(r"\n\." + re.escape(name) + r"\{([^}]*)\}", SRC)
    assert m, name
    return m.group(1)


def test_one_definition_outlined_no_fill():
    assert len(re.findall(r"\n\.admin-only\{", SRC)) == 1
    body = _rule("admin-only")
    assert "background:transparent" in body
    assert "border:1px solid var(--seafoam-deep)" in body and "color:var(--seafoam-deep)" in body
    assert "var(--seafoam);" not in body and "height:28px" in body and "border-radius:6px" in body
    page = _rule("admin-only-page")
    assert "height:42px" in page and "border-radius:10px" in page


def test_delete_red_hover_only_on_hover_devices():
    css = SRC[SRC.index("\n.admin-only{"):]
    css = css[:css.index("\n\n")]
    assert ".admin-only-del:hover" in css
    hover = css[css.index("@media(hover:hover)"):]
    assert ".admin-only-del:hover" in hover
    assert ".admin-only-del:hover" not in css[:css.index("@media(hover:hover)")]


def test_reader_card_border_is_one_pixel():
    assert '_ADMIN_ONLY_BORDER = "1px solid var(--seafoam-deep)"' in SRC


@pytest.fixture
def pages(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    for n in ("Abacum", "Datarails"):
        i = lib.add_tool(n, f"{n} description.", f"https://{n.lower()}.example", [], approved=1,
                         vendor_email="v@example.com", warm_intro_enabled=1, vendor_name=n)
        lib.approve_tool(i)
    c = lib.add_community("CFO Alliance", "https://cfoalliance.example", "Finance leaders", "$$", ["Peer"], approved=1)
    lib.approve_community(c)
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    admin = TestClient(appmod.app)
    assert admin.post("/login", data={"username": "boss", "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
    yield admin, TestClient(appmod.app)
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def test_signed_out_pages_carry_no_admin_only_markup(pages):
    admin, anon = pages
    for url in ("/tools/software/abacum", "/tools/communities/cfoalliance", "/tools/resources"):
        html = anon.get(url).text
        assert 'class="admin-only' not in html, url
    d = anon.get("/tools/software").text
    assert 'class="admin-only admin-only-page"' not in d and "/admin/tools/software/categories" not in d
    r = admin.get("/tools/resources").text
    assert 'class="admin-only">Manage' in r
    p = admin.get("/tools/software/abacum").text
    assert 'class="admin-only admin-only-page"' in p and "Edit</a>" in p
    assert 'class="admin-only admin-only-page">+ Add tool' in admin.get("/tools/software").text


def _launch():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except Exception:
        return None
    import glob
    for path in [None] + sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"), reverse=True):
        try:
            return pw, pw.chromium.launch(**({"executable_path": path} if path else {}))
        except Exception:
            continue
    pw.stop()
    return None


def test_tiers_measured_in_chromium(pages):
    admin, _ = pages
    got = _launch()
    if got is None:
        pytest.skip("no Chromium")
    pw, browser = got
    try:
        def measure(url, sel, w=1280):
            html = admin.get(url).text
            pg = browser.new_page(viewport={"width": w, "height": 900})
            pg.route(re.compile(r"^https?://(?!m\.test).*"), lambda r, _q=None: r.abort())
            pg.route("http://m.test/", lambda r, _q=None, h=html: r.fulfill(body=h, content_type="text/html"))
            pg.goto("http://m.test/")
            pg.wait_for_timeout(200)
            out = pg.evaluate("""(s)=>[...document.querySelectorAll(s)].filter(e=>e.getBoundingClientRect().width>0).map(e=>{
                const r=e.getBoundingClientRect(),c=getComputedStyle(e);
                return [e.textContent.trim(),Math.round(r.height*10)/10,c.borderRadius,c.borderTopWidth,c.backgroundColor,c.color]})""", sel)
            pg.close()
            return out
        for w in (1280, 390):
            card = [r for r in measure("/tools/software", ".admin-only,.tool-intro-btn", w) if r[0] != "+ Add tool"]
            names = {n for n, *_ in card}
            assert {"Quick edit", "Full edit", "Delete"} <= names
            for n, h, rad, bw, bg, color in card:
                assert abs(h - 28) < 0.6 and rad == "6px", (n, h, rad)
                if n != "📨 Warm intro":
                    assert bw == "1px" and bg == "rgba(0, 0, 0, 0)" and color == "rgb(31, 122, 102)", (n, bg, color)
            page = measure("/tools/software/abacum", ".admin-only", w)
            for n, h, rad, bw, bg, color in page:
                if n.endswith("Edit"):
                    assert abs(h - 42) < 0.6 and rad == "10px", (n, h, rad)
                else:
                    assert abs(h - 28) < 0.6 and rad == "6px", (n, h, rad)
        add = measure("/tools/software", ".admin-only-page")
        assert any(n == "+ Add tool" and abs(h - 42) < 0.6 for n, h, *_ in add)
    finally:
        browser.close()
        pw.stop()
