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

import ast
import re

# A hex color, excluding HTML numeric entities like &#127942; (preceded by '&')
# and an issue/PR reference like "PR #529" or "see issue #318" — a 3-digit
# GitHub reference number (e.g. #529 -> #552299 once padded) reads as a
# perfectly valid, off-palette hex color to this regex, and has tripped this
# exact false positive three times (#465, #318, #529 — see CLAUDE.md/the
# comments this fix let get reworded back to plain prose). Fixed at the
# checker rather than by rewording every future comment around it.
_HEX_RE = re.compile(
    r"(?<!&)(?<!(?i:PR ))(?<!(?i:issue ))(?<!#)#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"
)
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
#     chart's Elite/Strong/Average/Below-target tier bands (webapp/app.py:1964).
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
    "#faf1e1",  # line chart: Average tier band
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
# "Source Serif 4" was retired sitewide (2026-09, standing rule from Brian:
# only Outfit or DM Sans for content/reading typography, ever) — removed
# from this allowlist deliberately, not left in for a font nothing renders
# in any more, so a future reintroduction gets caught as a regression.
ALLOWED_FONTS = {"Outfit", "DM Sans", "Segoe UI", "Caveat", "Permanent Marker"}
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


# Icons with a hardcoded `fill="#...` opt out of the position-based
# seafoam/navy badge-color cycle (_CARD_ICON_STYLES) every plain stroke icon
# gets for free — safe only when that icon's badge index is pinned to
# whichever cycle position actually produces the matching color (see
# _ICON_HALF_CIRCLE's own comment in webapp/app.py, and PR #533's near-miss:
# dropping coral from a 3-color cycle to a 2-color one silently moved this
# icon's badge from seafoam to navy, clashing with its hardcoded seafoam
# fill). This registry is the one place that pin is declared —
# {icon constant name: (href it renders at, required _TOOLBOX_BADGE_INDEX value)}.
ICON_FILL_CONTRACTS = {
    "_ICON_HALF_CIRCLE": ("/tools/fpa-buddy", 0),
}

_ICON_DEF_RE = re.compile(r"^(_ICON_[A-Z_]+)\s*=", re.MULTILINE)
_BADGE_INDEX_ENTRY_RE = re.compile(r'"([^"]+)"\s*:\s*(\d+)')


def icon_fill_contract_problems(src: str) -> list[str]:
    """Flags (a) an `_ICON_*` constant with a hardcoded `fill="#...` that
    has no entry in ICON_FILL_CONTRACTS, and (b) a registered icon whose
    href doesn't resolve to its required index in `_TOOLBOX_BADGE_INDEX` —
    a stale/mistuned pin, not just a missing one."""
    problems: list[str] = []
    starts = list(_ICON_DEF_RE.finditer(src))
    for i, m in enumerate(starts):
        name = m.group(1)
        end = starts[i + 1].start() if i + 1 < len(starts) else min(len(src), m.end() + 2000)
        block = src[m.end():end]
        if 'fill="#' not in block:
            continue
        if name not in ICON_FILL_CONTRACTS:
            problems.append(f"Unregistered fixed-fill icon {name} — add it to ICON_FILL_CONTRACTS")
            continue
        href, required_index = ICON_FILL_CONTRACTS[name]
        badge_map_m = re.search(r"_TOOLBOX_BADGE_INDEX\s*=\s*\{([^}]*)\}", src)
        actual = dict((k, int(v)) for k, v in _BADGE_INDEX_ENTRY_RE.findall(badge_map_m.group(1))) if badge_map_m else {}
        if actual.get(href) != required_index:
            problems.append(
                f"{name} requires badge index {required_index} at {href!r}, "
                f"but _TOOLBOX_BADGE_INDEX has {actual.get(href)!r}"
            )
    return problems


# Every link that leaves bmweis.com opens in a new tab (BRAND.md §3.3). These
# are the hosts that count as "still on the site" — everything else with an
# http(s) href is outbound and needs target="_blank" rel="noopener". A
# relative href (/tools, #anchor) never matches the http(s) test at all, so
# internal links are same-tab for free.
INTERNAL_LINK_HOSTS = {"bmweis.com", "www.bmweis.com", "mcp.bmweis.com"}

