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
- More than ~one coral element per viewport — if you see two, remove one

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

Three content families, one combined Google Fonts import in `<head>`. No serif anywhere
except the reader. **Caveat** and **Permanent Marker** are accent-only fonts — approved
dependency exceptions for the graffiti refresh — each restricted to one specific use
and never used for headings or body copy (that's still Outfit/DM Sans/Source Serif 4,
unchanged).

| Family | Role | Weights |
|---|---|---|
| **Outfit** | Headings, display | 600 / 700 |
| **DM Sans** | Body copy, UI, labels, eyebrows | 400 / 500 / 600 |
| **Source Serif 4** | Long-form reading (`/read` only) | 400 / 500 / 600 |
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
| Reader body | Source Serif 4 | 18px / 1.75 | 400 | — |
| Reader chrome | DM Sans | 13–14px | 400–500 | — |

**Rules of thumb:** headings are tight (negative tracking) and Outfit; eyebrows are uppercase DM Sans
with wide tracking and `--muted` or `--navy`; the serif is *exclusively* for reading long articles.

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
   3-up rows. Fixed background order: seafoam-wash `#EAF7F2` → navy-wash `#EEF1F7` →
   coral-wash `#FBEAE3`, cycling in that order regardless of column count and reused
   for every card-row grid sitewide. Helper: `_card_icon()` in `webapp/app.py`.

4. **Spray-tag wordmark** — the sitewide "CFO Navigator"/logo mark, nav header and
   navy footer alike, renders in Permanent Marker instead of Outfit. Color unchanged
   (navy in the nav, white in the footer) — only the font swaps. No rotation, no
   halo, no separate decorative stamp elsewhere on the page; this reskins the
   wordmark text in place, sitewide, every page and breakpoint.

**New tokens** (see §7 for the generated block): `--ink-graffiti:#0d0d0d` (sticker
borders/shadows only — never a fill or text color elsewhere), `--font-sticker:
'Caveat',cursive` (sticker-only), and `--font-wordmark:'Permanent Marker',cursive`
(wordmark-only).

That's the entire graffiti vocabulary. No broader illustration style, no all-over
pattern, no graffiti marks on admin tables, forms, or the chat UI.

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
- **Admin section headings** — an informal sub-heading role used to break up an admin page into
  named sections (e.g. "Pending submissions" / "Approved software" on the Toolbox review pages,
  "AI research" / "Screenshot" / "Competitors" / "Features" on the tool-edit page, dependency-group
  titles on `/admin/open-source`). 16px, 600 weight — smaller and lighter than the base `h2` (21px/600),
  since these mark subsections within a page rather than the page's own top-level sections.
- **Cards** — white surface, `--line` border, radius 12–16px.
- **Card titles** — Outfit, 17px, 600 weight, `-0.01em` tracking, `--ink`. One standard across
  every card family sitewide: the Software/Community/Benchmark directory cards, thought-leadership
  landing cards, case-study cards, and the archive/feed cards all converged to this single value in
  the brand audit (previously split across three sizes and three weights with no shared standard).
  The one confirmed exception is `.sdr-outcome-title` (Sail Don't Row's game-over overlay,
  800 weight) — deliberately bolder, verified side-by-side against the 600-weight standard and kept
  distinct because it's a single bespoke result overlay, not a family of cards sharing a role.
- **Disclosure/accordion** — two variants. *Group-level* (top-level Admin sections, e.g. `/admin`
  index groups, `/admin/library` quadrants): bordered/boxed row, bold all-caps label + muted item
  count left-aligned, arrow right-aligned, points right collapsed / down expanded. *Item-level*
  (nested toggles within a section, e.g. capture-method instructions): bordered box, arrow
  left-aligned before the label. Don't invent a third variant — pick group or item based on
  hierarchy depth. The group-level row is one shared component, `_disclosure_group` in
  `webapp/app.py`; reuse it rather than rebuilding the markup.
- **Tables** — navy header row with white text; alt rows `--surface-2`.
  Checkbox/boolean-indicator columns are always center-justified, header and
  cells alike. Text, link, and dropdown columns are left-justified. Actions
  columns are right-aligned. Once a table collapses to stacked labelled rows on
  mobile the centring is dropped, since there are no columns left to align
  within.
- **Links** — navy; optional seafoam underline for emphasis in editorial copy.

### Radius scale
`10px` buttons & inputs · `12–16px` cards & panels · `6px` tags/chips & inline row-action buttons ·
`999px` filter pills.

### Layout

