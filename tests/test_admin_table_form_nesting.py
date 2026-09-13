"""Regression guard for a real, functionally-broken bug found in PR 32
(2026-09): /admin/tools/software and /admin/tools/communities each wrapped
their whole approved-rows <table> in an outer <form id="...-approved-form">
that no JS or route anywhere ever referenced by id (every bulk-select/
bulk-edit/bulk-delete function reads checked boxes via a plain class
selector, `document.querySelectorAll('.software-row-cb:checked')`, wholly
independent of any wrapping <form>) — pure dead markup. Every row's own
Delete/Mark-reviewed <form> lived inside it, making them NESTED <form>
elements: invalid HTML.

Per the HTML5 parsing algorithm, a real browser drops the first nested
<form> open tag it encounters entirely (no element created), and that
form's own closing </form> tag then pops the OUTER form off the parser's
stack instead — silently orphaning that one row's Delete button (no
onsubmit confirm, no submit target: clicking it did nothing). This was NOT
a cosmetic width bug — it was a broken control, and nothing caught it
because nothing checked the raw HTML for the one thing that actually
mattered here: is there ever a literal <form> inside another <form>.

A tree-building parser that faithfully reimplements the HTML5 spec's own
form-nesting recovery (a real browser, or a from-scratch reimplementation
of that algorithm) would be the WRONG tool for this test: it would
"helpfully" recover from the defect the same way a browser does, silently
dropping the nested tag before this test ever got a chance to see it,
making the check pass against BOTH the old broken markup and the fixed
markup alike — a guard that can never fail is worse than none. What's
needed instead is a flat, literal scan of the SOURCE tags exactly as
written, with no recovery behavior at all. Python's stdlib `html.parser`
is exactly that when used as a plain SAX-style scanner (feed() just calls
handle_starttag/handle_endtag for each literal tag, with no tree-building
or error-recovery logic of its own) — so a small stack-depth tracker built
on top of it can catch a genuine nested <form> in the source directly,
independent of how any particular downstream parser (browser or library)
might later try to recover from it.
"""
import os
import tempfile
from html.parser import HTMLParser

import pytest


class _FormNestingChecker(HTMLParser):
    """Tracks <form>...</form> nesting depth directly against the SOURCE
    markup (not a re-parsed/recovered DOM) and records every point where a
    <form> open tag appears while another <form> is already open — the
    literal, unambiguous shape of the PR 32 defect."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.max_depth = 0
        self.nested_starttags = []  # (tag, attrs) for each offending nested <form>

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)
            if self.depth > 1:
                self.nested_starttags.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "form" and self.depth > 0:
            self.depth -= 1


def _assert_no_nested_forms(html: str, label: str):
    checker = _FormNestingChecker()
    checker.feed(html)
    assert checker.nested_starttags == [], (
        f"{label}: found {len(checker.nested_starttags)} <form> tag(s) nested "
        f"inside another <form> — this is the exact PR 32 defect (a browser "
        f"silently drops one of them, orphaning a Delete/Mark-reviewed "
        f"button with no submit target). Offending tags: "
        f"{checker.nested_starttags}"
    )


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


def test_parser_sanity_check_catches_a_planted_nested_form():
    """Prove the checker itself actually works before trusting it against
    real page output — a regression guard that can never fail is worse
    than none."""
    bad = '<div><form id="outer"><form method="post" action="/x"><button>Go</button></form></form></div>'
    checker = _FormNestingChecker()
    checker.feed(bad)
    assert checker.max_depth == 2
    assert len(checker.nested_starttags) == 1

    good = '<div><form id="a"><button>Go</button></form><form id="b"><button>Go</button></form></div>'
    checker2 = _FormNestingChecker()
    checker2.feed(good)
    assert checker2.max_depth == 1
    assert checker2.nested_starttags == []


def test_software_admin_table_has_no_nested_forms(admin_client):
    client, appmod, db = admin_client
    lib = appmod.Library(db)
    # Several approved tools, at least one needing review (so its row also
    # carries a "Mark reviewed" <form> before the Delete <form> — the two-
    # nested-forms-per-row shape) alongside plain rows.
    lib.add_tool("Alpha Finance Tool", "Does alpha things.", "https://alpha.example",
                 ["FP&A"], approved=1, needs_review=1)
    lib.add_tool("Beta Ledger", "Does beta things.", "https://beta.example",
                 ["Accounting"], approved=1, needs_review=0)
    lib.add_tool("Gamma Close", "Does gamma things.", "https://gamma.example",
                 ["FP&A"], approved=1, needs_review=1)
    lib.close()

    resp = client.get("/admin/tools/software")
    assert resp.status_code == 200
    html = resp.text
    # Sanity: the page actually rendered the rows we expect, so a
    # zero-nested-forms result isn't just an empty/broken page.
    assert "Alpha Finance Tool" in html
    assert "Beta Ledger" in html
    assert "Gamma Close" in html
    assert html.count('action="/admin/tools/software/') >= 3  # at least one delete form per row
    _assert_no_nested_forms(html, "/admin/tools/software")


def test_communities_admin_table_has_no_nested_forms(admin_client):
    client, appmod, db = admin_client
    lib = appmod.Library(db)
    lib.add_community("Beyond the Books", "https://beyond-the-books.example",
                       "", "Undisclosed", ["FP&A"], approved=1)
    lib.add_community("CFO Alliance Network", "https://cfo-alliance.example",
                       "VPs and directors", "Paid", ["FP&A", "ERP"], approved=1)
    lib.add_community("Zenith Finance Collective", "https://zenith.example",
                       "Controllers", "Paid", ["Accounting"], approved=1)
    lib.close()

    resp = client.get("/admin/tools/communities")
    assert resp.status_code == 200
    html = resp.text
    assert "Beyond the Books" in html
    assert "CFO Alliance Network" in html
    assert "Zenith Finance Collective" in html
    assert html.count('action="/admin/tools/communities/') >= 3
    _assert_no_nested_forms(html, "/admin/tools/communities")


class _FormIdCollector(HTMLParser):
    """Collects the id= of every real <form> START TAG — deliberately not a
    substring check against the raw response text, since the fix's own
    explanatory code comment (a CSS comment, sent to the client verbatim
    inside <style>) names both retired ids as plain text. A substring
    match would false-positive on that prose; only an actual <form
    id="..."> tag should count."""

    def __init__(self):
        super().__init__()
        self.form_ids = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.form_ids.append(dict(attrs).get("id", ""))


def test_no_vestigial_wrapping_form_ids_remain(admin_client):
    """The actual fix: the two dead <form id="...-approved-form"> wrappers
    are gone outright, not just harmless — nothing else in the codebase
    ever referenced them (confirmed by grep before removing), so their
    reappearance would only ever mean this bug's root cause came back."""
    client, appmod, db = admin_client
    lib = appmod.Library(db)
    lib.add_tool("Solo Tool", "Does solo things.", "https://solo.example",
                 ["FP&A"], approved=1)
    lib.add_community("Solo Community", "https://solo-community.example",
                       "", "Free", ["FP&A"], approved=1)
    lib.close()

    software_html = client.get("/admin/tools/software").text
    communities_html = client.get("/admin/tools/communities").text

    software_ids = _FormIdCollector()
    software_ids.feed(software_html)
    communities_ids = _FormIdCollector()
    communities_ids.feed(communities_html)

    assert "software-approved-form" not in software_ids.form_ids
    assert "communities-approved-form" not in communities_ids.form_ids
