"""Phase B3/B4 (issue #738): FP&A Buddy's benchmark pre-step.

Every figure and publisher here is invented (tests/figures_fixtures.py). The
model is a fake client: no network, no API spend."""
import json
from types import SimpleNamespace

import pytest

from linklib import agent, benchmark_prestep as bp
from linklib.db import Article, Library
from tests import figures_fixtures as fx

SPLIT_JSON = json.dumps({
    "subqueries": [
        {"intent": "benchmark", "metric": "Net revenue retention", "band": "$25M ARR", "segment": "",
         "year": "", "text": "median net revenue retention at $25M ARR"},
        {"intent": "benchmark", "metric": "Burn multiple", "band": "$25M ARR", "segment": "",
         "year": "", "text": "burn multiple at $25M ARR"},
        {"intent": "other", "metric": "", "band": "", "segment": "", "year": "",
         "text": "how to structure an FP&A team at that size"},
    ],
    "rest_of_question": "What the FP&A team should look like."})
MULTI_Q = ("At about $25M ARR, how do our net revenue retention and burn multiple compare to peers, "
           "and how should we structure the FP&A team?")


class FakeClient:
    """Answers the split call (recognised by its system prompt) and the answer
    call, recording every request."""
    def __init__(self, split_text=SPLIT_JSON, split_raises=False, answer_raises=False, cite_first_doc=False):
        self.calls = []
        self.split_text, self.split_raises, self.answer_raises = split_text, split_raises, answer_raises
        self.cite_first_doc = cite_first_doc
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if kw["system"] == bp.SPLIT_SYSTEM:
            if self.split_raises:
                raise RuntimeError("split upstream 500 secret-xyz")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.split_text)],
                                   usage=SimpleNamespace(input_tokens=900, output_tokens=300))
        if self.answer_raises:
            raise RuntimeError("answer upstream 500")
        cites = [{"type": "char_location", "document_index": 0, "cited_text": "x",
                  "document_title": "t", "start_char_index": 0, "end_char_index": 1}] if self.cite_first_doc else []
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Answer text.", citations=cites)],
            usage=SimpleNamespace(input_tokens=2000, output_tokens=400,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
            stop_reason="end_turn")

    def answer_calls(self):
        return [c for c in self.calls if c["system"] != bp.SPLIT_SYSTEM]

    def split_calls(self):
        return [c for c in self.calls if c["system"] == bp.SPLIT_SYSTEM]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(agent, "_client", None)
    # No embeddings and no Exa: these tests are about the pre-step.
    monkeypatch.setattr("linklib.db.Library.vector_search_available", lambda self: False)


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    fx.seed_standard(db)
    yield db
    db.close()


def _client(monkeypatch, **kw) -> FakeClient:
    c = FakeClient(**kw)
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    return c


def _docs(call) -> list[dict]:
    return [b for b in call["messages"][-1]["content"] if b.get("type") == "document"]


def _texts(call) -> list[str]:
    return [b["text"] for b in call["messages"][-1]["content"] if b.get("type") == "text"]


def _doc_text(d) -> str:
    return d["source"]["data"]


def _ask(lib, q, **kw):
    kw.setdefault("use_feed", False)
    return agent.answer_question(lib, q, **kw)


# --- B3.1: rules ---------------------------------------------------------------

def test_a_question_with_no_registered_metric_is_untouched(lib, monkeypatch):
    c = _client(monkeypatch)
    ans = _ask(lib, "How should I structure my sales compensation plan?")
    assert not ans.failed and ans.prestep == {} and ans.prestep_cost_usd == 0
    assert c.split_calls() == [] and _docs(c.answer_calls()[0]) == []
    assert agent._PRESTEP_RULES not in c.answer_calls()[0]["system"]


def test_one_metric_gets_figures_documents_and_no_model_call(lib, monkeypatch):
    c = _client(monkeypatch)
    ans = _ask(lib, "What is the median net revenue retention at $15M ARR?")
    assert c.split_calls() == [] and ans.prestep["split_ran"] is False and ans.prestep["split_fallback"] == ""
    call = c.answer_calls()[0]
    titles = [d["title"] for d in _docs(call)]
    assert "Figures: Example Benchmarks 2099 (2099)" in titles
    assert agent._PRESTEP_RULES in call["system"]
    body = next(_doc_text(d) for d in _docs(call) if d["title"].startswith("Figures: Example Benchmarks 2099"))
    assert "$10M-$20M" in body and "match: exact" in body and "value 104" in body
    assert ans.prestep["detected"] == ["Net revenue retention"]


