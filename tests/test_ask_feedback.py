"""FP&A Buddy answer feedback + per-turn citation persistence.

Covers the three layers of the feature: the storage contract (one upserted
ask_feedback row per turn per user; ask_questions.citations_json snapshots
what a turn actually cited), the member API (POST /ask/feedback — auth,
validation, and you-rate-your-own-turns ownership), and the admin triage view
(/admin/fpa-buddy/feedback — admin-only, rating filter, cited sources rendered).
"""
import pathlib
import sys
import tempfile, os
import json

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def _feedback_rows(lib):
    return lib.conn.execute("SELECT * FROM ask_feedback").fetchall()


def test_rating_creates_exactly_one_row(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, 1, "helpful")
    rows = _feedback_rows(lib)
    assert len(rows) == 1
    assert rows[0]["rating"] == "helpful"
    assert rows[0]["comment"] == ""
    assert rows[0]["updated_at"] == ""   # never changed yet


def test_rerating_updates_in_place_never_stacks(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, 1, "helpful")
    lib.record_ask_feedback(qid, 1, "inaccurate", comment="numbers looked stale")
    rows = _feedback_rows(lib)
    assert len(rows) == 1
    assert rows[0]["rating"] == "inaccurate"
    assert rows[0]["comment"] == "numbers looked stale"
    assert rows[0]["updated_at"] != ""   # the change is timestamped
    # Re-rating back to helpful clears the stale "what was off?" note.
    lib.record_ask_feedback(qid, 1, "helpful", comment="")
    rows = _feedback_rows(lib)
    assert len(rows) == 1
    assert (rows[0]["rating"], rows[0]["comment"]) == ("helpful", "")


def test_one_row_per_user_not_per_turn(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, 1, "helpful")
    lib.record_ask_feedback(qid, 2, "not_helpful")
    assert len(_feedback_rows(lib)) == 2


