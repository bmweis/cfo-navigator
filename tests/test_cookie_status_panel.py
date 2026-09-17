"""Per-feed subscriber-cookie health, shown in the feed table's Cookie column
(2026-09) — not a separate summary panel sitting apart from the rows it
describes.

Before this, the health check rendered as a standalone "one row per domain"
panel above the feed table, and the table's own Cookie column only ever said
"configured" (a variable exists) with no way to tell whether fetching
actually worked. That gap was diagnosed live: Cautious Optimism's cookie was
set in Railway and the row still read exactly like a healthy one, because
nothing in the table connected to the health-check result at all. Now the
same persisted `auth_cookie_status` record renders per feed row, right next
to that feed's own Cookie/Subscriber/Order controls.

Before this, two of the three health states rendered nothing at all in the
old summary panel: it only showed a coral block when `stale_domains()` was
non-empty, and that returns domains where `ok is False` only. A working
cookie and a cookie that could not be probed looked identical — blank — so
"nothing on screen" meant both "healthy" and "we have no idea". That fix
carries over unchanged into the per-row rendering: all three states always
render something.

Amber is strictly `ok: None`. A passing check stays green however old it is:
staleness is shown as relative text, never promoted to its own colour.

Colours are true stoplight values, a sanctioned exception registered in
brand_check.AUX_COLORS and documented in BRAND.md §6 — the semantic tokens were
tried first and `--good` is navy, which reads as ordinary text rather than a
health signal. The amber is dot-only: #CA8A04 as text on white is 2.94:1, below
AA, so the state word stays outside the coloured span.
"""
import json
import pathlib
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
DOMAINS = ("mostlymetrics.com", "stratechery.com", "blog.publiccomps.com")
# One real feed per domain in REPO_OPML — see preferred_sites.opml itself.
FEED_NAMES = {
    "mostlymetrics.com": "Mostly Metrics (CJ Gustafson)",
    "stratechery.com": "Ben Thompson (Stratechery)",
    "blog.publiccomps.com": "Public Comps",
}


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """Boot with cookies configured, so the control renders at all.

    DOMAINS includes stratechery.com and blog.publiccomps.com purely as test
    fixtures for exercising all three health states. Since 2026-09 the
    candidate domain set is derived live from LINKLIB_SITES_OPML rather than
    a hardcoded registry — all three DOMAINS are already real feeds in
    REPO_OPML (copied below), so no domain-list patch is needed, only the
    env vars.
    """
    from linklib import extract as extract_mod
    for d in DOMAINS:
        monkeypatch.setenv(extract_mod._cookie_env_var(d), "x=1")
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    return appmod


def _seed_status(appmod, mostly=True, strat=False, public=None, hours=(3, 5, 9)):
    """Store one record per state. All timestamps stay under the 12h auto-
    refresh threshold, or loading the page would kick a real re-probe and
    overwrite the fixture."""
    lib = appmod._lib()
    try:
        lib.set_setting(authcheck.STATUS_KEY, json.dumps({
            "mostlymetrics.com": {"ok": mostly, "checked_at": _ago(hours[0]),
                                  "detail": "full text fetched (12,431 chars)"},
            "stratechery.com": {"ok": strat, "checked_at": _ago(hours[1]),
                                "detail": "got a preview/paywall"},
            "blog.publiccomps.com": {"ok": public, "checked_at": _ago(hours[2]),
                                     "detail": "no recent post found to test"},
        }))
    finally:
        lib.close()


def _feed_row(html: str, feed_name: str) -> str:
    """The <tr class="ff-row">...</tr> block for `feed_name`. Rows don't
    nest other <tr>s, so a plain split-and-find is enough."""
    marker = f'<td class="ff-name">{feed_name}</td>'
    idx = html.index(marker)
    start = html.rindex('<tr class="ff-row"', 0, idx)
    end = html.index("</tr>", idx) + len("</tr>")
    return html[start:end]


def _cookie_cell(html: str, feed_name: str) -> str:
    row = _feed_row(html, feed_name)
    start = row.index('<td class="ff-cookie">')
    end = row.index("</td>", start)
    return row[start:end]


def _cookie_rows(html: str) -> dict:
    """{domain: {"state", "colour", "age"}} for every domain with a real
    feed, parsed from each feed's own .ff-cookie cell — the per-row
    replacement for the old .ck-panel summary."""
    out = {}
    for dom, name in FEED_NAMES.items():
        cell = _cookie_cell(html, name)
        colour = re.search(r'background:([^;]+);', cell)
        # The state word is the second inline <span> (after the dot).
        state = re.search(r'color:[^;]+;">([a-z]+)</span>', cell)
        age = re.search(r'&middot;\s*([^<]+)</span>', cell)
        out[dom] = {
            "state": state.group(1) if state else "",
            "colour": colour.group(1) if colour else "",
            "age": (age.group(1).strip() if age else ""),
        }
    return out


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------

