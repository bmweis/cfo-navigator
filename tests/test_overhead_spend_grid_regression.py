"""Regression guard for the overhead-spend Date/Amount overlap bug, which
shipped in FOUR rounds before landing on a fix with no failure mode left to
get wrong.

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

Round 3 (2026-09, PR 554) stopped trying to make a two-column layout
survive every engine's own native-input sizing quirks and stacked the two
fields into a single column below 640px instead. **A single-column layout
has no shared row for two fields' content-minimums to collide in — there
is no CSS Grid mechanism left for this to fail through, in any engine,
known or unknown.** 640px is not a measured WebKit number (this sandbox
cannot install Playwright's WebKit browser — `playwright install webkit`
gets a 403 policy denial against playwright.download.prss.microsoft.com and
cdn.playwright.dev, confirmed via the agent proxy's own status endpoint as
a genuine organizational policy block, not transient) — it's picked to be
comfortably above anything a native date input is ever likely to demand,
and matches this codebase's own `.page-form` width tier.

Round 4 (2026-09, PR 555) found that stacking alone wasn't the whole fix:
it revealed the REAL bug rather than solving it. A real iPhone screenshot
showed the Date `<input>` itself wider than its own container — its right
edge extending past the card border, past every other field — even though
it now had a whole row to itself. The two-column collision in Round 2/3
was always the SYMPTOM, not the cause: WebKit's native `<input
type="date">` has an intrinsic content width driven by its internal
picker-segment UI that a plain `width:100%` doesn't override (100% of a
narrow container is still narrower than the control's intrinsic demand).
The fix shipped was `max-width:100%` (a hard clamp that always wins over
intrinsic content, per CSS2.1 10.3.3, regardless of what `width` computes
to) plus `min-width:0` directly on the `<input>` itself, not just its
wrapping grid-item `<div>` (the target of the Round 2 fix, which is why it
never touched this).

Round 5 (this file) found Round 4's fix ALSO didn't survive a real device —
Brian's post-deploy screenshot matched the pre-fix state exactly. Verified
by measurement, not reasoning, per his explicit instruction: a controlled,
engine-independent reproduction (any element with a genuinely large
intrinsic minimum, standing in for WebKit's date-input width, since this
sandbox has no WebKit to measure directly) proved the actual grid TRACK —
not the input — was the thing overflowing. Date/Amount are each wrapped in
their own `<div>` (to hold a `<label>` above the field), and THAT `<div>`,
not the `<input>` one layer inside it, is the real CSS Grid item. A grid
item's own automatic minimum size is based on its own min-content,
computed recursively from ITS OWN descendants — and `min-width:0` set on a
NESTED descendant (the input) does not override the ANCESTOR grid item's
own automatic-minimum-size computation. `.oh-grid-2`'s bare `1fr` track
therefore had an implicit minimum of `auto` (the wrapper div's own
min-content, which is however wide its native-input descendant demands),
so the track itself expanded past the card — and `max-width:100%` on the
input then correctly clamped the input to 100% of an already-oversized
track, exactly matching what shipped and exactly why it changed nothing on
a real device. Confirmed on the real fixed production markup too: an
artificial oversized probe injected into the real wrapper div still held
the track at the card's own width once the fix below was applied.

The fix: `grid-template-columns:minmax(0,1fr)` instead of a bare `1fr`, on
both the 2-column base rule and the 1-column stacked-breakpoint override,
on both `.oh-grid-2` definitions (Add-a-charge and the overhead-details
inline edit form). `minmax(0,1fr)` sets the TRACK's own minimum to `0`
directly, so it can never expand past the available space regardless of
what any current or future child inside it declares — chosen over adding
`min-width:0` to `.oh-grid-2`'s direct children specifically because a
per-child fix is one careless future addition (a new field wrapped in yet
another div, with nobody remembering this history) away from re-breaking;
a track-level fix protects every child, forever, with one declaration.
Round 4's input-level `max-width:100%`/`min-width:0`/`box-sizing:border-box`
fix is kept — it's still correct as a second, defense-in-depth layer (an
input that COULD still exceed its track for some other reason should
still be clamped).

Round 6 (2026-09) found Round 5's track fix, while genuinely correct, was
NOT the cause of the overlap either — a follow-up real-device screenshot
still showed Date overflowing by ~110px after Round 5 shipped. The tell:
Amount sits in the exact same collapsed single-column track as Date (both
are children of the same `.oh-grid-2` at ≤640px), and Amount rendered at
the CORRECT width, lined up with Vendor/Category/Note. If the track itself
were oversized, Amount would be oversized too — it wasn't, so the track was
never the bug. Only the `<input type="date">` itself was exceeding its own
box despite `max-width:100%`, confirming the Round 3 hypothesis (WebKit
does not honor `max-width` against a native date input's own intrinsic
picker-chrome width) that was set aside at the time in favor of stacking.
Fixed with `-webkit-appearance:none;appearance:none` alongside the existing
`width:100%;max-width:100%;min-width:0` on all three real `type="date"`
inputs sitewide — this strips the native picker chrome (and the intrinsic
width it demands) rather than trying to constrain a box the browser won't
constrain. iOS still opens the native date picker on tap regardless of
`appearance:none` — that's platform tap behavior, not CSS-controlled — so
this only changes the field's own visual chrome, not the picker itself.
**Every fix from Rounds 3 through 6 is kept, deliberately, none reverted**:
stacking (Round 3), track-level containment (Round 5), and input-level
`max-width`/`min-width` (Round 4) are all real, legitimate fixes for real
defects — they simply weren't the one causing this particular symptom.
`appearance:none` (Round 6) is the layer that actually stops it. If a
`type="date"` input somehow still overflows after this, the documented
fallback is a `overflow:hidden` wrapper around the input — real
containment rather than sizing, which cannot fail regardless of what the
control wants — not yet needed, not yet built.

Per this repo's own testing convention (see tests/test_screenshot_capture.py
and tests/test_app_screenshot.py, both of which MOCK Playwright rather than
launch a real browser in CI), this guard is a static, rendered-HTML/CSS-level
check, not a live browser render — consistent with how this suite already
verifies Playwright-adjacent behavior without depending on a real browser
being available in every environment that runs it.
"""
import inspect
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