def test_figure_citation_resolves_to_the_report_landing_url(lib, monkeypatch):
    lib.set_report_landing_manual("eb-2099", "https://reports.example.test/eb-2099")
    _client(monkeypatch, cite_first_doc=True)
    ans = _ask(lib, "What is median net revenue retention at $15M ARR?")
    assert ans.citations[0]["url"] == "https://reports.example.test/eb-2099"
    assert ans.citations[0]["title"].startswith("Figures: Example Benchmarks 2099")


def test_report_without_a_link_is_cited_by_name_with_the_note(lib, monkeypatch):
    c = _client(monkeypatch, cite_first_doc=True)
    ans = _ask(lib, "What is median net revenue retention at $15M ARR?")
    assert "No link on file for this report" in _doc_text(_docs(c.answer_calls()[0])[0])
    assert ans.citations[0]["url"] == "" and ans.citations[0]["title"].startswith("Figures: Example Benchmarks 2099")


def test_a_band_between_printed_bands_is_flagged_in_the_prompt(lib, monkeypatch):
    c = _client(monkeypatch)
    _ask(lib, "What is the median net revenue retention at $25M ARR?")
    body = next(_doc_text(d) for d in _docs(c.answer_calls()[0]) if d["title"].startswith("Figures: Example Benchmarks 2099"))
    assert body.count("match: adjacent_band") >= 2 and "you asked for '$25M'" in body


def test_no_data_says_so_in_the_lookup_notes(lib, monkeypatch):
    lib.conn.execute("DELETE FROM benchmark_figures")
    lib.conn.commit()
    c = _client(monkeypatch)
    ans = _ask(lib, "What is the median net revenue retention at $25M ARR?")
    notes = "\n".join(_texts(c.answer_calls()[0]))
    assert "No stored figure matches" in notes and "do not estimate" in notes.lower()
    assert not any(d["title"].startswith("Figures:") for d in _docs(c.answer_calls()[0]))
    assert ans.prestep["subs"][0]["figure_rows"] == 0


def test_change_over_time_asks_for_every_edition(lib, monkeypatch):
    c = _client(monkeypatch)
    _ask(lib, "How has median net revenue retention changed over time at $15M ARR?")
    titles = [d["title"] for d in _docs(c.answer_calls()[0])]
    assert any("2098" in t for t in titles) and any("2099" in t for t in titles)


def test_default_is_newest_edition_only(lib, monkeypatch):
    c = _client(monkeypatch)
    _ask(lib, "What is the median net revenue retention at $15M ARR?")
    titles = [d["title"] for d in _docs(c.answer_calls()[0])]
    assert not any("2098" in t for t in titles)
    assert "Older editions exist" in "\n".join(_texts(c.answer_calls()[0]))


def test_ambiguous_alias_question_flags_every_owner(lib, monkeypatch):
    fx.load_report(lib, "eb-2099", [fx.frow(metric="MRR", value="5"), fx.frow(metric="Net New ARR", value="7")])
    c = _client(monkeypatch)
    _ask(lib, "What is the benchmark for net new mrr?")
    text = " ".join(_doc_text(d) for d in _docs(c.answer_calls()[0]))
    assert "different_definition" in text and "more than one metric" in "\n".join(_texts(c.answer_calls()[0]))


# --- B3.2: the split -------------------------------------------------------------

def test_multi_part_question_splits_with_one_small_call(lib, monkeypatch):
    c = _client(monkeypatch)
    ans = _ask(lib, MULTI_Q)
    assert len(c.split_calls()) == 1 and c.split_calls()[0]["model"] == "claude-haiku-4-5-20251001"
    assert ans.prestep["split_ran"] and len(ans.prestep["subs"]) == 3
    assert ans.prestep_cost_usd > 0 and ans.cost_usd >= ans.prestep_cost_usd
    assert ans.prestep["rest_of_question"].startswith("What the FP&A team")
    plan = "\n".join(_texts(c.answer_calls()[0]))
    assert "split into parts" in plan and "Answer every part" in plan


