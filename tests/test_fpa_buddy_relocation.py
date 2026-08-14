"""Phase 2: FP&A Buddy relocation (/library/ask -> /tools/fpa-buddy) and the
Past Questions merge — a "search past questions" section folded into the
same page, filtered to only helpful-rated answers.

Access-tier coverage (member-gated, old routes gone, /ask untouched) lives in
test_access_tiers.py; this file covers the feature-specific acceptance
criteria: both the ask box and the past-questions search render on the new
page, the helpful-only filter actually filters (mixed-rating case), and the
dead nav.site-nav a[href="/ask"] CSS selector is gone.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def test_fpa_buddy_page_has_both_ask_box_and_past_questions_search(env):
    appmod, _ = env
    html = _member_client(appmod).get("/tools/fpa-buddy").text
    # The ask box (moved verbatim from /library/ask).
    assert 'id="ask-q"' in html
    assert 'onclick="doAsk()"' in html
    # The folded-in past-questions search.
    assert 'id="past-questions"' in html
    assert "Search past questions" in html
    assert '<input type="search" name="pq"' in html


def test_dead_ask_nav_selector_is_gone(env):
    appmod, _ = env
    html = _member_client(appmod).get("/tools/fpa-buddy").text
    assert 'nav.site-nav a[href="/ask"]' not in html


def test_past_questions_search_only_shows_helpful_rated(env):
    """Mixed-rating case: one question rated 'helpful', one rated
    'not_helpful', one left unrated. Only the helpful one should ever show
    up in the past-questions search, regardless of query."""
    appmod, db = env
    lib = Library(db)
    uid = lib.create_user("asker", "supersecret", role="user")
    helpful_id = lib.record_ask_question(
        uid, "What is a good rule of thumb for burn multiple?",
        "A burn multiple under 2x is generally considered efficient.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    unhelpful_id = lib.record_ask_question(
        uid, "How do I calculate CAC payback the wrong way?",
        "This answer turned out to be wrong.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    unrated_id = lib.record_ask_question(
        uid, "An unrated question nobody has judged yet.",
        "An answer with no feedback at all.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    lib.record_ask_feedback(helpful_id, uid, "helpful")
    lib.record_ask_feedback(unhelpful_id, uid, "not_helpful")
    lib.close()

    html = _member_client(appmod).get("/tools/fpa-buddy").text
    assert "What is a good rule of thumb for burn multiple?" in html
    assert "How do I calculate CAC payback the wrong way?" not in html
    assert "An unrated question nobody has judged yet." not in html


def test_past_questions_search_filters_by_text_too(env):
    appmod, db = env
    lib = Library(db)
    uid = lib.create_user("asker", "supersecret", role="user")
    burn_id = lib.record_ask_question(
        uid, "What is a good burn multiple?", "Under 2x is efficient.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    headcount_id = lib.record_ask_question(
        uid, "How do I plan headcount for next year?", "Start with a bottoms-up model.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    lib.record_ask_feedback(burn_id, uid, "helpful")
    lib.record_ask_feedback(headcount_id, uid, "helpful")
    lib.close()

    html = _member_client(appmod).get("/tools/fpa-buddy?pq=burn").text
    assert "What is a good burn multiple?" in html
    assert "How do I plan headcount for next year?" not in html


def test_post_ask_still_works(env, monkeypatch):
    """POST /ask (the API endpoint) is untouched by the page relocation —
    only the GET page that calls it moved."""
    appmod, _ = env
    import linklib.agent as agent

    def fake_answer(lib, question, **kwargs):
        return agent.Answer(text="EBITDA is earnings before interest, taxes, depreciation, and amortization.", model="m")

    monkeypatch.setattr(agent, "answer_question", fake_answer)
    c = _member_client(appmod)
    r = c.post("/ask", json={"question": "What is EBITDA?"})
    assert r.status_code == 200
    assert "answer" in r.json()
