"""Review-state publish gates for Description (tools) and Community profile —
extends the same Abacum-fabrication-finding pattern Agent taxonomy already
uses (tests/test_software_compare.py) to two more AI-drafted fields that
previously rendered publicly regardless of review state. See CLAUDE.md's
"Agent taxonomy publish gate" bullet for the original mechanism and the
Phase 2/Phase 3 PRs that flagged Description/Community profile as follow-ups.

Covers, for each field, at every public render site found in the Phase 0
investigation: the profile page, the compare matrix, and (Description only —
Community profile's directory card sources from `communities`, not
`community_profiles`, so it was never a leak) the `/tools/software` directory
card's visible render and client-side search-match string. Also covers the
review-state columns' NOT NULL DEFAULT 0 null-safety, per the task's explicit
ask, even though there is no real NULL case in practice for either column.
"""
import os
import pathlib
import sqlite3
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


# -- Description: profile page -------------------------------------------------

def test_description_unverified_hidden_from_public_profile(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "An LLM-drafted description of Runway's platform.",
                      "https://runway.com", ["FP&A"], approved=1, summary="LLM-drafted short summary.",
                      description_needs_verification=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert r.status_code == 200
    assert "An LLM-drafted description of Runway's platform." not in r.text
    assert "LLM-drafted short summary." not in r.text  # hero subhead, same gate


def test_description_unverified_visible_to_admin_labeled_hidden(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "An LLM-drafted description of Runway's platform.",
                      "https://runway.com", ["FP&A"], approved=1, summary="LLM-drafted short summary.",
                      description_needs_verification=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "An LLM-drafted description of Runway's platform." in r.text
    assert "LLM-drafted short summary." in r.text
    assert "hidden from visitors" in r.text
    assert ".tp-verify{" in r.text


def test_description_no_flag_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A confirmed, human-reviewed description.",
                      "https://runway.com", ["FP&A"], approved=1,
                      description_needs_verification=0)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "A confirmed, human-reviewed description." in r.text
    assert '<span class="tp-verify">unverified' not in r.text


# -- Description: compare matrix -----------------------------------------------

def test_compare_hides_unverified_description_from_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Drafted description for Runway.", "https://runway.com",
                      ["FP&A"], approved=1, description_needs_verification=1)
    b = lib.add_tool("Datarails", "Confirmed description for Datarails.", "https://datarails.com",
                      ["FP&A"], approved=1, description_needs_verification=0)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted description for Runway." not in r.text
    assert "Confirmed description for Datarails." in r.text
    assert "Not available yet" in r.text


