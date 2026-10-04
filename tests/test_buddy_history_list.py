"""FP&A Buddy past-questions list: first turns only, a heading that says what it
shows, a Helpful only toggle, date-only bylines, compact buttons, and the
retired "Anonymize asker" control. Chromium tests skip where no browser exists."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library
from tests import test_buddy_phone_fixes as T

_launch, Site = T._launch, T.Site


def _seed(db):
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    a = lib.create_user("author", "supersecret", role="user")
    a2 = lib.create_user("other", "supersecret", role="user")
    reader = lib.create_user("reader", "supersecret", role="user")
    mk = lambda uid, q, **kw: lib.record_ask_question(uid, q, "Answer [1].", "m", "standard", True, False, True, **kw)
    ids = {}
    ids["helpful"] = mk(a, "Helpful first question")
    lib.record_ask_feedback(ids["helpful"], a, "helpful", "")
    ids["plain"] = mk(a2, "Unrated first question")
    ids["bad"] = mk(a2, "Not helpful first question")
    lib.record_ask_feedback(ids["bad"], a, "not_helpful", "")
    ids["mixed"] = mk(a2, "Mixed first question")
    lib.record_ask_feedback(ids["mixed"], a, "helpful", "")
    lib.record_ask_feedback(ids["mixed"], reader, "inaccurate", "")
    ids["mine"] = mk(reader, "My own question")
    # a follow-up rated helpful: must never be its own row
    conv = str(ids["helpful"])
    ids["follow"] = mk(a, "Follow-up turn question", conversation_id=conv, turn_index=1)
    lib.record_ask_feedback(ids["follow"], a, "helpful", "")
    lib.conn.execute("UPDATE users SET name='Alexandra Author' WHERE username='author'")
    lib.conn.commit()
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
    yield client, ids, db
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _section(html):
    return html[html.index('id="past-questions"'):html.index("</details>\n", html.index('id="past-questions"'))]


def test_follow_up_turns_are_never_standalone_rows(site):
    _, ids, db = site
    lib = Library(db)
    got = {r["id"] for r in lib.list_public_ask_questions()}
    helpful = {r["id"] for r in lib.list_public_ask_questions(helpful_only=True)}
    lib.close()
    assert ids["follow"] not in got and ids["follow"] not in helpful
    assert {ids["helpful"], ids["plain"], ids["mine"]} <= got


def test_default_state_lists_all_questions_with_an_honest_heading(site):
    client, ids, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy").text)
    assert "Unrated first question" in sec and "Helpful first question" in sec
    assert "Follow-up turn question" not in sec
    assert re.search(r'<h2 class="ask-pq-heading">All questions</h2>', sec)
    assert 'aria-pressed="false">Helpful only' in sec


def test_helpful_only_filters_relabels_and_shows_pressed(site):
    client, _, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy?helpful=1").text)
    assert "Helpful first question" in sec and "Unrated first question" not in sec
    assert "Follow-up turn question" not in sec
    assert '<h2 class="ask-pq-heading">Helpful questions only</h2>' in sec
    assert 'aria-pressed="true">Helpful only' in sec
    # Search keeps the filter; the toggle flips it back off
    assert re.search(r'name="helpful" value="1" class="ask-ctl ask-ctl-sm ask-ctl-w">Search', sec)
    assert re.search(r'name="helpful" value="" class="ask-ctl ask-ctl-sm ask-ctl-w" aria-pressed="true">Helpful only', sec)


def test_intro_line(site):
    client, _, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy").text)
    assert "Search before running your query." in sec
    assert "Questions members rated helpful." not in sec


def test_bylines_by_viewer(site):
    client, ids, _ = site
    member = _section(client("reader").get("/tools/fpa-buddy").text)
    assert "Alexandra" not in member and "author" not in member.replace("Helpful first", "")
    assert "A member" not in member
    assert re.search(r"You &middot; \d{4}-\d{2}-\d{2}", member)
    admin = _section(client("boss").get("/tools/fpa-buddy").text)
    assert re.search(r"Alexandra Author &middot; \d{4}-\d{2}-\d{2}", admin)


def test_anonymize_is_retired_and_remove_is_relabelled(site):
    client, ids, _ = site
    admin = client("boss")
    html = admin.get("/tools/fpa-buddy").text
    assert "Anonymize asker" not in html and "Un-anonymize" not in html
    assert ">Remove from view</button>" in html and "Remove from this view" not in html
    assert admin.post(f"/questions/{ids['plain']}/anonymize", follow_redirects=False).status_code in (404, 405)
    assert admin.post(f"/questions/{ids['plain']}/hide", follow_redirects=False).status_code == 303


def test_resume_and_search_use_the_compact_class(site):
    client, ids, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    assert re.search(r'class="ask-ctl ask-ctl-sm ask-ctl-w"[^>]*onclick="resumeConvoById', html)
    assert "ask-ctl ask-ctl-sm ask-ctl-w\" onclick=\"event.stopPropagation();resumeConvo" in html
    assert ".ask-ctl-sm{min-height:28px" in html and ".ask-ctl-w{width:128px" in html


def test_browser_heights_widths_and_touch_target(site):
    client, ids, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        for w in (390, 1280):
            ctx = browser.new_context(viewport={"width": w, "height": 900}, is_mobile=(w < 500), has_touch=(w < 500))
            pg = ctx.new_page()
            pg.route(re.compile(r"^http://buddy\.test/.*"), Site(html).handle)
            pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
            pg.goto("http://buddy.test/tools/fpa-buddy")
            pg.evaluate("document.querySelectorAll('details.ask-pq-row').forEach(d=>d.open=true)")
            def box(sel):
                return pg.locator(sel).first.bounding_box()
            search = box('#past-questions button:has-text("Search")')
            helpful = box('#past-questions button:has-text("Helpful only")')
            field = box('#past-questions input[type=search]')
            pg.locator('.ask-pq-foot .ask-ctl').first.scroll_into_view_if_needed()
            resume = box('.ask-pq-foot .ask-ctl')
            assert abs(search["height"] - 28) < 0.6 and abs(helpful["height"] - 28) < 0.6
            assert abs(search["width"] - helpful["width"]) < 0.6   # neighbours share one width
            assert abs(search["width"] - 128) < 0.6 and abs(helpful["width"] - 128) < 0.6
            assert abs(search["y"] - helpful["y"]) < 0.6           # side by side, one row
            # no label wraps or clips inside 128px
            assert pg.evaluate("""()=>[...document.querySelectorAll('#past-questions form button')]
                .every(b=>b.scrollWidth<=b.clientWidth+0.5 && b.getBoundingClientRect().height<30)""")
            if w >= 1000:   # at 1280 they share the input's row, centred on it (align-items:center)
                assert abs((search["y"] + 14) - (field["y"] + field["height"] / 2)) < 1.5
            else:           # at 390 the pair wraps to its own row beneath the input
                assert search["y"] > field["y"] + field["height"]
            if resume:
                assert abs(resume["height"] - 28) < 0.6
                assert abs(resume["width"] - 128) < 0.6
                # touch target: a point 7px above the visible edge still hits the button
                x, y = resume["x"] + resume["width"] / 2, resume["y"] - 7
                assert pg.evaluate("([x,y])=>!!document.elementFromPoint(x,y).closest('.ask-ctl')", [x, y])
            ctx.close()
    finally:
        browser.close()
        pw.stop()


def test_each_row_shows_a_labelled_rating(site):
    client, ids, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy").text)
    def meta(q):
        i = sec.index(q)
        return sec[i:sec.index("</summary>", i)]
    assert 'aria-label="Rated helpful"' in meta("Helpful first question")
    assert "&#128077;" in meta("Helpful first question")
    assert "ask-pq-rate" not in meta("Unrated first question")
    assert "Not rated" not in sec
    assert 'aria-label="Rated not helpful"' in meta("Not helpful first question")
    m = meta("Mixed first question")
    assert 'aria-label="Rated mixed"' in m and "&#128077;&#128078;" in m
    css = client("reader").get("/tools/fpa-buddy").text
    rate_css = "".join(l for l in css.splitlines() if l.startswith(".ask-pq-rate"))
    assert rate_css and "border" not in rate_css and "background" not in rate_css


def test_rating_emoji_strings_pass_the_voice_and_typography_scanners():
    from html import unescape
    from linklib.voice_review import mechanical_findings, typography_findings_plain
    for ent in ("&#128077;", "&#128078;", "&#128077;&#128078;"):
        t = unescape(ent)
        assert all(0x1F300 <= ord(c) <= 0x1FAFF for c in t)   # no ZWJ, selector or skin tone
        assert not mechanical_findings(t) and not typography_findings_plain(t)
