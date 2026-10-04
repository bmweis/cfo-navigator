# Brand Standards — bmweis.com / CFO Navigator

*A graffiti/street-art personality layer over a restrained finance-tool base. An
editorial system for a senior-finance-executive audience: warm, structured, credible.
Cool navy and seafoam, a single warm coral pop, geometric sans typography — with a
hand-drawn marker-underline and small rotated sticker badges as the one decorative
accent layer, used sparingly and never on data-dense surfaces.*

---

## 1. Brand personality

| We are | We are not |
|---|---|
| Editorial, structured, considered | Flashy, trendy, startup-loud |
| Warm and human (off-white, not cold white) | Sterile / corporate-blue SaaS |
| A restrained graffiti accent layer (marker-underline, one or two stickers) | Illustration-heavy, an all-over graffiti aesthetic, clip-art |
| Confident in whitespace | Busy, gradient-heavy, decorative |

The whole system runs on **restraint**. Navy carries the structure, seafoam is a frequent-but-quiet
accent, and coral is the rare highlight you notice precisely *because* it's rare.

---

## 2. Color

### 2.1 The three brand colors — each with a working ramp

Three families: **Navy** (primary, cool), **Seafoam/Green** (cool accent), **Coral** (warm accent).
Each has a few shades so you're never stuck reaching outside the system. Contrast figures are
measured on the `#F5F4EF` canvas.

**Navy — primary (cool)**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--navy-deep` | `#001B4F` | 14:1 | Button hover, depth |
| `--navy` | `#002975` | 12:1 | Base — wordmark, links, buttons, headings accents |
| `--navy-light` | `#3F5C9A` | 5.9:1 | Lighter navy — secondary accents, borders (text-capable) |
| `--navy-wash` | `#EEF1F7` | fill | Soft navy fill — chip & ghost-button hovers |

**Seafoam / Green — cool accent**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--seafoam-deep` | `#1F7A66` | 4.7:1 | Deepest teal — text-capable on light (AA) |
| `--seafoam-mid` | `#2E9C86` | 3.1:1 | Mid teal — **data-viz** (legible as a fill/line); ≥18px text only |
| `--seafoam` | `#A3E5D4` | fill | Base accent (light mint) — tags, badges, active-nav underline |
| `--seafoam-wash` | `#EAF7F2` | fill | Soft fill — calculator readout, table accents |

**Seafoam has three uses, kept apart by form.**
1. **Category tag:** a non-interactive pill, seafoam fill, navy text, no border.
2. **Text highlight:** a seafoam background behind running text, such as the summary
   above a Compare table. No border.
3. **Admin-only surface:** a control or box visible only to admins on a public page.
   Seafoam fill (`--seafoam`, `_ADMIN_ONLY_BG`), navy text and a 1px border in
   `--seafoam-deep` (the token the Playbook tag uses; no new colour). The border is
   the admin-only signal: a seafoam element without it is never admin-only. The
   label and context also say it is admin-only, so colour is not the only cue.
   Controls inside `/admin/*` are all admin-only and do not take the border.
   **Admin-only status marker (text only):** a state an admin should see at a glance but
   that is not a control, such as "🚫 Hidden" on a past-question row. `--seafoam-deep`
   text (5.21:1 on white), weight 600, no fill and no border, so a status never looks
   like a button. A status shown to the asker as well (the grey "🔒 Private") stays muted.

First user of the button form: `.ask-ctl-admin` on FP&A Buddy's "Hide" and "Unhide"
(28px high, 44px touch target, width at least 128px). Contrast: the deep seafoam
border is 3.65:1 against the seafoam fill and 4.73:1 against the page background
(both over the 3:1 non-text minimum); navy text on the seafoam fill is 9.35:1. The Reader
box (`_reader_access_card_html`) already follows the surface form (1.5px border). Other
admin-only controls on public pages (Quick edit, Full edit and Delete on vendor
cards, the profile Edit button, Manage links) are still grey or navy; restyling them
is a separate decision. Seafoam uses that fit none of the three, noted so they are
not mistaken for admin-only: the "Reviewed" status pill, the selected state of
`.ask-tag` source chips, and the `.ask-src-list` link hover.

**Coral — warm accent (rare)**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--coral-deep` | `#B14A30` | 4.9:1 | Text-capable coral (AA) — *only* when coral must carry small text |
| `--coral` | `#E8704F` | 3.1:1 | Base — display pop, badges, data-viz; graphics & ≥24px only |
| `--coral-light` | `#F4A98F` | fill | Lighter coral — soft highlights, fills only (never text) |
| `--coral-wash` | `#FBEAE3` | fill | Soft fill — decorative highlight blocks (put **navy** text on it) |

> **Read the ramps the same way each time:** *deep* shades are dark enough for small text (AA);
> *base/mid* are for graphics, fills, and large display; *light/wash* are fills only. When in doubt,
> small text is navy, ink, or a `-deep`; never a `base`/`light`/`wash`.

### 2.2 Neutrals & semantic

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#F5F4EF` | Page canvas (warm off-white) |
| `--surface` | `#FFFFFF` | Cards, inputs |
| `--surface-2` | `#FAF9F4` | Alt panels, table stripes |
| `--ink` | `#1a1a1a` | Headings, primary text |
| `--ink-soft` | `#3a3833` | Body copy |
| `--muted` | `#6F6A60` | Meta, captions, kickers |
| `--line` | `#E4E0D6` | Warm hairline |
| `--line-strong` | `#D6D1C4` | Heavier divider — section rules, table borders |
| `--good` / `--caution` / `--alert` | `#002975` / `#9A6B12` / `#9E3B30` | **Status only** — GER tiers, form errors, Warnings callouts |
| `--alert-wash` | `#FBEEEC` | Soft alert fill — Warnings callout background only |

> **Semantic ≠ brand.** The alert red `#9E3B30` means *error/danger*. Coral is decorative and
> never signals status. They're 96 RGB-units apart so they don't read as the same color — keep it
> that way by never using coral for warnings or red for highlights.

### 2.3 Where coral goes (and where it doesn't)

Coral is the **warm counterweight** to a cool palette. Use it as a graphic/display accent, sparingly:

**✅ Sanctioned coral uses**
- "New" / "Just added" / "Featured" badges and eyebrow labels where you want energy
- A short coral underline or marker under a single hero word or section number
- Data-viz **third series** (e.g. the R&D lane in the GER contribution diagram)
- Large display numerals or stat call-outs (≥ 24px)

**🚫 Never coral**
- Body text or any text under ~18px (use `--coral-deep` only if unavoidable)
- Button fills (buttons are navy or ghost-navy — *no* color buttons, ever)
- Status/error states (that's `--alert`). This includes Warnings callouts (`.article-warn`;
  see "Callout taxonomy" below), which use `--alert`/`--alert-wash`, not coral.
- More than ~one coral element per viewport — if you see two, remove one. **Mechanically
  checked as of PR 16 (2026-09)**, on public pages — see §8's own bullet for what the check
  actually scans and its stated limits.

**Coral is not a color to spend by array position.** Before PR 16, `_CARD_ICON_STYLES`
cycled seafoam/navy/coral by loop index for every card-row/tile grid sitewide — so
whichever card happened to land in the third slot "spent" the one rare accent, not by
anyone's deliberate choice. Coral is dropped from that cycle entirely now (every such
grid cycles seafoam/navy only); the one deliberate coral use on `/tools` and the homepage
is the MCP capability callout (`_mcp_callout_html`), a non-clickable statement, never a
button.

**Original Content flagship-card eyebrow — semantic, not positional (2026-09).** A
separate, unrelated cycle (`_OC_TAG_COLORS` — never shared code with `_CARD_ICON_STYLES`
above) had the identical bug for the `/thought-leadership` flagship cards' small
uppercase tag eyebrow: coral/seafoam-deep/navy-light rotated by the card's position in
the row, so two cards sharing the same tag text could render in two different colors
depending only on which slot they landed in, and nothing tied the tag's meaning to its
color at all. Fixed by binding a fixed color to each of the three closed tag values
instead (`_OC_TAG_INFO` in `webapp/app.py`), with the choice itself semantic rather than
decorative:

| Tag | Meaning | Color |
|---|---|---|
| Guide | Instruction manuals, reference material | `--navy` — blue is something that stays |
| Playbook | Steps for how to do something, an action | `--seafoam-deep` — green is something you can run |
| Framework | A model or metric for thinking about something | `--coral-deep` — coral is meant to jump |

All three are the text-capable ramp shades (this is small uppercase text under 18px,
the same reason `--coral`/`--seafoam` themselves are never used for it — see the
"Never coral" list above) — though not the identical three the old positional cycle
used: that cycle's third shade was `--navy-light` (5.9:1), while Guide binds to the
darker `--navy` (12.1:1) instead. Both are existing tokens, so this is still no new
palette entry, but it's a substitution, not a carry-forward.

**Contrast headroom, worth guarding — do not lighten `--seafoam-deep` or `--coral-deep`.**
At 4.7:1 and 4.9:1 against `--bg` respectively, these are the two tightest ratios
anywhere in the palette — barely clearing the 4.5:1 AA floor for text — and they now
carry the Playbook and Framework eyebrows on top of their existing uses. A future
palette tweak that lightens either value for a different reason would silently drop
these two tag eyebrows below AA with no warning from `linklib/brand_check.py` (which
checks token *presence*, not contrast) — check both ratios again before touching
either hex value.

One practical consequence: coral frequency on this grid now
tracks how many published pieces are tagged Framework, not card position — with a
single Framework among the four pieces live at launch, that's a *better* fit with the
one-coral-per-viewport rule than the old positional cycle was (which put two coral
eyebrows on the live grid). `tag_label` is a closed three-value enum enforced at the
admin form (a `<select>`, no free text) specifically so this binding can't drift the
way the eyebrow *text* itself once did — see CLAUDE.md's "Original content — tag
taxonomy" entry for the full incident (a piece tagged "Setup Guide" whose link label and
teaser both described a Playbook) and the derived-`link_label` fix that closes it.

**Sanctioned exception: social share cards (2026-09).** The committed 1200×630 Open
Graph/Twitter Card PNGs (`webapp/static/og/`) carry **two** coral elements — a full-height
left edge bar and an uppercase eyebrow — deliberately, not an oversight. Reasoning:
1. The bar functions as **chrome** (a fixed frame element identifying "this is a CFO
   Navigator card," the same role a masthead color plays), not body content competing for
   the reader's attention the way a coral fill inside an article page would.
2. `coral_moment_problems()` (§8) renders HTML routes and counts coral **backgrounds**
   inside rendered `style="..."` attributes — it has no way to inspect a PNG's pixels at
   all, so this exception is structurally invisible to the mechanical check regardless of
   whether it's documented here. It's recorded anyway because the written standard should
   say what's true, not just what a scanner happens to catch.

This exception is scoped narrowly to the social-card template — it does not license a
second coral background anywhere else on a rendered page. See "Social share cards" in
§5's UI components for the full card spec (dimensions, element order, and the string
constraints that keep the hand-maintained template consistent).

### 2.4 Data-visualization palette

Charts use the three brand families as categorical colors — cool for structure and outcome,
warm to draw the eye. The GER calculator is the reference implementation:

| Series / role | Color | Token |
|---|---|---|
| GTM (investment) | Navy `#002975` | `--navy` |
| Revenue / growth (outcome) | Seafoam-mid teal `#2E9C86` | `--seafoam-mid` |
| R&D — the series to highlight ("the missing half") | Coral `#E8704F` | `--coral` |
| Tier / quality bands | seafoam · amber · coral **tints** | (light tints) |
| Axes, gridlines, reference lines | `#E4E0D6` · `#D6D1C4` · `#6F6A60` | `--line` / `--line-strong` / `--muted` |

