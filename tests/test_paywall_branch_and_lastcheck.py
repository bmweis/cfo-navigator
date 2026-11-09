"""Subscriber check follow-up (2026-11): why "Paywall text on a post page", and
a readable Last check column.

All HTTP is stubbed (requests.get in linklib.extract); nothing touches the
network. The cookie value is the sentinel SECRET and must never appear in the
stored record, a rendered cell or the logs.
"""
import json
import logging
import pathlib
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck, extract
from linklib.db import Library

SECRET = "SECRET_VALUE_9f3a"
DOMAIN = "mostlymetrics.com"
LONG_SLUG = "duration-risk-where-is-your-company-most-exposed-to-rate-moves-this-year"
POST = f"https://www.mostlymetrics.com/p/{LONG_SLUG}"
FULL_TEXT = "<html><body><article>" + ("word " * 400) + "</article></body></html>"
PAYWALL = ("<html><body><p>This post is for paid subscribers</p>"
           + ("teaser " * 30) + "</body></html>")
PAYWALL_LONGER = PAYWALL.replace("</body>", ("upsell block " * 80) + "</body>")
UPSELL_ONLY = ("<html><body><article>" + ("word " * 400)
               + "<footer>Become a paid subscriber</footer></article></body></html>")
REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")


def _resp(status, body, url=POST):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.encoding = "utf-8"
    r.url = url
    r.history = []
    r.reason = "x"
    return r


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv(extract._cookie_env_var(DOMAIN), f"token={SECRET}")
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: (POST, "A Post"))


def _stub(monkeypatch, results, seen):
    """results: one response (or exception) for every call, or a list consumed in
    order. Every call is recorded as (url, headers)."""
    queue = list(results) if isinstance(results, list) else None

    def fake_get(url, headers=None, timeout=None):
        seen.append((url, dict(headers or {})))
        r = queue.pop(0) if queue is not None else results
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(extract.requests, "get", fake_get)


def _check(lib):
    return authcheck.check_auth_cookies(lib, "ignored.opml")[DOMAIN]


# -- Part A: marker and the one comparison request --------------------------

def test_paywall_branch_records_the_matched_marker(lib, monkeypatch):
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, PAYWALL)], [])
    rec = _check(lib)
    assert rec["reason"] == "paywall"
    assert rec["paywall_marker"] == "this post is for paid subscribers"
    assert "cookie sent" in rec["reason_text"] and "bytes" in rec["reason_text"]
    assert rec["reason_text"].startswith("Paywall text on a post page")


def test_footer_upsell_on_a_full_page_is_named_as_the_marker(lib, monkeypatch):
    """A subscriber's full page with an upsell block trips the rule; the record
    must say which wording did it, so a false positive is visible."""
    _stub(monkeypatch, [_resp(200, UPSELL_ONLY), _resp(200, UPSELL_ONLY)], [])
    rec = _check(lib)
    assert rec["reason"] == "paywall"
    assert rec["paywall_marker"] == "become a paid subscriber"


def test_second_request_omits_the_cookie_and_the_first_sends_it(lib, monkeypatch):
    seen = []
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, PAYWALL)], seen)
    _check(lib)
    assert len(seen) == 2
    assert seen[0][0] == seen[1][0] == POST            # same post URL
    assert "Cookie" in seen[0][1] and SECRET in seen[0][1]["Cookie"]
    assert "Cookie" not in seen[1][1]                 # never sent on the comparison


def test_identical_pages_say_the_cookie_was_ignored(lib, monkeypatch):
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, PAYWALL)], [])
    rec = _check(lib)
    assert rec["compare_text"].startswith("Same page without the cookie: identical (cookie ignored)")
    assert "this post is for paid subscribers (present with and without the cookie)" in rec["compare_text"]
    assert rec["paywall_marker_without_cookie"] == rec["paywall_marker"]
    assert rec["bytes_with_cookie"] == rec["bytes_without_cookie"] == len(PAYWALL.encode())
    assert rec["same_without_cookie"] is True
    assert rec["compare_text"] in rec["detail"]


def test_different_pages_say_the_cookie_had_an_effect(lib, monkeypatch):
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, PAYWALL_LONGER)], [])
    rec = _check(lib)
    assert rec["same_without_cookie"] is False
    assert "cookie had an effect" in rec["compare_text"]
    assert "longer" in rec["compare_text"]
    assert rec["bytes_without_cookie"] > rec["bytes_with_cookie"]


def test_marker_only_with_the_cookie_is_stated(lib, monkeypatch):
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, FULL_TEXT)], [])
    rec = _check(lib)
    assert "present with the cookie only" in rec["compare_text"]
    assert rec["paywall_marker_without_cookie"] == ""
    assert rec["text_chars_without_cookie"] > rec["text_chars_with_cookie"]


