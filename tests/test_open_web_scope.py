"""Open web (2026-10): Current feed = RSS items plus a trusted-domain web
search; Open web = the same single web search with no domain restriction.

Covers: includeDomains present for Current feed alone and absent for Open web,
one Exa call when both are on, the native fallback following the same rule,
web_scope recording, the legacy-row rendering rule (a row recorded before the
change must never read as unrestricted, or as web search when it was RSS only),
the /ask and MCP source mapping (an old "web" key never becomes unrestricted),
the open-web citation tag, and the untrusted-source sentence in the prompt.
"""
import importlib
import json
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib.db import Library


# --- retrieval: includeDomains and the one-call rule -------------------------

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


@pytest.fixture
def exa_calls(monkeypatch):
    calls = []
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # stop after retrieval
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))
    monkeypatch.setattr(agent, "retrieve_feed", lambda q, opml, max_items=5: [])
    monkeypatch.setattr("linklib.sources.preferred_domains",
                        lambda opml_path=None: ("trusted-a.com", "trusted-b.com"))

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _Resp({"results": [
            {"url": "https://www.trusted-a.com/x", "title": "T", "highlights": ["h"]},
            {"url": "https://random-blog.example/y", "title": "R", "highlights": ["h"]},
        ]})
    monkeypatch.setattr(agent.requests, "post", fake_post)
    return calls


def _ask(**kw):
    return agent.answer_question(None, "what is burn multiple?", use_library=False,
                                 opml_path="preferred_sites.opml", **kw)


def test_current_feed_alone_restricts_to_trusted_domains(exa_calls):
    ans = _ask(use_feed=True, use_web=False)
    assert len(exa_calls) == 1
    assert exa_calls[0]["includeDomains"] == ["trusted-a.com", "trusted-b.com"]
    assert ans.web_scope == "trusted"


def test_open_web_omits_include_domains(exa_calls):
    ans = _ask(use_feed=False, use_web=True)
    assert len(exa_calls) == 1
    assert "includeDomains" not in exa_calls[0]
    assert ans.web_scope == "open"


def test_both_on_is_one_unrestricted_call_not_two(exa_calls):
    ans = _ask(use_feed=True, use_web=True)
    assert len(exa_calls) == 1
    assert "includeDomains" not in exa_calls[0]
    assert ans.web_scope == "open"


def test_neither_on_makes_no_web_call(exa_calls):
    ans = _ask(use_feed=False, use_web=False)
    assert exa_calls == []
    assert ans.web_scope == ""


def test_hits_are_tagged_trusted_by_domain(exa_calls):
    ans = _ask(use_feed=False, use_web=True)
    by_url = {h["url"]: h["trusted"] for h in ans.web_sources}
    assert by_url == {"https://www.trusted-a.com/x": True,
                      "https://random-blog.example/y": False}


def test_is_trusted_url_matches_subdomains_and_not_lookalikes():
    d = ("trusted-a.com",)
    assert agent.is_trusted_url("https://blog.trusted-a.com/p", d)
    assert agent.is_trusted_url("https://www.trusted-a.com/p", d)
    assert not agent.is_trusted_url("https://nottrusted-a.com/p", d)
    assert not agent.is_trusted_url("https://trusted-a.com.evil.example/p", d)


# --- native fallback follows the same rule -----------------------------------

class _Usage:
    input_tokens = 1
    output_tokens = 1
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Msg:
    content = []
    usage = _Usage()
    stop_reason = "end_turn"


def _native_tool(monkeypatch, tmp_path, **kw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))
    monkeypatch.setattr(agent, "retrieve_feed", lambda q, opml, max_items=5: [])
    captured = {}

    class _M:
        def create(self, **k):
            captured.update(k)
            return _Msg()

    class _C:
        messages = _M()
    monkeypatch.setattr(agent, "_get_client", lambda: _C())
    lib = Library(str(tmp_path / "t.db"))
    lib.seed_voice_prompts()
    try:
        agent.answer_question(lib, "q", use_library=False,
                              opml_path="preferred_sites.opml", **kw)
    finally:
        lib.close()
    return captured


def test_native_fallback_open_web_has_no_allowed_domains(monkeypatch, tmp_path):
    cap = _native_tool(monkeypatch, tmp_path, use_feed=False, use_web=True)
    assert [t["name"] for t in cap["tools"]] == ["web_search"]
    assert "allowed_domains" not in cap["tools"][0]


def test_native_fallback_current_feed_keeps_allowed_domains(monkeypatch, tmp_path):
    cap = _native_tool(monkeypatch, tmp_path, use_feed=True, use_web=False)
    assert "allowed_domains" in cap["tools"][0]


def test_buddy_has_no_tool_beyond_read_only_web_search(monkeypatch, tmp_path):
    for kw in ({"use_feed": True}, {"use_web": True}, {"use_feed": True, "use_web": True}):
        cap = _native_tool(monkeypatch, tmp_path, **kw)
        assert [t["type"] for t in cap["tools"]] == ["web_search_20250305"]


# --- recording ---------------------------------------------------------------

