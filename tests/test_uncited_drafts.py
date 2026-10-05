"""Issue #642: an AI Generate draft with no valid citations must not replace a
cited one. Covers the marker validator, the one-retry-then-refuse rule in the
three grounded generators, cost accounting across a retry, the Library guard
that stops a Generate path replacing a non-empty citation set with an empty
one, `_run_tool_research`, the AJAX Generate routes, and the regen script."""
import importlib
import os
import pathlib
import re
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import citations as cit
from linklib import enrich
from linklib.db import Library

PAGE = ("Runway is a financial planning platform for finance teams at growth-stage companies. "
        "It consolidates budgeting, forecasting, and headcount planning into one collaborative "
        "workspace built for FP&A analysts and controllers who need to model scenarios quickly. "
        "Teams connect their general ledger and payroll systems, then build driver-based models "
        "that update automatically as actuals come in from month to month. The platform is used "
        "by finance leaders who need to answer board questions about runway, burn, and hiring "
        "plans without waiting on a spreadsheet rebuild every single time a number changes.")
CITES = [{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]


# -- helpers -----------------------------------------------------------------

def _fetch(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=PAGE, raw_html=PAGE, blocked=False, fetch_error=""))
    monkeypatch.setattr(enrich, "_discover_nav_pages", lambda base_url, max_pages=10: [])


def _anthropic(monkeypatch, responses, in_tok=100, out_tok=50):
    """responses: one entry per messages.create call, each a list of
    (text, cited_doc_indexes) blocks. Returns the call counter."""
    calls = {"n": 0}

    class _C:
        def __init__(self, i):
            self.document_index = i

    class _B:
        def __init__(self, text, idx):
            self.type = "text"
            self.text = text
            self.citations = [_C(i) for i in idx]

    def _create(**kw):
        blocks = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        usage = types.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[_B(t, i) for t, i in blocks], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=_create)))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return calls


DESC_CITED = [("Runway is a planning platform.", [0]), ("\n\nSUMMARY: Short.\n\nCONFIDENT: true", [])]
DESC_UNCITED = [("Runway is a planning platform.\n\nSUMMARY: Short.\n\nCONFIDENT: true", [])]
DESC_ORPHAN = [("Runway is a planning platform [2].", [0]), ("\n\nSUMMARY: Short.\n\nCONFIDENT: true", [])]
TAX_CITED = [("Runway ships an agent called Aura.", [0]), ("\n\nCONFIDENT: true", [])]
TAX_UNCITED = [("Runway ships an agent called Aura.\n\nCONFIDENT: true", [])]
PROFILE_BODY = "\n".join(f"{f.upper()}: text for {f}" for f in enrich.COMMUNITY_PROFILE_FIELDS) + \
    "\nCONFIDENCE:\n" + "\n".join(f"{f.upper()}: true" for f in enrich.COMMUNITY_CONFIDENCE_FIELDS)
PROFILE_CITED = [(PROFILE_BODY, [0])]
PROFILE_UNCITED = [(PROFILE_BODY, [])]


# -- marker validator ----------------------------------------------------------

def test_citation_problem_cases():
    assert cit.citation_problem(["Fine [1]."], CITES) is None
    assert cit.citation_problem(["No markers."], []) == "no_citations"
    assert cit.citation_problem(["Marker [1] with no set."], []) == "no_citations"
    assert cit.citation_problem(["Orphan [2]."], CITES) == "orphan_markers"
    assert cit.citation_problem(["Fine [1]", "and [1] again"], CITES) is None
    assert cit.citation_problem(["In [2024] we grew."], CITES) is None   # a year is not a marker


# -- generators: retry once, then refuse ---------------------------------------------

def test_taxonomy_retry_succeeds_and_both_calls_are_costed(monkeypatch):
    _fetch(monkeypatch)
    calls = _anthropic(monkeypatch, [TAX_UNCITED, TAX_CITED])
    r = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="v")
    assert calls["n"] == 2
    assert r.citations and "[1]" in r.agent_taxonomy_note
    assert r.input_tokens == 200 and r.output_tokens == 100   # both calls, not just the last
    one_call = r.cost_usd / 2
    assert one_call > 0 and r.attempts == 2


def test_taxonomy_refuses_after_second_uncited_draft_and_carries_cost(monkeypatch):
    _fetch(monkeypatch)
    calls = _anthropic(monkeypatch, [TAX_UNCITED, TAX_UNCITED])
    with pytest.raises(enrich.UncitedDraft) as ei:
        enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="v")
    assert calls["n"] == 2
    e = ei.value
    assert e.field == "agent_taxonomy" and e.reason == "no_citations"
    assert e.input_tokens == 200 and e.output_tokens == 100 and e.cost_usd > 0