Coral marks the one series you want noticed. Chart text is **DM Sans**; big readouts are **Outfit**.
Never reintroduce the old generic data palette (`#3b82f6` / `#10b981` / `#f4683b`).

### 2.5 Usage balance

Think **70 / 20 / 10**: ~70% navy + neutrals (structure and text), ~20% seafoam (tags, active
states, soft panels), ~10% — really less — coral (one highlight per screen). White space is a color too.

### 2.6 Pairings & accessibility (measured on the `#F5F4EF` canvas)

| Combination | Ratio | Verdict |
|---|---|---|
| Navy text on canvas / white | 12–14:1 | ✅ Anything |
| Navy text on seafoam-wash / coral-wash | 11:1 | ✅ Anything — preferred for tinted highlight blocks |
| Coral `#E8704F` on canvas | 2.8:1 | ⚠️ Graphics & ≥24px display only — **not text** |
| Coral-deep `#B14A30` on canvas | 4.9:1 | ✅ AA for normal text (use sparingly) |
| Seafoam `#A3E5D4` as a fill behind navy | — | ✅ Tag/badge background only |

---

## 3. Typography

Two content families, one combined Google Fonts import in `<head>`. **Outfit for
headings, DM Sans for body copy — no other font renders content or reading text
anywhere on the site, ever** (Brian's explicit standing rule, 2026-09). **Caveat**
and **Permanent Marker** are accent-only fonts — approved dependency exceptions for
the graffiti refresh — each restricted to one specific decorative use (sticker
badges; the sitewide wordmark logo) and never used for headings or body copy; they
are a separate, narrow exception to "two families," not a third content font.

**2026-09 history — no serif anywhere any more, settled after three passes.** The
merged Feed/Archive/Read Later reader at `/read` used to render both title and body
in Source Serif 4 — confirmed wrong on direct live-site review. A first pass moved
both to Outfit; checked against the site's actual published long-form content (an
Original Content article's `.oc-body p` — the same template the Growth Engine
Ratio/Sail Don't Row/NetSuite MCP pieces render through) and found body copy is DM
Sans there, Outfit reserved for the `<h1>` — so "Outfit for body" would have made
this reader the one place on the whole site with body copy in a headings font, not
a return to an existing pattern. Settled on ordinary sitewide typography instead:
Outfit heading, DM Sans body. That still left one holdout — the standalone
single-article view at `/read/{article_id}` (a separate, older template,
`webapp/app.py`'s `_READER_CSS`) — which Brian then confirmed should also drop the
serif, stating the standing rule directly: only Outfit or DM Sans, ever, for
content typography. Retired there too in the same pass: body moved to DM Sans
(matching the sitewide default exactly, 16px/1.65 — this page's own explicit
`.reader-body` size override is unaffected), the `Source+Serif+4` Google Fonts
import removed, and `linklib/brand_check.py`'s `ALLOWED_FONTS` allowlist tightened
to drop it, so a future reintroduction gets caught as a regression rather than
silently allowed back in.

| Family | Role | Weights |
|---|---|---|
| **Outfit** | Headings, display | 600 / 700 |
| **DM Sans** | Body copy, UI, labels, eyebrows — every reading surface on the site, reader templates included | 400 / 500 / 600 |
| **Caveat** | Sticker badges only — never headings or body | 700 |
| **Permanent Marker** | Sitewide wordmark (nav + footer logo) only — never headings or body | 400 |

### 3.1 Scale (as shipped)

| Element | Font | Size / line | Weight | Tracking |
|---|---|---|---|---|
| Wordmark (nav) | Permanent Marker | 17px | 400 | normal |
| Wordmark (footer) | Permanent Marker | 12px | 400 | normal |
| Home masthead (H1) | Outfit | 42px / 1.05 | 600 | -0.025em |
| Page title (H1) | Outfit | 30px | 600 | -0.02em |
| Section (H2) | Outfit | 21px | 600 | -0.01em |
| Subhead (H3) | Outfit | 15px | 600 | — |
| Eyebrow / kicker | DM Sans | 11.5px uppercase | 600 | .1em |
| Body | DM Sans | 16px / 1.65 | 400 | — |
| UI / labels | DM Sans | 13–15px | 500–600 | — |
| Merged reader title (`/read`) | Outfit | 30px / 1.18 | 600 | -0.02em |
| Merged reader body (`/read`) | DM Sans | 17px / 1.75 (15–20px cycle) | 400 | — |
| Standalone reader body (`/read/{article_id}`) | DM Sans | 16px / 1.65 | 400 | — |
| Reader chrome | DM Sans | 13–14px | 400–500 | — |

**Rules of thumb:** headings are tight (negative tracking) and Outfit; eyebrows are uppercase DM Sans
with wide tracking and `--muted` or `--navy`; every reading surface on the site — both reader
templates included — is DM Sans body copy, no exceptions.

### 3.2 Copy casing

**Sentence case for all page titles, headers, section labels, and buttons across the site** —
capitalize only the first word (and anything that's always capitalized on its own). "Software
features," not "Software Features"; "Add software," not "Add Software."

Exceptions:
- **Named products and features** — FP&A Buddy, CFO Toolbox, Reader, Archive Queue, Feed, Saved,
  Read Later — keep their own established capitalization wherever they appear. The last five
  (Reader/Archive Queue/Feed/Saved/Read Later) only ever appear on admin-only surfaces (`/read*`,
  `/admin/*`) as of the 2026-08 casing audit — none is public-facing today, so the exception's
  actual footprint is admin-internal only; if any of them ever surfaces on a public page,
  re-confirm the exception still makes sense there.
- **Proper nouns** — a company name, a person's name.
- **User-typed identifiers** — a literal string a visitor is meant to type or paste verbatim, not
  read as prose. "Save to CFO Library" (the bookmarklet/Shortcut name suggested on `/bookmarklet`)
  is the one instance today: it's a suggested Shortcut/bookmark *name*, the same category as a
  file name or a slug, not page chrome — sentence-casing it would suggest a different literal
  string than the one that actually works.
- **Acronyms** — ERP, FP&A, ASC 606, RBAC, AI, and the like — keep their own casing.
- **A named metric/framework, when it's the title of the article that defines it** — e.g. "The
  Growth Engine Ratio" — the same category as "Magic Number" or "Rule of 40": the proper name of
  the thing itself, not just a headline about it. This is a narrow exception, not a blanket
  article-title exemption (see below).

**Not exempt, despite reading like a named concept at first glance:**
- **"Thought Leadership"** sentence-cases to "Thought leadership" (nav link, the public page's own
  header, and the admin hub-nav GROUP name) — it never made the named-product list above. The
  admin page/card once titled "Thought leadership" itself was relabeled "Third-party content" in
  the Admin URL restructure (group A, 2026-09) once it moved inside a "Thought leadership" hub-nav
  group alongside Original content and Sail, don't row settings — same page/group-name-collision
  reasoning as never letting a page be titled the same as the section it lives inside.
- **"Warm Intro" and other CTA text** sentence-case the same way ("Warm intro") — a CTA's wording
  isn't a named feature just because it recurs across pages.
- **Software Matchmaker / Community Matchmaker** sentence-case to "Software matchmaker" / "Community
  matchmaker" — these are descriptive feature labels, not named products the way FP&A Buddy is;
  revisit if either is ever formally named as a distinct product.
- **"Sail Don't Row"** — removed from the named-product list (2026-08 update). The game at `/play`
  never had a punctuation-consistent name to begin with (no
  comma, no apostrophe curl, Title Case) and reads as ordinary imperative phrasing once you look
  past the capitalization, not a coined product name the way "FP&A Buddy" is. Canonical form
  everywhere it appears — the game's own h1, `/play`'s and the leaderboard's tab titles,
  `/admin/thought-leadership/game-settings`' h1 and its admin-dashboard card, and the article-title case below — is
  now the single sentence-cased form **"Sail, don't row"** (comma, lowercase after it). There is no
  longer a second, differently-capitalized form for the game itself vs. an article title reusing
  the phrase; both read the same way.
- **Article titles, as a general rule** — sentence-case like any other page title, e.g.
  "Connecting Claude to NetSuite" (already compliant: only the first word and the two proper nouns
  are capitalized). The named-metric exception above is narrow, not "article titles are exempt."
  Concretely: **"Sail, Don't Row: AI Hackathon Playbook"** — the article's own title — sentence-cases
  to "Sail, don't row: AI hackathon playbook", the same form the `/play` game itself now uses (see
  above) — there's no longer a second capitalization to reconcile.

Established as part of the Manage Features pivot-table redesign (Phase 1c, 2026-08); applied there
to the pages that PR touched (the admin dashboard's Software cards, the feature pivot table, the
review-queue page) rather than swept across the whole site. A dedicated, phased casing pass across
every existing page (Phase 0 investigation logged 2026-08: ~150–250 instances across ~50–60 routes,
concentrated in public directory/product chrome, admin chrome, `<title>` tab strings, and in-article
subheadings, which stay a case-by-case editorial call rather than a mechanical sweep) is in
progress, phase by phase, as its own multi-PR body of work.

The sitewide sentence-case sweep is complete as of Aug 2026 (Phases A–E); in-article
subheadings were hand-edited via admin rather than swept mechanically, per the
case-by-case note above.

### 3.3 Outbound links open in a new tab

**Every link whose destination is not on bmweis.com carries `target="_blank"
rel="noopener"`. No exceptions.** Internal links — a relative path, an anchor, or an
absolute `bmweis.com` URL — stay in the same tab, unchanged.

The reasoning is the reader's place on the page: a visitor part-way through an article,
a comparison, or a half-filled admin form shouldn't lose it by following a citation. It
matters most exactly where links are most useful — a credit list, a sources list, a
vendor's own site — which is where they're densest.

Two practical consequences worth knowing before writing copy:

- **Markdown can't express this.** `[text](https://example.com)` renders a bare anchor
  with no `target`, and markdown has no syntax to add one. So any outbound link in
  prose that renders through a markdown renderer has to be written as a raw
  `<a href="…" target="_blank" rel="noopener">` tag instead. That's why
  `/how-this-is-built`'s credit links are raw HTML rather than markdown, and why
  `original_content.body_md` already writes its outbound links the same way.
- **`rel="noopener"` travels with `target="_blank"`**, always — a new-tab link without
  it hands the opened page a live reference back to this one.

Enforced mechanically by `linklib.brand_check.outbound_link_problems()` — its own
`/admin/checks` row ("Outbound links open in a new tab") and a CI test
(`tests/test_outbound_links.py`). It scans hand-written `<a>` tags in
`webapp/app.py`; it deliberately cannot see links built dynamically in JavaScript or
links stored in database content (an admin's saved copy, AI-drafted fields,
user-submitted text). See that function's own docstring for why each of those is out of
scope, and §8.

---

## 4. The graffiti/street-art accent layer (restrained — accents only)

The previous "restrained New England nautical" motif (rope rule, compass star) is
**retired completely, everywhere, including the footer** — with one deliberate
exception: **the favicon stays the existing compass star.** Brian likes it as-is; it
is not part of the retirement and must never be changed, deleted, or redesigned as
part of this system or any future refresh.

In its place, a graffiti/street-art personality layer sits on top of the same
functional navy/seafoam/coral base — accents only, never a new illustration style,
and never on data-dense surfaces (admin tables, forms, chat UI stay completely
undecorated).

1. **Marker-underline** — a single hand-drawn wavy SVG stroke under one word of a
   hero heading. Color `#1F7A66` (seafoam-deep), 3.5–5px stroke depending on heading
   size, `stroke-linecap:round`. Max **one per page**. Helper: `_marker_underline()`
   in `webapp/app.py`. Currently used **only on the homepage hero** — it's not a
   sitewide heading treatment; don't extrapolate it onto other pages' H1s without an
   explicit call to do so (a past over-extrapolation onto About/Thought
   Leadership/CFO Toolbox landings was reverted).
2. **Sticker badge** — a small rotated callout: white background, 2px graffiti-ink
   (`#0d0d0d`) border, 4–6° rotation, hard drop-shadow `2px 2px 0 #0d0d0d` (no blur),
   Caveat 700 text at **15px** (hero/header stickers) or **14px** (smaller card-corner
   stickers, e.g. a WIP flag) — Caveat's script letterforms read small at anything
   below that. Lives in a header/hero corner or a card corner only — never inline in
   body copy. Max **one or two per page**. Helper: `_sticker()` in `webapp/app.py`.
3. **Card category icons** — 2px-stroke line icons (not flat color squares) inside
   the small badge on a card-row grid — 2-up, 3-up, or 4-up alike, not restricted to
   3-up rows. Fixed background order: seafoam-wash `#EAF7F2` → navy-wash `#EEF1F7`,
   cycling in that order regardless of column count and reused for every card-row
   grid sitewide. **Coral is not part of this cycle** (dropped entirely, PR 16,
   2026-09 — see §2.3's own writeup of the "spent by array position" incident this
   fixed; this passage was simply never updated to match at the time). Helper:
   `_card_icon()` in `webapp/app.py`.

4. **Spray-tag wordmark** — the sitewide "CFO Navigator"/logo mark, nav header and
   navy footer alike, renders in Permanent Marker instead of Outfit. Color unchanged
   (navy in the nav, white in the footer) — only the font swaps. No rotation, no
   halo, no separate decorative stamp elsewhere on the page; this reskins the
   wordmark text in place, sitewide, every page and breakpoint.
5. **The cassette J-card panel — a single, page-scoped exception, `/current-feed`
   only (2026-09).** Not a fifth generally-available accent — the closed
   four-item vocabulary above still holds for every other page. `/current-feed`'s
   tracklist sits inside `.cf-tape-card`: an off-white paper panel (`#fbfaf6`,
   reusing the existing GER-projected-quarter-input aux color rather than a
   new hex) with a warm-gray border (`#d0cac0`, likewise reusing the reader
   empty-state input border), a warm-black drop shadow, and a slight (`-0.6deg`)
   tilt — the physical cassette J-card the page's copy already describes, not a
   nod to the aesthetic. A `2px` graffiti-ink bordered square (`.cf-ab-box`,
   reusing `--ink-graffiti`) holds each side's letter (A/B) directly beside its
   name. Approved after two full mockups (card-only, and card-plus-a-plastic-
   case) were built and screenshotted before any code shipped, at Brian's
   explicit ask that CSS's real ceiling be reported honestly rather than
   guessed at: the case (a tinted gradient, a diagonal sheen, repeating-gradient
   spine-hinge dashes) read as a convincing STYLIZED case, never photorealistic
   — a flat gradient and a border-radius aren't real depth — and its cost wasn't
   judged worth it against the plainer card, which already reads as "cassette"
   on its own. **Only the card shipped; there is no case anywhere on the live
   page.** At `max-width:430px` the tilt, shadow, and rounded corners all
   flatten to a plain bordered rectangle — no case, no tilt, no shadow drama on
   a phone. Never extrapolate this to another page without the same
   investigate-and-propose gate this one went through.

**New tokens** (see §7 for the generated block): `--ink-graffiti:#0d0d0d` (sticker
borders/shadows, plus the cassette A/B box border above — never a fill or text
color elsewhere), `--font-sticker:'Caveat',cursive`, and `--font-wordmark:
'Permanent Marker',cursive` (wordmark-only — no exception; a first pass at
`/current-feed` below tried one and it was reversed).
**One approved, deliberate exception to "sticker-only" for `--font-sticker`**:
`/current-feed`'s tracklist (2026-09) sets each track's title in
`var(--font-sticker)` at 18px/700 (bumped from an initial 16px in a later pass;
no rotation, border, or drop-shadow — the plain text style only, not the full
sticker-badge treatment), title only — never metadata (author, cadence stay DM
Sans). A first pass used the wordmark font instead, at 20px; reversed on direct
instruction, since Permanent Marker is built for a word or two, not a list of
names of varying length, and Caveat is already proven readable at this size in
sticker badges. Don't extrapolate either exception onto a denser list (an admin
table row, a card grid of many short labels) without the same size/short-text
reasoning holding.

That's the entire graffiti vocabulary, plus the one page-scoped cassette-panel
exception above. No broader illustration style, no all-over pattern, no
graffiti marks on admin tables, forms, or the chat UI.

---

## 5. UI components

- **Buttons** — primary = navy fill; secondary = ghost (navy outline, transparent). Radius 10px.
  Hover deepens to navy-deep. *Color is never a button background* — with one sanctioned
  exception: **destructive actions** (Delete/Reject) use `#b91c1c` text and border, `#fee2e2`
  hover fill, `#fca5a5` reject border. This is a semantic status color communicating
  "irreversible/dangerous," the same category as `--alert`, not a decorative button color —
  see `AUX_COLORS` in `linklib/brand_check.py` for the exact values.
- **Inline row-action buttons** — a third, smaller button style for compact per-row actions inside
  a list or card (e.g. "Quick edit" / "Full edit" / "Delete" / "Generate" on a Toolbox card, "Edit
  tags" on the reader). `--muted` text, `--line` border (not navy), radius 6px, ~12px font,
  `padding:3px 10px`. Hover fills `--navy-wash` (`--accent-light`) with `--ink` text. Quieter than
  the primary/secondary pair by design — these sit inside dense rows where a full navy or
  navy-outline button would compete with the row's own content, not label the row's primary action.
- **Inputs** — white surface, `--line` border, radius 10px. Focus = navy border + soft seafoam
  ring `0 0 0 3px rgba(163,229,212,.55)`.
- **Tags / badges** — seafoam fill, navy text, radius 6px, 600 weight, ~11px.
- **Review-status pill** — a whole-record admin bookkeeping signal (Software/Communities admin
  list rows, both public profile view pages when signed in as admin, and the top of both edit
  pages): `--seafoam` fill + navy text for "Reviewed", `--coral-wash` fill + navy text for "Needs
  review" (optionally with an "(n/total)" breakdown). Radius 999px (a true pill, not the 6px tag
  radius) — deliberately larger/rounder than the per-field badge below, so the two read as
  distinct component families even though they share the same color language. Coral-wash pairs
  with navy text, never `--coral-deep`, since `small_coral_text_spans` (§8) mechanically bans
  coral/coral-deep text under 18px. Helper: `_review_status_pill_html()` in `webapp/app.py`.
- **Per-field review badge** — the radical-transparency review standard's (§9-adjacent; see
  ARCHITECTURE.md/CLAUDE.md for the full mechanism) small trailing indicator on a Software/
  Communities field or card that hasn't been human-reviewed yet: `--coral-wash` fill, navy text,
  radius 5px, uppercase, 10px, 700 weight. Reads "under review" to a visitor, "unverified, visible
  to visitors" to an admin — populated content always renders regardless of review state; only
  this trailing badge differs by audience. Helper: `_review_state_badge()` in `webapp/app.py`
  (`.tp-verify`/`.cc-verify` CSS classes).
- **Admin section headings** — an informal sub-heading role used to break up an admin page into
  named sections (e.g. "Pending submissions" / "Approved software" on the Toolbox review pages,
  "Competition" / "Screenshots" / "Key features" on the tool-edit page, dependency-group
  titles on `/admin/open-source`). 16px, 600 weight — smaller and lighter than the base `h2` (21px/600),
  since these mark subsections within a page rather than the page's own top-level sections.
- **Cards** — white surface, `--line` border, radius 12–16px.
- **Card titles** — Outfit, 17px, 600 weight, `-0.01em` tracking, `--ink`. One standard across
  every card family sitewide: the Software/Community/Benchmark directory cards, thought-leadership
  landing cards, case-study cards, and the archive/feed cards all converged to this single value in
  the brand audit (previously split across three sizes and three weights with no shared standard).
  The one confirmed exception is `.sdr-outcome-title` (Sail, don't row's game-over overlay,
  800 weight) — deliberately bolder, verified side-by-side against the 600-weight standard and kept
  distinct because it's a single bespoke result overlay, not a family of cards sharing a role.
- **Disclosure/accordion** — two variants. *Group-level* (top-level Admin sections, e.g. `/admin`
  index groups, and the Reader group's quadrants nested inside CFO Toolbox): bordered/boxed row,
  bold all-caps label + muted item count left-aligned, arrow right-aligned, points right collapsed
  / down expanded. *Item-level* (nested toggles within a section, e.g. capture-method
  instructions): bordered box, arrow left-aligned before the label. Don't invent a third variant —
  pick group or item based on hierarchy depth. The group-level row is one shared component,
  `_disclosure_group` in `webapp/app.py`; reuse it rather than rebuilding the markup. **Every
  disclosure, at every nesting level, loads collapsed** — no exceptions, and nesting goes three
  deep on `/admin` today (CFO Toolbox → Reader → a quadrant), so "collapsed by default" has to
  hold for a group that only ever renders inside another one.
- **Tables** — one format for every table on the site (2026-09): a
  light-blue `--accent-light` header row with 13px sentence-case header
  text, **white rows** (`--surface`), a `--line` rule between rows, and a
  rounded 12px frame in `--table-border` (`--navy-light`). No zebra
  striping, no navy header rows. Admin pages, articles, the Compare pages,
  and the FP&A Buddy explainer all use it.
  - **Secondary format, for a table with subheading rows** (the Compare
    pages' section bands): the band is `--table-border` navy-light with
    white text, and the label column stays white rather than beige.
    On Compare, field names live in that first column (bold, sentence case),
    and the Bottom line row leads as a plain white row, set apart by a 3px
    `--navy` rule on its label (not a tint: a navy-light tint merges with the
    table header row). The AI summary card above the table opens with an h2,
    "How they compare".
  - It all lives in one `!important` block in `_CSS`, scoped to
    `main.site-main`, so a new table gets the format without anyone
    remembering it, and older inline styles (including article HTML stored
    in the database) can't override it. A table with sticky columns or a
    sticky label can't clip its own corners (`overflow:hidden` on the table
    breaks `position:sticky`), so its scroll wrapper carries the frame via
    `.table-frame` instead. The frame-child rule repeats the generic rule's
    `:not()` chain so it out-ranks it; before that it silently lost, every
    framed table kept `overflow:hidden` and its own border (a double frame),
    and the sticky Name column on the Software and Communities admin tables
    scrolled away (`tests/test_table_frame_sticky.py`).
  - **A table wider than its scroller needs `.table-frame` on the scroller.**
    A bare `overflow-x:auto` div around a table leaves the border on the table,
    so on a phone the right border scrolls out of view. The pending
    submissions tables (Software, Communities) and the contact deletion
    history are framed; the other bare tables are a separate, later pass
    (`tests/test_table_frame_mobile_polish.py`).
  - **A frame sits flush: no negative margin above it, no margin on the table
    inside it.** The frame draws the border, so a negative bottom margin on the
    control row above (the contact submissions "Delete selected" row had
    `-8px`) puts the frame's top edge behind the button, and a `margin-top` on
    the inner table shows as a white strip inside the frame. Put the gap on the
    element above the frame (`tests/test_table_frame_mobile_polish.py`).
  - **Every scroller around a table is a frame.** A scroller div that holds a table carries `.table-frame` (`tests/test_table_frame_mobile_polish.py` fails on a bare one). The only tables left without one are those that never scroll: the checks summary tables and the Sections table.
  - **A table whose last column holds row buttons heads it "Actions".** Never blank, never "Tools", never a variant. Build the cell with `_actions_th()`; a header with a checkbox or an `aria-label` is not blank. A column that mixes a count with buttons is split (Software categories: Tools, then Actions). `linklib.brand_check.actions_header_problems()` scans source for a blank header, or a button column headed otherwise, with an empty `ACTIONS_HEADER_ALLOWLIST` for any exception that needs a reason. It cannot see a table whose body rows are built in a different string than its header, so rule 2 is a floor; rule 1 (no blank header) holds everywhere. Shown on `/admin/checks`.
  - **Stacked cards** (the `.admin-table-responsive`, `.ff-table`/`.fs-table`
    and `.backup-log-table` layouts under 700px, 820px for the feeds tables):
    cells carry no top border, cards are separated by a 1px `--line` rule with
    none under the last one, and a checkbox shares its row with the name. The
    "no cell border" rule is written with the table's class and `.site-main`
    repeated so it out-ranks the generic td rule without a new `!important`;
    the version written before never applied.
  - **Sticky Name column** on the Software and Communities lists: one pinned
    column holding the checkbox and the name, `_COL_WIDTH_NAME_STICKY` (230px,
    26% of an 874px landscape phone). It is a cap for a pinned column, not
    `_COL_WIDTH_NAME`, which is a floor for an unpinned one.
  Covered by `tests/test_table_format.py`, and
    guarded live by the "One table format" row on `/admin/checks`
    (`brand_check.table_standard_problems`/`table_override_problems`): it
    fails if the block loses a rule, the border token changes, the scope
    grows an exclusion that isn't approved, or any other CSS rule sets a
    table's background or border with `!important`.
  - Not tables in this sense, so excluded: the profile page's Competitors
    logo list (`.tp-competitor-table`) and other sites' article HTML in the
    Reader (`.rr-reader-body`), where newsletters use tables for layout.
  Checkbox/boolean-indicator columns are always center-justified, header and
  cells alike. Text, link, and dropdown columns are left-justified. Actions
  columns are right-aligned. Once a table collapses to stacked labelled rows on
  mobile the centring is dropped, since there are no columns left to align
  within.
- **Links** — navy; optional seafoam underline for emphasis in editorial copy.

### Character budget (capped text fields)

A capped admin text field shows "Limited to N characters." and a live count
directly under it, in 12px `--muted` text. No HTML `maxlength` on these
fields, because a browser silently cuts a paste to it — the count and the
server-side refusal are the only enforcement. Built by `webapp.app._char_budget`.

Two tiers, for every AI-drafted field prone to drift (an admin-authored-only
field, e.g. `category_features.definition`, carries only the hard limit
below — no soft target, since there's no generator output to nudge):

- **Soft TARGET** — past it, the count turns `--caution` amber and reads
  "N characters. Aim for &lt;target&gt;." The save still works; this is an
  editorial nudge, not enforcement.
- **Hard MAX** — past it, the count turns `--alert` red, says how far over
  it is ("N characters, N over. This save will be refused."), and the
  form's submit button reads "Over limit" and disables. The server refuses
  the save outright and writes nothing.

The disabled "Over limit" button uses `cursor:not-allowed`, and a form may add a visible
sentence beside it saying why (`.char-budget-reason`, `--alert` text). The reason is never
only a tooltip.

Never coral for either state — both are warning states, not accents.

| Field | Target | Max |
|---|---|---|
| `tools.description` | 2,500 | 3,500 |
| `tools.summary` | 400 | 800 |
| `tools.agent_taxonomy_note` | 2,500 | 4,000 |
| `tools.competitive_differentiation` | 600 | 1,200 |
| `community_profiles.stage_focus`/`jobs_program`/`team_or_individual` | 300 | 800 |
| `tool_feature_links.public_note` | 500 | 1,000 |
| `category_features.definition`/`pointer_note` | — | 10,000 |

Every MAX clears its field's own longest value already stored in production
(a 2026-09-23 length read), with real headroom — see `linklib/db.py`'s own
comment above these constants for the exact numbers and reasoning, and the
character-budget-limits-targets PR (CLAUDE.md) for why `max_tokens` itself
was deliberately left unchanged on the two fields with a documented prior
truncation incident (Description, Agent taxonomy) — only the prompt's own
stated word/character ceiling moved.

### Admin and public names match

A control on an admin edit page that produces something a visitor sees carries the
same word the visitor sees. The words live as constants in `linklib/compare.py`
(Program details, Reach, Cost band, Sponsorship, Access, CPE eligible, Format,
Featured, Formal advisor), and the whole-record review pill reads "Under review",
the visitor's word. Only admin-only controls with no public rendering (Verification
status, Profile draft, Save buttons, Logo and Screenshots controls) may use their own
names. The Program details grid on the community edit page sets one explicit control
height (47px, `--program-ctl-h`) on its inputs and selects, because an input inherits
the page line height and a select does not.

### Radius scale
`10px` buttons & inputs · `12–16px` cards & panels · `6px` tags/chips & inline row-action buttons ·
`999px` filter pills.

### Layout

Two width tiers, keyed to content shape rather than one global reading measure.
Fully migrated as of the Phase 9 route sweep — every page-rendering route carries one
of the tiers below; the old three-tier system (`.page` 780px / `.page-narrow`
480px / `.page-wide` 960px) is retired and those two classes no longer exist in the
CSS. (The old `.page` measure had drifted from its documented values before this
system landed — the GER calculator was never actually 820px, it used plain 780px;
860px belonged to `/library/feed`, not the Toolbox grid; both since corrected.)

**Collapsed from four tiers to three (PR 13, 2026-09), then three to two
(PR 14, 2026-09).** PR 13's prior system's `.page-full` (1440px) and
`.page-admin` (1400px) were 40px apart — a distinction no reader could
perceive and no one could maintain deliberately — so both retired outright
into one **Standard** tier at the old `.page-grid`'s own 1300px, the width
Brian confirmed already felt right (a genuine 3-into-1 merge of
`.page-full`/`.page-grid`/`.page-admin` into `.page-standard`, not a rename
of one survivor). PR 13 also split off a narrower **Content** tier (900px)
for the handful of pages that are pure long-form reading — but its own
investigation had already found, before shipping, that on every one of
those pages essentially nothing lives outside the 760px `.tool-prose`
reading column: a back-link line, an eyebrow, or a diagram/table already
capped narrower than `.tool-prose` itself. A tier that changes the width of
a few short lines of text and nothing else isn't a tier, so PR 14 removed
Content outright — every page that used `.page-content` now uses
`.page-standard`, confirmed pixel-identical inside `.tool-prose` before and
after, since `.tool-prose` (unchanged) is what actually makes a content
page read as a content page, not the outer shell.

| Tier | CSS class | Width | Pages |
|---|---|---|---|
| Standard | `.page-standard` | 1300px | Every page except forms — homepage, Thought Leadership landing, every CFO Toolbox directory/profile/compare/matchmaker page (Software, Resources, Communities), every `/admin/*` list/dashboard/report page, `/admin/open-source`, FP&A Buddy chat, Growth Engine Ratio calculator, Sail, don't row (+ its leaderboard), `/read/{article_id}`, About, Thought Leadership's 3 ported long-form articles (Growth Engine Ratio, Sail Don't Row/AI Hackathon Playbook, Connecting Claude to NetSuite), `/tools/fpa-buddy/how-it-works`, `/ask/history` |
| Form | `.page-form` | 640px | Contact, login/forgot/reset-password, Privacy, all member-submission forms (library/tool/community submit, compare-summary feedback) — genuinely public, one-column forms only, never an admin edit form (2026-09 fix: `.page-form` had drifted onto Feeds/Resources/Third-party-content's admin add/edit forms; all three moved to Standard, matching every other admin edit form) |

Every admin single-record add/edit form (a tool, a community, a resource, a
feed, a thought-leadership entry, an AI surface, an original-content piece)
sits on `.page-standard`, not `.page-form` — the reference shape is
`_ai_surface_form_page`/`_oc_form_page`: a back-link + `<h1>` at the page's
own left edge, with just the `<form>` itself capped at
`max-width:900px;margin:0 auto`, so a single-column field stack still reads
narrow while the page shell matches every other admin page. `.page-form`'s
640px cap on the whole page (back-link included) reads too cramped once a
page has its own nav chrome — reserve it for a standalone, single-purpose
public form with nothing else on the page.

Admin data tables sit on the same 1300px Standard tier as everything
else, not a wider dedicated tier of their own — they already carry their
own `min-width` floors and `overflow-x:auto` horizontal scroll (PR 12,
2026-09; the floors themselves standardized into four rule-based buckets in
PR 14, 2026-09 — see "Admin table width floors" below), so narrowing their
shell doesn't squeeze a wide table's columns, it just scrolls the table
sooner. This was verified directly against the widest admin tables (Users,
with its column picker, especially) at both the 1300px shell and at a
390px mobile viewport before merging.

Combine with `.page` for its margin/padding (e.g. `class="page page-standard"`).
Generous page padding (≈48px top). Whitespace before density. Admin/data pages
get the width bump for scannability, not decoration — they never get any part
of the graffiti layer (§4). One page (`/read`, the merged Reader) uses a
bespoke full-bleed `.rr-shell` app layout outside the `.page` system entirely
and isn't part of this tier table — it's a fixed-height three-pane shell
(rail/list/reader), not reading-width content, so no single width constraint
applies. (Its two predecessor pages, `/library/archive` and `/library/feed`,
used this same carve-out at 960px and 860px respectively before the Phase 5
Reader merge retired both routes and replaced them with `/read`.)
`/read/{article_id}` (the standalone single-article Reader view, a different
route from the merged three-pane `/read` shell) is built from its own
`_READER_TMPL`/`_READER_CSS` and never uses the `.page`/`.page-standard`
classes directly, but its `.reader-layout` deliberately tracks
`.page-standard`'s own max-width — its two-column layout (a 760px reading
column plus a 220px sticky "On this page" TOC, joined by a 40px gap) needs
~1020px of real headroom just for the two columns, before any side
padding; the old, now-removed Content tier's 900px would have forced the
reading column to shrink below its own 760px floor, which is exactly the
measure this page exists to protect.

The former `.page-tool` tier (960px, "functional tools") was retired in Phase 9b —
those pages (FP&A Buddy, GER calculator, Sail, don't row + leaderboard) sit on the
Standard tier like every Toolbox/admin page, so they no longer feel visually cramped
next to it. GER calculator and Sail, don't row + leaderboard each still wrap their
actual working content (calculator, game canvas) in `.tool-inner` (1300px, centered,
card-grid scale) so the widget gets real room — identical to Standard's own 1300px,
so neither the PR 13 nor the PR 14 tier collapse changed anything about how these two
pages actually render. FP&A Buddy dropped its own `.tool-inner` wrapper in PR 17
(2026-09) — it was a pure no-op there (both values were already 1300px) once its top
section was rebuilt as a real two-column grid (description/example side by side,
filling to a single column on mobile) sitting directly on `.page-standard`. The
Growth Engine Ratio calculator's long-form paragraphs nest a narrower `.tool-prose`
(760px) inside its wrapper — 1300px is too wide a text measure to read comfortably,
but the calculator itself benefits from the extra width.

`.tool-prose` isn't limited to `.tool-inner` — it's a general-purpose narrow-reading
wrapper (max-width 760px, centered) usable inside any wider tier. (An earlier version
of this passage claimed the three `/admin/system/*` reference pages — Database, Page
Index, How FP&A Buddy works — all reused it directly inside `.page-admin` (now
`.page-standard`). Corrected, 2026-09: "How FP&A Buddy works" moved off `/admin/*` to
the public `/tools/fpa-buddy/how-it-works`; Database and Page Index render their intro
copy as a bare `<p>` with no `.tool-prose` wrapper at all. Neither gained a wrapper as
part of this correction — this is a doc fix, not a code change.)

**A table or grid on a content page belongs inside the reading column.** When a page
uses `.tool-prose`, every block a reader reads through—prose, tables, card
grids—is a descendant of it, not a sibling. A sibling renders at full
`.page-standard` width and breaks the column. Fixed on `/how-this-is-built`, and on
`/tools/fpa-buddy/how-it-works` in #583. Pages with no reading column (compare
tables, `/admin/system/page-index`) are unaffected: full width is correct there.
Checked mechanically by `brand_check.reading_column_problems()` (§8).

The brand audit's Phase 4 also found four pages with *no* reading-width constraint at
all — AI Hackathon Playbook, Connecting Claude to NetSuite, `/ask/history`, and the
since-retired `/library/past-questions` (folded into `/tools/fpa-buddy`'s "Search past
questions" section in the Phase 2 Library/Toolbox restructure, after this fix already
landed) — rendering body copy at the full `.page-full` measure (~1850px at the time).
A brief attempt at a new sitewide 1500px prose ceiling was tried and reverted (too wide
for comfortable reading, outside the usual 60–75-character-per-line guidance); the
interim fix was the same `.tool-prose` (760px) wrapper already proven on GER and the
SYSTEM pages, applied to those four as well.

**Layout sizing, as one family.** The two page tiers above, the table
width floors, the per-column widths, and the card widths below are four
instances of the same discipline: pick a value once, by rule, from what a
thing actually holds (a page's content shape, a table's column count, a
column's field type, a listing's item count) — not by eye, per instance.
Read them as one family, not four separate systems.

### Admin table width floors

An admin table's `min-width` — the point below which it scrolls horizontally
(inside its own `overflow-x:auto` wrapper) rather than squeezing its columns
unreadably narrow — is picked from **four rule-based buckets, by column
count**, not chosen by eye per table (PR 14, 2026-09, replacing 23 tables'
worth of hand-picked values from PR 12/PR 529):

| Bucket | Floor | Columns |
|---|---|---|
| Narrow | 480px | 2–3 |
| Medium | 640px | 4–5 |
| Wide | 800px | 6–7 |
| Extra wide | 960px | 8+ |

Defined as named constants in `webapp/app.py` (`_TABLE_FLOOR_NARROW` /
`_TABLE_FLOOR_MEDIUM` / `_TABLE_FLOOR_WIDE` / `_TABLE_FLOOR_XWIDE`), not
repeated literals. The point of a bucket system is that adding a column to
an existing table has an obvious answer — "does this cross a bucket
boundary?" — where "is 760 still right for this table?" didn't. Column
count means the columns actually rendered by default: for a column-picker
table (Software/Communities/Users, `.admin-table-responsive`, backed by
`_admin_column_picker_html`), that's the always-visible columns (Name,
Actions) plus whatever `ADMIN_DEFAULT_VISIBLE_COLS` shows by default, not
every optional column a viewer could toggle on.

`.admin-table-responsive` tables carry `min-width:0!important` on their own
`@media(max-width:700px)` card-stacking rule, keyed off the `.admin-table-
responsive` class rather than any specific pixel value — a bucket's inline
`min-width` on the `<table>` itself is otherwise something no plain media
query can beat, so without this override the mobile card view would stay
pinned at its desktop floor and force an invisible, pointless horizontal
scroll on an otherwise correctly stacked card. Preserved as-is by the PR 14
bucket sweep; re-verified at 390px, not just assumed to still hold.

A table whose real content forces a floor above what its column count would
otherwise assign (long URLs, a wide date-and-status column, a description
column with real prose) is a documented exception, not a silent one — see
the PR 14 build notes for the specific tables this applied to and why.

**PR 15, 2026-09 — the Software and Communities admin tables are no longer
two of those exceptions.** Both used to compute their own hand-tuned floor
(820px and 880px respectively) from their real rendered content; per
Brian's explicit call that the two tables should be **structurally
identical, not merely similar**, both now share the Extra-wide bucket
(960px) and a 280px sticky Name column (Software widened from 220px) — the
first instance of a larger, separately-scoped job (matching one field's
width everywhere it appears across every admin table), not a general
license to collapse every documented exception into a bucket.

### Admin table column widths, by field type

The job PR 15 named above: a column is now sized by what FIELD TYPE it holds,
not by whatever a given page happened to pick — so "Name" is the same width
everywhere, not 220px on one admin table and a bare, unspecified width on
another. Six named constants in `webapp/app.py`, next to the floor buckets
above:

| Constant | Width | Field type |
|---|---|---|
| `_COL_WIDTH_NAME` | 280px | Name / Title (matches the Software/Communities sticky Name column, PR 12/15) |
| `_COL_WIDTH_EMAIL` | 220px | Email address |
| `_COL_WIDTH_DATE` | 140px | Date / timestamp (sized for a full "YYYY-MM-DD HH:MM" value) |
| `_COL_WIDTH_STATUS` | 110px | A short status/state badge or label |
| `_COL_WIDTH_COUNT` | 80px | A small count/number column |
| `_COL_WIDTH_MESSAGE` | 320px, as `min-width` | A free-text column that is the point of its row (a contact message, an error, a flagged summary). A floor, not a fixed width: on the contact table it measured 109px at both 390px and 874px because the width-hinted Date, Name and Email columns took the table's 800px minimum first. The contact table's floor is now `_TABLE_FLOOR_XWIDE`, so it scrolls on a phone and landscape tablet instead of squeezing the message |
| `_COL_WIDTH_PERSON` | 160px | A person's name (a contact's name), narrower than `_COL_WIDTH_NAME`, which is for software and community names |
| `_COL_WIDTH_VENDOR` | 160px | A short vendor/company label, or a username (the FP&A Buddy report's Asker column, which used `_COL_WIDTH_NAME` and left the Question column about 100px wide) — deliberately narrower than `_COL_WIDTH_NAME`, which is calibrated for a full software/community name, not a one- or two-word vendor label (overhead spend fixes, 2026-09) |

These are plain `width:` hints on ordinary (non `table-layout:fixed`) tables,
not a hard cap — real content wider than the hint still grows the column
instead of getting clipped. A column holding a description, a reason, a URL,
or any other free-text field stays unwidthed and absorbs the remaining
space; every table needs at least one such column, same as it always has.
Applied wherever a column's own header literally names one of these field
types — a column that merely resembles one (e.g. a CSV-preview "Article"
column holding just `#123`, not a real title) is left alone.

**Scope: this standard applies to auto-layout ENTITY-LIST tables** — tables
whose rows are named database entities (a vendor, a community, a contact, a
user, a log entry) rendered with plain typed columns. It was never meant to
reach every `<table>` in the admin surface, and a first pass at this
documentation over-counted by treating "doesn't use these constants" as
"exception" — most of the tables below were never in scope to begin with,
not deviations from a rule that applies to them. Two genuinely different
reasons put a table outside that scope:

**Fixed-layout tables use a different sizing mechanism entirely**, so the
question "should this column be `_COL_WIDTH_NAME`?" doesn't apply — a
`table-layout:fixed` table's columns are set by percentage or by its own
named-class pixel widths, and can't sensibly mix either with these
constants:

- **Resources** (`/admin/tools/resources`) — percentage-based
  (`_ADMIN_RESOURCE_TABLE_COL_WIDTHS`, PR 11) so two tables sharing the same
  markup line up regardless of content length.
- **`/admin/reader/feeds`'s `.ff-table`/`.fs-table`** — same
  percentage-based reasoning, tuned to that page's own content.
- **`/admin/library-backup`'s `.backup-log-table`** is its own named
  pixel widths (`_BACKUP_COL_WIDTH_WHEN` 150, `_BACKUP_COL_WIDTH_FILENAME`
  236, `_BACKUP_COL_WIDTH_LOCATION` 154, `_BACKUP_COL_WIDTH_STATUS` 90), each
  sized to its content (a backup filename and the Drive link are short
  tokens that must not wrap; short tokens carry `white-space:nowrap`).
  Notes has no width and takes the rest. `_BACKUP_TABLE_MIN_WIDTH` (860)
  keeps Notes readable between the card breakpoint and a wide page, and the
  700px mobile-card rule resets it with `min-width:0 !important`, because an
  inline-style or table-level floor would otherwise pin the stacked card
  wide.
- **`/admin/checks`'s result column** (`_CHK_STATUS_COL_WIDTH`, 200px) is a
  fixed width rather than a third of the row: its widest unbreakable content
  is the Mark reviewed button (about 130px), and the explanation text takes
  the rest.

**Diagnostic and reference tables aren't entity lists**, so the
Name/Email/Date/Status/Count vocabulary doesn't describe what their columns
actually hold:

- **`/admin/system/database`, `/admin/system/page-index`, and the FP&A Buddy
  explainer's tier table** reuse the Compare page's `.cc-table` CSS class
  for convenience — their rows are a schema table name, a route path, a
  model tier, not a directory of named entities.
- **The admin brand-showcase page's two example tables** (the GER tier
  table, the Checks reference table) are illustrative component specimens,
  not real data.
- **The Reader content-backfill's three tables** (Recent attempts, Needs
  manual review, Accepted as final) are a diagnostic worklist of fetch
  attempts, not a directory of named entities — built from shared
  `_th`/`_th_nowrap` helpers reused across genuinely different field types
  per column (Article, Result, Attempts, When), including an already-tuned
  420px Article column, rather than the simple per-field-type shape this
  standard covers.