def test_the_split_model_is_the_admin_chosen_role(lib, monkeypatch):
    assert lib.set_role_model("prestep", "claude-sonnet-4-6") == []
    c = _client(monkeypatch)
    _ask(lib, MULTI_Q)
    assert c.split_calls()[0]["model"] == "claude-sonnet-4-6"


def test_a_single_metric_one_sentence_question_never_splits(lib, monkeypatch):
    c = _client(monkeypatch)
    _ask(lib, "What is the burn multiple at $15M ARR?")
    assert c.split_calls() == []


def test_two_metrics_split_without_a_broader_ask(lib, monkeypatch):
    c = _client(monkeypatch, split_text=json.dumps({"subqueries": [
        {"intent": "benchmark", "metric": "NRR", "text": "nrr"}, {"intent": "benchmark", "metric": "Burn multiple", "text": "burn"}],
        "rest_of_question": ""}))
    ans = _ask(lib, "Compare net revenue retention and burn multiple at $15M ARR.")
    assert len(c.split_calls()) == 1 and len(ans.prestep["subs"]) == 2


# --- B3.3: caps and budgets, per sub-query ---------------------------------------

def test_each_subquery_gets_the_tiers_full_library_cap(lib, monkeypatch):
    c = _client(monkeypatch)
    seen = []

    def fake_retrieve(lib_, question, max_sources=8, *, embedded=agent._UNSET):
        seen.append((question, max_sources))
        return [], 0, 0.0
    monkeypatch.setattr(agent, "retrieve", fake_retrieve)
    _ask(lib, MULTI_Q, effort="standard")
    assert [m for _, m in seen] == [agent.EFFORT_SETTINGS["standard"]["max_library"]] * 3
    assert [q for q, _ in seen] == ["median net revenue retention at $25M ARR", "burn multiple at $25M ARR",
                                    "how to structure an FP&A team at that size"]
    seen.clear()
    _ask(lib, MULTI_Q, effort="deep")
    assert {m for _, m in seen} == {agent.EFFORT_SETTINGS["deep"]["max_library"]}


def test_each_subquery_keeps_its_own_character_budget(lib, monkeypatch):
    _client(monkeypatch)
    budget = agent.EFFORT_SETTINGS["standard"]["global_chars"]
    big = lambda tag, i: {"id": hash((tag, i)) % 10**6, "title": f"{tag} {i}", "url": f"https://x.example.test/{tag}{i}",
                          "summary": "", "content": "w" * 5000}

    def fake_retrieve(lib_, question, max_sources=8, *, embedded=agent._UNSET):
        tag = question.split()[0]
        return [big(tag, i) for i in range(max_sources)], 0, 0.0
    monkeypatch.setattr(agent, "retrieve", fake_retrieve)
    c = FakeClient()
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    _ask(lib, MULTI_Q, effort="standard")
    lib_docs = [d for d in _docs(c.answer_calls()[0]) if not d["title"].startswith("Figures:")]
    total = sum(len(_doc_text(d)) for d in lib_docs)
    per_source = agent.EFFORT_SETTINGS["standard"]["source_chars"]
    assert total == 3 * 8 * per_source > budget          # 3 sub-queries, none sharing one budget
    assert total <= 3 * budget


def test_a_hit_found_by_two_subqueries_is_sent_once(lib, monkeypatch):
    hit = {"id": 7, "title": "Shared", "url": "https://x.example.test/s", "summary": "", "content": "text " * 50}
    monkeypatch.setattr(agent, "retrieve", lambda *a, **k: ([dict(hit)], 0, 0.0))
    c = _client(monkeypatch)
    _ask(lib, MULTI_Q)
    assert [d["title"] for d in _docs(c.answer_calls()[0])].count("Shared") == 1


def test_embeddings_run_per_subquery_and_are_costed(lib, monkeypatch):
    monkeypatch.setattr("linklib.db.Library.vector_search_available", lambda self: True)
    monkeypatch.setattr("linklib.db.Library.vector_search", lambda self, v, limit=8: [])
    calls = []

    def fake_embed(text):
        calls.append(text)
        return SimpleNamespace(vectors=[[0.1]], input_tokens=10, cost_usd=0.001)
    monkeypatch.setattr("linklib.embeddings.embed_text", fake_embed)
    _client(monkeypatch)
    ans = _ask(lib, MULTI_Q)
    assert len(calls) == 3 and ans.embed_cost_usd == pytest.approx(0.003)


# --- B3.2/B3.3: fallbacks ----------------------------------------------------------

