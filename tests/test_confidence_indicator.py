"""AI confidence indicator (2026-08) — genuine self-reported "confident"
fields added to generate_tool_description/generate_tool_differentiation,
matching Agent taxonomy's existing pattern (see CLAUDE.md). Covers:
linklib.enrich's parsed `confident` field (unit, mocked Claude call), the
edit-submit route's DB write (only when a field is both AI-drafted this
save AND carries a confidence pair), and the "Confidence: Yes/No" display
line, which is a fact distinct from the "Needs verification" badge and
only shown while the field is still unconfirmed.
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=80,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


# -- linklib.enrich: confident field parsing ---------------------------------

def test_generate_tool_description_parses_confident_true(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Real page text."))
    _mock_anthropic(monkeypatch, '{"description": "A tool.", "summary": "Short.", "confident": true}')
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert draft.confident is True
    assert draft.low_confidence is False


def test_generate_tool_description_parses_confident_false(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Thin page text."))
    _mock_anthropic(monkeypatch, '{"description": "A tool.", "summary": "Short.", "confident": false}')
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert draft.confident is False


def test_generate_tool_description_defaults_confident_false_when_missing(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Real page text."))
    _mock_anthropic(monkeypatch, '{"description": "A tool.", "summary": "Short."}')   # no "confident" key
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert draft.confident is False


def test_generate_tool_differentiation_parses_confident(monkeypatch):
    _mock_anthropic(monkeypatch, '{"competitive_differentiation": "Best for X.", "confident": true}')
    draft = enrich.generate_tool_differentiation("Runway", "https://runway.com", "A tool.", ["Datarails"])
    assert draft is not None
    assert draft.confident is True


# -- Library: DB round trip --------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    db = str(tmp_path / "library.db")
    library = Library(db)
    yield library
    library.close()


def test_update_tool_persists_description_ai_confident(lib):
    tid = lib.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tid, "Runway", "A tool.", "https://runway.com", ["FP&A"],
                     description_ai_confident=1)
    assert lib.get_tool(tid)["description_ai_confident"] == 1

    # None (not passed) leaves the column alone — same COALESCE contract as
    # description_needs_verification.
    lib.update_tool(tid, "Runway", "A tool.", "https://runway.com", ["FP&A"])
    assert lib.get_tool(tid)["description_ai_confident"] == 1


def test_update_tool_differentiation_persists_ai_confident(lib):
    tid = lib.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tid, "Best for X.", needs_verification=1, ai_confident=0)
    assert lib.get_tool(tid)["competitive_differentiation_ai_confident"] == 0


def test_new_tool_has_no_confidence_signal_by_default(lib):
    tid = lib.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    tool = lib.get_tool(tid)
    assert tool["description_ai_confident"] is None
    assert tool["competitive_differentiation_ai_confident"] is None


# -- Admin edit-submit route: confidence only saved for a fresh draft -------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_edit_submit_saves_confidence_only_for_ai_drafted_fields(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post("/tools/software/runway/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "Drafted description.",
        "summary": "Drafted short.", "categories": ["FP&A"],
        "ai_drafted_fields": "description,summary",
        "ai_drafted_confidence": "description:1,summary:1",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    lib_ = Library(os.environ["LINKLIB_DB"])
    tool = lib_.get_tool(tid)
    lib_.close()
    assert tool["description_ai_confident"] == 1
    assert tool["description_needs_verification"] == 1


def test_edit_submit_ignores_confidence_for_hand_edited_field(env):
    """A field NOT in ai_drafted_fields this save (hand-edited, or untouched)
    never gets a confidence value written, even if a stray pair for it
    somehow ended up in the hidden input."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post("/tools/software/runway/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "Hand-written description.",
        "summary": "Hand-written short.", "categories": ["FP&A"],
        "ai_drafted_fields": "",   # nothing drafted this save
        "ai_drafted_confidence": "description:1",   # stray/stale pair
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    lib_ = Library(os.environ["LINKLIB_DB"])
    tool = lib_.get_tool(tid)
    lib_.close()
    assert tool["description_ai_confident"] is None
    assert tool["description_needs_verification"] == 0


# -- Display: "Confidence: Yes/No" line --------------------------------------
# 2026-08 policy revision: the line is permanent — verification status and
# confidence are independent facts, both always visible, regardless of
# needs_verification state. No more gating on review status.

def test_confidence_line_shown_while_unverified(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.update_tool(tid, "Runway", "A tool.", "https://runway.com", ["FP&A"],
                     description_needs_verification=1, description_ai_confident=0)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: No" in r.text


def test_confidence_line_still_shown_once_verified(env):
    """Verification and confidence are independent facts — the line stays
    visible even after a human has verified the field, distinct from the
    badge/button, which do disappear once verified."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.update_tool(tid, "Runway", "A tool.", "https://runway.com", ["FP&A"],
                     description_needs_verification=0, description_ai_confident=0)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: No" in r.text


def test_confidence_line_hidden_when_no_signal_ever_reported(env):
    """The one condition that still hides the line: no generation has ever
    reported a confidence value for this field (a pre-existing row, or a
    field never run through Generate) — confident is None, not False."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence" not in r.text
