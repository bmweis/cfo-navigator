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
    _mock_anthropic(monkeypatch, "A tool.\n\nSUMMARY: Short.\n\nCONFIDENT: true")
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confident is True
    assert draft.low_confidence is False


def test_generate_tool_description_parses_confident_false(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Thin page text."))
    _mock_anthropic(monkeypatch, "A tool.\n\nSUMMARY: Short.\n\nCONFIDENT: false")
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confident is False


def test_generate_tool_description_defaults_confident_false_when_missing(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Real page text."))
    _mock_anthropic(monkeypatch, "A tool.\n\nSUMMARY: Short.")   # no CONFIDENT sentinel line
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confident is False


def test_generate_tool_differentiation_parses_confident(monkeypatch):
    _mock_anthropic(monkeypatch, '{"competitive_differentiation": "Best for X.", "confident": true}')
    draft = enrich.generate_tool_differentiation("Runway", "https://runway.com", "A tool.", ["Datarails"], voice_core="Test voice guide.")
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
    assert tool["agent_taxonomy_ai_confident"] is None


def test_set_tool_agent_taxonomy_draft_persists_ai_confident(lib):
    """2026-08 follow-up — Agent taxonomy's own genuine self-reported
    confidence, stored separately from agent_taxonomy_needs_verification
    (which it used to only ever drive, never persist on its own)."""
    tid = lib.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tid, "Uses an AI agent for X.",
                                       needs_verification=0, ai_confident=1)
    assert lib.get_tool(tid)["agent_taxonomy_ai_confident"] == 1

    # A caller that omits ai_confident (None) leaves the column alone —
    # same COALESCE contract as every other *_ai_confident column.
    lib.set_tool_agent_taxonomy_draft(tid, "Uses an AI agent for X, revised.",
                                       needs_verification=1)
    assert lib.get_tool(tid)["agent_taxonomy_ai_confident"] == 1


def test_update_tool_agent_taxonomy_leaves_ai_confident_untouched(lib):
    """A hand-edit/save through update_tool_agent_taxonomy (the "editing is
    itself a confirmation" path) doesn't reference ai_confident at all —
    the model's prior self-report about the pre-edit text stays on the
    row, same as description_ai_confident surviving an unrelated resave."""
    tid = lib.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tid, "Original note.", needs_verification=1, ai_confident=0)
    lib.update_tool_agent_taxonomy(tid, "Brian's hand-edited note.")
    tool = lib.get_tool(tid)
    assert tool["agent_taxonomy_note"] == "Brian's hand-edited note."
    assert tool["agent_taxonomy_needs_verification"] == 0
    assert tool["agent_taxonomy_ai_confident"] == 0


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


def test_confidence_line_shows_not_yet_assessed_when_no_signal_ever_reported(env):
    """2026-08 follow-up (a live check on Abacum's Description, which
    predates this column by three days, found the field silently showing
    nothing — indistinguishable from broken): confident is None (no
    generation has ever reported a value — a pre-existing row, or a field
    never run through Generate) still renders a line, a third distinct
    state rather than being hidden."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: Not yet assessed" in r.text


# -- Agent taxonomy confidence line (2026-08 follow-up) ----------------------
# Same permanent-display treatment as Description/Differentiation, added to
# the field this whole effort started from (the Abacum finding). Deliberately
# does NOT touch agent_taxonomy_needs_verification's "Mark verified"
# badge/button on this same edit page. The public profile page's own
# treatment of an unverified note (originally: hide it from visitors
# entirely) was later superseded by Brian's radical-transparency review
# standard (Gate-Extraction Phase 0/PR A) — see
# test_agent_taxonomy_review_standard_unaffected_by_confidence_display_change
# below, which now asserts the current label-not-hide behavior.

def test_agent_taxonomy_confidence_line_shown_on_edit_page_regardless_of_verification(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.set_tool_agent_taxonomy_draft(tid, "Uses an AI agent for X.",
                                       needs_verification=1, ai_confident=0)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: No" in r.text

    # Mark verified — the badge/button disappear, the confidence line stays.
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.mark_tool_agent_taxonomy_verified(tid)
    lib_.close()
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: No" in r.text


def test_agent_taxonomy_confidence_line_shows_not_yet_assessed_when_no_signal_ever_reported(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.update_tool_agent_taxonomy(tid, "Brian wrote this by hand, never generated.")
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert "Claude confidence: Not yet assessed" in r.text


def test_agent_taxonomy_review_standard_unaffected_by_confidence_display_change(env):
    """The confidence line is admin-edit-page-only. The public profile page's
    review-state treatment of an unverified note (radical-transparency
    standard: content always renders, labeled "under review" for a visitor
    and "unverified, visible to visitors" for an admin) must keep working
    exactly as before this confidence-display change — unaffected either
    way, since these are two independent display facts."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    tid = lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.set_tool_agent_taxonomy_draft(
        tid, "A drafted claim awaiting human review.",
        needs_verification=1, ai_confident=0)
    slug = lib_.get_tool(tid)["slug"]
    lib_.close()

    client = _client(env)
    # Signed out — content renders, labeled "under review".
    r = client.get(f"/tools/software/{slug}")
    assert "A drafted claim awaiting human review." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text

    # Signed in — same content, labeled "unverified, visible to visitors".
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "A drafted claim awaiting human review." in r.text
    assert "unverified, visible to visitors" in r.text
