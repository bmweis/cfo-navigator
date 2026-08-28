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


def test_reader_body_typography_is_unchanged_brand_serif():
    """Distraction-free mode must not swap in a different typeface (e.g. an
    Instapaper-style serif) — it only changes which panes are visible.
    The reader body keeps the site's own brand font stack."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text()
    assert "'Source Serif 4',Georgia,serif" in src
