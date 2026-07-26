"""Tests for the Exa web-retrieval tier (linklib/agent.py's retrieve_exa),
Phase 2 of the Exa migration: Exa's /search endpoint replaced the old
model-invoked web_search_20250305 tool, called Python-side the same way
retrieve() and retrieve_feed() are.

No real network calls — requests.post is monkeypatched throughout.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent, pricing
from linklib.db import Library


class _FakeResponse:
    """Minimal stand-in for requests.Response."""
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise Exception("HTTP error")

    def json(self):
        return self._payload


# --- retrieve_exa: graceful degradation -------------------------------------

def test_retrieve_exa_no_api_key_degrades_gracefully(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    hits, num_results, cost = agent.retrieve_exa("SaaS pricing benchmarks", None)
    assert hits == []
    assert num_results == 0
    assert cost == 0.0


def test_retrieve_exa_blank_question_is_noop(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def boom(*a, **k):
        raise AssertionError("must not call Exa for a blank question")
    monkeypatch.setattr(agent.requests, "post", boom)

    hits, num_results, cost = agent.retrieve_exa("   ", None)
    assert (hits, num_results, cost) == ([], 0, 0.0)


def test_retrieve_exa_network_failure_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def boom(*a, **k):
        raise Exception("connection reset")
    monkeypatch.setattr(agent.requests, "post", boom)

    hits, num_results, cost = agent.retrieve_exa("SaaS pricing benchmarks", None)
    assert (hits, num_results, cost) == ([], 0, 0.0)


def test_retrieve_exa_non_200_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(agent.requests, "post",
                        lambda *a, **k: _FakeResponse({}, status_ok=False))
    hits, num_results, cost = agent.retrieve_exa("SaaS pricing benchmarks", None)
    assert (hits, num_results, cost) == ([], 0, 0.0)


# --- retrieve_exa: successful call -------------------------------------------

def test_retrieve_exa_success_shapes_hits_and_cost(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    payload = {
        "results": [
            {"title": "Article A", "url": "https://a.com/1",
             "highlights": ["Median ACV is $25k.", "Growth benchmarks vary."]},
            {"title": "Article B", "url": "https://b.com/2", "text": "Full text body."},
        ]
    }
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: _FakeResponse(payload))

    hits, num_results, cost = agent.retrieve_exa("SaaS pricing benchmarks", None, max_results=4)

    assert num_results == 2
    assert hits == [
        {"title": "Article A", "url": "https://a.com/1",
         "summary": "Median ACV is $25k. Growth benchmarks vary."},
        {"title": "Article B", "url": "https://b.com/2", "summary": "Full text body."},
    ]
    assert cost == pytest.approx(pricing.compute_exa_cost("search", num_results=2))


def test_retrieve_exa_skips_results_without_a_url(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    payload = {"results": [{"title": "No URL"}, {"title": "Has URL", "url": "https://c.com"}]}
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: _FakeResponse(payload))

    hits, num_results, cost = agent.retrieve_exa("question", None)
    assert len(hits) == 1
    assert hits[0]["url"] == "https://c.com"
    # Exa's own result count (billed on), not the post-filter hit count.
    assert num_results == 2


def test_retrieve_exa_passes_include_domains_from_opml(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    import linklib.sources as sources_mod
    monkeypatch.setattr(sources_mod, "preferred_domains",
                        lambda opml_path=None: ("trusted-a.com", "trusted-b.com"))

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse({"results": []})
    monkeypatch.setattr(agent.requests, "post", fake_post)

    agent.retrieve_exa("question", "preferred_sites.opml", max_results=6)
    assert captured["url"] == agent.EXA_SEARCH_URL
    assert captured["headers"]["x-api-key"] == "fake-key"
    assert captured["json"]["includeDomains"] == ["trusted-a.com", "trusted-b.com"]
    assert captured["json"]["numResults"] == 6


# --- answer_question: Exa cost folds into cost_usd on every return path -----

def _fake_lib_retrieve(monkeypatch):
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))


def test_answer_question_folds_exa_cost_when_anthropic_key_missing(monkeypatch):
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "fake-key")  # Phase 7: Exa is the provider only when this is set
    exa_hits = [{"title": "Web Hit", "url": "https://w.com", "summary": "s"}]
    monkeypatch.setattr(agent, "retrieve_exa", lambda q, opml, max_results=4: (exa_hits, 3, 0.007))

    ans = agent.answer_question(None, "a question", use_library=False,
                                use_web=True, opml_path="preferred_sites.opml")

    assert ans.web_sources == exa_hits
    assert ans.exa_result_count == 3
    assert ans.exa_cost_usd == pytest.approx(0.007)
    assert ans.cost_usd == pytest.approx(0.007)


def test_answer_question_no_exa_call_without_opml_path(monkeypatch):
    """use_web without opml_path is the existing use_feed-without-opml_path
    contract — retrieve_exa must not be called at all."""
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "fake-key")  # Phase 7: force provider="exa" so this test's premise holds

    def boom(*a, **k):
        raise AssertionError("retrieve_exa must not run without an opml_path")
    monkeypatch.setattr(agent, "retrieve_exa", boom)

    ans = agent.answer_question(None, "a question", use_library=False,
                                use_web=True, opml_path=None)
    assert ans.exa_cost_usd == 0.0
    assert ans.web_sources == []


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50,
                cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _FakeAnswerResponse:
    def __init__(self):
        self.content = []
        self.usage = _FakeUsage()


def test_answer_question_folds_exa_cost_on_success(monkeypatch, tmp_path):
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("EXA_API_KEY", "fake-key")  # Phase 7: Exa is the provider only when this is set
    exa_hits = [{"title": "Web Hit", "url": "https://w.com", "summary": "s"}]
    monkeypatch.setattr(agent, "retrieve_exa", lambda q, opml, max_results=4: (exa_hits, 3, 0.007))

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeAnswerResponse()

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    lib = Library(str(tmp_path / "t.db"))
    try:
        ans = agent.answer_question(lib, "a question", use_library=False,
                                    use_web=True, opml_path="preferred_sites.opml")
    finally:
        lib.close()

    assert ans.web_sources == exa_hits
    assert ans.exa_result_count == 3
    assert ans.exa_cost_usd == pytest.approx(0.007)
    assert ans.cost_usd >= 0.007


def test_answer_question_folds_exa_cost_on_answer_call_exception(monkeypatch, tmp_path):
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("EXA_API_KEY", "fake-key")  # Phase 7: Exa is the provider only when this is set
    exa_hits = [{"title": "Web Hit", "url": "https://w.com", "summary": "s"}]
    monkeypatch.setattr(agent, "retrieve_exa", lambda q, opml, max_results=4: (exa_hits, 3, 0.007))

    class _FakeMessages:
        def create(self, **kwargs):
            raise Exception("upstream 500")

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    lib = Library(str(tmp_path / "t.db"))
    try:
        ans = agent.answer_question(lib, "a question", use_library=False,
                                    use_web=True, opml_path="preferred_sites.opml")
    finally:
        lib.close()

    # The Exa call already spent real money even though the answer call
    # failed — its cost must still be recorded on the Answer.
    assert ans.text.startswith("(Answer call failed")
    assert ans.web_sources == exa_hits
    assert ans.exa_result_count == 3
    assert ans.exa_cost_usd == pytest.approx(0.007)
    assert ans.cost_usd == pytest.approx(0.007)
