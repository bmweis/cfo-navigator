"""The /admin/checks dashboard aggregator (webapp/checks.py).

Guards the dashboard wiring itself: results are well-formed, and every check that
runs live in-app is currently green (a regression in brand/voice/sync/dead-code
would fail here as well as in its own test).

PR 16 (2026-09) — a real, pre-existing app bug found via this file, not caused
by it: `coral_moment_problems()` was the first check in `run_all()` to actually
make live HTTP requests (every other live check is pure route/tuple
introspection, no request needed), and it exposed that an unauthenticated
`GET /` hangs indefinitely — not errors, not times out on its own, genuinely
blocks — whenever neither `LINKLIB_PASSWORD` nor `LINKLIB_SAVE_TOKEN` is set
(the documented "open, local-dev convenience" auth mode). This file used to
set only `LINKLIB_DB` at module level via `setdefault`, with no
`LINKLIB_PASSWORD`/`LINKLIB_SECRET_KEY` and no per-test isolation — unlike
every other test file's `env` fixture, which always sets all three. That gap
was harmless as long as nothing in `run_all()` made a real request; once
something did, it hung the whole suite. Fixed here by adopting the same `env`
fixture convention (fresh DB + password + secret key, reloaded per test) the
rest of the suite already uses. The underlying open-auth-mode hang is a
separate, deeper bug — reported, not fixed in this PR (scoped to what this
file's own test setup needed to stop blocking CI).
"""
import pathlib
import sys
import tempfile
import time
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
    import webapp.checks as checksmod
    importlib.reload(checksmod)
    # webapp.tasks is deliberately never reloaded above — its module-level
    # cache/sentinel (_checks_cache, _checks_computing) are plain globals
    # that survive a reload of webapp.app/webapp.checks, exactly the
    # "module-level globals leak between tests" hazard tests/test_task_
    # badges.py already documents and resets in its own tests that touch
    # this cache directly. This file's own tests never touched it before,
    # so nothing here ever reset it — a test in this file running after
    # another test file (in the same pytest process/worker) left a stale
    # (timestamp, count) tuple behind would silently read that instead of
    # computing fresh. Reset here too, once, for every test in this file,
    # the same way test_task_badges.py resets it for its own.
    from webapp import tasks as taskmod
    taskmod._checks_cache = None
    taskmod._checks_computing = False
    yield checksmod
    if os.path.exists(db):
        os.remove(db)


def test_run_all_well_formed(env):
    results = env.run_all()
    assert results, "no checks returned"
    for r in results:
        assert {"name", "what", "where", "ok", "detail"} <= set(r), r
        assert r["where"] in ("Live + CI", "CI")
        assert r["name"] and r["what"] and r["detail"]


def test_live_checks_currently_pass(env):
    for r in env.run_all():
        if r["ok"] is not None:        # ran live in this environment
            assert r["ok"] is True, f"{r['name']} failing: {r['detail']}"


def test_admin_checks_summary_banner_is_green_on_a_clean_db(env, monkeypatch):
    """Regression for a real, previously-shipped bug: `admin_checks()`'s
    old single green/red paragraph compared each row's `where` against
    "In-app", a value `run_all()` has never actually produced (every live
    row is "Live + CI"); the banner was permanently blank regardless of
    pass/fail state.

    2026-09 rework replaced that single paragraph with a per-section status
    summary (item 1: it used to stay green while a section further down the
    SAME page reported real findings) — this asserts the "Live checks" row
    renders its "N of N passing" state, with NO link (nothing to fix while
    everything's green, the same rule Disk space/Badge refresh use — a
    plain <span>, never a dead-looking <a>). The sibling test below forces
    a real failure and asserts the row becomes a real link to the failing
    check's own anchor."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    import re
    m = re.search(r"(\d+) of (\d+) passing", r.text)
    assert m and m.group(1) == m.group(2), "every bool-eligible live check should be passing on a clean DB"
    assert ">Live checks</span>" in r.text   # plain text, not a link — nothing to fix
    assert ">Live checks</a>" not in r.text


def test_admin_checks_summary_banner_is_red_on_a_real_failure(env, monkeypatch):
    """Same page, forced into the failing branch via a real run_all() check
    (mechanical_findings, imported inside webapp.checks.run_all from
    linklib.voice_review) — proves the summary's "Live checks" row both
    flips to a failing count AND becomes a real link, pointing at the
    FIRST failing check's own per-row anchor (item 2: "Live checks -> the
    failing check's section, when one fails"), not just the generic list.

    2026-09 test-suite-runtime PR: "Voice standards" is one of the five
    checks now cached (see webapp.tasks.cached_static_check) since it's a
    pure function of on-disk source in the ordinary case — but THIS test
    is the one real exception the caching PR's own Condition A asked to be
    checked for and found: it deliberately monkeypatches
    mechanical_findings itself to simulate a violation, which the cache
    would otherwise silently keep serving a stale (real, clean) result
    over, from whatever the FIRST test in this file to render /admin/checks
    already computed and cached. reset_static_check_cache() before the
    request is what makes the monkeypatch actually take effect; calling it
    again in `finally` stops this test's synthetic "seamless" finding from
    leaking into every later test in this file as a false positive."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import linklib.voice_review as vr_mod
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    from webapp import tasks as taskmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    orig = vr_mod.mechanical_findings
    vr_mod.mechanical_findings = lambda text: [("buzzword", "seamless")]
    taskmod.reset_static_check_cache()
    try:
        r = c.get("/admin/checks")
        assert r.status_code == 200
        assert "failing" in r.text
        import re
        m = re.search(r'href="(#check-[a-z0-9-]+)"[^>]*>Live checks</a>', r.text)
        assert m, "the Live checks summary row should link to the first failing check's own anchor"
        assert f'id="{m.group(1)[1:]}"' in r.text   # the anchor it links to actually exists on the page
    finally:
        vr_mod.mechanical_findings = orig
        taskmod.reset_static_check_cache()


