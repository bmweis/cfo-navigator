# Site-wide layout audit — triage (PR 27)

**Status: read-only audit. No layout was changed in this PR.** This document is the
deliverable — a sorted inventory of every page's spacing/alignment/ordering, plus a
separate investigation into a reported badge-display bug, so Brian can spot-check both
before any fix PRs start.

Audited at commit `11942a6` (PR #543 merged, "Copy batch 7: remaining Tier 2 items").
Five Step-0 markers verified present on this HEAD before starting: `/admin/inbox/
community-gaps`' intro reading "Nothing here happens automatically" with no "folded in
from the retired /community waitlist page" text (PR #543); `/admin/reader/enrich`'s
intro with no "server-side" (PR #543); `_COMMUNITIES_REFERENCE_HTML` compressed,
pointing to ARCHITECTURE.md (PR #542); `docs/COPY_AUDIT_TRIAGE.md` present (PR #537);
and exactly two page tiers defined in `webapp/app.py` (`.page-standard` 1300px,
`.page-form` 640px — confirmed the only two `.page-*` CSS rules in the file).

Baseline: 3,000 tests collected and passing, run in four ~750-test chunks (not globally
exporting `LINKLIB_PASSWORD`/`LINKLIB_SECRET_KEY`, since three files assume ambient
open-auth and produce false failures otherwise). Confirmed unchanged after this audit —
nothing in the repo was edited; `git diff` against `origin/main` is empty except this
file.

## The standard

Brian's own framing: **no wasted space, natural order, consistent controls.** That came
out of six rounds on the FP&A Buddy page (`/tools/fpa-buddy`, not audited here — it's
the reference implementation this whole audit is calibrated against), where the real
fixes were: closing a ~110px gap between intro and form, sizing controls to their labels
instead of stretching them to fill a container, and putting related controls in one
sequence rather than splitting them across the page.

- **Tier 1** — fine. Nothing to do.
- **Tier 2** — small fix: a spacing value, a width, a reorder. One or two lines.
- **Tier 3** — real work: needs rethinking, the way the FP&A Buddy page did.

A page is tiered by its **worst** problem. Findings that are purely copy-length-driven
(a placeholder or option label too long for its own field) are noted but not tiered up
on copy grounds alone — the copy pass just finished, and a length problem is a layout
symptom worth flagging even though the fix might land in either camp.

## Methodology

A real uvicorn instance + seeded temp SQLite DB + Playwright (headless Chromium at
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome` — the default installed browser
directories in this sandbox do not work; documented here as the one environment quirk
worth remembering) rendered every page at 1280px, 1920px, and 390px, signed in as admin
and signed out where relevant. Four parallel passes split the surface (public+member;
admin Inbox/Thought-leadership/Brand/Configuration/Health; admin CFO Toolbox
Software/Communities/Resources; admin Reader+FP&A-Buddy-report/feedback). Every finding
below was confirmed with a real `getBoundingClientRect()`/`getComputedStyle()`
measurement, not a screenshot impression alone — several findings below exist only
because a measurement caught something a screenshot alone would have missed (e.g. a
CSS Grid reserving phantom empty tracks that read fine at a glance but measure real
blank pixels).

## 1. Full page table

### Public

| Page | Tier | Reason |
|---|---|---|
| `/` (home) | 1 | fine |
| `/about` | 1 | fine |
| `/thought-leadership` | 1 | fine |
| `/thought-leadership/growth-engine-calculator` | 1 | fine |
| `/thought-leadership/{slug}` (an original-content piece) | 1 | fine |
| `/contact` | 1 | fine |
| `/privacy` | 1 | fine |
| `/login` | 1 | fine |
| `/forgot-password` | 1 | fine |
| `/play` | **2** | ~532px of dead space right of the pre-game setup form at wide viewports (see §3.6) |
| `/play/leaderboard` | 1 | fine (sparse-data empty state) |

### CFO Toolbox — public directory + profile pages

| Page | Tier | Reason |
|---|---|---|
| `/tools` (Toolbox landing) | 1 | fine |
| `/tools/software` (directory) | 1 | fine |
| `/tools/software/find` (matchmaker) | 1 | fine |
| `/tools/software/compare` | 1 | fine (no-selection and real-comparison states both checked) |
| `/tools/software/{slug}` (profile) | **2** | orphaned 1px divider stranded alone on mobile action row (see §3.3) |
| `/tools/software/{slug}/edit` | 1 | fine |
| `/tools/communities` | 1 | fine |
| `/tools/communities/find` (matchmaker) | 1 | fine |
| `/tools/communities/compare` | 1 | fine (real 2-community data checked) |
| `/tools/communities/gap` | 1 | fine |
| `/tools/communities/submit` | 1 | fine |
| `/tools/communities/correct` | — | not a standalone page; requires `?community_id=N` or 404s (brief's premise wrong — see §7). The real form, reached correctly, is fine. |
| `/tools/communities/{slug}` (profile) | **2** | same orphaned-divider bug as the Software profile page |
| `/tools/communities/{slug}/edit` | 1 | fine |
| `/tools/resources` | 1 | fine |
| `/tools/submit` | — | not audited as its own render — member-gated, redirects to `/login` signed out (brief assumed public; see §7) |
| `/library/submit` | — | same — member-gated, not tested signed in as its own form render |

### Member-facing

| Page | Tier | Reason |
|---|---|---|
| `/ask/history` | 1 | fine |
| `/ask/conversations`, `/ask/conversations/{id}` | — | not HTML pages at all — plain JSON API routes the FP&A Buddy page's own JS fetches; no layout to audit (brief's premise wrong — see §7) |
| `/read` (three-pane Reader) | 1 | fine |
| `/read/{article_id}` (standalone single-article view) | **2** | ~496px of dead space beside the article body when it has no headings and the TOC hides (see §3.7) |
| `/change-password` | 1 | fine |
| `/bookmarklet`, `/read-later-bookmarklet` | — | not HTML pages — deliberate `PlainTextResponse` (raw copy-paste snippets); no layout to audit (brief's premise wrong — see §7) |

### Admin — hub, Inbox, Thought leadership

| Page | Tier | Reason |
|---|---|---|
| `/admin` (hub) | **2** | ~406px of dead vertical space between the last group card and the footer, at all three widths (see §3.8) — flagged with real ambivalence; may be an acceptable pattern for a short index page |
| `/admin/inbox/contact-submissions` | 1 | fine |
| `/admin/inbox/toolbox-intros` | 1 | fine |
| `/admin/inbox/community-gaps` | 1 | fine |
| `/admin/inbox/email-failures` | 1 | fine |
| `/admin/thought-leadership/third-party` (list) | 1 | fine |
| `/admin/thought-leadership/third-party/new` | **2** | 3 of 6 input placeholders overflow their own field width (copy-length-driven — see §3.9) |
| `/admin/thought-leadership/third-party/{id}/edit` | **2** | same form, same issue (identical template) |
| `/admin/thought-leadership/original` (list) | 1 | fine |
| `/admin/thought-leadership/original/new` | 1 | fine |
| `/admin/thought-leadership/original/{id}/edit` | 1 | fine |
| `/admin/thought-leadership/game-settings` | **2** | fixed 8-column `auto-fit` grid leaves ~689px blank on a partially-filled second row (see §3.2) |

### Admin — Brand, voice, and content / Configuration / Health and maintenance

| Page | Tier | Reason |
|---|---|---|
| `/admin/copy` | 1 | fine |
| `/admin/voice` | 1 | fine |
| `/admin/emails` | 1 | fine |
| `/admin/brand` | 1 | fine |
| `/admin/users` | 1 | fine (confirms the Add-a-member/Default-caps column-alignment fix already documented in CLAUDE.md) |
| `/admin/system/ai` | **2** | Enrichment-model `<select>` truncates its own option text mid-word (copy-length-driven — see §3.10) |
| `/admin/open-source` | 1 | fine — incomplete grid rows in later sections leave blank cells but keep real card width, unlike the two real grid bugs above |
| `/admin/checks` | 1 | fine |
| `/admin/overhead-spend` | **2**, lower confidence | "Monthly spend by category" card visually taller/emptier than its neighbor form in a zero-data state — screenshot-level only, not independently pixel-measured (see §6) |
| `/admin/overhead-spend/details` | 1 | fine |
| `/admin/system/database` | 1 | fine — one long generated ER diagram, by design |
| `/admin/system/page-index` | **3** | `auto-fill` grid reserves 5 phantom empty tracks beside 2 real stat tiles — ~837px of blank space (see §3.1) |
| `/admin/system/scripts` | 1 | fine — an apparent 390px overflow reading turned out to be a benign scrollable code-box artifact, confirmed via direct bounding-box check |
| `/admin/library-backup` | 1 | fine |
| `/admin/compare-summary-feedback` | 1 | fine |

### Admin — CFO Toolbox: Software, Communities, Resources

| Page | Tier | Reason |
|---|---|---|
| `/admin/tools/software` (list) | 1 | fine |
| `/admin/tools/software/new` | **2** | category checkbox grid stretches sparse items full-width instead of clustering (see §3.4) |
| `/admin/tools/software/categories` | 1 | fine |
| `/admin/tools/software/features` | 1 | fine |
| `/admin/tools/software/feature-review-queue` | 1 | fine |
| `/admin/tools/software/name-duplicates` | 1 | fine |
| `/tools/software/{slug}/edit` | **2** | same category-checkbox stretch issue as Add software |
| `/admin/tools/communities` (list) | 1 | fine |
| `/admin/tools/communities/new` | **2** | Reach/Demographic 2-column grid never collapses on mobile — placeholder truncates at 390px (see §3.5) |
| `/admin/tools/communities/categories` | 1 | fine |
| `/admin/tools/communities/{id}/profile` | **2** | "Quick facts" 2-column grid has the same non-collapsing pattern — 5 placeholders truncate at 390px |
| `/tools/communities/{slug}/edit` | **2** | same Reach/Demographic issue, and here it truncates real saved data, not just a placeholder |
| `/admin/tools/resources` (list) | 1 | fine |
| `/admin/tools/resources/new` | 1 | fine |
| `/admin/tools/resources/{id}/edit` | 1 | fine |

### Admin — Reader sub-group, FP&A Buddy report/feedback

| Page | Tier | Reason |
|---|---|---|
| `/admin/reader/feeds` (list) | 1 | fine |
| `/admin/reader/feeds/new` | 1 | fine |
| `/admin/reader/feeds/{id}/edit` | 1 | fine |
| `/admin/reader/backfill-content` | **2** | a bare, uncarded failure-reason pill row floats between two clearly-carded sections with no heading/border of its own (see §3.11) |
| `/admin/reader/dedupe` | 1 | fine (pre-scan state only — see §6) |
| `/admin/reader/tag-management` | 1 | fine — the merged two-section seam reads as one page, not two stitched halves |
| `/admin/reader/enrich` | 1 | fine |
| `/admin/reader/bulk-delete` | 1 | fine |
| `/admin/fpa-buddy/report` | **3** | plain `<table>` with no responsive treatment squeezes at 390px into wildly uneven row heights (36px header vs. 145–188px data rows) — see §3.12 |
| `/admin/fpa-buddy/feedback` | **2** | two real defects at 390px: a filter label detaches from its own `<select>`, and the same action-button pair renders on two different layouts depending on sibling badge-text length (see §3.13) |

## 2. Counts

| Tier | Count |
|---|---|
| Tier 1 (fine) | 62 |
| Tier 2 (small fix) | 17 |
| Tier 3 (real work) | 2 |
| Not applicable (not a real rendered page, or gated differently than assumed) | 5 |

**86 rows total.** Roughly 22% of pages carry a real (non-copy-only) or copy-length
finding; only 2 pages need real rethinking (`/admin/system/page-index`, `/admin/
fpa-buddy/report`), and both are one-line-per-instance CSS-grid/table-treatment fixes
in practice, not architectural problems — "Tier 3" here means "this one component
needs a different approach," not "the page is a mess."

## 3. Worst offenders

### 3.1 `/admin/system/page-index` — Tier 3 — `auto-fill` grid reserves 5 phantom tracks

`getComputedStyle` on the stat-tile row: `grid-template-columns` resolved to **7
identical 167.4px tracks** for only 2 real children (PAGES, FLAGGED). `auto-fill`
reserves a track for every column that *could* fit, whether or not content exists to
fill it; `auto-fit` collapses empty tracks instead. Each real tile renders at 167px
wide inside a 1232px container, with ~837px of blank space to their right. **Fix
touches:** one CSS value, `auto-fill` → `auto-fit`, on that route's stat-card grid.

### 3.2 `/admin/thought-leadership/game-settings` — Tier 2 — same `auto-fill` pattern, second instance

The rank-detail field grid computed to **8 equal 137.75px columns** for ranks with
"shark" fields (Mate, First Mate, Skipper — 11 fields each); row 2 only populates 3 of
8 columns, leaving ~689px blank on a 1186px-wide grid. Deckhand (7 fields, no shark
mechanics) never wraps to a second row so never shows the gap. **Fix touches:** give
the 3 shark-lunge fields their own row-scoped layout instead of sharing the outer
8-column grid, or switch to `auto-fit`.

**These two are the same underlying mechanism** (see §4) — worth fixing together or at
least flagging as one root cause, not two unrelated bugs.

### 3.3 `/tools/software/{slug}` and `/tools/communities/{slug}` — Tier 2 — orphaned divider on mobile

The profile-page action row (`Visit → Compare → [1px divider] → Edit`) has no
wrap-awareness on its divider (`width:1px;align-self:stretch;margin:0 2px`, no
`flex-basis`). At 390px, Visit+Compare fill the row, leaving ~103px — not enough for
divider+Edit together, so Edit wraps alone and the divider is left stranded at the end
of row 1 with nothing beside it. Measured on a real tool profile: divider at
x=286.7–287.7 on the Visit/Compare row; Edit on the next row down. Identical CSS class,
identical code shape, confirmed on both entity types. **Fix touches:** hide the divider
below the breakpoint where it would wrap alone, or pair it with Edit so they wrap
together — one shared CSS rule, benefits both pages at once.

### 3.4 `/admin/tools/software/new` and its edit-page twin — Tier 2 — category checkboxes stretch instead of clustering

`_tool_category_checkboxes`'s container (`display:grid;grid-template-columns:
repeat(auto-fit,minmax(150px,1fr));gap:8px`) stretches populated tracks to fill the
row when there are few items. With 3 seeded categories at 1280px, measured gaps of
roughly 400–410px between checkboxes — three items spanning the full ~1230px row as
equal `1fr` columns instead of clustering left like a tag list. The identical pattern
backs Communities' own category checklist. Production has ~15–22 categories per
entity, so this shows less dramatically there (only an incomplete last row stretches),
but the underlying behavior is unchanged. **Fix touches:** `auto-fill` +
`minmax(150px,max-content)` with `justify-content:flex-start` (or a plain
`flex-wrap:wrap` row) instead of `auto-fit` + `1fr`.

### 3.5 `/admin/tools/communities/new`, its profile-edit page, and the public edit page — Tier 2 — fixed 2-column grid with no mobile breakpoint

`_community_form_fields_parts`'s Reach/Demographic pair and
`_community_profile_form_fields`'s "Quick facts" block both use a hardcoded
`display:grid;grid-template-columns:1fr 1fr;gap` with **no media query** — unlike every
other multi-column layout on the same pages, which either use `.tool-form-cols`
(which does have a 700px breakpoint) or an `auto-fit` grid (which collapses). At 390px
this renders ~170px columns. On the **public edit page**, a real saved value ("VPs and
directors in SaaS finance") visibly truncates to "VPs and directors" — an admin can't
read saved data on a phone without clicking in; on the Add/profile pages it's the
placeholder text that truncates. **Fix touches:** swap `1fr 1fr` for the same
`repeat(auto-fit,minmax(200px,1fr))` pattern already used two sections lower on the
identical page.

### 3.6 `/play` — Tier 2 — dead space beside the pre-game setup form

`#sdrRoot` (1252px wide at 1920px) contains `#sdrIntro`/`#sdrPreGame`, both capped at
`max-width:720px` but left-aligned rather than centered within the wider container.
Measured: container right edge − form right edge = **532px**. The game canvas fills
this space once play starts, but the setup screen itself reads lopsided. **Fix
touches:** center the pregame content within its container (`margin:0 auto`), or cap
the container's own width to match.

### 3.7 `/read/{article_id}` — Tier 2 — dead space when the article has no headings

`.reader-layout` uses `justify-content:space-between` between `.reader-main` (760px)
and `.reader-toc` (220px); the page's own JS hides the TOC entirely when an article has
zero `<h2>`s, leaving one flex child that `space-between` just left-aligns instead of
centering. Measured: **496px** of dead space at 1280px, same absolute gap at 1920px.
Plausibly common — a lot of archive content has no real heading structure. **Fix
touches:** one CSS class (switch to `justify-content:center`, or `margin:0 auto` on
`.reader-main`) applied whenever the TOC-hidden class is set, plus the one line of JS
already toggling that class.

### 3.8 `/admin` (hub) — Tier 2, flagged with genuine ambivalence — dead space below the group cards

Measured at 1280×900: last card bottom edge at y=424px, footer starts at y=830px — 
**~406px** of unused vertical space, reproduced at 1920px and 390px too. A short
index/nav page with white space below is a common and often-acceptable pattern, and
there's no obvious one-line fix (it would mean either shrinking the page's effective
height commitment or vertically centering the content block) — reporting the
measurement rather than asserting it's wrong. Worth Brian's own call on whether this is
worth touching at all.

### 3.9 / 3.10 Copy-length-driven field overflow (two instances, not rewritten here)

`/admin/thought-leadership/third-party/new` (and its `/edit` twin): canvas-measured
placeholder overflow — `title` placeholder 731px of text in a 592px field, `date_label`
496px in a 289px field, `display_order` 212px in a 180px field. `/admin/system/ai`: the
Enrichment-model `<select>` renders "Opus 5—Deepest summaries. The one to standardize
the archi…" truncated mid-word in its own closed dropdown box. Per this brief's scope,
copy length isn't being rewritten here — flagged as the layout symptom (field too
narrow for its own intended content) for whoever picks up the fix to decide whether the
fix is a wider field or shorter copy.

### 3.11 `/admin/reader/backfill-content` — Tier 2 — orphaned, uncarded pill row

The failure-reason pill row (e.g. "1 Bot challenge") sits between the Purge card and
the Recent attempts heading as bare `<div style="display:flex...">` markup with no
card border, no heading, unlike every other block on this page (Limit form, Retry
Wayback, Needs manual review, Accepted as final, Purge articles, Recent attempts — all
bordered 14px-radius cards with their own heading). Spacing itself is normal (~20px
gaps on either side); the problem is structural inconsistency, not a gap. **Fix
touches:** wrap the pill row (and the related domain-clustering banner sharing the same
insertion point) in the same card treatment as its neighbors.

### 3.12 `/admin/fpa-buddy/report` — Tier 3 — table squeeze produces wildly uneven rows at 390px

Measured cell heights at 390px: header row 36.3px; data rows 145.6px, 188.5px,
166.6px — for rows holding the same shape of content. The table has all 5 real columns
present with no responsive collapse/scroll, so at 390px the Question column squeezes
to ~95px and Settings to ~198px, forcing multi-line wraps. **Fix touches:** give this
table the same `overflow-x:auto` + `min-width` treatment (or mobile-card fallback)
other admin tables already have — this is exactly the class of table-responsiveness
work already done elsewhere in the codebase, just not yet applied here.

### 3.13 `/admin/fpa-buddy/feedback` — Tier 2 — two real 390px defects

(1) "Reviewed:" label sits on the same row as "Filter by rating:" at `top:694.9,
left:277.3`, but the `<select>` it labels renders a full row down, left-aligned to the
page margin (`top:731.1, left:24`) — the label and its control visually separate. (2)
The "View in ask report →"/"Mark reviewed" action pair renders two different ways on
the same page depending on the preceding badge's text length: stacked and
misaligned when the badge text is long ("Inaccurate"/"Not helpful"), side-by-side when
short ("Helpful"). **Fix touches:** wrap each label+select as one non-wrapping pair;
give the action pair its own dedicated row (`flex-basis:100%`) so it always wraps the
same way regardless of sibling text.

## 4. Recurring patterns

- **CSS Grid `auto-fill` vs. `auto-fit` is the single biggest repeat offender** — two
  independent pages (§3.1, §3.2) hit the identical mechanism: a grid sized for the
  *maximum* possible item count leaves large blank areas when an instance has fewer
  items. `/admin/open-source`'s card grid has the same shape (incomplete rows) but
  doesn't trigger the bug because it keeps card width fixed rather than stretching or
  reserving phantom tracks. CLAUDE.md documents this exact failure class recurring
  elsewhere in the codebase already (the CSS-Grid-blowout / auto-fit-vs-auto-fill
  lessons from Phase P and PR 12) — this audit found it hadn't yet been swept from
  `page-index` or `game-settings`.
- **Hardcoded `1fr 1fr` grids with no mobile breakpoint** — Communities' Reach/
  Demographic pair and its Quick-facts block (§3.5) are the only two instances found;
  every equivalent Software field-pairing already uses either `.tool-form-cols` (which
  has a breakpoint) or an `auto-fit` grid (which collapses), so Software never hits
  this one. A single shared fix (reuse the same `auto-fit` pattern already used two
  sections below it on the identical page) closes both instances at once.
  A third instance of the exact same fixed-two-column shape is used correctly with
  `auto-fit` right below both broken instances on the same pages — meaning the fix is
  "match what's already two sections down," not a new pattern to invent.
  Note: three fields Brian may want to revisit separately (`stage_focus`/
  `jobs_program`/`team_or_individual`, collected in this same "Quick facts" block but
  never rendered publicly per CLAUDE.md's own note) live inside the very grid this
  finding covers — the layout fix and that separate content question are unrelated,
  flagging only so a future pass doesn't conflate them.
- **Components that hold together at desktop widths but come apart at 390px because
  the wrap point depends on sibling content length** — the feedback page's label/
  control pairing and action-row placement (§3.13), and the profile-page orphaned
  divider (§3.3), are all instances of the same underlying gap: a flat `flex-wrap` row
  with no explicit grouping of "which control belongs with which," so a longer sibling
  shifts where the wrap lands and strands something. Three independent pages, same
  root shape — worth a shared fix pattern (explicit sub-grouping via nested flex
  containers or `flex-basis:100%` on the piece that should always wrap together) rather
  than three separate one-off patches.
- **Left-aligned content inside a wider, centered container with nothing else to
  justify the imbalance** — `/play` (§3.6) and `/read/{article_id}` (§3.7) share this
  shape even though they're unrelated CSS. Both read as "a narrower true content
  column stranded to one side of a wider box that has nothing else in it," and both
  would be fixed the same conceptual way (center instead of anchor left).
- **Copy-length overflow** shows up twice (§3.9/3.10) in genuinely unrelated forms —
  not a layout bug per se, but a real signal that a few specific fields are sized for
  shorter content than what they're actually being asked to hold.

## 5. Proposed batching for the fix phase

Grouped by shared root cause/mechanism, not by page area — several findings above are
one fix applied at more than one call site, so batching by mechanism means fewer PRs
and less risk of the two call sites drifting again.

1. **`auto-fill` → `auto-fit` sweep** (§3.1, §3.2) — `/admin/system/page-index`'s stat
   tiles and `/admin/thought-leadership/game-settings`' rank-detail fields. Same
   one-value CSS change, same root cause, no shared helper function to touch (each
   page builds its own grid) — small, mechanical, low risk.
2. **Grid-strategy fixes for sparse-item layouts** (§3.4, §3.5) — the Software/
   Communities category-checkbox grids (stretch → cluster) and Communities' two
   hardcoded `1fr 1fr` grids (no breakpoint → `auto-fit`). Four call sites, one
   underlying fix pattern per finding, touches shared helper functions
   (`_tool_category_checkboxes`, `_community_category_checkboxes`,
   `_community_form_fields_parts`, `_community_profile_form_fields`) so worth doing
   together to keep the two entity types visually consistent, per this repo's own
   standing preference for that.
3. **Mobile wrap-grouping fixes** (§3.3, §3.13, and the backfill-content pill-row card
   wrap from §3.11 if convenient to fold in) — the orphaned profile-page divider, the
   FP&A Buddy feedback page's label/select separation and action-row inconsistency, and
   the uncarded failure-pill row. Three to four unrelated pages, same underlying "give
   this piece its own explicit wrap group" fix shape.
4. **Centering fixes for left-anchored content in an otherwise-empty wider container**
   (§3.6, §3.7) — `/play`'s pregame form and `/read/{article_id}`'s TOC-hidden state.
   Unrelated CSS/pages but the same one-line conceptual fix (`margin:0 auto` /
   `justify-content:center`) — small enough to combine into one PR, or split if either
   surfaces a wrinkle the other doesn't.
5. **`/admin/fpa-buddy/report`'s table responsiveness** (§3.12) — Tier 3, but a known,
   already-solved pattern elsewhere in this codebase (the `admin-table-responsive`
   mobile-card treatment already used on other admin tables) rather than new design
   work — its own PR since it's the one Tier-3 item with real scope, but low risk since
   it's applying an existing pattern, not inventing one.

**Not yet batched, pending a decision rather than a fix:**
- `/admin` hub's ~406px of dead space (§3.8) — flagged with genuine ambivalence; needs
  Brian's own call on whether it's worth touching before it becomes a PR at all.
- The two copy-length-driven field-overflow findings (§3.9/§3.10) — need a decision
  (widen the field vs. shorten the copy) before they're sized as a fix.

**Estimated PR count: 5** for the batches above, assuming both undecided items above
either get folded into an adjacent batch once decided or stay unfixed for now.

## 6. Explicit gaps in coverage

Per the brief's own allowance ("if exhaustive coverage isn't possible, say what you
sampled and what you didn't"):

- **Claude-backed interactive results were not exercised** — no `ANTHROPIC_API_KEY` in
  this sandbox, so the dedupe scan's results view, tag-management's "Suggest merges"/
  "Learn from my archive" outputs, and any Generate/Refresh AI-drafted-field flow were
  only checked in their pre-run empty state, not their populated result state.
- **Backfill-content's live in-progress job banner and Retry-Wayback in-progress
  state** were not exercised — this sandbox's outbound HTTP to arbitrary domains is
  restricted, so no real fetch job could run; only the idle "no run recorded" states
  were checked.
- **Bulk-delete's preview/confirm screens** (after a CSV upload) were not tested — only
  the initial two-step upload page.
- **1920px and 390px coverage across the admin Inbox/Thought-leadership/Brand/
  Configuration/Health group** is partial — that pass confirmed `document.body.
  scrollWidth` at both widths for all 27 of its pages (only one benign scrollable-
  code-box false positive, independently confirmed harmless), but full visual
  screenshot review at those two widths was done for `/admin` and the four Inbox pages
  specifically, not for every page in that group; 1280px visual review was done for
  all of them.
- **`/admin/overhead-spend`'s chart-vs-form height asymmetry** (§1, flagged lower
  confidence) was not independently pixel-measured — screenshot-level only, against a
  zero-data seed. Worth a follow-up look with real seeded vendor-spend rows before
  treating it as a confirmed finding.
- **Landscape orientation (844×390)** was not captured for any public/member page —
  none of that group's pages have an orientation-specific breakpoint the way the
  Reader or Sail Don't Row's in-game stage do (the `/play` setup screen's only
  orientation-aware element only appears once a run starts, which wasn't exercised).
- **`/tools/submit` and `/library/submit`** were not re-tested signed in as their own
  form renders — confirmed member-gated (redirect to `/login` signed out), but the
  actual form layout for a signed-in member is unverified in this pass.
- Several sparse-data empty states (`/thought-leadership` with 4 seeded items,
  `/tools/software/compare` with no ids, `/play/leaderboard`, `/tools/resources`) show
  large blank areas below short content purely from thin seed data, not a layout
  defect — not flagged as findings, noted here so a reviewer doesn't mistake "my seed
  data was sparse" for "the audit missed a real gap."

## 7. Claims in the brief that didn't hold up against source

- **`/tools/submit` and `/library/submit` are not signed-out pages** — both call
  `_is_member(request)` and redirect to `/login` when unauthenticated. The brief's
  page list implied they were public.
- **`/ask/conversations` and `/ask/conversations/{id}` are not HTML pages** — both are
  plain FastAPI dict-returning routes (JSON), the client-side data source the FP&A
  Buddy page's own JS fetches for its "Recent conversations" UI. No rendered layout
  exists to audit.
- **`/bookmarklet` and `/read-later-bookmarklet` are not HTML pages** — both are
  `response_class=PlainTextResponse` by design (raw JS meant to be selected and
  copy-pasted, per RUNBOOK.md's own instructions). No layout to audit.
- **`/tools/communities/correct` is not a standalone page** — it requires
  `?community_id=N` and 404s without one; the brief's list implied it was reachable
  bare. The real form, reached correctly from a community's profile page, is fine.
- Everything else named in the brief (every other route in every group, the seed-method
  names once grepped rather than guessed, the "no wasted space / natural order /
  consistent controls" framing, the FP&A Buddy page's own reference-case history) held
  up against actual source.

## 8. Part 1 investigation report — the badge bug

**Investigated separately from the layout audit above; this is the required companion
report, not a layout finding of its own (per the brief, the badge bug's own display
logic was explicitly excluded from the layout-audit scope).**

### Symptoms, restated

1. CFO Toolbox's collapsed badge reads 7; expanding the group makes the 7 disappear,
   and no child sub-group shows a count in its place.
2. Inbox renders a bare coral dot with no number.
3. No child group (Software, Community, Resources, FP&A Buddy, Reader, Compare summary
   feedback) ever shows a count, in either state.

### Root cause — one bug explains symptoms 1 and 3; symptom 2 is intended behavior, not a bug

**Symptoms 1 and 3 are the same bug.** `webapp/app.py`'s `admin_page()` defines, in its
inline `<style>` block:

```css
.admin-group[open] .group-badge{display:none;}
```

This is a plain **descendant** selector — it matches *every* `.group-badge` span that
is a descendant of *any* currently-open `.admin-group`, however deeply nested, not just
the badge belonging to that specific group's own `<summary>`. The intent (per the
surrounding code comment, added 2026-09-06 in commit `a013491`) was narrower: hide only
a group's own badge once its contents are visible, since the badge is redundant once
you're looking directly at what it's counting. But CFO Toolbox nests two, and in
Reader's case three, levels deep (CFO Toolbox → Software/Communities/FP&A Buddy/Reader
→ Reader's own three quadrants), and every one of those nested levels also carries
class `admin-group` and its own `.group-badge` span. Opening CFO Toolbox makes its
children's `<details>` elements visible in the DOM, but the unscoped CSS rule also
matches — and hides — every one of those children's own badges, **regardless of
whether the child itself is open or closed.**

Confirmed empirically, not just reasoned from the CSS spec: a minimal reproduction
(`<details open>Parent<badge>7</badge> → <details>Child A (closed)<badge>3</badge>,
<details open>Child B (open)<badge>4</badge>`) rendered in a real headless Chromium
with the live selector shows **all three badges hidden** the moment the parent opens —
Child A's badge is hidden even though Child A itself is still closed, and Child B's
badge is hidden even though Child B is independently open. A scoped version of the
same selector (`.admin-group[open] > summary .group-badge{display:none;}` — using a
direct-child combinator to the group's own `<summary>`) reproduced against the same
markup correctly hides only the Parent's own badge, leaving Child A's badge visible
(closed, showing its count) and Child B's badge hidden (open, showing its own contents
instead) — exactly the behavior the original 2026-09-06 comment describes wanting.

This is why expanding CFO Toolbox (symptom 1) makes its own 7 disappear with no child
count appearing in its place, and why no child group ever shows a count "in either
state" (symptom 3) — to see a nested group at all, an ancestor `.admin-group` must
already be open, and that ancestor being open unconditionally blanks every descendant
badge regardless of the nested group's own open/closed state. The bug is structural,
not data-related: the underlying counts computed by `webapp/tasks.open_task_counts()`
and aggregated per group by `_group_badge()` are correct — this was confirmed by
reading through `toolbox_hrefs`' construction (folds in `software_hrefs` +
`communities_hrefs` + `fpa_hrefs` + `reader_hrefs` + the compare-summary-feedback href,
every one of which maps to a real key in `open_task_counts()`'s return dict). Nothing
is being miscounted; the number is simply never rendered visible once you actually
drill in to see which child it belongs to.

**The existing test suite has a real, documented gap that let this ship.**
`tests/test_task_badges.py::test_nested_group_badge_shows_through_collapsed_parent`
explicitly documents (in its own docstring) that "Software's own badge... structurally
can't [show through]... since it lives inside CFO Toolbox's collapsed body" — the test
was written to prove the AGGREGATE parent badge covers the count while everything
stays collapsed, and deliberately never asserts what happens once a reader actually
opens something to look. It checks for the literal string `"task-badge"` inside the
raw HTML response, not the CSS-computed visibility a real browser would show. This is
exactly the class of gap CLAUDE.md's own testing-standard section warns about
elsewhere in this repo (rendered-HTML-string assertions vs. real computed/visible
state) — nobody wrote the test for "expand a nested group and check its own badge is
actually visible," because nobody was looking for that specific failure mode.

**Symptom 2 (Inbox's bare dot) is intended, documented behavior — not a bug.**
`webapp/tasks.py`'s `DOT_ONLY_HREFS` (`{"/admin/inbox/contact-submissions",
"/admin/inbox/toolbox-intros"}`) marks sources that are reviewed as one whole list at
once, with no per-item action — Contact submissions and Toolbox intros both fit this:
you read every message, there's nothing to individually approve/dismiss/resolve. A
numeric badge on a source like that would imply a granularity ("3 things to act on
individually") that doesn't actually exist. `_group_badge()`'s logic sums only the
non-dot-only hrefs (Community gaps, Email delivery) into a real numeric total; if that
total is zero but a dot-only source has something pending, it renders a plain dot
instead of a misleading number — this is exactly `_badge_for_href`'s and `webapp/
tasks.py`'s own module docstring describing the intended design, not an accident.
Whatever state Brian saw (a bare dot, no number) is consistent with: Inbox currently
has a pending contact submission or toolbox intro, and zero pending community-gap or
email-failure rows.

**Design decision (Brian, 2026-09-13): `DOT_ONLY_HREFS`'s dot-vs-count logic is correct
behavior in the wrong place.** A dot with no number is the right signal for the top nav
bar — a glance-level "something needs you," with no room for detail there anyway. On
`/admin` itself, the badges are where Brian actually decides what to work on next, and
"4 things pending" is a materially different decision to make than "1 thing pending" —
collapsing that distinction to a dot is a real information loss on the one page whose
job is showing him what's queued.

**Checked, not assumed: the top nav bar already carries exactly this coarse signal,
independent of `DOT_ONLY_HREFS` entirely — nothing new needs building there.** The
"Admin" link in `_page()`'s own nav (`webapp/app.py`, ~line 1789) already renders a
plain `.task-dot` span whenever `_has_open_admin_tasks()` is true, which resolves to
`bool(open_task_counts(lib))` — true the moment *any* href has a nonzero count, whether
or not it's in `DOT_ONLY_HREFS`. So the nav-bar dot already fires correctly for
Contact-submissions/Toolbox-intros pending items today, alongside every other pending
source, with no special-casing needed. **Resolution: the fix is narrower than initially
scoped — retire `DOT_ONLY_HREFS`'s special-casing from `/admin`-hub badge rendering
only (every badge there shows its real numeric count, always, including Contact
submissions and Toolbox intros); the nav-bar dot needs no change at all.** Still folded
into the same fix PR as the CSS-scoping fix per Brian's direction, since both touch the
same badge-rendering code path — just a smaller change than "build a new home for the
dot" would have been.

### What a fix would touch (not built in this PR, per instructions)

- One CSS selector in `admin_page()`'s inline `<style>` block: scope
  `.admin-group[open] .group-badge` to `.admin-group[open] > summary .group-badge` (or
  equivalent — anything that stops the rule from reaching a nested `.admin-group`'s own
  badge through an open ancestor). Verified working via the reproduction above.
- A new regression test that actually asserts computed visibility for the specific
  case the current suite misses: a nested group's own badge stays visible when its
  ancestor is open but the nested group itself is closed, and correctly hides only when
  the nested group itself is opened. This repo's test suite is TestClient/string-based
  rather than Playwright-based (this session's headless-Chromium path is a real,
  working option — `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` — but isn't
  currently used anywhere in `tests/`), so whoever picks this up should decide whether
  to add a real browser-rendered visibility check or a narrower assertion against the
  CSS selector text itself (weaker, but consistent with the rest of the suite's style).
- **Per the design decision above:** `_badge_for_href()`/`_group_badge()` (both in
  `webapp/app.py`) need to stop consulting `DOT_ONLY_HREFS` for `/admin`-hub rendering
  — every badge there always shows its numeric count, including Contact submissions and
  Toolbox intros. `DOT_ONLY_HREFS` itself likely goes away entirely once nothing reads
  it (confirm no other caller exists before deleting it, per this repo's "no dead
  code" discipline) — it isn't needed elsewhere, since the nav-bar dot
  (`_has_open_admin_tasks()`, `webapp/app.py` ~line 1789) is already driven by the
  simpler, DOT_ONLY_HREFS-agnostic `has_open_tasks()`/`bool(open_task_counts(lib))` and
  needs no change. `webapp/tasks.py`'s own module docstring (which currently explains
  `DOT_ONLY_HREFS`'s purpose) needs updating or removing to match whatever the fix PR
  actually does with the constant.
- No change needed to `webapp/tasks.py`'s `open_task_counts()` itself, `has_open_tasks()`,
  or how counts are computed/aggregated — only where the dot-vs-number rendering
  decision applies on the hub page.

## Two notes carried forward for the record (not acted on, per the brief)

- **`webapp.checks.run_all()` is O(routes).** It renders every public route through
  `TestClient` so `coral_moment_problems()` can count coral in rendered HTML. Every
  route added makes `tests/test_checks.py` and `tests/test_coral_discipline.py` (and,
  live, `/admin/checks` itself, and its 120s-cached badge count) slower, permanently.
  Not a bug — a compounding cost worth knowing about before the next big page-count
  increase.
- **Three test files depend on ambient open-auth** (`test_overhead_spend_chart.py`,
  `test_overhead_spend_csv_routes.py`, `test_play_route.py`) and set no auth env of
  their own — they pass today because of an environment assumption (no
  `LINKLIB_PASSWORD`/`LINKLIB_SECRET_KEY` set anywhere in the process) that's never
  stated in the files themselves. Exporting those two vars globally before a full
  suite run produces 19 false failures across these three files, as this session's own
  baseline run confirmed.
