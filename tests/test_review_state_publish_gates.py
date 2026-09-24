"""Review-state display for Description, Agent taxonomy, Competitive
differentiation (tools) and Community profile — Brian's ratified
radical-transparency review standard (Gate-Extraction Phase 0/PR A):
"nothing ever disappears." A populated field always renders for every
viewer now, at every state (verified / populated-pending-review / empty),
with the difference between viewers limited to a trailing review-state
badge ("under review" for a visitor, "unverified, visible to visitors" for
an admin) or, for the Empty row, an extra admin-only "go fill this in"
sentence.

This supersedes the earlier hide-from-visitors publish gate this file used
to cover (added after a confirmed fabrication on Abacum's record) —
Description/Agent taxonomy/Community profile all previously hid unverified
content from a public visitor entirely; Competitive differentiation never
had a gate at all (a real, independently-confirmed pre-existing bug this
standard fixes as part of the same PR, not a separate one).

Covers, for each field, at every public render site: the profile page, the
compare matrix, and (Description/Agent taxonomy only — Community profile's
directory card sources from `communities`, not `community_profiles`, so it
was never part of this) the `/tools/software` directory card's visible
render and client-side search-match string. Also covers the review-state
columns' NOT NULL DEFAULT 0 null-safety.
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

def test_description_unverified_shown_under_review_to_public_profile(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "An LLM-drafted description of Runway's platform.",
                      "https://runway.com", ["FP&A"], approved=1, summary="LLM-drafted short summary.",
                      description_needs_verification=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert r.status_code == 200
    assert "An LLM-drafted description of Runway's platform." in r.text
    assert "LLM-drafted short summary." in r.text  # hero subhead, same field
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_description_unverified_visible_to_admin_labeled_unverified(env):
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
    assert "unverified, visible to visitors" in r.text
    assert ".tp-verify{" in r.text


def test_description_no_badge_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A confirmed, human-reviewed description.",
                      "https://runway.com", ["FP&A"], approved=1,
                      description_needs_verification=0)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "A confirmed, human-reviewed description." in r.text
    assert '<span class="tp-verify">' not in r.text


def test_description_empty_shows_placeholder_to_both_viewers_admin_gets_prompt(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "Description coming soon." in r.text
    assert "Add one from the edit page." not in r.text

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "Description coming soon. Add one from the edit page." in r.text


# -- Description: compare matrix -----------------------------------------------

def test_compare_shows_unverified_description_under_review_to_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Drafted description for Runway.", "https://runway.com",
                      ["FP&A"], approved=1, description_needs_verification=1)
    b = lib.add_tool("Datarails", "Confirmed description for Datarails.", "https://datarails.com",
                      ["FP&A"], approved=1, description_needs_verification=0)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted description for Runway." in r.text
    assert "Confirmed description for Datarails." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_compare_distinguishes_pending_from_truly_empty_description(env):
    """The third compare-cell state: a tool with a pending-review description
    and a tool with NO description at all must render two different cells —
    they used to collapse into the same "Not available yet" text for a
    public visitor, indistinguishable from each other."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "Drafted description for Runway.", "https://runway.com",
                      ["FP&A"], approved=1, description_needs_verification=1)
    b = lib.add_tool("Empty Co", "", "https://empty.example", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted description for Runway." in r.text
    assert "under review" in r.text
    assert "Not available." in r.text


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
# The card's ALL_TOOLS JSON payload used to strip unverified description/
# summary/agent_taxonomy_note text to "" for an anonymous response (a
# page-source leak fix under the old hide-from-visitors gate). Under the
# radical-transparency standard the text is visible content for every
# viewer, so it always ships now — the *_needs_verification booleans still
# ship too, driving only the client-side badge, never a content strip.

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


def test_directory_card_json_carries_unverified_text_for_every_viewer(env):
    """Radical-transparency review standard: an unverified description/
    summary/agent_taxonomy_note always ships in the JSON payload now, for
    both an anonymous and an authed response — visible content for every
    viewer, not stripped for anyone."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool(
        "Runway", "A drafted description for Runway awaiting review.",
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
    assert tool["description"] == "A drafted description for Runway awaiting review."
    assert tool["summary"] == "A drafted short summary."
    assert tool["agent_taxonomy_note"] == "A drafted, unverified agent taxonomy note."
    assert "A drafted description for Runway awaiting review." in anon.text

    client = _client(env)
    _login(client)
    admin = client.get("/tools/software")
    m = re.search(r"var ALL_TOOLS = (\[.*?\]);", admin.text)
    tool = _json.loads(m.group(1))[0]
    assert tool["description"] == "A drafted description for Runway awaiting review."
    assert tool["summary"] == "A drafted short summary."
    assert tool["agent_taxonomy_note"] == "A drafted, unverified agent taxonomy note."


def test_directory_card_shows_under_review_badge_and_search_matches_for_public(env):
    """The card's own render badge and search-match text both cover
    unverified content for every viewer now — a visitor's search can match
    an unverified description/agent taxonomy note the same as an admin's,
    since the text is visible content either way."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool(
        "Runway", "A drafted description mentioning zzsearchable.",
        "https://runway.com", ["FP&A"], approved=1,
        description_needs_verification=1,
    )
    lib.close()

    r = _client(env).get("/tools/software")
    assert "descUnverified" in r.text
    assert "tool-desc-verify" in r.text
    assert "A drafted description mentioning zzsearchable." in r.text


