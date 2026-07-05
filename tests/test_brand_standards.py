"""Automated brand-standards check (see BRAND.md §8).

The whole site is rendered from inline strings in ``webapp/app.py``. The rules and
scanner live in ``linklib/brand_check.py`` (the single source of truth, also used by
the live checks dashboard at /admin/checks); this test holds the site source to them.

When you intentionally add a color, add it to ``AUX_COLORS`` in
``linklib/brand_check.py`` with a comment — that records the decision, which is the
point. New, undocumented colors fail the build.
"""
import pathlib

import pytest

from linklib import brand_check as bc

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "webapp" / "app.py").read_text(encoding="utf-8")


def test_no_brand_violations():
    """Palette + fonts: every color is a token or documented aux, fonts are on-brand,
    tokens and ramps are present. Any drift fails with a specific message."""
    problems = bc.findings(APP_SRC)
    assert not problems, "Brand-standards violations in webapp/app.py:\n- " + "\n- ".join(problems)


def test_no_banned_legacy_colors():
    found = sorted(bc.colors_in(APP_SRC) & bc.BANNED_COLORS)
    assert not found, f"Legacy/off-brand color(s) reintroduced: {', '.join(found)}"


def test_no_banned_fonts():
    found = bc.banned_fonts_used(APP_SRC)
    assert not found, f"Off-brand font(s) referenced: {', '.join(found)}"


@pytest.mark.parametrize("token", bc.EXPECTED_TOKENS)
def test_brand_token_present(token):
    assert f"{token}:" in APP_SRC, f"Brand token {token} missing from _CSS :root"


@pytest.mark.parametrize("family", list(bc.RAMPS))
def test_palette_ramp_present(family):
    tokens = bc.brand_token_colors(APP_SRC)
    missing = [c for c in bc.RAMPS[family] if c not in tokens]
    assert not missing, f"{family} ramp missing shades: {missing}"


def test_small_coral_text_flagged():
    """BRAND.md §2.3: coral/coral-deep text under 18px is never sanctioned."""
    bad = '<div style="font-size:14px;color:var(--coral-deep);">too small</div>'
    assert bc.small_coral_text_spans(bad)


def test_small_coral_text_allows_navy_on_coral_wash():
    """The sanctioned callout pattern — coral-wash background, navy text — must
    not be flagged, regardless of size."""
    ok = '<div style="background:var(--coral-wash);font-size:14px;color:var(--navy);">fine</div>'
    assert not bc.small_coral_text_spans(ok)


def test_small_coral_text_allows_large_coral():
    """Large coral display text (>=18px) is a sanctioned use (stat call-outs)."""
    ok = '<div style="font-size:24px;color:var(--coral);">42%</div>'
    assert not bc.small_coral_text_spans(ok)