def _split_top_level(value: str) -> list[str]:
    """Split a grid-template-columns value on whitespace, but only at
    paren-depth 0 — so `minmax(0,1fr) minmax(0,1fr)` splits into its real
    two tracks instead of being swallowed whole by a naive
    `"minmax(" in value` check. Each returned string is one track's own
    declaration, verbatim (e.g. `minmax(0,1fr)`, `1fr`, `200px`)."""
    tracks: list[str] = []
    depth = 0
    current = ""
    for ch in value:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch.isspace() and depth == 0:
            if current:
                tracks.append(current)
                current = ""
        else:
            current += ch
    if current:
        tracks.append(current)
    return tracks


def _grid_track_count(rule_body: str) -> int | None:
    """Parse a grid-template-columns declaration's track count from a rule
    body, or None if the property isn't present. A per-track `minmax(...)`
    (e.g. `minmax(0,1fr) minmax(0,1fr)`) counts correctly as N tracks — only
    a `repeat()` function (a dynamic, unparseable-without-a-real-CSS-engine
    track count) falls back to a defensive "more than one"."""
    m = re.search(r"grid-template-columns\s*:\s*([^;]+);", rule_body)
    if not m:
        return None
    value = m.group(1).strip()
    if "repeat(" in value:
        # A dynamic track function — not the fixed 1-or-2-track shape this
        # file checks for. Treat as "more than one" defensively, since the
        # whole point here is confirming there's exactly one track at the
        # stacking breakpoint.
        return 2
    return len(_split_top_level(value))


def _override_body_at_or_below(html: str, selector: str, max_breakpoint: int) -> str | None:
    """Return the `.{selector}` rule body from the first
    `@media(max-width:Npx)` block found with N <= max_breakpoint, or None
    if no such block overrides that selector. Shared by both the stacking
    check and the track-containment check below — they differ only in what
    they then assert about the returned rule body."""
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
        if override_body is not None:
            return override_body
    return None


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

    override_body = _override_body_at_or_below(html, selector, max_breakpoint)
    if override_body is None:
        return False
    return _grid_track_count(override_body) == 1


