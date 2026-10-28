"""FP&A Buddy cost display: a rounded-up typical cost in the Depth label, and the
real metered cost of a turn under its answer, for the asker only.

Fixtures are invented. The label is a typical simple question (answer call at
model_pricing rates times typical tokens, query embedding, one search); the line
under an answer is ask_questions.cost_usd, the same total that counts toward the
monthly cap."""
import importlib
import math
import os
import re
import tempfile
import types

import pytest

from linklib import agent
from linklib.agent import Answer
from linklib.db import Library
from linklib.pricing import (compute_embedding_cost, compute_exa_cost, rates_for,
                             round_up_cent, turn_cost_line)

Q = "What does a typical annual planning calendar look like for a PE backed company?"


# -- the pure pieces ----------------------------------------------------------

def test_round_up_cent_never_goes_below_the_input():
    for x in (0.0001, 0.0284, 0.03, 0.0301, 0.0350001, 0.1168, 1.0):
        assert round_up_cent(x) >= x - 1e-12
        assert round_up_cent(x) - x < 0.0100001
    assert round_up_cent(0.03) == 0.03                       # an exact cent is not pushed up
    assert round_up_cent(0.0301) == 0.04
    assert round_up_cent(0.0284) == 0.03


def test_turn_cost_line_wording():
    assert turn_cost_line(0.1168) == "This question cost $0.12."
    assert turn_cost_line(0.0351) == "This question cost $0.04."
    assert turn_cost_line(0.002) == "This question cost less than $0.01."
    assert turn_cost_line(0) == "" and turn_cost_line(None) == ""
    assert "—" not in turn_cost_line(0.12) and "member" not in turn_cost_line(0.12).lower()


def _typical(model, tier, tokens, exa):
    r = rates_for(model)
    i, o = tokens
    return (i * r["input"] + o * r["output"]) / 1e6 + compute_embedding_cost(
        "text-embedding-3-small", agent.SIMPLE_QUESTION_EMBED_TOKENS) + exa


@pytest.mark.parametrize("tier", ["quick", "standard", "deep"])
def test_label_cost_is_never_below_the_typical_simple_turn_on_the_profile(tier):
    model = agent.EFFORT_SETTINGS[tier]["model"]
    exa = compute_exa_cost("search")
    label = agent.tier_label_cost(model, tier, None, exa)
    want = _typical(model, tier, agent.TIER_TOKEN_PROFILE[tier], exa)
    assert label >= want - 1e-12 and label - want < 0.0100001
    assert round(label * 100) == label * 100 or abs(label * 100 - round(label * 100)) < 1e-9   # whole cents


# -- the page label, against measured fixtures --------------------------------

@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    a = lib.create_user("author", "supersecret", role="user")
    b = lib.create_user("reader", "supersecret", role="user")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def client(user):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield client, db, a, b
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _labels(html):
    return {m.group(1): float(m.group(2)) for m in re.finditer(
        r'data-tier="(\w+)".*?<span class="ask-tag-cost">about \$(\d+\.\d\d)</span>', html, re.S)}


def test_page_labels_cover_the_measured_typical_turn_for_every_user(site):
    client, db, a, b = site
    # Twelve measured Standard first turns (invented): 8,000 in and 1,100 out on average.
    lib = Library(db)
    for _ in range(12):
        lib.record_ask_question(a, Q, "x", "claude-sonnet-4-6", "standard", True, True, False,
                                input_tokens=7000, cache_read_tokens=1000, output_tokens=1100, cost_usd=0.05)
    model = lib.get_role_model("buddy_standard")
    lib.close()
    exa = compute_exa_cost("search")
    want = _typical(model, "standard", (8000, 1100), exa)
    for user in ("reader", "boss"):
        labels = _labels(client(user).get("/tools/fpa-buddy").text)
        assert set(labels) == {"quick", "standard", "deep"}
        assert labels["standard"] >= want - 1e-12 and labels["standard"] - want < 0.0100001
    # No measured rows for the other tiers: the fixed profile applies, still never below it.
    lib = Library(db)
    for tier in ("quick", "deep"):
        mdl = lib.get_role_model(f"buddy_{tier}")
        w = _typical(mdl, tier, agent.TIER_TOKEN_PROFILE[tier], exa)
        assert labels[tier] >= w - 1e-12
    lib.close()


