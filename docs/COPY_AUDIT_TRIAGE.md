# Site-wide plain-language copy audit — triage (PR 20, step 1)

**Status: read-only triage. No copy was rewritten in this PR.** This document is the
deliverable — a sorted inventory so Brian can spot-check the size and shape of the job
before any rewriting starts.

**Approved 2026-09-12, with five corrections** (folded into this document; see §7 and
the batching order in §5 for the substance): the audience test for the System/
judgment-call pages, one false-positive worst offender pulled, `/tools/fpa-buddy/how-it-works`
re-scoped as a deliberate exception rather than a Tier-1 pass, the admin add/edit forms
moved from "sampled clean" to unsorted (real audit deferred to the rewrite phase), and
the batching reordered by surface priority (public → member → admin) rather than by
area. The rewrite phase has not started — batch 1's build prompt is separate.

Audited at commit `fce674b` (PR #536 merged, "Standardize admin table column widths by
field type"). Six Step-0 markers verified present on this HEAD before starting: the
`_COL_WIDTH_*` constants (#536), BRAND.md's column-width scope note (#536 follow-up),
`icon_fill_contract_problems()` (#535), GER's 0.60/0.80/1.20 "Average" tier (#535),
`coral_moment_problems()` with coral removed from `_CARD_ICON_STYLES` (#533), and exactly
two page tiers (`.page-standard` 1300px, `.page-form` 640px — confirmed the only two
`.page-*` CSS rules actually defined in `webapp/app.py`).

## The standard

Copy passes (Tier 1) if someone who has never used this app, and didn't build it,
understands what the thing does and what happens if they click it: front-loads the
answer, names what the thing does (not what it's called internally), short chunks, no
unexplained codebase jargon, says what happens next for anything destructive.

- **Tier 1** — ships as-is.
- **Tier 2** — light pass: one or two problems, fixable in a sentence or two.
- **Tier 3** — real work: assumes internal knowledge, describes mechanism instead of
  outcome, or the point is buried. Needs rewriting, not editing.

A mixed page is tiered by its **worst** copy, with a note that the rest is fine — so a
rewrite doesn't churn what already works.

## 1. Full page/section table

### Public

| Page | Tier | Reason |
|---|---|---|
| `/` Homepage | 1 | clean |
| `/about` | 1 | clean |
| `/thought-leadership` | 1 | clean |
| `/tools` (CFO Toolbox landing) | 1 | clean — confirmed matches PR #525 |
| `/tools/software` (directory) | 1 | clean |
| `/tools/software/{slug}` (profile page) | **3** | "Agent taxonomy" is a literal, unexplained public section header |
| `/tools/software/compare` | 1 | clean |
| `/tools/software/find` (matchmaker) | 1 | clean |
| `/tools/resources` | 1 | clean — confirmed matches PR #533 |
| `/tools/communities` + gap/submit/correct/compare/find | 1 | clean, warm, plain |
| `/tools/communities/{slug}` (profile page) | 2 | mostly clean; minor spillover from shared empty-state copy |
| `/tools/submit`, `/library/submit` | 1 | clean |
| `/contact`, `/privacy` | 1 | clean |
| `/tools/fpa-buddy` top intro/example/usage/controls | 1 | clean — confirmed matches PR 17 |
| `/tools/fpa-buddy/how-it-works` | 1 | exception, not a pass — a deliberate technical showcase for both technical and non-technical readers; mechanism IS the content here, so the plain-language standard doesn't apply |
| Default email templates (`linklib/email_utils.py`) | 1 | clean, warm, plain across all 7 templates |
| Destructive-action `confirm()` dialogs (sitewide) | 1 | clean, consistently states consequence |

### Member-facing

| Page | Tier | Reason |
|---|---|---|
| `/read` (Reader shell) | 1 | clean, short UI labels |
| `/read` subscriber-access alert | 2 | "Re-run the subscriber cookie refresh flow" is mechanism-first phrasing |
| `/read/{article_id}` | 1 | clean |
| `/ask/history` | 1 | clean |
| `/change-password` | 1 | clean |

### Admin — Inbox

| Page | Tier | Reason |
|---|---|---|
| Contact submissions | 1 | clean |
| Email delivery failures | 1 | clean, front-loaded |
| Toolbox intros | 1 | clean |
| Community gaps | 2 | exposes irrelevant internal history ("folded in from the retired /community waitlist page") |

### Admin — Reader tools

| Page | Tier | Reason |
|---|---|---|
| Manage feeds | **3** | raw env-var syntax, a Python function name, a doc-file citation in visible help text |
| Reader content backfill | 1 | clean, front-loaded |
| Content de-dupe | 1 | clean |
| Tag cleanup and style | **3** overall (intro is Tier 1) | page intro is genuinely plain; the carried-over Tagging-style section body is not — see below |
| Enrich archive | 2 | "server-side" is unnecessary jargon, otherwise clear |
| Bulk delete articles | 1 | clean |

### Admin — Toolbox

| Page | Tier | Reason |
|---|---|---|
| Software vendors (list) | 1 | clean |
| Name-duplicate check | 2 | dense mechanism-first paragraph, though it does state the consequence |
| Software categories / Community categories | 1 | clean |
| Manage Features | **3** | "controlled vocabulary tools get mapped against," raw GitHub link as the explanation |
| Feature review queue | **3** | "live feature tables," a doc-section citation |
| Resources (admin list) | **3** | tells the admin to edit `_DEFAULT_BENCHMARKS` in `webapp/app.py` to change page content |
| Add/Edit resource, Add/Edit software, Add/Edit community | **unsorted** | sampling isn't a tier — Brian doesn't believe these are clean; needs a real audit, deferred to the rewrite phase (batch 5) |
| Communities list + "How this works" reference block | 1 (intro) / **unsorted** (reference block) | header/intro clean; the reference block's full body needs a real audit, deferred to batch 5 |
| Community profile edit | **unsorted** | not audited; deferred to batch 5 |

### Admin — Thought Leadership

| Page | Tier | Reason |
|---|---|---|
| Third-party content, Original content, Sail Don't Row settings | 1 | clean, minimal |

### Admin — System

| Page | Tier | Reason |
|---|---|---|
| Scripts | **3** | entire page is written as developer documentation |
| Database | **3** | raw table/variable/file references throughout |
| Page index | **3** | "route," "width tier," `app.routes`, doc-section citation |
| AI configuration and usage | **3** | its own top intro is Tier 1 (see §8 correction); Tier 3 comes from mechanism-describing prose elsewhere on the page (e.g. naming `linklib/pricing.py`'s `MODEL_PRICING` to explain what a freshness banner checks) |
| Checks | **3** | "commit," "GitHub QA workflow," code citations throughout |
| Overhead spend | 1 | clean, front-loaded |
| Archive backup (hub card) | 2 | "Railway Cron Service" — a specific infra product name with no explanation |
| Open source | 1 | clean, warm |

### Admin — Brand/Voice/Copy/Email/Users

| Page | Tier | Reason |
|---|---|---|
| Brand standards | **3** | raw repo paths in the intro |
| Verbal identity (voice) | 2 | "no redeploy," "API call" |
| Site copy | 2 | "no redeploy" |
| Email templates | 1 | override mechanism explained in plain terms |
| Users | 1 | clean, reassuring |
| Users → "How to set up a new MCP user" | 2 | "MCP" unexplained in its own disclosure header (low severity — opt-in click) |
| Sail Don't Row rank settings | 2 | "4300 world-units," "pace score," "no redeploy" |
| FP&A Buddy report | 1 | clean |
| FP&A Buddy feedback | 2 | "retrieval," "Capture and triage only" |

## 2. Tier counts

Of ~55 distinct pages/sections inspected:

- **Tier 1: 34**
- **Tier 2: 11**
- **Tier 3: 10**

Tier 3 is concentrated almost entirely in **System** (Scripts, Database, Page index,
Checks, AI configuration and usage, Brand standards) plus two **Toolbox admin** pages
(Manage Features, Resources) and two **Reader-tools** sections (Manage feeds, Tagging
style) — not spread evenly across ordinary CRUD forms.

**Correction (2026-09-12):** ~150 additional per-item admin add/edit sub-routes
(Software/Communities/Resources/Thought-Leadership), plus the Communities "How this
works" reference block's full body and the Community profile edit page, were sampled
representatively rather than read exhaustively, and were originally reported here as
"presumed Tier 1." Brian doesn't believe that's accurate. These are now marked
**unsorted**, not Tier 1 — they need a real audit, not a sample-based inference, and
that audit is deferred to the rewrite phase (batch 5, see §5) rather than assumed clean
in this document.

## 3. Worst offenders (verbatim, spot-checked against source)

1. **Resources admin list** — instructs the admin to edit code to change content:
   > "Editing `_DEFAULT_BENCHMARKS` in `webapp/app.py` updates a Benchmarking
   > resource's name and description here automatically on the next deploy. No manual
   > re-seed needed. Coverage and Pricing are database-only…"

2. **`webapp/app.py:8085` / `linklib/gates.py`** (public `/tools/software/{slug}`
   profile page) — jargon term used as-is on a **public** page, in both the card
   header and its empty state:
   > `<h2 class="tp-card-h"><small>AI and Agent Capabilities</small>Agent
   > taxonomy{_at_badge}</h2>` … "Agent taxonomy not yet available."

3. **`webapp/app.py:25927`** (Tag cleanup and style → Tagging style section) —
   confirmed present verbatim, and it's the exact phrase this brief cites as the
   canonical bad example:
   > "From that, it distills soft rules injected into enrichment, so new tags match
   > your judgment."

4. **Manage feeds** — raw env-var syntax and a Python function name in admin help
   text:
   > "Each domain's cookie lives in its own `LINKLIB_COOKIE_<DOMAIN>` variable, and
   > `extract.fetch_page` applies it automatically wherever the domain matches (see
   > `RUNBOOK.md` §5 for finding and setting one)."

5. **Scripts page intro** — pure dev-console language:
   > "The CLI scripts still worth running—purpose, cadence, required env vars, and
   > exact invocation. Hand-maintained: a small, slow-changing list, kept honest by
   > the standing rule in `CLAUDE.md`…"

6. **Page index intro**:
   > "A live, self-updating map of every route and its width tier—introspected from
   > `app.routes` on every page load, not a maintained list. Skips non-page endpoints
   > (redirects, JSON/AJAX APIs, file downloads)…"

~~7. **AI configuration and usage intro**~~ — **withdrawn (2026-09-12 correction).**
   This line was written to the plain-language standard and approved in PR #527. It
   front-loads what's editable and names the vendors, which is the point of a page
   about which products power what — "Exa" unexpanded is a product name, not jargon
   substituting for explanation. Left in the audit only to record the correction; the
   intro itself is Tier 1 and shouldn't be touched in the rewrite phase.

8. **Manage Features** — controlled vocabulary + a raw GitHub link as the explanation:
   > "The curated 'key features' list for each Toolbox category—the controlled
   > vocabulary tools get mapped against. See [docs/FEATURE_TAXONOMY.md] for the
   > naming/curation rules." — confirmed verbatim at `webapp/app.py:13000`.

9. **Brand standards intro**:
   > "The full written reference is `BRAND.md` in the repo; an automated check
   > (`tests/test_brand_standards.py`) keeps new content on-palette."

10. **Checks page intro**:
    > "**Every check here runs on each commit** in the GitHub QA workflow; the
    > deterministic ones (*Live + CI*) also run live on this page…"

## 4. Recurring patterns

- **"No redeploy" / "redeploy needed"** — appears 11+ times (Verbal identity, Site
  copy, Feeds, Sail Don't Row settings, Library backup, Re-enrich, …). A reader doesn't
  need to know there's a deploy pipeline, only that a change takes effect right away.
  One-line fix, repeated ~8-10 times — a clean mechanical batch.
- **Raw code/file references in prose** (`webapp/app.py`, `library.db`, `BRAND.md`,
  `RUNBOOK.md`, `FEATURE_TAXONOMY.md`, `ARCHITECTURE.md`, internal variable names) —
  the single biggest structural pattern. Concentrated almost entirely in
  System/Health & Maintenance plus two Toolbox pages. Reads as though an entire admin
  sub-area was written as developer documentation, not owner-facing copy — worth
  deciding as one judgment call (see §7), not fixing page by page.
- **"Agent taxonomy" as an unexplained section label** — identical on the public
  profile page, the public compare matrix, and the shared empty-state string in
  `linklib/gates.py`. One shared constant; fixing it once fixes every render site,
  including the highest-visibility one (public site).
- **Em-dash density** — checked, not a real pattern. Most pages use 1-2 unspaced em
  dashes doing legitimate work, consistent with house style. No page reads as tic-like.
- **Confirm-dialog copy is uniformly good** — a genuine bright spot: consistently
  front-loads consequence and reassures about what isn't lost.

## 5. Proposed batching for the rewrite phase

**Rebatched 2026-09-12, by surface priority (public → member → admin), per Brian's
correction — supersedes the by-area ordering originally proposed here.**

1. **Public — "Agent taxonomy" label + empty-state copy** (`linklib/gates.py` + 2-3
   render sites) — small, high-visibility, single PR.
2. **Member-facing — Reader subscriber-access alert** ("Re-run the subscriber cookie
   refresh flow" → plain terms).
3. **"No redeploy" sweep** — mechanical, low-risk, ~10 pages, one sentence each.
4. **System / Health & Maintenance rewrite** (Scripts, Database, Page index, Checks,
   AI configuration and usage — minus the withdrawn intro line, see §3 — Brand
   standards, Archive backup card). Judged against the standard set in §7: keep file
   paths/commands/variable names where the page's own job is a reference (Scripts'
   exact invocations, e.g.), cut prose that explains itself in codebase terms instead
   of telling three-months-later Brian how to use the page.
5. **Toolbox admin, plus the unsorted forms** — Manage Features, Feature review queue,
   Resources admin list, **and** the real audit of the ~150 add/edit forms + the
   Communities reference block + the Community profile edit page that §2 moved out of
   "presumed Tier 1." Likely more than one PR once that audit actually runs; treat this
   line as a placeholder pending that count, not a single fixed-size batch.
6. **Reader tools — Manage feeds, Tagging style section.**
7. **Remaining Tier 2** (Name-duplicate check, Community gaps, FP&A Buddy feedback,
   MCP-setup disclosure header, Enrich archive — Archive backup card and Reader
   subscriber-access alert already covered above).

**Estimate: 6–7+ PRs** — up from the original 5–6, since batch 5 is no longer a single
known-sized PR once the unsorted forms are folded in.

Per Brian's direction: **no rewrite batch starts from this document** — the build
prompt for batch 1 is separate and comes on its own.

## 6. Settings-stored copy (reported, not tiered — edited via `/admin/copy` / `/admin/voice`)

| Setting key | Controls |
|---|---|
| `homepage_headline_copy` | Homepage hero `<h1>` |
| `homepage_subhead_copy` | Homepage hero subhead |
| `homepage_teaser_copy` | Homepage "Status:" box, first block |
| `homepage_expanded_copy` | Homepage "Status:" box, second block |
| `about_page_copy` | `/about` bio body |
| `voice_core` | Base voice rubric for all AI-drafted copy |
| `voice_fpa_buddy` | FP&A Buddy-specific voice addendum |
| `voice_matchmaker` | Matchmaker-specific voice addendum |
| `tag_guide` | The Tagging-style guide text steering auto-tagging |
| `warm_intro_subject/body/signoff_template` | Warm-intro email |
| `welcome_subject/body/signoff_template` | New-account welcome email |
| `admin_password_reset_subject/body/signoff_template` | Admin-triggered password-reset email |
| `password_reset_subject/body/signoff_template` | Self-service password-reset email |
| `tool_submission_subject/body/signoff_template` | Tool-submission confirmation email |
| `community_submission_subject/body/signoff_template` | Community-submission confirmation email |
| `contact_confirmation_subject/body/signoff_template` | Contact-form confirmation email |

(Excluded as non-copy state: `avatar_json`, `pricing_last_verified`, `models_last_reviewed`,
`exa_pricing_last_verified`, `tag_guide_status`, `tag_merge_status`, `tag_merge_suggestions`.)

## 7. Approval split — resolved 2026-09-12

**The audience question is answered, correcting this section's original framing.** It
was originally posed as "Brian-as-developer" vs. "Brian-as-site-owner" — a false
choice. Brian's actual answer: the test is **Brian in three months, having not opened
that page in a while.** He's a CFO, somewhat technical, not a developer.

That means file paths, commands, and variable names stay wherever they're genuinely
what the page is for — the Scripts page listing exact invocations is doing its job,
that's not a violation. What goes is prose that explains itself in codebase terms:
"introspected from `app.routes` on every page load," "kept honest by the standing rule
in `CLAUDE.md`," "the controlled vocabulary tools get mapped against."

**The per-string test**: would three-months-from-now Brian need this to use the page,
or is it explaining how the page was built? The former stays (even if technical); the
latter goes (even if short). This is the standard batch 4 (System / Health &
Maintenance) applies, and it's why the "AI configuration and usage" intro's vendor
names survive (§3 correction) while its mechanism-describing prose elsewhere doesn't.

- **Tier 3 → approval before shipping**: unchanged, confirmed.
- **Tier 1 untouched**: unchanged, confirmed.
- **Tier 2 "ships with a before/after report, no approval gate"** — the pushback in
  the original draft of this section is **accepted**: send the before/after report
  *before* merging, not after, for the mixed Tier 2 batch (batch 7). A few of those
  items (the "Users → MCP setup" disclosure, the retired "Railway Cron Service" card
  wording, FP&A Buddy feedback's "retrieval"/"Capture and triage only") could resolve
  as a clean mechanical drop or could need a rethought sentence — the pre-merge review
  is what tells the difference before it ships either way.

## 8. Grep-sweep / fact-check results

- **Tag-management carried-over inventory (PR #524)**: confirmed. The page-level intro
  is genuinely plain-language, matching the claimed pilot. But `webapp/app.py`'s own
  build note admits the section copy below it "is carried over from the two original
  pages verbatim, not rewritten" — verified true: the Tagging-style section still reads
  "it distills soft rules injected into enrichment," verbatim, at line 25927. Net: the
  page tiers **3** by its worst section, per the worst-copy rule — expected, not a
  surprise.
- **BRAND.md's "$0.50–$0.70" en-dash example**: confirmed present verbatim at
  `BRAND.md:918` — `"$0.50–$0.70"`, `Q3–Q4`, `1–10 employees`. These are the retired
  pre-#535 GER "Typical" tier thresholds ($0.60–$0.80 as of #535). Flagged only, per
  instructions — not fixed here, since it's a punctuation-rule illustration, not a
  GER reference, but could be mistaken for one by a future reader.
- **`/tools` intro + MCP callout (PR #525)**: confirmed clean — front-loaded, no
  jargon. "MCP" itself is used unexpanded ("connect it to your own AI assistant. The
  whole toolbox runs over MCP.") but the surrounding sentence still conveys the action;
  left as Tier 1 as expected, borderline.
- **`/tools/resources` intro (PR #533)**: confirmed clean, verbatim: "What's here: the
  benchmarking sources I rely on, and books that shaped how I do this job. Not
  exhaustive, just what's held up."
- **FP&A Buddy top intro (PR 17)**: confirmed clean, verbatim: "Ask a real FP&A
  question and get an answer with its sources, not half a day of Googling…"
- **Tag-management hub-nav card description**: confirmed clean ("Merge, rename, or
  remove existing tags, and edit the guide that steers how new ones get chosen.") — the
  finding is specifically in the unrewritten section body below it, as PR #524's own
  note flags.
- **"AI configuration and usage" intro (2026-09-12 correction)**: this line was
  originally listed as worst-offender #7. Brian confirmed it was written to the
  plain-language standard and approved in PR #527 — front-loads what's editable, names
  the vendors, which is the page's whole point. Withdrawn; see §3.
- **`/tools/fpa-buddy/how-it-works` (2026-09-12 correction)**: originally tiered clean
  on the reasoning that the page states its own technical audience up front. Brian's
  correction: it's Tier 1 for a different reason — it's a deliberate technical
  showcase written for both technical and non-technical readers, where mechanism is
  content, not a lapse. The plain-language standard doesn't apply to it at all, rather
  than applying and passing.

## Known audit gaps (unsorted — resolved into batch 5, not assumed Tier 1)

- ~150 per-item admin add/edit forms across Software/Communities/Resources/
  Thought-Leadership were sampled, not read exhaustively (see §2). Brian doesn't
  believe these are clean; a real audit is deferred to batch 5.
- The Communities "How this works" reference block's full body, and the Community
  profile edit page, were not fully audited — deferred to batch 5 for the same reason.