def test_each_state_gets_its_own_colour_and_label(app_env):
    _seed_status(app_env)
    with _client(app_env) as client:
        rows = _cookie_rows(client.get("/admin/reader/feeds").text)

    assert rows["mostlymetrics.com"]["state"] == "working"
    assert rows["mostlymetrics.com"]["colour"] == "#15803D"

    assert rows["stratechery.com"]["state"] == "expired"
    assert rows["stratechery.com"]["colour"] == "#b91c1c"

    assert rows["blog.publiccomps.com"]["state"] == "inconclusive"
    assert rows["blog.publiccomps.com"]["colour"] == "#CA8A04"


def test_working_and_inconclusive_now_render_at_all(app_env):
    """The regression this feature exists to fix: previously only `expired`
    produced any output, so a healthy and an untestable cookie were both
    indistinguishable from "nothing checked yet"."""
    _seed_status(app_env, mostly=True, strat=True, public=None)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert "Subscriber cookie expired" not in html      # nothing is expired
    rows = _cookie_rows(html)
    assert {r["state"] for r in rows.values()} == {"working", "inconclusive"}


def test_a_stale_but_passing_check_stays_green(app_env):
    """Explicitly not a fourth state: age is reported, never recoloured."""
    _seed_status(app_env, mostly=True, strat=True, public=True,
                 hours=(11.5, 11.5, 11.5))
    with _client(app_env) as client:
        rows = _cookie_rows(client.get("/admin/reader/feeds").text)
    assert all(r["colour"] == "#15803D" for r in rows.values())
    assert all(r["state"] == "working" for r in rows.values())
    assert all(r["age"].endswith("ago") for r in rows.values())


def test_a_never_checked_domain_reads_configured_not_yet_checked(app_env):
    """No stored record at all — the cell must still say something (not go
    blank), but "never checked" and "checked and inconclusive" are genuinely
    different facts, so this doesn't collapse into the amber state."""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    for name in FEED_NAMES.values():
        cell = _cookie_cell(html, name)
        # The aria-label keeps the fuller phrase for accessibility; the
        # VISIBLE text was shortened (2026-09) after the fuller phrase
        # wrapped to three lines in a production report (Cautious
        # Optimism) — see test_not_yet_checked_text_fits_on_one_line.
        assert "configured, not yet checked" in cell
        assert ">Not yet checked<" in cell


def test_not_yet_checked_text_is_short_enough_for_the_column(app_env):
    """2026-09 regression: "configured, not yet checked" (27 chars) wrapped
    to three lines in the Cookie column, stretching that row far taller
    than its neighbors. Shortened to "Not yet checked" — this doesn't
    re-measure real layout (no browser here), but pins the actual visible
    string so a future edit can't silently revert to the longer phrase."""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    for name in FEED_NAMES.values():
        cell = _cookie_cell(html, name)
        if "Not yet checked" in cell:
            visible = re.search(r">([^<]+)</span>$", cell.rstrip())
            assert visible and visible.group(1) == "Not yet checked"


# test_cookie_column_width_uses_the_named_status_constant was removed
# (2026-09): it pinned the Cookie column's <th>/CSS width to _COL_WIDTH_STATUS
# (110px), which was the column's width when this test was written. A later
# fix ("Feeds admin Cookie column, wide enough for status plus age" — see
# CLAUDE.md) deliberately widened the column to a dedicated
# _COL_WIDTH_STATUS_AGE (190px) constant, because the column's real content
# (a dot, a state word, and a relative age like "· 45d ago") measured wider
# than 110px in production and wrapped to two lines on every configured row.
# That fix shipped its own regression test —
# test_cookie_column_is_wide_enough_for_status_plus_age in
# tests/test_feed_cookie_flag.py — asserting the correct, current width; this
# test was simply never updated or removed at the time, so it kept asserting
# the pre-fix value and started failing against current code. Deleted rather
# than fixed in place, since the behavior it asserted was deliberately
# superseded and a correct replacement already exists elsewhere.