def test_label_has_no_benchmark_or_split_cost_and_no_old_admin_note(site):
    client, *_ = site
    html = client("boss").get("/tools/fpa-buddy").text
    assert "Dollar amounts are estimates" not in html
    assert "EXA_UNIT" not in html
    foot = re.search(r'<p class="ask-dd-foot" id="ask-dd-foot">([^<]*)</p>', html).group(1)
    assert "$" not in foot
    assert foot.startswith("Benchmark questions cost two to three times as much")


# -- the cost line under an answer ---------------------------------------------

def _stub(monkeypatch, **fields):
    def fake(lib, question, model="", effort="standard", use_library=True, use_feed=True,
             use_web=False, opml_path=None, history=None, budget_usd=None):
        return Answer(text="stub answer", model="claude-sonnet-4-6", **fields)
    monkeypatch.setattr("linklib.agent.answer_question", fake)


def test_cost_line_equals_the_stored_metered_total(site, monkeypatch):
    """answer + pre-step split + query embedding + search + rewrite, summed once."""
    client, db, a, b = site
    parts = dict(answer=0.0731, prestep=0.0031, embed=0.00002, exa=0.007, rewrite=0.0004)
    total = sum(parts.values())
    _stub(monkeypatch, cost_usd=total, prestep_cost_usd=parts["prestep"], embed_cost_usd=parts["embed"],
          exa_cost_usd=parts["exa"], rewrite_cost_usd=parts["rewrite"])
    d = client("author").post("/ask", json={"question": Q, "effort": "standard", "sources": ["library"]}).json()
    lib = Library(db)
    row = lib.conn.execute("SELECT cost_usd, prestep_cost_usd, embed_cost_usd, exa_cost_usd FROM ask_questions").fetchone()
    spent = lib.ask_cost_this_month(a)
    lib.close()
    assert row["cost_usd"] == pytest.approx(total)
    assert d["cost_usd"] == pytest.approx(row["cost_usd"])                  # the line's number is the stored one
    assert d["cost_line"] == turn_cost_line(row["cost_usd"]) == "This question cost $0.08."
    assert spent == pytest.approx(row["cost_usd"])                          # and it is what counts toward the cap
    assert row["prestep_cost_usd"] + row["embed_cost_usd"] + row["exa_cost_usd"] < row["cost_usd"]   # already inside it, not added again


def test_page_script_shows_the_line_under_the_answer(site):
    client, *_ = site
    html = client("author").get("/tools/fpa-buddy").text
    assert "function costLineHtml(d)" in html and "d.cost_line" in html
    assert "mdToHtml(d.answer) + cutoffHtml(d) + goDeeperHtml(d) + costLineHtml(d) + srcListHtml(d)" in html
    assert "mdToHtml(t.answer) + cutoffHtml(t) + goDeeperHtml(t) + costLineHtml(t) + srcListHtml(t)" in html


class _Client:
    def __init__(self, stop):
        resp = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Half an answer", citations=None)],
            usage=types.SimpleNamespace(input_tokens=1000, output_tokens=2500,
                                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
            stop_reason=stop)
        self.messages = types.SimpleNamespace(create=lambda **kw: resp)


def test_a_cut_off_turn_still_shows_its_real_cost(site, monkeypatch):
    client, db, a, b = site
    monkeypatch.setattr(agent, "_get_client", lambda: _Client("max_tokens"))
    d = client("author").post("/ask", json={"question": Q, "effort": "standard", "sources": ["library"]}).json()
    assert d["notice"] and d["cost_line"]
    lib = Library(db)
    stored = lib.conn.execute("SELECT cost_usd FROM ask_questions").fetchone()[0]
    lib.close()
    assert stored > 0 and d["cost_usd"] == pytest.approx(stored)
    assert d["cost_line"] == turn_cost_line(stored)