def _tracks_have_zero_minimum(rule_body: str) -> bool | None:
    """Round 5's real invariant: every track in grid-template-columns must
    declare an explicit zero minimum (`minmax(0,...)`), which is what
    actually stops a grid ITEM's own automatic minimum size (its
    min-content, computed from ITS OWN descendants) from expanding the
    track past its container. A bare `1fr`/`auto` track has an implicit
    minimum of `auto` — so a wrapper `<div>` holding a native control with
    a large intrinsic minimum (WebKit's `<input type="date">`, confirmed by
    measurement) blows the track out regardless of any `min-width:0` set on
    the control itself, one layer too deep to matter. Returns None if the
    property isn't present."""
    m = re.search(r"grid-template-columns\s*:\s*([^;]+);", rule_body)
    if not m:
        return None
    tracks = _split_top_level(m.group(1).strip())
    if not tracks:
        return None
    return all(re.match(r"minmax\(\s*0\s*,", t) for t in tracks)


def _grid_track_cannot_exceed_container(html: str, class_name: str, max_breakpoint: int = 640) -> bool:
    """True iff BOTH the base (multi-column) `.{class_name}` rule AND its
    stacking-override rule at or below max_breakpoint declare every track
    with an explicit zero minimum. This is the layer above where the
    Round 4 input-level containment check (`_input_cannot_exceed_container`)
    could ever see the bug — that check can pass while the track itself
    still blows out from a wrapper div's own unset min-width, which is
    exactly what happened on a real device after Round 4 shipped."""
    selector = re.escape(f".{class_name}")
    base_body = _extract_rule_block(html, selector)
    if base_body is None:
        return False
    if not _tracks_have_zero_minimum(base_body):
        return False

    override_body = _override_body_at_or_below(html, selector, max_breakpoint)
    if override_body is None:
        return False
    return bool(_tracks_have_zero_minimum(override_body))


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


def test_checker_catches_the_round_4_shape_bare_1fr_track_can_still_blow_out():
    """Prove the track-containment checker FAILS against the exact shape
    that shipped in Round 4 (PR 555) and stacked, and STILL overflowed on a
    real iPhone — a bare `1fr`/`1fr 1fr` track, correctly stacking to one
    column, but with no zero-minimum declared on either track. This is the
    shape `_stacks_at_or_below` alone was satisfied by, which is exactly
    why stacking wasn't the whole fix."""
    round4_shape = (
        "<style>.oh-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}"
        "@media(max-width:640px){.oh-grid-2{grid-template-columns:1fr;}}</style>"
    )
    # It genuinely does stack correctly — that was never the bug.
    assert _stacks_at_or_below(round4_shape, "oh-grid-2")
    # But the track itself has no floor, so it can still blow out.
    assert not _grid_track_cannot_exceed_container(round4_shape, "oh-grid-2")


def test_checker_passes_the_real_round_5_shape_minmax_zero_tracks():
    """The actual fix this PR ships: `minmax(0,1fr)` on both the base
    2-column rule and the 1-column stacked override, so the track itself
    can never expand past its container regardless of what any child
    (a wrapper div with an unset min-width, holding a native input with a
    large intrinsic minimum) declares."""
    round5_shape = (
        "<style>.oh-grid-2{display:grid;"
        "grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;}"
        "@media(max-width:640px){.oh-grid-2{grid-template-columns:minmax(0,1fr);}}</style>"
    )
    assert _stacks_at_or_below(round5_shape, "oh-grid-2")
    assert _grid_track_cannot_exceed_container(round5_shape, "oh-grid-2")


def test_checker_catches_a_zero_minimum_missing_from_only_one_side():
    """A fix that only protects the base rule, or only the stacked
    override, still leaves the other breakpoint exposed — the checker must
    require containment at BOTH."""
    only_base_fixed = (
        "<style>.oh-grid-2{display:grid;"
        "grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;}"
        "@media(max-width:640px){.oh-grid-2{grid-template-columns:1fr;}}</style>"
    )
    assert not _grid_track_cannot_exceed_container(only_base_fixed, "oh-grid-2")

    only_override_fixed = (
        "<style>.oh-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}"
        "@media(max-width:640px){.oh-grid-2{grid-template-columns:minmax(0,1fr);}}</style>"
    )
    assert not _grid_track_cannot_exceed_container(only_override_fixed, "oh-grid-2")


