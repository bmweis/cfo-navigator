# AI surfaces brief

Raw material for the explainer pages that will hang off `/how-this-is-built`
(PR 34). This is facts, sources, and decisions — not prose, not a draft. Every
claim below is sourced to a file/function or a CLAUDE.md/ARCHITECTURE.md
passage; read those directly for the full write-up where noted.

`/tools/fpa-buddy/how-it-works` already exists and is linked from
`/how-this-is-built` as-is — see "1. FP&A Buddy" below for what it already
covers and what it doesn't, so a new page doesn't duplicate it.

---

## 1. FP&A Buddy

**Already has a public page — rewritten for a CFO audience, 2026-09.**
`webapp/app.py`'s `fpa_buddy_how_it_works()` route, at
`/tools/fpa-buddy/how-it-works`. The page was originally written for "a PM,
an engineer, or a technically comfortable CFO" and covered real mechanism
detail this brief's earlier version listed in full. Brian rewrote it
against the site's actual standing audience (CFOs and finance leaders) and
cut it roughly in half; see ARCHITECTURE.md's "CFO-audience rewrite
(2026-09)" bullet for the full before/after. It now covers, briefly:

- The three sources (archive/feeds/web) and that human curation, not a
  scrape, is what makes the archive worth retrieving from.
- That the web tier is restricted to a trusted-sites list, linked to
  `/current-feed`, with a pointer to `/how-this-is-built/web-search` for
  the Exa-vs-native-fallback engine detail (moved off this page — see
  below).
- Own-writing citations are labeled as Brian's own when cited.
- Why citations can be trusted, in brief — real Citations-API document
  blocks, mechanically verified, not self-reported.
- The Quick/Standard/Deep effort tiers, read live from
  `linklib.agent.EFFORT_SETTINGS`, with each tier's blurb now describing
  the job ("a fast read on something you mostly already know") rather than
  the model.
- The cost model: a monthly dollar cap per person (read live from
  `Library.get_default_ask_cap()`, printed inline), priced from real usage
  not a query count.
- A new "What it won't do" section: the model says so when sources don't
  cover a question, and can't cite outside the three listed sources.

**Cut in the rewrite — now genuinely NOT covered here, so a new page CAN
cover this without duplicating it:**
- The archive's content pipeline in any implementation detail — Claude for
  summaries/tags/dedupe judging, OpenAI `text-embedding-3-small` for
  semantic search merged with FTS5 via reciprocal rank fusion, Exa's search
  API repurposed for dead-link recovery, the Wayback Machine as a last
  resort, structured HTML extraction, per-fetch quality checks. All real,
  none of it retained after the rewrite — fair game for a future page.
- Exa vs. Claude's native `web_search_20250305` as the web tier's two
  interchangeable engines. This detail specifically now belongs to
  `/how-this-is-built/web-search` (see "2. Exa across four call sites"
  below), which this page links to rather than re-explains — **do not
  duplicate it back into a redrafted FP&A Buddy page.**
- Published-content ingestion (Brian's own writing joining retrieval) —
  `linklib.original_content_sync`, `articles.is_own_content`. Deliberately
  **not read by ranking at all** — `retrieve()`/`_rrf_merge()`/
  `Library.search()`/`Library.vector_search()` never touch the flag, so a
  mirrored article surfaces and ranks purely on merit; the flag only drives
  a "(own writing)" citation label (now mentioned briefly on the rewritten
  page, but not explained mechanically). See CLAUDE.md's "FP&A Buddy
  Published-Content Ingestion" bullet.
- The hybrid-retrieval design rationale itself (why RRF over picking one
  search strategy) — see "Rejected decisions" below.
- Conversation history/follow-up rewrite cost accounting
  (`embed_cost_usd`/`rewrite_cost_usd` breakouts).

**Model/mechanism:** `linklib/agent.py`'s `EFFORT_SETTINGS` maps each tier to
a model + per-tier archive/feed/web source counts + token budget — a
completely separate mapping from `linklib/models.py`'s curated registry (see
CLAUDE.md's "Adding a new Claude model — every touchpoint" section: there is
no single model picker for Buddy, and putting a model in the registry does
not make it reachable by a question).