def test_a_failed_comparison_request_is_reported_not_raised(lib, monkeypatch):
    _stub(monkeypatch, [_resp(200, PAYWALL), requests.exceptions.ConnectionError("dns")], [])
    rec = _check(lib)
    assert rec["reason"] == "paywall"
    assert rec["compare_text"] == "Without the cookie: could not fetch the page again"
    assert "Matched wording" not in rec["compare_text"]
    assert rec["same_without_cookie"] is None


@pytest.mark.parametrize("make", [
    lambda: _resp(200, FULL_TEXT),                                   # accepted
    lambda: _resp(403, "Forbidden"),                                 # rejected
    lambda: _resp(503, "unavailable"),                               # unexpected
    lambda: _resp(200, "<html><body>Sign in</body></html>",
                  url="https://app.beehiiv.com/login"),              # login redirect
    lambda: requests.exceptions.Timeout("slow"),                     # unreachable
])
def test_no_second_request_for_any_other_reason(lib, monkeypatch, make):
    seen = []
    _stub(monkeypatch, make(), seen)
    rec = _check(lib)
    assert rec["reason"] != "paywall"
    assert len(seen) == 1
    assert "compare_text" not in rec


def test_no_second_request_when_the_cookie_was_never_sent(lib, monkeypatch):
    other = "https://mostlymetrics.beehiiv.com/p/x"
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: (other, "A Post"))
    seen = []
    _stub(monkeypatch, _resp(200, PAYWALL, url=other), seen)
    rec = _check(lib)
    assert rec["reason"] == "not_sent"
    assert len(seen) == 1


def test_cookie_value_never_in_the_record_log_or_stored_status(lib, monkeypatch, caplog):
    _stub(monkeypatch, [_resp(200, PAYWALL), _resp(200, PAYWALL)], [])
    with caplog.at_level(logging.DEBUG):
        rec = _check(lib)
    assert SECRET not in json.dumps(rec)
    assert SECRET not in lib.get_setting(authcheck.STATUS_KEY)
    assert SECRET not in caplog.text


# -- Part B: the Last check column ------------------------------------------

@pytest.fixture
def appmod(tmp_path, monkeypatch):
    for d in ("mostlymetrics.com", "stratechery.com", "blog.publiccomps.com"):
        monkeypatch.setenv(extract._cookie_env_var(d), f"x={SECRET}")
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as m
    return importlib.reload(m)


def _ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _paywall_record(same=True):
    return {"ok": False, "checked_at": _ago(3), "detail": "x", "reason": "paywall",
            "reason_text": "Paywall text on a post page (cookie sent, 142,331 bytes)",
            "http_status": 200, "final_host": "www.mostlymetrics.com",
            "final_path": f"/p/{LONG_SLUG}", "post_host": "www.mostlymetrics.com",
            "cookie_sent": True, "cookie_names": ["token"], "cookie_name_count": 1,
            "cookie_bytes": 354, "paywall_marker": "this post is for paid subscribers",
            "compare_text": ("Same page without the cookie: identical (cookie ignored)" if same
                             else "Without the cookie: 160,000 bytes vs 142,331 with it, longer, "
                                  "same paywall wording (cookie had an effect)"),
            "same_without_cookie": same}


def _seed(appmod, mostly):
    lib = appmod._lib()
    try:
        lib.set_setting(authcheck.STATUS_KEY, json.dumps({"mostlymetrics.com": mostly}))
    finally:
        lib.close()


def _page(appmod):
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:          # `with` runs the startup seeding
        c.post("/login", data={"username": "admin", "password": "adminpass"},
               follow_redirects=False)
        return c.get("/admin/reader/feeds").text


def _row(html, name):
    marker = f'<td class="ff-name">{name}</td>'
    i = html.index(marker)
    start = html.rindex('<tr class="ff-row"', 0, i)
    return html[start:html.index("</tr>", i) + 5]


def _cell(row, cls):
    start = row.index(f'<td class="{cls}">')
    return row[start:row.index("</td>", start)]


def test_two_columns_exist_and_split_the_content(appmod):
    _seed(appmod, _paywall_record())
    html = _page(appmod)
    heads = re.findall(r"<th[^>]*>([A-Za-z ]+?)(?:<span|</th>)", html[html.index('id="ff-table"'):])
    assert heads.index("Cookie") + 1 == heads.index("Last check")
    row = _row(html, "Mostly Metrics (CJ Gustafson)")
    cookie, last = _cell(row, "ff-cookie"), _cell(row, "ff-lastcheck")
    assert "expired" in cookie and "3h ago" in cookie
    assert "Paywall text on a post page" not in cookie      # reason is in Last check
    assert "Paywall text on a post page" in last
    assert "HTTP 200" in last and "1 cookie (token), 354 bytes" in last
    assert SECRET not in html


