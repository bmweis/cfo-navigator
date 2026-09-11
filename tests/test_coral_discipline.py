"""Coral discipline (PR 16, 2026-09) — at most one coral "moment" per public
page, checked signed out. See webapp/app.py's `coral_moment_problems()` own
module comment for the full design and its honest, stated limits (signed-out
snapshot only, inline `style=` backgrounds only, non-admin/no-path-param
routes only).

Found 2026-09: `_CARD_ICON_STYLES` cycled seafoam/navy/coral by array index,
so whichever card landed in the third slot spent the site's one rare accent
by accident, not deliberate placement — Communities on /tools, and (twice
over) on the homepage. This suite proves the detector actually catches that
class of regression (not just passes trivially against whatever the current
code happens to render) and that the real, current pages are clean.
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


def test_no_coral_moment_violations_today(env):
    """The real, current state: every checked public page has at most one
    coral moment, signed out. This is the "passes once the gap is fixed"
    half of the build brief — it exercises the live routes exactly as
    committed, not a rigged fixture."""
    assert env.coral_moment_problems() == []


def test_detector_actually_catches_an_injected_second_coral_moment(env, monkeypatch):
    """Proves the detector isn't trivially passing: inject a second coral
    background into the MCP callout (the one deliberate coral moment /tools
    and the homepage already have) and confirm both pages are flagged."""
    orig = env._mcp_callout_html

    def rigged(*, compact=False):
        return orig(compact=compact) + '<div style="background:var(--coral-wash);">extra</div>'

    monkeypatch.setattr(env, "_mcp_callout_html", rigged)

    problems = env.coral_moment_problems()
    assert any(p.startswith("/ ") for p in problems), problems
    assert any(p.startswith("/tools ") for p in problems), problems


def test_card_icon_styles_no_longer_cycles_coral(env):
    """The actual fix: coral is dropped from the icon cycle entirely, not
    just reordered — every entry is seafoam or navy."""
    for bg, stroke in env._CARD_ICON_STYLES:
        assert "coral" not in bg
        assert "coral" not in stroke
    assert len(env._CARD_ICON_STYLES) == 2


def test_mcp_callout_is_coral_not_navy(env):
    """The freed-up coral moment: the MCP callout is the one deliberate
    coral use on /tools and the homepage now, not navy."""
    html = env._mcp_callout_html()
    assert "var(--coral-wash)" in html
    assert "var(--navy-wash)" not in html
    # non-clickable statement of capability, never a coral button
    assert "<a " not in html
    assert "<button" not in html


def test_checks_run_all_reports_coral_discipline_pass(env):
    # Unlike hub_nav_orphans (pure route/tuple introspection), this check
    # does real signed-out HTTP GETs against routes that touch the DB, so it
    # needs the `env` fixture's real (empty, schema-initialized) database —
    # not the ambient/unset LINKLIB_DB the process might otherwise have.
    from webapp import checks
    results = checks.run_all()
    row = next(r for r in results if r["name"] == "Coral discipline (one moment per page)")
    assert row["ok"] is True
    assert row["where"] == "Live + CI"