**Cost tracking:** `ask_questions` table, per-question `cost_usd` plus
breakout columns (`embed_cost_usd`, `rewrite_cost_usd`, `exa_result_count`/
`exa_cost_usd`). Monthly caps: `users.ask_cap_usd` override, else
`settings['ask_default_cap_usd']`.

---

## 2. Exa across four call sites

**What it does, per site:**

1. **FP&A Buddy's web tier** — `linklib.agent.retrieve_exa()`. Searches Exa
   restricted to `preferred_sites.opml`'s domain list
   (`linklib.sources.preferred_domains()`), returns hits shaped like
   library/feed hits so they ride the same Citations-API document-block
   path. Best-effort: no key, empty question, network failure, or bad
   response all degrade to `([], 0, 0.0)` rather than blocking an answer.
2. **Reader backfill, domain-migration tier** — `linklib/domain_migration.py`.
   For a URL on a hand-confirmed migrated domain (e.g.
   `pointsandfigures.com` → `jeffreycarter.substack.com`,
   `avc.com` → `avc.xyz`, both in `linklib.pipeline._DOMAIN_MIGRATIONS`),
   searches Exa restricted to just that one destination domain for the
   article's stored title, then validates the candidate's title against the
   original (`_titles_match`, 0.7 word-overlap) before accepting it.
3. **Reader backfill, Medium-platform tier** — `linklib/medium_platform.py`.
   For a URL on a recognized Cloudflare-blocked host (`medium.com` and its
   subdomains, plus a small hand-curated custom-domain/other-blocked-host
   list), searches Exa **unrestricted** (no domain allowlist, since a Medium
   article can resolve to many different real hosts) and validates the
   same way. Also tries Exa's `/contents` endpoint directly against a
   known corrected URL before falling back to search-by-title
   (`fetch_content_by_url`).
4. **Feature-scan vendor research** — `linklib/feature_scan.py`. Restricted
   to the vendor's **own** domain (not the OPML trusted-sites list), one
   search per sourcing-hierarchy tier (changelog/help-center/product-page/
   press-release/etc.), tagging each hit with an inferred tier from its URL
   path. **This is a manual script tool, not wired into any live web
   route** — run by hand against 1-2 tools at a time, not registered in
   `/admin/system/scripts` (confirmed: no reference to `feature_scan` in
   `webapp/app.py` beyond a comment).