def test_compare_shows_description_verification_flag_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Drafted description for Runway.", "https://runway.com",
                      ["FP&A"], approved=1, description_needs_verification=1)
    b = lib.add_tool("Datarails", "Confirmed description for Datarails.", "https://datarails.com",
                      ["FP&A"], approved=1, description_needs_verification=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted description for Runway." in r.text
    assert "Confirmed description for Datarails." in r.text
    assert '<span class="cc-verify">unverified' in r.text
    confirmed_idx = r.text.index("Confirmed description for Datarails.")
    assert "cc-verify" not in r.text[confirmed_idx:confirmed_idx + 80]


# -- Description: /tools/software directory card -------------------------------
# The card's ALL_TOOLS JSON payload used to keep the raw description/summary/
# agent_taxonomy_note text regardless of verification state, relying only on
# client-side JS (AUTHED) to hide what had already been sent — a real leak,
# since the JSON lands in page source either way, indexable by a crawler and
# readable via view-source by anyone. Fixed (page-source leak fix, same PR as
# the matchmaker context filter): the server already knows AUTHED when
# building this page, so an unverified field is now stripped to "" in the
# JSON itself for an anonymous/non-admin response — the admin Quick Edit
# panel, which needs the raw text verbatim even when unverified, still gets
# it because an authed response is never stripped. The client-side
# render/search gates stay in place as defense in depth for the admin case
# (there's nothing left to hide for anon now that the payload itself is
# stripped) — the actual DOM/search behavior for each visitor type was
# verified live with Playwright during development (see the PR description),
# consistent with this repo's own testing-standard precedent for
# client-side-only behavior (tests/test_play_route.py's module docstring).

def test_directory_card_json_carries_description_verification_flag(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "Drafted description for Runway.", "https://runway.com",
                 ["FP&A"], approved=1, description_needs_verification=1)
    lib.add_tool("Datarails", "Confirmed description for Datarails.", "https://datarails.com",
                 ["FP&A"], approved=1, description_needs_verification=0)
    lib.close()

    r = _client(env).get("/tools/software")
    assert r.status_code == 200
    import re, json as _json
    m = re.search(r"var ALL_TOOLS = (\[.*?\]);", r.text)
    assert m, "ALL_TOOLS JSON not found in page"
    tools = {t["name"]: t for t in _json.loads(m.group(1))}
    assert tools["Runway"]["description_needs_verification"] is True
    assert tools["Datarails"]["description_needs_verification"] is False


def test_directory_card_json_strips_unverified_text_for_anon_keeps_for_admin(env):
    """The actual page-source leak fix: an unverified description/summary/
    agent_taxonomy_note is stripped to "" in the JSON payload itself for an
    anonymous response, not just hidden by client JS — and an authed
    response still carries the raw text, since admin Quick Edit needs it."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool(
        "Runway", "A fabricated-sounding drafted description for Runway.",
        "https://runway.com", ["FP&A"], approved=1,
        summary="A drafted short summary.",
        description_needs_verification=1,
    )
    lib.set_tool_agent_taxonomy_draft(
        tid, "A drafted, unverified agent taxonomy note.", needs_verification=1,
    )
    lib.close()

    import re, json as _json

    anon = _client(env).get("/tools/software")
    m = re.search(r"var ALL_TOOLS = (\[.*?\]);", anon.text)
    tool = _json.loads(m.group(1))[0]
    assert tool["description"] == ""
    assert tool["summary"] == ""
    assert tool["agent_taxonomy_note"] == ""
    assert "fabricated-sounding" not in anon.text
    assert "drafted, unverified agent taxonomy note" not in anon.text

    client = _client(env)
    _login(client)
    admin = client.get("/tools/software")
    m = re.search(r"var ALL_TOOLS = (\[.*?\]);", admin.text)
    tool = _json.loads(m.group(1))[0]
    assert tool["description"] == "A fabricated-sounding drafted description for Runway."
    assert tool["summary"] == "A drafted short summary."
    assert tool["agent_taxonomy_note"] == "A drafted, unverified agent taxonomy note."


def test_directory_card_gates_visible_render_and_search_on_authed(env):
    """Structural check that the card-render and search-filter JS both branch
    on description_needs_verification && !AUTHED — the actual DOM outcome for
    each visitor type was verified live with a real headless-browser session
    (Playwright), not re-derived from a TestClient-rendered string here."""
    r = _client(env).get("/tools/software")
    assert "descHiddenFromVisitor" in r.text
    assert "descHiddenFromSearch" in r.text
    assert "t.description_needs_verification && !AUTHED" in r.text


# -- Community profile: profile page --------------------------------------------

def test_community_profile_unverified_hidden_from_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="A drafted ideal-member note.",
        verdict_summary="A drafted bottom-line verdict.",
        founded_year=2019, cpe_eligible="Yes",
        needs_review=1,
    )
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "A drafted ideal-member note." not in r.text
    assert "A drafted bottom-line verdict." not in r.text
    assert "2019" not in r.text
    assert "CPE eligible" not in r.text


def test_community_profile_unverified_visible_to_admin_labeled_hidden(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="A drafted ideal-member note.",
        verdict_summary="A drafted bottom-line verdict.",
        founded_year=2019, cpe_eligible="Yes",
        needs_review=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert "A drafted ideal-member note." in r.text
    assert "A drafted bottom-line verdict." in r.text
    assert "2019" in r.text
    assert "CPE eligible" in r.text
    assert "hidden from visitors" in r.text


def test_community_profile_no_flag_once_reviewed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="A reviewed ideal-member note.",
        verdict_summary="A reviewed bottom-line verdict.",
        needs_review=1,
    )
    lib.mark_community_profile_reviewed(c)
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "A reviewed ideal-member note." in r.text
    assert "A reviewed bottom-line verdict." in r.text
    assert '<span class="tp-verify">unverified' not in r.text


# -- Community profile: compare matrix ------------------------------------------

def test_compare_hides_unverified_community_profile_from_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, ideal_member="Drafted note for Peer CFOs.",
                                  founded_year=2020, needs_review=1)
    lib.upsert_community_profile(b, ideal_member="Confirmed note for Finance Guild.",
                                  founded_year=2015, needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert "Drafted note for Peer CFOs." not in r.text
    assert "2020" not in r.text
    assert "Confirmed note for Finance Guild." in r.text
    assert "2015" in r.text
    assert "Not available yet" in r.text


def test_compare_shows_community_profile_verification_flag_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, ideal_member="Drafted note for Peer CFOs.", needs_review=1)
    lib.upsert_community_profile(b, ideal_member="Confirmed note for Finance Guild.", needs_review=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/compare?ids={a},{b}")
    assert "Drafted note for Peer CFOs." in r.text
    assert "Confirmed note for Finance Guild." in r.text
    # .cc-verify, not .comm-verify (2026-08 brand-consistency pass split the
    # two: .comm-verify is the unrelated data-completeness "Needs
    # verification" flag; .cc-verify is this publish-gate "unverified—hidden
    # from visitors" flag, matching every other surface that renders it).
    assert '<span class="cc-verify">unverified' in r.text
    confirmed_idx = r.text.index("Confirmed note for Finance Guild.")
    assert "cc-verify" not in r.text[confirmed_idx:confirmed_idx + 80]


# -- Null-state safety -----------------------------------------------------------
# Both review-state columns are NOT NULL DEFAULT 0 (linklib/db.py migrations)
# — confirmed here first: SQLite itself rejects a raw UPDATE that tries to
# write NULL to either one, so there is no real NULL row possible in
# practice. Every gate still reads the column via bool(row.get(...)) rather
# than a bare row["..."], so a dict that's missing the key entirely (e.g. a
# future caller that builds a row dict by hand and forgets it) degrades to
# the same "verified" behavior as 0, never to "unverified" — exercised below
# by monkeypatching the lookup to return exactly such a dict, since no real
# schema-backed row can ever be in that state.

def test_review_columns_reject_null_at_the_schema_level(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description.", "https://runway.com", ["FP&A"], approved=1)
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    lib.upsert_community_profile(c, ideal_member="A note.")
    lib.close()

    conn = sqlite3.connect(os.environ["LINKLIB_DB"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tools SET description_needs_verification=NULL WHERE id=?", (a,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE community_profiles SET needs_review=NULL WHERE community_id=?", (c,))
    conn.close()


def test_description_missing_column_key_treated_as_verified_not_unverified(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description with no verification signal.",
                      "https://runway.com", ["FP&A"], approved=1,
                      description_needs_verification=0)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    real_get = Library.get_tool_by_slug

    def _missing_key(self, slug_arg):
        tool = real_get(self, slug_arg)
        if tool is not None:
            tool = dict(tool)
            del tool["description_needs_verification"]
        return tool

    monkeypatch.setattr(Library, "get_tool_by_slug", _missing_key)

    r = _client(env).get(f"/tools/software/{slug}")
    assert "A description with no verification signal." in r.text
    assert '<span class="tp-verify">unverified' not in r.text


def test_community_profile_missing_column_key_treated_as_verified_not_unverified(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(c, ideal_member="A note with no verification signal.",
                                  needs_review=0)
    lib.close()

    real_get = Library.get_community_profile

    def _missing_key(self, community_id):
        profile = real_get(self, community_id)
        if profile is not None:
            profile = dict(profile)
            del profile["needs_review"]
        return profile

    monkeypatch.setattr(Library, "get_community_profile", _missing_key)

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "A note with no verification signal." in r.text
    assert '<span class="tp-verify">unverified' not in r.text
