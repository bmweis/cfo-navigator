"""Deterministic brand-standards rules + scanner (BRAND.md §8).

Single source of truth for the *visual* rules — palette, fonts, and one placement
rule (coral text under 18px, BRAND.md §2.3) — used by both the QA test
(``tests/test_brand_standards.py``) and the live checks dashboard
(``/admin/checks``). Pure-Python (only ``re``), so it runs anywhere.

The site is rendered from inline strings in ``webapp/app.py`` (CSS, per-page
styles, inline SVG, JS that builds markup), so the check scans that source and
flags anything off-brand.

Most of BRAND.md §2.3 ("where coral goes") is a judgment call — "unavoidable",
"one coral element per viewport" — and doesn't reduce to regex without false
positives, so it isn't automated here. The one sub-rule that *is* mechanical and
unambiguous — coral/coral-deep as a `color:` value inside a `style="..."`
attribute that also sets `font-size` under 18px — is checked, since it's a plain
text-color-vs-size fact, not a design judgment.
"""
from __future__ import annotations

import re

# A hex color, excluding HTML numeric entities like &#127942; (preceded by '&').
_HEX_RE = re.compile(r"(?<!&)#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
# A font-family / font-shorthand value (CSS or SVG attribute), up to a terminator.
_FONT_RE = re.compile(r"font(?:-family)?\s*[:=]\s*([^;\"}<]+)", re.IGNORECASE)
_QUOTED_RE = re.compile(r"['\"]([A-Za-z0-9 ]+)['\"]")
# style="..." attributes, to scope the coral-text-size check to one element's own
# declarations (not just proximity in the source).
_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')
# The `color:` property specifically — not `border-color:`/`background-color:`,
# which also contain the substring "color:".
_TEXT_COLOR_CORAL_RE = re.compile(r"(?:^|;)\s*color\s*:\s*var\(--coral(-deep)?\)")
_FONT_SIZE_PX_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")
_CORAL_TEXT_MIN_PX = 18

# --- Documented non-token colors that are allowed to appear -------------------
# Each must be sanctioned and commented. Adding one here is a deliberate act.
#
# Unlike §7's core tokens (checked value-by-value against BRAND.md, see
# scripts/generate_brand_docs.py), these are NOT individually CI-checked against
# BRAND.md — BRAND.md §8 documents the categories, not each hex. So when you add
# one: don't invent a new hue from scratch. Find the existing color already used
# for a similar purpose below and match its tone/weight (a status red should look
# like the other status reds, not a fresh shade). The categories, each grounded in
# a real, current usage:
#
#   - Neutrals / surfaces — off-token grays/creams close to --ink-soft/--muted,
#     used inline instead of the token. E.g. #5a5248, the feed-card summary text
#     (webapp/app.py, CFO Feed reader).
#   - Data-viz tints — light fills/bands for chart series and tier bands, kin of
#     the seafoam/coral tokens but softened for use as an area fill rather than a
#     line/text color. E.g. #D6EFE8, the GER contribution diagram's prior-revenue
#     bar (webapp/app.py:1777); #E3F2EC/#EDF5F1/#FAF1E1/#F9E8E3, the GER line
#     chart's Elite/Strong/Typical/Below-target tier bands (webapp/app.py:1964).
#   - Status / feedback — semantic UI state, not brand: success green, error red,
#     advisory amber. E.g. #d1fae5/#065f46, the "Saved"/"Done" success banner
#     (webapp/app.py:5809); #b91c1c/#fca5a5, the "Delete"/"Reject" button and its
#     hover border (webapp/app.py:5664); #fef3c7/#92400e, the feed paywall badge,
#     "Needs verification" badges, and verify banners — a deliberately distinct
#     advisory tone from --caution (which BRAND.md scopes to the GER calculator
#     readout only), not a near-miss to reconcile.
#   - Benchmark coverage badges (CFO Toolbox) — one fixed color pair per
#     Private/Public/Both tag. E.g. #dbeafe/#1d4ed8 for "Private"
#     (webapp/app.py:5020).
#   - In-progress / info state — admin job progress bars and banners (a distinct
#     blue from the benchmark badges above, reserved for "a background job is
#     running"). E.g. #eff6ff/#bfdbfe/#2563eb, the re-enrichment progress bar
#     (webapp/app.py:11715-11719).
#   - Amber / warning — advisory banners and counts that aren't errors but need
#     attention. E.g. #d97706, the "needs enrichment" stat (webapp/app.py:11762);
#     #fefce8/#fde68a, the queue-sweep advisory banner (webapp/app.py:11992).
#   - Misc — one-off UI accents that don't fit the above. E.g. #b8860b, the
#     Toolbox advisor gold-star marker (webapp/app.py:4639).
#   - "Sail, Don't Row" (/play) — a deliberately realistic sky/water/skyline/boat
#     palette, not brand tokens (see the design/mockups/ spec); the game reads as
#     an actual landscape. E.g. #C9A24B, the State House gold dome
#     (webapp/app.py:2770); #B5553A, the mainsail's coral shading
#     (webapp/app.py:2922).
AUX_COLORS = {
    # Neutrals / surfaces used inline (close kin of the neutral tokens)
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
    # Glanceable health indicators (sanctioned exception, BRAND.md §2/§6) —
    # today that is only the cookie-status panel. The semantic tokens were
    # tried first: --good is navy, the site's dominant colour, so a healthy
    # cookie read as ordinary text rather than a signal. Red deliberately
    # reuses the destructive #b91c1c above rather than adding a second red.
    "#15803d",                        # indicator: healthy / working
    "#ca8a04",                        # indicator: inconclusive
    "#fef3c7", "#92400e",            # advisory amber (bg / text) — feed paywall badge,
                                      # "Needs verification" badges, and verify banners
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
    # "Sail, Don't Row" (/play) — deliberately realistic sky/water/skyline/boat/
    # obstacle palette, not brand tokens. Per the design spec (design/mockups/),
    # the game reads as an actual landscape, muted except for the gold State
    # House dome and the coral sail — same rationale as the GER chart tints above.
    "#000000",  # CSS mask-image gradient stop (alpha mask, never a visible color)
    "#eaf0f5", "#dceeea", "#bdebdd",           # sky-to-water gradient
    "#0e5a7a", "#4fa8a0",                       # water mid/deep teal bands
    "#7c93b8", "#3e5fa8",                       # atmospheric skyline / building fill
    "#c9a24b",                                  # State House gold dome (the one color pop)
    "#2a4a82", "#0a2a6b",                       # Hancock Tower / bridge line navy-blue
    "#274e96", "#061a45", "#16418f", "#123a86", "#5fb89e",  # boat hull/waterline/cabin
    "#e0917a", "#b5553a", "#f5e4da", "#8b3f28",             # mainsail coral + fold shading
    "#d8987c", "#96432c",                                    # jib gradient
    "#a8a69c", "#5c5a52", "#7a7869",           # rock (grey stone)
    "#d6d4c8", "#3a3830",                       # rock lit facet / cast-shadow facet
    "#9c7a54", "#5a4128", "#3a2c18",           # buoy (weathered brown wood)
    "#f1eee4", "#d9d4c6",                       # sail gradient: mid stop / leech shadow stop
    "#ffe9a8",                                  # active rank-pill sleeve-stripe highlight
    # Water color progression (river to open ocean) — one tint per checkpoint
    # leg, crossfaded the same way the skyline backdrop is. Kin of the
    # #0e5a7a/#4fa8a0 water bands above, not brand tokens.
    "#4f9e7a",   # Charles River — brackish green
    "#3e7fa0",   # Boston Harbor — open, grayer blue
    "#2fa7b5",   # Cape Cod Bay — clearer turquoise
    "#1d6fa5",   # Martha's Vineyard Sound — deep ocean blue
    "#123e6e",   # Nantucket — deepest indigo ocean
    # Phase 3 checkpoint backdrops (Boston Harbor / Cape Cod / Martha's
    # Vineyard / Nantucket) — dunes, lighthouses, cottages, bluffs.
    "#d9cba3", "#c9b896",                       # dune / bluff sand tones
    "#ede8dd",                                  # lighthouse tower white
    "#8a9b6e",                                  # beach grass
    "#a3b8d8", "#7fa3c9",                       # cottage wall / roof pale blues
    # Phase 7 difficulty pills (gradient-fill, design/mockups/
    # sail-dont-row-v10-boat-and-pills.html) — Choppy/Rough/Storm tiers.
    # Fair Winds reuses existing seafoam-family tokens/aux colors already
    # listed above (#a3e5d4 is a token; #c9a24b already listed).
    "#e4f8f0",                                  # Fair Winds pill light stop
    "#e9d19e",                                  # Choppy Waters pill light stop
    "#e0a57d", "#c97a4a",                       # Rough Seas pill gradient
    "#5a6b7e", "#2c3a48",                       # Storm Warning pill gradient
    # Shark pursuit hazard (Mate+, Martha's Vineyard leg onward) — dark
    # navy-grey body, restrained/realistic like the other wildlife (whale).
    "#3d4650", "#1c2126",                       # shark body gradient
    "#0a0e14",                                   # shark eye
}

# Colors purged in the visual refresh — must never reappear.
BANNED_COLORS = {
    "#3b82f6", "#10b981", "#f4683b",  # old generic data-viz palette
    "#1a4d3c", "#2d6a4f", "#b45309",  # old brand greens / amber
    "#16130f", "#faf7f2", "#e6e0d6", "#eef3f0",  # old ink / bg / line / green wash
}

# Fonts allowed to be named in quotes within a font declaration.
# "Caveat" is an approved dependency exception for the graffiti/street-art
# refresh (BRAND.md §4) — weight 700 only, sticker badges only, never headings
# or body copy. "Permanent Marker" is a second approved exception — weight 400
# only, the sitewide "CFO Navigator"/logo wordmark only, never headings or body
# copy. This check can't enforce those placement rules mechanically; it only
# confirms neither font itself is an off-brand-font regression.
ALLOWED_FONTS = {"Outfit", "DM Sans", "Source Serif 4", "Segoe UI", "Caveat", "Permanent Marker"}
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


# The `--font-head` declaration is unique to the main design-system :root block
# in `_CSS` (as opposed to `/read`'s smaller, scoped :root), so it disambiguates
# which block is "the" palette when a source file defines more than one.
_MAIN_ROOT_MARKER = "--font-head"


def brand_root_block(src: str) -> str:
    """Raw text of the main design-system `:root{...}` block, `:root{` through
    the matching `}` inclusive — used to regenerate BRAND.md §7 verbatim from
    the live CSS (see `scripts/generate_brand_docs.py`)."""
    for m in re.finditer(r":root\{(.*?)\}", src, re.S):
        if _MAIN_ROOT_MARKER in m.group(1):
            return m.group(0)
    raise ValueError("main :root block (containing --font-head) not found")


_TOKEN_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def brand_tokens(src: str) -> dict[str, str]:
    """Ordered name -> value mapping (hex colors and font stacks alike) from the
    main design-system :root block."""
    return {m.group(1): m.group(2).strip() for m in _TOKEN_RE.finditer(brand_root_block(src))}


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


def small_coral_text_spans(src: str) -> list[str]:
    """`style="..."` attributes that set coral/coral-deep text color alongside a
    font-size under 18px — BRAND.md §2.3's "never coral" case for small text.
    Returns a short excerpt of each offending style attribute."""
    hits: list[str] = []
    for m in _STYLE_ATTR_RE.finditer(src):
        style = m.group(1)
        if not _TEXT_COLOR_CORAL_RE.search(style):
            continue
        size_m = _FONT_SIZE_PX_RE.search(style)
        if size_m and float(size_m.group(1)) < _CORAL_TEXT_MIN_PX:
            hits.append(style[:80])
    return hits


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
    small_coral = small_coral_text_spans(src)
    if small_coral:
        problems.append(
            "Coral text under 18px (BRAND.md §2.3 — use --navy or --alert instead): "
            + "; ".join(small_coral)
        )
    missing_tokens = [t for t in EXPECTED_TOKENS if f"{t}:" not in src]
    if missing_tokens:
        problems.append("Missing brand token(s): " + ", ".join(missing_tokens))
    for family, members in RAMPS.items():
        miss = [c for c in members if c not in tokens]
        if miss:
            problems.append(f"{family} ramp missing shades: " + ", ".join(miss))
    return problems
