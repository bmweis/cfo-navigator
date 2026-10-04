"""FP&A Buddy Keep private: ask-time checkbox, whole-conversation privacy,
Make private / Allow sharing toggles, private labels, and the list filter."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library


def _seed(db):
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    a = lib.create_user("author", "supersecret", role="user")
    b = lib.create_user("reader", "supersecret", role="user")
    mk = lambda uid, q, **kw: lib.record_ask_question(uid, q, "Answer.", "m", "standard", True, False, True, **kw)
    ids = {}
    ids["shared"] = mk(a, "Shared question from author")
    ids["priv"] = mk(a, "Private question from author", is_private=True)
    ids["priv_follow"] = mk(a, "Private follow-up", conversation_id=str(ids["priv"]), turn_index=1, is_private=True)
    ids["mine_priv"] = mk(b, "Reader's own private question", is_private=True)
    ids["mine"] = mk(b, "Reader's own shared question")
    lib.close()
    return ids, a, b


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    ids, a, b = _seed(db)
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


def test_private_rows_are_never_listed_for_other_members(site):
    client, ids, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy").text)
    assert "Shared question from author" in sec
    assert "Private question from author" not in sec
    assert "Reader&#x27;s own private question" in sec or "own private question" in sec


def test_own_private_row_shows_lock_and_allow_sharing(site):
    client, ids, _ = site
    sec = _section(client("reader").get("/tools/fpa-buddy").text)
    i = sec.index("own private question")
    row = sec[i:sec.index("</details>", i)] if "</details>" in sec[i:] else sec[i:]
    assert "&#128274; Private" in row and ">Allow sharing</button>" in row
    j = sec.index("own shared question")
    row2 = sec[j:sec.index("</details>", j)] if "</details>" in sec[j:] else sec[j:]
    assert "&#128274;" not in row2 and ">Make private</button>" in row2


def test_admin_sees_private_with_lock_and_full_name(site):
    client, ids, _ = site
    sec = _section(client("boss").get("/tools/fpa-buddy").text)
    i = sec.index("Private question from author")
    row = sec[i:sec.index("</summary>", i)]
    assert "&#128274; Private" in row and "author" in row
    assert "Hide from members" in sec


def test_list_query_is_viewer_scoped(site):
    _, ids, db = site
    lib = Library(db)
    other = {r["id"] for r in lib.list_public_ask_questions(viewer_id=999)}
    admin = {r["id"] for r in lib.list_public_ask_questions(see_private=True)}
    lib.close()
    assert ids["priv"] not in other and ids["shared"] in other
    assert ids["priv"] in admin and ids["mine_priv"] in admin


def test_toggle_route_flips_whole_conversation_for_owner_only(site):
    client, ids, db = site
    r = client("author")
    assert r.post(f"/questions/{ids['shared']}/private", follow_redirects=False).status_code == 303
    lib = Library(db)
    assert lib.get_ask_question(ids["shared"])["is_private"] == 1
    lib.close()
    # someone else's: 403
    assert client("reader").post(f"/questions/{ids['shared']}/private", follow_redirects=False).status_code == 403
    # whole conversation moves together
    r.post(f"/questions/{ids['priv']}/private", follow_redirects=False)
    lib = Library(db)
    assert lib.get_ask_question(ids["priv"])["is_private"] == 0
    assert lib.get_ask_question(ids["priv_follow"])["is_private"] == 0
    lib.close()


def test_json_toggle_ownership_and_conversation_payloads(site):
    client, ids, db = site
    cid = str(ids["shared"])
    a = client("author")
    assert a.post(f"/ask/conversations/{cid}/private", json={"private": True}).json() == {"ok": True, "private": True}
    assert a.get(f"/ask/conversations/{cid}").json()["private"] is True
    convs = a.get("/ask/conversations").json()["conversations"]
    assert [c for c in convs if c["conversation_id"] == cid][0]["private"] is True
    assert client("reader").post(f"/ask/conversations/{cid}/private", json={"private": False}).status_code == 403
    assert a.post("/ask/conversations/nope/private", json={"private": True}).status_code == 404


def test_run_ask_sets_privacy_on_first_turn_and_follow_up_inherits(site, monkeypatch):
    _, ids, db = site
    import webapp.ask_orchestrator as o
    from types import SimpleNamespace

    def fake(lib, q, **kw):
        return SimpleNamespace(text="A", model="m", input_tokens=0, output_tokens=0,
                               cache_creation_tokens=0, cache_read_tokens=0, cost_usd=0.0,
                               rewrite_input_tokens=0, rewrite_output_tokens=0, rewrite_cost_usd=0.0,
                               embed_input_tokens=0, embed_cost_usd=0.0, exa_result_count=0,
                               exa_cost_usd=0.0, citations=[], sources=[], feed_sources=[],
                               web_sources=[], stop_reason="")
    monkeypatch.setattr("linklib.agent.answer_question", fake)
    monkeypatch.setattr(o, "stop_reason_of", lambda ans: "")
    lib = Library(db)
    uid = lib.conn.execute("SELECT id FROM users WHERE username='reader'").fetchone()["id"]
    d = o.run_ask(lib, uid, "New one", is_private=True)
    assert d["is_private"] is True
    d2 = o.run_ask(lib, uid, "Follow", conversation_id=d["conversation_id"], is_private=False)
    assert d2["is_private"] is True
    assert lib.get_ask_question(d2["turn_id"])["is_private"] == 1
    lib.close()


def test_page_has_checkbox_note_and_toggle_hooks(site):
    client, ids, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    assert 'id="ask-private"' in html and "Keep this question private" in html
    assert ("Shared with other members without your name. Admins can see every question. "
            "Leave out company names and figures you want kept confidential.") in html
    assert '<a href="/privacy">Privacy policy</a>' in html
    assert "Allow sharing" in html and "Make private" in html and 'id="fu-priv"' in html
    assert "private: (!followUp && !convoId)" in html


def test_privacy_and_history_copy(site):
    client, _, _ = site
    p = client("reader").get("/privacy").text
    assert "FP&amp;A Buddy questions" in p and "Keep this question private" in p
    h = client("reader").get("/ask/history").text
    assert "Unless you mark a question private" in h and "Some of your questions may also appear" not in h


def test_lock_and_label_strings_pass_voice_scanners():
    from html import unescape
    from linklib.voice_review import mechanical_findings, typography_findings_plain
    for t in (unescape("&#128274;"), "Make private", "Allow sharing", "Keep this question private",
              "Shared with other members without your name. Admins can see every question. Leave out company names and figures you want kept confidential.",
              "Hide from members", "Hidden from members", "Unhide"):
        assert not mechanical_findings(t) and not typography_findings_plain(t)


def test_browser_private_buttons_fit_128px_and_thread_toggle_works(site):
    from tests import test_buddy_phone_fixes as T
    client, ids, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    launched = T._launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        for w in (390, 1280):
            ctx = browser.new_context(viewport={"width": w, "height": 900}, is_mobile=(w < 500), has_touch=(w < 500))
            pg = ctx.new_page()
            pg.route(re.compile(r"^http://buddy\.test/.*"), T.Site(html).handle)
            pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
            pg.goto("http://buddy.test/tools/fpa-buddy")
            pg.evaluate("""() => { asked = true; convoId = 'c-1'; convoPrivate = false;
              document.getElementById('ask-thread').innerHTML = '<div class="ask-q-bubble">Q</div>';
              fuRender('ready'); }""")
            m = pg.evaluate("""() => { var b = document.getElementById('fu-priv'), out = [];
              ['Make private', 'Allow sharing'].forEach(function(t) { b.textContent = t;
                var r = b.getBoundingClientRect();
                out.push([r.width, r.height, b.scrollWidth > b.clientWidth]); });
              var d = document.getElementById('fu-done').getBoundingClientRect(), p = b.getBoundingClientRect();
              out.push([p.right <= d.left]); return out; }""")
            for width, height, clipped in m[:2]:
                assert abs(width - 128) < 0.6 and abs(height - 28) < 0.6 and not clipped
            assert m[2][0]    # the toggle sits left of Done
            ctx.close()
    finally:
        browser.close()
        pw.stop()


def test_private_conversation_is_absent_from_other_members_list_and_search(site):
    client, ids, _ = site
    html = client("reader").get("/tools/fpa-buddy?pq=Private").text
    assert "Private question from author" not in html and "Private follow-up" not in html
    # the conversation endpoints refuse someone else's private thread
    cid = str(ids["priv"])
    assert client("reader").get(f"/ask/conversations/{cid}").status_code == 403
    assert all(c["conversation_id"] != cid for c in client("reader").get("/ask/conversations").json()["conversations"])


def test_admin_hide_is_visible_undoable_and_separate_from_private(site):
    client, ids, db = site
    boss = client("boss")
    assert boss.post(f"/questions/{ids['shared']}/hide", follow_redirects=False).status_code == 303
    sec = _section(boss.get("/tools/fpa-buddy").text)
    i = sec.index("Shared question from author")
    row = sec[i:]
    assert "Hidden from members" in row and ">Unhide</button>" in row and "ask-ctl-admin" in row
    # members never see a hidden row
    assert "Shared question from author" not in _section(client("reader").get("/tools/fpa-buddy").text)
    # the asker's Make private / Allow sharing never touches the admin's hide
    client("author").post(f"/questions/{ids['shared']}/private", follow_redirects=False)
    client("author").post(f"/questions/{ids['shared']}/private", follow_redirects=False)
    lib = Library(db)
    assert lib.get_ask_question(ids["shared"])["hidden_public"] == 1
    lib.close()
    boss.post(f"/questions/{ids['shared']}/hide", follow_redirects=False)
    sec = _section(boss.get("/tools/fpa-buddy").text)
    assert ">Hide from members</button>" in sec and "Hidden from members<" not in sec


def test_hover_backgrounds_sit_inside_a_hover_media_query(site):
    client, _, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    for rule in (".ask-pq-sum:hover", ".ask-recent-item:hover", ".ask-turn-row:hover", ".ask-ctl:hover"):
        assert "@media(hover:hover){" + rule in html, rule
        assert ("\n" + rule) not in html, rule      # never also declared bare


def test_rating_slot_is_at_the_far_right_and_buttons_keep_a_width_floor(site):
    client, _, _ = site
    html = client("reader").get("/tools/fpa-buddy").text
    assert ".ask-pq-slot{flex:0 0 24px;width:24px" in html
    assert ".ask-ctl-w{min-width:128px" in html and "white-space:nowrap" in html
    sec = _section(html)
    # every row carries the slot; the rating sits inside it, after the date
    assert sec.count('class="ask-pq-slot"') == sec.count('<details class="ask-pq-row">')
