"""Software comparison matrix (/tools/software/compare, Phase 5) and the
agent-taxonomy free-text field (a Phase 0 decision never actually shipped
until now, since the matrix is the first thing that needed it rendered).
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


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


# -- agent_taxonomy_note ------------------------------------------------------

def test_agent_taxonomy_saved_via_admin_edit(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "agent_taxonomy_note": "Fully independent AI agent, not a bolted-on feature.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["agent_taxonomy_note"] == "Fully independent AI agent, not a bolted-on feature."
    lib.close()


def test_agent_taxonomy_shown_on_profile_and_searchable_on_card(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Agent-assisted, not fully autonomous.")
    lib.close()

    client = _client(env)
    r = client.get("/tools/software/runway")
    assert r.status_code == 200
    assert "Agent taxonomy" in r.text
    assert "Agent-assisted, not fully autonomous." in r.text

    r = client.get("/tools/software")
    assert "Agent-assisted, not fully autonomous." in r.text   # present in the embedded ALL_TOOLS JSON


def test_agent_taxonomy_hidden_when_empty(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Solo Co", "No agent taxonomy set.", "https://solo.example", [], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "Agent taxonomy" not in r.text


# -- compare route ------------------------------------------------------------

def test_compare_route_not_swallowed_by_slug_route(env):
    """Regression: /tools/software/compare must resolve to the compare view,
    not 404 as if "compare" were a slug — route registration order matters."""
    r = _client(env).get("/tools/software/compare")
    assert r.status_code == 200
    assert "Compare software" in r.text
    assert "Pick at least two tools" in r.text


def test_compare_requires_at_least_two(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software/compare?ids=1")
    assert "Pick at least two tools" in r.text


def test_compare_renders_directory_fields_side_by_side(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Financial planning for high-growth teams.", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "Financial planning inside Excel.", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Fully independent agent.")
    lib.update_tool_differentiation(b, "Keeps teams in Excel.")
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "Runway" in r.text and "Datarails" in r.text
    assert "Financial planning for high-growth teams." in r.text
    assert "Financial planning inside Excel." in r.text
    assert "Fully independent agent." in r.text
    assert "Keeps teams in Excel." in r.text


def test_compare_gives_agent_involvement_its_own_section(env):
    """AI/agent involvement is a dedicated section (same visual weight as
    Features), not just another row lumped in with Description — buyers
    increasingly ask about this first."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Concourse", "AI agents for finance.", "https://concourse.co", ["FP&A"], approved=1)
    b = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_agent_taxonomy(a, "Fully independent agent that runs the whole workflow.")
    # Runway has no agent_taxonomy_note set — must read as "not documented",
    # never as "this tool has no agent capability."
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "AI / Agent involvement" in r.text
    assert "Fully independent agent that runs the whole workflow." in r.text
    assert "Not documented yet" in r.text
    assert "not have" not in r.text.lower() and "no agent" not in r.text.lower()


def test_agent_taxonomy_unverified_hidden_from_public_profile(env):
    """A drafted (unconfirmed) agent_taxonomy_note is a publish gate, not
    just a badge — added after a confirmed fabrication (Abacum) sat
    unreviewed and publicly visible. A public visitor never sees it."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." not in r.text


def test_agent_taxonomy_unverified_visible_to_admin_labeled_hidden(env):
    """The same unconfirmed note IS visible to a signed-in admin, clearly
    labeled as hidden from public visitors — so it can actually be
    reviewed and verified."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." in r.text
    assert "hidden from visitors" in r.text
    assert ".tp-verify{" in r.text   # profile page has its own <style> block


def test_agent_taxonomy_no_flag_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Uses an LLM-drafted agent summary.", 0.5)
    lib.mark_tool_agent_taxonomy_verified(a)
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert "Uses an LLM-drafted agent summary." in r.text
    assert '<span class="tp-verify">unverified' not in r.text


def test_compare_hides_unverified_agent_taxonomy_from_public(env):
    """Same publish gate on the compare matrix: an unverified note reads as
    "Not documented yet" to a public visitor, never the drafted text."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Drafted agent note for Runway.", 0.5)
    lib.update_tool_agent_taxonomy(b, "Confirmed agent note for Datarails.")
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted agent note for Runway." not in r.text
    assert "Confirmed agent note for Datarails." in r.text
    assert "Not documented yet" in r.text


def test_compare_shows_agent_taxonomy_verification_flag_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Drafted agent note for Runway.", 0.5)
    lib.update_tool_agent_taxonomy(b, "Confirmed agent note for Datarails.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert '<span class="cc-verify">unverified' in r.text
    assert "Drafted agent note for Runway." in r.text
    assert "Confirmed agent note for Datarails." in r.text
    # The confirmed tool's note must not itself carry the flag.
    confirmed_idx = r.text.index("Confirmed agent note for Datarails.")
    assert "cc-verify" not in r.text[confirmed_idx:confirmed_idx + 80]


def test_compare_caps_at_four_tools(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = [lib.add_tool(f"Tool {i}", "d", f"https://tool{i}.com", ["FP&A"], approved=1) for i in range(6)]
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={','.join(str(i) for i in ids)}")
    assert r.status_code == 200
    for i in range(4):
        assert f"Tool {i}" in r.text
    for i in range(4, 6):
        assert f"Tool {i}" not in r.text


def test_compare_excludes_unapproved_tools(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Pending Co", "Not approved", "https://pending.example", [], approved=0)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Pick at least two tools" in r.text   # only 1 approved tool made it in, falls below the minimum


def test_directory_card_has_compare_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software")
    assert "toggleToolCompareSelect" in r.text
    assert "TOOL_COMPARE_MAX = 4" in r.text