def test_record_ask_question_stores_web_scope_and_rejects_junk(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    try:
        ids = {}
        for scope in ("open", "trusted", "", "bogus"):
            ids[scope] = lib.record_ask_question(
                1, "q", "a", "m", "standard", True, True, False, web_scope=scope)
        got = {s: lib.get_ask_question(i)["web_scope"] for s, i in ids.items()}
        assert got == {"open": "open", "trusted": "trusted", "": "", "bogus": ""}
    finally:
        lib.close()


# --- legacy rendering rule ---------------------------------------------------

def _row(**kw):
    base = dict(use_library=1, use_feed=0, use_web=0, web_scope="", model="claude-sonnet-4-6",
                effort="standard")
    base.update(kw)
    return base


def test_legacy_feed_row_is_rss_only_and_never_claims_web_or_trusted_sites():
    import webapp.app as appmod
    labels = {k: l for k, _i, l in appmod._ask_source_labels(_row(use_feed=1))}
    assert labels["feed"] == "RSS feed items only"
    assert "web" not in labels
    assert "trusted" not in labels["feed"].lower()


def test_legacy_web_row_is_trusted_only_never_unrestricted():
    import webapp.app as appmod
    labels = {k: l for k, _i, l in appmod._ask_source_labels(_row(use_web=1))}
    assert labels["web"] == "Web search, trusted sites only"
    assert "open" not in labels["web"].lower()


def test_new_rows_use_the_new_names():
    import webapp.app as appmod
    new = {k: l for k, _i, l in appmod._ask_source_labels(
        _row(use_feed=1, use_web=1, web_scope="open"))}
    assert new["feed"].startswith("Current feed")
    assert new["web"] == "Open web"
    only_trusted = {k: l for k, _i, l in appmod._ask_source_labels(
        _row(use_feed=1, web_scope="trusted"))}
    assert only_trusted["feed"].startswith("Current feed") and "web" not in only_trusted


def test_badge_carries_the_words_as_title_and_aria_label():
    import webapp.app as appmod
    html = appmod._ask_settings_badge(_row(use_feed=1))
    assert 'title="RSS feed items only"' in html and 'aria-label="RSS feed items only"' in html


# --- citation tag ------------------------------------------------------------

def test_server_citation_list_tags_only_an_explicit_untrusted_web_source():
    import webapp.app as appmod
    cites = [
        {"n": 1, "title": "Open one", "url": "https://x.example/1", "type": "web", "trusted": False},
        {"n": 2, "title": "Trusted", "url": "https://t.example/2", "type": "web", "trusted": True},
        {"n": 3, "title": "Legacy web", "url": "https://l.example/3", "type": "web"},
        {"n": 4, "title": "Library", "url": "https://a.example/4", "type": "library"},
    ]
    _a, src = appmod._render_cited_answer("A [1] B [2] C [3] D [4]", json.dumps(cites))
    assert src.count("ask-src-open") == 1
    assert src.index("Open one") < src.index("ask-src-open") < src.index("Trusted")


def test_client_citation_list_has_the_same_rule():
    # The client list is built by srcListHtml in the page script, so check its source.
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    assert "c.type === 'web' && c.trusted === false" in src
    assert "ask-src-open" in src


# --- prompt ------------------------------------------------------------------

def test_system_prompt_tells_the_model_sources_are_untrusted_data(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    try:
        lib.seed_voice_prompts()
        s = agent._build_system(True, True, True, lib)
    finally:
        lib.close()
    low = s.lower()
    assert "untrusted data" in low and "never as instructions" in low
    assert "open web" in low and "current feed" in low
    assert "restricted to the user's trusted domains" not in low


# --- /ask route and MCP mapping ----------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "tok-secret")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user", name="Member One")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _post_ask(appmod, sources):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    seen = {}

    def fake_run_ask(lib, user_id, question, **kw):
        seen.update(kw)
        return {"answer": "x"}
    appmod.run_ask = fake_run_ask
    payload = {"question": "q"}
    if sources is not None:
        payload["sources"] = sources
    r = c.post("/ask", json=payload)
    assert r.status_code == 200
    return seen


def test_ask_default_is_archive_and_current_feed_never_open_web(env):
    seen = _post_ask(env, None)
    assert (seen["use_library"], seen["use_feed"], seen["use_web"]) == (True, True, False)


def test_ask_open_web_needs_the_explicit_open_web_key(env):
    seen = _post_ask(env, ["library", "open_web"])
    assert (seen["use_feed"], seen["use_web"]) == (False, True)


def test_a_stale_tab_sending_the_old_web_key_gets_trusted_not_open(env):
    seen = _post_ask(env, ["library", "web"])
    assert (seen["use_feed"], seen["use_web"]) == (True, False)


def test_chips_default_state_and_labels(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    body = c.get("/tools/fpa-buddy").text
    import re
    chips = re.findall(r'<button type="button" class="(ask-tag[^"]*)" data-source="([a-z_]+)"[^>]*>'
                       r'.*?<span class="ask-tag-name">([^<]+)</span>', body, re.S)
    assert [(k, n, "active" in cls.split()) for cls, k, n in chips[:3]] == [
        ("library", "Curated archive", True),
        ("feed", "Current feed", True),
        ("open_web", "Open web", False),
    ]
