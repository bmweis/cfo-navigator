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


def test_queue_count_already_exists(lib):
    # Backs the Archive Queue badge — pinning it's the right shape for tasks.py.
    # (The sibling "in_scope"/flagged_count check this test used to also cover
    # was removed in PR 4's "Remove content" retirement — see
    # linklib/db.py's articles.in_scope column comment.)
    assert lib.queue_count(status="pending") == 0


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
    assert "/admin/contacts" not in counts
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
    assert tasks.DOT_ONLY_HREFS == {"/admin/contacts", "/admin/tools/software/leads"}


def test_badge_for_href_renders_dot_for_all_or_none(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    assert appmod._badge_for_href("/admin/contacts", 3) == '<span class="task-badge-dot" aria-label="Unread"></span>'
    assert appmod._badge_for_href("/admin/tools/software/leads", 1) == '<span class="task-badge-dot" aria-label="Unread"></span>'
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
    counts = {"/admin/contacts": 2, "/admin/tools/software/leads": 1, "/admin/email-failures": 0}
    hrefs = ["/admin/contacts", "/admin/tools/software/leads", "/admin/email-failures"]
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
    # needs_review=0 (a brand-new tool otherwise defaults to 1, per add_tool's
    # own docstring) so the only open task in play here is the lead itself —
    # otherwise a lingering count_tools_needing_attention()>0 would keep the
    # global nav dot lit after the lead-specific dot clears below, unrelated
    # to what this test is actually about.
    tool_id = lib.add_tool("A", "desc", "https://a.example", [], approved=1, needs_review=0)
    lib.save_tool_lead(tool_id, "A", "Jane", "jane@x.com", "Acme", "50-200")
    lib.close()

    r1 = client.get("/admin")
    assert '<span class="task-badge-dot" aria-label="Unread"></span>' in r1.text

    client.get("/admin/tools/software/leads")   # unfiltered view clears it

    r2 = client.get("/admin")
    assert '<span class="task-dot"' not in r2.text


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
    assert counts["/admin/library/backfill-content"] == 1


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
# (`.admin-group[open] .group-badge{display:none;}`), so a collapsed-by-
# default group with something pending still shows its badge number,
# unhidden, right on the summary row. See CLAUDE.md.)

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
    OPENED — see the CSS rule `.admin-group[open] .group-badge{display:
    none;}` — so collapsed-by-default never hides a pending badge)."""
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