def test_splitter_exception_falls_back_to_one_lookup_and_records_it(lib, monkeypatch):
    c = _client(monkeypatch, split_raises=True)
    ans = _ask(lib, MULTI_Q)
    assert not ans.failed and ans.text == "Answer text."
    assert ans.prestep["split_fallback"] == "call_failed" and ans.prestep["split_ran"] is False
    assert "secret-xyz" not in json.dumps(ans.prestep) and "secret-xyz" not in ans.text
    titles = [d["title"] for d in _docs(c.answer_calls()[0])]
    assert any(t.startswith("Figures:") for t in titles)       # the rules-only lookup still ran
    assert ans.prestep["subs"][0]["metrics"] == ["net revenue retention", "burn multiple"]


@pytest.mark.parametrize("raw", ["not json at all", "[1, 2]", '{"subqueries": []}', '{"subqueries": "x"}',
                                 '{"subqueries":[{"intent":"benchmark"}]}', ""])
def test_unusable_split_output_falls_back_and_is_never_shown(lib, monkeypatch, raw):
    c = _client(monkeypatch, split_text=raw)
    ans = _ask(lib, MULTI_Q)
    assert not ans.failed and ans.prestep["split_fallback"] == "invalid_json"
    assert (raw == "" or raw not in ans.text) and (raw == "" or raw not in json.dumps(ans.prestep))
    assert ans.prestep_cost_usd > 0                              # the call happened, so it is billed


def test_a_budget_that_cannot_cover_the_split_skips_it(lib, monkeypatch):
    c = _client(monkeypatch)
    ans = _ask(lib, MULTI_Q, budget_usd=0.0001)
    assert c.split_calls() == [] and ans.prestep["split_fallback"] == "over_cap" and ans.prestep_cost_usd == 0
    assert not ans.failed


def test_a_split_that_does_not_fit_degrades_to_fewer_parts_with_a_notice(lib, monkeypatch):
    s = agent.EFFORT_SETTINGS["standard"]
    two = bp.estimate_extra_usd(s["model"], "standard", s, 2, True)
    three = bp.estimate_extra_usd(s["model"], "standard", s, 3, False)
    budget = two + 0.0005                                    # covers the call and two parts, not three
    assert bp.estimate_extra_usd(s["model"], "standard", s, 3, False) > budget - 0.003
    c = _client(monkeypatch)
    ans = _ask(lib, MULTI_Q, budget_usd=budget)
    assert ans.prestep["split_ran"] and len(ans.prestep["subs"]) == 2 < ans.prestep["requested_subs"]
    assert "covered 2 of the 3 parts" in ans.prestep_notice
    assert not ans.failed


def test_unlimited_budget_never_degrades(lib, monkeypatch):
    _client(monkeypatch)
    ans = _ask(lib, MULTI_Q, budget_usd=None)
    assert ans.prestep_notice == "" and len(ans.prestep["subs"]) == 3


def test_subquery_ceiling_is_four():
    many = json.dumps({"subqueries": [{"intent": "benchmark", "metric": "NRR", "text": f"q{i}"} for i in range(9)]})
    subs, _ = bp.parse_split(many)
    assert len(subs) == 4 == bp.MAX_SUBQUERIES


def test_parse_split_cleans_fences_and_unknown_fields():
    subs, rest = bp.parse_split('```json\n{"subqueries":[{"intent":"weird","metric":"NRR","year":"20x","text":"t"}],'
                                '"rest_of_question":"r"}\n```')
    assert subs[0].intent == "other" and subs[0].year == "" and rest == "r"


# --- B3.4/5: definitions -----------------------------------------------------------

def test_workbook_card_precedes_every_other_source_and_other_defs_are_newest_first(lib, monkeypatch):
    fx.add_digest(lib, "saas-metric-burn-multiple", text="Burn multiple = net burn / net new ARR.")
    old = {"id": 901, "title": "Older definition", "url": "https://x.example.test/old", "summary": "", "content": "old def " * 20, "published_at": "2019-01-01"}
    new = {"id": 902, "title": "Newer definition", "url": "https://x.example.test/new", "summary": "", "content": "new def " * 20, "published_at": "2024-01-01"}
    monkeypatch.setattr(agent, "retrieve", lambda *a, **k: ([old, new], 0, 0.0))
    c = _client(monkeypatch, split_text=json.dumps({"subqueries": [
        {"intent": "definition", "metric": "Burn multiple", "text": "define burn multiple"},
        {"intent": "benchmark", "metric": "NRR", "text": "nrr benchmark"}], "rest_of_question": ""}))
    ans = _ask(lib, "How is burn multiple defined, and what is the benchmark for net revenue retention? Also what about hiring?")
    titles = [d["title"] for d in _docs(c.answer_calls()[0])]
    assert titles[0] == "Burn multiple (workbook definition)"
    assert titles.index("Newer definition") < titles.index("Older definition")
    assert titles.index("Burn multiple (workbook definition)") < titles.index("Newer definition")
    assert "workbook definition" in c.answer_calls()[0]["system"]