**Fallback cascade (backfill only):** domain-migration tier → Medium-platform
tier → Wayback Machine (`linklib/wayback.py`) → give up. Exactly one
`content_refetch_log` row per attempt regardless of how many tiers were
tried. FP&A Buddy's web tier has its own, separate two-engine cascade (Exa →
Claude's native `web_search_20250305`), governed by one setting
(`exa_enabled`) that gates **all four** Exa call sites at once but has a
fallback only for #1 — turning Exa off silently disables #2 and #3 with no
substitute (see `_ai_exa_config_html`'s banner copy on `/admin/system/ai`).

**Allowlist mechanism:** `preferred_sites.opml` (dual-purpose: also the
Reader's Feed-view subscription list) feeds `linklib.sources
.preferred_domains()`, an `lru_cache`d parse of the OPML file's
`<outline>` elements. Used by call site #1 only — #2/#3 are single-domain-
restricted or unrestricted by design (see above), #4 is vendor-domain-
restricted.

**Cost:**
- `compute_exa_cost(endpoint="search", num_results)` — a fixed
  $7.00/1,000 requests covering the first 10 results, +$1.00/1,000 per
  result over that. **Billed on real results delivered, even a zero-result
  miss** (a miss still costs the base rate, since `overage = max(0,
  num_results - 10)` floors at 0, not the whole call at $0).
- Call site #1: `Answer.exa_result_count`/`exa_cost_usd` → `ask_questions`
  columns, folded into the turn's `cost_usd` total that the monthly cap
  sums.
- Call sites #2/#3: `content_refetch_log.exa_cost_usd` — accumulated across
  every Exa call one `backfill_article_content()` attempt makes, logged on
  whichever single row that attempt ultimately produces.
- Call site #4: computed and returned/printed by the script
  (`FeatureDraft.exa_cost_usd`), **not written to any DB table** — it's a
  manual, occasional tool, not a recurring cost center.
- **No pricing API exists for Exa** (unlike the Models API for Claude), so
  `EXA_PRICING`'s accuracy is a dated manual attestation:
  `/admin/system/ai`'s "Exa pricing freshness" banner
  (`linklib.pricing.exa_pricing_review_is_stale`, 90-day window,
  `settings['exa_pricing_last_verified']`) — amber past 90 days or never
  reviewed, with a "Mark reviewed" action. Only the `search` endpoint is
  modeled; `EXA_PRICING`'s own comment says explicitly not to add a row for
  Contents/Deep Search/Deep-Reasoning Search/Answer until a caller actually
  needs one.

**What was considered and rejected:**
- The domain-migration and Medium-platform tiers are explicitly **not** a
  general search fallback — trusted only because a human confirmed the
  destination domain (or, for Medium, validates every candidate's title
  against the original) before it ships. `domain_migration.py`'s own
  module docstring states this directly.
- A browser User-Agent swap was tested against real 403s from the
  Cloudflare-blocked domains and **confirmed not to fix them** — Cloudflare
  fingerprints the TLS/connection layer, not the UA string. Kept as the new
  sitewide default anyway (no downside), but the PR says plainly it doesn't
  solve what it was proposed for. See CLAUDE.md's "Phase 5b second
  follow-up" bullet.
- Publisher-boilerplate stripping on the Medium-platform tier's live-refetch
  path was explicitly scoped out as "a future Reader-quality pass, not this
  one's job."

---

## 3. Profile and description generation

**What it does.** Four Claude-drafted fields per Software tool
(`linklib/enrich.py`): `generate_tool_description` (Description + Short
summary), `generate_tool_agent_taxonomy` (the "how agents are involved"
note — real Citations-API grounding against the vendor's own fetched page),
`generate_tool_differentiation` (competitive read, still plain JSON, no
citations mechanism), and `generate_competitor_matches` (judges candidate
competitor matches). Communities get one combined call,
`generate_community_profile`, drafting all 23 profile fields (12 of them
narrative/fabrication-risky) in a single response, plus
`generate_community_listing` for the public basic-listing auto-fill.
`generate_compare_summary` (surface 4, below) is enrich.py's fifth
generator.

**Claude-drafted vs. hand-written:** every field above starts as an AI
draft, but nothing publishes unreviewed by default — see review states,
next.

**Review states — the "radical-transparency" three-state standard**
(`linklib/gates.py`, PR A 2026-09): every AI-drafted field is always
**Verified** (content, no label), **Pending** (content + a visible "under
review"/"unverified, visible to visitors" label — content is shown either
way, never hidden), or **Empty** (a placeholder, plus a "go fill this in"
prompt for an admin). This superseded an earlier design that hid an
unverified/low-confidence note from public visitors entirely (the "Agent
taxonomy publish gate," triggered by a real incident — see "Rejected
decisions" below) — the current standard shows the content and labels it,
rather than hiding it.

`needs_verification` means: this field was drafted or refreshed by AI and
hasn't had a human click "Mark verified" since. It's per-field for Software
(`description_needs_verification`, `agent_taxonomy_needs_verification`,
`competitive_differentiation_needs_verification`) and one whole-profile flag
for Communities (`community_profiles.needs_review`) — a documented, single
deliberate divergence (N independent flags vs. one flag driving N badges),
not an oversight (see CLAUDE.md's "Communities... one flag driving N
badges" note).

A separate, independent fact: **AI confidence** — Claude's own
self-reported "confident: true/false" per field, rendered as a permanent
"Claude confidence: Yes/No/Not yet assessed" line, shown regardless of
review status (a field can be verified AND low-confidence, or unverified
AND high-confidence — the two facts are tracked and shown separately).

**Grounding/citations:** Agent taxonomy and Description both use the
Citations API (`linklib/citations.py`) against a real fetched page —
citations are mechanically extracted, not self-reported, closing a real
fabrication incident (see below). Competitive differentiation has **no**
citations mechanism as of this writing — deliberately deferred, "pending a
decision on whether it gains real fetched competitor content to ground on."

**Cost tracking:** `enrichment_cost` table via `Library.record_enrichment_cost`
— every real generation call logs `input_tokens`/`output_tokens`/
`cost_usd`. No per-user cap (this is Brian's own overhead spend, not a
member-facing budget) — visible at `/admin/overhead-spend`.

**What was considered and rejected — the most consequential incident on
this surface:**
- **The Abacum fabrication incident.** A live agent-taxonomy note
  referenced a nonexistent page and quoted invented-sounding language. Root
  cause: the generation prompt asked the model to self-report
  `confident: false` when unsure, but **nothing ever gated on that
  signal** — even a self-flagged low-confidence draft published with only
  a small badge as the visible difference. Fixed first as an immediate
  publish gate (hide from visitors entirely when unverified), then
  superseded by real Citations-API grounding (mechanically verified
  citations, not a self-report), then superseded again by the current
  "show it, label it" radical-transparency standard once citations made
  hiding unnecessary. Three real design iterations on the same problem,
  each superseding the last — not one decision.
- **JSON-plus-citations turned out to be structurally incompatible.**
  Anthropic's Citations API can't be combined with strict JSON-schema
  output. An early citations rollout asked for both at once, and the model
  improvised its own literal `<cite index="...">` pseudo-tag text into
  public-facing fields when it had no clean way to satisfy both
  constraints. Fixed by dropping the JSON contract for
  citation-bearing fields (plain prose + trailing `"KEY: value"`
  sentinel lines, parsed deterministically) rather than trying to patch
  the prompt. See CLAUDE.md's "Citation-tag investigation" bullets for the
  full incident and its two follow-on fixes (Description's token budget was
  separately found to be too tight for the new verbose format, truncating
  answers mid-sentence).
- **A silent regression, caught and reversed**: `_seed_toolbox()`'s startup
  sync was found to be reverting 81 of 148 tools' AI-regenerated
  descriptions back to the original seed blurb on every deploy — a
  same-name/same-URL sync path that was only ever meant to fix a
  typo'd name, not overwrite a real regenerated description. Fixed by
  retiring `description` from that sync entirely.

---

## 4. Matchmakers and compare summaries

**What it does.** Two conversational assistants (`linklib/matchmaker.py`) —
`/tools/software/find` and `/tools/communities/find` — that ask a few
clarifying questions, then narrow to 2-3 best-fit suggestions. No
retrieval layer at all: the entire directory (~150 tools, ~38 communities)
rides as full context on every turn (`_build_communities_context`/
`_build_software_context`), cached server-side via Anthropic prompt
caching since the context is identical turn to turn. `generate_compare_summary`
(`linklib/enrich.py`) is a separate, smaller generator: a 1-3 sentence
AI-written orientation note above the Compare page's side-by-side table,
built from the same `CompareEntity`/`CompareField` data the page already
assembled — no separate fetch.

**The retired 10-dimension weighted quiz.** Communities' matchmaker
**replaces** a 4-question quiz at the same URL (`GET
/tools/communities/find`) that filtered the directory, then re-sorted
results by a weighted match score across 10 fixed dimensions (defined in
`_WEIGHT_DIMENSIONS`, 8 profile-sourced + 2 more), each with its own
controlled-vocabulary `*_tags` JSON column on `community_profiles`. **Removed
entirely, by explicit decision**, once it was confirmed the Matchmaker never
read any of it and nothing else did either: the weighting UI/admin panel,
every Python helper, all 8 `*_tags` columns those dimensions wrote, plus 4
already-retired `*_tags` columns from before it, and the one-off
classification scripts that populated them. See ARCHITECTURE.md's
"Best-fit weighting (historical...)" section for the full dimension list
and why each was scored the way it was — kept as a historical record of a
system that no longer exists.

**Dollar caps:** both matchmakers (`kind='software'`/`'community'`) share
ONE monthly budget by design — a session/user's cap is the sum of `cost_usd`
across both kinds, not two independent pools
(`matchmaker_cost_this_month[_session]`). No login required (unlike FP&A
Buddy) — an anonymous visitor's spend keys off the `cfo_visitor` session
cookie, falling back to a per-user cap only when signed in.
`users.matchmaker_cap_usd` override, else
`settings['matchmaker_default_cap_usd']`.

**Compare summary caching and cost cap:** `compare_summary_cache`, keyed by
entity set + a content hash of what was actually summarized — a genuine
edit invalidates the cache, an unrelated edit doesn't. A **small, shared
daily budget** (`Library._DEFAULT_COMPARE_SUMMARY_CAP_USD = $2.00`,
admin-adjustable via `settings['compare_summary_default_cap_usd']`) caps
total spend across ALL compare-summary generations per day, not per user —
a cap hit renders a labeled note instead of failing the page.

**The standing footnote.** Every compare summary carries: *"AI-generated
summary, not human-verified."* — with a second clause, *"Includes catalog
content still under review,"* appended only when at least one summarized
entity has unreviewed content (`_CMP_SUMMARY_FOOTNOTE_PREFIX` vs.
`_CMP_SUMMARY_FOOTNOTE_PREFIX_VERIFIED`), plus a "Flag an issue" link to a
public feedback form (`compare_summary_feedback`, reviewed by hand at
`/admin/compare-summary-feedback` — no automated action taken on a flag).

**Unverified-content disclosure inside the matchmakers themselves.** The
matchmakers also surface catalog content that hasn't been reviewed — a
Software field is marked inline ("(unverified)"), a Community's whole
unreviewed profile gets one leading note per community, and the system
prompt gets a standing instruction to call out unverified content in the
synthesized answer rather than presenting it as confirmed
(`_build_communities_context`/`_build_software_context` returning
`(context, has_unverified)`, `_build_system` appending the disclaimer only
when at least one marker is present).

**What was considered and rejected:**
- The 10-dimension weighted quiz itself, as above — a complete, working
  ranking system, retired outright rather than kept alongside the
  Matchmaker, because nothing consumed it.
- Communities' unreviewed content is disclosed **per-community, not
  per-field** in the matchmaker context, deliberately — a design choice
  distinct from the Software matchmaker's per-field marking, following the
  same one-flag-drives-everything shape `community_profiles.needs_review`
  already has everywhere else.

---

## Other rejected decisions worth knowing about (found during the sweep, not tied to one surface above)

- **The Archive Queue retirement (2026-09, PR 3).** An AI-enriched
  proposal/review pipeline (RSS scan + Claude keep/skip advisory + a full
  review UI) was retired outright — not paused, not simplified — after a
  production query found it at 5,508 rows, **100% dismissed, 0 pending, 0
  member submissions ever**, dormant since 2026-06-28. Replaced by nothing
  more than a plain email notification on the one live caller
  (`/library/submit`). The standing lesson recorded for this one: "re-check
  the actual submission volume first" before ever rebuilding something like
  it.
- **A confirmed false lead in the build brief for this PR**: the brief
  asked to note "bring-your-own-Anthropic-key for non-admin MCP users,
  killed after inspection showed a third-party credential couldn't express
  this app's own permission model." **No such decision exists anywhere in
  this codebase** — searched `CLAUDE.md`, `ARCHITECTURE.md`, and every
  `linklib`/`webapp` source file for any BYO-key/bring-your-own-key/
  per-user-credential concept; nothing matches. The MCP server's actual,
  documented auth model (`api_tokens`, a dedicated per-user token table,
  sha256-hashed, resolved to a live `users.id` + current role on every
  call) never involved letting a caller supply their own Anthropic key at
  all — there was nothing here to reject. Flagged per the standing
  instruction to report what's found, not what makes a good story.
- **Hybrid retrieval design** (FTS5 + vector search merged by reciprocal
  rank fusion) was chosen explicitly over picking one search strategy —
  semantic search catches an article whose wording doesn't match the
  question's, keyword search catches exact terms semantic search can miss.
  Embeddings can't be written from a SQL trigger (a network call can't run
  inside one), so vector search is deliberately eventually-consistent by
  design: embed-on-save handles new articles, a one-off backfill script
  covers the rest.
- **Published content gets no ranking boost.** `articles.is_own_content` is
  read only by citation labeling (`_build_source_documents`/
  `extract_citations`) — never by `retrieve()`, `_rrf_merge()`,
  `Library.search()`, or `Library.vector_search()`. An inline first-person
  prose mention ("as I wrote...") was explicitly considered and rejected as
  a voice-integrity risk during design — a small citation-list label was
  judged the safer choice.