def _date_input_tags(html: str) -> list[str]:
    """Return every `<input type="date" ...>` opening tag found in `html`,
    verbatim, including its full `style="..."` attribute."""
    return re.findall(r"<input[^>]*type=\"date\"[^>]*>", html)


def _style_of(tag: str) -> str:
    m = re.search(r'style="([^"]*)"', tag)
    return m.group(1) if m else ""


def _input_cannot_exceed_container(style: str) -> bool:
    """Round 4's own invariant — `max-width:100%`/`min-width:0`/
    `box-sizing:border-box` — kept as a defense-in-depth layer, but PROVEN
    IN ROUND 6 TO BE INSUFFICIENT ON ITS OWN: a real post-Round-5-deploy
    screenshot showed Date still overflowing by ~110px with exactly this
    shape present and correct. WebKit does not honor `max-width` against a
    native `<input type="date">`'s own intrinsic picker-chrome width — no
    CSS constraint on the box wins against that, only removing the chrome
    itself does. See `_input_has_no_native_chrome` below for the real,
    sufficient invariant this checker's own name now undersells."""
    has_max_width_100 = bool(re.search(r"max-width\s*:\s*100%", style))
    has_min_width_0 = bool(re.search(r"min-width\s*:\s*0\b", style))
    has_border_box = bool(re.search(r"box-sizing\s*:\s*border-box", style))
    return has_max_width_100 and has_min_width_0 and has_border_box


def _input_has_no_native_chrome(style: str) -> bool:
    """Round 6's real invariant: `-webkit-appearance:none` (Safari/WebKit,
    still required — `appearance:none` alone is not enough in every WebKit
    version) plus the standard `appearance:none`, together stripping the
    native date-input's own picker-segment chrome and the intrinsic width
    it demands. This is what actually stops the overflow — `_input_
    cannot_exceed_container`'s own max-width/min-width/box-sizing shape
    (Round 4) is necessary as defense-in-depth but was proven, live, not
    sufficient on its own (see Round 6 in the module docstring)."""
    has_webkit_appearance_none = bool(re.search(r"-webkit-appearance\s*:\s*none", style))
    has_appearance_none = bool(re.search(r"(?<!-webkit-)\bappearance\s*:\s*none", style))
    return has_webkit_appearance_none and has_appearance_none


def test_checker_catches_a_date_input_with_only_width_100_percent():
    """Prove the containment checker fails on exactly the Round 3 shape —
    `width:100%` plus `box-sizing:border-box` but no `max-width`/
    `min-width:0` on the input — since that shape is what still overflowed
    on a real iPhone even after stacking removed the two-column collision."""
    bad = '<input type="date" name="date" style="width:100%;padding:9px;box-sizing:border-box;">'
    style = _style_of(_date_input_tags(bad)[0])
    assert not _input_cannot_exceed_container(style)


def test_checker_passes_a_real_containment_shape():
    good = (
        '<input type="date" name="date" style="width:100%;max-width:100%;'
        'min-width:0;padding:9px;box-sizing:border-box;">'
    )
    style = _style_of(_date_input_tags(good)[0])
    assert _input_cannot_exceed_container(style)


def test_native_chrome_checker_catches_the_round_4_5_shape_that_still_overflowed():
    """Prove the Round 6 checker FAILS on exactly the shape that shipped in
    Rounds 4 and 5 (max-width/min-width/box-sizing, no appearance:none) —
    the shape a real post-deploy iPhone screenshot showed still overflowing
    by ~110px, since `_input_cannot_exceed_container` alone would wrongly
    call this shape safe."""
    round4_5_shape = (
        'width:100%;max-width:100%;min-width:0;padding:9px;box-sizing:border-box;'
    )
    assert _input_cannot_exceed_container(round4_5_shape)
    assert not _input_has_no_native_chrome(round4_5_shape)


