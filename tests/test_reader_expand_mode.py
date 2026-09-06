"""Regression coverage for the corrected Reader "expand"/distraction-free
mode (2026-08). The original build only shrank `.rr-list-pane` to a 220px
sliver on expand, leaving `.rr-rail` (the left nav rail) fully visible on
desktop — confirmed live as a real bug, not the intended design, against
Instapaper's own expand view (both the left rail AND the article list
disappear completely, leaving just the centered reading pane with the
sticky action bar still visible). Fixed: `.rr-shell.rr-focus-mode` now hides
`.rr-rail` and `.rr-list-pane` (and their resize handles) outright, on every
viewport — no more sliver mechanism. The existing `#rr-reader-expand` button
in the sticky `.rr-reader-header` doubles as the collapse-back affordance,
since there's no sliver left to click through.
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


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_focus_mode_hides_rail_and_list_pane_outright(env):
    c = _admin_client(env)
    r = c.get("/read")
    assert r.status_code == 200
    html = r.text
    assert ".rr-shell.rr-focus-mode .rr-rail" in html
    assert ".rr-shell.rr-focus-mode .rr-list-pane" in html
    # Both selectors must land in the SAME rule (comma-joined) ending in
    # display:none, not a sliver-narrowing flex/max-width treatment.
    rule_start = html.index(".rr-shell.rr-focus-mode .rr-rail")
    rule_end = html.index("}", rule_start)
    rule = html[rule_start:rule_end + 1]
    assert ".rr-list-pane" in rule
    assert "display:none" in rule
    assert "220px" not in rule and "260px" not in rule


def test_focus_mode_also_hides_both_resize_handles(env):
    c = _admin_client(env)
    html = c.get("/read").text
    rule_start = html.index(".rr-shell.rr-focus-mode .rr-rail")
    rule_end = html.index("}", rule_start)
    rule = html[rule_start:rule_end + 1]
    assert "#rr-resize-rail" in rule
    assert "#rr-resize-list" in rule


def test_sliver_mechanism_is_gone():
    """The sliver (list-pane-narrowed-to-a-title-strip) treatment predates
    this fix and must be fully removed, not left dead alongside the new
    hide-outright rule."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text()
    assert "rr-sliver" not in src
    assert "rrUpdateSliver" not in src


def test_expand_button_still_present_as_the_collapse_back_affordance(env):
    c = _admin_client(env)
    html = c.get("/read").text
    assert 'id="rr-reader-expand"' in html
    assert "rrToggleFocusMode()" in html
    # Confirm the toggle function still swaps the button's own icon/title
    # between expand and collapse — this is what stands in for the
    # reference's dedicated top-left arrow, since there's no sliver button
    # to click through any more.
    fn_start = html.index("function rrSetFocusMode(")
    fn_end = html.index("function rrToggleFocusMode(")
    body = html[fn_start:fn_end]
    assert "RR_ICON_COLLAPSE" in body and "RR_ICON_EXPAND" in body


def test_reader_title_and_body_match_sitewide_typography(env):
    """2026-09: the merged reader's .rr-reader-title/.rr-reader-body-text
    both used to fall through to 'Source Serif 4',Georgia,serif — the title
    unintentionally (a leftover never updated to match the standalone
    /read/{id} reader's own Outfit h1), the body intentionally at first (a
    real, then-current BRAND.md exception) but reverted per direct
    confirmation that it read wrong live. A first pass moved the body to
    Outfit alongside the title; checked against the site's actual published
    long-form content (an Original Content article's .oc-body p) and found
    body copy is DM Sans there, Outfit only for the heading — so this
    settled on ordinary sitewide typography instead: Outfit heading,
    DM Sans body, no reader-specific exception at all. Distraction-free
    mode must not swap in a different typeface either — it only changes
    which panes are visible, so the same assertion holds in both states."""
    c = _admin_client(env)
    html = c.get("/read").text
    assert "font-family:'Source Serif 4'" not in html
    assert ".rr-reader-title{font-family:var(--font-head)" in html
    assert ".rr-reader-body-text{font-family:var(--font-body)" in html


def test_reader_expand_icon_points_nw_se_not_ne_sw(env):
    """2026-09: RR_ICON_EXPAND/RR_ICON_COLLAPSE originally sat on the NE/SW
    diagonal (Feather's stock maximize-2/minimize-2 — arrows toward the
    top-right/bottom-left corners), confirmed wrong live; mirrored onto the
    NW/SE diagonal (top-left/bottom-right) instead, keeping each icon's own
    outward (default state)/inward (focus-mode state) direction unchanged.
    Pin the actual corner coordinates so a future edit can't silently drift
    back onto the old axis."""
    c = _admin_client(env)
    html = c.get("/read").text
    # NW/SE corner coordinates present (the outward-pointing default icon's
    # brackets sit at (3,3) and (21,21); the inward-pointing focus-mode
    # icon's shafts trail out to the same two corners).
    assert 'points="9 3 3 3 3 9"' in html
    assert 'points="15 21 21 21 21 15"' in html
    assert 'points="20 14 14 14 14 20"' in html
    assert 'points="4 10 10 10 10 4"' in html
    # The old NE/SW corner brackets must be gone.
    assert 'points="15 3 21 3 21 9"' not in html
    assert 'points="9 21 3 21 3 15"' not in html