def test_taxonomy_first_cited_draft_makes_one_call(monkeypatch):
    _fetch(monkeypatch)
    calls = _anthropic(monkeypatch, [TAX_CITED])
    r = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="v")
    assert calls["n"] == 1 and r.attempts == 1


def test_description_orphan_marker_triggers_retry_then_refusal(monkeypatch):
    _fetch(monkeypatch)
    calls = _anthropic(monkeypatch, [DESC_ORPHAN, DESC_ORPHAN])
    with pytest.raises(enrich.UncitedDraft) as ei:
        enrich.generate_tool_description("Runway", "https://runway.com", voice_core="v")
    assert calls["n"] == 2 and ei.value.reason == "orphan_markers" and ei.value.field == "description"


def test_description_uncited_then_cited_is_saved(monkeypatch):
    _fetch(monkeypatch)
    calls = _anthropic(monkeypatch, [DESC_UNCITED, DESC_CITED])
    d = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="v")
    assert calls["n"] == 2 and d.citations and d.attempts == 2
    assert d.input_tokens == 200


def test_community_profile_uncited_refused_and_cited_retry_ok(monkeypatch):
    _fetch(monkeypatch)
    _anthropic(monkeypatch, [PROFILE_UNCITED, PROFILE_UNCITED])
    with pytest.raises(enrich.UncitedDraft) as ei:
        enrich.generate_community_profile("CFO Club", "https://cfo.example", voice_core="v")
    assert ei.value.field == "community_profile"
    calls = _anthropic(monkeypatch, [PROFILE_UNCITED, PROFILE_CITED])
    d = enrich.generate_community_profile("CFO Club", "https://cfo.example", voice_core="v")
    assert calls["n"] == 2 and d.citations and d.attempts == 2


# -- Library guard ---------------------------------------------------------------------

@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    lb = Library(db)
    yield lb
    lb.close()
    if os.path.exists(db):
        os.remove(db)


def test_generated_citations_never_replace_a_nonempty_set_with_an_empty_one(lib):
    lib.set_entity_citations("tool", 1, "agent_taxonomy", CITES, model="m")
    assert lib.set_generated_entity_citations("tool", 1, "agent_taxonomy", [], model="m2") is False
    assert lib.get_entity_citations("tool", 1, "agent_taxonomy") == CITES
    assert lib.set_generated_entity_citations("tool", 1, "agent_taxonomy", CITES[:1], model="m3") is True


def test_missing_row_and_empty_json_are_the_same_thing(lib):
    assert lib.get_entity_citations("tool", 9, "description") == []
    lib.set_entity_citations("tool", 9, "description", [], model="")
    assert lib.get_entity_citations("tool", 9, "description") == []
    assert lib.set_generated_entity_citations("tool", 9, "description", [], model="m") is False


def test_no_generate_path_calls_set_entity_citations_directly():
    """Structural guard: webapp and scripts persist citations only through
    set_generated_entity_citations (which refuses an empty set) or
    clear_entity_citations (a deliberate clear)."""
    root = pathlib.Path(__file__).resolve().parents[1]
    bad = []
    for sub in ("webapp", "scripts"):
        for p in (root / sub).rglob("*.py"):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                code = line.split("#")[0]
                if re.search(r"\.set_entity_citations\(", code) and "diagnose" not in p.name \
                        and "audit_thin" not in p.name:
                    bad.append(f"{p.relative_to(root)}:{i}")
    assert bad == [], bad


# -- _run_tool_research ----------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    s = Library(db)
    s.seed_voice_prompts()
    s.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _tool_with_cited_note():
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tid, "Old cited note [1].", needs_verification=0)
    lib.set_entity_citations("tool", tid, "agent_taxonomy", CITES, model="old")
    lib.close()
    return tid


def _read(tid):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        return lib.get_tool(tid)["agent_taxonomy_note"], lib.get_entity_citations("tool", tid, "agent_taxonomy"), \
            lib.conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd),0) FROM enrichment_cost").fetchone()
    finally:
        lib.close()


def test_run_tool_research_zero_citations_is_refused_and_keeps_old(env, monkeypatch):
    tid = _tool_with_cited_note()
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy", lambda *a, **k: enrich.AgentTaxonomyResult(
        agent_taxonomy_note="Brand new uncited note.", agent_taxonomy_needs_verification=False,
        model="m", input_tokens=10, output_tokens=5, cost_usd=0.01, citations=[]))
    ok, reason, url = env._run_tool_research(tid)
    assert ok is False and reason.startswith("uncited:")
    note, cites, (n, cost) = _read(tid)
    assert note == "Old cited note [1]." and cites == CITES
    assert n == 1 and cost == pytest.approx(0.01)   # the spend is still recorded


