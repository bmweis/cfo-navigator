"""Phase 7 of the Exa migration: an Exa kill switch with native-tool
fallback. Exa is the preferred web-search mechanism when the `exa_enabled`
setting is on AND EXA_API_KEY is set; otherwise Claude's native
web_search_20250305 tool (restored from before Phase 2 removed it) handles
the web tier instead. Exactly one mechanism runs per turn, and citations
from either carry a `provider` field ("exa" | "native") so the "Powered by
Exa" caption (Phase 3) can gate on the real mechanism.

No real network calls — requests.post and the Anthropic client are
monkeypatched throughout.
"""
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib.db import Library


# --- Library.get_exa_enabled / set_exa_enabled -------------------------------

def test_exa_enabled_defaults_to_true(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    try:
        assert lib.get_exa_enabled() is True
    finally:
        lib.close()


def test_exa_enabled_persists_toggle(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    try:
        lib.set_exa_enabled(False)
        assert lib.get_exa_enabled() is False
        lib.set_exa_enabled(True)
        assert lib.get_exa_enabled() is True
    finally:
        lib.close()


# --- _web_provider: the unified fallback condition --------------------------

def test_web_provider_is_exa_when_enabled_and_keyed(monkeypatch, tmp_path):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    lib = Library(str(tmp_path / "t.db"))
    try:
        assert agent._web_provider(lib) == "exa"
    finally:
        lib.close()


def test_web_provider_is_native_when_toggled_off(monkeypatch, tmp_path):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    lib = Library(str(tmp_path / "t.db"))
    try:
        lib.set_exa_enabled(False)
        assert agent._web_provider(lib) == "native"
    finally:
        lib.close()


def test_web_provider_is_native_when_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    lib = Library(str(tmp_path / "t.db"))
    try:
        assert lib.get_exa_enabled() is True   # toggle itself is on
        assert agent._web_provider(lib) == "native"   # key missing still falls back
    finally:
        lib.close()


def test_web_provider_tolerates_lib_none(monkeypatch):
    """Some callers/tests never open a connection — None must not crash,
    and is treated as toggle-on (same as a fresh database's default)."""
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    assert agent._web_provider(None) == "exa"
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    assert agent._web_provider(None) == "native"


# --- answer_question: exactly one mechanism runs per turn -------------------

def _fake_lib_retrieve(monkeypatch):
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class _FakeAnswerResponse:
    def __init__(self, content=None):
        self.content = content or []
        self.usage = _FakeUsage()


def test_native_tool_is_armed_when_exa_disabled(monkeypatch, tmp_path):
    """When Exa is off, kwargs['tools'] must carry the restored
    web_search_20250305 spec — and retrieve_exa must never be called."""
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("retrieve_exa must not run when Exa is disabled")
    monkeypatch.setattr(agent, "retrieve_exa", boom)

    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeAnswerResponse()

    class _FakeClient:
        messages = _FakeMessages()
    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    lib = Library(str(tmp_path / "t.db"))
    try:
        agent.answer_question(lib, "a question", use_library=False,
                              use_web=True, opml_path="preferred_sites.opml")
    finally:
        lib.close()

    assert "tools" in captured
    tool = captured["tools"][0]
    assert tool["type"] == "web_search_20250305"
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == agent.EFFORT_SETTINGS["standard"]["max_web"]
    assert "allowed_domains" in tool


def test_native_tool_not_armed_when_exa_enabled(monkeypatch, tmp_path):
    """The either/or invariant from the other side: when Exa handles the
    turn, no 'tools' kwarg should be sent at all."""
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(agent, "retrieve_exa", lambda q, opml, max_results=4: ([], 0, 0.0))

    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeAnswerResponse()

    class _FakeClient:
        messages = _FakeMessages()
    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    lib = Library(str(tmp_path / "t.db"))
    try:
        agent.answer_question(lib, "a question", use_library=False,
                              use_web=True, opml_path="preferred_sites.opml")
    finally:
        lib.close()

    assert "tools" not in captured


def test_native_tool_armed_even_without_opml_path(monkeypatch, tmp_path):
    """Restored exactly as the pre-Phase-2 tool behaved: armed on use_web
    alone, unlike Exa which requires opml_path."""
    _fake_lib_retrieve(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeAnswerResponse()

    class _FakeClient:
        messages = _FakeMessages()
    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    lib = Library(str(tmp_path / "t.db"))
    try:
        agent.answer_question(lib, "a question", use_library=False,
                              use_web=True, opml_path=None)
    finally:
        lib.close()

    assert "tools" in captured


# --- citation provider tagging -----------------------------------------------

class _Block:
    def __init__(self, text, citations=None, type="text"):
        self.type = type
        self.text = text
        self.citations = citations


class _DocCit:
    def __init__(self, document_index):
        self.type = "char_location"
        self.document_index = document_index
        self.cited_text = "…"


class _WebCit:
    def __init__(self, url, title=""):
        self.type = "web_search_result_location"
        self.url = url
        self.title = title
        self.cited_text = "…"


def test_exa_document_citation_gets_provider_exa():
    sent = [{"title": "Web Hit", "url": "https://w.com", "type": "web", "provider": "exa"}]
    blocks = [_Block("A fact.", citations=[_DocCit(0)])]
    _, cites = agent._assemble_cited_answer(blocks, sent)
    assert cites == [{"n": 1, "title": "Web Hit", "url": "https://w.com",
                      "type": "web", "provider": "exa"}]


def test_native_tool_url_citation_gets_provider_native():
    blocks = [_Block("A fact.", citations=[_WebCit("https://n.com", "Native Hit")])]
    _, cites = agent._assemble_cited_answer(blocks, [])
    assert cites == [{"n": 1, "title": "Native Hit", "url": "https://n.com",
                      "type": "web", "provider": "native"}]


def test_library_citation_has_no_provider_field():
    sent = [{"title": "Lib", "url": "https://l.com", "type": "library"}]
    blocks = [_Block("A fact.", citations=[_DocCit(0)])]
    _, cites = agent._assemble_cited_answer(blocks, sent)
    assert "provider" not in cites[0]


# --- test_exa_connection: manual/on-demand admin action ---------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.exceptions.HTTPError(response=self)
            raise err

    def json(self):
        return self._payload


def test_test_exa_connection_no_key():
    import os
    if "EXA_API_KEY" in os.environ:
        del os.environ["EXA_API_KEY"]
    result = agent.test_exa_connection()
    assert result == {"ok": False, "error": "EXA_API_KEY is not set.", "cost_usd": 0.0}


def test_test_exa_connection_success(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(agent.requests, "post",
                        lambda *a, **k: _FakeResponse({"results": [{"url": "https://x.com"}]}))
    result = agent.test_exa_connection()
    assert result["ok"] is True
    assert result["error"] == ""
    assert result["cost_usd"] > 0


def test_test_exa_connection_auth_failure(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "bad-key")
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: _FakeResponse({}, status_code=401))
    result = agent.test_exa_connection()
    assert result["ok"] is False
    assert "Authentication failed" in result["error"]
    assert result["cost_usd"] == 0.0


def test_test_exa_connection_rate_limited(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: _FakeResponse({}, status_code=429))
    result = agent.test_exa_connection()
    assert result["ok"] is False
    assert "Rate limited" in result["error"]


def test_test_exa_connection_network_failure(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def boom(*a, **k):
        raise Exception("connection reset")
    monkeypatch.setattr(agent.requests, "post", boom)
    result = agent.test_exa_connection()
    assert result["ok"] is False
    assert result["cost_usd"] == 0.0
