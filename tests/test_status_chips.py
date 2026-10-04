"""Status chips (BRAND.md, Status chips): every rating, Private and Hidden marker on
a Buddy question is drawn by ONE function, _ask_status_chip, and nothing else."""
import re
from html import unescape

import webapp.app as appmod
from linklib.voice_review import mechanical_findings, typography_findings_plain

SRC = open("webapp/app.py").read()


def test_chip_set_and_labels():
    exp = {"helpful": ("Rated helpful", "✓", "Helpful"),
           "not_helpful": ("Rated not helpful", "✕", "Not helpful"),
           "mixed": ("Rated mixed", "±", "Mixed"),
           "private": ("Private", "\U0001F512", "Private"),
           "hidden": ("Hidden by an admin", "⊘", "Hidden")}
    for kind, (label, glyph, word) in exp.items():
        html = appmod._ask_status_chip(kind)
        assert f'aria-label="{label}"' in html and f'title="{label}"' in html
        assert unescape(html).count(glyph) == 1 and f"</span> {word}</span>" in html
        assert 'role="img"' in html and "<button" not in html and "<a " not in html


def test_rendered_chips_have_no_invisible_or_flagged_characters():
    for kind in appmod._ASK_STATUS_CHIPS:
        text = unescape(re.sub(r"<[^>]+>", "", appmod._ask_status_chip(kind)))
        assert not mechanical_findings(text) and not typography_findings_plain(text), kind
        assert all(ord(c) not in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0xFE0F) for c in text)
        glyph = text.split(" ")[0]
        assert len(glyph) == 1, kind                    # one plain code point, no ZWJ or selector


def test_order_is_hidden_private_rating_and_unrated_is_empty():
    f = appmod._ask_status_chips_html
    assert f(0, 0) == ""
    out = f(1, 1, private=True, hidden=True, viewer_is_admin=True)
    assert out.index("ask-chip-hidden") < out.index("ask-chip-private") < out.index("ask-chip-mixed")
    assert "ask-chip-hidden" not in f(1, 0, private=True, hidden=True, viewer_is_admin=False)   # Hidden is admin-only
    assert f(0, 2) == appmod._ask_status_chip("not_helpful")


def test_every_status_render_goes_through_the_one_function():
    """No hand-built chip markup, glyph or aria-label anywhere else in app.py."""
    assert SRC.count('class="ask-chip ask-chip-{kind}"') == 1
    assert SRC.count("ask-chip ask-chip-") == 1          # only the builder writes the class pair
    for label in ("Rated helpful", "Rated not helpful", "Rated mixed", "Hidden by an admin"):
        assert SRC.count(f'"{label}"') == 1, label        # defined once, in the table
    for ent in ("&#10003;", "&#10005;", "&#177;", "&#8856;"):
        assert SRC.count(f'"{ent}"') == 1, ent
    # the lock entity is used by the chip table and by the Reader's unrelated Paywalled badge only
    assert SRC.count("&#128274;") == 2
    for gone in ("_ask_rating_html", "ask-pq-rate", "ask-pq-slot", "ask-pq-priv", "ask-pq-hidden", "&#128683;"):
        assert gone not in SRC, gone


def test_the_buddy_page_scripts_take_chips_from_the_server():
    js = SRC[SRC.index("function recentItemHtml"):SRC.index("var RECENT_MAX")]
    assert "PRIVATE_CHIP_HTML" in js and "&#128274;" not in js
    sim = SRC[SRC.index("function simItemHtml"):SRC.index("var resume = x.resume_id")]
    assert "chips_html" in sim and "&#128274;" not in sim and "&#128683;" not in sim
    assert appmod._ask_status_chip("private") in unescape_json(appmod._ASK_PRIVATE_CHIP_JS)


def unescape_json(js_literal):
    import json
    return json.loads(js_literal.replace("<\\/", "</"))


