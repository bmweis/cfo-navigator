"""Automated brand-standards check (see BRAND.md §8).

The whole site is rendered from inline strings in ``webapp/app.py`` — global CSS,
per-page styles, inline SVG charts, and JS that builds markup. This test scans that
source and fails when new content drifts off-brand:

* fonts   — only Outfit / DM Sans / Source Serif 4 (+ system/generic fallbacks)
* colors  — every hex must be a brand token (parsed from the :root blocks, so the
            palette is its single source of truth) or a documented auxiliary color
* legacy  — colors purged in the refresh can never reappear
* tokens  — the full token set must stay present

When you intentionally add a color (a new chart series, a new status state), add it to
the matching group in ``AUX_COLORS`` with a comment — that records the decision, which
is the point. New, undocumented colors fail the build.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "webapp" / "app.py").read_text(encoding="utf-8")

# A hex color, excluding HTML numeric entities like &#127942; (preceded by '&').
_HEX_RE = re.compile(r"(?<!&)#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
# A font-family / font-shorthand declaration value (CSS or SVG attribute), up to a
# terminator. Quoted family names are pulled out of the captured value.
_FONT_RE = re.compile(r"font(?:-family)?\s*[:=]\s*([^;\"}<]+)", re.IGNORECASE)
_QUOTED_RE = re.compile(r"['\"]([A-Za-z0-9 ]+)['\"]")


def _norm(hex_str: str) -> str:
    h = hex_str.lower().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def _colors_in(text: str) -> set[str]:
    return {_norm(m.group(0)) for m in _HEX_RE.finditer(text)}


def _brand_token_colors() -> set[str]:
    """Every hex defined inside a :root{...} block — the palette source of truth."""
    colors: set[str] = set()
    for block in re.findall(r":root\{(.*?)\}", APP_SRC, re.S):
        colors |= _colors_in(block)
    return colors


# --- Documented non-token colors that are allowed to appear -------------------
# Each must be sanctioned and commented. Adding one here is a deliberate act.
AUX_COLORS = {
    # Neutrals / surfaces used inline (close kin of the neutral tokens)
    "#3a352e",  # legacy body-copy ink (≈ --ink-soft) in card/summary text
    "#5a5248",  # feed-card summary text
    "#fdfcfa",  # GER benchmark table alt-row stripe
    "#fbfaf6",  # GER projected-quarter input background
    "#d0cac0",  # reader empty-state input border (warm gray)
    "#c9eadf",  # seafoam-family border on the GER seafoam-wash readout
    "#f3d3c6",  # coral-family border on the brand-guide coral-wash callout
    "#f0ece4",  # reader inline-code background
    "#f4f0e8",  # reader table-header background
    "#b8b1a4",  # GER line-chart break-even reference line
    # Data-viz tints — seafoam / amber / coral families (chart bands & fills)
    "#d6efe8",  # contribution diagram: prior-revenue bar fill
    "#e7f5f0",  # contribution diagram: annualized-growth callout fill
    "#d8d3c8",  # line chart: projection uncertainty band
    "#e3f2ec",  # line chart: Elite tier band
    "#edf5f1",  # line chart: Strong tier band
    "#faf1e1",  # line chart: Typical tier band
    "#f9e8e3",  # line chart: Below-target tier band
    # Status / feedback — semantic UI, not brand
    "#d1fae5", "#065f46",            # success toast (bg / text)
    "#b91c1c", "#fee2e2", "#fca5a5",  # danger: delete/error text, hover bg, reject border
    "#fef3c7", "#92400e",            # feed paywall badge (amber bg / text)
    # Benchmark coverage badges (CFO Toolbox)
    "#dbeafe", "#1d4ed8",            # Private
    "#dcfce7", "#16a34a",            # Public
    "#ede9fe", "#7c3aed",            # Both
    # In-progress / info state (admin job progress bars and banners)
    "#eff6ff", "#bfdbfe", "#2563eb",  # blue info bg / border / fill for running jobs
    # Success state border (admin completion banners alongside existing #d1fae5 / #065f46)
    "#6ee7b7",  # success border green (emerald-300, complements #d1fae5 success bg)
    # Amber / warning tones (admin one-time-operation advisory and needs-enrichment count)
    "#d97706",  # amber text for "needs enrichment" stat (amber-600)
    "#fefce8", "#fde68a",  # amber warning banner bg / border (yellow-50 / yellow-200)
    # Misc
    "#b8860b",  # advisor gold-star marker
}

# Colors purged in the visual refresh — must never reappear.
BANNED_COLORS = {
    "#3b82f6", "#10b981", "#f4683b",  # old generic data-viz palette
    "#1a4d3c", "#2d6a4f", "#b45309",  # old brand greens / amber
    "#16130f", "#faf7f2", "#e6e0d6", "#eef3f0",  # old ink / bg / line / green wash
}

# Fonts allowed to be named in quotes within a font declaration.
ALLOWED_FONTS = {"Outfit", "DM Sans", "Source Serif 4", "Segoe UI"}
# Off-brand fonts that must never be referenced.
BANNED_FONTS = {
    "Inter", "Lora", "Arial", "Helvetica", "Times New Roman",
    "Times", "Roboto", "Verdana", "Tahoma", "Courier", "Georgia Pro",
}

# Tokens that define the system — all three ramps + neutrals + semantic + type.
EXPECTED_TOKENS = [
    "--navy-deep", "--navy", "--navy-light", "--navy-wash",
    "--seafoam-deep", "--seafoam-mid", "--seafoam", "--seafoam-wash",
    "--coral-deep", "--coral", "--coral-light", "--coral-wash",
    "--ink", "--ink-soft", "--muted", "--line", "--line-strong",
    "--good", "--caution", "--alert", "--font-head", "--font-body",
]


def _quoted_fonts_used() -> set[str]:
    names: set[str] = set()
    for m in _FONT_RE.finditer(APP_SRC):
        for q in _QUOTED_RE.findall(m.group(1)):
            names.add(q.strip())
    return names


# --- Tests -------------------------------------------------------------------

def test_all_colors_are_on_palette():
    """Every hex in the site is a brand token or a documented auxiliary color."""
    allowed = _brand_token_colors() | AUX_COLORS
    used = _colors_in(APP_SRC)
    offenders = sorted(used - allowed)
    assert not offenders, (
        "Off-palette color(s) found in webapp/app.py: "
        + ", ".join(offenders)
        + ".\nFix to a brand token, or — if intentional — add it to a token in _CSS "
        "or to AUX_COLORS (with a comment) in tests/test_brand_standards.py."
    )


def test_no_banned_legacy_colors():
    used = _colors_in(APP_SRC)
    found = sorted(used & BANNED_COLORS)
    assert not found, f"Legacy/off-brand color(s) reintroduced: {', '.join(found)}"


def test_no_banned_fonts():
    found = sorted(
        f for f in BANNED_FONTS
        if f"'{f}'".lower() in APP_SRC.lower() or f'"{f}"'.lower() in APP_SRC.lower()
    )
    assert not found, f"Off-brand font(s) referenced: {', '.join(found)}"


def test_only_brand_fonts_named():
    used = _quoted_fonts_used()
    offenders = sorted(used - ALLOWED_FONTS)
    assert not offenders, (
        "Non-brand font name(s) in a font declaration: "
        + ", ".join(offenders)
        + ". Use Outfit (headings), DM Sans (body/UI), or Source Serif 4 (reader)."
    )


@pytest.mark.parametrize("token", EXPECTED_TOKENS)
def test_brand_token_present(token):
    assert f"{token}:" in APP_SRC, f"Brand token {token} missing from _CSS :root"


def test_palette_has_three_full_ramps():
    """Each brand family exposes a working ramp (not just a single value)."""
    tokens = _brand_token_colors()
    # navy ramp, seafoam/green ramp, coral ramp — each needs several distinct shades
    for family, members in {
        "navy": ["#001b4f", "#002975", "#3f5c9a"],
        "seafoam": ["#1f7a66", "#2e9c86", "#a3e5d4", "#eaf7f2"],
        "coral": ["#b14a30", "#e8704f", "#f4a98f", "#fbeae3"],
    }.items():
        missing = [c for c in members if c not in tokens]
        assert not missing, f"{family} ramp missing shades: {missing}"
