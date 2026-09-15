"""Regression guard for the overhead-spend Date/Amount overlap bug, which
shipped twice (2026-08-28, still present as of 2026-09-15) because both
times it was verified in Chromium only.

The real defect: a CSS Grid item defaults to `min-width:auto`, so its
track can't shrink below the item's own content-minimum width even inside
a `minmax(140px,1fr)` track — the `auto-fit`/`minmax` switch (the original
2026-08-28 fix) stops the whole PAGE from overflowing, but does nothing
about one grid ITEM overflowing its own track when that item's intrinsic
minimum exceeds the track's floor. A native `<input type="date">`'s
content-minimum is engine-specific — this repo's own fix comment
originally cited "~160px in Chromium" as the number that drove the
`minmax(140px,...)` choice, which is Chromium-specific evidence for a
cross-engine problem. WebKit (Safari, and therefore every iOS browser
including "Chrome" on iPhone, which Apple requires to run on WebKit) has a
substantially larger native date-input minimum, so the same 140px floor
that satisfies Chromium can still be narrower than WebKit's own minimum —
producing the overlap Brian reported on a real iPhone 16 Pro even though
a Chromium-only sweep (at any viewport width) came back clean.

This sandbox cannot install or launch Playwright's WebKit browser to
confirm the fix visually (`playwright install webkit` fails with a 403
policy denial against playwright.download.prss.microsoft.com and
cdn.playwright.dev — a genuine organizational network policy block, not a
transient failure; confirmed via the agent proxy's own status endpoint).
So this guard can't be a live WebKit rendering assertion. What it CAN do,
and does, is assert the actual CSS-spec-level fix is present in the
rendered markup: `min-width:0` on every grid item that shares a
`display:grid` container with a native date/time/number input — the
standard, engine-independent remedy for exactly this failure class (grid
items default to `min-width:auto`; `min-width:0` is what lets a track
actually shrink to its `minmax()` floor, in every engine, not just the one
a sighted test happened to run in). This is the same discipline CLAUDE.md
already documents for the CSS Grid blowout class of bug (Phase P, the
Categories-checklist fix) — verify the DECLARED CSS property is correct,
since that's what determines cross-engine behavior, not a single engine's
rendered pixels.

The checker below is generic, not two pinned string matches: it scans
every `<div style="display:grid;...">`...`</div>` region in the rendered
page and, for any such region that contains a native date/time/number
input, requires `min-width:0` to appear somewhere inside it (on the grid
container itself, on a wrapping child div, or directly on the input —
covering both fix shapes used in this codebase). That means it also
guards the second real site of this bug, the overhead-spend "All vendor
charges" details table's inline edit form (Vendor+Date, Amount+Category),
and would catch a third, not-yet-noticed instance anywhere else on these
two pages, not just the two spots this PR happened to touch.

Sweep performed for this PR (2026-09-15, `grep -n
'grid-template-columns:1fr 1fr\\|grid-template-columns:repeat(auto-fit'
webapp/app.py`, cross-referenced against every native
`type="date"`/`type="time"`/`type="number"` input in the file): the ONLY
two admin-form grid regions anywhere in webapp/app.py that pair a native
date/time/number input with a grid sibling are (1) the overhead-spend
"Add a charge" form's Date/Amount pair, and (2) the overhead-spend details
table's inline edit form's Vendor+Date and Amount+Category pairs. Every
other fixed/auto-fit grid checked (Users' "Add a member" form, the
Resources add/edit form's Coverage/Pricing selects, the Third-party
content admin form's Venue/Date-label pair — "Date label" is a plain text
input, not `type="date"`) contains no native date/time/number input at
all, so none of them are in scope for this defect and none are covered by
this test file.
"""
import os
import tempfile
from html.parser import HTMLParser

import pytest


_NATIVE_RISK_TYPES = {"date", "time", "number"}


def _style_dict_flat(style: str) -> str:
    """Return the style string with all whitespace stripped, so
    'min-width: 0' and 'min-width:0' and 'display: grid' all normalize to
    a single comparable form regardless of incidental spacing."""
    return "".join((style or "").split())