def test_a_budget_trimmed_turn_still_shows_its_real_cost(site, monkeypatch):
    client, db, a, b = site
    _stub(monkeypatch, cost_usd=0.1168, prestep_notice="Part of this question was answered with fewer lookups to stay within your monthly budget.")
    d = client("author").post("/ask", json={"question": Q, "effort": "standard", "sources": ["library"]}).json()
    assert "monthly budget" in d["notice"]
    assert d["cost_line"] == "This question cost $0.12."


class _Boom:
    class messages:
        @staticmethod
        def create(**kw):
            raise Exception("upstream 500")


def test_a_failed_turn_shows_no_cost_line_but_the_spend_still_counts(site, monkeypatch):
    client, db, a, b = site
    monkeypatch.setattr(agent, "_get_client", lambda: _Boom())
    monkeypatch.setattr(agent, "retrieve_exa", lambda q, opml, max_results=4, restrict=True: ([], 3, 0.007))
    c = client("author")
    r = c.post("/ask", json={"question": Q, "effort": "standard", "sources": ["library", "open_web"]})
    assert r.status_code == 502
    body = r.json()
    assert body.get("failed") is True
    assert not body.get("cost_line") and "cost_usd" not in body
    lib = Library(db)
    spent = lib.ask_cost_this_month(a)
    lib.close()
    assert spent > 0                                                       # counted toward the cap as before
    page = c.get("/tools/fpa-buddy").text
    assert "This question cost" not in page                                # never on a failed row's page
    assert "This question cost" not in c.get("/ask/history").text


def test_a_capped_turn_carries_no_cost_line(site, monkeypatch):
    client, db, a, b = site
    lib = Library(db)
    lib.set_user_ask_cap(a, 0.01)
    lib.record_ask_question(a, "earlier", "x", "claude-sonnet-4-6", "standard", True, True, False, cost_usd=5.0)
    lib.close()
    _stub(monkeypatch, cost_usd=0.1)
    d = client("author").post("/ask", json={"question": Q, "effort": "standard", "sources": ["library"]}).json()
    assert d.get("capped") is True                                         # nothing was recorded, so nothing to show
    assert not d.get("cost_line") and not d.get("cost_usd")


# -- past answers: the asker's own, never another user's ------------------------

def _seed_past(db, uid):
    lib = Library(db)
    rid = lib.record_ask_question(uid, Q, "A planning calendar answer", "claude-sonnet-4-6", "standard",
                                  True, True, False, cost_usd=0.1168)
    lib.conn.execute("UPDATE ask_questions SET conversation_id=? WHERE id=?", (str(rid), rid))
    lib.conn.commit()
    lib.close()
    return rid


def test_past_questions_list_shows_cost_to_the_asker_only(site):
    client, db, a, b = site
    _seed_past(db, a)
    own = client("author").get("/tools/fpa-buddy").text
    assert "This question cost $0.12." in own
    other = client("reader").get("/tools/fpa-buddy").text
    assert "A planning calendar answer" in other and "This question cost" not in other
    admin = client("boss").get("/tools/fpa-buddy").text
    assert "This question cost" not in admin                               # an admin is not the asker


def test_resumed_transcript_carries_each_turns_cost_line(site):
    client, db, a, b = site
    rid = _seed_past(db, a)
    d = client("author").get(f"/ask/conversations/{rid}").json()
    assert [t["cost_line"] for t in d["turns"]] == ["This question cost $0.12."]


def test_mcp_ask_result_carries_the_turn_cost(site, monkeypatch):
    """ask_fpa_buddy returns run_ask's dict unchanged, so cost_usd and cost_line ride along."""
    client, db, a, b = site
    _stub(monkeypatch, cost_usd=0.0351)
    from webapp.ask_orchestrator import run_ask
    lib = Library(db)
    try:
        out = run_ask(lib, a, Q, effort="standard", use_library=True, use_feed=False, use_web=False,
                      conversation_id="", opml_path=None)
    finally:
        lib.close()
    assert out["cost_usd"] == pytest.approx(0.0351)
    assert out["cost_line"] == "This question cost $0.04."