def test_single_definition_question_sends_the_card_first_without_a_split(lib, monkeypatch):
    fx.add_digest(lib, "saas-metric-burn-multiple", text="Burn multiple = net burn / net new ARR.")
    c = _client(monkeypatch)
    _ask(lib, "How is burn multiple calculated?")
    docs = _docs(c.answer_calls()[0])
    assert docs[0]["title"] == "Burn multiple (workbook definition)" and "net burn" in _doc_text(docs[0])
    assert c.split_calls() == []


# --- failure and cost ----------------------------------------------------------------

def test_a_failed_answer_after_a_split_is_never_an_answer_and_keeps_the_spend(lib, monkeypatch):
    _client(monkeypatch, answer_raises=True)
    ans = _ask(lib, MULTI_Q)
    assert ans.failed and ans.text == "" and "upstream 500" in ans.error
    assert ans.cost_usd == pytest.approx(ans.prestep_cost_usd) and ans.prestep_cost_usd > 0
    assert ans.prestep["split_ran"]


def test_figures_documents_have_their_own_budget_and_say_what_was_left_out(lib, monkeypatch):
    fx.load_report(lib, "eb-2099", [fx.frow(band=f"${i}M-${i + 1}M", value=str(i), note="n" * 40) for i in range(1, 120)])
    monkeypatch.setitem(bp.FIGURES_CHARS, "standard", 1500)
    c = _client(monkeypatch)
    ans = _ask(lib, "What is median net revenue retention?")
    text = " ".join(_doc_text(d) for d in _docs(c.answer_calls()[0]) if d["title"].startswith("Figures:"))
    assert "left out for length" in text and ans.prestep["subs"][0]["figure_rows_left_out"] > 0
    assert "left out for length" in "\n".join(_texts(c.answer_calls()[0]))


def test_no_library_means_no_lookup(lib, monkeypatch):
    c = _client(monkeypatch)
    ans = _ask(lib, "What is median net revenue retention?", use_library=False)
    assert ans.prestep == {} and not any(d["title"].startswith("Figures:") for d in _docs(c.answer_calls()[0]))


