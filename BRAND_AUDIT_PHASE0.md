# Site-Wide Brand Standards Audit — Phase 0 Findings

**Investigation only. No code changed.** Consolidates the deferred "Phase 9: full route
sweep" and "BRAND.md/voice doc-sync audit." Baseline: live `:root` CSS + `.page-*` tier
classes in `webapp/app.py`, `BRAND.md`, and the voice rules in `linklib/voice_review.py`
/ `linklib/agent.py`. Two previously-fixed issues (admin SYSTEM pages' width/typography;
`/library`'s eyebrow font-size conflict) are **not** re-reported below.

---

## 1. Route inventory

Pulled live from `/admin/system/page-index`'s own introspection (`_page_index_snapshot()`),
not a manual crawl — 77 HTML page routes.

**Public (no auth), full visual-identity standard:** `/`, `/about`, `/thought-leadership`
(+ its 3 articles: growth-engine-ratio, ai-hackathon-playbook, netsuite-mcp), `/contact`,
`/tools`, `/tools/software` (+ `/compare`, `/find`, `/{slug}`), `/tools/communities`
(+ `/compare`, `/find`, `/{slug}`, `/gap`, `/correct`, `/submit`), `/tools/benchmarks`,
`/tools/submit`, `/login`, `/forgot-password`, `/reset-password`, `/privacy`, `/play`,
`/play/leaderboard`.

**Private, functional/reference standard:** `/library`, `/library/archive`, `/library/feed`,
`/library/ask`, `/library/past-questions`, `/library/submit`, `/ask/history`, `/read`.

**Admin back office (~40 pages), functional/reference standard:** everything under
`/admin/*` — dashboards, list/report pages (`page-admin`, 1500px), single-record forms
(`page-form`/`page-grid`, edit/new), and the three `/admin/system/*` reference pages
(already fixed).

**Tooling gap found:** `/read` (`webapp/app.py:11193`) is flagged by Page Index as
**"No tier assigned."** It isn't actually mis-sized — its bespoke template
(`_READER_TMPL`/`_READER_CSS`, `webapp/app.py:11064-11190`) hard-codes
`max-width:1900px` on `.reader-layout`, which sits squarely in the documented
`page-full` range (~1800-2000px) and matches BRAND.md §5's own listing of `/read`
under that tier. But because the reader is a fully standalone `<!doctype html>`
template that never uses the `.page`/`.page-full` class system, Page Index's
class-name-grep can't recognize it — the same situation `/library/archive` and
`/library/feed` are in, except those two are allow-listed
(`_PAGE_INDEX_CUSTOM_EXCEPTIONS`) and `/read` isn't. **Doc/tooling drift, not a visual
bug** — `/read` should be added to that exception set (or the tool taught to recognize
it), so Page Index doesn't show a false "unassigned" flag forever.

---

## 2. Visual audit — width tiers & typography

**Width tiers: clean.** Verified all four live tier widths directly against BRAND.md's
ranges — `page-full` 1900px, `page-grid` 1300px, `page-form` 640px, `page-admin` 1500px
(`webapp/app.py:700-707`) — all inside their documented bands. The two bespoke
full-bleed exceptions also check out: `/library/archive` 960px, `/library/feed` 860px.
No stray `.page` without a second tier class exists anywhere, and `page-tool` /
`page-narrow` / `page-wide` appear only in retirement-history code comments
(`app.py:698,710`), never as live markup — the retired "functional tools" tier is
genuinely gone, not lurking.

### Reused-pattern conflicts (highest priority — repeat of the eyebrow-bug shape)

**Eyebrows / kickers / section labels** — beyond the two already-fixed spots, at least
**8 more distinct value combinations** exist for what BRAND.md §3.1 defines as one thing
(DM Sans, 11.5px, 600, .1em tracking, muted/navy):

| Combo | Where |
|---|---|
| 12px/600/.16em | Homepage bio eyebrows ("A CFO, for CFOs" `1372`; "CFO · Boston, MA" `1406`) |
| 13px/600/.04–.06em | GER calculator section labels (`1600,1718,1734,1760,1784`), reused verbatim on two other thought-leadership articles (`2372, 2720`) |
| 11px/700/.14em | `.fah-callout-title`/`.fah-warn-title` (`2303,2308`), copy-pasted as `.ns-callout-title`/`.ns-warn-title` (`2671,2676`) |
| 10px/700/.08em | Case-study labels "Airbnb"/"IBM"/"Google" (`2410,2415,2420`) |
| 12px/600/.04em vs 12px/700/.06em | `.cc-label` (`5536,6961`) vs `.cc-section` (`5539`) — internal conflict *within the same compare-card component* |
| 12px/600/.06em | Matchmaker labels (`5840,5847,5856,7369`) |
| 11px/700/.08em | "Bottom line" (`7353`) |
| 12px/700/.07em | "Warm Intro" badge (`9843,10163`) |
| 12px/600/.06em | "Sources"/"Topics" (`10825,10839`) |
| 11px/700/.1em | `.reader-toc-title` (`11088`) |
| 11px/700/.08em | `.ask-section-label` (`11988`) |

**Card titles** — the 17px cluster alone spans **three different font weights** for
the same size: 700 (`.tl-card h3`, `1509`), 600 (`.tool-name`/`.comm-name`, `4923,5544,
6281,6965` — internally consistent with each other), 800 (`.sdr-outcome-title`, `3237`).
Separately, 16px (`.card-title` `11467`, `.fcard-title` `10863`, `.ns-case-title` `2692`)
and 15px (`.fah-tier-title` `2357`) split card titles across yet more sizes depending on
which grid they're in.

**`.cc-cell` (admin table cells)** — defined **4 separate times** with two diverging
rule sets: 14px font / 14–16px padding (`5534,6959`) vs. no explicit font-size / 8–12px
padding (`13314,13459`). Same class name, no shared stylesheet reuse. Header labels
(`<th>`) also split 12px/600/.06em (`8415-16,9357-58,14396-98,16653-55`) vs. 11px/700/.06em
(`15229-33,15381-95`).

**`.tool-admin-btn`** — the one component BRAND.md documents most precisely
(inline-row-action: `--muted` text, `--line` border, 6px radius, ~12px, `padding:3px
10px`). Defined 3 times: `4939` matches spec exactly; `6051` and `10273` use `padding:5px
12px` instead.

**Hardcoded near-miss hex** — `#3a352e` appears **25 times** across nearly every
card-description surface (tool-desc, bench-desc, comm-demo, profile fields — e.g.
`4930,5535,5848,5857,6011,6138,6284,6960,7362,7370`) where the actual token
`--ink-soft` (`#3a3833`) should be referenced instead. This is the textbook "should be
a token, isn't" case, and it's the most-repeated one found.

**Homepage hero H1 line-height** — `webapp/app.py:1373` sets
`line-height:1.08` on the masthead H1, but the documented/canonical value is `1.05` —
confirmed both in BRAND.md §3.1 and in `/admin/brand`'s own live type specimen
(`webapp/app.py:16945`), and matched correctly by `/about`'s identical-role hero H1
(`webapp/app.py:1407`). Isolated single-spot miss, but on the highest-traffic page
in the app.

### Isolated / lower-priority visual misses

- **Undocumented "admin H2" sizes** — three different sub-heading sizes compete for
  the same role across admin pages: 16px/700 (`13011`), 16px/600 (~8× — `8221,8237,
  9201,9217,10212,10221,10231,10250,7730`), 15px/600 (`8424,9366`). Not a spec
  violation (each is a deliberate override), but three undocumented values for one
  informal "admin section heading" role.
- **`.b8860b` / `.92400e`/`#fef3c7`** — an amber "verify/caution" badge family used
  for star ratings and "needs verification" flags, near-miss of `--caution` (`#9A6B12`)
  but not using the token.
- **Sail Don't Row illustration palette** (`#274E96,#3E5FA8,#0A2A6B,#061A45,#16418F,
  #B5553A,#5FB89E`, ~`3170-3390`) — decorative SVG gradient shading, near-misses of
  navy/coral/seafoam, confined entirely to one illustration.
- **Undocumented destructive-button variant** — `color:#b91c1c;border-color:#fca5a5`
  on Delete/Reject actions (`7683,8158,8188,8396,8553`, plus `.tool-admin-btn` hover at
  `4941`). Internally consistent, semantically justified (destructive action, not
  decoration), but BRAND.md §6 says "buttons navy or ghost" with no stated exception.

### Doc-only drift

- None of the above are doc-only — code is internally *inconsistent* in every case
  above, not just diverging from a stale BRAND.md table. The Sail Don't Row palette and
  the destructive-button color are the closest to "doc gap" (consistent code, missing
  documentation of an intentional exception) rather than a code bug.

---

## 3. Content audit — voice consistency

Sampled 11 public pages/sections and 8 admin pages against `linklib/voice_review.py`'s
mechanical rules and the tone guidance in `VOICE_CORE_DEFAULT`.

**Mechanical (banned words/filler/performative): clean.** `tests/test_voice_standards.py`
scans the entire `app.py` source file-wide already, so CI would already catch any hit
anywhere — confirmed none exist; no new mechanical violations found in the sample.

**Placeholder/off-voice copy:** One concrete hit — "Thanks, that's genuinely useful."
(`webapp/app.py:6668` and `:7261`, the communities-gap/correct thank-you pages). "Genuinely"
is explicitly on the voice guide's avoid list; it's documented as intentional in
`_COMMUNITIES_REFERENCE_HTML` (`app.py:9001`), so this may already be a deliberate,
reviewed choice — but it's a literal guide violation and worth Brian's second look.
Everything else sampled (homepage, `/contact`, `/tools` landing, thought-leadership
copy) reads as genuinely first-person and specific — no AI-slop pattern found. Admin
copy sampled is mostly functional labels with little free prose to check.

**Spaced em dashes — 7 instances, zero existing automated coverage.** BRAND.md's policy
is unambiguous: a spaced em dash is *never* allowed (unspaced, or a matched unspaced
pair, is fine). None of these are caught by any CI check — `test_voice_standards.py`
only checks banned words/filler/performative, not dash spacing:

1. `app.py:5790` + `:7218` (identical) — matchmaker budget-cap message: "...resets at
   the start of next month — in the meantime, browse the full directory..."
2. `app.py:5514-5516` — `/tools/software/compare` intro, two spaced dashes in one
   sentence: "...whether AI agents are actually involved — not just a tagline, since
   that's increasingly a deciding factor — plus feature availability..."
3. `app.py:7534` — communities-submit JS status: "Review before saving — anything
   marked 'Needs verification' needs a manual check."
4. `app.py:13527` — `/admin/exa-settings` toggle text: "Exa is off — using the native
   web-search fallback."
5. `app.py:13531` — same page, error state: "Save failed — try again."
6. `app.py:10251` — tool-edit Features intro: "...feeds the Phase 5 comparison matrix.
   Rows flagged 'Needs verification' came from the LLM enrichment pass..." (dash before
   the quoted clause)