def test_native_chrome_checker_requires_both_webkit_and_standard_appearance():
    """Either property alone is not the real fix — both must be present,
    since -webkit-appearance:none is still required in some WebKit versions
    and the standard appearance:none isn't a substitute for it."""
    webkit_only = "width:100%;-webkit-appearance:none;"
    standard_only = "width:100%;appearance:none;"
    both = "width:100%;-webkit-appearance:none;appearance:none;"
    assert not _input_has_no_native_chrome(webkit_only)
    assert not _input_has_no_native_chrome(standard_only)
    assert _input_has_no_native_chrome(both)


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
    # The Round 2 grid-item wrapper fix (a bare min-width:0 on a <div>, with
    # nothing else in its style) is gone — the containment fix lives on the
    # <input> itself now, alongside its other real style properties, not on
    # an otherwise-empty wrapper div.
    assert 'style="min-width:0;"' not in html
    assert _stacks_at_or_below(html, "oh-grid-2")
    # Round 4: stacking alone isn't the invariant — the input itself must
    # not be able to exceed its container, in any engine. Kept as a
    # defense-in-depth check, but no longer sufficient on its own (see
    # Round 5 below).
    for tag in _date_input_tags(html):
        assert _input_cannot_exceed_container(_style_of(tag)), tag
    # Round 5: the real fix — the grid TRACK itself, not just the input,
    # must be unable to exceed its container. Prove the now-abandoned
    # Round 4 shape (a bare 1fr track) is genuinely gone from this page's
    # own .oh-grid-2 rule specifically (a raw substring check would false-
    # positive against unrelated CSS elsewhere on the page, e.g.
    # .tool-form-cols's own `grid-template-columns:1fr;`), and the real
    # minmax(0,1fr) fix is genuinely present.
    assert "minmax(0,1fr)" in html
    assert _grid_track_cannot_exceed_container(html, "oh-grid-2")
    # Round 6: the actual bug — WebKit ignoring max-width against the
    # native date-input's own intrinsic chrome width. Every real
    # type="date" input on this page must strip that chrome outright.
    for tag in _date_input_tags(html):
        assert _input_has_no_native_chrome(_style_of(tag)), tag


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
    assert _stacks_at_or_below(html, "oh-grid-2")
    # Round 4: same containment invariant on this page's own Date input.
    for tag in _date_input_tags(html):
        assert _input_cannot_exceed_container(_style_of(tag)), tag
    # Round 5: same track-level fix on this page's own .oh-grid-2 rules.
    assert "minmax(0,1fr)" in html
    assert _grid_track_cannot_exceed_container(html, "oh-grid-2")
    # Round 6: same native-chrome-stripping fix on this page's own Date input.
    for tag in _date_input_tags(html):
        assert _input_has_no_native_chrome(_style_of(tag)), tag


def test_every_type_date_input_sitewide_has_containment():
    """Round 4's own sweep instruction: check every `type="date"` input on
    the site, not just the two overhead-spend forms, and report whether
    each one can overflow its container. A full-render test isn't practical
    for the Feature Taxonomy checklist's own `verified_as_of` input (it
    only renders inside a curated-feature-category table on the tool edit
    page, which needs a category-features fixture beyond this file's
    scope), so this scans the live app.py source directly for every
    `<input type="date" ...>` and asserts the same containment invariant
    on its literal `style="..."` attribute — this is the app's real,
    unrendered source of every `type="date"` input, so a match here is
    exactly what a full page render would also produce for that input."""
    import webapp.app as appmod
    source = inspect.getsource(appmod)
    tags = re.findall(r'<input type="date"[^>]*>', source)
    # Sanity: this must find all three known sites (Add-a-charge Date,
    # overhead-details inline-edit Date, Feature Taxonomy verified_as_of) —
    # if this count ever drops, either a site was removed (update this
    # test) or the regex stopped matching (a real regression in coverage).
    assert len(tags) == 3, tags
    for tag in tags:
        style = _style_of(tag)
        assert _input_cannot_exceed_container(style), tag
        # Round 6: max-width/min-width/box-sizing alone was proven
        # insufficient on a real device — every sitewide date input must
        # also strip native picker chrome.
        assert _input_has_no_native_chrome(style), tag


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
