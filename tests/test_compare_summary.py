"""Compare Redesign Phase 2 — the AI overlap/contrast summary block on
/tools/software/compare and /tools/communities/compare: caching, the daily
dollar cap, cost logging, and the feedback submission + admin review flow.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # The generate-summary path refuses (require_voice_setting) unless
    # voice_core is seeded — the startup hook that normally does this only
    # fires under a real ASGI lifespan (a context-managed TestClient), not
    # a bare `TestClient(app)`, so every test file that exercises a
    # generation route seeds it explicitly here (same convention
    # test_model_selection.py's own `env` fixture established).
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


def _two_tools(db_path: str) -> tuple[int, int]:
    lib = Library(db_path)
    a = lib.add_tool("RightRev", "RightRev automates ASC 606 revenue recognition.",
                      "https://rightrev.com", ["Revenue Recognition"], approved=1)
    b = lib.add_tool("NetSuite", "NetSuite is a full ERP suite.",
                      "https://netsuite.com", ["ERP", "Revenue Recognition"], approved=1)
    lib.close()
    return a, b


def _fake_draft(text="RightRev specializes in revenue recognition; NetSuite bundles it inside a broader ERP suite."):
    from linklib.enrich import CompareSummaryDraft
    return CompareSummaryDraft(summary=text, model="claude-sonnet-5",
                                input_tokens=50, output_tokens=15, cost_usd=0.01)


# -- Library-layer: cache round trip -----------------------------------------

def test_compare_summary_cache_round_trips(env):
    lib = Library(os.environ["LINKLIB_DB"])
    h = lib.compare_summary_content_hash("hello world")
    assert lib.get_compare_summary("tool", "1,2", h) is None
    lib.set_compare_summary("tool", "1,2", h, "A summary.", "claude-sonnet-5", 10, 5, 0.02)
    cached = lib.get_compare_summary("tool", "1,2", h)
    assert cached["summary"] == "A summary."
    assert cached["model"] == "claude-sonnet-5"
    assert cached["cost_usd"] == 0.02
    lib.close()


def test_compare_summary_cache_upserts_on_same_key(env):
    lib = Library(os.environ["LINKLIB_DB"])
    h = lib.compare_summary_content_hash("hello world")
    lib.set_compare_summary("tool", "1,2", h, "First.", "claude-sonnet-5", 1, 1, 0.01)
    lib.set_compare_summary("tool", "1,2", h, "Second.", "claude-sonnet-5", 1, 1, 0.01)
    rows = lib.conn.execute("SELECT COUNT(*) FROM compare_summary_cache").fetchone()[0]
    assert rows == 1
    assert lib.get_compare_summary("tool", "1,2", h)["summary"] == "Second."
    lib.close()


def test_compare_summary_content_hash_changes_with_content(env):
    lib = Library(os.environ["LINKLIB_DB"])
    h1 = lib.compare_summary_content_hash("Description: RightRev does A.")
    h2 = lib.compare_summary_content_hash("Description: RightRev does B.")
    assert h1 != h2
    lib.close()


def test_compare_summary_cache_applies_em_dash_backstop(env):
    """set_compare_summary runs the same mechanical voice backstop every
    other prose-capable Library write applies before persisting."""
    lib = Library(os.environ["LINKLIB_DB"])
    h = lib.compare_summary_content_hash("x")
    lib.set_compare_summary("tool", "1,2", h, "RightRev is narrow — NetSuite is broad.", "m")
    cached = lib.get_compare_summary("tool", "1,2", h)
    assert "— " not in cached["summary"] and " —" not in cached["summary"]
    assert "—" in cached["summary"]  # unspaced em dash preserved
    lib.close()


def test_compare_summary_default_cap_round_trips(env):
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_default_compare_summary_cap() == Library._DEFAULT_COMPARE_SUMMARY_CAP_USD
    lib.set_default_compare_summary_cap(5.0)
    assert lib.get_default_compare_summary_cap() == 5.0
    lib.close()


def test_compare_summary_cost_today_sums_only_todays_rows(env):
    lib = Library(os.environ["LINKLIB_DB"])
    h1 = lib.compare_summary_content_hash("a")
    h2 = lib.compare_summary_content_hash("b")
    lib.set_compare_summary("tool", "1,2", h1, "S1", "m", cost_usd=0.30)
    lib.set_compare_summary("tool", "3,4", h2, "S2", "m", cost_usd=0.20)
    assert lib.compare_summary_cost_today() == pytest.approx(0.50)
    # A row from yesterday shouldn't count.
    lib.conn.execute("UPDATE compare_summary_cache SET created_at=? WHERE entity_ids='3,4'",
                      ("2020-01-01T00:00:00",))
    lib.conn.commit()
    assert lib.compare_summary_cost_today() == pytest.approx(0.30)
    lib.close()


# -- webapp route: generation, caching, cap enforcement ----------------------

def test_summary_block_renders_on_cache_miss_and_persists(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    calls = []

    def _fake_generate(entity_type, entities, model=None, voice_core=""):
        calls.append((entity_type, len(entities)))
        return _fake_draft()

    monkeypatch.setattr("linklib.enrich.generate_compare_summary", _fake_generate)
    client = _client(env)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "specializes in revenue recognition" in r.text
    assert "Flag an issue" in r.text
    assert len(calls) == 1

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.enrichment_cost_total() > 0  # cost was logged
    row = lib.conn.execute("SELECT COUNT(*) FROM compare_summary_cache").fetchone()[0]
    assert row == 1
    lib.close()


def test_summary_block_uses_cache_on_second_view_no_regen(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    calls = []

    def _fake_generate(entity_type, entities, model=None, voice_core=""):
        calls.append(1)
        return _fake_draft()

    monkeypatch.setattr("linklib.enrich.generate_compare_summary", _fake_generate)
    client = _client(env)
    r1 = client.get(f"/tools/software/compare?ids={a},{b}")
    r2 = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(calls) == 1  # second view was a cache hit


def test_summary_block_regenerates_when_content_edited(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    calls = []

    def _fake_generate(entity_type, entities, model=None, voice_core=""):
        calls.append(1)
        return _fake_draft(f"Summary #{len(calls)}.")

    monkeypatch.setattr("linklib.enrich.generate_compare_summary", _fake_generate)
    client = _client(env)
    client.get(f"/tools/software/compare?ids={a},{b}")
    assert len(calls) == 1

    lib = Library(os.environ["LINKLIB_DB"])
    lib.update_tool(a, name="RightRev", description="RightRev now does something completely different.",
                     url="https://rightrev.com", categories=["Revenue Recognition"], summary="")
    lib.close()

    client.get(f"/tools/software/compare?ids={a},{b}")
    assert len(calls) == 2  # content changed -> hash changed -> cache miss


def test_summary_block_omitted_when_generation_fails(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    monkeypatch.setattr("linklib.enrich.generate_compare_summary", lambda *a, **k: None)
    client = _client(env)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert '<div class="cmp-summary">' not in r.text
    assert "Flag an issue" not in r.text


def test_summary_block_never_fails_the_page_on_exception(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("linklib.enrich.generate_compare_summary", _boom)
    client = _client(env)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200  # the page itself must still render
    assert "Compare software" in r.text


def test_cap_hit_omits_generation_and_shows_labeled_note(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_compare_summary_cap(0.01)
    # Pre-spend past the cap via an unrelated cached entry, so the *next*
    # lookup (a genuine cache miss for this pair) sees the cap already hit.
    h = lib.compare_summary_content_hash("unrelated")
    lib.set_compare_summary("tool", "999,998", h, "Other.", "m", cost_usd=1.00)
    lib.close()

    calls = []
    monkeypatch.setattr("linklib.enrich.generate_compare_summary",
                         lambda *a, **k: calls.append(1) or _fake_draft())
    client = _client(env)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "temporarily unavailable" in r.text
    assert "daily budget reached" in r.text
    assert len(calls) == 0  # generation was never attempted


def test_missing_voice_core_omits_summary_without_failing_page(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_setting("voice_core", "")
    lib.close()
    calls = []
    monkeypatch.setattr("linklib.enrich.generate_compare_summary",
                         lambda *a, **k: calls.append(1) or _fake_draft())
    client = _client(env)
    r = client.get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert len(calls) == 0
    assert "Flag an issue" not in r.text


def test_has_unverified_reflects_live_state_not_cached_generation_time(env, monkeypatch):
    """Per Brian's approval: has_unverified is decoupled from the
    content-hash cache key, computed live at render time — a verify-only
    action (no text edit) must not force a regen, but the footnote must
    still reflect today's real review state."""
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_tool_needs_review(a, 1)
    lib.update_tool(a, name="RightRev", description="RightRev automates ASC 606 revenue recognition.",
                     url="https://rightrev.com", categories=["Revenue Recognition"],
                     summary="", description_needs_verification=1)
    lib.close()

    calls = []
    monkeypatch.setattr("linklib.enrich.generate_compare_summary",
                         lambda *a, **k: calls.append(1) or _fake_draft())
    client = _client(env)
    r1 = client.get(f"/tools/software/compare?ids={a},{b}")
    assert "Includes catalog content still under review" in r1.text
    assert len(calls) == 1

    # Mark the field verified — no text change, so the content hash (and
    # therefore the cache entry) is unchanged.
    lib = Library(os.environ["LINKLIB_DB"])
    lib.update_tool(a, name="RightRev", description="RightRev automates ASC 606 revenue recognition.",
                     url="https://rightrev.com", categories=["Revenue Recognition"],
                     summary="", description_needs_verification=0)
    lib.close()

    r2 = client.get(f"/tools/software/compare?ids={a},{b}")
    assert len(calls) == 1  # still a cache hit — no wasteful regen
    assert "Includes catalog content still under review" not in r2.text  # but disclosure updated live


