"""Task badge system (admin coral notifications): the DB-layer counts each
badge is built from, and webapp.tasks aggregating them into one dict keyed by
admin href.
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _mark_all_freshness_reviewed(db_path: str) -> None:
    """A fresh Library starts with pricing_last_verified/models_last_reviewed/
    exa_pricing_last_verified all empty — which is honest (nobody has
    reviewed them yet) but means every never-touched test DB now
    contributes 3 to the /admin/checks badge (2026-09: see
    webapp.tasks._stale_admin_checks_reminders). Real and correct in
    production — a genuinely never-reviewed table SHOULD show as pending —
    but noise in a test asserting an otherwise-clean badge baseline that
    has nothing to do with pricing/model freshness. Tests that care about
    an exact badge count/absence call this in setup to establish "already
    reviewed" as the clean baseline, same as a real admin clicking "Mark
    reviewed" three times on day one would."""
    from datetime import datetime, timezone
    lib = Library(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        lib.set_setting("pricing_last_verified", now)
        lib.set_setting("models_last_reviewed", now)
        lib.set_setting("exa_pricing_last_verified", now)
    finally:
        lib.close()


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


# The Archive Queue badge (queue_count) and the "Remove content" badge
# (flagged_count) were both retired along with their underlying features —
# 2026-09, PR 3 and PR 4 respectively. See CLAUDE.md's "Archive Queue
# retired outright" and "'Remove content' retired" bullets.


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
    assert "/admin/library/queue" not in counts
    assert "/admin/tools/software" not in counts
    assert "/admin/tools/communities" not in counts
    assert "/admin/inbox/contact-submissions" not in counts
    assert tasks.has_open_tasks(lib) is (len(counts) > 0)


def test_open_task_counts_reflects_pending_tool(lib):
    from webapp import tasks
    # add_tool() defaults needs_review=1 for EVERY brand-new tool (see its
    # own docstring) and approve_tool() never touches needs_review — so an
    # unapproved tool always satisfies both "pending approval" and "needs
    # review" at once. Before the dedup fix, the badge summed two separate
    # counts and double-counted this every time; now it's a single deduped
    # count and this tool counts once.
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 1
    assert tasks.has_open_tasks(lib) is True


def test_open_task_counts_does_not_double_count_pending_and_needing_review_tool(lib):
    """Direct regression test for the double-count bug: a single tool that
    is BOTH pending approval AND needs-review (the guaranteed-overlap case
    every brand-new tool hits) must count once in the Software badge, not
    twice — and a second, distinct pending tool must still add a second
    count on top of it."""
    from webapp import tasks
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)  # pending + needs_review=1
    lib.add_tool("B", "desc", "https://b.example", [], approved=1, needs_review=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 1  # not 2

    lib.add_tool("C", "desc", "https://c.example", [], approved=0)  # a second, distinct overlap case
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 2  # not 4


def test_open_task_counts_dedupes_per_field_verification_flags(lib):
    """The three per-field *_needs_verification flags (Description/Agent
    taxonomy/Competitive differentiation) ARE folded into the Software
    card's badge, via count_tools_needing_attention() — but deduped per
    tool, not summed per field: a tool with 2 or 3 pending fields still
    counts once. Also covers the edge case that method's docstring names:
    a field flag pending with no whole-record needs_review (and not
    awaiting approval) still shows up."""
    from webapp import tasks
    tool_id = lib.add_tool("A", "desc", "https://a.example", [], approved=1, needs_review=0)
    counts = tasks.open_task_counts(lib)
    assert "/admin/tools/software" not in counts

    lib.set_tool_agent_taxonomy_draft(tool_id, "note", needs_verification=1)
    lib.mark_tool_reviewed(tool_id)  # clears needs_review, leaves the field flag set
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 1

    # A second pending field on the SAME tool must not push the count to 2.
    lib.update_tool(tool_id, "A", "new desc", "https://a.example", [],
                     summary="new summary", description_needs_verification=1)
    lib.mark_tool_reviewed(tool_id)  # needs_review only, field flags untouched
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software"] == 1


def test_open_task_counts_reflects_pending_community(lib):
    from webapp import tasks
    # Unlike tools, a freshly submitted community has NO community_profiles
    # row at all (that table's needs_review flag doesn't exist until a
    # profile is actually drafted) — so this pending community is NOT also
    # needs-review, and the count here (1) isn't proof of dedup on its own;
    # see test_open_task_counts_does_not_double_count_pending_and_needing_
    # review_community below for the real overlap case.
    lib.add_community(name="A", url="https://a.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=0)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/communities"] == 1
    assert tasks.has_open_tasks(lib) is True


def test_open_task_counts_does_not_double_count_pending_and_needing_review_community(lib):
    """Communities' overlap isn't automatic the way tools' is (see
    Library.count_communities_needing_attention()'s own docstring) — it
    requires an admin to draft/save a profile for a still-pending
    community, reachable only by direct URL — but nothing in the code
    prevents it, so it must still dedupe correctly when it happens."""
    from webapp import tasks
    community_id = lib.add_community(name="A", url="https://a.example", demographic="CFOs",
                                      cost_band="Free", categories=[], approved=0)
    lib.upsert_community_profile(community_id, ideal_member="CFOs at Series B+", needs_review=1)
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/communities"] == 1  # not 2

    lib.add_community(name="B", url="https://b.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)  # a distinct approved-but-unrelated row
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/communities"] == 1  # unaffected — B has no profile, not needs-review

    lib.add_community(name="C", url="https://c.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=0)  # a second, distinct pending community
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/communities"] == 2  # not 3


def test_open_task_counts_reflects_new_contact(lib):
    from webapp import tasks
    lib.save_contact("Jane", "jane@x.com", "hi")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/inbox/contact-submissions"] == 1
    lib.set_setting("admin_viewed_contacts", lib.list_contacts()[0]["created_at"])
    counts = tasks.open_task_counts(lib)
    assert "/admin/inbox/contact-submissions" not in counts   # viewing clears it


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
    assert counts["/admin/inbox/email-failures"] == 1


# --- badge rendering: always a real count (PR 28, 2026-09) -------------------
# DOT_ONLY_HREFS (an "all-or-none sources get a dot, not a count" rule) is
# retired from webapp.tasks entirely — the admin page always shows a real
# count now, even for a source with no per-item drill-down, per Brian's
# explicit call. The nav bar's own presence-only dot is a separate mechanism
# (see test_contacts_badge_clears_after_viewing_at_every_level below) and is
# unaffected.

def test_dot_only_hrefs_is_retired():
    from webapp import tasks
    assert not hasattr(tasks, "DOT_ONLY_HREFS")


def test_badge_for_href_always_renders_a_count(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._badge_for_href("/admin/inbox/contact-submissions", 3) == '<span class="task-badge">3</span>'
    assert appmod._badge_for_href("/admin/inbox/toolbox-intros", 1) == '<span class="task-badge">1</span>'
    assert appmod._badge_for_href("/admin/inbox/contact-submissions", 0) == ""
    assert appmod._badge_for_href("/admin/tools/software", 2) == '<span class="task-badge">2</span>'
    assert appmod._badge_for_href("/admin/inbox/email-failures", 1) == '<span class="task-badge">1</span>'
    assert appmod._badge_for_href("/admin/tools/software", 0) == ""


def test_group_badge_sums_every_href_into_one_count(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    counts = {"/admin/inbox/contact-submissions": 2, "/admin/inbox/toolbox-intros": 1, "/admin/inbox/email-failures": 0}
    hrefs = ["/admin/inbox/contact-submissions", "/admin/inbox/toolbox-intros", "/admin/inbox/email-failures"]
    assert appmod._group_badge(counts, hrefs) == '<span class="task-badge">3</span>'

    counts2 = {"/admin/inbox/contact-submissions": 1, "/admin/inbox/email-failures": 2}
    hrefs2 = ["/admin/inbox/contact-submissions", "/admin/inbox/email-failures"]
    assert appmod._group_badge(counts2, hrefs2) == '<span class="task-badge">3</span>'


def test_group_badge_empty_when_nothing_pending(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._group_badge({}, ["/admin/inbox/contact-submissions", "/admin/tools/software"]) == ""


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
    r2 = client.get("/admin/inbox/contact-submissions")
    assert r2.headers.get("cache-control") == "no-store"
    # Public pages are untouched — no reason to disable caching there.
    r3 = client.get("/health")
    assert r3.headers.get("cache-control") != "no-store"


def test_contacts_badge_clears_after_viewing_at_every_level(admin_client):
    client, appmod, db = admin_client
    _mark_all_freshness_reviewed(db)   # isolate this test from the /admin/checks reminders
    from linklib.db import Library
    lib = Library(db)
    lib.save_contact("Jane", "jane@x.com", "hi there")
    lib.close()

    r1 = client.get("/admin")
    # 2026-09: the nav badge is a real number now, not a presence dot (see
    # webapp/tasks.py's module docstring) — with exactly one open task in
    # play here, both the nav total and the card read "1".
    assert r1.text.count('<span class="task-badge">1</span>') >= 2  # nav + card
    assert '<span class="task-badge">1</span>' in r1.text           # card shows a real count now (PR 28)

    client.get("/admin/inbox/contact-submissions")   # visiting clears the read-state

    r2 = client.get("/admin")
    assert '<span class="task-badge">1</span>' not in r2.text
    assert 'class="task-badge"' not in r2.text   # nav total is also 0 now — no badge renders at all


def test_tool_leads_badge_clears_after_viewing(admin_client):
    client, appmod, db = admin_client
    _mark_all_freshness_reviewed(db)   # isolate this test from the /admin/checks reminders
    from linklib.db import Library
    lib = Library(db)
    # needs_review=0 (a brand-new tool otherwise defaults to 1, per add_tool's
    # own docstring) so the only open task in play here is the lead itself —
    # otherwise a lingering count_tools_needing_attention()>0 would keep the
    # global nav dot lit after the lead-specific dot clears below, unrelated
    # to what this test is actually about.
    tool_id = lib.add_tool("A", "desc", "https://a.example", [], approved=1, needs_review=0)
    lib.save_tool_lead(tool_id, "A", "Jane", "jane@x.com", "Acme", "50-200")
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-badge">1</span>' in r1.text

    client.get("/admin/inbox/toolbox-intros")   # unfiltered view clears it

    r2 = client.get("/admin")
    assert 'class="task-badge"' not in r2.text   # .task-dot is retired — no badge at all once clean


def test_pending_tool_badge_only_clears_on_approval_not_view(admin_client):
    """Individually-actionable sources shouldn't clear from merely looking at
    the list — only acting on the item (here, approving it) resolves it."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    # needs_review=0 to isolate this test to the approval-queue count alone —
    # a brand-new tool otherwise defaults to needs_review=1, which would add
    # its own contribution via count_tools_needing_attention() (see the
    # dedicated tests for that above) and muddy this test's "1".
    lib.add_tool("A", "desc", "https://a.example", [], approved=0, needs_review=0)
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


# --- Phase 1c badge scope-down: feature review queue, content backfill,
# name-duplicates --------------------------------------------------------

def test_count_feature_review_queue(lib):
    assert lib.count_feature_review_queue(status="pending") == 0
    cat_id = lib.add_tool_category("ERP")
    lib.add_feature_review_queue_item(
        "scan", "new_feature", {"category_id": cat_id, "feature": {"name": "A"}, "links": []},
        category_id=cat_id,
    )
    assert lib.count_feature_review_queue(status="pending") == 1
    assert lib.count_feature_review_queue(status=None) == 1


def test_open_task_counts_reflects_pending_feature_review_queue_item(lib):
    from webapp import tasks
    cat_id = lib.add_tool_category("ERP")
    lib.add_feature_review_queue_item(
        "scan", "new_feature", {"category_id": cat_id, "feature": {"name": "A"}, "links": []},
        category_id=cat_id,
    )
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software/feature-review-queue"] == 1


def test_open_task_counts_reflects_content_backfill_needs(lib):
    from webapp import tasks
    from linklib.db import Article
    art = Article(url="https://x.example/needs-check", title="t", source="s")
    aid = lib.upsert(art)
    lib.set_content_check_flag(aid, True, "too-thin")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/reader/backfill-content"] == 1


def test_open_task_counts_reflects_name_duplicates(lib):
    from webapp import tasks
    lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    lib.add_tool("Rillet", "d", "https://rillet-dup.example", ["ERP"], approved=1, summary="s")
    counts = tasks.open_task_counts(lib)
    assert counts["/admin/tools/software/name-duplicates"] == 1


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


def _group_label_index(text: str, label: str) -> int:
    """Locate a group's own heading `>{label}<` inside its `_disclosure_group`
    summary span, not some other coincidental occurrence of the same text
    elsewhere on the page. Needed specifically for "CFO Toolbox": the public
    nav bar's own `<a href="/tools">CFO Toolbox</a>` link renders *before*
    the admin groups in document order and also matches a bare `>CFO
    Toolbox<` substring search, so a naive `text.index(label)` finds the nav
    link, not the group heading — `rfind("<details", ...)` from there then
    walks back into an unrelated `<details>` (or, worse, the literal text
    "<details" inside a CSS comment in the page's <style> block), producing
    a false-passing assertion rather than a real one. Searching only after
    `<h1>Admin</h1>` (the admin body's own start, always after the nav in
    document order) sidesteps this for every group label, not just this
    one."""
    body_start = text.index("<h1>Admin</h1>")
    return text.index(label, body_start)


# --- default-collapsed admin menus, badge stays visible collapsed ----------
# (2026-09 — reverses the 2026-08 "a nonzero badge auto-expands its group"
# fix below, per Brian's explicit call: badges ARE the review-inbox signal,
# and auto-expanding on top of that duplicated it as an intrusive default
# rather than adding a genuinely different safeguard. The 2026-08 fix's own
# root cause — LiveFlow/Liveflow and a Runway name-duplicate pair sat
# correctly detected and correctly badge-counted, but nobody noticed because
# the Software sub-group carrying that badge was collapsed, two disclosure
# levels deep, with only a small number on its summary row to hint at it —
# is still guarded against here, just by a different mechanism: the group's
# own badge only ever hides once its <details> is OPENED
# (`.admin-group[open] > summary .group-badge{display:none;}`), so a
# collapsed-by-default group with something pending still shows its badge
# number, unhidden, right on the summary row. See CLAUDE.md. (PR 28, 2026-09:
# the selector itself was a real bug until this point — the descendant form
# `.admin-group[open] .group-badge` matched every badge nested under an
# open group, including a still-collapsed CHILD group's own badge; scoped
# to `> summary` so opening a parent can only ever hide its own badge. See
# test_opening_a_group_does_not_hide_a_collapsed_child_groups_badge below.)

def test_all_groups_start_collapsed_even_with_a_nonzero_badge(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("LiveFlow", "desc", "https://liveflow.example", ["ERP"], approved=1)
    lib.add_tool("Liveflow", "desc", "https://liveflow2.example", ["Financial Reporting"], approved=1)
    lib.close()

    r = client.get("/admin")
    # Every top-level group's <details>, and the nested Software sub-group's
    # own <details>, must render collapsed on load — including Inbox (always
    # open pre-2026-09) and Software (carrying a real pending name-duplicate
    # badge here, which used to force it open too).
    for label in (">Inbox<", ">CFO Toolbox<", ">Software<"):
        idx = _group_label_index(r.text, label)
        details_start = r.text.rfind("<details", 0, idx)
        tag_end = r.text.index(">", details_start)
        assert " open" not in r.text[details_start:tag_end], label


def test_group_with_zero_badge_also_stays_collapsed(admin_client):
    """Control case — a group with nothing pending starts collapsed too,
    same as always."""
    client, appmod, db = admin_client
    r = client.get("/admin")
    idx = _group_label_index(r.text, ">Software<")
    details_start = r.text.rfind("<details", 0, idx)
    tag_end = r.text.index(">", details_start)
    assert " open" not in r.text[details_start:tag_end]


def test_group_badge_still_visible_while_collapsed(admin_client):
    """The real regression guard replacing the old auto-expand fix: a
    collapsed group carrying a real pending item still shows its badge
    number on the summary row (`.group-badge` only hides once the group is
    OPENED — see the CSS rule `.admin-group[open] > summary .group-badge
    {display:none;}` — so collapsed-by-default never hides a pending
    badge)."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("LiveFlow", "desc", "https://liveflow.example", ["ERP"], approved=1)
    lib.add_tool("Liveflow", "desc", "https://liveflow2.example", ["Financial Reporting"], approved=1)
    lib.close()

    r = client.get("/admin")
    idx = _group_label_index(r.text, ">Software<")
    details_start = r.text.rfind("<details", 0, idx)
    details_end = r.text.index("</details>", idx)
    # The Software sub-group's own badge markup must appear before its body
    # content starts, i.e. inside the (collapsed) summary row.
    summary_end = r.text.index("</summary>", details_start)
    assert "task-badge" in r.text[details_start:summary_end]
    assert details_end > summary_end


def test_nested_group_badge_shows_through_collapsed_parent(admin_client):
    """The exact condition the incident comment describes, re-verified now
    that EVERY group (not just non-badged ones) defaults to collapsed: a
    real pending item inside Software (nested two levels deep — CFO Toolbox
    -> Software -> the tool itself) must still be visible from the admin
    hub's top level without expanding anything, since a native <details>
    hides its entire body — including a nested <details> and that nested
    group's OWN badge span — the moment its parent is collapsed.

    The mechanism that satisfies this isn't Software's own badge "showing
    through" — it structurally can't, since it lives inside CFO Toolbox's
    collapsed body. It's that CFO Toolbox's own group-level badge already
    aggregates every href nested inside it (see `toolbox_hrefs` in
    admin_page(), which folds in `software_hrefs`), so CFO Toolbox's own
    <summary> — which stays visible regardless of its own open/closed state
    — already reflects Software's pending count without needing Software's
    <details> to be open at all."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)
    lib.close()

    r = client.get("/admin")

    # Both CFO Toolbox's and Software's own <details> render collapsed.
    for label in (">CFO Toolbox<", ">Software<"):
        idx = _group_label_index(r.text, label)
        details_start = r.text.rfind("<details", 0, idx)
        tag_end = r.text.index(">", details_start)
        assert " open" not in r.text[details_start:tag_end], label

    # CFO Toolbox's own summary row (visible without expanding anything)
    # already carries a nonzero badge reflecting Software's pending tool.
    idx = _group_label_index(r.text, ">CFO Toolbox<")
    details_start = r.text.rfind("<details", 0, idx)
    summary_end = r.text.index("</summary>", details_start)
    toolbox_summary = r.text[details_start:summary_end]
    assert "task-badge" in toolbox_summary

    m = re.search(r'<span class="task-badge">(\d+)</span>', toolbox_summary)
    assert m is not None
    assert int(m.group(1)) >= 1  # the pending tool (deduped to 1, see the badge-dedup tests)


# --- PR 28 (2026-09): the badge-hiding CSS scoping bug ----------------------
# `.admin-group[open] .group-badge{display:none;}` was a descendant selector
# that matched every `.group-badge` nested under an open `.admin-group`, at
# any depth — not just that group's own summary. Opening CFO Toolbox (which
# nests Software/Community/FP&A Buddy/Reader as still-collapsed sub-groups)
# hid every one of THEIR badges too, even though none of them were open.
# Fixed by scoping the rule to `> summary` — a child combinator that can
# only ever reach the group's own <summary>, never a nested sub-group's
# (which lives in the sibling <div> after <summary>, not inside it).

def test_group_badge_css_rule_is_scoped_to_its_own_summary(admin_client):
    """The literal CSS rule shipped on the page must be the scoped
    `> summary` form, not the old bare-descendant form that caused the bug."""
    client, appmod, db = admin_client
    r = client.get("/admin")
    assert ".admin-group[open] > summary .group-badge{display:none;}" in r.text
    assert ".admin-group[open] .group-badge{display:none;}" not in r.text


def test_opening_a_group_does_not_hide_a_collapsed_child_groups_badge(admin_client):
    """Direct regression test for the scoping bug itself: CFO Toolbox and its
    nested Software sub-group both carry real, nonzero badges. Rendering the
    page with CFO Toolbox's own `open` attribute forced on (simulating what
    the browser does the instant a visitor expands it) must still leave
    Software's own badge markup intact and visible in the response — the old
    descendant selector would have hidden it via CSS the moment CFO Toolbox
    was open, even though Software itself stays collapsed. TestClient can't
    evaluate CSS, so this asserts on the one thing that actually decides the
    bug in either direction: the shipped selector text (covered by the test
    above) plus proof that Software's badge markup is still there,
    independent of CFO Toolbox's own open/closed state, for the CSS rule to
    correctly leave alone."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("A", "desc", "https://a.example", [], approved=0)  # gives Software a real badge
    lib.close()

    r = client.get("/admin")
    idx = _group_label_index(r.text, ">Software<")
    details_start = r.text.rfind("<details", 0, idx)
    details_end = r.text.index("</details>", idx)
    summary_end = r.text.index("</summary>", details_start)
    # Software's own badge lives inside ITS OWN summary — the scoped CSS rule
    # (`.admin-group[open] > summary .group-badge`) can only ever match a
    # badge inside the summary of the SAME <details> that carries [open], so
    # this markup being present and unconditional (not swapped out depending
    # on CFO Toolbox's state) is what the fix guarantees.
    assert "task-badge" in r.text[details_start:summary_end]
    assert details_end > summary_end


# --- _failing_checks_count() re-entrancy + concurrent-miss dedup (2026-09) --
# See webapp/tasks.py's own comment above `_checks_computing` for the full
# story: this function is genuinely re-entered through its own call chain in
# open-auth mode (coral_moment_problems() -> render every public route as
# role="admin" -> _has_open_admin_tasks() -> this function again, on a
# DIFFERENT OS thread than the one already running the outer call, since
# anyio dispatches each nested render onto its own threadpool worker) — so a
# plain lock held across the compute step would deadlock (the outer thread
# holds it while blocked on the render; the nested call, on a different
# thread, blocks trying to acquire the very lock the outer thread won't
# release until the nested call returns). Fixed with a non-blocking sentinel
# instead of any lock a thread can wait on.

@pytest.fixture
def no_password_env(monkeypatch):
    """No LINKLIB_PASSWORD/LINKLIB_SAVE_TOKEN at all — the documented "open,
    local-dev convenience" auth mode that makes _is_authed()/_role() treat
    every visitor as admin, which is what actually triggers the re-entrant
    call chain this section tests. Same shape as
    tests/test_coral_discipline.py's own fixture of the same name."""
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.delenv("LINKLIB_PASSWORD", raising=False)
    monkeypatch.delenv("LINKLIB_SAVE_TOKEN", raising=False)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def test_concurrent_cache_miss_computes_run_all_only_once(monkeypatch):
    """Two GENUINELY INDEPENDENT concurrent cache-miss callers (e.g. two
    separate admin browser tabs hitting a stale cache around the same
    moment) — deliberately synchronized, not hoped-for — must result in
    run_all() executing once, not twice. This is the real waste the
    coral-PR investigation flagged: a plain cache-check-then-compute with no
    de-dup between the check and the compute lets two overlapping callers
    both redo the ~3.5-9s run_all() pass for the same 120s-TTL value.

    Written to fail deterministically against the pre-fix code (a bare
    cache-check with no _checks_computing sentinel: both threads pass the
    stale-cache check before either writes a fresh value, so both call
    run_all()) and pass against the fix — see the PR description for real
    captured output from both states, per the same discipline the coral PR
    itself used."""
    import threading as _threading
    from webapp import tasks as taskmod

    taskmod._checks_cache = None
    taskmod._checks_computing = False

    run_all_calls = {"n": 0}
    a_started = _threading.Event()
    a_may_finish = _threading.Event()
    call_lock = _threading.Lock()

    def fake_run_all():
        with call_lock:
            run_all_calls["n"] += 1
            n = run_all_calls["n"]
        if n == 1:
            # Thread A: signal it has started computing, then block — the
            # exact window a genuinely concurrent second caller would land
            # in against the real ~3.5-9s run_all() pass.
            a_started.set()
            a_may_finish.wait(timeout=5)
        return [{"where": "In-app", "ok": True}]

    monkeypatch.setattr(taskmod._checks, "run_all", fake_run_all)

    results = {}

    def run_a():
        results["a"] = taskmod._failing_checks_count()

    def run_b():
        assert a_started.wait(timeout=5), "thread A never started computing"
        results["b"] = taskmod._failing_checks_count()

    t_a = _threading.Thread(target=run_a)
    t_b = _threading.Thread(target=run_b)
    t_a.start()
    assert a_started.wait(timeout=5), "thread A never started computing"
    t_b.start()
    t_b.join(timeout=10)
    a_may_finish.set()
    t_a.join(timeout=10)

    assert not t_a.is_alive() and not t_b.is_alive(), "a thread never finished"
    assert run_all_calls["n"] == 1, (
        f"run_all() was computed {run_all_calls['n']} times for two "
        "concurrent independent cache-miss callers — should be exactly 1"
    )
    # Thread B got a real value back (whatever's cached/in-flight — never an
    # exception, never a wait that never resolves) rather than being made to
    # redundantly recompute.
    assert results.get("b") == 0


def test_reentrant_failing_checks_count_does_not_hang(no_password_env):
    """The test that matters most: a real signed-out GET "/" in open-auth
    mode — which recurses through _page()'s admin-nav badge computation back
    into _failing_checks_count(), on a different OS thread per nested render
    (see the module comment above) — must finish within a generous bound in
    a background thread. A naive fix here (a threading.Lock or RLock held
    across run_all()) would hang this permanently, not just slowly: the
    outer thread holds the lock while blocked waiting for the render to
    finish, and the nested call — on a different thread — blocks trying to
    acquire that same lock. This is the same harness shape
    tests/test_coral_discipline.py used to catch the analogous
    threading.local() regression in the coral guard itself."""
    import threading as _threading
    from webapp import tasks as taskmod
    from fastapi.testclient import TestClient

    # webapp.tasks' cache/sentinel are plain module globals, never reloaded
    # by no_password_env's importlib.reload(webapp.app) — reset explicitly
    # so an earlier test in this same process (which may have left a fresh
    # cache behind) can't make this render skip the compute path entirely.
    taskmod._checks_cache = None
    taskmod._checks_computing = False

    client = TestClient(no_password_env.app, raise_server_exceptions=False)
    result = {}

    def run():
        result["resp"] = client.get("/", follow_redirects=False)

    t = _threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=90)
    assert not t.is_alive(), (
        "GET \"/\" in open-auth mode did not finish within 90s — a lock "
        "held across run_all() would deadlock here permanently; the fix "
        "must be a non-blocking sentinel instead"
    )
    assert result["resp"].status_code == 200


def test_reentrant_call_skips_recomputation_entirely(no_password_env):
    """Direct proof the fix does more than avoid a hang: the re-entrant call
    (triggered for real, not simulated) never redoes run_all() at all — it
    returns the in-flight sentinel's fallback immediately. Instruments
    webapp.checks.run_all (the exact symbol webapp.tasks imports as
    `_checks`) to count real invocations for one real signed-out GET "/" in
    open-auth mode; before this fix, the coral PR's own investigation
    measured exactly 2 (the outer call, plus one genuine recomputation
    triggered by the very first nested render) — this asserts exactly 1."""
    from webapp import tasks as taskmod

    # Same isolation reset as the sibling test above — a fresh cache left
    # by an earlier test in this process would make this render skip
    # run_all() entirely and falsely look like the fix already works.
    taskmod._checks_cache = None
    taskmod._checks_computing = False

    call_count = {"n": 0}
    orig_run_all = taskmod._checks.run_all

    def counting_run_all():
        call_count["n"] += 1
        return orig_run_all()

    taskmod._checks.run_all = counting_run_all
    try:
        from fastapi.testclient import TestClient
        client = TestClient(no_password_env.app, raise_server_exceptions=False)
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert call_count["n"] == 1, (
            f"run_all() ran {call_count['n']} times for one page render — "
            "the re-entrant nested call should return the in-flight "
            "sentinel's fallback instead of recomputing"
        )
    finally:
        taskmod._checks.run_all = orig_run_all


# --- Background checks refresher: single-iteration + health visibility -----
# (2026-09 follow-up) — before this, the one code path that actually makes
# production fast (webapp.tasks._run_one_refresh_iteration, driven on a
# schedule by _checks_refresher_loop) had no direct test coverage at all;
# only the request-triggered synchronous fallback in _failing_checks_count()
# did. These call it directly and synchronously — no thread, no
# time.sleep(_CHECKS_CACHE_TTL) — so they run in a fraction of a second.

@pytest.fixture
def refresher_state_reset():
    """Isolates each test in this section from whatever _checks_cache/
    _checks_last_attempt_at/_checks_last_error a previous test in this same
    process left behind — same reset discipline as the re-entrancy tests
    above."""
    from webapp import tasks as taskmod
    taskmod._checks_cache = None
    taskmod._checks_computing = False
    taskmod._checks_last_attempt_at = None
    taskmod._checks_last_error = None
    yield taskmod
    taskmod._checks_cache = None
    taskmod._checks_computing = False
    taskmod._checks_last_attempt_at = None
    taskmod._checks_last_error = None


def test_run_one_refresh_iteration_updates_cache_on_success(refresher_state_reset, monkeypatch):
    taskmod = refresher_state_reset
    monkeypatch.setattr(taskmod._checks, "run_all", lambda: [
        {"where": "In-app", "ok": False}, {"where": "In-app", "ok": True},
    ])

    assert taskmod._checks_cache is None
    assert taskmod.refresher_status()["last_success_at"] is None

    taskmod._run_one_refresh_iteration()

    assert taskmod._checks_cache is not None
    assert taskmod._checks_cache[1] == 1   # one failing In-app row
    status = taskmod.refresher_status()
    assert status["last_success_at"] is not None
    assert status["last_attempt_at"] is not None
    assert status["last_error"] is None


def test_run_one_refresh_iteration_catches_and_records_exceptions(refresher_state_reset, monkeypatch, caplog):
    """The core ask: one bad pass must not propagate, must not corrupt the
    cache, and must be visible afterward (both logged, per the standing
    "never silently fail" rule, and recorded in refresher_status() for
    /admin/checks to render)."""
    taskmod = refresher_state_reset

    def boom():
        raise RuntimeError("simulated run_all() failure")

    monkeypatch.setattr(taskmod._checks, "run_all", boom)

    import logging
    with caplog.at_level(logging.ERROR, logger="webapp.tasks"):
        taskmod._run_one_refresh_iteration()   # must not raise

    assert taskmod._checks_cache is None   # nothing bogus written on failure
    status = taskmod.refresher_status()
    assert status["last_attempt_at"] is not None   # the attempt itself is still recorded
    assert status["last_error"] is not None
    assert "RuntimeError" in status["last_error"]
    assert "simulated run_all() failure" in status["last_error"]
    assert any("refresher" in r.message.lower() for r in caplog.records), (
        "a failed iteration must be logged, not silently swallowed"
    )

    # And the loop keeps working on the next call — one bad pass doesn't
    # wedge the refresher permanently.
    monkeypatch.setattr(taskmod._checks, "run_all", lambda: [{"where": "In-app", "ok": True}])
    taskmod._run_one_refresh_iteration()
    assert taskmod._checks_cache is not None
    assert taskmod._checks_cache[1] == 0
    assert taskmod.refresher_status()["last_error"] is None   # a later success clears the error


def test_refresher_status_reflects_started_flag(refresher_state_reset):
    taskmod = refresher_state_reset
    taskmod._checks_refresher_started = False
    assert taskmod.refresher_status()["started"] is False
    taskmod._checks_refresher_started = True
    assert taskmod.refresher_status()["started"] is True
    taskmod._checks_refresher_started = False   # don't leak into other tests


# --- Background refresher: does not start under the test suite (issue #592
# item 2) -----------------------------------------------------------------
# start_background_checks_refresher() used to always spin up a real daemon
# thread whenever the FastAPI startup event fired (which happens under
# `with TestClient(...)`, per test_seed_toolbox_startup.py), re-reading
# LINKLIB_DB on every iteration — a real "wander between test databases"
# risk once a leftover thread from one test outlives that test's own
# monkeypatched-and-deleted DB file. These prove the fix directly: under
# pytest (PYTEST_CURRENT_TEST is always set here, since we're inside a
# test), calling it with no arguments must be a no-op, and force=True must
# still start the thread for a test that deliberately wants to exercise it.

def test_start_background_checks_refresher_does_not_start_under_pytest(refresher_state_reset):
    taskmod = refresher_state_reset
    assert "PYTEST_CURRENT_TEST" in os.environ, "sanity: pytest always sets this while a test is running"
    taskmod._checks_refresher_started = False
    taskmod.start_background_checks_refresher()
    assert taskmod._checks_refresher_started is False, (
        "the refresher must not start under the test suite by default"
    )


def test_start_background_checks_refresher_backup_guard_via_sys_modules(refresher_state_reset, monkeypatch):
    """issue #592's REFRESHER CHECK follow-up — PYTEST_CURRENT_TEST is only
    set while a test is actively running, never during collection/import;
    confirmed (see start_background_checks_refresher's own docstring) that
    no code path in the CURRENT suite reaches this function before then, but
    "pytest" in sys.modules is a strictly broader, free backup for a future
    test file that might. Proven directly here by removing the narrower
    signal and confirming the broader one alone still blocks the start —
    not just that both together happen to work under a normal test run."""
    taskmod = refresher_state_reset
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert "pytest" in taskmod.sys.modules, "sanity: the pytest package is always importable/imported here"
    taskmod._checks_refresher_started = False
    taskmod.start_background_checks_refresher()
    assert taskmod._checks_refresher_started is False, (
        "the sys.modules backup guard alone must still block the start"
    )


def test_start_background_checks_refresher_force_true_still_starts(refresher_state_reset, monkeypatch, tmp_path):
    taskmod = refresher_state_reset
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "forced.db"))
    taskmod._checks_refresher_started = False
    started_threads = []
    orig_thread = taskmod.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    monkeypatch.setattr(taskmod.threading, "Thread", _tracking_thread)
    taskmod.start_background_checks_refresher(force=True)
    try:
        assert taskmod._checks_refresher_started is True
        assert len(started_threads) == 1
        assert started_threads[0].daemon is True
    finally:
        taskmod._checks_refresher_started = False   # don't leak a real thread's state into other tests


def test_start_background_checks_refresher_is_idempotent_when_forced(refresher_state_reset, monkeypatch, tmp_path):
    """A second call, even with force=True, must not start a second thread
    once one is already marked started — the pre-existing idempotency
    guard is untouched by the pytest-detection fix."""
    taskmod = refresher_state_reset
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "forced2.db"))
    taskmod._checks_refresher_started = True   # simulate an already-started refresher
    started_threads = []
    monkeypatch.setattr(
        taskmod.threading, "Thread",
        lambda *a, **k: started_threads.append(1) or taskmod.threading.Thread(*a, **k),
    )
    taskmod.start_background_checks_refresher(force=True)
    taskmod._checks_refresher_started = False
    assert started_threads == [], "already-started must stay a no-op regardless of force"


def test_checks_refresher_banner_renders_all_three_states(refresher_state_reset):
    """Direct coverage for webapp.app._checks_refresher_banner — never
    started (red), started-but-no-success-yet (amber), and healthy
    (green) — matching what admin_checks() actually feeds it via
    webapp.tasks.refresher_status()."""
    import time
    import webapp.app as appmod

    not_started = appmod._checks_refresher_banner(
        {"started": False, "last_success_at": None, "last_attempt_at": None, "last_error": None})
    assert "not started" in not_started
    assert "var(--alert)" in not_started

    starting_up = appmod._checks_refresher_banner(
        {"started": True, "last_success_at": None, "last_attempt_at": time.time(), "last_error": None})
    assert "hasn" in starting_up and "completed its first pass" in starting_up
    assert "#92400e" in starting_up   # amber text color

    healthy = appmod._checks_refresher_banner(
        {"started": True, "last_success_at": time.time(), "last_attempt_at": time.time(), "last_error": None})
    assert "last refreshed" in healthy
    assert "var(--seafoam)" in healthy

    failing = appmod._checks_refresher_banner(
        {"started": True, "last_success_at": time.time() - 999, "last_attempt_at": time.time(),
         "last_error": "RuntimeError: boom"})
    assert "failing" in failing
    assert "boom" in failing
    assert "var(--alert)" in failing
