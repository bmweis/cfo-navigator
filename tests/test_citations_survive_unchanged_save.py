"""Issue #634: a Save must not wipe the citations Generate just wrote.

Rule under test (three branches, per field): fresh validated citations
arriving with the save are written; otherwise the citations are cleared only
if the field's text actually changed (voice_mechanics.norm_for_compare:
whitespace-only edits, CRLF vs LF, NULL vs "" and spaced-em-dash
normalization all count as unchanged); otherwise the stored rows are left
alone. Covers the three sites: Library.update_tool_agent_taxonomy, the tool
edit route's Description branch, and the community profile route.

The route/DB tests deliberately import nothing new, so the identical file
can be run against the pre-fix commit to show them failing.
"""
import importlib
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library

CITES = [{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]
CITES_JSON = '[{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]'
FRESH_JSON = '[{"n": 1, "title": "Fresh", "url": "https://fresh.example", "type": "tool_page"}]'


@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _lib():
    return Library(os.environ["LINKLIB_DB"])


def _tool(description="Old description.", note="Agent note.", desc_cites=True, note_cites=True):
    lib = _lib()
    tid = lib.add_tool("Runway", description, "https://runway.com", [], approved=1, summary="Old summary.")
    lib.update_tool_agent_taxonomy(tid, note)
    if desc_cites:
        lib.set_entity_citations("tool", tid, "description", CITES, model="claude-opus-5")
    if note_cites:
        lib.set_entity_citations("tool", tid, "agent_taxonomy", CITES, model="claude-opus-5")
    slug = lib.get_tool(tid)["slug"]
    lib.close()
    return tid, slug


def _edit(client, slug, **over):
    data = {"name": "Runway", "url": "https://runway.com", "description": "Old description.",
            "summary": "Old summary.", "agent_taxonomy_note": "Agent note."}
    data.update(over)
    r = client.post(f"/tools/software/{slug}/edit", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]


def _cites(kind, eid, field):
    lib = _lib()
    try:
        return lib.get_entity_citations(kind, eid, field)
    finally:
        lib.close()


# -- Agent taxonomy ----------------------------------------------------------

def test_agent_taxonomy_generate_then_unchanged_save_keeps_citations(client):
    """Generate persists draft + citations in its own request (what
    _run_tool_research does); the reloaded form re-posts the note untouched."""
    lib = _lib()
    tid = lib.add_tool("Runway", "Old description.", "https://runway.com", [], approved=1, summary="Old summary.")
    slug = lib.get_tool(tid)["slug"]
    lib.set_tool_agent_taxonomy_draft(tid, "Drafted note [1].", needs_verification=1)
    lib.set_entity_citations("tool", tid, "agent_taxonomy", CITES, model="claude-opus-5")
    lib.close()

    _edit(client, slug, agent_taxonomy_note="Drafted note [1].")

    assert _cites("tool", tid, "agent_taxonomy") == CITES


def test_agent_taxonomy_hand_edited_note_clears_citations(client):
    tid, slug = _tool()
    _edit(client, slug, agent_taxonomy_note="A note a human rewrote.")
    assert _cites("tool", tid, "agent_taxonomy") == []


def test_agent_taxonomy_library_method_unchanged_vs_changed(app_module):
    tid, _ = _tool()
    lib = _lib()
    lib.update_tool_agent_taxonomy(tid, "Agent note.")
    assert lib.get_entity_citations("tool", tid, "agent_taxonomy") == CITES
    lib.update_tool_agent_taxonomy(tid, "Different note.")
    assert lib.get_entity_citations("tool", tid, "agent_taxonomy") == []
    lib.close()


def test_agent_taxonomy_crlf_submitted_vs_lf_stored_is_unchanged(client):
    lib = _lib()
    tid = lib.add_tool("Runway", "Old description.", "https://runway.com", [], approved=1, summary="Old summary.")
    slug = lib.get_tool(tid)["slug"]
    lib.update_tool_agent_taxonomy(tid, "- one\n- two")
    lib.set_entity_citations("tool", tid, "agent_taxonomy", CITES, model="m")
    lib.close()
    _edit(client, slug, agent_taxonomy_note="- one\r\n- two")
    assert _cites("tool", tid, "agent_taxonomy") == CITES


def test_agent_taxonomy_spaced_em_dash_stored_normalized_is_unchanged(client):
    """Stored text was normalized on write (spaced em dash collapsed); the
    form re-posts whatever the textarea held, which may still be spaced."""
    lib = _lib()
    tid = lib.add_tool("Runway", "Old description.", "https://runway.com", [], approved=1, summary="Old summary.")
    slug = lib.get_tool(tid)["slug"]
    lib.update_tool_agent_taxonomy(tid, "Fast — and cheap.")
    assert lib.get_tool(tid)["agent_taxonomy_note"] == "Fast—and cheap."
    lib.set_entity_citations("tool", tid, "agent_taxonomy", CITES, model="m")
    lib.close()
    _edit(client, slug, agent_taxonomy_note="Fast — and cheap.")
    assert _cites("tool", tid, "agent_taxonomy") == CITES


def test_agent_taxonomy_whitespace_only_edit_is_unchanged(client):
    tid, slug = _tool()
    _edit(client, slug, agent_taxonomy_note="  Agent   note.\n\n")
    assert _cites("tool", tid, "agent_taxonomy") == CITES


# -- Description -------------------------------------------------------------

def test_description_fresh_page_load_unrelated_save_keeps_citations(client):
    """Hidden ai-drafted-citations field is empty on a fresh load; changing
    only the summary must not touch the description's citations."""
    tid, slug = _tool()
    _edit(client, slug, summary="A brand new summary.")
    assert _cites("tool", tid, "description") == CITES


def test_description_changed_with_fresh_citations_writes_them(client):
    tid, slug = _tool()
    _edit(client, slug, description="Freshly generated description.",
          ai_drafted_fields="description,summary", ai_drafted_citations=FRESH_JSON,
          ai_drafted_citations_model="claude-opus-5")
    got = _cites("tool", tid, "description")
    assert [c["url"] for c in got] == ["https://fresh.example"]


def test_description_changed_without_citations_clears(client):
    tid, slug = _tool()
    _edit(client, slug, description="Hand-edited description.")
    assert _cites("tool", tid, "description") == []


def test_description_fresh_draft_with_zero_citations_and_changed_text_clears(client):
    tid, slug = _tool()
    _edit(client, slug, description="Ungrounded new draft.",
          ai_drafted_fields="description,summary", ai_drafted_citations="[]")
    assert _cites("tool", tid, "description") == []


def test_description_crlf_and_whitespace_only_is_unchanged(client):
    lib = _lib()
    tid = lib.add_tool("Runway", "Line one.\nLine two.", "https://runway.com", [], approved=1, summary="S.")
    slug = lib.get_tool(tid)["slug"]
    lib.set_entity_citations("tool", tid, "description", CITES, model="m")
    lib.close()
    _edit(client, slug, description="Line one.\r\nLine two.  ", summary="S.")
    assert _cites("tool", tid, "description") == CITES


def test_description_spaced_em_dash_is_unchanged(client):
    lib = _lib()
    tid = lib.add_tool("Runway", "Fast — cheap.", "https://runway.com", [], approved=1, summary="S.")
    slug = lib.get_tool(tid)["slug"]
    assert lib.get_tool(tid)["description"] == "Fast—cheap."
    lib.set_entity_citations("tool", tid, "description", CITES, model="m")
    lib.close()
    _edit(client, slug, description="Fast — cheap.", summary="S.")
    assert _cites("tool", tid, "description") == CITES


# -- Community profile -------------------------------------------------------

def _community(**fields):
    lib = _lib()
    cid = lib.add_community(name="Chief", url="https://chief.com", demographic="Senior executive women",
                            cost_band="Paid", categories=[], approved=1)
    base = dict(ideal_member="Senior women.", value_prop="Peer network.", verdict_summary="Solid.",
                founded_year=2019)
    base.update(fields)
    lib.upsert_community_profile(cid, **base)
    lib.set_entity_citations("community", cid, "community_profile", CITES, model="claude-opus-5")
    lib.close()
    return cid


def _profile_post(client, cid, **over):
    data = {"ideal_member": "Senior women.", "value_prop": "Peer network.",
            "verdict_summary": "Solid.", "founded_year": "2019"}
    data.update(over)
    r = client.post(f"/admin/tools/communities/{cid}/profile", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]


def test_profile_unchanged_save_with_empty_hidden_field_keeps_citations(client):
    cid = _community()
    _profile_post(client, cid)
    assert _cites("community", cid, "community_profile") == CITES


def test_profile_one_changed_field_invalidates_shared_set(client):
    cid = _community()
    _profile_post(client, cid, value_prop="A hand-edited value prop.")
    assert _cites("community", cid, "community_profile") == []


def test_profile_changed_with_fresh_citations_writes_them(client):
    cid = _community()
    _profile_post(client, cid, value_prop="Freshly generated.",
                  ai_drafted_fields="value_prop", ai_drafted_citations=FRESH_JSON,
                  ai_drafted_citations_model="claude-opus-5")
    got = _cites("community", cid, "community_profile")
    assert [c["url"] for c in got] == ["https://fresh.example"]


def test_profile_fresh_draft_with_zero_citations_and_changed_text_clears(client):
    cid = _community()
    _profile_post(client, cid, value_prop="Ungrounded redraft.",
                  ai_drafted_fields="value_prop", ai_drafted_citations="[]")
    assert _cites("community", cid, "community_profile") == []


def test_profile_crlf_whitespace_and_founded_year_int_vs_string_unchanged(client):
    cid = _community(value_prop="Line one.\nLine two.")
    _profile_post(client, cid, value_prop="Line one.\r\nLine two.  ", founded_year=" 2019 ")
    assert _cites("community", cid, "community_profile") == CITES


def test_profile_compare_is_derived_from_field_id_list(app_module, monkeypatch):
    """Not a hardcoded 23-field list: shrinking _COMMUNITY_PROFILE_FIELD_IDS
    (as the next PR will) narrows the compare with it, without error."""
    existing = {"value_prop": "a", "ideal_member": "x"}

    class F(dict):
        pass

    form = F(value_prop="CHANGED", ideal_member="x")
    assert app_module._community_profile_text_changed(existing, form, None) is True
    monkeypatch.setattr(app_module, "_COMMUNITY_PROFILE_FIELD_IDS", ["ideal_member"])
    assert app_module._community_profile_text_changed(existing, form, None) is False


# -- norm_for_compare unit ---------------------------------------------------

def test_norm_for_compare_none_and_empty_are_the_same():
    from linklib.voice_mechanics import norm_for_compare
    assert norm_for_compare(None) == norm_for_compare("") == ""


def test_norm_for_compare_crlf_whitespace_and_em_dash():
    from linklib.voice_mechanics import norm_for_compare
    assert norm_for_compare("a\r\nb") == norm_for_compare("a\nb") == "a b"
    assert norm_for_compare("  a   b \n") == "a b"
    assert norm_for_compare("x — y") == norm_for_compare("x—y")
    assert norm_for_compare("one") != norm_for_compare("two")
