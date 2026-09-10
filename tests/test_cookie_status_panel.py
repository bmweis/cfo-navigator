"""Per-domain subscriber-cookie health summary on /admin/reader/feeds.

Before this, two of the three states rendered nothing at all: the page only
showed a panel when `stale_domains()` was non-empty, and that returns domains
where `ok is False` only. A working cookie and a cookie that could not be
probed looked identical — blank — so "nothing on screen" meant both "healthy"
and "we have no idea".

The summary reads the persisted `auth_cookie_status` record, so it survives a
reload rather than only appearing right after a Re-check.

Amber is strictly `ok: None`. A passing check stays green however old it is:
staleness is shown as relative text, never promoted to its own colour.

Colours are true stoplight values, a sanctioned exception registered in
brand_check.AUX_COLORS and documented in BRAND.md §6 — the semantic tokens were
tried first and `--good` is navy, which reads as ordinary text rather than a
health signal. The amber is dot-only: #CA8A04 as text on white is 2.94:1, below
AA, so the state word stays in --ink-soft.
"""
import json
import pathlib
import shutil
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
DOMAINS = ("mostlymetrics.com", "stratechery.com", "blog.publiccomps.com")


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
    fixtures for exercising all three health states — patched into the real
    _COOKIE_DOMAINS registry rather than assuming they're production entries.
    """
    from linklib import extract as extract_mod
    monkeypatch.setattr(extract_mod, "_COOKIE_DOMAINS", DOMAINS)
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


def _panel_markup(html):
    """The <div class="ck-panel"> element only. Matching on the bare string
    would hit the stylesheet rule of the same name, which is always emitted."""
    start = html.index('<div class="ck-panel"')
    depth, i = 0, start
    while i < len(html):
        if html.startswith("<div", i):
            depth += 1
        elif html.startswith("</div>", i):
            depth -= 1
            if depth == 0:
                return html[start:i + 6]
        i += 1
    raise AssertionError("unbalanced ck-panel markup")


def _rows(html):
    import re
    out = []
    for block in re.findall(r'<div class="ck-row">(?:(?!</div>).)*</div>', html, re.S):
        dom = re.search(r'class="ck-dom">([^<]+)', block)
        state = re.search(r'class="ck-state"[^>]*>([^<]+)', block)
        colour = re.search(r'class="ck-dot" style="background:([^;]+);', block)
        age = re.search(r'class="ck-age">([^<]*)', block)
        out.append({"domain": dom.group(1) if dom else "",
                    "state": state.group(1) if state else "",
                    "colour": colour.group(1) if colour else "",
                    "age": age.group(1) if age else ""})
    return out


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------

def test_each_state_gets_its_own_colour_and_label(app_env):
    _seed_status(app_env)
    with _client(app_env) as client:
        rows = _rows(client.get("/admin/reader/feeds").text)

    by_dom = {r["domain"]: r for r in rows}
    assert len(rows) == 3

    assert by_dom["mostlymetrics.com"]["state"] == "working"
    assert by_dom["mostlymetrics.com"]["colour"] == "#15803D"

    assert by_dom["stratechery.com"]["state"] == "expired"
    assert by_dom["stratechery.com"]["colour"] == "#b91c1c"

    assert by_dom["blog.publiccomps.com"]["state"] == "inconclusive"
    assert by_dom["blog.publiccomps.com"]["colour"] == "#CA8A04"


def test_working_and_inconclusive_now_render_at_all(app_env):
    """The regression this feature exists to fix: previously only `expired`
    produced any output, so a healthy and an untestable cookie were both
    invisible."""
    _seed_status(app_env, mostly=True, strat=True, public=None)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert "Subscriber cookie expired" not in html      # nothing is expired
    rows = _rows(html)
    assert {r["state"] for r in rows} == {"working", "inconclusive"}


def test_a_stale_but_passing_check_stays_green(app_env):
    """Explicitly not a fourth state: age is reported, never recoloured."""
    _seed_status(app_env, mostly=True, strat=True, public=True,
                 hours=(11.5, 11.5, 11.5))
    with _client(app_env) as client:
        rows = _rows(client.get("/admin/reader/feeds").text)
    assert all(r["colour"] == "#15803D" for r in rows)
    assert all(r["state"] == "working" for r in rows)
    assert all(r["age"].endswith("ago") for r in rows)


def test_a_never_checked_domain_reads_inconclusive(app_env):
    """No stored record at all — the panel must still render a row rather than
    omit the domain silently."""
    with _client(app_env) as client:
        rows = _rows(client.get("/admin/reader/feeds").text)
    assert len(rows) == 3
    assert all(r["state"] == "inconclusive" for r in rows)
    assert all(r["colour"] == "#CA8A04" for r in rows)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_the_result_survives_a_reload(app_env):
    """It reads the stored record, so it is not a flash message."""
    _seed_status(app_env)
    with _client(app_env) as client:
        first = _rows(client.get("/admin/reader/feeds").text)
        second = _rows(client.get("/admin/reader/feeds").text)
    assert first == second
    assert {r["state"] for r in first} == {"working", "expired", "inconclusive"}


def test_relative_age_is_shown_per_domain(app_env):
    _seed_status(app_env, hours=(3, 5, 9))
    with _client(app_env) as client:
        by_dom = {r["domain"]: r for r in _rows(client.get("/admin/reader/feeds").text)}
    assert by_dom["mostlymetrics.com"]["age"] == "3h ago"
    assert by_dom["stratechery.com"]["age"] == "5h ago"
    assert by_dom["blog.publiccomps.com"]["age"] == "9h ago"


# ---------------------------------------------------------------------------
# Separation from the Cookie checkbox, and from the expired panel
# ---------------------------------------------------------------------------

def test_the_panel_holds_no_checkboxes_and_no_seafoam(app_env):
    """Different thing from the Cookie column: static declaration vs dynamic
    health. A dot, not a checkbox, and none of the checkbox's accent colour."""
    _seed_status(app_env)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    panel = _panel_markup(html)
    assert panel.count('class="ck-row"') == 3
    assert "<input" not in panel
    assert "seafoam" not in panel