def test_run_tool_research_uncited_exception_keeps_old_and_records_both_calls(env, monkeypatch):
    tid = _tool_with_cited_note()

    def _boom(*a, **k):
        raise enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://runway.com",
                                  model="m", input_tokens=200, output_tokens=100, cost_usd=0.04)
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy", _boom)
    ok, reason, url = env._run_tool_research(tid)
    assert ok is False and reason == "uncited:no_citations"
    note, cites, (n, cost) = _read(tid)
    assert note == "Old cited note [1]." and cites == CITES
    assert cost == pytest.approx(0.04)


def test_run_tool_research_cited_draft_is_saved(env, monkeypatch):
    tid = _tool_with_cited_note()
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy", lambda *a, **k: enrich.AgentTaxonomyResult(
        agent_taxonomy_note="New note [1].", agent_taxonomy_needs_verification=False, model="m",
        citations=[{"n": 1, "title": "T", "url": "https://x.example", "type": "tool_page"}]))
    assert env._run_tool_research(tid)[0] is True
    note, cites, _ = _read(tid)
    assert note == "New note [1]." and cites[0]["url"] == "https://x.example"


def test_refresh_route_shows_specific_uncited_banner(env, monkeypatch):
    from fastapi.testclient import TestClient
    tid = _tool_with_cited_note()

    def _boom(*a, **k):
        raise enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://runway.com", cost_usd=0.01)
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy", _boom)
    c = TestClient(env.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.post(f"/admin/tools/software/{tid}/research/refresh", follow_redirects=True)
    assert r.status_code == 200
    html = r.text
    assert "no citations" in html.lower() and "kept" in html.lower()


# -- AJAX Generate routes ----------------------------------------------------------------

def _login(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_generate_description_route_refuses_uncited_and_records_cost(env, monkeypatch):
    def _boom(*a, **k):
        raise enrich.UncitedDraft("description", "orphan_markers", "https://runway.com",
                                  model="claude-opus-5", input_tokens=200, output_tokens=100, cost_usd=0.05)
    monkeypatch.setattr(enrich, "generate_tool_description", _boom)
    r = _login(env).post("/admin/tools/software/generate-description",
                         json={"name": "Runway", "url": "https://runway.com"})
    assert r.status_code == 503 and r.json()["ok"] is False
    err = r.json()["error"]
    assert "Description" in err and "nothing was changed" in err.lower()
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.conn.execute("SELECT SUM(cost_usd) FROM enrichment_cost").fetchone()[0] == pytest.approx(0.05)
    lib.close()


def test_generate_community_profile_route_refuses_uncited(env, monkeypatch):
    def _boom(*a, **k):
        raise enrich.UncitedDraft("community_profile", "no_citations", "https://c.example",
                                  model="claude-opus-5", input_tokens=10, output_tokens=5, cost_usd=0.02)
    monkeypatch.setattr(enrich, "generate_community_profile", _boom)
    r = _login(env).post("/admin/tools/communities/generate-profile",
                         json={"name": "CFO Club", "url": "https://c.example"})
    assert r.status_code == 503 and "no citations" in r.json()["error"].lower()


# -- regen script ------------------------------------------------------------------------

def test_regen_description_with_no_citations_writes_nothing(monkeypatch):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import scripts.regen_ai_drafted_fields as regen
    db = tempfile.mktemp(suffix=".db")
    lb = Library(db)
    tid = lb.add_tool("Runway", "Old description.", "https://runway.com", [], approved=1, summary="Old summary.")
    lb.set_entity_citations("tool", tid, "description", CITES, model="m")
    tool = lb.get_tool(tid)
    draft = enrich.ToolDescriptionDraft(description="Fresh uncited text.", summary="S", model="m",
                                        input_tokens=1, output_tokens=1, cost_usd=0.01, citations=[])
    monkeypatch.setattr(regen, "generate_tool_description", lambda *a, **k: draft)
    log = tempfile.mktemp(suffix=".jsonl")
    regen._regen_tool_description(lb, tool, "m", "voice", log, True)
    t = lb.get_tool(tid)
    assert t["description"] == "Old description."
    assert lb.get_entity_citations("tool", tid, "description") == CITES
    assert "uncited" in open(log).read().lower() or "no_citations" in open(log).read()
    lb.close()