- **The Software name-duplicate check's Tool A/Tool B/decision tables**
  are a diagnostic comparison report, not an entity list — each cell holds
  rich, multi-line content (`_tool_cell()`), not a plain name string.

That leaves **two real exceptions**, both genuine entity-list tables this
standard does cover, each kept at its own value for a stated reason — the
handful this standard's own PR anticipated:

- **Overhead spend's "By source" and "By month" summary tables** sit inside
  a `flex:1 1 460px` column with its own tight `min-width:400px`/`320px` —
  applying `_COL_WIDTH_NAME` to their label column would force them wider
  than the layout they're built to fit inside.
- **The Users admin table**'s Username column is left unwidthed — it's a
  primary identifier without a real analog among these five field types,
  and it's always visible (no `data-col`), unlike every other column on
  that table. Its Name/Email/Last login/Status columns do use the shared
  constants.

The Software and Communities approved-tables' sticky Name column was the
origin case for 280px. Since Refs 655 (B1) it is a single pinned column that
holds the checkbox too, sized by its own `_COL_WIDTH_NAME_STICKY` (230px),
because 280px plus a separate checkbox column was 37 to 45% of an 874px
landscape phone.

**Overhead spend's "All vendor charges" details table** (2026-09) is the
origin case for `_COL_WIDTH_VENDOR`: its Vendor column used to carry
`_COL_WIDTH_NAME` (280px, calibrated for a full software/community name),
which was too wide for a one-word vendor label ("Railway", "Anthropic")
and starved the table's genuinely free-text Note column of room, wrapping
a real note to 5 lines at a scrolled mobile width. Fixed with the narrower
`_COL_WIDTH_VENDOR` constant plus `white-space:nowrap` on Category and
Amount (both short, fixed-vocabulary fields that shouldn't wrap at all) —
Note stays unwidthed, per the standard's own free-text rule, and now
absorbs the space Vendor and Category no longer need. The table's
`min-width` also moved from a hand-picked `720px` to the Wide bucket
(`_TABLE_FLOOR_WIDE`, 800px — Vendor/Date/Category/Note/Amount/Actions is
6 columns), matching the bucket system rather than a leftover literal.

### Card widths

**Cards keep their width; containers distribute them.** A card stretching
to fill its row reads wrong — the same family should render at the same
width whether its row holds 1 card or 20.

**The mechanism**: on a `grid-template-columns:repeat(_, minmax(_,1fr))`
listing grid, use `auto-fill`, never `auto-fit`. `auto-fit` stretches
populated tracks to fill the row — it collapses whatever tracks a sparse
row doesn't need and hands that space to the cards that are there.
`auto-fill` keeps every card at its floor width and leaves the leftover
space empty beside it. For a card listing, `auto-fill` is correct: the
phantom tracks are a feature, not a bug — they're what preserves a card's
width when the list is short.

| Constant | Width | Card family |
|---|---|---|
| `_CARD_WIDTH_DIRECTORY_MIN` | 320px | Software + Communities directory cards (`.tool-card`/`.comm-card`) |
| `_CARD_WIDTH_RESOURCE_MIN` | 260px | Benchmarking + Books cards (`.bench-card` — one shared class, no separate `.book-card`) |

**When the distinction doesn't matter**: a grid with a fixed item count
that always matches its own shape exactly has no sparse case, so
`auto-fill` and `auto-fit` render identically — there's nothing left for
the rule to protect. Three families are out of scope for exactly this
reason, named here so a later pass doesn't "standardize" them onto these
constants for consistency's sake and reintroduce the complexity this rule
exists to prevent:

- **Toolbox landing tiles** (`/tools`, `.toolbox-grid`) — a fixed
  `1fr`→`1fr 1fr` grid, always exactly 4 tiles.
- **Admin hub group cards** (`.admin-cols`, top-level and nested) — a
  fixed `1fr`→`1fr 1fr` grid of always-6 top-level groups, and a
  single-column list for nested items with no `grid-template-columns` at
  all. These are `<details>` accordion sections, not a card-row grid to
  begin with — catalog size changes a badge number, never the layout.
- **Homepage sidebar panel and Recent highlights** — the Toolbox panel is
  one fixed-360px card, nothing to distribute; Recent highlights is a
  fixed 2-column grid capped at 4 hand-curated featured pieces (any mix
  of types — no longer one per thought-leadership type, see CLAUDE.md's
  "Recent highlights" bullet), and collapses entirely (heading and grid
  both) rather than stretching when there's nothing to show.

**Homepage Original content block** (`.tl-featured-home`): the homepage's
flagship cards are a fixed 2-column grid (one column below 560px), not the
shared `.tl-featured` auto-fill rule. The query caps the block at 4 pieces
(`Library.HOME_ORIGINAL_CONTENT_CAP`), so item count always matches shape
and there is no sparse case. Cause of the old three-across layout, measured:
the shared 220px floor fit only 3 tracks in the ~820px column (4 need 922px).
`/thought-leadership`'s own `.tl-featured` row is unchanged. The block's
label reuses the "Recent highlights" label style. The Original content
admin page notes any flagged pieces the cap hides.

**Directory cards: clamp and `min-height` are a pair.** On the two directory
cards (`.tool-card` on `/tools/software`, `.comm-card` on `/tools/communities`),
a `-webkit-line-clamp` on a variable-length field bounds its ceiling, and a
matching `min-height` on the same element keeps rows even. CSS Grid sizes each
row by its own tallest card, so a clamp alone still leaves short rows shorter.
Apply both together on every variable-length field in these cards, or neither.
The cards keep a "Full profile" link so the clamped text is one click away. No
test pins the pairing today; `tests/test_resources_card_no_clamp.py` covers only
Resources, which has no clamp.

**Resources cards are not clamped (2026-10).** `/tools/resources` card
names and descriptions render in full: no `-webkit-line-clamp`, `overflow`,
`max-height` or `min-height` floor, and no "Show more". Cards keep the
`_CARD_WIDTH_RESOURCE_MIN` floor on an `auto-fill` grid, and cards in one row
stretch to the tallest (grid default). Rows may differ in height from each
other, by design. A clamp hides text; the only clamps left on the site are the
two directory cards (`/tools/software`, `/tools/communities`), which keep a
"Full profile" link. Do not add a clamp to a card without that link.
`tests/test_resources_card_no_clamp.py` measures it in Chromium.

### Social share cards

**1200×630 PNGs, committed to `webapp/static/og/`, hand-built — not generated.**
Generation was investigated and killed outright (Phase 2 of the original build,
2026-09): Pillow is a dependency this codebase has deliberately avoided at least
three separate times (client-side Cropper.js for the App screenshot upload instead
of server-side image processing; `scripts/audit_tool_logo_dimensions.py`'s own
"NO NEW DEPENDENCY" hand-parsed PNG/JPEG/GIF/WEBP/ICO header reader; magic-bytes
upload validation in several places, explicitly chosen over Pillow each time), and
at roughly one original piece a month, an automated generator loses to a ten-minute
pass in a design tool on cost alone — the fixed cost of building and maintaining a
renderer (plus committing font files, since none of Outfit/DM Sans/Caveat/Permanent
Marker exist locally — this site loads all four from the Google Fonts CDN, which a
server-side renderer can't reach) never pays back at that cadence. This reasoning is
recorded here, not just the outcome, specifically so a future pass doesn't rebuild
Phase 2 without first re-checking whether either premise has actually changed.

**Element order and dimensions:**
1. Canvas: 1200×630px, `--bg` (warm off-white).
2. A coral left edge bar, full height — see §2.3's sanctioned exception for why two
   coral elements are allowed on this one template.
3. An uppercase coral eyebrow — the piece's `tag_label`, uppercased.
4. A large Outfit headline — the piece's `title`.
5. A DM Sans subhead — a one-line summary distinct from the page's own teaser/meta
   description field (see the field-mapping table below).
6. A muted footer line, static: `Brian Weisberg · bmweis.com`.

**String constraints — hand-enforced, not validated, because Phase 2 (the only thing
that could enforce them automatically) is killed:**
- Headline must fit one line at the template's specified size.
- Subhead: 50–66 characters, one line. The three cards built so far run 66, 60, and
  61 characters — treat 66 as the practical ceiling, not just the nominal one.
- Eyebrow is the piece's `tag_label`, uppercased — no separate copy to write.
- Footer is always exactly `Brian Weisberg · bmweis.com` — never per-card copy.

Because nothing renders or wraps these strings before they're baked into a PNG, a
subhead that runs long or a headline that wraps is a silent authoring mistake, not
a build failure — check both against the constraints above by eye before exporting
a new card, the same discipline the hand-built favicon files already require.

**Field mapping** (for the one page every card is generated against a real teaser
field, before the PNG is hand-built): eyebrow ← `tag_label`; headline ← `title`;
subhead ← a hand-trimmed one-liner, not necessarily identical to the page's own
`teaser`/description field — the live Growth Engine Ratio card's subhead ("A metric
for how R&D and go-to-market work together to drive growth.") is a deliberately
shortened, reworded version of that piece's actual `teaser`
("...GTM investments work together to drive growth—with an interactive
calculator."), picked for what reads well at card size, not copied verbatim.

### Editorial content system — Atlantic pattern

A long-form register for pages Brian wants to read like a considered piece rather
than a wall of prose: generous whitespace, pull-quotes, built for sitting with a
piece. (A shorter, scan-built "Axios" pattern — bold ledes, bullet-heavy — is a
separate, not-yet-built register for a different kind of page.)

**The tag:** `.article-atlantic`, applied alongside a page's width tier (e.g.
`class="page page-standard article-atlantic"`). It widens paragraph rhythm inside
`.tool-prose` (line-height 1.65 → 1.75, more paragraph spacing). **What it can't
do:** insert a pull-quote, decide where a callout goes, or write a subhead — those
are still hand-authored into the page's HTML. The tag is a typographic switch, not
an automated layout system.

**Shared devices:** `.article-pull` (quote), `.article-callout`/`.article-callout-title`
(tip), `.article-warn`/`.article-warn-title` (warning), `.article-cta` (CTA) — one
shared base each, so a new Atlantic page composes them directly with no per-page
override needed.

**The break rule:** an Atlantic-tagged page shouldn't run more than ~250 words
(roughly 5–6 lines at this measure) of unbroken paragraph text before a subhead,
pull-quote, callout, bulleted list, flowchart, or image. A bulleted list and a
simple flowchart are legitimate break devices in their own right, same standing as
a quote or callout — don't force content that's actually a process or a set of
parallel items into quote styling just to satisfy the rule.

**Images:** no reusable in-body image component exists yet. `/about`'s hand-coded
2-column photo grid with caption is the only precedent — worth generalizing into a
shared helper the first time an Atlantic page actually needs one.

#### Callout taxonomy

Every box on an Atlantic page is one of exactly four types, distinguished by color
and treatment so each reads as what it is:

| Type | Class | Color | Treatment | Width |
|---|---|---|---|---|
| **CTA** | `.article-cta` | Navy (`--navy-wash` fill, `--navy` left border) | Boxed, `border-radius:0 10px 10px 0`, `padding:18px 22px` — "here's a link to follow" | Body-copy (760px, `.tool-prose`) |
| **Tips** | `.article-callout` | Seafoam (`--seafoam-wash` fill, `--seafoam-mid` top border) | Boxed, titled (`.article-callout-title`, uppercase seafoam-deep), `border-radius:0 0 10px 10px` — "here's a fact/technique" | Body-copy (760px) |
| **Warnings** | `.article-warn` | Alert red (`--alert-wash` fill, `--alert` top border) | Boxed, titled (`.article-warn-title`, uppercase `--alert`), `border-radius:0 0 10px 10px` — "here's a failure mode to avoid" | Body-copy (760px) |
| **Quotes** | `.article-pull` | No fill — `--navy` left border only | Unboxed: `font-family:var(--font-head)`, 600 weight, italic, 22px, `line-height:1.45` — "this is the idea," not a boxed fact. No surrounding quotation marks — the rule/type treatment already signals "this is a quote." | Body-copy (760px, `.tool-prose`) |

**Width is part of the taxonomy — and the rule is now "one shared left edge, no
exceptions" (revised 2026-09, PR 1 of the article-alignment pass).** Every element
on an article page — CTA/Tips/Warnings, Quotes, the back-link, and the body text
itself — renders at body-copy width so the whole page anchors on one left margin.
Quotes used to widen to 1040px (a middle ground between the 760px reading column
and `.tool-inner`'s full 1300px, via `left:50%`/`transform:translateX(-50%)` against
`width:calc(100vw - 48px)`), on the reasoning that a short quote reads as a
deliberate editorial moment when it's set wider than the paragraphs around it. In
practice that breakout put the quote at a different left AND right edge than every
other element on the page, and — combined with the back-link rendering outside
`.tool-prose` on two pages — meant an article page had no single edge to anchor on
at all: three or four different widths stacked on top of each other read as
scattered, not editorial. The breakout is removed outright, not left dormant —
`.article-pull` keeps its navy left-rule/italic treatment, just at the same width
as everything else.

**Informational asides that aren't warnings or quotable ideas** (e.g. a
per-person/per-account setup note) belong in the Tips family — don't invent a
fifth box style for something that's functionally a tip.

**Why Warnings uses `--alert`, not coral:** a Warning box is a status signal
("here's a failure mode"), and §2.2's rule is that status colors (`--good`/
`--caution`/`--alert`) are reserved for status, with coral reserved for
decorative highlights only. Warnings previously used coral, which required a
one-off exception to coral's "one per viewport" rule. Moving Warnings to
`--alert` removed the need for that exception: coral is now purely rare and
decorative everywhere on the site, with no carve-outs.