def test_the_expired_panel_no_longer_repeats_the_domains(app_env):
    """The summary already names them in colour; the coral panel keeps only
    the part the summary cannot carry, which is how to fix it."""
    _seed_status(app_env, mostly=True, strat=False, public=None)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    i = html.index("Subscriber cookie expired")
    coral = html[i:html.index("</div>", html.index("</ol>", i))]
    for dom in DOMAINS:
        assert dom not in coral
    assert "To refresh an expired cookie" in html


def test_the_panel_sits_between_the_header_and_the_feed_table(app_env):
    _seed_status(app_env)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert html.index('class="ff-head"') < html.index('class="ck-panel"')
    assert html.index('class="ck-panel"') < html.index('class="ff-table"')


def test_nothing_renders_when_no_cookies_are_configured(monkeypatch, tmp_path):
    """The whole feature stays dormant until a LINKLIB_COOKIE_<DOMAIN> var is set."""
    from linklib import extract as extract_mod
    monkeypatch.setattr(extract_mod, "_COOKIE_DOMAINS", DOMAINS)
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
    assert '<div class="ck-panel"' not in html   # the CSS rule is always emitted
    assert "Re-check subscriber access" not in html


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


def test_the_state_word_is_not_tinted_amber(app_env):
    """#CA8A04 as text on the panel's white surface is 2.94:1 — under AA (4.5)
    and even AA-large (3.0). The colour belongs on the dot, which is a graphic;
    the word stays readable ink."""
    _seed_status(app_env)
    with _client(app_env) as client:
        panel = _panel_markup(client.get("/admin/reader/feeds").text)
    assert 'class="ck-state" style="color:' not in panel
    assert "#CA8A04" in panel        # still present, on the dot


def test_the_red_reuses_the_destructive_action_value(app_env):
    """One red sitewide: the same #b91c1c as Delete/Reject, not a second red."""
    _seed_status(app_env, mostly=True, strat=False, public=True)
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert "#b91c1c" in _panel_markup(html)
    # It is the same value the Remove buttons already use on this page.
    assert html.count("#b91c1c") > 1


def test_the_stoplight_colours_are_registered_brand_exceptions(app_env):
    """A brand-new off-palette hex fails the build by design; these three are
    sanctioned, so they must be in the allowlist rather than silently passing."""
    from linklib.brand_check import AUX_COLORS
    for hexv in ("#15803d", "#ca8a04", "#b91c1c"):
        assert hexv in AUX_COLORS, f"{hexv} missing from AUX_COLORS"
