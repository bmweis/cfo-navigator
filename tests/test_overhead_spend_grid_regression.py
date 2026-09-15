"""Regression guard for the overhead-spend Date/Amount overlap bug, which
shipped THREE times before landing on a fix with no failure mode left to get
wrong.

Round 1 (2026-08-28): a rigid `1fr 1fr` grid overflowed the whole page at
phone widths, fixed by switching to `repeat(auto-fit,minmax(140px,1fr))`.
Round 2 (2026-09, PR 553): that fix never accounted for WebKit's larger
native `<input type="date">` content-minimum — a grid item defaults to
`min-width:auto`, so its track can't shrink below the item's own content
minimum even inside a `minmax()` track. Verified in Chromium only (the
fix's own code comment cited "~160px in Chromium"), it still overlapped on
Brian's real iPhone 16 Pro (WebKit). Round 2's own fix, `min-width:0` on the
grid items, ALSO didn't survive contact with WebKit — confirmed by a real
screenshot from the deployed fix, Date's input still rendering underneath
Amount's left edge.

Round 3 (this file) stops trying to make a two-column layout survive every
engine's own native-input sizing quirks and stacks the two fields into a
single column below 640px instead. **A single-column layout has no shared
row for two fields' content-minimums to collide in — there is no CSS Grid
mechanism left for this to fail through, in any engine, known or unknown.**
640px is not a measured WebKit number (this sandbox cannot install
Playwright's WebKit browser — `playwright install webkit` gets a 403 policy
denial against playwright.download.prss.microsoft.com and cdn.playwright.dev,
confirmed via the agent proxy's own status endpoint as a genuine
organizational policy block, not transient) — it's picked to be comfortably
above anything a native date input is ever likely to demand, and matches
this codebase's own `.page-form` width tier.

Per this repo's own testing convention (see tests/test_screenshot_capture.py
and tests/test_app_screenshot.py, both of which MOCK Playwright rather than
launch a real browser in CI), this guard is a static, rendered-HTML/CSS-level
check, not a live browser render — consistent with how this suite already
verifies Playwright-adjacent behavior without depending on a real browser
being available in every environment that runs it.
"""
import os
import re
import tempfile

import pytest