Four width tiers, keyed to content shape rather than one global reading measure.
Fully migrated as of the Phase 9 route sweep — every page-rendering route carries one
of the tiers below; the old three-tier system (`.page` 780px / `.page-narrow`
480px / `.page-wide` 960px) is retired and those two classes no longer exist in the
CSS. (The old `.page` measure had drifted from its documented values before this
system landed — the GER calculator was never actually 820px, it used plain 780px;
860px belonged to `/library/feed`, not the Toolbox grid; both since corrected.)

| Tier | CSS class | Width | Pages |
|---|---|---|---|
| Full-width content | `.page-full` | ~1800–2000px | Homepage/About, Thought Leadership landing (+ its 3 long-form articles), Library landing (+ past questions, ask history), article reader (`/read`), CFO Toolbox community profile pages, FP&A Buddy chat, Growth Engine Ratio calculator, Sail Don't Row (+ its leaderboard) |
| Card grids | `.page-grid` | ~1200–1400px | CFO Toolbox landing + Software directory, Benchmarks directory, Communities directory (+ compare, find-results), `/admin/open-source` |
| Forms | `.page-form` | ~600–700px | Contact, login/forgot/reset-password, Privacy, all member-submission forms (library/tool/community submit), admin single-record add/edit forms |
| Admin data tables | `.page-admin` | ~1400–1600px | All remaining `/admin/*` list, dashboard, and report pages |

Combine with `.page` for its margin/padding (e.g. `class="page page-full"`). Generous
page padding (≈48px top). Whitespace before density. Admin/data pages get the width
bump for scannability, not decoration — they never get any part of the graffiti layer
(§4). Two pages (`/library/archive`, `/library/feed`) use a bespoke full-bleed layout
outside the `.page` system entirely and aren't part of this tier table — their own
internal content widths (960px and 860px respectively) were left alone or adjusted in
place rather than forced into a tier that doesn't fit their structure.

The former `.page-tool` tier (960px, "functional tools") was retired in Phase 9b —
those pages (FP&A Buddy, GER calculator, Sail Don't Row + leaderboard) now sit on
`.page-full` like every other content page, so they no longer feel visually cramped
next to it. Each wraps its actual working content (chat, calculator, game canvas) in
`.tool-inner` (1300px, centered, card-grid scale) so the widget gets real room instead
of the old 960px box. The Growth Engine Ratio's long-form paragraphs nest a narrower
`.tool-prose` (760px) inside that wrapper — 1300px is too wide a text measure to read
comfortably, but the calculator itself benefits from the extra width.

`.tool-prose` isn't limited to `.tool-inner` — it's a general-purpose narrow-reading
wrapper (max-width 760px, centered) usable inside any wider tier. The three
`/admin/system/*` reference pages (Database, Page Index, How FP&A Buddy works) reuse it
directly inside `.page-admin` (1500px): each nests its intro copy and any prose-only
section in `.tool-prose`, while diagrams and tables stay at the full `page-admin` width
so they don't get squeezed into a 760px column meant for reading text.

The brand audit's Phase 4 also found four pages with *no* reading-width constraint at
all — AI Hackathon Playbook, Connecting Claude to NetSuite, `/ask/history`, and
`/library/past-questions` — rendering body copy at the full `page-full` measure
(~1850px). A brief attempt at a new sitewide 1500px prose ceiling was tried and reverted
(too wide for comfortable reading, outside the usual 60–75-character-per-line
guidance); the interim fix is the same `.tool-prose` (760px) wrapper already proven on
GER and the SYSTEM pages, applied to those four as well.

### Editorial content system — Atlantic pattern

A long-form register for pages Brian wants to read like a considered piece rather
than a wall of prose: generous whitespace, pull-quotes, built for sitting with a
piece. (A shorter, scan-built "Axios" pattern — bold ledes, bullet-heavy — is a
separate, not-yet-built register for a different kind of page.)

**The tag:** `.article-atlantic`, applied alongside a page's width tier (e.g.
`class="page page-full article-atlantic"`). It widens paragraph rhythm inside
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
| **Quotes** | `.article-pull` | No fill — `--navy` left border only | Unboxed: `font-family:var(--font-head)`, 600 weight, italic, 22px, `line-height:1.45` — "this is the idea," not a boxed fact. No surrounding quotation marks — the rule/type treatment already signals "this is a quote." | Wider breakout (1040px, centered under `.tool-inner` via `left:50%`/`transform:translateX(-50%)` against `width:calc(100vw - 48px)`) |