def test_a_prestep_crash_is_logged_and_the_turn_continues(lib, monkeypatch):
    monkeypatch.setattr(bp, "plan_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _client(monkeypatch)
    ans = _ask(lib, "What is median net revenue retention?")
    assert not ans.failed and ans.prestep["split_fallback"] == "prestep_error"


# --- B4: the turn log ---------------------------------------------------------------

def _user(lib):
    lib.conn.execute("INSERT INTO users (username, role, active, created_at) VALUES ('u1','user',1,'')")
    lib.conn.commit()
    return lib.get_user("u1")["id"]


def test_run_ask_records_the_log_and_the_split_cost(lib, monkeypatch):
    from webapp import ask_orchestrator
    _client(monkeypatch)
    uid = _user(lib)
    out = ask_orchestrator.run_ask(lib, uid, MULTI_Q, use_feed=False)
    row = lib.list_ask_questions(user_id=None, limit=1)[0]
    log = json.loads(row["prestep_json"])
    assert log["split_ran"] and len(log["subs"]) == 3 and log["detected"] == ["Net revenue retention", "Burn multiple"]
    assert row["prestep_cost_usd"] > 0 and row["cost_usd"] >= row["prestep_cost_usd"]
    assert "match_mix" in log and out["answer"] == "Answer text."


def test_the_split_cost_counts_against_the_users_monthly_cap(lib, monkeypatch):
    from webapp import ask_orchestrator
    _client(monkeypatch)
    uid = _user(lib)
    before = lib.ask_cost_this_month(uid)
    ask_orchestrator.run_ask(lib, uid, MULTI_Q, use_feed=False)
    spent = lib.ask_cost_this_month(uid) - before
    row = lib.list_ask_questions(user_id=None, limit=1)[0]
    assert spent == pytest.approx(row["cost_usd"]) and row["prestep_cost_usd"] > 0


def test_run_ask_passes_the_remaining_budget_to_the_pre_step(lib, monkeypatch):
    from webapp import ask_orchestrator
    seen = {}
    real = agent.answer_question

    def spy(lib_, q, **kw):
        seen.update(kw)
        return real(lib_, q, **kw)
    monkeypatch.setattr("linklib.agent.answer_question", spy)
    _client(monkeypatch)
    uid = _user(lib)
    ask_orchestrator.run_ask(lib, uid, MULTI_Q, use_feed=False)
    assert seen["budget_usd"] == pytest.approx(lib.get_effective_ask_cap(uid) - 0.0, abs=0.05)


def test_a_budget_notice_reaches_the_user(lib, monkeypatch):
    from webapp import ask_orchestrator
    s = agent.EFFORT_SETTINGS["standard"]
    two = bp.estimate_extra_usd(s["model"], "standard", s, 2, True)
    uid = _user(lib)
    lib.set_user_ask_cap(uid, round(two + 0.0005, 4)) if hasattr(lib, "set_user_ask_cap") else None
    _client(monkeypatch)
    out = ask_orchestrator.run_ask(lib, uid, MULTI_Q, use_feed=False)
    if hasattr(lib, "set_user_ask_cap"):
        assert "parts of this question" in out["notice"]


def test_a_failed_turn_still_records_the_prestep_spend(lib, monkeypatch):
    from webapp import ask_orchestrator
    _client(monkeypatch, answer_raises=True)
    uid = _user(lib)
    with pytest.raises(ask_orchestrator.AskTurnFailed):
        ask_orchestrator.run_ask(lib, uid, MULTI_Q, use_feed=False)
    row = lib.list_ask_questions(user_id=None, limit=1)[0]
    assert row["failed"] == 1 and row["prestep_cost_usd"] > 0 and json.loads(row["prestep_json"])["split_ran"]


def test_ordinary_turns_leave_the_log_empty(lib, monkeypatch):
    from webapp import ask_orchestrator
    _client(monkeypatch)
    uid = _user(lib)
    ask_orchestrator.run_ask(lib, uid, "How should I run quarterly planning?", use_feed=False)
    row = lib.list_ask_questions(user_id=None, limit=1)[0]
    assert row["prestep_json"] == "" and row["prestep_cost_usd"] == 0


# --- admin report + no-link rendering ----------------------------------------------

def test_admin_report_shows_the_lookup_and_names_a_fallback(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "pw")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "s.opml"))
    import webapp.app as appmod
    importlib.reload(appmod)
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    fx.seed_standard(db)
    uid = _user(db)
    from webapp import ask_orchestrator
    monkeypatch.setattr(agent, "_get_client", lambda: FakeClient())
    ask_orchestrator.run_ask(db, uid, MULTI_Q, use_feed=False)
    monkeypatch.setattr(agent, "_get_client", lambda: FakeClient(split_raises=True))
    ask_orchestrator.run_ask(db, uid, MULTI_Q + " Thanks.", use_feed=False)
    db.close()
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "pw"})
    html = c.get("/admin/fpa-buddy/report").text
    assert "Benchmark lookup: Metrics: Net revenue retention, Burn multiple" in html
    assert "split into 3 of 3 parts" in html and "figure rows" in html
    assert "the split call failed; one rules-only lookup ran" in html


