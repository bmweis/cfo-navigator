"""Deterministic brand-standards rules + scanner (BRAND.md §8).

Single source of truth for the *visual* rules — palette and fonts — used by both
the QA test (``tests/test_brand_standards.py``) and the live checks dashboard
(``/admin/checks``). Pure-Python (only ``re``), so it runs anywhere.

The site is rendered from inline strings in ``webapp/app.py`` (CSS, per-page
styles, inline SVG, JS that builds markup), so the check scans that source and
flags anything off-brand.
"""
from __future__ import annotations

import re

# A hex color, excluding HTML numeric entities like &#127942; (preceded by '&').
_HEX_RE = re.compile(r"(?<!&)#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
# A font-family / font-shorthand value (CSS or SVG attribute), up to a terminator.
_FONT_RE = re.compile(r"font(?:-family)?\s*[:=]\s*([^;\"}<]+)", re.IGNORECASE)
_QUOTED_RE = re.compile(r"['\"]([A-Za-z0-9 ]+)['\"]")

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
    # Success state border (admin completion banners)
    "#6ee7b7",  # success border green (emerald-300, complements #d1fae5 success bg)
    # Amber / warning tones (admin advisory and needs-enrichment count)
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

# Each brand family must expose a working ramp (not just a single value).
RAMPS = {
    "navy": ["#001b4f", "#002975", "#3f5c9a"],
    "seafoam": ["#1f7a66", "#2e9c86", "#a3e5d4", "#eaf7f2"],
    "coral": ["#b14a30", "#e8704f", "#f4a98f", "#fbeae3"],
}


def _norm(hex_str: str) -> str:
    h = hex_str.lower().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def colors_in(text: str) -> set[str]:
    return {_norm(m.group(0)) for m in _HEX_RE.finditer(text)}


def brand_token_colors(src: str) -> set[str]:
    """Every hex defined inside a :root{...} block — the palette source of truth."""
    colors: set[str] = set()
    for block in re.findall(r":root\{(.*?)\}", src, re.S):
        colors |= colors_in(block)
    return colors


def quoted_fonts_used(src: str) -> set[str]:
    names: set[str] = set()
    for m in _FONT_RE.finditer(src):
        for q in _QUOTED_RE.findall(m.group(1)):
            names.add(q.strip())
    return names


def banned_fonts_used(src: str) -> list[str]:
    low = src.lower()
    return sorted(f for f in BANNED_FONTS
                  if f"'{f}'".lower() in low or f'"{f}"'.lower() in low)


def findings(src: str) -> list[str]:
    """All brand-standards violations in `src`, as human-readable strings. Empty = clean."""
    problems: list[str] = []
    tokens = brand_token_colors(src)
    used = colors_in(src)

    off = sorted(used - tokens - AUX_COLORS)
    if off:
        problems.append("Off-palette color(s): " + ", ".join(off))
    legacy = sorted(used & BANNED_COLORS)
    if legacy:
        problems.append("Legacy color(s) reintroduced: " + ", ".join(legacy))
    bad_fonts = banned_fonts_used(src)
    if bad_fonts:
        problems.append("Off-brand font(s) referenced: " + ", ".join(bad_fonts))
    off_fonts = sorted(quoted_fonts_used(src) - ALLOWED_FONTS)
    if off_fonts:
        problems.append("Non-brand font name(s): " + ", ".join(off_fonts))
    missing_tokens = [t for t in EXPECTED_TOKENS if f"{t}:" not in src]
    if missing_tokens:
        problems.append("Missing brand token(s): " + ", ".join(missing_tokens))
    for family, members in RAMPS.items():
        miss = [c for c in members if c not in tokens]
        if miss:
            problems.append(f"{family} ramp missing shades: " + ", ".join(miss))
    return problems