def test_unknown_rating_rejected(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    with pytest.raises(ValueError):
        lib.record_ask_feedback(qid, 1, "meh")
    assert _feedback_rows(lib) == []


def test_feedback_counts_by_rating(lib):
    for i, rating in enumerate(["helpful", "helpful", "inaccurate"]):
        qid = lib.record_ask_question(1, f"q{i}", "a", "m", "standard", True, False, True)
        lib.record_ask_feedback(qid, 1, rating)
    counts = lib.ask_feedback_counts()
    assert counts == {"helpful": 2, "inaccurate": 1, "not_helpful": 0}
    # A `since` in the future excludes everything but every key stays present.
    assert lib.ask_feedback_counts(since="2999-01-01") == {
        "helpful": 0, "inaccurate": 0, "not_helpful": 0}


def test_list_ask_feedback_joins_turn_and_filters(lib):
    uid = lib.create_user("member1", "supersecret")
    q1 = lib.record_ask_question(uid, "first q", "answer one", "claude-sonnet-4-6",
                                 "standard", True, False, True, cost_usd=0.02)
    q2 = lib.record_ask_question(uid, "second q", "answer two", "claude-sonnet-4-6",
                                 "deep", True, False, True, cost_usd=0.05)
    lib.record_ask_feedback(q1, uid, "helpful")
    lib.record_ask_feedback(q2, uid, "not_helpful", comment="missed the point")
    all_rows = lib.list_ask_feedback()
    assert {r["question"] for r in all_rows} == {"first q", "second q"}
    assert all(r["rater_username"] == "member1" for r in all_rows)
    neg = lib.list_ask_feedback(rating="not_helpful")
    assert len(neg) == 1
    assert neg[0]["question"] == "second q"
    assert neg[0]["comment"] == "missed the point"
    assert neg[0]["cost_usd"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Citation persistence
# ---------------------------------------------------------------------------

MIXED_CITATIONS = [
    {"n": 1, "title": "Saved piece", "url": "https://ex.com/a", "type": "library", "article_id": 42},
    {"n": 2, "title": "Feed item", "url": "https://ex.com/b", "type": "feed"},
    {"n": 3, "title": "Web hit", "url": "https://ex.com/c", "type": "web"},
]


def test_citations_snapshot_round_trips_all_three_types(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, True, True,
                                  citations=MIXED_CITATIONS)
    stored = json.loads(lib.get_ask_question(qid)["citations_json"])
    assert stored == MIXED_CITATIONS
    # The library entry keeps its articles.id; feed/web are snapshot-only.
    assert stored[0]["article_id"] == 42
    assert "article_id" not in stored[1] and "article_id" not in stored[2]


def test_zero_citations_persist_cleanly(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    assert lib.get_ask_question(qid)["citations_json"] == "[]"
    qid2 = lib.record_ask_question(1, "q2", "a2", "m", "standard", True, False, True,
                                   citations=[])
    assert lib.get_ask_question(qid2)["citations_json"] == "[]"


def test_agent_library_citations_carry_article_id():
    """_build_source_documents tags library sent_docs with the articles.id,
    and _assemble_cited_answer carries it into the cited entry — that id is
    what makes a persisted citation traceable back to the archive row."""
    from linklib.agent import _build_source_documents

    lib_hits = [{"id": 7, "title": "T", "url": "https://ex.com/t", "summary": "body text"}]
    feed_items = [{"title": "F", "url": "https://ex.com/f", "summary": "feed text"}]
    _, sent_docs = _build_source_documents(lib_hits, feed_items)
    assert sent_docs[0]["article_id"] == 7
    assert "article_id" not in sent_docs[1]


# ---------------------------------------------------------------------------
# POST /ask/feedback endpoint + /admin/fpa-buddy/feedback view
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # POST /ask must take the deterministic no-key fallback path (which still
    # records the turn) — never a real API call from the test suite.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user", name="Member One")
    other = lib.create_user("member2", "supersecret", role="user")
    turn_id = lib.record_ask_question(
        uid, "What is CAC payback?", "It's the months to recover CAC.",
        "claude-sonnet-4-6", "standard", True, False, True,
        cost_usd=0.02, citations=MIXED_CITATIONS)
    lib.close()
    yield appmod, turn_id, uid, other
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(appmod, username, password):
    c = _client(appmod)
    c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    return c


def test_feedback_requires_auth(env):
    appmod, turn_id, _, _ = env
    r = _client(appmod).post("/ask/feedback", json={"question_id": turn_id, "rating": "helpful"})
    assert r.status_code == 401


def test_feedback_validation(env):
    appmod, turn_id, _, _ = env
    c = _login(appmod, "member1", "supersecret")
    assert c.post("/ask/feedback", json={"rating": "helpful"}).status_code == 400
    assert c.post("/ask/feedback", json={"question_id": turn_id, "rating": "meh"}).status_code == 400
    assert c.post("/ask/feedback", json={"question_id": 999999, "rating": "helpful"}).status_code == 404


def test_feedback_only_on_your_own_turns(env):
    appmod, turn_id, _, _ = env
    c = _login(appmod, "member2", "supersecret")
    r = c.post("/ask/feedback", json={"question_id": turn_id, "rating": "helpful"})
    assert r.status_code == 403


def test_feedback_happy_path_and_rerate(env):
    appmod, turn_id, uid, _ = env
    c = _login(appmod, "member1", "supersecret")
    assert c.post("/ask/feedback",
                  json={"question_id": turn_id, "rating": "helpful"}).json() == {"ok": True}
    assert c.post("/ask/feedback",
                  json={"question_id": turn_id, "rating": "inaccurate",
                        "comment": "the benchmark was outdated"}).json() == {"ok": True}
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        rows = lib.conn.execute("SELECT * FROM ask_feedback").fetchall()
        assert len(rows) == 1
        assert rows[0]["rating"] == "inaccurate"
        assert rows[0]["comment"] == "the benchmark was outdated"
        assert rows[0]["user_id"] == uid
    finally:
        lib.close()


def test_admin_feedback_view_is_admin_only(env):
    appmod, _, _, _ = env
    assert _client(appmod).get("/admin/fpa-buddy/feedback", follow_redirects=False).status_code == 303
    member = _login(appmod, "member1", "supersecret")
    assert member.get("/admin/fpa-buddy/feedback", follow_redirects=False).status_code == 303


def test_admin_feedback_view_renders_and_filters(env):
    appmod, turn_id, _, _ = env
    member = _login(appmod, "member1", "supersecret")
    member.post("/ask/feedback", json={"question_id": turn_id, "rating": "inaccurate",
                                       "comment": "numbers looked stale"})
    admin = _login(appmod, "admin", "adminpass")
    html = admin.get("/admin/fpa-buddy/feedback").text
    assert "What is CAC payback?" in html
    assert "numbers looked stale" in html
    # The turn's persisted citation snapshot renders as links, with the
    # library entry traceable back to its archive row.
    assert "https://ex.com/a" in html and "https://ex.com/c" in html
    assert "archive #42" in html
    # Filtering to a rating with no rows hides the entry.
    filtered = admin.get("/admin/fpa-buddy/feedback?rating=helpful").text
    assert "What is CAC payback?" not in filtered
    matching = admin.get("/admin/fpa-buddy/feedback?rating=inaccurate").text
    assert "What is CAC payback?" in matching


# ---------------------------------------------------------------------------
# Reviewed toggle (Phase 3) — same manual pattern as /admin/inbox/community-gaps,
# not auto-clear-on-view. See Library.toggle_ask_feedback_reviewed's docstring.
# ---------------------------------------------------------------------------

def test_new_feedback_row_starts_unreviewed(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, 1, "helpful")
    rows = _feedback_rows(lib)
    assert rows[0]["reviewed"] == 0


def test_toggle_ask_feedback_reviewed_flips_in_place(lib):
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    fid = lib.record_ask_feedback(qid, 1, "helpful")
    lib.toggle_ask_feedback_reviewed(fid)
    assert _feedback_rows(lib)[0]["reviewed"] == 1
    lib.toggle_ask_feedback_reviewed(fid)
    assert _feedback_rows(lib)[0]["reviewed"] == 0


def test_count_unreviewed_ask_feedback(lib):
    q1 = lib.record_ask_question(1, "q1", "a", "m", "standard", True, False, True)
    q2 = lib.record_ask_question(1, "q2", "a", "m", "standard", True, False, True)
    f1 = lib.record_ask_feedback(q1, 1, "helpful")
    lib.record_ask_feedback(q2, 1, "inaccurate")
    assert lib.count_unreviewed_ask_feedback() == 2
    lib.toggle_ask_feedback_reviewed(f1)
    assert lib.count_unreviewed_ask_feedback() == 1


def test_list_ask_feedback_reviewed_filter(lib):
    q1 = lib.record_ask_question(1, "q1", "a", "m", "standard", True, False, True)
    q2 = lib.record_ask_question(1, "q2", "a", "m", "standard", True, False, True)
    f1 = lib.record_ask_feedback(q1, 1, "helpful")
    lib.record_ask_feedback(q2, 1, "inaccurate")
    lib.toggle_ask_feedback_reviewed(f1)
    assert len(lib.list_ask_feedback(reviewed=True)) == 1
    assert len(lib.list_ask_feedback(reviewed=False)) == 1
    assert len(lib.list_ask_feedback()) == 2


def test_open_task_counts_reflects_unreviewed_ask_feedback(lib):
    from webapp import tasks
    qid = lib.record_ask_question(1, "q", "a", "m", "standard", True, False, True)
    fid = lib.record_ask_feedback(qid, 1, "helpful")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/fpa-buddy/feedback"] == 1
    lib.toggle_ask_feedback_reviewed(fid)
    counts = tasks.open_task_counts(lib)
    assert "/admin/fpa-buddy/feedback" not in counts


def test_admin_feedback_view_renders_reviewed_toggle(env):
    appmod, turn_id, _, _ = env
    member = _login(appmod, "member1", "supersecret")
    member.post("/ask/feedback", json={"question_id": turn_id, "rating": "inaccurate",
                                       "comment": "numbers looked stale"})
    admin = _login(appmod, "admin", "adminpass")
    html = admin.get("/admin/fpa-buddy/feedback").text
    assert "Mark reviewed" in html
    assert ">New<" in html   # unreviewed badge
    assert "Unreviewed" in html   # stat tile label


def test_admin_feedback_toggle_route_flips_and_redirects(env):
    appmod, turn_id, _, _ = env
    member = _login(appmod, "member1", "supersecret")
    member.post("/ask/feedback", json={"question_id": turn_id, "rating": "helpful"})
    admin = _login(appmod, "admin", "adminpass")

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        feedback_id = lib.conn.execute("SELECT id FROM ask_feedback").fetchone()["id"]
    finally:
        lib.close()

    r = admin.post(f"/admin/fpa-buddy/feedback/{feedback_id}/toggle-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/fpa-buddy/feedback"

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        assert lib.conn.execute("SELECT reviewed FROM ask_feedback WHERE id=?",
                                 (feedback_id,)).fetchone()["reviewed"] == 1
    finally:
        lib.close()

    html = admin.get("/admin/fpa-buddy/feedback").text
    assert "Mark unreviewed" in html
    assert ">Reviewed<" in html

    # A non-admin can't flip it either.
    non_admin_resp = _client(appmod).post(
        f"/admin/fpa-buddy/feedback/{feedback_id}/toggle-reviewed", follow_redirects=False)
    assert non_admin_resp.status_code == 303
    assert non_admin_resp.headers["location"] != "/admin/fpa-buddy/feedback"


def test_admin_feedback_view_reviewed_filter(env):
    appmod, turn_id, _, _ = env
    member = _login(appmod, "member1", "supersecret")
    member.post("/ask/feedback", json={"question_id": turn_id, "rating": "helpful"})
    admin = _login(appmod, "admin", "adminpass")

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        feedback_id = lib.conn.execute("SELECT id FROM ask_feedback").fetchone()["id"]
        lib.toggle_ask_feedback_reviewed(feedback_id)
    finally:
        lib.close()

    unreviewed_view = admin.get("/admin/fpa-buddy/feedback?reviewed=no").text
    assert "What is CAC payback?" not in unreviewed_view
    reviewed_view = admin.get("/admin/fpa-buddy/feedback?reviewed=yes").text
    assert "What is CAC payback?" in reviewed_view


def test_ask_response_includes_turn_id(env):
    """POST /ask returns the recorded row id as turn_id — what the feedback
    controls rate. No ANTHROPIC_API_KEY in tests, so the fallback answer path
    still records a turn (same as production when the key is missing)."""
    appmod, _, uid, _ = env
    c = _login(appmod, "member1", "supersecret")
    d = c.post("/ask", json={"question": "How should I size my FP&A team?"}).json()
    assert isinstance(d["turn_id"], int)
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib.get_ask_question(d["turn_id"])
        assert row is not None and row["user_id"] == uid
    finally:
        lib.close()