#### Quote vs. Tip — the test

A real pull-quote is a standalone declarative statement or insight — it reads
naturally as "this is the idea" on its own, out of context. It is *not*:
- A process/sequence → a flowchart + caption instead
- A list of steps or parallel items → a bulleted list instead
- A plain informational sentence that just happens to sound punchy → standard body
  text instead

Anything that needs structure (a list, multiple sentences of explanation) is a Tip
or a plain break device, even if it originated as a "pull-quote."

---

## 6. Do / Don't

| ✅ Do | 🚫 Don't |
|---|---|
| Let navy + off-white do most of the work | Reach for color to fill space |
| Use coral once per screen, as a pop | Spread coral across a layout |
| Keep status colors for status only | Use `--alert` red as a highlight, or coral as a *system* status/error color |
| Outfit for headings, DM Sans for body copy — every reading surface, both reader templates included, no exceptions | Introduce a serif or any other third content font, or put body copy in Outfit anywhere |
| One marker-underline, one or two stickers per page, in a header/hero or card corner | Repeat the graffiti kit decoratively, or put it on admin/data surfaces |
| Buttons navy or ghost | Make a coral button, or a seafoam button without the deep seafoam border (that form means admin-only) |

The one sanctioned exception to "buttons navy or ghost": Delete/Reject actions use the
status-red `#b91c1c`/`#fee2e2`/`#fca5a5` family (§5) — a destructive-action signal, the
same category as `--alert`, not a decorative color choice.