**Width is part of the taxonomy.** CTA/Tips/Warnings stay at body-copy width —
they're mostly multi-line instructional prose, and a wide box reads as an odd
second column, not a design choice. Quotes are short by nature, so widening them
(1040px — a middle ground between the 760px reading column and `.tool-inner`'s full
1300px) reads as a deliberate editorial moment instead.

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
| Headings in Outfit, reading in Source Serif 4 | Mix the serif into UI, or set body in Outfit |
| One marker-underline, one or two stickers per page, in a header/hero or card corner | Repeat the graffiti kit decoratively, or put it on admin/data surfaces |
| Buttons navy or ghost | Make a seafoam or coral button |

The one sanctioned exception to "buttons navy or ghost": Delete/Reject actions use the
status-red `#b91c1c`/`#fee2e2`/`#fca5a5` family (§5) — a destructive-action signal, the
same category as `--alert`, not a decorative color choice.

One sanctioned status-color exception: the cookie-status panel on `/admin/library/feeds`
uses true stoplight colors (`#15803D` green / `#b91c1c` red / `#CA8A04` amber) instead of
the semantic `--good`/`--caution`/`--alert` tokens — a working-vs-broken health check needs
to read instantly, and navy (`--good`) is too close to the site's dominant color to register
as a status signal at a glance. Scoped to that one panel; every other pass/warn/error use
case stays on the semantic tokens. The red is the same `#b91c1c` as destructive actions
rather than a second red, so "red = bad" is one value sitewide. **The amber is dot-only:**
`#CA8A04` on white measures 2.94:1, below AA for text, so the state word beside it stays in
`--ink-soft` and the color lives on the indicator dot alone.

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

- **Fonts** — only Outfit, DM Sans, Source Serif 4, and system/generic fallbacks may appear. Inter,
  Lora, Arial, Helvetica, Times, Roboto, etc. are banned (this is the exact class of regression that
  slipped in before the refresh).
- **Colors** — every hex in the codebase must be a brand token (parsed from the `:root` above, so the
  palette is its single source of truth) or one of the explicitly-documented auxiliary colors
  (status/feedback, benchmark badges, chart tints, and three deliberate exceptions: destructive-action
  buttons, §5/§6; the cookie-status stoplight, §6; and "Sail, Don't Row"'s realistic
  sky/water/skyline/boat illustration palette,
  which reads as an actual landscape rather than brand-token shading, confined entirely to that one
  game). A brand-new off-palette hex fails the build, forcing a deliberate choice: add it to the
  system or fix it.
- **Banned legacy colors** — the specific values purged in the refresh (old greens, the generic
  `#3b82f6`/`#10b981`/`#f4683b` data palette) can never reappear.
- **Token integrity** — the full token set (all three ramps + neutrals + semantic) must be present.

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
  game-changer*), filler (*"at the end of the day", "in order to", "needless to say"*), and performative
  openers/closers (*"thrilled to", "Onward!", "excited for what's next"*). `tests/test_voice_standards.py`
  scans the site copy in `webapp/app.py` for these and fails the build on a hit. The rules live in
  `linklib/voice_review.py` (`BANNED_WORDS` / `FILLER_PHRASES` / `PERFORMATIVE`) as the single source
  of truth. Context-dependent words (*leverage* the noun, *actually*/*honestly* as filler) are left to
  the holistic review to avoid false positives.
- **Tone** (judgment) — "does this sound like me." Reviewed on demand by Claude, never in CI (it costs
  API and isn't deterministic). Use the **Check content against your voice** box on `/admin/voice`, or
  the CLI: `python -m scripts.voice_review draft.md` (reads a file or stdin; exits non-zero on any
  mechanical violation, so it can gate a pre-publish script).

**Dash rules — two distinct characters, two distinct jobs.** Don't conflate these into one "any
dash" policy:

- **Em dash (—)** is sentence-level punctuation — an aside, or a punchy two-part close. Always
  unspaced (`point—not like this`, never `point — not like this`), used sparingly. New em dashes
  in fresh copy get flagged to Brian with full sentence context before shipping — see CLAUDE.md's
  "Voice — em dash policy" for the complete rule and the flagging workflow.
- **En dash (–)** is a numeric-range separator — `$0.50–$0.70`, `Q3–Q4`, `1–10 employees`. Also
  always unspaced, same discipline as the em dash, but it's a different character doing a
  different job (a range, not a sentence-level pause), not a second flavor of em dash. No flagging
  workflow needed for en dashes — a spaced one is a straightforward typo to fix on sight, since
  there's no voice/tone judgment call involved, just a typographic convention.

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
