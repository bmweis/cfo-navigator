"""Task badge system (admin coral notifications): the DB-layer counts each
badge is built from, and webapp.tasks aggregating them into one dict keyed by
admin href.
"""
import pathlib
import sys

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
    assert "/admin/tools" not in counts
    assert "/admin/contacts" not in counts
    assert tasks.has_open_tasks(lib) is (len(counts) > 0)


def test_open_task_counts_reflects_pending_tool(lib):
    from webapp import tasks
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools"] == 1
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
