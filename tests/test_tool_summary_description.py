"""Description-length follow-up: `tools.description` grows into a full
~8-12 sentence profile-page write-up; `tools.summary` is a new short 2-3
sentence field for the directory card and client-side search. Covers the
schema migration/backfill, the DB write paths (add/update/quick-edit —
including the bulk-edit echo-through that must never blank summary), the
two-part `generate_tool_description` LLM draft, and the admin UI wiring.
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


# -- schema / backfill -----------------------------------------------------------

def test_new_tool_has_empty_summary_by_default(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "FP&A platform", "https://runway.com", [], approved=1)
    tool = lib.get_tool(tool_id)
    assert tool["summary"] == ""
    lib.close()


def test_add_tool_accepts_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "Long description here.", "https://runway.com", [],
                            approved=1, summary="Short summary.")
    tool = lib.get_tool(tool_id)
    assert tool["summary"] == "Short summary."
    lib.close()


def test_migration_backfills_summary_from_description(tmp_path):
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    # A row added without a summary (the pre-migration shape — description only).
    tool_id = lib.add_tool("Runway", "Old short description.", "https://runway.com", [], approved=1)
    assert lib.get_tool(tool_id)["summary"] == ""
    lib.close()

    # Reopening the DB re-runs the migration/backfill step.
    lib2 = Library(db_path)
    tool = lib2.get_tool(tool_id)
    assert tool["summary"] == "Old short description."
    lib2.close()


def test_backfill_never_overwrites_a_real_summary(tmp_path):
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                            approved=1, summary="Real drafted summary.")
    lib.close()

    lib2 = Library(db_path)  # re-run migration/backfill
    assert lib2.get_tool(tool_id)["summary"] == "Real drafted summary."
    lib2.close()


# -- DB write paths ---------------------------------------------------------------

def test_update_tool_writes_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "desc", "https://runway.com", [], approved=1, summary="old summary")
    lib.update_tool(tool_id, "Runway", "new long description", "https://runway.com", [],
                    summary="new short summary")
    tool = lib.get_tool(tool_id)
    assert tool["description"] == "new long description"
    assert tool["summary"] == "new short summary"
    lib.close()


def test_quick_update_tool_writes_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "desc", "https://runway.com", [], approved=1, summary="old summary")
    lib.quick_update_tool(tool_id, "new desc", 0, "", "", summary="new summary")
    tool = lib.get_tool(tool_id)
    assert tool["description"] == "new desc"
    assert tool["summary"] == "new summary"
    lib.close()


# -- generate_tool_description (mocked Claude call, voice_core="Test voice guide.") --------------------------------

def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=300, output_tokens=200,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def _mock_fetch_page(monkeypatch, content=""):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content=content))


def test_generate_tool_description_parses_both_fields(monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform for finance teams.")
    _mock_anthropic(monkeypatch,
        "Runway is a financial planning platform built for finance teams at "
        "growth-stage companies. It centralizes headcount, revenue, and expense planning "
        "into a single collaborative model.\n\n"
        "SUMMARY: Runway is an FP&A platform for growth-stage finance teams. It centralizes "
        "headcount and revenue planning into one collaborative model.\n\n"
        "CONFIDENT: true")
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert "financial planning platform" in draft.description
    assert "FP&A platform" in draft.summary
    assert draft.low_confidence is False
    assert draft.cost_usd > 0


def test_generate_tool_description_low_confidence_when_no_page_content(monkeypatch):
    _mock_fetch_page(monkeypatch, "")
    _mock_anthropic(monkeypatch, "A finance tool.\n\nSUMMARY: A finance tool.\n\nCONFIDENT: false")
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.low_confidence is True


def test_generate_tool_description_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is None


# -- admin routes -------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # 2026-08 visibility follow-up: the generate-* routes now refuse
    # (require_voice_setting) unless voice_core is seeded.
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
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


def test_admin_add_tool_requires_summary(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com", "description": "Long description.",
    })
    assert r.status_code == 400
    assert "summary" in r.json()["detail"].lower()


def test_admin_add_tool_saves_summary(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com", "description": "Long description.",
        "summary": "Short summary.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["summary"] == "Short summary."
    lib.close()


def test_public_submission_uses_description_as_initial_summary(env):
    client = _client(env)
    _login(client)  # local dev: no password gate distinction, but _is_member covers it
    r = client.post("/tools/submit", data={
        "name": "Runway", "url": "https://runway.com", "description": "A short pitch.",
        "submitted_by": "brian@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=False)[0]
    assert tool["summary"] == "A short pitch."
    lib.close()


def test_bulk_edit_does_not_blank_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Real summary text.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/bulk-edit", json={
        "ids": [tool_id], "field": "advisor", "value": "1",
    })
    assert r.status_code == 200

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["advisor"] == 1
    assert tool["summary"] == "Real summary text."   # not wiped by the bulk toggle
    lib.close()


def test_quick_edit_route_saves_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Old summary.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/quick-edit", json={
        "description": "Updated long description.", "summary": "Updated summary.",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["tool"]["summary"] == "Updated summary."

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["summary"] == "Updated summary."
    lib.close()


def test_generate_description_route_returns_summary(env, monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform.")
    _mock_anthropic(monkeypatch, "A long description.\n\nSUMMARY: A short summary.\n\nCONFIDENT: true")
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/generate-description", json={
        "name": "Runway", "url": "https://runway.com",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["description"] == "A long description."
    assert d["summary"] == "A short summary."


def test_directory_page_serializes_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                approved=1, summary="Short card summary.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software")
    assert r.status_code == 200
    assert "Short card summary." in r.text


# -- Edit-page-fixes item 1: Short summary won't accept input (2026-09) -----------
#
# Root cause: a legacy `summary` value can already exceed the textarea's
# `maxlength="400"` (the one-time column-introducing migration backfilled
# `summary` from the full, uncapped `description`, and nothing server-side
# has ever enforced a length limit). HTML `maxlength` blocks appending any
# new character once the field's current value is already at or past the
# cap — the textarea still focuses and shows a blinking cursor, but nothing
# typed lands. Originally fixed by rendering `maxlength="400"` only when
# the stored value already fits inside it — that conditional-guard
# mechanism is retired as of the character-budget-limits-targets PR
# (2026-09): `summary` now uses the same `_char_budget` live-counter/
# server-side-refusal mechanism as description/agent_taxonomy_note/
# competitive_differentiation (target 400, max 800), which never blocks
# typing at all, at any length — see `tests/test_char_budget_targets.py`
# for the coverage of that shared mechanism, including a legacy
# over-old-cap value round-tripping unchanged and the hard-limit refusal.

def test_edit_page_never_uses_maxlength_on_summary_even_when_over_the_old_cap(env):
    lib = Library(os.environ["LINKLIB_DB"])
    over_old_cap = "A" * 450
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary=over_old_cap)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    summary_i = r.text.index('<textarea id="tool-summary"')
    summary_field = r.text[summary_i:summary_i + 300]
    assert 'name="summary" required' in summary_field
    assert 'maxlength=' not in summary_field
    assert over_old_cap in r.text


def test_edit_page_omits_maxlength_on_summary_when_it_comfortably_fits(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="A normal, compliant short summary.")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    # No maxlength on this field at all — the live char-budget counter is
    # the only guard now (other fields on this page, e.g. Name, legitimately
    # keep their own maxlength, so scope the check to the summary field).
    summary_i = r.text.index('<textarea id="tool-summary"')
    summary_field = r.text[summary_i:summary_i + 300]
    assert 'maxlength=' not in summary_field
    assert 'data-char-limit="800"' in summary_field
    assert 'data-char-target="400"' in summary_field


def test_admin_can_save_a_trimmed_summary_after_it_was_over_the_old_cap(env):
    """End-to-end: a legacy over-old-cap value can be edited and saved without
    ever being blocked — confirms the whole round trip, not just the
    rendered attribute."""
    lib = Library(os.environ["LINKLIB_DB"])
    over_old_cap = "A" * 450
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary=over_old_cap)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "Long description.",
        "summary": "Trimmed, compliant short summary.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["summary"] == "Trimmed, compliant short summary."
    lib.close()

    # No maxlength ever reappears on this field — the char-budget counter
    # is the guard now, not a conditional HTML attribute.
    client2 = _client(env)
    _login(client2)
    r2 = client2.get(f"/tools/software/{slug}/edit")
    summary_i = r2.text.index('<textarea id="tool-summary"')
    summary_field = r2.text[summary_i:summary_i + 300]
    assert 'maxlength=' not in summary_field


# -- Edit-page-fixes item 2: "Bottom line" label mismatch (2026-09) ---------------
#
# tools.competitive_differentiation renders verbatim as the profile page's
# "Bottom line" callout — confirmed a direct 1:1 mapping, no transformation.
# The edit page's field was still labeled "Competitive differentiation",
# which didn't match what admins actually see once it ships. Renamed to
# "Bottom line" — label only, no schema/route/display-logic change.

def test_edit_page_labels_the_field_bottom_line(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Short summary.")
    lib.update_tool_differentiation(tool_id, "Best for finance teams that want speed.", 0)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert ">Bottom line<" in r.text
    assert "Competitive differentiation<" not in r.text


def test_edit_page_label_matches_public_profile_display_name(env):
    """The label an admin edits under should read exactly like the heading a
    visitor sees on the public profile page — that's the whole point of the
    fix (item 2)."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Short summary.")
    lib.update_tool_differentiation(tool_id, "Best for finance teams that want speed.", 0)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    edit_html = client.get(f"/tools/software/{slug}/edit").text
    profile_html = client.get(f"/tools/software/{slug}").text
    assert "Bottom line" in edit_html
    assert "Bottom line" in profile_html
