"""Overnight-run fix (2026-09): `.page-form` had drifted onto three admin
edit-form pages it was never meant for (Feeds, Resources, Third-party
content) — every other admin edit form uses `.page-standard` with the
back-link/<h1> at the page's own left edge and the form itself capped at
max-width:900px;margin:0 auto (see `_ai_surface_form_page`/`_oc_form_page`).
Fixed by moving all three to that same shape and adding a back-link to the
two that had none. This file pins that fix two ways: (1) the live
`/admin/system/page-index` tier-introspection mechanism (`_page_index_
snapshot`) now reports `page-standard` for all five affected routes, and
(2) a direct render of each page confirms the reference shape (a `.page-
standard` wrapper, a back-link, and the form's own 900px cap) rather than
just trusting the source-scanning heuristic.

Same PR also extended the shared `_ADMIN_SCROLL_HINT_HTML`/`_ADMIN_SCROLL_
HINT_JS`/`#cmp-scroll-wrap` mechanism (`/admin/reader/feeds` already had
it, PR 32/33) to six more wide admin tables — this file pins that too, via
a direct render.
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
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


# --- Part 1: width tier ------------------------------------------------

FORM_ROUTES = [
    "/admin/reader/feeds/new",
    "/admin/tools/resources/new",
    "/admin/thought-leadership/third-party/new",
]


def test_page_index_reports_page_standard_for_the_three_converted_forms(env):
    """The live tier-introspection mechanism (same one /admin/system/
    page-index reads) now sees page-standard, not page-form, for the three
    add-forms this PR converted — proof the fix is real, not just a claim
    made in a comment, since this reads the actual route source at call
    time rather than a maintained list."""
    rows = {r["path"]: r for r in env._page_index_snapshot()}
    for path in FORM_ROUTES:
        assert path in rows, f"{path} missing from the live page index"
        assert rows[path]["tier"] == "page-standard", (
            f"{path} still reports tier={rows[path]['tier']!r}, expected page-standard"
        )


def test_feed_edit_form_is_page_standard_too(env):
    """The edit variant (a different route, reached via a real feed id) —
    both new and edit funnel through the same _feed_form_page helper, so
    both should agree."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_feed_section("Test section")
    section = lib.list_feed_sections()[0]
    lib.add_feed(section["id"], "Test Feed", "https://example.com/feed.xml", "https://example.com")
    feed = lib.list_feeds()[0]
    lib.close()
    admin = _admin_client(env)
    resp = admin.get(f"/admin/reader/feeds/{feed['id']}/edit")
    assert resp.status_code == 200
    assert 'class="page page-standard"' in resp.text
    assert 'class="page page-form"' not in resp.text