def test_list_and_edit_pages_agree_on_a_feeds_cookie_state(app_env):
    """2026-09 regression: the edit form used to compute its own narrower
    "is a variable set" answer and say "Cookie configured for this
    domain" in green for ANY configured domain — even one the list page's
    own health probe had already marked expired. Both surfaces now read
    the same _cookie_health_state(), so a real health state (not just
    "configured") has to agree between them."""
    import json
    from linklib import authcheck
    fresh = _ago(0.01)
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = lib.find_feed_by_url("https://www.mostlymetrics.com/feed")
            lib.set_setting(authcheck.STATUS_KEY, json.dumps({
                "mostlymetrics.com": {"ok": False, "checked_at": fresh, "detail": "got a preview/paywall"},
            }))
        finally:
            lib.close()
        list_html = client.get("/admin/reader/feeds").text
        list_cell = _cookie_cell(list_html, feed["name"])
        assert "expired" in list_cell

        edit_html = client.get(f"/admin/reader/feeds/{feed['id']}/edit").text
        idx = edit_html.index("Cookie</label>")
        edit_excerpt = edit_html[idx:idx + 300]
        # The edit form must say the SAME thing the list does — "expired",
        # not the old context-free "Cookie configured for this domain".
        assert "expired" in edit_excerpt
        assert "Cookie configured for this domain" not in edit_excerpt


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_the_result_survives_a_reload(app_env):
    """It reads the stored record, so it is not a flash message."""
    _seed_status(app_env)
    with _client(app_env) as client:
        first = _cookie_rows(client.get("/admin/reader/feeds").text)
        second = _cookie_rows(client.get("/admin/reader/feeds").text)
    assert first == second
    assert {r["state"] for r in first.values()} == {"working", "expired", "inconclusive"}


def test_relative_age_is_shown_per_domain(app_env):
    _seed_status(app_env, hours=(3, 5, 9))
    with _client(app_env) as client:
        rows = _cookie_rows(client.get("/admin/reader/feeds").text)
    assert rows["mostlymetrics.com"]["age"] == "3h ago"
    assert rows["stratechery.com"]["age"] == "5h ago"
    assert rows["blog.publiccomps.com"]["age"] == "9h ago"


# ---------------------------------------------------------------------------
# Separation from the expired panel; dormant with no cookies configured
# ---------------------------------------------------------------------------

def test_the_expired_panel_no_longer_repeats_the_domains(app_env):
    """The per-row cells already name each domain's status, in colour, right
    on the feed table — the coral panel keeps only the part a row can't
    carry without cluttering it: how to fix it."""
    _seed_status(app_env, mostly=True, strat=False, public=None)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    i = html.index("Subscriber cookie expired")
    coral = html[i:html.index("</div>", html.index("</ol>", i))]
    for dom in DOMAINS:
        assert dom not in coral
    assert "To refresh an expired cookie" in html


def test_nothing_renders_when_no_cookies_are_configured(monkeypatch, tmp_path):
    """The whole feature stays dormant until a LINKLIB_COOKIE_<DOMAIN> var is
    set — the Cookie column still renders (as a dash for every feed), but
    there's no Re-check button and no expired-cookie panel."""
    from linklib import extract as extract_mod
    for d in DOMAINS:
        monkeypatch.delenv(extract_mod._cookie_env_var(d), raising=False)
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "nocookie.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)

    with _client(appmod) as client:
        html = client.get("/admin/reader/feeds").text
    # The button's own action, not its label text — the label also appears
    # in the page's static Cookie-column help copy (naming the button by
    # name), which renders regardless of whether the feature is active.
    assert '/admin/auth/recheck' not in html
    for name in FEED_NAMES.values():
        assert "&mdash;" in _cookie_cell(html, name)


# ---------------------------------------------------------------------------
# The age helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("delta,expected", [
    (timedelta(seconds=5), "just now"),
    (timedelta(minutes=20), "20m ago"),
    (timedelta(hours=3), "3h ago"),
    (timedelta(days=9), "9d ago"),
])
def test_relative_age_formatting(app_env, delta, expected):
    stamp = (datetime.now(timezone.utc) - delta).isoformat()
    assert app_env._relative_age(stamp) == expected


def test_relative_age_is_safe_on_junk_input(app_env):
    """A malformed stored timestamp must not 500 the whole admin page."""
    assert app_env._relative_age("") == ""
    assert app_env._relative_age("not-a-date") == ""
    assert app_env._relative_age(None or "") == ""


def test_relative_age_handles_a_naive_timestamp(app_env):
    """Older records may lack a timezone; treat them as UTC rather than
    raising on the subtraction."""
    naive = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    assert app_env._relative_age(naive) == "2h ago"


def test_the_red_reuses_the_destructive_action_value(app_env):
    """One red sitewide: the same #b91c1c as Delete/Remove, not a second red."""
    _seed_status(app_env, mostly=True, strat=False, public=True)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    # It is the same value the Remove buttons already use on this page.
    assert html.count("#b91c1c") > 1


def test_the_stoplight_colours_are_registered_brand_exceptions(app_env):
    """A brand-new off-palette hex fails the build by design; these three are
    sanctioned, so they must be in the allowlist rather than silently passing."""
    from linklib.brand_check import AUX_COLORS
    for hexv in ("#15803d", "#ca8a04", "#b91c1c"):
        assert hexv in AUX_COLORS, f"{hexv} missing from AUX_COLORS"