def _extract_rule_block(css: str, selector_pattern: str) -> str | None:
    """Find the FIRST `{selector}{...}` block matching selector_pattern (a
    regex for the selector text, e.g. r'\\.oh-grid-2') and return its body
    (the text between the braces), or None if not found. Handles the
    doubled-brace f-string artifacts already resolved in rendered output —
    this operates on the real HTML response, not Python source."""
    m = re.search(selector_pattern + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def _grid_track_count(rule_body: str) -> int | None:
    """Parse a grid-template-columns declaration's track count from a rule
    body, or None if the property isn't present. Doesn't need to handle
    repeat()/minmax() — this file only ever compares a fixed 1fr/1fr shape
    against a fixed single-track shape."""
    m = re.search(r"grid-template-columns\s*:\s*([^;]+);", rule_body)
    if not m:
        return None
    value = m.group(1).strip()
    if "repeat(" in value or "minmax(" in value:
        # A dynamic track function — not the fixed 1-or-2-track shape this
        # file checks for. Treat as "more than one" defensively, since the
        # whole point here is confirming there's exactly one track at the
        # stacking breakpoint.
        return 2
    return len(value.split())


def _stacks_at_or_below(html: str, class_name: str, max_breakpoint: int = 640) -> bool:
    """True iff `html` contains a base `.{class_name}` rule with more than
    one grid track, AND an `@media(max-width:Npx)` block (N <= max_breakpoint)
    whose own `.{class_name}` rule collapses to exactly one track."""
    selector = re.escape(f".{class_name}")
    base_body = _extract_rule_block(html, selector)
    if base_body is None:
        return False
    base_tracks = _grid_track_count(base_body)
    if base_tracks is None or base_tracks < 2:
        return False

    for media_m in re.finditer(r"@media\s*\(\s*max-width\s*:\s*(\d+)px\s*\)\s*\{", html):
        breakpoint_px = int(media_m.group(1))
        if breakpoint_px > max_breakpoint:
            continue
        # Find the matching closing brace for this @media block by scanning
        # forward and tracking nested braces (the block contains one or more
        # CSS rules, each with their own {}).
        start = media_m.end()
        depth = 1
        i = start
        while i < len(html) and depth > 0:
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
            i += 1
        media_block = html[start:i - 1]
        override_body = _extract_rule_block(media_block, selector)
        if override_body is None:
            continue
        override_tracks = _grid_track_count(override_body)
        if override_tracks == 1:
            return True
    return False


def test_checker_catches_missing_stacking_override():
    """Prove the checker actually fails when the base rule has two tracks
    but nothing collapses it at a narrow width — the exact shape of the
    ORIGINAL bug (a fixed two-column grid with no stacking escape hatch)."""
    bad = "<style>.oh-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}</style>"
    assert not _stacks_at_or_below(bad, "oh-grid-2")


def test_checker_catches_a_breakpoint_that_is_too_wide():
    """A stacking override that only kicks in ABOVE 640px doesn't protect a
    402px iPhone viewport — the checker must reject it."""
    bad = (
        "<style>.oh-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}"
        "@media(max-width:900px){.oh-grid-2{grid-template-columns:1fr;}}</style>"
    )
    # 900 > 640, so this should NOT satisfy the default max_breakpoint=640 check.
    assert not _stacks_at_or_below(bad, "oh-grid-2", max_breakpoint=640)
    # But it DOES satisfy a looser check that allows a wider breakpoint.
    assert _stacks_at_or_below(bad, "oh-grid-2", max_breakpoint=900)


def test_checker_passes_a_real_stacking_shape():
    """The actual shape this PR ships: two tracks by default, one track at
    640px and below."""
    good = (
        "<style>.oh-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}"
        "@media(max-width:640px){.oh-grid-2{grid-template-columns:1fr;}}</style>"
    )
    assert _stacks_at_or_below(good, "oh-grid-2")


def test_checker_still_fails_the_old_auto_fit_minmax_shape():
    """The two PREVIOUS fixes (auto-fit/minmax, then min-width:0) never
    collapsed to a genuine single grid track at any breakpoint — both used
    a dynamic minmax() track that can still place two items side by side at
    some width. Confirm the checker correctly treats that shape as
    unprotected, since it's exactly the shape that overlapped on a real
    iPhone twice already."""
    old_broken = (
        "<style>.oh-grid-2{display:grid;"
        "grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;}</style>"
    )
    assert not _stacks_at_or_below(old_broken, "oh-grid-2")


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


def test_add_a_charge_form_stacks_date_and_amount_below_640px(admin_client):
    client, appmod, db = admin_client
    resp = client.get("/admin/overhead-spend")
    assert resp.status_code == 200
    html = resp.text
    assert "Add a charge" in html
    # The Date/Amount pair is now class-based, not an inline auto-fit/minmax
    # grid style — this is what actually lets a shared media query reach it.
    assert 'class="oh-grid-2"' in html
    assert 'type="date" name="date"' in html
    assert 'type="number" step="0.01" name="amount"' in html
    # The two previous, now-abandoned fix shapes must be genuinely gone from
    # this page's rendered output, not just superseded in source.
    assert "auto-fit,minmax(140px,1fr)" not in html
    assert 'style="min-width:0;"' not in html
    assert _stacks_at_or_below(html, "oh-grid-2")


def test_overhead_details_inline_edit_form_stacks_vendor_date_and_amount_category(admin_client):
    client, appmod, db = admin_client
    lib = appmod.Library(db)
    lib.add_manual_overhead("Railway", "2026-09-01", 20.0, category="Infrastructure", note="Hosting")
    lib.close()

    resp = client.get("/admin/overhead-spend/details")
    assert resp.status_code == 200
    html = resp.text
    assert "Railway" in html
    assert 'onclick="toggleOverheadEdit(' in html
    assert 'type="date" name="date"' in html
    # Both the Vendor+Date and Amount+Category rows share the same class —
    # confirm it appears (at least) twice, once per pairing.
    assert html.count('class="oh-grid-2"') >= 2
    assert "auto-fit,minmax(120px,1fr)" not in html
    assert "min-width:0;padding:6px 10px" not in html
    assert _stacks_at_or_below(html, "oh-grid-2")


def test_scroll_hint_has_a_real_gap_and_breathing_room(admin_client):
    """Second item from the follow-up report: the arrow glyph sat flush
    against 'Scroll for more' with no space, and the hint hugged the
    table's top-left corner. Confirm both are fixed in the shared
    .admin-scroll-hint rule (used on this page and on Software/Communities'
    admin tables alike)."""
    client, appmod, db = admin_client
    resp = client.get("/admin/overhead-spend/details")
    assert resp.status_code == 200
    html = resp.text
    rule = _extract_rule_block(html, re.escape(".admin-scroll-hint"))
    assert rule is not None
    gap_m = re.search(r"gap\s*:\s*(\d+)px", rule)
    assert gap_m is not None
    assert int(gap_m.group(1)) >= 10, "gap between the icon and text should have real breathing room, not the old cramped 6px"
    margin_m = re.search(r"margin\s*:\s*[\d.]+px\s+[\d.]+(?:px)?\s+(\d+)px", rule)
    assert margin_m is not None
    assert int(margin_m.group(1)) >= 12, "space below the hint (above the table) should be more than the old 8px"