7. `app.py:8940` — tool-edit "Resources included" placeholder: "Templates,
   benchmarking, research, job boards, etc. — or 'No'."

Notably inconsistent within the same file: correct unspaced `&mdash;` usage sits right
alongside these (`app.py:1335,1338,4782,4786,4804`), so this isn't a blanket style
choice that needs reconciling with policy — it's unreviewed drift in exactly the way
the standing em-dash rule anticipates catching.

---

## 4. Findings by severity / blast radius

### A. Reused-pattern conflicts (highest priority — fix once, fixes many pages)
1. Eyebrow/kicker/section-label — 8+ diverging value sets beyond the two already fixed
2. Card titles — 17px cluster split three ways (600/700/800 weight); 15/16/17px split across grids
3. `.cc-cell` admin table cells — defined 4× with 2 diverging rule sets; `<th>` labels split 2 ways
4. `.tool-admin-btn` inline-row-action padding — 2 of 3 definitions diverge from the documented spec
5. `#3a352e` hardcoded near-miss of `--ink-soft` — 25 occurrences across card descriptions sitewide
6. Spaced em dashes — 7 instances, no CI coverage, contradicts BRAND.md's em-dash rule

### B. Isolated single-page/single-spot misses (lower priority, narrow fix)
1. Homepage hero H1 `line-height:1.08` vs. canonical `1.05` (`app.py:1373`)
2. Undocumented "admin H2" sizes — 3 competing values, no spec violation but no single source of truth
3. Amber "verify/caution" badge family near-miss of `--caution` token
4. "Genuinely" in the communities thank-you copy (possibly already reviewed/intentional)