class _GridItemMinWidthChecker(HTMLParser):
    """Walks the rendered HTML looking for a <div style="display:grid;...">
    region that contains a native date/time/number input, and requires
    min-width:0 to appear somewhere inside that region (on the grid div
    itself, a child div, or the input). Both flags propagate up through
    div nesting so it doesn't matter which fix shape (wrapping div vs.
    input-is-the-grid-item) is used at a given site."""

    def __init__(self):
        super().__init__()
        # Stack of frames for every currently-open <div>.
        self._stack = []
        # Completed grid regions that had a native input but no min-width:0.
        self.violations = []  # list of (line, col) of the offending grid div

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        style = _style_dict_flat(attrs_d.get("style", ""))
        if tag == "div":
            frame = {
                "is_grid": "display:grid" in style,
                "has_native_input": False,
                "has_min_width_zero": "min-width:0" in style,
                "pos": self.getpos(),
            }
            self._stack.append(frame)
        elif tag == "input":
            is_native = attrs_d.get("type") in _NATIVE_RISK_TYPES
            has_mw0 = "min-width:0" in style
            if self._stack:
                top = self._stack[-1]
                top["has_native_input"] = top["has_native_input"] or is_native
                top["has_min_width_zero"] = top["has_min_width_zero"] or has_mw0

    def handle_startendtag(self, tag, attrs):
        # Some renderers may emit <input ... /> as a self-closing tag;
        # html.parser routes that here instead of handle_starttag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag != "div" or not self._stack:
            return
        frame = self._stack.pop()
        if frame["is_grid"] and frame["has_native_input"] and not frame["has_min_width_zero"]:
            self.violations.append(frame["pos"])
        if self._stack:
            parent = self._stack[-1]
            parent["has_native_input"] = parent["has_native_input"] or frame["has_native_input"]
            parent["has_min_width_zero"] = parent["has_min_width_zero"] or frame["has_min_width_zero"]


def _assert_no_grid_overlap_risk(html: str, label: str):
    checker = _GridItemMinWidthChecker()
    checker.feed(html)
    assert checker.violations == [], (
        f"{label}: found a display:grid region containing a native "
        f"date/time/number input with no min-width:0 anywhere inside it "
        f"(at parser positions {checker.violations}) — this is the exact "
        f"cross-engine overlap bug (grid items default to min-width:auto, "
        f"so a track can't shrink below the input's own content minimum, "
        f"which is larger in WebKit than in Chromium). Add min-width:0 to "
        f"the grid item (or the input itself)."
    )


def test_checker_catches_the_original_bug_shape():
    """Prove the checker actually fails on the shape this bug had before
    either fix — a grid item is only as good as its ability to fail."""
    bad = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;">'
        '<div><input type="date" name="date"></div>'
        '<div><input type="number" name="amount"></div>'
        "</div>"
    )
    checker = _GridItemMinWidthChecker()
    checker.feed(bad)
    assert len(checker.violations) == 1


def test_checker_passes_the_wrapping_div_fix_shape():
    """The Add-a-charge form's fix shape: min-width:0 on a wrapping div
    around each native input."""
    good = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;">'
        '<div style="min-width:0;"><input type="date" name="date"></div>'
        '<div style="min-width:0;"><input type="number" name="amount"></div>'
        "</div>"
    )
    checker = _GridItemMinWidthChecker()
    checker.feed(good)
    assert checker.violations == []


def test_checker_passes_the_direct_input_fix_shape():
    """The overhead-details inline-edit-form fix shape: min-width:0
    directly on the input, which is itself the grid item (no wrapping div)."""
    good = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;">'
        '<input type="text" name="vendor" style="padding:6px;">'
        '<input type="date" name="date" style="min-width:0;padding:6px;">'
        "</div>"
    )
    checker = _GridItemMinWidthChecker()
    checker.feed(good)
    assert checker.violations == []


def test_checker_ignores_grids_with_no_native_risky_input():
    """A display:grid region with no date/time/number input at all is not
    in scope for this defect and must not be flagged, whatever its
    min-width situation — e.g. the Users 'Add a member' form (text/email/
    select fields only)."""
    fine = (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">'
        '<input type="text" name="username">'
        '<input type="email" name="email">'
        "</div>"
    )
    checker = _GridItemMinWidthChecker()
    checker.feed(fine)
    assert checker.violations == []


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


def test_add_a_charge_form_has_no_grid_overlap_risk(admin_client):
    client, appmod, db = admin_client
    resp = client.get("/admin/overhead-spend")
    assert resp.status_code == 200
    html = resp.text
    assert "Add a charge" in html
    assert 'type="date" name="date"' in html
    assert 'type="number" step="0.01" name="amount"' in html
    _assert_no_grid_overlap_risk(html, "/admin/overhead-spend (Add a charge)")


def test_overhead_details_inline_edit_form_has_no_grid_overlap_risk(admin_client):
    client, appmod, db = admin_client
    lib = appmod.Library(db)
    lib.add_manual_overhead("Railway", "2026-09-01", 20.0, category="Infrastructure", note="Hosting")
    lib.close()

    resp = client.get("/admin/overhead-spend/details")
    assert resp.status_code == 200
    html = resp.text
    # Sanity: the inline edit form actually rendered (not an empty page).
    assert "Railway" in html
    assert 'onclick="toggleOverheadEdit(' in html
    assert 'type="date" name="date"' in html
    _assert_no_grid_overlap_risk(html, "/admin/overhead-spend/details (inline edit form)")
