"""Task badge system (admin coral notifications): the DB-layer counts each
badge is built from, and webapp.tasks aggregating them into one dict keyed by
admin href.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library, Article


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_count_pending_tools(lib):
    assert lib.count_pending_tools() == 0
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    lib.add_tool("B", "desc", "https://b.example", [], approved=1)
    assert lib.count_pending_tools() == 1


def test_count_pending_communities(lib):
    assert lib.count_pending_communities() == 0
    lib.add_community(name="A", url="https://a.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=0)
    lib.add_community(name="B", url="https://b.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    assert lib.count_pending_communities() == 1


def test_count_contacts_since(lib):
    assert lib.count_contacts_since("") == 0
    lib.save_contact("Jane", "jane@x.com", "hi")
    assert lib.count_contacts_since("") == 1
    now = lib.list_contacts()[0]["created_at"]
    assert lib.count_contacts_since(now) == 0   # nothing strictly after "now"


def test_count_tool_leads_since(lib):
    tool_id = lib.add_tool("A", "desc", "https://a.example", [], approved=1)
    assert lib.count_tool_leads_since("") == 0
    lib.save_tool_lead(tool_id, "A", "Jane", "jane@x.com", "Acme", "50-200")
    assert lib.count_tool_leads_since("") == 1


def test_flagged_and_queue_counts_already_exist(lib):
    # These back the Archive badge — pinning they're the right shape for tasks.py.
    assert lib.flagged_count() == 0
    assert lib.queue_count(status="pending") == 0
    art = Article(url="https://x.example/1", title="t", source="s")
    aid = lib.upsert(art)
    lib.apply_enrichment(aid, "summary", ["tag"], "model", "rules", in_scope=False, scope_reason="off-audience")
    assert lib.flagged_count() == 1


def test_password_reset_request_lifecycle(lib):
    uid = lib.create_user("jane", "supersecret")
    assert lib.count_pending_password_resets() == 0
    rid = lib.create_password_reset_request(uid, "jane")
    assert lib.count_pending_password_resets() == 1
    pending = lib.list_password_reset_requests(pending_only=True)
    assert len(pending) == 1 and pending[0]["id"] == rid

    lib.resolve_password_resets_for_user(uid)
    assert lib.count_pending_password_resets() == 0


def test_dismiss_password_reset(lib):
    uid = lib.create_user("jane", "supersecret")
    rid = lib.create_password_reset_request(uid, "jane")
    lib.dismiss_password_reset(rid)
    assert lib.count_pending_password_resets() == 0


# --- webapp.tasks aggregation -----------------------------------------------

def test_open_task_counts_empty_by_default(lib):
    from webapp import tasks
    counts = tasks.open_task_counts(lib)
    # Brand/voice/open-source checks run live against the real app.py — assert
    # only on the signals this test actually manipulates, not the whole dict.
    assert "/admin/queue" not in counts
    assert "/admin/tools/software" not in counts
    assert "/admin/tools/communities" not in counts
    assert "/admin/contacts" not in counts
    assert tasks.has_open_tasks(lib) is (len(counts) > 0)


def test_open_task_counts_reflects_pending_tool(lib):
    from webapp import tasks
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 1
    assert tasks.has_open_tasks(lib) is True


def test_open_task_counts_reflects_pending_community(lib):
    from webapp import tasks
    lib.add_community(name="A", url="https://a.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/communities"] == 1
    assert tasks.has_open_tasks(lib) is True


def test_open_task_counts_reflects_new_contact(lib):
    from webapp import tasks
    lib.save_contact("Jane", "jane@x.com", "hi")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/contacts"] == 1
    lib.set_setting("admin_viewed_contacts", lib.list_contacts()[0]["created_at"])
    counts = tasks.open_task_counts(lib)
    assert "/admin/contacts" not in counts   # viewing clears it


def test_open_task_counts_reflects_pending_password_reset(lib):
    from webapp import tasks
    uid = lib.create_user("jane", "supersecret")
    lib.create_password_reset_request(uid, "jane")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/users"] == 1


# --- email delivery failures -------------------------------------------------

def test_email_failure_lifecycle(lib):
    assert lib.count_pending_email_failures() == 0
    fid = lib.log_email_failure("contact", "401 Unauthorized")
    assert lib.count_pending_email_failures() == 1
    pending = lib.list_email_failures(pending_only=True)
    assert len(pending) == 1 and pending[0]["id"] == fid and pending[0]["context"] == "contact"

    lib.dismiss_email_failure(fid)
    assert lib.count_pending_email_failures() == 0
    # Dismissed failures stay in the full history, just not the pending view.
    assert len(lib.list_email_failures(pending_only=False)) == 1
    assert len(lib.list_email_failures(pending_only=True)) == 0


def test_open_task_counts_reflects_email_failure(lib):
    from webapp import tasks
    lib.log_email_failure("tool_submission", "boom")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/email-failures"] == 1


# --- dot-vs-count rendering ---------------------------------------------------
# All-or-none sources (viewed as one full list, no per-item action) get a plain
# dot instead of a misleading count; individually-actionable sources keep theirs.

def test_dot_only_hrefs_are_all_or_none_sources():
    from webapp import tasks
    assert tasks.DOT_ONLY_HREFS == {"/admin/contacts", "/admin/tools/leads"}


def test_badge_for_href_renders_dot_for_all_or_none(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._badge_for_href("/admin/contacts", 3) == '<span class="task-badge-dot" aria-label="Unread"></span>'
    assert appmod._badge_for_href("/admin/tools/leads", 1) == '<span class="task-badge-dot" aria-label="Unread"></span>'
    assert appmod._badge_for_href("/admin/contacts", 0) == ""


def test_badge_for_href_renders_count_for_individually_actionable(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._badge_for_href("/admin/tools/software", 2) == '<span class="task-badge">2</span>'
    assert appmod._badge_for_href("/admin/email-failures", 1) == '<span class="task-badge">1</span>'
    assert appmod._badge_for_href("/admin/tools/software", 0) == ""


def test_group_badge_dot_when_only_all_or_none_pending(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    counts = {"/admin/contacts": 2, "/admin/tools/leads": 1, "/admin/email-failures": 0}
    hrefs = ["/admin/contacts", "/admin/tools/leads", "/admin/email-failures"]
    assert appmod._group_badge(counts, hrefs) == '<span class="task-badge-dot" aria-label="Unread"></span>'


def test_group_badge_counts_when_individually_actionable_pending(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    # A real per-item task (an email failure) dominates the section total —
    # the all-or-none contact isn't double-counted as if it were 1 more task.
    counts = {"/admin/contacts": 1, "/admin/email-failures": 2}
    hrefs = ["/admin/contacts", "/admin/email-failures"]
    assert appmod._group_badge(counts, hrefs) == '<span class="task-badge">2</span>'


def test_group_badge_empty_when_nothing_pending(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._group_badge({}, ["/admin/contacts", "/admin/tools/software"]) == ""


# --- end-to-end: badge clears at every level after viewing --------------------

@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_admin_pages_are_never_cached(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin")
    assert r.headers.get("cache-control") == "no-store"
    r2 = client.get("/admin/contacts")
    assert r2.headers.get("cache-control") == "no-store"
    # Public pages are untouched — no reason to disable caching there.
    r3 = client.get("/health")
    assert r3.headers.get("cache-control") != "no-store"


def test_contacts_badge_clears_after_viewing_at_every_level(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.save_contact("Jane", "jane@x.com", "hi there")
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-dot"' in r1.text                      # nav dot
    assert '<span class="task-badge-dot" aria-label="Unread"></span>' in r1.text  # card dot, not a count

    client.get("/admin/contacts")   # visiting clears the read-state

    r2 = client.get("/admin")
    assert '<span class="task-dot"' not in r2.text
    assert '<span class="task-badge-dot"' not in r2.text


def test_tool_leads_badge_clears_after_viewing(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    tool_id = lib.add_tool("A", "desc", "https://a.example", [], approved=1)
    lib.save_tool_lead(tool_id, "A", "Jane", "jane@x.com", "Acme", "50-200")
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-badge-dot" aria-label="Unread"></span>' in r1.text

    client.get("/admin/tools/leads")   # unfiltered view clears it

    r2 = client.get("/admin")
    assert '<span class="task-dot"' not in r2.text


def test_pending_tool_badge_only_clears_on_approval_not_view(admin_client):
    """Individually-actionable sources shouldn't clear from merely looking at
    the list — only acting on the item (here, approving it) resolves it."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-badge">1</span>' in r1.text

    client.get("/admin/tools/software")   # merely viewing the pending-submissions page

    r2 = client.get("/admin")
    assert '<span class="task-badge">1</span>' in r2.text   # still open — viewing isn't the action


def test_pending_community_badge_clears_on_approval(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    community_id = lib.add_community(name="A", url="https://a.example",
                                      demographic="CFOs", cost_band="Free", categories=[], approved=0)
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-badge">1</span>' in r1.text

    client.get("/admin/tools/communities")   # merely viewing doesn't clear it
    r2 = client.get("/admin")
    assert '<span class="task-badge">1</span>' in r2.text

    # Approving redirects straight to the profile editor so "Generate profile
    # draft" is immediately in front of Brian, rather than back to the list.
    r3 = client.post(f"/admin/tools/communities/{community_id}/approve", follow_redirects=False)
    assert r3.status_code == 303
    assert r3.headers["location"] == f"/admin/tools/communities/{community_id}/profile"

    r4 = client.get("/admin")
    assert '<span class="task-badge">1</span>' not in r4.text


def test_reject_community_deletes_pending_submission(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    community_id = lib.add_community(name="A", url="https://a.example",
                                      demographic="CFOs", cost_band="Free", categories=[], approved=0)
    lib.close()

    r = client.post(f"/admin/tools/communities/{community_id}/reject", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/tools/communities"

    lib = Library(db)
    try:
        assert lib.get_community(community_id) is None
    finally:
        lib.close()