def test_a_citation_without_a_url_renders_by_name_with_the_note(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    import webapp.app as appmod
    importlib.reload(appmod)
    cites = json.dumps([{"n": 1, "title": "Figures: Example Benchmarks 2099 (2099)", "url": "", "type": "library"}])
    _, sources = appmod._render_cited_answer("The median is 104.[1]", cites)
    assert "no link on file" in sources and 'href=""' not in sources


# --- the "broader ask" rule -------------------------------------------------------

@pytest.mark.parametrize("q,mixed", [
    ("What is the median net revenue retention at $15M ARR?", False),
    ("How is burn multiple calculated?", False),
    ("What is the median NRR for B2B SaaS companies in North America at $15M ARR?", False),
    ("What is median NRR at $15M ARR, and how many AEs should we hire next year?", True),
    ("What is median NRR at $15M ARR? Also how should we run quarterly planning?", True),
])
def test_mixed_benchmark_and_broader_ask_is_detected_by_rules(lib, q, mixed):
    assert bp.detect(lib, q)["mixed"] is mixed


def test_a_benchmark_inside_a_hiring_question_is_split_and_both_answered(lib, monkeypatch):
    c = _client(monkeypatch, split_text=json.dumps({"subqueries": [
        {"intent": "benchmark", "metric": "Net revenue retention", "band": "$15M ARR", "text": "median NRR at $15M ARR"},
        {"intent": "other", "text": "how many account executives to hire next year"}],
        "rest_of_question": "Hiring plan."}))
    ans = _ask(lib, "What is median NRR at $15M ARR, and how many AEs should we hire next year?")
    assert ans.prestep["split_ran"] and [s["intent"] for s in ans.prestep["subs"]] == ["benchmark", "other"]
    assert any(d["title"].startswith("Figures:") for d in _docs(c.answer_calls()[0]))
    assert "Answer every part" in "\n".join(_texts(c.answer_calls()[0]))


# --- trigger beyond the registry: metric names found by full-text search ---------

def _unregistered_lib(tmp_path):
    db = Library(str(tmp_path / "u.db"))
    db.seed_voice_prompts()
    # No registry at all, and metric names that are in no registry.
    fx.load_report(db, "ex-2099", [
        fx.frow(basis="Revenue", band="$20M-$30M", metric="Total employees", value="140", unit="count"),
        fx.frow(basis="Revenue", band="$20M-$30M", metric="Expense as a percent of revenue", value="88", unit="percent"),
        fx.frow(basis="Revenue", band="$10M-$20M", metric="Expense as a percent of revenue", value="92", unit="percent"),
    ])
    return db


def test_unregistered_metric_names_trigger_a_lookup(tmp_path, monkeypatch):
    db = _unregistered_lib(tmp_path)
    c = _client(monkeypatch, split_text=json.dumps({"subqueries": [
        {"intent": "benchmark", "metric": "Total employees", "band": "$20M-$30M", "text": "total employees"},
        {"intent": "benchmark", "metric": "Expense as a percent of revenue", "band": "$20M-$30M", "text": "expense pct"}],
        "rest_of_question": ""}))
    ans = _ask(db, "What are total employees and expense as a percent of revenue at $20-$30M revenue?")
    assert sorted(ans.prestep["detected"]) == ["Expense as a percent of revenue", "Total employees"]
    assert ans.prestep["signals"]["via_fts"] is True
    text = " ".join(_doc_text(d) for d in _docs(c.answer_calls()[0]) if d["title"].startswith("Figures:"))
    assert "value 140" in text and "value 88" in text and "match: exact" in text
    db.close()


def test_a_single_unregistered_metric_with_a_band_triggers_without_a_split(tmp_path, monkeypatch):
    db = _unregistered_lib(tmp_path)
    c = _client(monkeypatch)
    ans = _ask(db, "What is expense as a percent of revenue for a $25M revenue company?")
    assert c.split_calls() == [] and ans.prestep["subs"][0]["figure_rows"] >= 1
    db.close()


@pytest.mark.parametrize("q", [
    "How should we structure our sales compensation plan?",
    "What is our total plan for employees training next year?",          # both words, no benchmark context
    "Should we cut expense growth as a percent of our revenue target?",   # name words, no band or benchmark cue
    "What is the best way to run quarterly planning at $25M revenue?",    # a band, but no stored metric name
])
def test_ordinary_questions_do_not_trigger_the_full_text_path(tmp_path, monkeypatch, q):
    db = _unregistered_lib(tmp_path)
    c = _client(monkeypatch)
    ans = _ask(db, q)
    assert ans.prestep == {} and c.split_calls() == []
    db.close()


def test_the_registry_still_wins_and_fts_adds_only_what_it_does_not_cover(lib, monkeypatch):
    fx.load_report(lib, "eb-2099", [fx.frow(metric="Total employees", basis="Revenue", band="$20M-$30M", value="9")])
    sig = bp.detect(lib, "What is median net revenue retention at $25M ARR, and total employees?")
    assert sig["metrics"][0] == "Net revenue retention" and "Total employees" in sig["metrics"]