# Matches an <a> tag from `<a` to its closing `>`. Deliberately run over raw
# source rather than evaluated string values: an anchor is routinely split
# across adjacent Python string literals (`'<a href="…"' ' target="_blank">'`),
# and since the Python syntax between the fragments contains no `>`, the raw
# scan still sees one whole tag. An AST/value-based scan would see two
# fragments and report a false positive on the half without the attribute.
_ANCHOR_TAG_RE = re.compile(r"<a\s[^>]*>", re.S)
_ANCHOR_HREF_RE = re.compile(r'href="(https?://[^"]*)"')


def _outbound_host(href: str) -> str:
    """Host of an absolute href, lowercased, port stripped. '' if not http(s)."""
    m = re.match(r"https?://([^/?#]+)", href)
    if not m:
        return ""
    return m.group(1).split("@")[-1].split(":")[0].lower()


def outbound_link_problems(src: str) -> list[str]:
    """Anchors in `src` that leave bmweis.com without `target="_blank"`.

    The mechanical half of BRAND.md §3.3's "outbound links open in a new
    tab" rule, in the same spirit as voice_review.typography_findings():
    a deterministic scan over hand-written source, not a judgment call.

    **Scanned:** `<a>` tags written literally into Python source (which is
    where every hand-authored anchor on this site lives, since all HTML is
    inline in webapp/app.py).

    **Deliberately NOT scanned, and each for a real reason — a clean result
    here is not a claim that every rendered anchor site-wide complies:**

    * **Links built dynamically in JavaScript** (`'<a href="' + url + '">'`).
      The href isn't a literal in the source at all, so there's nothing for
      a static scan to classify as outbound; a rendered-DOM scan couldn't
      see them either unless the JS had actually run.
    * **Links inside stored database content** — `original_content.body_md`,
      AI-drafted tool/community fields, user-submitted text. These aren't
      source, they're data, and they're edited through the admin UI rather
      than in a PR, so a source lint can't reach them and shouldn't try to
      rewrite them. (The /how-this-is-built copy is the one body of prose
      that is BOTH source-resident and admin-editable: the defaults here are
      scanned, but an admin's saved override is not.)
    * **Markdown links in that same prose** (`[text](https://…)`). Markdown
      has no syntax for `target`, so a markdown link is a latent violation
      by construction — which is exactly why the /how-this-is-built copy
      writes its outbound links as raw `<a>` tags instead.
    """
    problems: list[str] = []
    for m in _ANCHOR_TAG_RE.finditer(src):
        tag = m.group(0)
        href_m = _ANCHOR_HREF_RE.search(tag)
        if not href_m:
            continue
        href = href_m.group(1)
        host = _outbound_host(href)
        if not host or host in INTERNAL_LINK_HOSTS:
            continue
        if 'target="_blank"' in tag:
            continue
        line = src.count("\n", 0, m.start()) + 1
        problems.append(f"line {line}: {href} (missing target=\"_blank\")")
    return problems


# --- Reading-column rule (BRAND.md section 5) -------------------------------
_COLUMN_TAG_RE = re.compile(r"<(/?)(div|table|ul|section)\b([^<>]{0,1500})>", re.I)
_PROSE_CLASS_RE = re.compile(r"\btool-prose\b")


def _literal_segments(src: str):
    """(first line number, text) of every top-level string literal in `src`
    (an f-string counts once, not once per fragment). Segments are sliced
    from the source text by position: ast.get_source_segment re-splits the
    whole file on every call, which is minutes on a 2 MB module."""
    tree = ast.parse(src)
    data = src.encode()
    offs = [0]
    for line in data.split(b"\n"):
        offs.append(offs[-1] + len(line) + 1)
    nested: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for child in ast.walk(n):
                if child is not n:
                    nested.add(id(child))
    for n in ast.walk(tree):
        if id(n) in nested:
            continue
        if isinstance(n, ast.Constant) and not isinstance(n.value, str):
            continue
        if not isinstance(n, (ast.Constant, ast.JoinedStr)):
            continue
        text = data[offs[n.lineno - 1] + n.col_offset: offs[n.end_lineno - 1] + n.end_col_offset].decode()
        yield n.lineno, text