def test_long_slug_is_shortened_in_the_cell_and_whole_in_details(appmod):
    _seed(appmod, _paywall_record())
    last = _cell(_row(_page(appmod), "Mostly Metrics (CJ Gustafson)"), "ff-lastcheck")
    summary = last[last.index("<summary"):last.index("</summary>")]
    assert "…" in summary and LONG_SLUG not in summary
    details = last[last.index("</summary>"):]
    assert f"www.mostlymetrics.com/p/{LONG_SLUG}" in details
    assert "this post is for paid subscribers" in details
    assert "Same page without the cookie" in details
    assert "title=" not in last                              # touch: no tooltips


def test_each_state_says_something_never_blank(appmod):
    _seed(appmod, _paywall_record())
    html = _page(appmod)
    unchecked = _cell(_row(html, "Public Comps"), "ff-lastcheck")
    assert "Not checked" in unchecked
    plain = _cell(_row(html, "TechCrunch"), "ff-lastcheck")  # no cookie variable
    assert "—" in plain or "&mdash;" in plain


def test_shorten_middle_keeps_both_ends():
    s = appmod_shorten("/p/" + LONG_SLUG, 30)
    assert len(s) == 30 and s.startswith("/p/duration") and s.endswith("this-year") and "…" in s
    assert appmod_shorten("/short", 30) == "/short"


def appmod_shorten(text, limit):
    import webapp.app as m
    return m._shorten_middle(text, limit)


# -- banner steps follow the reason ----------------------------------------

def test_banner_for_a_cookie_ignored_paywall_drops_the_header_copy_steps(appmod):
    _seed(appmod, _paywall_record(same=True))
    html = _page(appmod)
    banner = html[html.index("Subscriber cookie expired"):html.index("The RSS subscriptions")]
    assert "Same page without the cookie: identical (cookie ignored)" in banner
    assert "Replacing the cookie will not change that" in banner
    assert "logged-out window" in banner
    assert "Copy the full" not in banner


def test_banner_for_a_paywall_where_the_cookie_had_an_effect_keeps_refresh_steps(appmod):
    _seed(appmod, _paywall_record(same=False))
    html = _page(appmod)
    banner = html[html.index("Subscriber cookie expired"):html.index("The RSS subscriptions")]
    assert "Copy the full" in banner
    assert "Replacing the cookie will not change that" not in banner


def test_banner_for_a_rejected_login_keeps_the_existing_steps(appmod):
    rec = {"ok": False, "checked_at": _ago(3), "detail": "x", "reason": "rejected",
           "reason_text": "Rejected: HTTP 401 or 403", "http_status": 403,
           "final_host": "www.mostlymetrics.com", "final_path": "/p/x"}
    _seed(appmod, rec)
    html = _page(appmod)
    banner = html[html.index("Subscriber cookie expired"):html.index("The RSS subscriptions")]
    assert "Copy the full" in banner and "logged-out window" not in banner


# -- the 3-line target, measured in a real browser --------------------------

def _chromium():
    import os
    pw = pytest.importorskip("playwright.sync_api")
    p = pw.sync_playwright().start()
    for exe in ("/opt/pw-browsers/chromium", None):
        kw = {"args": ["--no-sandbox"]}
        if exe:
            if not os.path.exists(exe):
                continue
            kw["executable_path"] = exe
        try:
            return p, p.chromium.launch(**kw)
        except Exception:
            continue
    p.stop()
    pytest.skip("no Chromium available")


def test_last_check_cell_holds_to_three_lines_at_1280(appmod):
    _seed(appmod, _paywall_record())
    html = _page(appmod)
    p, browser = _chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(html)
        info = page.evaluate("""() => {
          const row = [...document.querySelectorAll('.ff-row')]
            .find(r => r.querySelector('.ff-name').textContent.includes('Mostly Metrics'));
          const s = row.querySelector('.ff-lastcheck summary');
          const lines = [...s.children].map(d => d.getBoundingClientRect().height);
          const lh = parseFloat(getComputedStyle(s.children[0]).lineHeight);
          return {lines, lh, total: s.getBoundingClientRect().height,
                  clipped: [...s.children].some(d => d.scrollWidth > d.clientWidth + 1)};
        }""")
        assert len(info["lines"]) == 3
        assert info["total"] <= info["lh"] * 3 + 2          # three lines, no wrapping
        assert not info["clipped"]                           # nothing cut off by the cell
    finally:
        browser.close()
        p.stop()