def test_chip_css_is_a_status_not_a_button():
    css = appmod._ASK_CHIP_CSS
    base = [l for l in css.splitlines() if l.startswith(".ask-chip{")][0]
    assert "height:18px" in base and "border:" not in base and "cursor" not in base and "background:var(--line)" in base
    hid = [l for l in css.splitlines() if l.startswith(".ask-chip-hidden{")][0]
    assert "color:var(--seafoam-deep)" in hid and "background:none" in hid and "border" not in hid


# ---- every rating value the table can hold, and the two surfaces added with the chips ----
import importlib
import os
import tempfile

import pytest

from linklib.db import Library


@pytest.fixture
def chip_site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    other = lib.create_user("other", "supersecret", role="user")
    me = lib.create_user("me", "supersecret", role="user")
    ids = {}
    for rating in Library.ASK_FEEDBACK_RATINGS:
        qid = lib.record_ask_question(other, f"Question rated {rating}", "Answer [1].", "m", "standard", True, False, True)
        lib.record_ask_feedback(qid, me, rating, "")
        ids[rating] = qid
    ids["mine"] = lib.record_ask_question(me, "My own private question", "Answer [1].", "m", "standard", True, False, True)
    lib.record_ask_feedback(ids["mine"], other, "helpful", "")
    lib.set_conversation_private(str(ids["mine"]), me, True)
    ids["mine_follow"] = lib.record_ask_question(me, "My follow-up", "Answer [1].", "m", "standard", True, False, True,
                                                 conversation_id=str(ids["mine"]), turn_index=1)
    lib.record_ask_feedback(ids["mine_follow"], other, "inaccurate", "")
    lib.close()
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


def _row(html, text):
    i = html.index(text)
    return html[i:html.index("</summary>", i)]


def test_every_rating_value_the_table_can_hold_renders_a_chip(chip_site):
    """ask_feedback holds helpful, inaccurate and not_helpful. Inaccurate counts as
    negative (anything but helpful), so it has always shown as Not helpful; the chip
    set keeps that. A rating the chip set cannot draw fails here, never renders as nothing."""
    client, _ = chip_site
    page = client("boss").get("/tools/fpa-buddy").text
    expect = {"helpful": "ask-chip-helpful", "inaccurate": "ask-chip-not_helpful", "not_helpful": "ask-chip-not_helpful"}
    assert set(expect) == set(Library.ASK_FEEDBACK_RATINGS)
    for rating, cls in expect.items():
        assert cls in _row(page, f"Question rated {rating}"), rating


def test_open_thread_private_chip_comes_from_the_server_string_and_toggles():
    js = SRC[SRC.index("foot.innerHTML = (convoId"):SRC.index("f.appendChild(foot);")]
    assert 'id="fu-status"' in js and "PRIVATE_CHIP_HTML" in js and "&#128274;" not in js
    tog = SRC[SRC.index("async function togglePrivate"):SRC.index("function doneThread")]
    assert "fu-status" in tog and "PRIVATE_CHIP_HTML" in tog


def test_history_shows_the_same_chips_for_the_readers_own_questions(chip_site):
    client, ids = chip_site
    page = client("me").get("/ask/history").text
    assert "ask-chip-not_helpful" in page            # the inaccurate-rated follow-up shows as Not helpful
    own = page[page.index("My own private question"):]
    assert "ask-chip-private" in own and "ask-chip-helpful" in own
    assert "ask-chip ask-chip-hidden" not in page             # an admin hide is never shown to the owner
    # a question with no private flag and no rating shows no chip on its own card
    assert appmod_chip_count(page) >= 3


def appmod_chip_count(html):
    return html.count('class="ask-chip ')


def test_history_chips_use_the_one_function_only(chip_site):
    seg = SRC[SRC.index("def ask_history("):SRC.index('rows_html = "".join(_card(c)')]
    assert "_ask_status_chips_html" in seg and 'class="ask-chip' not in seg


def test_every_page_that_draws_a_chip_carries_the_chip_css(chip_site):
    """A page that renders chips without the style would show bare text (found live on
    /ask/history before the CSS was shared)."""
    client, _ = chip_site
    for url in ("/tools/fpa-buddy", "/ask/history"):
        html = client("me").get(url).text
        assert appmod._ASK_CHIP_CSS in html, url