def reading_column_problems(src: str) -> list[str]:
    """A table or grid that sits NEXT TO a `.tool-prose` reading column
    instead of inside it (BRAND.md section 5, "A table or grid on a content
    page belongs inside the reading column"). A sibling renders at the full
    page-tier width and breaks the column; this broke /how-this-is-built and
    /tools/fpa-buddy/how-it-works.

    A source scan, not a rendered one: rendering every page from inside
    run_all() re-enters run_all() across threadpool threads.

    **Flagged:** a `<table>`, or an element with `display:grid` /
    `grid-template-columns`, written in the same string literal as a
    `.tool-prose` div and (a) a direct child of a container that also
    directly holds a `.tool-prose`, or (b) inside an `overflow-x` wrapper
    div (the site's table-wrapper idiom) that is such a direct child.

    **Not caught, so a clean result is not a claim about every page:**
    * blocks interpolated into the page (`{cards}`, a helper that returns a
      table) or assembled across separate string literals, and a
      `.tool-prose` opened in one literal and closed in another;
    * blocks built in JavaScript or stored in the database;
    * a table or grid reached through any other wrapper (a styled card, a
      plain `<div>`): a deliberate full-width card next to prose is allowed
      (the Growth Engine Ratio calculator), and telling it apart from an
      accidental one needs a rendered measurement, not a scan.
    """
    problems: list[str] = []
    for first_line, text in _literal_segments(src):
        if "tool-prose" not in text:
            continue
        root = {"kind": "root", "children": []}
        stack = [root]
        for m in _COLUMN_TAG_RE.finditer(text):
            closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
            if closing:
                if name == "div" and len(stack) > 1:
                    stack.pop()
                continue
            grid = "display:grid" in attrs.replace(" ", "") or "grid-template-columns" in attrs
            if name == "div":
                if _PROSE_CLASS_RE.search(attrs):
                    kind = "prose"
                elif "overflow-x" in attrs.replace(" ", ""):
                    kind = "wrapper"
                else:
                    kind = "grid" if grid else "box"
                node = {"kind": kind, "children": [], "pos": m.start()}
                stack[-1]["children"].append(node)
                stack.append(node)
            elif name == "table" or grid:
                stack[-1]["children"].append(
                    {"kind": "table" if name == "table" else "grid", "children": [], "pos": m.start()})

        def visit(container: dict) -> None:
            has_prose = any(c["kind"] == "prose" for c in container["children"])
            for c in container["children"]:
                if has_prose and c["kind"] in ("table", "grid"):
                    _flag(c)
                if has_prose and c["kind"] == "wrapper":
                    for g in c["children"]:
                        if g["kind"] in ("table", "grid"):
                            _flag(g)
                if c["kind"] != "prose":
                    visit(c)

        def _flag(block: dict) -> None:
            line = first_line + text.count("\n", 0, block["pos"])
            problems.append(f"line {line}: {block['kind']} sits beside a .tool-prose reading column, not inside it")

        visit(root)
    return sorted(set(problems))


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
    icon_fill = icon_fill_contract_problems(src)
    if icon_fill:
        problems.append("Icon fill contract violation(s): " + "; ".join(icon_fill))
    # outbound_link_problems() is deliberately NOT folded in here, unlike
    # icon_fill_contract_problems() above: findings() is the palette/typeface
    # check, and its /admin/checks row describes itself that way. The outbound
    # rule is its own standing rule with its own row (see webapp.checks.run_all).
    return problems


# ---------------------------------------------------------------------------
# One table format (2026-09)
# ---------------------------------------------------------------------------
# Every table on the site takes its look from ONE !important block in
# webapp/app.py's _CSS: light-blue header, white rows, a line between rows,
# a rounded --table-border (navy-light) frame, and the secondary format for
# subheading bands. Inline styles can't override !important, so the ways
# the standard can quietly break are: the block loses a rule, the scope
# grows a new exclusion, the border token changes, or some other CSS rule
# fights the block with its own !important. These two functions catch all
# four from source text; no rendering needed.

TABLE_SCOPE = ".site-main table:not(.tp-competitor-table):not(.rr-reader-body table)"

# The only tables left out of the standard, each for a reason Brian signed
# off on. Adding one here is a design decision, not a code fix.
TABLE_SCOPE_EXCLUSIONS = {
    ".tp-competitor-table": "the profile page's Competitors logo list inside a card; a frame would box in a box",
    ".rr-reader-body table": "other sites' article HTML in the Reader; newsletters use tables for layout",
}

