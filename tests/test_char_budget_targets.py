"""Character budget: hard limit AND soft target (character-budget-limits-
targets PR, following on from #598/#599's counter/definition work).

A production length read (2026-09-23) found caps below what's already
stored:

    tools.agent_taxonomy_note          cap 1200   longest 3540
    community_profiles.stage_focus     cap  300   longest  440
    tools.summary                      cap  400   longest  453
    tools.description                  cap 2500   longest 2339 (0 over)
    tools.competitive_differentiation  cap  600   longest  546 (0 over)

Every field below now carries two numbers: a soft TARGET (the counter turns
--caution amber past it, but the save still works) and a hard MAX (the save
is refused, nothing is written) — both above their field's real longest
stored value, so nothing already saved becomes unsavable.
"""
import os
import pathlib
import re
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
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


# --- _char_budget itself: the new `target` tier ----------------------------

def test_under_target_renders_plain(env):
    appmod, _db = env
    attrs, counter = appmod._char_budget(1000, "a" * 100, "f", target=500)
    assert 'data-char-target="500"' in attrs
    assert 'class="char-budget"' in counter
    assert "100 characters" in counter
    assert "Aim for" not in counter


def test_over_target_under_limit_is_amber_and_names_the_target(env):
    appmod, _db = env
    attrs, counter = appmod._char_budget(4000, "a" * 2900, "f", target=2500)
    assert 'data-char-target="2500"' in attrs
    assert 'class="char-budget char-budget-warn"' in counter
    assert "2,900 characters. Aim for 2,500." in counter
    assert "refused" not in counter


def test_over_limit_still_wins_over_target_and_is_refused_wording(env):
    appmod, _db = env
    _attrs, counter = appmod._char_budget(4000, "a" * 4200, "f", target=2500)
    assert 'class="char-budget char-budget-over"' in counter
    assert "4,200 characters, 200 over. This save will be refused." in counter
    assert "Aim for" not in counter   # the over-limit message wins, not both


def test_no_target_keeps_old_single_tier_behavior(env):
    """Backward compatible: a field with no target (every #598/#599 call site
    that doesn't pass one) never renders the word "Aim" and never carries
    data-char-target."""
    appmod, _db = env
    attrs, counter = appmod._char_budget(1000, "a" * 900, "f")
    assert "data-char-target" not in attrs
    assert "Aim for" not in counter


def test_warn_color_is_caution_token_never_coral(env):
    appmod, _db = env
    rule = re.search(r"\.char-budget-warn \.char-budget-count\{([^}]*)\}", appmod._CSS)
    assert rule, "no over-target rule for the counter"
    assert "color:var(--caution)" in rule.group(1)
    assert "coral" not in rule.group(1)


def test_budget_js_still_valid_javascript(env, tmp_path):
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    appmod, _db = env
    f = tmp_path / "budget.js"
    f.write_text(appmod._CHAR_BUDGET_JS, encoding="utf-8")
    subprocess.run(["node", "--check", str(f)], check=True)


# --- Library-level hard limits ----------------------------------------------

def test_description_hard_limit_and_headroom_over_longest(env):
    from linklib.db import Library
    assert Library.TOOL_DESCRIPTION_TARGET == 2500
    assert Library.TOOL_DESCRIPTION_MAX == 3500
    assert Library.TOOL_DESCRIPTION_MAX > 2339   # the real longest stored value
    with pytest.raises(ValueError, match=r"3,501 characters; the limit is 3,500"):
        Library._check_text_field_length("Description", "d" * 3501, Library.TOOL_DESCRIPTION_MAX)
    Library._check_text_field_length("Description", "d" * 3500, Library.TOOL_DESCRIPTION_MAX)  # does not raise


def test_summary_hard_limit_and_headroom_over_longest(env):
    from linklib.db import Library
    assert Library.TOOL_SUMMARY_TARGET == 400
    assert Library.TOOL_SUMMARY_MAX == 800
    assert Library.TOOL_SUMMARY_MAX > 453
    with pytest.raises(ValueError):
        Library._check_text_field_length("Short summary", "s" * 801, Library.TOOL_SUMMARY_MAX)


