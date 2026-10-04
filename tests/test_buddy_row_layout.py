"""Past-questions row: the byline follows one rule (own first, then admin name,
then nothing), the collapsed title is never squeezed into a side column on a
phone, and the meta can only break between segments."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library
from tests import test_buddy_phone_fixes as T

LONG = ("For a venture-backed B2B SaaS company growing 80 percent year over year with "
        "usage-based pricing, how should I think about recognizing revenue and forecasting "
        "net revenue retention across annual and monthly contracts?")


def _seed(db):
    lib = Library(db)
    lib.seed_voice_prompts()
    boss = lib.create_user("boss", "supersecret", role="admin")
    other = lib.create_user("other", "supersecret", role="user")
    member = lib.create_user("member", "supersecret", role="user")
    lib.conn.execute("UPDATE users SET name='Zed Administrator' WHERE username='boss'")
    lib.conn.execute("UPDATE users SET name='Olivia Other' WHERE username='other'")
    lib.conn.commit()
    mk = lambda uid, q: lib.record_ask_question(uid, q, "Answer.", "m", "standard", True, False, True)
    ids = {"boss": mk(boss, "Boss own question " + LONG), "other": mk(other, "Other member question " + LONG),
           "member": mk(member, "Member own question")}
    lib.record_ask_feedback(ids["boss"], other, "helpful", "")
    lib.close()
    return ids


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    ids = _seed(db)
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def client(user):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield client, ids
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _meta(html, start):
    i = html.index(start)
    j = html.index('<span class="ask-pq-meta">', i)
    return html[j:html.index("</summary>", j)]


def test_admin_sees_you_on_own_row_and_full_name_on_others(site):
    client, _ = site
    html = client("boss").get("/tools/fpa-buddy").text
    assert ">You<" in _meta(html, "Boss own question")
    assert "Zed Administrator" not in _meta(html, "Boss own question")
    other = _meta(html, "Other member question")
    assert "Olivia Other" in other and ">You<" not in other


def test_member_sees_you_on_own_row_and_no_name_on_others(site):
    client, _ = site
    html = client("member").get("/tools/fpa-buddy").text
    assert ">You<" in _meta(html, "Member own question")
    other = _meta(html, "Other member question")
    assert "Olivia" not in other and ">You<" not in other
    assert "Olivia Other" not in html and "Zed Administrator" not in html


def test_break_glass_admin_has_no_own_rows(site):
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False).status_code in (302, 303)
    html = c.get("/tools/fpa-buddy").text
    assert ">You<" not in html and "Zed Administrator" in _meta(html, "Boss own question")


def test_byline_helper_cases():
    from webapp.app import _ask_byline
    row = {"user_id": 5, "asker_name": "Full Name", "asker_username": "fn"}
    assert _ask_byline(row, 5, True) == "You" and _ask_byline(row, 5, False) == "You"
    assert _ask_byline(row, 9, True) == "Full Name"
    assert _ask_byline(row, 9, False) is None
    assert _ask_byline(row, None, True) == "Full Name"


def test_browser_title_is_full_width_and_meta_never_breaks_inside_a_segment(site):
    client, ids = site
    html = client("boss").get("/tools/fpa-buddy").text
    launched = T._launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        for w in (390, 430, 768, 1280):
            ctx = browser.new_context(viewport={"width": w, "height": 900}, is_mobile=(w < 500), has_touch=(w < 500))
            pg = ctx.new_page()
            pg.route(re.compile(r"^http://buddy\.test/.*"), T.Site(html).handle)
            pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
            pg.goto("http://buddy.test/tools/fpa-buddy")
            m = pg.evaluate("""()=>{const r=[...document.querySelectorAll('.ask-pq-row')].find(x=>x.textContent.indexOf('Boss own question')>-1),
              s=r.querySelector('.ask-pq-sum').getBoundingClientRect(),
              q=r.querySelector('.ask-pq-q').getBoundingClientRect(),
              segs=[...r.querySelectorAll('.ask-pq-meta .ask-pq-seg, .ask-pq-meta .ask-pq-rate')],
              ds=getComputedStyle(r.querySelector('.ask-pq-date')).whiteSpace;
              return {sum:s.width,q:q.width,qh:q.height,ds:ds,
                segs:segs.map(e=>[e.getBoundingClientRect().height, getComputedStyle(e).whiteSpace]),
                lines:Math.round(q.height/ (parseFloat(getComputedStyle(r.querySelector('.ask-pq-q')).lineHeight)||19))}}""")
            print(w, {k: m[k] for k in ("sum", "q", "qh")})
            assert m["ds"] == "nowrap" and all(ws == "nowrap" and h < 26 for h, ws in m["segs"])
            if w < 700:
                assert m["q"] >= 0.8 * m["sum"] - 40      # full row width minus the caret column
            ctx.close()
    finally:
        browser.close()
        pw.stop()