# What the block must declare, in the words a failure message uses.
_TABLE_STANDARD_RULES = (
    ("rounded frame", f"{TABLE_SCOPE}{{border-collapse:separate!important;"),
    ("frame color", "border:1px solid var(--table-border)!important"),
    ("frame radius", "border-radius:12px!important"),
    ("light-blue header", "background:var(--accent-light)!important;color:var(--ink)!important"),
    ("white rows", f"{TABLE_SCOPE} tr{{background:var(--surface);}}"),
    ("line between rows", f"{TABLE_SCOPE} td{{border-top:1px solid var(--line)!important"),
    ("subheading band (secondary format)",
     f"{TABLE_SCOPE} td.cc-section{{background:var(--table-border)!important;color:#fff!important;}}"),
    ("white label column (secondary format)",
     f"{TABLE_SCOPE}>tbody td.cc-label{{background:var(--surface)!important;}}"),
    ("sticky-table wrapper frame", ".site-main .table-frame{"),
    ("navy-light border token", "--table-border:var(--navy-light);"),
)

# Other !important table rules that exist to SUPPORT the standard, not fight
# it. Keyed by selector, with the reason.
TABLE_OVERRIDE_ALLOWLIST = {
    ".oc-body tbody tr": "resets row stripes stored inline in article HTML to the standard's white rows",
    ".oc-body .ger-table-wrap,.oc-body .ns-table-wrap":
        "removes the frame stored on article table wrappers so the table's own frame is the only one",
    ".admin-table-responsive tr": "mobile card layout, where a table's rows become stacked cards",
    ".backup-log-table td": "mobile card layout for the backup history table",
    # Refs 655, B1. The generic td border rule is !important, so the rule that
    # drops cell borders in a stacked card has to be too; the earlier
    # non-lifted version never applied (see _CSS). Two entries, one per card
    # breakpoint.
    ".site-main.site-main table.admin-table-responsive.admin-table-responsive td, "
    ".site-main.site-main table.backup-log-table.backup-log-table td":
        "mobile card layout: cells carry no top border (replaces the specificity-losing rule below)",
    ".site-main.site-main table.ff-table.ff-table td, .site-main.site-main table.fs-table.fs-table td":
        "mobile card layout for the feeds tables: cells carry no top border",
}

_CSS_RULE_RE = re.compile(r"([^{}]{1,400})\{([^{}]{0,800})\}")
_TABLE_TOKEN_RE = re.compile(r"(?<![\w-])(?:table|thead|tbody|tr|th|td)(?![\w-])|\.cc-section|\.cc-label|\.table-frame")
_IMPORTANT_FRAME_RE = re.compile(r"(?:background|border)[\w-]*\s*:[^;]*!\s*important")


def _last_selector(raw: str) -> str:
    """The selector text a CSS rule starts with, stripped of whatever Python
    or CSS came before it in the source (a comment, a string quote)."""
    for sep in ("*/", "'", '"', "}"):
        if sep in raw:
            raw = raw.rsplit(sep, 1)[1]
    return " ".join(raw.split())


def table_standard_problems(css: str) -> list[str]:
    """The one-table-format block in the live _CSS is whole and its scope
    excludes only the approved tables."""
    problems = [f"table standard is missing its {label} rule"
                for label, needle in _TABLE_STANDARD_RULES if needle not in css]
    scopes = set(re.findall(r"\.site-main table((?::not\([^)]*\))+)", css))
    for scope in scopes:
        excluded = set(re.findall(r":not\(([^)]*)\)", scope))
        for extra in sorted(excluded - set(TABLE_SCOPE_EXCLUSIONS)):
            problems.append(f"table standard excludes {extra!r}, which isn't an approved exception "
                            "(TABLE_SCOPE_EXCLUSIONS in linklib/brand_check.py)")
    return problems


def table_override_problems(src: str) -> list[str]:
    """No CSS rule outside the standard block sets a table's background or
    border with !important. That's the one way a later rule could beat the
    standard, since the standard itself is !important."""
    norm = src.replace("{{", "{").replace("}}", "}")
    problems = []
    for m in _CSS_RULE_RE.finditer(norm):
        decl = m.group(2)
        if not _IMPORTANT_FRAME_RE.search(decl):
            continue
        selector = _last_selector(m.group(1))
        if not _TABLE_TOKEN_RE.search(selector):
            continue
        in_block = selector.startswith((TABLE_SCOPE, ".site-main .table-frame"))
        if in_block or selector in TABLE_OVERRIDE_ALLOWLIST:
            continue
        line = norm.count("\n", 0, m.start(2)) + 1
        problems.append(f"line {line}: {selector[:90]!r} overrides the table standard with !important. "
                        "Drop the !important, or add the selector to TABLE_OVERRIDE_ALLOWLIST with a reason")
    return problems