def test_agent_taxonomy_hard_limit_and_headroom_over_longest(env):
    from linklib.db import Library
    assert Library.TOOL_AGENT_TAXONOMY_TARGET == 2500
    assert Library.TOOL_AGENT_TAXONOMY_MAX == 4000
    assert Library.TOOL_AGENT_TAXONOMY_MAX > 3540   # the real longest stored value
    with pytest.raises(ValueError):
        Library._check_text_field_length("Agent taxonomy", "a" * 4001, Library.TOOL_AGENT_TAXONOMY_MAX)


def test_differentiation_hard_limit_and_headroom_over_longest(env):
    from linklib.db import Library
    assert Library.TOOL_DIFFERENTIATION_TARGET == 600
    assert Library.TOOL_DIFFERENTIATION_MAX == 1200
    assert Library.TOOL_DIFFERENTIATION_MAX > 546
    with pytest.raises(ValueError):
        Library._check_text_field_length("Bottom line", "d" * 1201, Library.TOOL_DIFFERENTIATION_MAX)


def test_community_short_field_hard_limit_and_headroom_over_longest(env):
    from linklib.db import Library
    assert Library.COMMUNITY_SHORT_FIELD_TARGET == 300
    assert Library.COMMUNITY_SHORT_FIELD_MAX == 800
    assert Library.COMMUNITY_SHORT_FIELD_MAX > 440   # the real longest stage_focus value
    with pytest.raises(ValueError):
        Library._check_text_field_length("Stage focus", "s" * 801, Library.COMMUNITY_SHORT_FIELD_MAX)


def test_feature_link_public_note_target_added_max_unchanged(env):
    from linklib.db import Library
    assert Library.FEATURE_LINK_PUBLIC_NOTE_MAX == 1000   # unchanged — column is new/empty
    assert Library.FEATURE_LINK_PUBLIC_NOTE_TARGET == 500
    assert Library.text_budget_length("a\r\nb") == 3   # CRLF counts once, matching the counter
    Library._check_feature_link_public_note("x" * 1000)  # does not raise
    with pytest.raises(ValueError):
        Library._check_feature_link_public_note("x" * 1001)


# --- Write-path enforcement, per field --------------------------------------

def _seed_tool(db, **overrides):
    from linklib.db import Library
    lib = Library(db)
    try:
        tool_id = lib.add_tool(
            overrides.get("name", "Rillet"), overrides.get("description", "d"),
            overrides.get("url", "https://rillet.example"), [], approved=1,
            summary=overrides.get("summary", "s"),
        )
        return tool_id
    finally:
        lib.close()