# -- Competitive differentiation: profile page -----------------------------------
# The one field with NO gate at all before this standard — an unverified
# "Bottom line" callout rendered identically to a verified one, to every
# viewer, with no badge for anyone (Phase 0 investigation's headline
# finding). Fixed here as a labeling fix, not a hiding fix, consistent with
# every other field.

def test_differentiation_unverified_shown_under_review_to_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description.", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "A drafted differentiation note.", needs_verification=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "A drafted differentiation note." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_differentiation_unverified_visible_to_admin_labeled_unverified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description.", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "A drafted differentiation note.", needs_verification=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "A drafted differentiation note." in r.text
    assert "unverified, visible to visitors" in r.text


def test_differentiation_no_badge_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description.", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "A confirmed differentiation note.", needs_verification=0)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "A confirmed differentiation note." in r.text
    assert '<span class="tp-verify">' not in r.text


def test_differentiation_empty_shows_placeholder_to_both_viewers_admin_gets_prompt(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "A description.", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{slug}")
    assert "Bottom line not available." in r.text
    assert "Add one from the edit page." not in r.text

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "Bottom line not available." in r.text
    assert "Add one from the edit page." in r.text


# -- Competitive differentiation: compare matrix ---------------------------------

def test_compare_shows_unverified_differentiation_under_review_to_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "Drafted differentiation for Runway.", needs_verification=1)
    lib.update_tool_differentiation(b, "Confirmed differentiation for Datarails.", needs_verification=0)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted differentiation for Runway." in r.text
    assert "Confirmed differentiation for Datarails." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_compare_distinguishes_pending_from_truly_empty_differentiation(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Empty Co", "d", "https://empty.example", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "Drafted differentiation for Runway.", needs_verification=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Drafted differentiation for Runway." in r.text
    assert "under review" in r.text
    assert "Not available." in r.text


def test_compare_shows_differentiation_verification_flag_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "Drafted differentiation for Runway.", needs_verification=1)
    lib.update_tool_differentiation(b, "Confirmed differentiation for Datarails.", needs_verification=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert '<span class="cc-verify">unverified' in r.text
    confirmed_idx = r.text.index("Confirmed differentiation for Datarails.")
    assert "cc-verify" not in r.text[confirmed_idx:confirmed_idx + 80]


# -- Community profile: whole-profile pending, per-card badges -------------------

def test_community_profile_unverified_shown_under_review_to_public(env):
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
    assert "A drafted ideal-member note." in r.text
    assert "A drafted bottom-line verdict." in r.text
    assert "2019" in r.text
    assert "CPE eligible" in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_community_profile_unverified_visible_to_admin_labeled_unverified(env):
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
    assert "unverified, visible to visitors" in r.text


def test_community_profile_no_badge_once_reviewed(env):
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
    assert '<span class="tp-verify">' not in r.text


def test_community_profile_empty_shows_placeholder_to_both_viewers_admin_gets_prompt(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "Bottom line not available." in r.text
    assert "This section hasn't been researched." in r.text
    assert "Generate a draft from the edit page." not in r.text

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert "Bottom line not available. Generate a draft from the edit page." in r.text
    assert "This section hasn't been researched. Generate a draft from the edit page." in r.text


def test_community_profile_sources_render_alongside_pending_content(env):
    """Sources render alongside pending content now — they used to be
    suppressed by the same whole-profile gate that hid the draft itself."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(c, verdict_summary="A drafted verdict.", needs_review=1)
    lib.set_entity_citations("community", c, "community_profile", [
        {"url": "https://source.example/a", "title": "A source", "n": 1},
    ])
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert "A drafted verdict." in r.text
    assert "source.example" in r.text


# -- Community profile: compare matrix, per-cell badges --------------------------

def test_compare_shows_unverified_community_profile_under_review_to_public(env):
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
    assert "Drafted note for Peer CFOs." in r.text
    assert "2020" in r.text
    assert "Confirmed note for Finance Guild." in r.text
    assert "2015" in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_compare_distinguishes_pending_from_truly_empty_community_profile(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, ideal_member="Drafted note for Peer CFOs.", needs_review=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert "Drafted note for Peer CFOs." in r.text
    assert "under review" in r.text
    assert "Not available." in r.text


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
    # verification" flag; .cc-verify is this review-state badge, matching
    # every other surface that renders it).
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
    assert '<span class="tp-verify">' not in r.text


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
    assert '<span class="tp-verify">' not in r.text
