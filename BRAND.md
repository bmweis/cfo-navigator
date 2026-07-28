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
| `--coral-wash` | `#FBEAE3` | fill | Soft fill — callout blocks (put **navy** text on it) |

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
| `--good` / `--caution` / `--alert` | `#002975` / `#9A6B12` / `#9E3B30` | **Status only** — GER tiers, form errors |

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
- A coral-wash (`#FBEAE3`) **warning box** (`.article-warn` — see "Callout taxonomy" below)
  — put **navy** text on it (11:1 contrast)

**🚫 Never coral**
- Body text or any text under ~18px (use `--coral-deep` only if unavoidable)
- Button fills (buttons are navy or ghost-navy — *no* color buttons, ever)
- Status/error states (that's `--alert`)
- More than ~one coral element per viewport — if you see two, remove one, **except**
  `.article-warn` warning boxes (see the "Callout taxonomy" entry below): a long-form
  page can legitimately need more than one warning called out in the same view (e.g.
  Connecting Claude to NetSuite's setup guide has a warning at the top of "The security
  architecture" plus one nested in each of two later setup steps), and diluting that to
  "one warning per page" would mean either merging unrelated warnings together or
  demoting real ones to plain text. This is a **deliberate, sanctioned exception** to the
  one-per-viewport rule — specific to warning boxes, not a general loosening of the coral
  rule. Everything else on this list (badges, underlines, data-viz, display numerals,
  the coral-wash callout/quote block) still holds to one-per-viewport.

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
| Navy text on seafoam-wash / coral-wash | 11:1 | ✅ Anything — preferred for tinted callouts |
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
- **Tables** — navy header row with white text; alt rows `--surface-2`.
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

### Editorial content system (Phase 6) — Atlantic tag

Phase 6a investigated two long-form registers Brian wants available — a short-form
"Axios" pattern (bold ledes, bullet-heavy, built for scanning) and a long-form
"Atlantic" pattern (generous whitespace, pull-quotes, built for sitting with a piece).
Phase 6b built the long-form half and piloted it on AI Hackathon Playbook; the
short-form pattern is a separate, later phase.

**Shared article devices** (`.article-pull`, `.article-callout`/`.article-callout-title`,
`.article-warn`/`.article-warn-title`) are the pull-quote/callout/warning box trio,
extracted in Phase 6b from AI Hackathon Playbook's and Connecting Claude to NetSuite's
previously-duplicated `.fah-*`/`.ns-*` copies — those two pages' CSS was pixel-for-pixel
identical except for a handful of small spacing/font-size values, so each page still
composes the shared base class with its own small override class for just what
differs (e.g. `class="article-pull fah-pull"`), keeping both pages' rendering
unchanged from before the extraction. A new long-form page can use the shared classes
alone, no override needed.

**The Atlantic tag** (`.article-atlantic`, applied alongside the page's width tier,
e.g. `class="page page-full article-atlantic"`) is the tagging mechanism — the
simplest viable option identified in Phase 6a: a second CSS class, not a new Python
abstraction. It widens paragraph rhythm inside `.tool-prose` (line-height 1.65 → 1.75,
more paragraph spacing) for a more unhurried reading feel. **What it can't do:** insert
a pull-quote, decide where a callout goes, or write a subhead — those are still
hand-authored into the page's HTML regardless of the tag. The tag is a typographic
switch and a documentation signal, not enforcement; see `EDITORIAL_SYSTEM_PHASE6A.md`
for the full reasoning.

**The break rule:** an Atlantic-tagged page shouldn't run more than ~250 words
(roughly 5-6 lines at this measure) of unbroken paragraph text before a subhead,
pull-quote, callout, or image. AI Hackathon Playbook was audited word-by-word against
this rule during the Phase 6b pilot — its longest unbroken stretch anywhere is ~144
words, comfortably under the threshold, using only its existing pull-quotes/callouts
repositioned via the shared classes above. No new copy or images were needed for this
page to satisfy the rule.

**Images:** no reusable in-body image component exists yet. The only precedent is
`/about`'s hand-coded 2-column photo grid with caption — worth generalizing into a
shared helper whenever an Atlantic-tagged page actually needs one, per
`EDITORIAL_SYSTEM_PHASE6A.md` §2/§4. Not built in Phase 6b since Hackathon Playbook's
pilot didn't need one to satisfy the break rule.

**Phase 6c — does Atlantic work on technical content?** Hackathon Playbook is a
narrative essay; Connecting Claude to NetSuite is a technical setup guide (numbered
steps, a permissions table, a troubleshooting table) that doesn't naturally produce
quotable prose the way an essay does. The tag was applied anyway to test the
hypothesis that Atlantic's *typography* (wider rhythm, generous whitespace) can still
suit technical content even where its signature device (the pull-quote) mostly can't.

The break rule needed nothing new: NetSuite MCP's existing subheads, use-case cards,
numbered step tracks, callouts, and tables already keep every unbroken stretch under
~106 words — well inside the ~250-word threshold — with zero new copy. One genuine
pull-quote candidate did turn up in the existing prose ("The protection is enforced by
NetSuite, not by hoping Claude behaves.") and was repositioned (not rewritten) out of
its paragraph into a `.article-pull`; two other candidate sentences were considered and
rejected as too tied to their surrounding instructional context to stand alone. The
numbered setup-step prose (Parts 1-2) is deliberately pull-quote-free — procedural
"go here, click this" instructions don't compress into standalone insights, and
forcing one would read as decorative rather than earned.

**Verdict:** the hypothesis held. Atlantic's typography reads well on technical
content on its own — the wider rhythm doesn't fight a setup guide's structure, since
the guide's density comes from its instructional steps and tables, not from paragraph
length. The one genuine pull-quote is a nice addition, not a rescue; the piece would
still read fine without it. This is a different result from a narrative essay, where
pull-quotes are doing real structural work breaking up long prose (Hackathon
Playbook) — on technical content they're closer to an occasional accent than a load-bearing device.

**Correction (post-taxonomy):** on review, "The protection is enforced by NetSuite, not
by hoping Claude behaves" turned out to be a plain declarative sentence, not a quotable
idea — it restates the preceding paragraph's point rather than compressing it into
something a reader would repeat. It's been moved back into that paragraph as standard
body text. Per the "it's a nice addition, not a rescue" verdict above, removing it
doesn't open a break-rule gap: the surrounding stretch (H2 to the next warning box) is
106 words, unchanged from the count already documented above, comfortably under the
~250-word threshold. This is the Quote-vs-Tip judgment call (see the Callout taxonomy
entry below) applied a third way: some sentences that read as quotable in isolation
turn out, on a second look, to just be text.

**Phase 6d — does Atlantic coexist with an already-narrowed page, and can it carry
new content, not just typography?** Growth Engine Ratio (GER) is a different shape
again: an interactive calculator with prose woven around it, and its reading width
was already narrowed to `.tool-prose` (760px) inside the wider `.tool-inner` (1300px)
in an earlier fix, deliberately — not something to casually undo. `.article-atlantic`'s
only CSS effect is `.article-atlantic .tool-prose p{line-height:1.75;margin-bottom:22px;}`,
scoped to paragraphs nested inside `.tool-prose`. GER already wraps its two prose
sections (before and after the calculator) in separate `.tool-prose` blocks, with the
calculator itself sitting outside `.tool-prose` in between — so tagging the outer
`.page` wrapper widens rhythm in both prose sections automatically while leaving the
calculator's markup, inputs, and JS completely untouched. No restructuring of
`.tool-inner`/`.tool-prose` was needed; they coexist with Atlantic exactly as designed.

Unlike 6b/6c, this pilot wasn't purely a typography-and-break-rule exercise — it
paired the tag with real content updates: a contributor attribution line (Katherine
Zhang, CEO of OPEXEngine by Bain & Company, credited by name for the first time), a
short teaser naming two of the benchmark's actual companies (Reddit at $2.94,
Palantir at $2.04) to reinforce the existing download CTA without reproducing the
guide's full table, and three pull-quotes Brian selected directly from the published
whitepaper, placed verbatim at the points in the prose they contextualize (the
efficiency-disaster line illustrating GTM/R&D misalignment, the outlier/network-effects
line following the two companies that cleared $1.00, and the permanent-capital-loss
line following the churn discussion). GER's existing structure (subheads, the formula
block, the tier table) already kept every unbroken stretch well under the ~250-word
threshold before any of this was added, so the new devices layer on top of an
already-compliant page rather than fixing a gap.

**Verdict:** Atlantic and a narrowed reading width aren't in tension — the tag's
narrow CSS footprint (a `.tool-prose p` selector only) means it composes with any
width tier a page already uses, including one that deliberately narrowed its width
for a documented reason earlier; that earlier fix was worth verifying, not assuming
safe. This phase also showed Atlantic pages absorbing curated external content
(attribution, named benchmark data, verbatim quotes from a source document) as
cleanly as originally-authored prose.

**Full-width breakout (Phase 6d addendum):** GER's three pull-quotes break out of
the 760px `.tool-prose` reading column to span the full 1300px `.tool-inner` width,
rather than sitting inline at the narrower text measure — body paragraphs stay at
the comfortable reading width, only the pull-quote/callout/stat-block devices break
out wider. The CSS (a page-scoped `.ger-pull` modifier, not a change to the shared
`.article-pull` base) uses `left:50%` plus `transform:translateX(-50%)` against a
`width:calc(100vw - 48px)` capped at `max-width:1300px` — since `.tool-prose`,
`.tool-inner`, and `.page` are all centered on the same axis with a fixed 48px total
side padding, this re-centers the wider box under `.tool-inner` at any viewport size
and collapses cleanly to the same width as the surrounding prose once the viewport is
too narrow to have room to break out (verified at 375/900/1400/2400px viewports, no
horizontal overflow at any of them). This breakout treatment is scoped to GER's three
Phase 6d pull-quotes only — it did not touch the shared `.article-pull` base or the
existing inline pull-quotes on Hackathon Playbook or NetSuite MCP. (A later retrofit
phase brought the same breakout treatment to Hackathon Playbook and NetSuite MCP's
existing boxes too — see the PR history; this entry describes only what Phase 6d
itself did.)

**Callout taxonomy:** comparing GER's new pull-quotes against Hackathon Playbook's
existing one surfaced a problem — every box on an Atlantic page (CTAs, tips, warnings,
quotes) reused the same generic pale-box treatment, so a short pull-quote read as
awkward empty space rather than an intentional design moment. Every box on an
Atlantic page is now one of exactly four types, distinguished by color and treatment
so each reads as what it is:

| Type | Class | Color | Treatment | Width |
|---|---|---|---|---|
| **CTA** | `.article-cta` | Navy (`--navy-wash` fill, `--navy` left border) | Boxed, `border-radius:0 10px 10px 0`, `padding:18px 22px` — "here's a link to follow" | Body-copy (760px, `.tool-prose`) |
| **Tips** | `.article-callout` | Seafoam (`--seafoam-wash` fill, `--seafoam-mid` top border) | Boxed, titled (`.article-callout-title`, uppercase seafoam-deep), `border-radius:0 0 10px 10px` — "here's a fact/technique" | Body-copy (760px) |
| **Warnings** | `.article-warn` | Coral (`--coral-wash` fill, `--coral` top border) | Boxed, titled (`.article-warn-title`, uppercase coral-deep), `border-radius:0 0 10px 10px` — "here's a failure mode to avoid" | Body-copy (760px) |
| **Quotes** | `.article-pull` | No fill — `--navy` left border only | Unboxed: `font-family:var(--font-head)`, 600 weight, italic, 22px, `line-height:1.45` — "this is the idea," not a boxed fact | Wider breakout (1040px) |

**Width is part of the taxonomy, not an afterthought.** CTA/Tips/Warnings stay at
body-copy width (`.tool-prose`'s 760px column) rather than breaking out — reviewed
live, they're mostly multi-line instructional prose, and a wide box just reads as an
odd second column next to the reading column, not an intentional layout choice. Quotes
are the opposite case: short by nature, so widening them reads as a deliberate
editorial moment rather than empty space. **1040px** is the chosen Quote width — a
middle ground between the 760px reading column and `.tool-inner`'s full 1300px
(the width a wide UI element like GER's calculator card uses); wide enough to read as
intentional without being identical to a full-width container element. The CSS is the
same breakout technique introduced in Phase 6d (`left:50%` / `transform:translateX
(-50%)` against `width:calc(100vw - 48px)`), just with the `max-width` cap changed
from 1300px to 1040px and scoped only to `.ger-pull`/`.fah-pull` — the breakout rule
was removed entirely from Hackathon Playbook's callout/warning boxes and from
NetSuite MCP's callout/warning boxes (including retiring the now-unused
`.ns-warn-wide` modifier, since NetSuite MCP currently has no Quote instances at all
after the "protection is enforced by NetSuite" sentence was reverted to body text —
see the Phase 6c correction above).

The CTA class formalizes GER's pre-existing "download the full guide" box (previously
an ad-hoc inline style, not a shared class) rather than inventing a new visual —
it's the reference implementation for what a CTA looks like. Tips and Warnings needed
no color change (`.article-callout` was already seafoam, `.article-warn` was already
coral from the Phase 6b extraction) — only the taxonomy naming and the sanctioned
coral exception (§2.3 above) are new. Quotes got the real design work: `.article-pull`
previously used the same navy-wash boxed treatment as a CTA, which is exactly what
made a short pull-quote read as an awkward, undersized version of a CTA box instead of
an intentional editorial moment. Dropping the box and scaling up the type (using the
display font, `--font-head`, instead of body copy) gives a quote real visual weight
without competing with the boxed types. The three page-specific per-quote font-size
overrides this replaced (`.ger-pull p`, `.fah-pull p`, `.ns-pull p`) are gone — one
consistent Quote size now applies everywhere, which is possible now that quotes are a
real distinct type rather than a differently-fudged version of a box. The literal
surrounding quotation marks were also dropped from every quote's text — with the
left-rule accent and the distinct display-font treatment already signaling "this is a
quote," a leading/trailing `"` was redundant, not clarifying.

One non-obvious retag: NetSuite MCP's `.ns-note` ("The connection is per person...")
was previously its own fifth gray style (`--surface-2`/`--line-strong`) that didn't
map onto any of the four types. It's folded into the Tips family (seafoam) rather
than kept as a one-off exception, since its content — informational, not a warning,
not a CTA, not a quotable idea — is functionally a tip. NetSuite MCP's `.ns-tip`
boxes (the compact seafoam annotations nested inside its use-case cards) were already
correctly seafoam before this phase and needed no change — they're a legitimate
compact, card-nested variant of the Tips type, not a duplicate to consolidate away.

**Quote vs. Tip is a content judgment call, not just a mechanical retag.** Hackathon
Playbook's opening pull-quote ("Before any piece of work, two questions...") was
rewritten mid-phase into a two-question numbered list plus an explanatory paragraph —
content that structurally doesn't fit the Quote type's unboxed, single-idea, large-type
treatment. It was moved to a Tip box instead (which already supports ordered/unordered
lists), rather than forcing a list and a paragraph into 22px italic display type. The
rule of thumb going forward: a Quote is one short, standalone idea a reader could
repeat verbatim; anything that needs structure (a list, multiple sentences of
explanation) is a Tip even if it originated as a "pull-quote."

**Pull-quote audit (post-taxonomy):** with Quotes now a real, distinctly-styled type
rather than a fudged box, several blocks repositioned into `.article-pull` during the
original 6b/6c builds turned out not to pass a genuine test on review. **The test:** a
real pull-quote is a standalone declarative statement or insight — it reads naturally
as "this is the idea" on its own, out of context. It is *not* a process/sequence (→
flowchart + caption instead), a list of steps or parallel items (→ bulleted list
instead), or a plain informational sentence that just happens to sound punchy (→
standard body text instead, same fix already applied to NetSuite MCP's "protection is
enforced by NetSuite" line above).

Auditing every `.article-pull` instance across all three Atlantic pages against this
test:

- **GER's three Phase 6d quotes** (efficiency-disaster, network-effects, permanent-capital-loss)
  all pass — each reads as a standalone idea a reader could repeat verbatim, out of
  context. Sourced from the published PDF and already vetted; untouched.
- **NetSuite MCP** has zero remaining `.article-pull` instances (its one quote was
  already reverted to body text — see above).
- **Hackathon Playbook** had three that failed the test, all fixed:
  - *"Diverge first...Then converge...The separation matters..."* was a two-mode
    process description, not an idea → converted to a plain bulleted list (unboxed,
    matching the page's other plain lists), with the "why the order matters" sentence
    kept as a following paragraph rather than folded into a list item.
  - *"Inspire → Sleep → Build. That's the sequence..."* was a three-step sequence, not
    an idea → converted to a lightweight CSS-only boxes-and-arrows flowchart (three
    steps connected by arrows, stacking vertically with down-arrows on mobile) with the
    explanatory sentence underneath as a plain caption, not quote styling. No charting
    dependency needed — Mermaid is already wired up elsewhere (the admin ER diagram and
    "How FP&A Buddy Works" sequence diagram) but only for genuinely complex diagrams;
    three linear boxes on a public page didn't justify loading it.
  - *"The goal is at least one thing in production before anyone gets on a plane..."*
    was a plain declarative sentence → converted to standard body text, same treatment
    as the NetSuite MCP correction above.

Break rule re-verified after all three conversions: the unbroken-prose stretches on
either side of each conversion are 52/114 words (design-thinking section), 77/90 words
(inspire/sleep/build section), and 82 words (Park-verdict section) — all comfortably
under the ~250-word threshold. A bulleted list and a flowchart are legitimate break
devices in their own right, same as a pull-quote, callout, or image, so removing a
quote in favor of one doesn't reopen a gap.

---

## 6. Do / Don't

| ✅ Do | 🚫 Don't |
|---|---|
| Let navy + off-white do most of the work | Reach for color to fill space |
| Use coral once per screen, as a pop (`.article-warn` boxes excepted — see §5's Callout taxonomy) | Spread coral across a layout |
| Keep status colors for status only | Use `--alert` red as a highlight, or coral as a *system* status/error color |
| Headings in Outfit, reading in Source Serif 4 | Mix the serif into UI, or set body in Outfit |
| One marker-underline, one or two stickers per page, in a header/hero or card corner | Repeat the graffiti kit decoratively, or put it on admin/data surfaces |
| Buttons navy or ghost | Make a seafoam or coral button |

The one sanctioned exception to "buttons navy or ghost": Delete/Reject actions use the
status-red `#b91c1c`/`#fee2e2`/`#fca5a5` family (§5) — a destructive-action signal, the
same category as `--alert`, not a decorative color choice.

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
  --coral-wash:#FBEAE3;    /* soft coral fill — callouts (navy text) */
  /* Text */
  --ink:#1a1a1a;
  --ink-soft:#3a3833;
  --muted:#6F6A60;         /* warm mid-gray */
  /* Lines (warm-toned) */
  --line:#E4E0D6;
  --line-strong:#D6D1C4;
  /* Semantic — GER calculator readout only */
  --good:#002975; --caution:#9A6B12; --alert:#9E3B30;
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
  (status/feedback, benchmark badges, chart tints, and two deliberate exceptions: destructive-action
  buttons, §5/§6; and "Sail, Don't Row"'s realistic sky/water/skyline/boat illustration palette,
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
