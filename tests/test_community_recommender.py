"""Communities Recommender (/tools/communities/find, Phase 7): a short quiz
that filters the directory by role/budget/access/focus, reusing the same
filter semantics as the directory's own client-side filtering, and logs
every completed quiz into community_gap_submissions with
submission_type='recommender' (a zero/thin result is the same kind of gap
signal as a zero-result directory search).
"""
import pathlib
import sys
import tempfile, os

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


def _seed(lib):
    cfo = lib.add_community("CFO Guild", "https://example.com", "CFOs at high-growth companies",
                             "Free", ["CFO-specific invite-only"], access="Invite-only", approved=1)
    controller = lib.add_community("Controller Circle", "https://example.com", "Controllers",
                                    "<$1k/yr", ["Controller/accounting"], access="Open", approved=1)
    return cfo, controller


def test_quiz_form_renders_all_four_questions(env):
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "What best describes your role?" in r.text
    assert "What's your budget for dues?" in r.text
    assert "What kind of access are you looking for?" in r.text
    assert "Anything more specific you&#x27;re looking for?" in r.text or \
        "Anything more specific you're looking for?" in r.text


def test_quiz_submission_filters_to_matching_community(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cfo, controller = _seed(lib)
    lib.close()

    c = _client(env)
    r = c.post("/tools/communities/find", data={
        "role": "cfo", "budget": "any", "access": "invite", "focus": "none",
    }, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/tools/communities/find/results?")

    results = c.get(location)
    assert results.status_code == 200
    assert "CFO Guild" in results.text
    assert "Controller Circle" not in results.text


def test_quiz_submission_logs_recommender_row(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    _seed(lib)
    lib.close()

    c = _client(env)
    c.post("/tools/communities/find", data={
        "role": "controller", "budget": "under1k", "access": "open", "focus": "none",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    rows = lib.list_community_gap_submissions()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["submission_type"] == "recommender"
    import json
    ctx = json.loads(rows[0]["search_context_json"])
    assert ctx["quiz"] is True
    assert ctx["result_count"] == 1
    assert ctx["role"] == "Controller or accounting team leader"


def test_quiz_zero_results_shows_gap_cta_and_is_logged(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    _seed(lib)
    lib.close()

    c = _client(env)
    # A CFO-only community exists, but demanding "Open" access rules it out —
    # no community satisfies role=cfo AND access=open in the seed data.
    r = c.post("/tools/communities/find", data={
        "role": "cfo", "budget": "any", "access": "open", "focus": "none",
    }, follow_redirects=False)
    results = c.get(r.headers["location"])
    assert results.status_code == 200
    assert "Nothing in the directory matched" in results.text
    assert "/tools/communities/gap" in results.text

    lib = Library(os.environ["LINKLIB_DB"])
    rows = lib.list_community_gap_submissions()
    lib.close()
    assert len(rows) == 1
    import json
    ctx = json.loads(rows[0]["search_context_json"])
    assert ctx["result_count"] == 0


def test_quiz_results_page_is_idempotent_get_no_double_logging(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    _seed(lib)
    lib.close()

    c = _client(env)
    r = c.post("/tools/communities/find", data={
        "role": "cfo", "budget": "any", "access": "any", "focus": "none",
    }, follow_redirects=False)
    location = r.headers["location"]
    c.get(location)
    c.get(location)
    c.get(location)

    lib = Library(os.environ["LINKLIB_DB"])
    rows = lib.list_community_gap_submissions()
    lib.close()
    assert len(rows) == 1


def test_quiz_weight_step_renders_function_dimension_and_cpe_rename(env):
    """Recommender weighting redesign PR 1: Function is a new dimension
    alongside the narrowed Level vocabulary, and CPE's admin/quiz label is
    "CPE eligible events" rather than the old bare "CPE"."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Function" in r.text
    assert "Overall finance org" in r.text
    assert "Senior Exec (VP+)" in r.text
    assert "CPE eligible events" in r.text


def test_quiz_weight_step_renders_merged_looking_for_dimension(env):
    """Recommender weighting redesign PR 2: primary_purpose and
    resources_included retire in favor of one merged multi-select, "What
    you're looking for", with Vendor connections as a wholly new option."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "What you&#x27;re looking for" in r.text or "What you're looking for" in r.text
    assert "Peer discussions" in r.text
    assert "Vendor connections" in r.text
    assert "Resources &amp; templates" in r.text or "Resources & templates" in r.text
    assert "Resources included" not in r.text


def test_quiz_weight_step_renders_new_platform_vocabulary(env):
    """Recommender weighting redesign PR 3: Platform narrows from
    Slack/chat-based, In-person only, Mix to naming the actual platform
    (Slack, Circle, Email, LinkedIn, Proprietary). LinkedIn was added after
    the initial pass specifically for Modern Finance Forum for CFOs, whose
    actual platform is a LinkedIn group."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Slack" in r.text
    assert "Circle" in r.text
    assert "LinkedIn" in r.text
    assert "Proprietary" in r.text
    assert "Slack / chat-based" not in r.text
    assert "In-person only" not in r.text


def test_quiz_weight_step_renders_merged_programming_dimension(env):
    """Recommender weighting redesign PR 4: meeting_format and event_style
    retire in favor of one merged multi-select reusing the "Programming"
    label. Demo Days is deliberately left out (no supporting research
    text), so it should never appear."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Meals (dinners, etc.)" in r.text
    assert "Conferences" in r.text
    assert "Retreats" in r.text
    assert "Virtual Panels" in r.text
    assert "Demo Days" not in r.text
    assert "Event style" not in r.text


def test_quiz_weight_step_renders_dues_as_profile_sourced(env):
    """Recommender weighting redesign PR 5: Dues (paid_free) moves from a
    derived-from-cost_band single value to a profile-sourced, independently
    dual-taggable dimension, so it now also gets a checkbox group on the
    admin profile edit form (verified in test_community_profiles.py's
    admin-save regression test) in addition to rendering on the quiz."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Dues" in r.text or "Cost" in r.text


def test_quiz_weight_step_renders_new_industry_dimension(env):
    """Recommender weighting redesign PR 6: Industry is a wholly new
    dimension (Life sciences / Healthcare / Private equity/funds /
    Industry-neutral) with no free-text sibling column."""
    c = _client(env)
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Industry" in r.text
    assert "Life sciences" in r.text
    assert "Private equity/funds" in r.text
    assert "Industry-neutral" in r.text


def test_quiz_works_with_answers_missing_or_set_to_no_preference(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    _seed(lib)
    lib.close()

    c = _client(env)
    # role/access omitted entirely; budget/focus explicitly "no preference" —
    # none of these contribute a filter, so both seeded communities match.
    r = c.post("/tools/communities/find", data={"budget": "any", "focus": "none"},
                follow_redirects=False)
    assert r.status_code == 303
    results = c.get(r.headers["location"])
    assert results.status_code == 200
    assert "CFO Guild" in results.text
    assert "Controller Circle" in results.text
