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


# --- PR 15: PR/issue-number false positive (the third occurrence — #465,
# #318, #529) is fixed at the checker itself, not by rewording every future
# comment that happens to mention a 3-digit PR/issue number. ---------------

def test_pr_number_reference_does_not_flag_as_a_hex_color():
    assert bc.colors_in("Fix brand-check false positive from PR #529 comment reference.") == set()


def test_issue_number_reference_does_not_flag_as_a_hex_color():
    assert bc.colors_in("see issue #318 for the full history") == set()


def test_a_genuine_bare_three_digit_hex_still_flags():
    """The fix is scoped to an issue-reference CONTEXT, not every 3-digit hex
    with no preceding '&' — a real off-palette color like `#529` used as an
    actual CSS value (not preceded by PR/issue/#) still has to be caught."""
    assert bc.colors_in('style="color:#529;"') == {"#552299"}


# --- PR 18: icon fill contract — a hardcoded `fill="#..."` icon opts out of
# the position-based badge color cycle and is only safe when its badge index
# is pinned to match. This is prevention, not a fix for a live bug (the one
# known instance, _ICON_HALF_CIRCLE, is already correctly registered and
# pinned — see the clean-source assertion below). --------------------------

def test_the_real_icon_registry_is_clean():
    """The live registry (_ICON_HALF_CIRCLE -> /tools/fpa-buddy @ index 0) is
    correctly pinned in the actual site source today."""
    assert bc.icon_fill_contract_problems(APP_SRC) == []


def test_unregistered_fixed_fill_icon_is_flagged():
    """A decoy `_ICON_*` constant with a hardcoded fill and no registry entry
    must be caught — this is the exact shape PR #533 found latent in
    _ICON_HALF_CIRCLE before it was pinned."""
    decoy_src = (
        '_ICON_DECOY = \'<circle cx="12" cy="12" r="9"/>'
        '<path fill="#1F7A66" stroke="none"/>\'\n'
    )
    problems = bc.icon_fill_contract_problems(decoy_src)
    assert any("_ICON_DECOY" in p and "Unregistered" in p for p in problems)
    # Torn down: the decoy source is local to this test and never touches
    # the real registry or webapp/app.py.
    assert bc.icon_fill_contract_problems(APP_SRC) == []


def test_registered_icon_with_mistuned_badge_index_is_flagged():
    """A registered icon whose caller resolves to the wrong badge index (a
    color-cycle change silently repointing it, PR #533's actual near-miss)
    must be caught even though the icon itself is registered."""
    mistuned_src = (
        '_ICON_HALF_CIRCLE = \'<circle cx="12" cy="12" r="9"/>'
        '<path fill="#1F7A66" stroke="none"/>\'\n'
        '_TOOLBOX_BADGE_INDEX = {"/tools/fpa-buddy": 1}\n'
    )
    problems = bc.icon_fill_contract_problems(mistuned_src)
    assert any("_ICON_HALF_CIRCLE" in p and "requires badge index 0" in p for p in problems)