# -- feedback submission + admin review list ---------------------------------

def test_feedback_form_requires_valid_cached_reference(env):
    client = _client(env)
    r = client.get("/compare-summary/feedback?type=tool&ids=1,2&hash=deadbeef")
    assert r.status_code == 404

    r2 = client.get("/compare-summary/feedback")
    assert r2.status_code == 400


def test_feedback_submit_and_admin_review_flow(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    monkeypatch.setattr("linklib.enrich.generate_compare_summary", lambda *a, **k: _fake_draft())
    client = _client(env)
    client.get(f"/tools/software/compare?ids={a},{b}")  # populates the cache

    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.conn.execute("SELECT entity_ids, content_hash FROM compare_summary_cache").fetchone()
    entity_ids, content_hash = row["entity_ids"], row["content_hash"]
    lib.close()

    form_page = client.get(f"/compare-summary/feedback?type=tool&ids={entity_ids}&hash={content_hash}")
    assert form_page.status_code == 200
    assert "specializes in revenue recognition" in form_page.text

    submit = client.post("/compare-summary/feedback", data={
        "type": "tool", "ids": entity_ids, "hash": content_hash,
        "note": "This mischaracterizes RightRev's breadth.",
    }, follow_redirects=False)
    assert submit.status_code == 200
    assert "flagged for review" in submit.text.lower()

    # Anonymous users can't see the admin review list.
    r_unauth = client.get("/admin/compare-summary-feedback", follow_redirects=False)
    assert r_unauth.status_code in (302, 303)

    _login(client)
    r_admin = client.get("/admin/compare-summary-feedback")
    assert r_admin.status_code == 200
    assert "This mischaracterizes RightRev" in r_admin.text
    assert "Mark reviewed" in r_admin.text

    lib = Library(os.environ["LINKLIB_DB"])
    feedback_rows = lib.list_compare_summary_feedback()
    assert len(feedback_rows) == 1
    assert feedback_rows[0]["reviewed_at"] == ""
    feedback_id = feedback_rows[0]["id"]
    assert lib.count_compare_summary_feedback(reviewed=False) == 1
    lib.close()

    mark = client.post(f"/admin/compare-summary-feedback/{feedback_id}/mark-reviewed", follow_redirects=False)
    assert mark.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.count_compare_summary_feedback(reviewed=False) == 0
    assert lib.count_compare_summary_feedback(reviewed=True) == 1
    lib.close()

    r_admin2 = client.get("/admin/compare-summary-feedback")
    assert "Reviewed" in r_admin2.text


def test_feedback_route_rejects_bad_entity_type(env):
    client = _client(env)
    r = client.post("/compare-summary/feedback", data={"type": "bogus", "ids": "1,2", "hash": "x"})
    assert r.status_code == 400


def test_admin_compare_summary_feedback_badges_open_task_count(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    monkeypatch.setattr("linklib.enrich.generate_compare_summary", lambda *a, **k: _fake_draft())
    client = _client(env)
    client.get(f"/tools/software/compare?ids={a},{b}")
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.conn.execute("SELECT entity_ids, content_hash FROM compare_summary_cache").fetchone()
    lib.add_compare_summary_feedback("tool", row["entity_ids"], row["content_hash"], "S", "note")
    lib.close()

    from webapp import tasks as _tasks
    lib = Library(os.environ["LINKLIB_DB"])
    counts = _tasks.open_task_counts(lib)
    lib.close()
    assert counts.get("/admin/compare-summary-feedback") == 1


def test_summary_card_has_how_they_compare_heading(env, monkeypatch):
    """The AI summary card opens with an h2, "How they compare", before its
    text; the footnote is unchanged and the capped note carries no heading."""
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    monkeypatch.setattr("linklib.enrich.generate_compare_summary",
                         lambda *x, **k: _fake_draft())
    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    html = r.text
    assert html.count('<h2 class="cmp-summary-h">How they compare</h2>') == 1
    assert html.index("cmp-summary-h") < html.index("specializes in revenue recognition")
    assert "Flag an issue" in html


def test_capped_summary_note_has_no_heading(env, monkeypatch):
    a, b = _two_tools(os.environ["LINKLIB_DB"])
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_compare_summary_cap(0.01)
    lib.set_compare_summary("tool", "999,998", lib.compare_summary_content_hash("u"), "O.", "m", cost_usd=1.00)
    lib.close()
    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "cmp-summary-capped" in r.text
    assert '<h2 class="cmp-summary-h">' not in r.text


def test_bottom_line_row_is_white_with_navy_label_rule(env):
    """Option A: the Bottom line row is a plain white row, set apart by a navy
    rule on its label, not a seafoam or navy-light tint. One shared rule
    serves both Compare pages."""
    from webapp import app as appmod
    import re
    css = re.sub(r"/\*.*?\*/", "", appmod._CMP_SHARED_CSS, flags=re.S)
    bl = re.findall(r"[^}]*cc-bl\{[^}]*\}", css)
    assert len(bl) == 2, bl
    joined = " ".join(bl)
    assert "seafoam-wash" not in joined
    assert "navy-light" not in joined
    assert "background:var(--surface)!important" in joined
    assert "box-shadow:inset 3px 0 0 var(--navy)" in joined