### C. Doc-only drift / documentation gaps (code is fine, docs need updating)
1. `/read` should join `_PAGE_INDEX_CUSTOM_EXCEPTIONS` (or Page Index should recognize
   its bespoke template) — currently shows a false "unassigned tier" flag
2. Sail Don't Row's illustration-only palette isn't recorded as an `AUX_COLORS`
   exception in `linklib/brand_check.py`
3. Destructive-button red isn't documented as a sanctioned exception to "buttons are
   navy or ghost only"

---

## Proposed phasing (Brian's call — nothing started)

- **Phase 1 — Eyebrows/labels + card titles.** Highest fix-once/fixes-many ratio;
  directly continues the pattern of the two bugs that triggered this audit.
- **Phase 2 — Admin table cells (`.cc-cell`) + `.tool-admin-btn` padding.** Contained to
  admin surfaces, mirrors the recently-fixed SYSTEM-pages work.
- **Phase 3 — Color-token hygiene.** Replace the 25 `#3a352e` occurrences with
  `--ink-soft`; reconcile the amber badge family with `--caution` or add it to
  `AUX_COLORS` as a deliberate exception.
- **Phase 4 — Voice cleanup.** Fix the 7 spaced em dashes (each needs Brian's sign-off
  per the standing em-dash policy — quote/context provided above); resolve the
  "genuinely" instance.
- **Phase 5 — Doc-only fixes.** `/read`'s Page Index exception; document the Sail Don't
  Row and destructive-button exceptions in `BRAND.md`/`brand_check.py`.
- **Isolated single-spot items** (homepage H1 line-height, admin H2 sizes) can ride
  along with whichever phase touches that area, or be swept up on their own — narrow
  enough not to need dedicated phasing.
