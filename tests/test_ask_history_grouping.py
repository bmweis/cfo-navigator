"""Conversation grouping in /ask/history and /admin/ask-report (display-only).

Multi-turn FP&A Buddy conversations used to render as N unrelated rows in
both views. These tests pin the grouping helper's ordering rules and the two
views' grouped rendering — including legacy rows that predate conversation_id
(stored as the column default ''), which must stand alone rather than all
collapsing into one giant '' conversation. The CSV export stays flat.
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
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _seed(db: str) -> dict:
    """One 3-turn conversation, one single-turn, one legacy pair for member1;
    one single-turn for member2. Returns ids/costs for assertions."""
    lib = Library(db)
    uid1 = lib.create_user("member1", "supersecret", role="user")
    uid2 = lib.create_user("member2", "supersecret", role="user")

    first = lib.record_ask_question(uid1, "What is NRR?", "a1", "claude-sonnet-4-6",
                                    "standard", True, False, True, cost_usd=0.01)
    cid = str(first)
    lib.record_ask_question(uid1, "And for Series A?", "a2", "claude-sonnet-4-6",
                            "standard", True, False, True,
                            conversation_id=cid, turn_index=1, cost_usd=0.02)
    lib.record_ask_question(uid1, "What about GRR?", "a3", "claude-haiku-4-5-20251001",
                            "quick", True, False, True,
                            conversation_id=cid, turn_index=2, cost_usd=0.03)

    lib.record_ask_question(uid1, "Standalone question", "a4", "claude-sonnet-4-6",
                            "standard", True, False, True, cost_usd=0.05)

    # Legacy rows predating conversation_id: insert directly so the
    # record_ask_question backfill can't fill them in.
    for q in ("Legacy question one", "Legacy question two"):
        lib.conn.execute(
            "INSERT INTO ask_questions (conversation_id, turn_index, user_id, question,"
            " answer, model, effort, created_at, cost_usd)"
            " VALUES ('', 0, ?, ?, 'old answer', 'claude-sonnet-4-6', 'standard',"
            " '2025-01-01T00:00:00Z', 0.001)", (uid1, q))
    lib.conn.commit()

    lib.record_ask_question(uid2, "Other member question", "a5", "claude-sonnet-4-6",
                            "standard", True, False, True, cost_usd=0.04)
    lib.close()
    return {"uid1": uid1, "cid": cid}


def _member(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    return c


def _admin(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


def test_group_conversations_ordering_and_legacy(env):
    appmod, db = env
    _seed(db)
    lib = Library(db)
    rows = lib.list_ask_questions(limit=500)
    lib.close()
    convos = appmod._group_conversations(rows)
    # 3-turn convo + standalone + 2 legacy solos + other member = 5 conversations
    assert len(convos) == 5
    multi = [c for c in convos if len(c) > 1]
    assert len(multi) == 1 and len(multi[0]) == 3
    assert [t["question"] for t in multi[0]] == \
        ["What is NRR?", "And for Series A?", "What about GRR?"]   # turn order
    # Legacy '' rows each stand alone instead of merging into one bucket.
    legacy = [c for c in convos if c[0]["question"].startswith("Legacy")]
    assert len(legacy) == 2 and all(len(c) == 1 for c in legacy)
    # Conversations ordered by first-turn recency: legacy (2025) sort last.
    assert convos[-1][0]["question"].startswith("Legacy")
    assert convos[-2][0]["question"].startswith("Legacy")


def test_ask_history_groups_member_view(env):
    appmod, db = env
    _seed(db)
    html = _member(appmod).get("/ask/history").text
    # Multi-turn card: first question is the summary, follow-up count chip,
    # all turns present inside, conversation total on the summary line.
    assert "2 follow-ups" in html
    assert "<details" in html and "<summary" in html
    for q in ("What is NRR?", "And for Series A?", "What about GRR?"):
        assert q in html
    assert "$0.060 total" in html          # 0.01 + 0.02 + 0.03
    # Single-turn and legacy rows render as plain standalone cards.
    assert "Standalone question" in html
    assert "Legacy question one" in html and "Legacy question two" in html
    # Scoped to the signed-in member only.
    assert "Other member question" not in html


def test_admin_report_rollups_and_filter(env):
    appmod, db = env
    seeded = _seed(db)
    c = _admin(appmod)
    html = c.get("/admin/ask-report").text
    # Rollup: turn count chip, summed cost, distinct models used.
    assert "3 turns" in html
    assert "$0.0600" in html               # conversation cost rollup
    assert "haiku-4-5-20251001, sonnet-4-6" in html
    # Per-turn rows exist beneath, hidden until toggled.
    assert html.count(f'data-convo="{seeded["cid"]}"') == 3
    assert "toggleConvo" in html
    # Other users' rows still visible unfiltered.
    assert "Other member question" in html
    # User filter still works and shows whole conversations.
    filtered = c.get("/admin/ask-report", params={"user": "member1"}).text
    assert "3 turns" in filtered and "Other member question" not in filtered
    filtered2 = c.get("/admin/ask-report", params={"user": "member2"}).text
    assert "Other member question" in filtered2 and "3 turns" not in filtered2


def test_csv_export_stays_flat(env):
    appmod, db = env
    _seed(db)
    csv_text = _admin(appmod).get("/admin/ask-report/export.csv").text
    lines = [ln for ln in csv_text.strip().splitlines() if ln]
    # Header + one row per TURN (7 turns total across all conversations).
    assert len(lines) == 1 + 7
    assert "conversation_id" in lines[0]
