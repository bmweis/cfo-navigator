"""FP&A Buddy similar-question suggestions: text-only Dice matching, the shared
visibility rule (private / hidden never reach another user), and the free
pre-send check route."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library
from linklib import similar_questions as sq


Q = "What does a typical annual planning calendar look like for a PE backed company?"


def _seed(db):
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    a = lib.create_user("author", "supersecret", role="user")
    b = lib.create_user("reader", "supersecret", role="user")
    mk = lambda uid, q, **kw: lib.record_ask_question(uid, q, "Answer.", "m", "standard", True, False, True, **kw)
    ids = {}
    ids["shared"] = mk(a, "Typical annual planning calendar for a PE backed company?")
    ids["priv"] = mk(a, "Annual planning calendar for a PE backed company (private)", is_private=True)
    ids["hidden"] = mk(a, "What does the annual planning calendar look like for a PE backed company, hidden")
    lib.set_ask_question_hidden(ids["hidden"], True)
    ids["mine_priv"] = mk(b, "My own annual planning calendar PE backed company question", is_private=True)
    ids["inaccurate"] = mk(a, "Annual planning calendar PE backed company, inaccurate one")
    lib.record_ask_feedback(ids["inaccurate"], b, "inaccurate")
    ids["follow"] = mk(a, "Annual planning calendar PE backed company follow-up",
                       conversation_id=str(ids["shared"]), turn_index=1)
    ids["unrelated"] = mk(a, "How should we price a usage based contract?")
    ids["helpful"] = mk(a, "Annual planning calendar for PE backed company, thumbs up")
    lib.record_ask_feedback(ids["helpful"], b, "helpful")
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


def _sugg(c, q=Q):
    r = c.post("/ask/similar", json={"question": q})
    assert r.status_code == 200
    return {s["id"]: s for s in r.json()["suggestions"]}


def test_dice_threshold_and_ranking():
    a = sq.tokens("annual planning calendar pe backed company")
    assert sq.dice(a, a) == 1.0
    assert sq.dice(a, sq.tokens("pricing usage based contract")) == 0.0
    assert sq.dice(frozenset(), a) == 0.0
    cands = [{"question": "annual planning calendar pe backed company", "helpful_count": 0},
             {"question": "annual planning calendar pe backed company", "helpful_count": 2},
             {"question": "something else entirely", "helpful_count": 5}]
    out = sq.rank_similar("annual planning calendar pe backed company", cands)
    assert [c["helpful_count"] for c in out] == [2, 0]      # thumbs-up first, non-matches dropped


def test_private_and_hidden_never_suggested_to_another_user(site):
    client, ids, _ = site
    got = _sugg(client("reader"))
    assert ids["priv"] not in got and ids["hidden"] not in got
    assert ids["shared"] in got or ids["helpful"] in got


def test_own_private_question_is_suggested_to_its_asker(site):
    client, ids, _ = site
    got = _sugg(client("reader"), "My own annual planning calendar PE backed company question")
    assert ids["mine_priv"] in got and got[ids["mine_priv"]]["private"] is True
    # ...but nobody else gets it
    assert ids["mine_priv"] not in _sugg(client("author"), "My own annual planning calendar PE backed company question")


def test_admin_sees_private_and_hidden_labelled(site):
    client, ids, _ = site
    c = client("boss")
    got = _sugg(c, "Annual planning calendar for a PE backed company (private)")
    assert got[ids["priv"]]["private"] is True
    got = _sugg(c, "What does the annual planning calendar look like for a PE backed company, hidden")
    assert got[ids["hidden"]]["hidden"] is True
    # a non-admin gets neither label because neither row is returned
    assert ids["hidden"] not in _sugg(client("reader"), "What does the annual planning calendar look like for a PE backed company, hidden")


def test_inaccurate_followups_and_unrelated_never_suggested(site):
    client, ids, _ = site
    for who in ("reader", "boss"):
        got = _sugg(client(who))
        assert ids["inaccurate"] not in got and ids["follow"] not in got and ids["unrelated"] not in got


def test_thumbs_up_ranks_first_and_at_most_three(site):
    client, ids, _ = site
    r = client("reader").post("/ask/similar", json={"question": Q}).json()["suggestions"]
    assert len(r) <= 3 and r[0]["id"] == ids["helpful"] and "Rated helpful" in r[0]["rating_html"]


def test_requires_login_and_blank_is_empty(site):
    client, ids, _ = site
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    assert TestClient(appmod.app).post("/ask/similar", json={"question": Q}).status_code in (401, 403)
    assert client("reader").post("/ask/similar", json={"question": "  "}).json() == {"suggestions": []}


def test_list_and_suggestions_share_one_visibility_rule(site, monkeypatch):
    """Both call Library.ask_visibility_clause; changing it changes both."""
    client, ids, db = site
    calls = []
    orig = Library.ask_visibility_clause
    monkeypatch.setattr(Library, "ask_visibility_clause",
                        staticmethod(lambda *a, **k: (calls.append(1), orig(*a, **k))[1]))
    lib = Library(db)
    lib.list_public_ask_questions(viewer_id=1, see_private=False)
    lib.similar_ask_candidates(1, False)
    lib.close()
    assert len(calls) == 2


def test_no_second_copy_of_the_visibility_rule():
    src = open("linklib/db.py").read()
    assert src.count("hidden_public=0") == 1       # only inside ask_visibility_clause


def test_page_has_panel_and_ask_anyway():
    import webapp.app as appmod
    importlib.reload(appmod)
    src = open("webapp/app.py").read()
    assert 'id="ask-sim"' in src and "Ask anyway" in src and "/ask/similar" in src


def test_new_strings_pass_voice_lint_and_say_users_not_members():
    src = open("webapp/app.py").read()
    seg = src[src.index("// Similar questions"):src.index("// followUp=false")]
    assert "member" not in seg.lower() and " — " not in seg and "&amp;" not in seg


def test_suggestions_draw_ratings_with_the_one_shared_function(site):
    """Same markup as the past-questions list; the function is _ask_rating_html and
    no rating markup is hand-built anywhere else on the Buddy surfaces."""
    client, ids, _ = site
    import webapp.app as appmod
    c = client("reader")
    got = _sugg(c)
    assert got[ids["helpful"]]["rating_html"] == appmod._ask_rating_html(1, 0)
    page = c.get("/tools/fpa-buddy").text
    assert appmod._ask_rating_html(1, 0) in page          # the list row, same string
    assert _sugg(c)[ids["shared"]]["rating_html"] == ""   # unrated: nothing
    src = open("webapp/app.py").read()
    for label in ("Rated helpful", "Rated mixed", "Rated not helpful"):
        assert src.count(label) == 1, label               # defined once, in the function


def test_suggestion_dates_are_yyyy_mm_dd(site):
    client, ids, _ = site
    for s in _sugg(client("reader")).values():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"])
    src = open("webapp/app.py").read()
    seg = src[src.index("// Similar questions"):src.index("// followUp=false")]
    assert "toLocale" not in seg and "Answered " not in seg
