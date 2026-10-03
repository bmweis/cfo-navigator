"""PR 2a.2: one name for each Software field, wherever a visitor or an admin
sees it, and the Compare "Short summary" row (option A).

The words live in `linklib/tool_labels.py`. Labels only: no column, key,
parameter or MCP field name is derived from them, which the last tests pin
(the `agent_taxonomy` key, the `description` compare key, the stored columns).
"""
import importlib
import os
import pathlib
import re
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import compare, gates, tool_labels
from linklib.db import Library


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
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


@pytest.fixture
def admin(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    return c


@pytest.fixture
def visitor(app_module):
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, raise_server_exceptions=True)


def _lib():
    return Library(os.environ["LINKLIB_DB"])


def _tool(name="Acme", summary="Acme short summary.", description="Acme long description text.",
          agent="Acme agent note.", bottom="Acme bottom line."):
    lib = _lib()
    tid = lib.add_tool(name, description, f"https://{name.lower()}.example", ["FP&A"],
                       approved=1, summary=summary, needs_review=0)
    lib.conn.execute("UPDATE tools SET agent_taxonomy_note=?, competitive_differentiation=? WHERE id=?",
                     (agent, bottom, tid))
    lib.conn.commit()
    row = lib.get_tool(tid)
    lib.close()
    return row


# --- the edit page uses the shared words -------------------------------------

def test_edit_page_labels_come_from_the_constants(admin):
    t = _tool()
    html = admin.get(f"/tools/software/{t['slug']}/edit").text
    for label in (tool_labels.SHORT_SUMMARY, tool_labels.DESCRIPTION, tool_labels.AGENT, tool_labels.BOTTOM_LINE):
        assert re.search(rf">\s*{re.escape(label)}\s*\*?\s*(<|\{{)", html) or f">{label}" in html, label
    assert ">Agent taxonomy<" not in html and "Agent taxonomy</label>" not in html


def test_new_page_labels_come_from_the_constants(admin):
    html = admin.get("/admin/tools/software/new").text
    assert f">{tool_labels.SHORT_SUMMARY} *</label>" in html
    assert f">{tool_labels.DESCRIPTION} *</label>" in html


def test_over_limit_banner_uses_the_same_words(admin):
    t = _tool()
    r = admin.post(f"/tools/software/{t['slug']}/edit", data=dict(
        name="Acme", url="https://acme.example", description="d", summary="s",
        agent_taxonomy_note="a" * (Library.TOOL_AGENT_TAXONOMY_MAX + 1)))
    assert f"<strong>{tool_labels.AGENT}</strong>" in r.text
    assert "<strong>Agent taxonomy</strong>" not in r.text


def test_library_refusal_message_uses_the_agent_label():
    lib = Library(tempfile.mktemp(suffix=".db"))
    tid = lib.add_tool("Z", "d", "https://z.example", [], approved=1, summary="s")
    with pytest.raises(ValueError, match=re.escape(tool_labels.AGENT)):
        lib.update_tool_agent_taxonomy(tid, "x" * (Library.TOOL_AGENT_TAXONOMY_MAX + 1))
    lib.close()


# --- the profile ---------------------------------------------------------------

def test_profile_agent_heading_and_eyebrow(visitor):
    t = _tool()
    html = visitor.get(f"/tools/software/{t['slug']}").text
    assert (f'<h2 class="tp-card-h"><small>{tool_labels.EYEBROW_AGENT}</small>{tool_labels.AGENT}'
            in html)


# --- per-field badge: one admin string for edit page and profile --------------

def test_edit_badge_is_the_admin_profile_string(admin):
    t = _tool()
    lib = _lib()
    lib.conn.execute("UPDATE tools SET agent_taxonomy_needs_verification=1, "
                     "description_needs_verification=1 WHERE id=?", (t["id"],))
    lib.conn.commit()
    lib.close()
    edit = admin.get(f"/tools/software/{t['slug']}/edit").text
    profile = admin.get(f"/tools/software/{t['slug']}").text
    assert gates.BADGE_TEXT_ADMIN in edit
    assert gates.BADGE_TEXT_ADMIN in profile
    assert ">Needs verification<" not in edit


def test_injected_save_and_mark_verified_badge_uses_the_same_string(app_module):
    assert gates.BADGE_TEXT_ADMIN in app_module._GENERATE_DESC_JS
    assert "__ADMIN_UNVERIFIED_BADGE__" not in app_module._GENERATE_DESC_JS
    assert "Needs verification</span>" not in app_module._GENERATE_DESC_JS