def test_add_tool_refuses_over_limit_description(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    try:
        with pytest.raises(ValueError):
            lib.add_tool("Too Long", "d" * (Library.TOOL_DESCRIPTION_MAX + 1),
                         "https://toolong.example", [], approved=1, summary="s")
    finally:
        lib.close()


def test_update_tool_refuses_over_limit_summary_and_keeps_stored_value(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db, summary="Keep me.")
    lib = Library(db)
    try:
        with pytest.raises(ValueError, match="Nothing was saved"):
            lib.update_tool(tool_id, "Rillet", "d", "https://rillet.example", [],
                            summary="s" * (Library.TOOL_SUMMARY_MAX + 1))
        assert lib.get_tool(tool_id)["summary"] == "Keep me."
    finally:
        lib.close()


def test_update_tool_agent_taxonomy_refuses_over_limit(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    try:
        lib.update_tool_agent_taxonomy(tool_id, "Keep me.")
        with pytest.raises(ValueError):
            lib.update_tool_agent_taxonomy(tool_id, "a" * (Library.TOOL_AGENT_TAXONOMY_MAX + 1))
        assert lib.get_tool(tool_id)["agent_taxonomy_note"] == "Keep me."
    finally:
        lib.close()


def test_update_tool_differentiation_refuses_over_limit(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    try:
        lib.update_tool_differentiation(tool_id, "Keep me.")
        with pytest.raises(ValueError):
            lib.update_tool_differentiation(tool_id, "d" * (Library.TOOL_DIFFERENTIATION_MAX + 1))
        assert lib.get_tool(tool_id)["competitive_differentiation"] == "Keep me."
    finally:
        lib.close()


def test_quick_update_tool_refuses_over_limit(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db, description="Keep me.")
    lib = Library(db)
    try:
        with pytest.raises(ValueError):
            lib.quick_update_tool(tool_id, "d" * (Library.TOOL_DESCRIPTION_MAX + 1), 0, "", "", summary="s")
        assert lib.get_tool(tool_id)["description"] == "Keep me."
    finally:
        lib.close()


def test_upsert_community_profile_refuses_over_limit_stage_focus_jobs_team(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    try:
        cid = lib.add_community("Peer CFOs", "https://peercfos.example", "CFOs", "Free", [], approved=1)
        lib.upsert_community_profile(cid, verdict_summary="Great fit.", stage_focus="Keep me.")
        for field in ("stage_focus", "jobs_program", "team_or_individual"):
            with pytest.raises(ValueError):
                lib.upsert_community_profile(cid, verdict_summary="Great fit.",
                                              **{field: "s" * (Library.COMMUNITY_SHORT_FIELD_MAX + 1)})
        assert lib.get_community_profile(cid)["stage_focus"] == "Keep me."
    finally:
        lib.close()


# --- Routes: refuse the save, keep the stored value, name limit + length ---

def test_tool_edit_route_refuses_over_limit_description(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db, description="Keep me.")
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    over = "d" * (Library.TOOL_DESCRIPTION_MAX + 1)
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": over,
        "summary": "s",
    })
    assert r.status_code == 400
    assert "3,501 characters" in r.text and "3,500" in r.text
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["description"] == "Keep me."
    lib2.close()


def test_tool_edit_route_refuses_over_limit_agent_taxonomy(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    lib.update_tool_agent_taxonomy(tool_id, "Keep me.")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    over = "a" * (Library.TOOL_AGENT_TAXONOMY_MAX + 1)
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": "s",
        "agent_taxonomy_note": over,
    })
    assert r.status_code == 400
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["agent_taxonomy_note"] == "Keep me."
    lib2.close()


def test_community_profile_route_refuses_over_limit_stage_focus(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cid = lib.add_community("Peer CFOs", "https://peercfos.example", "CFOs", "Free", [], approved=1)
    lib.upsert_community_profile(cid, verdict_summary="Great fit.", stage_focus="Keep me.")
    lib.close()
    over = "s" * (Library.COMMUNITY_SHORT_FIELD_MAX + 1)
    r = _client(appmod).post(f"/admin/tools/communities/{cid}/profile", data={
        "verdict_summary": "Great fit.", "stage_focus": over,
    })
    assert r.status_code == 400
    lib2 = Library(db)
    assert lib2.get_community_profile(cid)["stage_focus"] == "Keep me."
    lib2.close()


# --- Round-trip: a value longer than today's real-world longest saves fine -

def test_longest_stored_agent_taxonomy_note_still_round_trips(env):
    """The real production longest (3,540 chars) must not become unsavable."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    long_value = ("This tool has a real named agent roster. " * 80).strip()   # ~3,440 chars
    assert Library.text_budget_length(long_value) < Library.TOOL_AGENT_TAXONOMY_MAX
    assert Library.text_budget_length(long_value) > Library.TOOL_AGENT_TAXONOMY_TARGET
    lib = Library(db)
    lib.update_tool_agent_taxonomy(tool_id, long_value)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": "s",
        "agent_taxonomy_note": long_value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["agent_taxonomy_note"] == long_value
    lib2.close()


def test_over_target_under_limit_save_succeeds_and_counter_would_warn(env):
    """A value between target and limit is a normal, successful save — only
    the live counter's color changes, never the server's willingness to
    write it."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = "d" * 2900   # over the 2,500 target, comfortably under the 3,500 limit
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": value, "summary": "s",
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["description"] == value
    lib2.close()
    attrs, counter = appmod._char_budget(Library.TOOL_DESCRIPTION_MAX, value, "x",
                                          target=Library.TOOL_DESCRIPTION_TARGET)
    assert "char-budget-warn" in counter


# --- No maxlength remains on the touched fields; counter renders -----------

def test_no_maxlength_on_tool_edit_fields(env):
    appmod, db = env
    tool_id = _seed_tool(db)
    from linklib.db import Library
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    page = _client(appmod).get(f"/tools/software/{slug}/edit").text
    for name in ("description", "summary", "agent_taxonomy_note", "competitive_differentiation"):
        m = re.search(rf'<textarea[^>]*\bname="{name}"[^>]*>', page)
        assert m, f"{name} textarea not found"
        assert "maxlength" not in m.group(0), f"{name} still carries a maxlength"
        assert "data-char-limit=" in m.group(0), f"{name} has no character budget"
    assert "<script>(function(){" in page


def test_no_maxlength_on_tool_add_fields(env):
    appmod, db = env
    page = _client(appmod).get("/admin/tools/software/new").text
    for name in ("description", "summary"):
        m = re.search(rf'<textarea[^>]*\bname="{name}"[^>]*>', page)
        assert m, f"{name} textarea not found"
        assert "maxlength" not in m.group(0)
        assert "data-char-limit=" in m.group(0)


def test_no_maxlength_on_community_short_fields(env):
    """stage_focus/jobs_program/team_or_individual moved from a single-line
    <input> to a full-width <textarea> in the community quick-facts width
    fix (2026-09, see test_community_profile_layout.py) — same character-
    budget mechanism (data-char-limit, no maxlength), just a different tag,
    since a 233px-wide input truncated a real saved value and wrapped its
    own counter text to two lines."""
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cid = lib.add_community("Peer CFOs", "https://peercfos.example", "CFOs", "Free", [], approved=1)
    lib.close()
    page = _client(appmod).get(f"/admin/tools/communities/{cid}/profile").text
    for name in ("stage_focus", "jobs_program", "team_or_individual"):
        m = re.search(rf'<textarea[^>]*\bname="{name}"[^>]*>', page)
        assert m, f"{name} textarea not found"
        assert "maxlength" not in m.group(0)
        assert "data-char-limit=" in m.group(0)
    # The other six Quick facts fields deliberately keep their unenforced
    # maxlength="300" — confirmed against production (see
    # test_other_quick_facts_fields_stay_well_under_their_unenforced_cap
    # below), not just assumed, and none of them is close.
    for name in ("primary_purpose", "cpe_eligible", "platform_type",
                  "meeting_format", "event_style", "seniority_band"):
        m = re.search(rf'<input[^>]*\bname="{name}"[^>]*>', page)
        assert m, f"{name} input not found"
        assert 'maxlength="300"' in m.group(0)
        assert "data-char-limit" not in m.group(0)


# --- The counter always comes from the shared helper -----------------------

def test_tool_edit_counters_use_shared_char_budget_ids(env):
    appmod, db = env
    tool_id = _seed_tool(db)
    from linklib.db import Library
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    page = _client(appmod).get(f"/tools/software/{slug}/edit").text
    for field_id in ("tool-desc", "tool-summary", "tool-taxonomy", "tool-differentiation"):
        assert f'id="{field_id}-budget" class="char-budget' in page


def test_public_note_counter_present_and_target_wired(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cat = lib.add_tool_category("Close Management")
    fid = lib.add_category_feature(cat, "Continuous reconciliation", "def")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.example", ["Close Management"],
                           approved=1, summary="s")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    page = _client(appmod).get(f"/tools/software/{slug}/edit").text
    m = re.search(rf'<textarea name="feature_{fid}_public_note"([^>]*)>', page)
    assert m
    assert "maxlength" not in m.group(1)
    assert 'data-char-limit="1000"' in m.group(1)
    assert 'data-char-target="500"' in m.group(1)
    assert f'id="pub-note-{fid}-budget"' in page


# --- tools.summary conditional-maxlength guard is retired -------------------

def test_summary_conditional_guard_gone_even_for_a_legacy_over_length_value(env):
    """A tool whose summary is already over the OLD 400 cap (the exact
    scenario _summary_maxlength_attr existed to patch around) must render
    the textarea with no maxlength at all, budgeted the same as any other
    row — not the old conditional attr that only ever protected fields
    already under the cap."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db, summary="s" * 453)   # the real longest stored value
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    page = _client(appmod).get(f"/tools/software/{slug}/edit").text
    m = re.search(r'<textarea id="tool-summary"[^>]*>', page)
    assert m
    assert "maxlength" not in m.group(0)
    assert 'data-char-limit="800"' in m.group(0)
    assert "_summary_maxlength_attr" not in dir(appmod)


# --- Per-field 3-state coverage completion (PR 600 review, item 4) ---------
#
# The brief asked for three tests per touched field: (1) a value over the
# field's OLD cap round-trips unchanged through the real write path, (2) a
# value over the NEW max is refused with the limit+length named, and (3) a
# value between target and max saves successfully, amber. (2) was already
# covered for every field above. This section closes the two real gaps the
# review found: description/competitive_differentiation had no "old cap"
# scenario to round-trip (0 rows were ever over their notional old caps —
# there was no enforced cap before this PR — so the closest honest analog
# is the real production longest value round-tripping unchanged), and
# summary/agent_taxonomy_note/differentiation/stage_focus had no
# field-specific over-target-under-max round-trip through the actual route
# (only description did, and only agent_taxonomy_note had a genuine
# route-level legacy round-trip).

def test_description_longest_stored_value_round_trips(env):
    """The real production longest (2,339 chars) must still save cleanly —
    description had 0 rows over its notional old cap (there was no
    enforced cap before this PR), so this is the closest honest analog to
    a legacy round-trip for this field."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = ("Rillet automates the accounting close for finance teams. " * 50)[:2339]
    assert len(value) == 2339
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": value, "summary": "s",
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["description"] == value
    lib2.close()


def test_differentiation_longest_stored_value_round_trips(env):
    """The real production longest (546 chars) — differentiation had 0 rows
    over its notional old cap, same reasoning as description above."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = ("Unlike generic BI tools, Rillet is purpose-built for the close. " * 10).strip()[:546]
    assert len(value) == 546
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": "s",
        "competitive_differentiation": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["competitive_differentiation"] == value
    lib2.close()


def test_summary_legacy_over_old_cap_value_round_trips_via_route(env):
    """Companion to test_summary_conditional_guard_gone_even_for_a_legacy_
    over_length_value (which only checked the GET-rendered page): the real
    production longest (453 chars, over the OLD 400 cap) must also
    round-trip through an actual POST to the edit route, unchanged."""
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = "s" * 453
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["summary"] == value
    lib2.close()


def test_stage_focus_legacy_value_round_trips_via_route(env):
    """The real production longest stage_focus (440 chars, over the OLD 300
    cap) must round-trip through the community profile edit route,
    unchanged — the community-side analog of the agent_taxonomy_note
    round-trip test above."""
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cid = lib.add_community("Peer CFOs", "https://peercfos.example", "CFOs", "Free", [], approved=1)
    lib.close()
    value = ("Series B through pre-IPO growth-stage finance leaders, with a "
              "smaller cohort of later-stage public-company CFOs who join "
              "mainly for the peer network rather than the curriculum. " * 3).strip()[:440]
    assert len(value) == 440
    r = _client(appmod).post(f"/admin/tools/communities/{cid}/profile", data={
        "verdict_summary": "Great fit.", "stage_focus": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_community_profile(cid)["stage_focus"] == value
    lib2.close()


def test_over_target_under_limit_save_succeeds_summary(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = "s" * 600   # over the 400 target, under the 800 limit
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["summary"] == value
    lib2.close()
    _attrs, counter = appmod._char_budget(Library.TOOL_SUMMARY_MAX, value, "x",
                                           target=Library.TOOL_SUMMARY_TARGET)
    assert "char-budget-warn" in counter


def test_over_target_under_limit_save_succeeds_agent_taxonomy(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = "a" * 3000   # over the 2,500 target, under the 4,000 limit
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": "s",
        "agent_taxonomy_note": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["agent_taxonomy_note"] == value
    lib2.close()
    _attrs, counter = appmod._char_budget(Library.TOOL_AGENT_TAXONOMY_MAX, value, "x",
                                           target=Library.TOOL_AGENT_TAXONOMY_TARGET)
    assert "char-budget-warn" in counter


def test_over_target_under_limit_save_succeeds_differentiation(env):
    appmod, db = env
    from linklib.db import Library
    tool_id = _seed_tool(db)
    lib = Library(db)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()
    value = "d" * 900   # over the 600 target, under the 1,200 limit
    r = _client(appmod).post(f"/tools/software/{slug}/edit", data={
        "name": "Rillet", "url": "https://rillet.example", "description": "d", "summary": "s",
        "competitive_differentiation": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_tool(tool_id)["competitive_differentiation"] == value
    lib2.close()
    _attrs, counter = appmod._char_budget(Library.TOOL_DIFFERENTIATION_MAX, value, "x",
                                           target=Library.TOOL_DIFFERENTIATION_TARGET)
    assert "char-budget-warn" in counter


def test_over_target_under_limit_save_succeeds_stage_focus(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cid = lib.add_community("Peer CFOs", "https://peercfos.example", "CFOs", "Free", [], approved=1)
    lib.close()
    value = "s" * 500   # over the 300 target, under the 800 limit
    r = _client(appmod).post(f"/admin/tools/communities/{cid}/profile", data={
        "verdict_summary": "Great fit.", "stage_focus": value,
    }, follow_redirects=False)
    assert r.status_code == 303
    lib2 = Library(db)
    assert lib2.get_community_profile(cid)["stage_focus"] == value
    lib2.close()
    _attrs, counter = appmod._char_budget(Library.COMMUNITY_SHORT_FIELD_MAX, value, "x",
                                           target=Library.COMMUNITY_SHORT_FIELD_TARGET)
    assert "char-budget-warn" in counter


# --- Quick facts fields left uncapped: checked against production, not just
# assumed (PR 600 review, item 1) ------------------------------------------
#
# The other six community_profiles Quick facts fields (primary_purpose,
# cpe_eligible, platform_type, meeting_format, event_style, seniority_band)
# kept their unenforced maxlength="300" with no budget added. At the time
# this PR shipped that was stated as "nothing suggested a comparable
# overflow risk" — true, but not actually checked against production data.
# It has since been checked directly (all 40 live community_profiles rows,
# via the /mcp introspection tools): the real longest value across all six
# fields is seniority_band at 128 characters — well under half the 300-char
# cap, with every other field's longest well below that. Pinned here as a
# static ceiling so a future regeneration pass that starts pushing these
# fields longer gets caught by a failing test rather than a silent surprise.

def test_other_quick_facts_fields_stay_well_under_their_unenforced_cap():
    """Not a live DB check (this suite runs against a fresh temp DB, not
    production) — a static ceiling recording what a direct production
    check found, so a future regression here fails loudly instead of
    silently reopening the same gap community_profiles.stage_focus had."""
    observed_production_longest = {
        "primary_purpose": 79,
        "cpe_eligible": 91,
        "platform_type": 90,
        "meeting_format": 116,
        "event_style": 100,
        "seniority_band": 128,
    }
    for field, longest in observed_production_longest.items():
        assert longest < 300, f"{field}'s real longest ({longest}) is approaching its unenforced 300 cap"
