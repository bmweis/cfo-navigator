"""The on-add research draft is always saved under review (Refs #698).

A tool added from /admin/tools/software/new is public at once, and the
background job it queues drafts "What its agents do". That draft used to be
saved with needs_verification = not confident, so a confident run went live
as verified with no human look. It is now always under review; Claude's
confidence is still stored, as the separate Yes/No line. The note under the
"Add to directory" buttons says what the job drafts, from one list, and a
test fails if the job writes a field that list does not name."""
import os
import pathlib
import re
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich, gates
from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.close()
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _confident_result():
    return enrich.AgentTaxonomyResult(
        agent_taxonomy_note="Aura drafts variance commentary [1].",
        agent_taxonomy_needs_verification=False, confident=True, low_confidence=False,
        model="claude-opus-5-5", input_tokens=500, output_tokens=300, cost_usd=0.02,
        citations=[{"n": 1, "title": "Agents", "url": "https://x.example/agents", "type": "tool_page"}],
    )


def _mock(monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy", lambda *a, **k: _confident_result())


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    if login:
        assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                      follow_redirects=False).status_code in (302, 303)
    return c


def _add(client):
    r = client.post("/admin/tools/software/new", data={
        "name": "Aura", "url": "https://aura.example", "description": "FP&A platform",
        "summary": "FP&A platform for scenario modeling."}, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    lib.close()
    return tool


def test_confident_on_add_draft_is_saved_under_review(env, monkeypatch):
    _mock(monkeypatch)
    tool = _add(_client(env))
    assert tool["agent_taxonomy_note"]
    assert tool["agent_taxonomy_needs_verification"] == 1
    # Confidence is kept as its own fact.
    assert tool["agent_taxonomy_ai_confident"] == 1


def test_under_review_shows_on_edit_page_and_to_visitors(env, monkeypatch):
    _mock(monkeypatch)
    admin = _client(env)
    tool = _add(admin)
    edit = admin.get(f"/tools/software/{tool['slug']}/edit").text
    assert gates.BADGE_TEXT_ADMIN in edit
    visitor = _client(env, login=False)
    page = visitor.get(f"/tools/software/{tool['slug']}").text
    assert "Aura drafts variance commentary" in page
    assert gates.BADGE_TEXT_VISITOR in page


def test_refresh_button_keeps_its_own_rule(env, monkeypatch):
    """Only the on-add job changed: a direct (Refresh) run still follows confidence."""
    _mock(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Aura", "FP&A", "https://aura.example", ["FP&A"], approved=1)
    lib.close()
    assert env._run_tool_research(tool_id)[0] is True
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["agent_taxonomy_needs_verification"] == 0
    lib.close()


# -- the note ---------------------------------------------------------------------

def test_note_is_on_the_new_tool_page_and_not_on_the_edit_page(env, monkeypatch):
    _mock(monkeypatch)
    admin = _client(env)
    assert 'id="on-add-note"' in admin.get("/admin/tools/software/new").text
    tool = _add(admin)
    assert 'id="on-add-note"' not in admin.get(f"/tools/software/{tool['slug']}/edit").text


def test_note_names_every_drafted_field_and_follows_the_list(env, monkeypatch):
    note = env._on_add_note_html()
    for _key, label in env._ON_ADD_DRAFTED_FIELDS:
        assert label in note
    assert gates.BADGE_TEXT_ADMIN in note and gates.BADGE_TEXT_VISITOR in note
    monkeypatch.setattr(env, "_ON_ADD_DRAFTED_FIELDS", (("x", "Zebra field"), ("y", "Other field")))
    assert "Zebra field" in env._on_add_note_html() and "Other field" in env._on_add_note_html()


def test_note_voice(env):
    text = re.sub(r"<[^>]+>", "", env._on_add_note_html())
    assert not re.search(r"\s—|—\s", text)      # no spaced em dash
    assert "member" not in text.lower() and "seamless" not in text.lower()
    assert len(re.findall(r"[.!?](?:\s|$)", text)) <= 3


_BOOKKEEPING = {"updated_at", "needs_review"}
_COLUMNS = {"agent_taxonomy": {"agent_taxonomy_note", "agent_taxonomy_needs_verification",
                               "agent_taxonomy_ai_confident", "agent_taxonomy_low_confidence"}}


def test_job_writes_only_what_the_list_names(env, monkeypatch):
    """Fails when someone adds a drafted field to the job without adding it to the note's list."""
    _mock(monkeypatch)
    db = os.environ["LINKLIB_DB"]
    lib = Library(db)
    # summary is set so opening the database again does not backfill it from
    # the description and look like a write by the job.
    tool_id = lib.add_tool("Aura", "FP&A", "https://aura.example", ["FP&A"], approved=1, summary="Short.")
    lib.close()

    def snapshot():
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        row = dict(con.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone())
        cites = {r[0] for r in con.execute(
            "SELECT field_name FROM entity_citations WHERE entity_type='tool' AND entity_id=?", (tool_id,))}
        con.close()
        return row, cites

    before, _ = snapshot()
    assert env._run_tool_research(tool_id, True)[0] is True
    after, cites = snapshot()
    changed = {k for k in after if after[k] != before[k]}
    allowed = set(_BOOKKEEPING)
    for key, _label in env._ON_ADD_DRAFTED_FIELDS:
        allowed |= _COLUMNS[key]
    assert changed <= allowed, f"job wrote columns not named by _ON_ADD_DRAFTED_FIELDS: {changed - allowed}"
    assert cites == {k for k, _ in env._ON_ADD_DRAFTED_FIELDS}