**Sanctioned status-color exception — glanceable health indicators:** any UI element built
specifically for instant pass/fail/warn recognition (test results, uptime/sync status, the
cookie-status panel, and similar future indicators) uses true stoplight colors instead of the
semantic `--good`/`--caution`/`--alert` tokens — a working-vs-broken signal needs to read
instantly, and navy (`--good`) is too close to the site's dominant color to register as a
status signal at a glance. Palette: `#15803D` green, `#b91c1c` red (reuse the existing
destructive-action red — don't introduce a second red), `#CA8A04` amber. Color lives on the
indicator itself (a dot, icon, or badge) only — accompanying state text stays in the page's
normal text color (`--ink-soft` or equivalent), never tinted, since tinting some state words
and not others (where one color fails text-contrast) reads as a bug. Register any new hex
introduced under this exception in `brand_check.py`'s allowlist in the same PR, or the build
fails as an off-palette leak rather than a sanctioned exception. Every other pass/warn/error
use case — banners, callout text, inline copy — stays on the semantic tokens; this exception
is scoped to glanceable indicator elements specifically, not status communication in general.

**Sanctioned coral exception — pending-count badges:** the admin hub's `.task-badge`/
`.task-badge-dot`/`.task-dot` notification badges (a numeric count, or a plain dot for an
all-or-none source — see `webapp/tasks.py`) use `var(--coral)`, not a semantic token — "something
here needs you" is attention/urgency, the same register coral's decorative "pop" use is already
sanctioned for, not a pass/fail/warn status the glanceable-indicators exception above covers.
Documented here (Phase 1c, 2026-08) rather than introduced: this usage already shipped with the
admin hub's original notification-badge build and has been live since. No new hex — `--coral` is
an existing token, so nothing to add to `brand_check.py`'s allowlist. Scoped narrowly to pending-
count/unread badges specifically; this doesn't reopen coral for other status use ("keep status
colors for status only," §6, still holds everywhere else).

---

## 7. Token reference (CSS variables)

<!-- BEGIN GENERATED TOKENS (scripts/generate_brand_docs.py) -->
> Generated from `webapp/app.py`'s `:root` block — don't hand-edit this table.
> To change the brand, edit the CSS, then run `python -m scripts.generate_brand_docs`.

```css
:root{
  /* Surfaces */
  --bg:#F5F4EF;            /* warm off-white page */
  --surface:#FFFFFF;       /* cards, inputs */
  --surface-2:#FAF9F4;     /* subtle alt panels, table stripes */
  /* Brand — Navy (primary, cool). deep · base · light · wash */
  --navy-deep:#001B4F;     /* button hover / depth */
  --navy:#002975;          /* primary base */
  --navy-light:#3F5C9A;    /* lighter navy — secondary accents, borders */
  --navy-wash:#EEF1F7;     /* soft navy fill — chip/ghost hovers */
  --accent:#002975;        /* legacy name now = navy (keeps old markup working) */
  --accent-light:#EEF1F7;  /* legacy name now = --navy-wash */
  /* Brand — Seafoam/Green (cool accent). deep · mid · base · wash */
  --seafoam-deep:#1F7A66;  /* deepest teal — text-capable on light (AA) */
  --seafoam-mid:#2E9C86;   /* mid teal — data-viz (legible as a fill/line) */
  --seafoam:#A3E5D4;       /* accent base (light mint) — tags, badges, underline */
  --seafoam-wash:#EAF7F2;  /* soft accent fill — calc readout, table accents */
  /* Brand — Coral (warm accent, rare). deep · base · light · wash */
  --coral-deep:#B14A30;    /* coral that must carry small text (AA on canvas) */
  --coral:#E8704F;         /* warm accent base — display pop, data-viz R&D series */
  --coral-light:#F4A98F;   /* lighter coral — soft highlights */
  --coral-wash:#FBEAE3;    /* soft coral fill — decorative highlight blocks (navy text) */
  /* Text */
  --ink:#1a1a1a;
  --ink-soft:#3a3833;
  --muted:#6F6A60;         /* warm mid-gray */
  /* Lines (warm-toned) */
  --line:#E4E0D6;
  --line-strong:#D6D1C4;
  --table-border:var(--navy-light); /* the one frame color every table uses; also the subheading band */
  /* Semantic — status only (GER calculator readout, form pass/fail, Warnings callouts) */
  --good:#002975; --caution:#9A6B12; --alert:#9E3B30;
  --alert-wash:#FBEEEC;    /* soft alert fill — Warnings callout background only */
  /* Type */
  --font-head:'Outfit',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-body:'DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
  /* Graffiti/street-art accent layer (BRAND.md §4) — sticker borders/shadows and
     the marker-underline only. Never a fill, never body/heading text. */
  --ink-graffiti:#0d0d0d;
  --font-sticker:'Caveat',cursive;
  /* Sitewide wordmark treatment — nav + footer "CFO Navigator"/logo mark only,
     never body or heading text. */
  --font-wordmark:'Permanent Marker',cursive;
}
```
<!-- END GENERATED TOKENS -->

This token block is wired into the live `_CSS` in `webapp/app.py`. Coral is in use on the GER charts
(the data-viz R&D series). The automated brand check in `tests/test_brand_standards.py` keeps the
palette and fonts honest — any new color or font outside this system fails CI (see §8).

---

## 8. Automated enforcement

Two test suites run on every push/PR via GitHub Actions (`.github/workflows/qa.yml`):
`tests/test_brand_standards.py` (visual) and `tests/test_voice_standards.py` (verbal — see §9).
`test_brand_standards.py` scans the rendered site (`webapp/app.py`, including the inline CSS, SVG
charts, and JS-built markup) and fails if new content drifts off-brand:

- **Fonts** — only Outfit and DM Sans for headings/body copy, Caveat (stickers only) and
  Permanent Marker (wordmark only) as their own narrow decorative exceptions, and
  system/generic fallbacks may appear. Source Serif 4 was retired sitewide (2026-09) and
  removed from the allowlist along with it — a reintroduction now fails this check like any
  other off-brand font. Inter, Lora, Arial, Helvetica, Times, Roboto, etc. are banned (this
  is the exact class of regression that slipped in before the refresh).
- **Colors** — every hex in the codebase must be a brand token (parsed from the `:root` above, so the
  palette is its single source of truth) or one of the explicitly-documented auxiliary colors
  (status/feedback, benchmark badges, chart tints, and three deliberate exceptions: destructive-action
  buttons, §5/§6; glanceable health indicators' stoplight colors, §6; and "Sail, don't row"'s realistic
  sky/water/skyline/boat illustration palette,
  which reads as an actual landscape rather than brand-token shading, confined entirely to that one
  game). A brand-new off-palette hex fails the build, forcing a deliberate choice: add it to the
  system or fix it.
- **Banned legacy colors** — the specific values purged in the refresh (old greens, the generic
  `#3b82f6`/`#10b981`/`#f4683b` data palette) can never reappear.
- **Token integrity** — the full token set (all three ramps + neutrals + semantic) must be present.
- **Coral discipline (PR 16, 2026-09)** — at most one coral *background* moment per public
  page, checked signed out via `webapp.app.coral_moment_problems()` (`tests/
  test_coral_discipline.py`, and a live "Coral discipline" row on `/admin/checks`). This is
  a best-effort, **not** an exhaustive proof, and says so plainly in its own output rather
  than overclaiming:
  - Renders every real public (non-`/admin`, no path-param) GET/HTML route, signed out only —
    a page that shows different content signed in (e.g. `/tools`' admin-only Reader card) is
    not re-checked in that state.
  - Counts coral **background** declarations inside actual rendered `style="..."`
    attributes only — never a `<style>` block's own CSS rules (which can declare a class no
    element on that particular render actually carries) and never a `<script>` block's JS
    template strings. A coral moment built purely through a CSS class with no inline style
    would not be caught.
  - `<head>` (the shared sitewide stylesheet, including the admin-only pending-count-badge
    exception below) and each page's own `<header>`/`<footer>` are excluded as chrome — only
    a page's own `<body>` content is scored.
  - Small coral-deep **text** (the separately-sanctioned "text-capable coral" use, e.g. the
    Original Content flagship cards' tag color) is deliberately not counted — it doesn't
    compete for the same rare-accent budget as a coral fill, and counting it would also flag
    the ordinary navy-text-on-coral-wash badge pairing this codebase already sanctions
    throughout.
  - Admin pages are out of scope entirely — see the sanctioned pending-count-badge exception
    below, which already puts more than one coral element on an admin screen.
- **Reading column holds its tables and grids (2026-10)** — §5's rule, enforced by
  `linklib.brand_check.reading_column_problems()`, with its own `/admin/checks` row and
  `tests/test_reading_column_rule.py`. A **source** scan, for the same reason as outbound
  links: rendering pages from inside `run_all()` re-enters it across threadpool threads. It
  flags a `<table>` or a `display:grid` element written in the same string literal as a
  `.tool-prose` div when it is a direct child of that div's container, or sits in an
  `overflow-x` wrapper that is. Run against the page source from before #583, it flags both
  the grid and the table on `/tools/fpa-buddy/how-it-works`, which proves the check
  works. The current page is clean: the check reports zero findings on `main`. It cannot see: blocks
  interpolated into the page (`{cards}`) or built across separate string literals; blocks
  built in JavaScript or stored in the database; or a table or grid behind any other
  wrapper. A styled full-width card next to prose (the Growth Engine Ratio calculator) is
  allowed on purpose, since telling it from an accidental one needs a rendered measurement.
- **Grid tracks around form controls are `minmax(0,...)` (2026-10)** — a bare `1fr` track
  has an implicit minimum of its cell's min-content, so one wide unbreakable child widens
  the track and pushes the grid past its container. Six admin grids carried it
  (`.tool-form-cols`, `.qe-row`, `.users-top-grid`, the Users add-member form, the Resources
  Coverage/Pricing pair, the Third-party Source/venue pair). Covered by
  `tests/test_grid_track_zero_minimum.py`, which injects a wide cell into the real page in
  Chromium. Native `type="date"` inputs also need `appearance:none` (see the overhead-spend
  rounds); `time`, `datetime-local`, `month` and `week` share that picker UI and would need
  the same, but none exists in the app today. `number` inputs do not, since their width is
  not driven by native picker chrome.
- **Outbound links open in a new tab (PR 35, 2026-09)** — §3.3's rule, enforced by
  `linklib.brand_check.outbound_link_problems()`, with its own `/admin/checks` row and
  `tests/test_outbound_links.py`. Deliberately a **source** scan rather than a rendered-page
  one, unlike coral discipline above: the failure mode is a hand-typed anchor in
  `webapp/app.py`, and scanning source covers admin pages too (which a signed-out
  rendered scan can't reach) at no render cost and with no re-entrancy hazard. It reads raw
  source rather than evaluated string values on purpose — an anchor is routinely split
  across adjacent Python string literals, and a value-based scan would see two fragments
  and flag the half without the `target` attribute. Like the coral check, it says what it
  can't see rather than overclaiming:
  - **Links built in JavaScript** (`'<a href="' + url + '">'`) have no literal href to
    classify, so they're invisible to this check — and to a rendered-DOM scan too, unless
    that JS had actually run.
  - **Links inside stored database content** — an admin's saved `/how-this-is-built` copy,
    `original_content.body_md`, AI-drafted tool/community fields, user-submitted text — are
    data, not source. They're edited through the admin UI rather than in a PR, so a source
    lint can't reach them. The five `_HTIB_*_DEFAULT` constants **are** scanned; an override
    saved over one of them is not.
  - **Markdown links** (`[text](https://…)`) can't carry `target` at all, so they're a
    latent violation by construction — which is why §3.3 says to write outbound links in
    prose as raw `<a>` tags.
- **Icon fill contract (PR 18, 2026-09)** — a plain stroke-path `_ICON_*` icon inherits its
  color from whatever badge the position-based seafoam/navy cycle (`_CARD_ICON_STYLES`)
  assigns it, so cycling the array is safe by construction. An icon with its own hardcoded
  `fill="#..."` (e.g. `_ICON_HALF_CIRCLE`) opts out of that and is only safe when its badge
  index is pinned to the position whose cycle color actually matches the hardcoded fill —
  the exact near-miss PR 16 fixed by hand (dropping coral from a 3-color to a 2-color cycle
  silently moved that icon's badge from seafoam to navy). `linklib.brand_check.
  icon_fill_contract_problems()` (folded into `findings()`, so it's part of the same "Brand
  standards" check/test) now catches this mechanically: any `_ICON_*` constant with a
  hardcoded fill must have an entry in `ICON_FILL_CONTRACTS` (icon name -> required href +
  badge index), and that pin must actually match what `_TOOLBOX_BADGE_INDEX` resolves for
  that href — a stale/mistuned pin is flagged the same as a missing one.

To run locally: `pip install -r requirements-dev.txt && pytest -q`.

When you intentionally introduce a new color (e.g. a new chart series or status state), add it to the
relevant group in `AUX_COLORS` in `linklib/brand_check.py` (the single source of truth for the rules,
also surfaced live on the **Checks** admin page) with a comment — that's the moment the decision gets
recorded, which is the point.

---

## 9. Verbal identity — voice

The brand is verbal as well as visual. The full voice guide (lead with the point, specific over
abstract, first-person proof, earned metaphors, the LinkedIn shape, and the hard mechanical rules) is
the editable **Voice guide** on `/admin/voice` — it's both what Claude uses to draft posts and the
rubric the voice check holds new writing to.

`/admin/voice` edits three separate, independently-customizable layers, each falling back to a
built-in default when empty: **Voice core** (the shared mechanics/tone above, doubling as the
"General / site copy" rubric), **FP&A Buddy voice** (appended after the core for FP&A Buddy's
third-person, cite-or-name-the-gap register), and **Chat Matchmaker voice** (appended after the
core for the Communities and Software matchmakers — first person plural, references what the
visitor said, no invented experience with any listed community or vendor). The **Check content
against your voice** rubric picker on the same page mirrors this three-way split.

Like color, voice has two kinds of rules:

- **Mechanical** (deterministic) — banned buzzwords (*delve, robust, seamless, synergy, transformative,
  game-changer*), filler (*"at the end of the day", "in order to", "needless to say", "there are many
  factors to consider"*), and performative openers/closers (*"thrilled to", "Onward!", "excited for
  what's next"*). The rules live in `linklib/voice_review.py` (`BANNED_WORDS` / `FILLER_PHRASES` /
  `PERFORMATIVE`) as the single source of truth, **permanently** — decided and closed (2026-09
  voice-enforcement PR): a DB-editable copy of these lists is what would let `voice_review.py`
  contradict the rest of the voice guide, since a committed CI mirror can drift from a live settings
  row and nothing in the running Railway container can push that drift back to git. `/admin/voice`
  mirrors all three lists read-only, marked "changing them is a code change," so the full voice picture
  — editable prose rubric, mechanical enforcement — lives on one page even though only one half is
  DB-backed. Context-dependent words (*leverage* the noun, *actually*/*honestly* as filler) are left to
  the holistic review to avoid false positives.
  - **`tests/test_voice_standards.py`** runs `mechanical_findings()` directly (not a separate
    reimplementation — see that test file's own note on why `_hits()` was retired) against
    `VOICE_SCANNED_FILES` (`webapp/app.py`, `linklib/enrich.py`, `linklib/feature_scan.py`) and fails
    the build on a hit. A rubric line that cites a banned word as a "don't write this" example (e.g.
    `enrich.py`'s "No marketing language: no 'powerful,' 'seamless,' …") is masked before scanning —
    see `voice_review._mask_rubric_enumerations` — so stating the rule isn't itself a violation of it.
  - **Semantic contradiction, checked separately**: does `VOICE_CORE_DEFAULT`'s own prose quote a
    word/phrase as unwanted that the mechanical lists don't actually enforce? `voice_review.
    voice_core_gap_problems` extracts every 2+-word quoted phrase and confirms each is really covered
    (one direction only — a list entry the prose doesn't mention is fine). Source-only, same as the
    lists themselves — an admin edit to the *live* `voice_core` setting isn't checked by this, only
    the code default.
  - **Database content is scanned too, but only live, never in CI** — `/admin/checks`' "Database-backed
    copy" section (`linklib.voice_db_scan.scan_db_copy`) runs both mechanical and typography checks
    against every DB column confirmed to render on a public page (original content, tool/community
    profiles, saved homepage/about overrides, and more — see that module's own file for the exact
    list). It reports; it never rewrites — any fix is an ordinary editorial change through whichever
    admin page owns the record, reviewed by Brian before it ships, same as any other copy edit.
    `category_features.definition`/`pointer_note` ARE scanned as of the 2026-09 voice-review-queue
    PR — they don't render on any public page (the "Key features" card's own SQL doesn't even select
    them), but `_voice_fix` already normalizes both at write time, so the scanner needs to be able to
    catch the same thing the write-time corrector fixes.
  - **A finding is a review-queue row, not a silently-applied correction or a bare count** — every
    `_voice_fix` correction and every scanner finding lands in `voice_review_queue`
    (`linklib/db.py`), reviewable at `/admin/voice/review-queue` (Accept/Revert/Edit/"Accept as
    exception," grouped by rule). Asynchronous by design, not a save-time blocking gate. Row-scoped
    exceptions here (`Library.is_voice_exception`) are a separate, visibly distinct mechanism from
    the global, source-side `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS` allowlists above — accepting one
    specific record's exception never changes a global rule. See CLAUDE.md's "Voice review queue"
    bullet for the full write-up, including the disclosed scope cut on which write paths are
    instrumented so far.
- **Tone** (judgment) — "does this sound like me." Reviewed on demand by Claude, never in CI (it costs
  API and isn't deterministic). Use the **Check content against your voice** box on `/admin/voice`, or
  the CLI: `python -m scripts.voice_review draft.md` (reads a file or stdin; exits non-zero on any
  mechanical violation, so it can gate a pre-publish script).

**Dash rules — two distinct characters, two distinct jobs.** Don't conflate these into one "any
dash" policy:

- **Em dash (—)** is sentence-level punctuation — an aside, or a punchy two-part close. Always
  unspaced (`point—not like this`, never `point — not like this`), used sparingly. New em dashes
  in fresh copy get flagged to Brian with full sentence context before shipping — see CLAUDE.md's
  "Voice — em dash policy" for the complete rule and the flagging workflow. The *unspaced* half is
  mechanically enforced as of PR 9 (2026-09), widened in the 2026-09 voice-enforcement PR:
  `linklib.voice_review.typography_findings` fails CI on a spaced em dash across `VOICE_SCANNED_FILES`
  (`webapp/app.py`, `linklib/enrich.py`, `linklib/feature_scan.py`), in both its literal and `&mdash;`
  spellings — plus, live only (never CI), across the DB-backed copy columns `/admin/checks`' "Database-
  backed copy" section scans. The flagging workflow still applies to every new em dash, spaced or not
  — the lint checks typography, not cadence.
- **En dash (–)** is a numeric-range separator — `$1.10–$1.30`, `Q3–Q4`, `1–10 employees`. Also
  always unspaced, same discipline as the em dash, but it's a different character doing a
  different job (a range, not a sentence-level pause), not a second flavor of em dash. No flagging
  workflow needed for en dashes — a spaced one is a straightforward typo to fix on sight, since
  there's no voice/tone judgment call involved, just a typographic convention.

**Ampersands.** Spell out "and" in UI copy. The exception is a standard finance or business
abbreviation that carries `&` as part of the term itself (FP&A, R&D, Q&A, P&L, M&A, S&P, S&M,
D&A, T&E) or a proper name that genuinely contains one (Sales & Marketing and Research &
Development as GAAP line items; a real company name like Bain & Company). Also mechanically
enforced as of PR 9 — same `typography_findings` check, same allowlists, both living in
`linklib/voice_review.py`. CI scans Python source only — never database content, which has no
route from a GitHub Actions run (see the "Mechanical" bullet above). The live "Database-backed
copy" scan on `/admin/checks` reaches DB content too, but deliberately exempts `tools.name`/
`communities.name`/`benchmarks.name` from this specific rule: a real vendor/community/resource
name can legitimately contain an ampersand (Bain & Company), same reasoning as the source-side
exception — confirmed to reproduce for DB content, not just assumed, before that exemption was
added. Every other scanned column, including Brian's own curated `category_features.name`/
`tool_categories.name`/`community_categories.name`, stays in scope.

**Standing disclosure lines.** A few claims on the site carry enough legal/editorial weight that
they get a short, muted, footnote-style line placed right next to the claim rather than folded
into the surrounding copy or buried on a separate page — small type, `var(--muted)` color, a
border-top separator, positioned adjacent to what it's disclosing. Two so far, both on Software
(and eventually Communities) profile pages:

- **Advisor disclosure** (`tp-footnote`, page-level, shown when `tool.advisor`/`community.advisor`
  is set): "Brian is a formal advisor to [name]. Advisor relationships are always disclosed and
  never affect ranking or inclusion." A conflict-of-interest disclosure.
- **Features sourcing disclaimer** (`tp-footnote`, directly under the Features card,
  `_FEATURES_SOURCING_DISCLAIMER` in `webapp/app.py`): features and capabilities are drawn from
  public company websites and marketing materials, not independently tested or confirmed true. A
  claims-accuracy disclosure — distinct from the per-feature "verify" tag, which only confirms the
  feature *text* matches what the vendor's site said (extraction accuracy), not that the
  underlying capability is real. New disclosure lines in this family should draft carefully to
  keep that distinction (or whatever adjacent distinction applies) legible rather than
  contradicting an existing badge's meaning, and should get the same "show the wording before it
  ships" treatment as any neutrality-sensitive copy.
