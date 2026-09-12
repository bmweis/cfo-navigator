# Site-wide plain-language copy audit — triage (PR 20, step 1)

**Status: read-only triage. No copy was rewritten in this PR.** This document is the
deliverable — a sorted inventory so Brian can spot-check the size and shape of the job
before any rewriting starts.

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
| `/tools/fpa-buddy/how-it-works` | 1 | clean — page states its own technical audience up front, so retrieval-mechanics language is in scope here |
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
| Add/Edit resource, Add/Edit software, Add/Edit community | 1 | clean (sampled; not exhaustively field-by-field — see gap note) |
| Communities list + "How this works" reference block | 1–2 | header/intro clean; long reference-block body not fully audited (gap noted) |
| Community profile edit | not fully sampled | gap noted |

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
| AI configuration and usage | **3** | unexplained vendor name ("Exa"), file/variable names in the usage index |
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
style) — not spread evenly across ordinary CRUD forms. ~150 additional per-item
admin add/edit sub-routes (Software/Communities/Resources/Thought-Leadership) were
sampled representatively rather than read exhaustively; the field-label pattern in
every sample was Tier 1, and there's no reason to expect the unsampled remainder to
differ materially — flagged as a residual gap, not asserted with full confidence.

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

7. **AI configuration and usage intro** — short but jargon-substituting-for-explanation,
   the worse of the two failure modes named in the brief:
   > "Two live settings, plus a read-only map of every Claude, Exa, and OpenAI surface
   > in the app."

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

Grouped by area (clearer diffs than by tier, since each area shares one voice problem):

1. **Public "Agent taxonomy" label + empty-state copy** (`linklib/gates.py` + 2-3
   render sites) — small, high-visibility, single PR.
2. **System / Health & Maintenance rewrite** (Scripts, Database, Page index, Checks,
   AI configuration and usage, Brand standards, Archive backup card) — the concentrated
   Tier 3 cluster. One PR, but see §7 — this batch carries a real audience judgment
   call worth putting to Brian before drafting, not just before shipping.
3. **Toolbox admin — Manage Features, Feature review queue, Resources admin list** —
   one PR; all three share the "cites an internal doc/variable as the explanation"
   failure.
4. **Reader tools — Manage feeds, Tagging style section** — one PR; both are dense,
   mechanism-heavy explainer text inside otherwise well-organized pages.
5. **"No redeploy" sweep** — mechanical, low-risk, ~10 pages, one sentence each. Can
   ride with PR 2 or ship standalone.
6. **Small Tier 2 cleanups** (Name-duplicate check, Community gaps, FP&A Buddy
   feedback, Archive backup card, MCP-setup disclosure header, Reader subscriber-access
   alert, Enrich archive) — one PR.

**Estimate: 5–6 PRs.**

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

## 7. Read on the proposed approval split

Mostly holds, one real correction:

- **Tier 3 → approval before shipping**: agree. Several Tier 3 items are audience
  judgment calls, not pure wording fixes — most centrally, should the System/Health &
  Maintenance group stay written for Brian-as-developer (he built this; he may
  genuinely want the file paths and env-var names as a quick reference) or get rewritten
  for Brian-as-site-owner? That's a product decision, not a copy fix, and belongs in
  front of Brian before batch 2 is even drafted, not just before it ships.
- **Tier 1 untouched**: agree.
- **Tier 2 "ships with a before/after report, no approval gate" — push back here.**
  A few Tier 2 items carry the same audience-judgment question as Tier 3, just in
  smaller doses: the "Users → MCP setup" disclosure and the "Railway Cron Service" card
  both raise "who is this actually for," and "FP&A Buddy feedback"'s "retrieval"/
  "Capture and triage only" and "Enrich archive"'s "server-side" could resolve as a
  clean one-word drop or could need a rethought sentence, depending on how far the fix
  goes. Recommendation: keep Tier 2 as non-blocking, but send batch 6's before/after
  report *before* merging rather than strictly after, since it's a mixed bag rather
  than uniformly mechanical.

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

## Known audit gaps

- ~150 per-item admin add/edit forms across Software/Communities/Resources/
  Thought-Leadership were sampled, not read exhaustively (see §2).
- The Communities "How this works" reference block's full body, and the Community
  profile edit page, were not fully audited — flagged for a closer pass before batch 3
  is drafted, not assumed clean.