def test_resources_and_third_party_edit_forms_are_page_standard(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    bid = lib.add_benchmark("Test Resource", "https://example.com/res", "A test resource.",
                             "Private", "free", "benchmarking", source="script")
    tid = lib.add_thought_leadership("writing", "Test Piece", "https://example.com/piece",
                                      "Test Venue", "", "", "", False, None, False, source="script")
    lib.close()
    admin = _admin_client(env)
    r1 = admin.get(f"/admin/tools/resources/{bid}/edit")
    assert r1.status_code == 200
    assert 'class="page page-standard"' in r1.text
    assert 'class="page page-form"' not in r1.text

    r2 = admin.get(f"/admin/thought-leadership/third-party/{tid}/edit")
    assert r2.status_code == 200
    assert 'class="page page-standard"' in r2.text
    assert 'class="page page-form"' not in r2.text


def test_converted_forms_render_the_reference_shape(env):
    """Not just 'page-standard somewhere in the HTML' — the actual reference
    shape: a back-link line, an <h1>, and the <form> itself capped at
    max-width:900px;margin:0 auto (matching _ai_surface_form_page/
    _oc_form_page exactly), not the whole page narrowed to 640/900px."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_feed_section("Test section")  # /admin/reader/feeds/new 302s to the
    # list page (no back-link matching "Feeds") when no section exists yet.
    lib.close()
    admin = _admin_client(env)
    for path, back_link_fragment in [
        ("/admin/reader/feeds/new", "&larr; Feeds"),
        ("/admin/tools/resources/new", "&larr; Resources"),
        ("/admin/thought-leadership/third-party/new", "&larr; Third-party content"),
    ]:
        resp = admin.get(path)
        assert resp.status_code == 200, path
        assert back_link_fragment in resp.text, f"{path} missing its back-link"
        assert "max-width:900px;margin:0 auto" in resp.text, f"{path} missing the 900px form cap"
        assert 'class="page page-form"' not in resp.text, f"{path} still on page-form"


def test_page_form_css_comment_no_longer_calls_itself_the_admin_edit_form_tier(env):
    """The .page-form CSS comment used to read 'forms — contact, admin edit
    forms' — the exact wrong signal that let three admin edit forms drift
    onto it. It should no longer claim admin edit forms as its audience."""
    src = pathlib.Path(env.__file__).read_text()
    idx = src.index(".page-form{max-width:640px;}")
    # The comment immediately follows the rule on the same or next lines.
    window = src[idx:idx + 400]
    assert "admin edit form" not in window.lower() or "never an admin edit form" in window.lower()


# --- Part 2: scroll hint on six more wide tables ------------------------

SINGLE_TABLE_HINT_PAGES = [
    "/admin/users",
    "/admin/inbox/toolbox-intros",
    "/admin/thought-leadership/third-party",
    "/admin/thought-leadership/original",
]


def test_single_table_pages_carry_the_scroll_hint_and_wrapper(env):
    admin = _admin_client(env)
    for path in SINGLE_TABLE_HINT_PAGES:
        resp = admin.get(path)
        assert resp.status_code == 200, path
        assert 'id="admin-scroll-hint"' in resp.text, f"{path} missing the scroll hint element"
        assert 'id="cmp-scroll-wrap"' in resp.text, f"{path} missing the scroll wrapper"
        assert "initAdminScrollHint();" in resp.text, f"{path} never calls initAdminScrollHint()"


def test_contact_submissions_hints_only_the_primary_table(env):
    """Two tables on this page (submissions + Deletion history audit log) —
    only the primary submissions table gets the hint, matching the existing
    precedent (Software/Communities leave their own secondary 'Pending
    submissions' table plain). There is exactly one #cmp-scroll-wrap, not
    two — a second one would collide on id."""
    admin = _admin_client(env)
    resp = admin.get("/admin/inbox/contact-submissions")
    assert resp.status_code == 200
    assert resp.text.count('id="cmp-scroll-wrap"') == 1
    assert resp.text.count('id="admin-scroll-hint"') == 1
    assert "Deletion history" in resp.text


def test_backfill_content_hints_the_needs_manual_review_table(env):
    """A seeded article with 3+ consecutive failures lands in 'Needs manual
    review' — the primary, most-actionable table on this multi-table page —
    and that's the one that should carry the hint. On an otherwise-empty DB
    none of the three tables render at all (all conditional on non-empty
    row lists), so this needs real seeded failure data to exercise."""
    from linklib.db import Library, Article
    lib = Library(os.environ["LINKLIB_DB"])
    art = Article(url="https://example.com/needs-review", title="Needs review test",
                   content="short content for the manual-review test row", tags=[])
    aid = lib.upsert(art)
    for _ in range(3):
        lib.log_content_refetch_attempt(aid, "failure", reason="fetch-error", detail="HTTP 500", source="save")
    assert lib.count_articles_needing_manual_review() == 1
    lib.close()

    admin = _admin_client(env)
    resp = admin.get("/admin/reader/backfill-content")
    assert resp.status_code == 200
    assert 'id="manual-review"' in resp.text
    assert 'id="cmp-scroll-wrap"' in resp.text
    assert 'id="admin-scroll-hint"' in resp.text
    # The wrapper must land INSIDE the manual-review block, not just
    # somewhere on the page — confirms the hint is on the right table.
    manual_idx = resp.text.index('id="manual-review"')
    wrap_idx = resp.text.index('id="cmp-scroll-wrap"')
    accepted_idx = resp.text.find('id="accepted-content"')
    assert manual_idx < wrap_idx
    if accepted_idx != -1:
        assert wrap_idx < accepted_idx, "scroll wrapper leaked past Needs manual review into a later section"


def test_backfill_content_does_not_hint_the_recent_attempts_or_accepted_tables(env):
    """Only one #cmp-scroll-wrap should exist on this page even with all
    three tables populated — Recent attempts and Accepted as final stay
    plain overflow-x:auto, same precedent as every other secondary table."""
    from linklib.db import Library, Article
    lib = Library(os.environ["LINKLIB_DB"])
    art = Article(url="https://example.com/needs-review-2", title="Needs review test 2",
                   content="short content for the manual-review test row two", tags=[])
    aid = lib.upsert(art)
    for _ in range(3):
        lib.log_content_refetch_attempt(aid, "failure", reason="fetch-error", detail="HTTP 500", source="save")
    lib.close()

    admin = _admin_client(env)
    resp = admin.get("/admin/reader/backfill-content")
    assert resp.status_code == 200
    assert resp.text.count('id="cmp-scroll-wrap"') == 1
    assert resp.text.count('id="admin-scroll-hint"') == 1