def test_visitor_badge_stays_under_review(visitor):
    t = _tool()
    lib = _lib()
    lib.conn.execute("UPDATE tools SET agent_taxonomy_needs_verification=1 WHERE id=?", (t["id"],))
    lib.conn.commit()
    lib.close()
    html = visitor.get(f"/tools/software/{t['slug']}").text
    assert gates.BADGE_TEXT_VISITOR in html
    assert gates.BADGE_TEXT_ADMIN not in html


# --- Compare: option A ----------------------------------------------------------

def _compare_html(client, *tools):
    ids = ",".join(str(t["id"]) for t in tools)
    return client.get(f"/tools/software/compare?ids={ids}").text


def _row_label_cells(html):
    return re.findall(r'<td class="cc-cell cc-label[^"]*">([^<]*)</td>', html)


def test_compare_row_is_short_summary_and_shows_only_the_summary(visitor):
    a = _tool("Alpha", summary="ALPHA-SHORT", description="ALPHA-LONG-DESCRIPTION")
    b = _tool("Beta", summary="BETA-SHORT", description="BETA-LONG-DESCRIPTION")
    html = _compare_html(visitor, a, b)
    labels = _row_label_cells(html)
    assert tool_labels.SHORT_SUMMARY in labels
    assert "Description" not in labels
    assert "ALPHA-SHORT" in html and "BETA-SHORT" in html
    assert "ALPHA-LONG-DESCRIPTION" not in html and "BETA-LONG-DESCRIPTION" not in html
    assert tool_labels.SECTION_AGENT in labels and tool_labels.BOTTOM_LINE in labels
    assert tool_labels.COMPETITORS in labels


def _blank_summary(tid, value):
    c = sqlite3.connect(os.environ["LINKLIB_DB"])
    c.execute("UPDATE tools SET summary=? WHERE id=?", (value, tid))
    c.commit()
    c.close()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_empty_or_whitespace_summary_shows_placeholder_not_the_description(visitor, blank):
    """Library.__init__ refills an empty summary from the description on every
    open, so the entities are built directly here, the one way a blank summary
    can reach build_software_compare."""
    t = dict(_tool("Gamma", summary="x", description="GAMMA-LONG-DESCRIPTION"))
    t["summary"] = blank
    t2 = dict(_tool("Delta", summary="DELTA-SHORT"))
    entities, _ = compare.build_software_compare([t, t2], {}, {})
    f = entities[0].sections[0].fields[0]
    assert f.label == tool_labels.SHORT_SUMMARY
    assert f.text == "" and f.state == gates.GateState.EMPTY
    assert entities[1].sections[0].fields[0].text == "DELTA-SHORT"
    assert "GAMMA-LONG-DESCRIPTION" not in repr(entities[0].sections[0])


def test_compare_row_empty_state_uses_the_standard_placeholder(app_module):
    t = dict(_tool("Eps", summary="x"))
    t["summary"] = "  "
    t2 = dict(_tool("Zeta", summary="ZETA-SHORT"))
    entities, _ = compare.build_software_compare([t, t2], {}, {})
    cell = app_module._cmp_section_cell_html(entities[0].sections[0], False, "tool_description")
    assert gates.COMPARE_EMPTY_LABELS["tool_description"] in cell or "Not available" in cell


# --- labels only: no key, column or parameter changed ---------------------------

def test_no_key_or_column_name_was_touched(app_module):
    t = _tool()
    entities, _ = compare.build_software_compare([t], {}, {})
    keys = [s.fields[0].key for s in entities[0].sections]
    assert keys == ["description", "agent_taxonomy", "competitive_differentiation"]
    cols = {r[1] for r in sqlite3.connect(os.environ["LINKLIB_DB"]).execute("PRAGMA table_info(tools)")}
    assert {"summary", "description", "agent_taxonomy_note", "competitive_differentiation"} <= cols
    for needle in ("agent_taxonomy_note", "competitive_differentiation", "summary"):
        assert needle not in vars(tool_labels).values()


def test_tool_labels_is_a_leaf_module():
    src = pathlib.Path(tool_labels.__file__).read_text()
    assert not re.search(r"^\s*(import|from)\s", src, re.M)