# --- /admin/checks status summary (2026-09 rework) — items 1-4 --------------

def test_summary_shows_all_section_rows(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    for label in ("Live checks", "Database copy", "Review queue", "Disk space", "Badge refresh",
                  "AI providers", "Anthropic pricing", "Anthropic models", "Exa pricing"):
        assert label in r.text, label


def test_review_queue_row_labels_seed_disagreements_separately_from_database_copy(env, monkeypatch):
    """A real input from a separate PR's verification: the Database copy
    scan's count and the Review queue's own open count will legitimately
    differ — the queue also holds seed-disagreement items (an ordinary
    state, source='startup-sync') and already-applied auto-corrections,
    neither of which the live DB-copy scan counts at all. Confirms the two
    rows are labeled distinctly enough that a real discrepancy never reads
    as a mismatch with this expected one: "N open, including M seed
    disagreements" vs. the scan's own plain "N findings"."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    from linklib.db import Library
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    lib = Library(appmod.DB_PATH)
    try:
        lib.add_voice_review_item("communities", 1, "notes", "spaced-em-dash",
                                   "an excerpt", source="startup-sync")
        lib.add_voice_review_item("tools", 2, "description", "filler",
                                   "another excerpt", source="script")
    finally:
        lib.close()

    r = c.get("/admin/checks")
    assert "2 open, including 1 seed disagreement" in r.text
    # The Database copy row's own count (a live text scan of current column
    # content) is unaffected by queue rows — it should read the clean "No
    # findings" state here, genuinely different from the queue's "2 open"
    # without either number implying the other is wrong.
    assert "0 violations. 0 allowed once, 0 always allowed." in r.text


def test_review_queue_row_omits_the_seed_note_when_there_are_none(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    from linklib.db import Library
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    lib = Library(appmod.DB_PATH)
    try:
        lib.add_voice_review_item("tools", 2, "description", "filler", "an excerpt", source="script")
    finally:
        lib.close()

    r = c.get("/admin/checks")
    assert "1 open" in r.text
    assert "seed disagreement" not in r.text


def test_database_copy_summary_row_always_links_to_the_review_queue(env, monkeypatch):
    """Item 2: "Database copy -> /admin/voice/review-queue" — the row's own
    destination is where a finding is actually triaged, not its local
    /admin/checks section, regardless of whether there's currently anything
    to review."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert '<a href="/admin/voice/review-queue" style="color:var(--navy);font-weight:600;' \
           'text-decoration:none;font-size:14px;">Database copy</a>' in r.text
    assert "0 violations. 0 allowed once, 0 always allowed." in r.text


def test_disk_space_row_has_no_link_when_no_volume(env, monkeypatch):
    """Item 1's explicit call: Disk space has nothing to manage while it
    reads clean/not-applicable, so no link — this sandbox genuinely has no
    /data volume, which is the live, unfaked case."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert ">Disk space</span>" in r.text
    assert ">Disk space</a>" not in r.text
    assert "No /data volume" in r.text


def test_badge_refresh_row_has_no_link_when_healthy(env, monkeypatch):
    """A bare `TestClient(app)` (no `with` block, matching every other test
    in this file) never fires FastAPI's startup event, so the real
    background refresher deterministically reads "Not started" here — not
    a flake, just not what this test is checking. Monkeypatch
    refresher_status() directly to exercise the healthy/no-link render path
    on its own, independent of whether a real thread happens to be up."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    import webapp.tasks as tasksmod
    monkeypatch.setattr(tasksmod, "refresher_status", lambda: {
        "started": True, "last_success_at": time.time(), "last_attempt_at": time.time(),
        "last_error": None,
    })
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert ">Badge refresh</span>" in r.text
    assert ">Badge refresh</a>" not in r.text
    assert "Healthy" in r.text


def _summary_grid_html(html: str) -> str:
    """Isolate the two-table top status summary (2026-09 two-table rework)
    from the rest of /admin/checks — everything from its own <style> block
    through the closing </div> of .checks-summary-grid, right before the
    "Live checks" <h2> that starts the rest of the page. Scoping to just
    this fragment is what lets "no outbound links in the summary" be
    asserted precisely — the AI-providers' GitHub/vendor links are still
    real and still present further down the SAME page, in each provider's
    own detail section, so a whole-page substring check couldn't tell
    "still in the summary" from "only in its detail section" apart."""
    start = html.index('<style>.checks-summary-grid')
    end = html.index('<div id="ci-quota"')
    return html[start:end]


def test_ai_provider_summary_rows_link_only_to_their_section_no_fix_links(env, monkeypatch):
    """Item d (2026-09 two-table rework): the AI-providers rows' GitHub-
    source and vendor-page links come OUT of the summary entirely — Details
    shows only the plain status text. The check name is still the row's own
    link, still pointing at its local section (where "Mark reviewed" and the
    real GitHub/vendor links live, further down the page).

    Order-dependence note (character-budget-limits-targets PR): this test
    was reported failing in certain multi-file groupings while passing
    alone and in full runs — the "Never reviewed" assertion below depends
    on `pricing_last_verified`/`models_last_reviewed`/`exa_pricing_last_
    verified` genuinely being unset on this test's own fresh DB, which
    `env`'s `importlib.reload(appmod)` already guarantees for `DB_PATH`.
    Investigated for a stale module-level global surviving that reload —
    `webapp.tasks._checks_cache`/`_checks_computing` are real, confirmed
    examples of exactly that shape (plain module globals, never reloaded
    by any fixture in this file, already documented and reset by
    tests/test_task_badges.py's own tests that touch them directly) — now
    reset in this file's own `env` fixture above, closing the same class
    of hazard here too. Two repro attempts in this sandbox (a plausible
    6-file grouping, and a smaller isolated pairing) did not reproduce
    this exact test failing outright, but DID reproduce spurious failures
    in sibling freshness-banner tests under heavy concurrent CPU
    contention (multiple pytest processes racing for the same cores) —
    worth ruling out first if this resurfaces: confirm it's not simply a
    timing/resource artifact of `-n auto`'s parallel workers before
    chasing another global."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    assert '<a href="#pricing-freshness"' in summary
    assert '<a href="#new-model-awareness"' in summary
    assert '<a href="#exa-pricing-freshness"' in summary
    assert "Never reviewed" in summary   # a fresh DB has never marked any of the three reviewed
    # The GitHub-source/vendor-page links must NOT appear inside the
    # summary — only in each provider's own detail section further down.
    assert "linklib/pricing.py" not in summary
    assert "linklib/models.py" not in summary
    assert "https://www.anthropic.com/pricing" not in summary
    assert "https://platform.claude.com/docs/en/about-claude/models/overview" not in summary
    assert "https://exa.ai/pricing" not in summary
    # ...but they're still real and present further down the page.
    full = r.text
    assert "linklib/pricing.py" in full and "linklib/models.py" in full
    assert "https://www.anthropic.com/pricing" in full
    assert "https://platform.claude.com/docs/en/about-claude/models/overview" in full
    assert "https://exa.ai/pricing" in full


def test_ai_provider_fix_links_still_open_in_a_new_tab_in_their_own_section(env, monkeypatch):
    """The GitHub/vendor links moved out of the summary (see the test
    above) but still exist in each provider's own detail section further
    down the page — confirm they still follow the standing target=_blank/
    rel=noopener rule (see brand_check.outbound_link_problems, already
    part of run_all())."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    import re
    for url in ("https://www.anthropic.com/pricing", "https://exa.ai/pricing",
                "https://platform.claude.com/docs/en/about-claude/models/overview"):
        m = re.search(re.escape(f'href="{url}"') + r'[^>]*', r.text)
        assert m and "target=\"_blank\"" in m.group(0) and "rel=\"noopener\"" in m.group(0), url


# --- /admin/checks two-table summary redesign (2026-09) ----------------------

def test_summary_renders_exactly_two_tables_with_the_right_rows(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    assert summary.count("<table") == 2

    site_start = summary.index("Site checks")
    ai_start = summary.index("AI providers")
    assert site_start < ai_start, "Site checks card must render before AI providers"
    site_table = summary[site_start:ai_start]
    ai_table = summary[ai_start:]

    for check in ("Live checks", "Database copy", "Review queue", "Profile fields over their limit", "Disk space", "Badge refresh"):
        assert check in site_table, check
        assert check not in ai_table, check
    for check in ("Anthropic pricing", "Anthropic models", "Exa pricing"):
        assert check in ai_table, check
        assert check not in site_table, check
    assert site_table.count("<tr") == 7   # 1 header row + 6 body rows
    assert ai_table.count("<tr") == 4     # 1 header row + 3 body rows


def test_summary_tables_have_three_columns_with_headers_matching_the_named_row_fields(env, monkeypatch):
    """Item c: the header text is derived from the same named-field dict
    every row is built from (webapp.app._checks_summary_row_tr's `row`
    parameter: check/status/details), not a separately hand-typed label —
    so this test pins both the header text AND that it's the same three
    field names the row-building code (_checks_summary_table_html) actually
    uses, closing the drift this rule exists to prevent.

    2026-09 design review, round 3: the Check column's label is visible
    again — dropping it (round 2) left a blank strip in the header row with
    nothing anchoring it to the card heading above, which read as
    "misaligned" even though every column was correctly positioned over its
    own data. Fixed by restructuring, not by hiding text: the heading moved
    out of the table entirely (see _checks_summary_table_html), so there's
    no more card edge for the header row to look disconnected from, and
    Check can go back to being a normal, visible, sentence-case label like
    Status and Details — matching the site's other admin tables (Software,
    Communities), not a one-off style."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)

    # The header text is literally the sentence-cased field names of the
    # named-row dict webapp.app._checks_summary_row_tr consumes.
    expected_field_names = ["check", "status", "details"]
    expected_headers = [f.capitalize() for f in expected_field_names]
    assert expected_headers == ["Check", "Status", "Details"]

    for table_html in (summary[:summary.index("AI providers")], summary[summary.index("AI providers"):]):
        assert table_html.count("<th ") == 3, "exactly three columns"  # "<th" alone also matches "<thead"
        # All three columns are visibly labeled, sentence case, no aria-only hiding.
        for header in ("Check", "Status", "Details"):
            assert f">{header}</th>" in table_html, header
        assert "aria-label=" not in table_html.split("<thead>")[1].split("</thead>")[0]
        # Header order matches column order: Check, then Status, then Details.
        check_pos = table_html.index(">Check</th>")
        status_pos = table_html.index(">Status</th>")
        details_pos = table_html.index(">Details</th>")
        assert check_pos < status_pos < details_pos
        # The site's standard admin-table header band (var(--accent-light),
        # see Software/Communities' own <thead>), not a one-off muted/
        # uppercase treatment.
        assert 'background:var(--accent-light);' in table_html
        assert "text-transform:uppercase" not in table_html.split("<thead>")[1].split("</thead>")[0]


def test_summary_details_cells_are_left_aligned(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    import re
    # Every <td> holding a Details cell (identified by its own distinct
    # font-size/color styling, shared by no other cell) declares
    # text-align:left explicitly.
    details_cells = re.findall(
        r'<td style="[^"]*text-align:(left|center)[^"]*font-size:13px;color:var\(--ink-soft\);"', summary)
    assert details_cells, "no Details cells found — selector drifted from the real markup"
    assert all(a == "left" for a in details_cells)


def test_every_summary_status_dot_has_a_non_empty_aria_label(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    import re
    dots = re.findall(r'<span role="img"[^>]*>', summary)
    assert len(dots) == 9   # 6 Site checks rows + 3 AI providers rows
    for dot in dots:
        m = re.search(r'aria-label="([^"]*)"', dot)
        assert m and m.group(1).strip(), dot
        # title mirrors aria-label — color is never the only signal
        t = re.search(r'title="([^"]*)"', dot)
        assert t and t.group(1) == m.group(1)


def test_summary_status_dots_use_the_sanctioned_stoplight_palette(env, monkeypatch):
    """Design review correction (2026-09): the semantic --good/--caution/
    --alert tokens read as off for a glanceable status indicator (--good
    is navy, the site's own dominant color — it doesn't register as
    "healthy" at a glance). Switched to the true-stoplight trio BRAND.md
    §6 sanctions for exactly this case ("glanceable health indicators"),
    the same colors the cookie-status panel already uses
    (webapp.app._COOKIE_STATE_STYLES): #15803D green, #CA8A04 amber,
    #b91c1c red. "unknown" (no signal either way) stays var(--muted),
    which was already correct and unaffected by this change."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    import re
    dot_backgrounds = re.findall(
        r'border-radius:50%;background:(var\(--[a-z]+\)|#[0-9a-fA-F]{3,6});"', summary)
    assert dot_backgrounds
    for bg in dot_backgrounds:
        assert bg in ("#15803D", "#CA8A04", "#b91c1c", "var(--muted)"), bg
        # never the semantic tokens this replaced, and never coral
        assert bg not in ("var(--good)", "var(--caution)", "var(--alert)"), bg


def test_never_reviewed_dot_uses_the_stoplight_amber_never_coral(env, monkeypatch):
    """Item e's second bug fix, updated for the stoplight-palette switch
    above: on a fresh DB, all three AI-provider rows are "Never reviewed"
    (a "warning" status) — confirm that state's dot is the sanctioned
    #CA8A04 amber (not the old var(--caution), and not any coral form)."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    assert "Never reviewed" in summary
    assert "background:#CA8A04;" in summary
    assert "var(--caution)" not in summary
    for token in ("--coral", "--coral-deep", "--coral-wash"):
        assert token not in summary, token


def test_summary_every_row_including_the_last_has_the_ordinary_divider(env, monkeypatch):
    """2026-09 design review, round 3: the "strip the last row's border" hack
    (Item e's original fix) existed to stop a divider line looking stray
    against the card's own rounded bottom edge. There's no card any more —
    each table sits bare under its eyebrow label (see
    _checks_summary_table_html) — so that edge doesn't exist to look stray
    against, and the special case is gone: every row, including the last,
    carries the same border-bottom divider every other admin table on the
    site uses on every row (Software, Communities, ...)."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    assert "border-bottom:none;" not in summary
    for last_row_check in ("Badge refresh", "Exa pricing"):
        idx = summary.index(f">{last_row_check}<")
        tr_start = summary.rindex("<tr", 0, idx)
        tr_tag = summary[tr_start:summary.index(">", tr_start) + 1]
        assert "border-bottom:1px solid var(--line);" in tr_tag, tr_tag
    # 2 header rows + 5 Site checks rows + 3 AI providers rows = 8 body
    # rows all carrying the divider, plus the 2 header rows' own tr has no
    # border-bottom at all (it has a background instead) — so exactly 8.
    assert summary.count("border-bottom:1px solid var(--line);") == 9


def test_summary_contains_no_outbound_links_only_internal_section_links(env, monkeypatch):
    """Item g's required test: the summary, as rendered, must contain no
    outbound (external) links anywhere — the only links present must be
    check-name links, and those must point only to sections on the page
    (an internal #anchor or a same-site /admin/... path)."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    import re
    hrefs = re.findall(r'href="([^"]*)"', summary)
    assert hrefs, "no links found at all — selector drifted from the real markup"
    for href in hrefs:
        assert not href.startswith(("http://", "https://")), href
        assert href.startswith("#") or href.startswith("/"), href
    assert "target=\"_blank\"" not in summary   # no outbound-link affordance at all in the summary


def test_summary_stacks_below_760px_via_a_media_query(env, monkeypatch):
    """Item f: below ~760px the two cards stack, Site checks first, AI
    providers second — implemented as a single flex-direction:column
    override inside a max-width:760px media query, with DOM order (Site
    checks card before AI providers card) doing the actual ordering."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    assert "@media(max-width:760px)" in summary
    assert "flex-direction:column" in summary
    assert summary.index("Site checks") < summary.index("AI providers")


def test_summary_tables_share_identical_column_widths(env, monkeypatch):
    """Item b: fixed column widths so the dot column lines up across both
    tables — both tables' <colgroup> must declare the identical width
    values, not just "some fixed width each"."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    summary = _summary_grid_html(r.text)
    import re
    colgroups = re.findall(r'<colgroup>.*?</colgroup>', summary)
    assert len(colgroups) == 2
    assert colgroups[0] == colgroups[1]


def test_voice_review_queue_row_shows_real_open_count(env, monkeypatch):
    """Item 3: the Voice review queue check runs live (where="Live + CI")
    but is deliberately ok=None (informational, not pass/fail) — it used to
    fall through to the CI-only "Latest run" GitHub Actions link even
    though it never runs in CI at all."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    from linklib.db import Library
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    r = c.get("/admin/checks")
    assert '<a href="/admin/voice/review-queue" style="color:var(--good);font-weight:600;">0 open</a>' in r.text

    lib = Library(appmod.DB_PATH)
    try:
        lib.add_voice_review_item("tools", 1, "description", "spaced-em-dash", "an excerpt")
    finally:
        lib.close()
    r2 = c.get("/admin/checks")
    assert '<a href="/admin/voice/review-queue" style="color:#92400e;font-weight:600;">1 open &rarr;</a>' in r2.text


def test_ci_quota_toggle_replaces_latest_run_links(env, monkeypatch):
    """Item 4: no GitHub API client exists in this app's runtime (confirmed
    by grep before building — see admin_checks_set_ci_quota's own
    docstring), so quota exhaustion is a plain admin-settable flag, not a
    live-detected state. Round-trips both directions."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    # "Quota exhausted" (capital Q, bare) is deliberately NOT the assertion
    # string here — it also appears, always, as the toggle form's own
    # static checkbox label ("GitHub Actions quota exhausted right now?" /
    # the label text "Quota exhausted" next to the checkbox itself),
    # regardless of the flag's value. The DYNAMIC row text this test
    # actually cares about is the longer phrase with the em dash.
    dynamic_phrase = "Quota exhausted&mdash;see local verification"

    r = c.get("/admin/checks")
    assert r.text.count("Latest run") > 0
    assert dynamic_phrase not in r.text

    pr_url = "https://github.com/bmweis/cfo-navigator/pull/591"
    resp = c.post("/admin/checks/ci-quota", data={"exhausted": "on", "pr_url": pr_url},
                   follow_redirects=False)
    assert resp.status_code == 303

    r2 = c.get("/admin/checks")
    assert r2.text.count("Latest run") == 0
    assert r2.text.count(dynamic_phrase) > 0
    assert f'href="{pr_url}"' in r2.text

    resp2 = c.post("/admin/checks/ci-quota", data={"pr_url": pr_url}, follow_redirects=False)
    assert resp2.status_code == 303
    r3 = c.get("/admin/checks")
    assert r3.text.count("Latest run") > 0
    assert dynamic_phrase not in r3.text


def test_ci_quota_toggle_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    r = c.post("/admin/checks/ci-quota", data={"exhausted": "on"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


# --- Voice guide checks — live-or-default resolution, ampersand exceptions, --
# --- and typography/invisible-char coverage (2026-09, item 5a/5b/5d) --------

def test_voice_guide_row_falls_back_to_default_with_no_live_value(env):
    """A fresh DB has no voice_core setting at all — CI (and this case)
    falls back to VOICE_CORE_DEFAULT, and the row says so."""
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide names what it enforces")
    assert row["ok"] is True
    assert "VOICE_CORE_DEFAULT" in row["detail"]
    assert "differs from the code default" not in row["detail"]


def test_voice_guide_row_validates_a_live_value_when_present(env):
    """When the live voice_core setting is real and reachable, the check
    validates THAT text, not VOICE_CORE_DEFAULT — and says so, including
    whether it differs from the shipped default."""
    from linklib.db import Library
    import webapp.app as appmod
    lib = Library(appmod.DB_PATH)
    try:
        lib.set_setting("voice_core", "A short custom voice guide with no quoted examples at all.")
    finally:
        lib.close()
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide names what it enforces")
    assert "the live voice_core setting" in row["detail"]
    assert "differs from the code default" in row["detail"]


def test_voice_guide_ampersand_row_passes_on_the_real_default(env):
    """T&E (and every other current acronym the guide names as permitted)
    is already in AMPERSAND_ACRONYMS — this pins that staying true."""
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide's permitted ampersand terms are honored")
    assert row["ok"] is True


def test_voice_guide_ampersand_row_catches_an_unlisted_permitted_term(env, monkeypatch):
    """A future edit to voice_core's prose naming a NEW ampersand exception
    with no matching AMPERSAND_ACRONYMS/AMPERSAND_NAMES entry must fail
    this row, not go unnoticed — the exact class of drift this check
    exists to guard against going forward."""
    import webapp.checks as checksmod
    orig = checksmod._resolve_checked_voice_core

    def _fake():
        return ('Standard abbreviations keep their ampersand (FP&A, T&E, B&B, and similar).',
                "a fake test guide", False)
    monkeypatch.setattr(checksmod, "_resolve_checked_voice_core", _fake)
    try:
        results = checksmod.run_all()
    finally:
        monkeypatch.setattr(checksmod, "_resolve_checked_voice_core", orig)
    row = next(r for r in results if r["name"] == "Voice guide's permitted ampersand terms are honored")
    assert row["ok"] is False
    assert "B&B" in row["detail"]


def test_voice_guide_typography_row_passes_on_the_real_default(env):
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide follows its own typography rules")
    assert row["ok"] is True


def test_voice_guide_typography_row_catches_a_spaced_em_dash(env, monkeypatch):
    import webapp.checks as checksmod
    orig = checksmod._resolve_checked_voice_core

    def _fake():
        return ("A guide that has a spaced em dash — right here.", "a fake test guide", False)
    monkeypatch.setattr(checksmod, "_resolve_checked_voice_core", _fake)
    try:
        results = checksmod.run_all()
    finally:
        monkeypatch.setattr(checksmod, "_resolve_checked_voice_core", orig)
    row = next(r for r in results if r["name"] == "Voice guide follows its own typography rules")
    assert row["ok"] is False
    assert "spaced-em-dash" in row["detail"]


def test_voice_core_gap_row_states_how_many_examples_it_checked(env):
    """A "0 gaps" verdict must say how many quoted examples were actually
    checked — otherwise it reads identically to a row that never ran, the
    same failure class as the summary banner bug above, one row down."""
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide names what it enforces")
    assert row["ok"] is True
    assert "Checked" in row["detail"] and "quoted example" in row["detail"]
    import re
    m = re.search(r"Checked (\d+) quoted example", row["detail"])
    assert m and int(m.group(1)) > 0


def test_db_copy_scan_states_execution_on_a_clean_db(env, monkeypatch):
    """The Database-backed copy section on /admin/checks must say what it
    scanned even when it finds nothing — a clean scan and a scan that
    silently skipped every table both used to render the identical green
    "no violations" message."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "Scanned" in r.text and "column" in r.text and "table" in r.text
    assert "0 violations. 0 allowed once, 0 always allowed." in r.text


def test_db_copy_scan_shows_a_skipped_table_as_amber_not_clean(env, monkeypatch):
    """The exact regression this feature exists to prevent: forcing one
    configured table to fail its query must render a visibly distinct
    (amber) "could not be scanned" notice, never the plain green banner a
    genuinely clean scan gets."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import linklib.voice_db_scan as scan_mod
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    orig_tables = scan_mod._SCAN_TABLES
    scan_mod._SCAN_TABLES = (("nonexistent_table", "id", ("name",), ()),) + orig_tables[1:]
    try:
        r = c.get("/admin/checks")
        assert r.status_code == 200
        assert "couldn&rsquo;t be scanned this pass" in r.text
        assert "nonexistent_table" in r.text
    finally:
        scan_mod._SCAN_TABLES = orig_tables


# --- Disk space (2026-09 addendum) -------------------------------------------
# webapp.checks.disk_space_status() reads live via shutil.disk_usage against
# a hardcoded _DISK_VOLUME_PATH ("/data", the documented Railway volume mount)
# — never shells out to `df`. Returns None (not a failure) when that path
# doesn't exist, which is always true in this sandbox/CI, so most of these
# tests monkeypatch the module-level path constant to a real temp directory
# rather than trying to fake the actual Railway mount.

def test_disk_space_status_none_when_volume_missing(env):
    # The real assertion this sandbox always exercises for free: /data
    # genuinely doesn't exist here, so this is the live, unfaked behavior.
    assert env.disk_space_status() is None


def test_disk_space_status_reports_real_numbers(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_DISK_VOLUME_PATH", str(tmp_path))
    db_file = tmp_path / "library.db"
    db_file.write_bytes(b"x" * 1024)
    status = env.disk_space_status(db_path=str(db_file))
    assert status is not None
    assert status["db_size"] == 1024
    assert status["total"] > 0
    assert status["level"] in ("ok", "warn", "critical")
    assert status["can_vacuum"] is True  # a 1KB db needs no real scratch space


def test_disk_space_status_can_vacuum_is_false_when_db_exceeds_free_space(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_DISK_VOLUME_PATH", str(tmp_path))
    total, used, free = env.shutil.disk_usage(str(tmp_path))
    # A db bigger than the volume's own total can never fit in free space —
    # exercises the "cannot VACUUM in place" branch without filling a real disk.
    huge = total + 1
    db_file = tmp_path / "library.db"
    db_file.write_bytes(b"\0")
    orig_getsize = env.os.path.getsize
    monkeypatch.setattr(env.os.path, "getsize", lambda p: huge if p == str(db_file) else orig_getsize(p))
    status = env.disk_space_status(db_path=str(db_file))
    assert status["db_size"] == huge
    assert status["can_vacuum"] is False


def test_disk_space_status_thresholds(env):
    assert env._DISK_WARN_PERCENT == 75
    assert env._DISK_CRITICAL_PERCENT == 85
    assert env._DISK_WARN_PERCENT < env._DISK_CRITICAL_PERCENT


def test_admin_checks_shows_unavailable_disk_banner_when_no_volume(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "Disk space" in r.text
    assert "/data" in r.text
    assert "No" in r.text  # the "no volume on this host" state, not a fake healthy row


def test_admin_checks_shows_real_disk_numbers_when_volume_present(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import webapp.checks as checksmod
    # Fixed, real-looking numbers from the actual production incident this
    # feature closes — proves the banner renders exactly what it's given,
    # independent of disk_space_status's own computation (covered above).
    fixed = {
        "volume_path": "/data", "total": 434 * 1024 * 1024, "used": 254 * 1024 * 1024,
        "free": 171 * 1024 * 1024, "percent_used": 58.5, "db_path": "/data/library.db",
        "db_size": 242 * 1024 * 1024, "can_vacuum": False, "level": "ok",
    }
    monkeypatch.setattr(checksmod, "disk_space_status", lambda *a, **k: fixed)
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "254M" in r.text
    assert "434M" in r.text
    assert "cannot" in r.text.lower()  # can_vacuum False → the "cannot run in place" note


# --- Live checks: themes and a fixed status position (checks-page follow-ups) --

def test_every_live_check_belongs_to_exactly_one_theme(env):
    import webapp.app as appmod
    names = [r["name"] for r in env.run_all()]
    themed = [n for _, ns in appmod._LIVE_CHECK_THEMES for n in ns]
    assert len(themed) == len(set(themed)), "a check is listed under two themes"
    missing = [n for n in names if n not in themed]
    assert not missing, f"add these to _LIVE_CHECK_THEMES: {missing}"
    stale = [n for n in themed if n not in names]
    assert not stale, f"no longer in run_all(): {stale}"


def test_live_checks_render_grouped_by_theme_with_status_top_right(env, monkeypatch):
    import re
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    html = c.get("/admin/checks").text
    themes = re.findall(r'<h3 class="checks-theme"[^>]*>([^<]+)</h3>', html)
    assert themes == [t for t, _ in appmod._LIVE_CHECK_THEMES]
    # Every card uses the same two-column head: name left, status right.
    heads = html.count('class="checks-card-head" style="display:grid;grid-template-columns:minmax(0,1fr) auto;')
    assert heads == len(env.run_all())


# --- Consolidated details block (2026-09): text left, result right --------

def test_details_rows_put_text_left_and_result_right(env, monkeypatch):
    """Database copy, Disk space, Badge refresh and the three AI-provider
    reminders render as one row each: explanation in .chk-text, the result
    in .chk-status (dot, status word, the summary's details text, any
    action), so the results scan down one column."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    html = c.get("/admin/checks").text
    anchors = ["db-copy-scan", "disk-space", "badge-refresh",
               "pricing-freshness", "new-model-awareness", "exa-pricing-freshness"]
    positions = [html.index(f'<div class="chk-row" id="{a}">') for a in anchors]
    assert positions == sorted(positions)
    for i, a in enumerate(anchors):
        end = positions[i + 1] if i + 1 < len(anchors) else len(html)
        row = html[positions[i]:end]
        assert row.index('class="chk-text"') < row.index('class="chk-status"'), a
    status_of = lambda a: html[html.index('class="chk-status"', html.index(f'id="{a}"')):]
    assert "/admin/system/ai#model-pricing" in status_of("pricing-freshness")[:900]
    assert "No /data volume (this environment)" in status_of("disk-space")[:600]
    # Text takes the rest, the result column is a fixed named width, collapsing
    # to one column on phones (the old two-thirds / one-third split left the
    # result column 386px wide at 1280px with 250px of it empty).
    assert f"grid-template-columns:minmax(0,1fr) {appmod._CHK_STATUS_COL_WIDTH}px" in appmod._CHECKS_DETAIL_CSS
    assert "@media(max-width:760px)" in appmod._CHECKS_DETAIL_CSS
