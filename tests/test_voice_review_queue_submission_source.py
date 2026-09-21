"""Regression coverage for the fourth voice_review_queue.source taxonomy
value, 'submission' — see CLAUDE.md's "Voice review queue — a source/
trigger taxonomy" bullet for the full history.

The two public, member-gated submission routes (POST /tools/submit,
POST /tools/communities/submit) were originally left with source=None on
the theory that neither is an admin-edit nor a script/sync context and a
4th taxonomy value was unneeded scope. Reversed on review: source=None
renders as "unknown" on /admin/voice/review-queue, indistinguishable from a
genuine coverage gap, when the provenance here is actually known (a public
submission) and is exactly the content Brian most wants to review closely.
These tests submit content containing a spaced em dash — the mechanical
_voice_fix backstop normalizes it at write time and logs an auto_corrected
voice_review_queue row, which is the only way to observe what `source`
value the write path actually recorded.
"""
import pathlib
import sys
import tempfile
import os

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
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


def test_tool_submission_logs_source_submission(env):
    c = _admin_client(env)   # /tools/submit is member-gated (spam control)
    r = c.post("/tools/submit", data={
        "name": "Test Tool",
        "url": "https://example.com",
        # a spaced em dash triggers normalize_voice_mechanics() at write
        # time, which is what actually inserts a voice_review_queue row
        "description": "Automates AP — end to end.",
        "submitted_by": "user@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        rows = lib.conn.execute(
            "SELECT source, table_name, column_name FROM voice_review_queue "
            "WHERE table_name='tools' AND column_name='description'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source"] == "submission"
    finally:
        lib.close()


def test_community_submission_route_passes_source_submission():
    """POST /tools/communities/submit always calls add_community() with
    demographic="" (the route accepts no free-text fields from the form at
    all — name/url/submitted_by only), so no column add_community actually
    voice-fixes (demographic/cost_note/notes/local_markets) ever receives
    user-supplied text through this route, and a live HTTP round trip can
    never observe a real voice_review_queue row from it. Asserted directly
    against source instead — the one place this wiring can actually be
    checked for this specific route."""
    import inspect
    import webapp.app as appmod
    src = inspect.getsource(appmod.tools_communities_submit)
    assert 'source="submission"' in src


def test_add_community_source_submission_is_logged_when_content_is_corrected(env):
    """Directly exercises Library.add_community()'s own source threading —
    the exact call shape the route above makes, just with real content in
    one of the columns add_community voice-fixes, so a row is actually
    logged to observe."""
    lib = env._lib()
    try:
        lib.add_community(
            name="Test Community", url="https://example.com",
            demographic="", cost_band="Undisclosed dues", categories=[],
            notes="A great community — for finance leaders.",
            submitted_by="user@example.com", approved=0,
            source="submission",
        )
        rows = lib.conn.execute(
            "SELECT source, table_name, column_name FROM voice_review_queue "
            "WHERE table_name='communities' AND column_name='notes'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source"] == "submission"
    finally:
        lib.close()
