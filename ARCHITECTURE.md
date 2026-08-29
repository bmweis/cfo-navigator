# Architecture

A living technical overview of CFO Navigator / bmweis.com, written for someone
seeing the codebase for the first time. It covers how the system is put
together and *why* it's put together that way — not a line-by-line tour.
Keep it current: see the **Documentation** section of `CLAUDE.md` for the
maintenance rules that apply to every PR.

## 1. System overview

The whole site is **one FastAPI monolith** (`webapp/app.py`) over **one SQLite
database** with FTS5 full-text search (`linklib/db.py`), running as a single
process on Railway. All HTML/CSS/JS is inline in Python strings — there is no
template framework and no frontend build step. The public face (bio, thought
leadership, CFO Toolbox, contact) needs no login; a signed-cookie login gates
the member tools (article archive, feed reader, FP&A Buddy) and a ~40-page
admin back office. Railway auto-deploys from `main` (all changes ship via PR),
the database lives on a Railway volume so it survives deploys, and Cloudflare
sits in front of Railway serving the `bmweis.com` domain.

```mermaid
flowchart LR
    B["Browser"] --> CF["Cloudflare<br/>bmweis.com"]
    CF --> R

    subgraph Railway ["Railway (single instance, auto-deploys from main)"]
        R["FastAPI app<br/>webapp/app.py<br/>uvicorn, Dockerfile"]
        V[("SQLite + FTS5<br/>library.db on a<br/>Railway volume")]
        R <--> V
    end

    R -->|"Q&A, enrichment, rewrite,<br/>dedupe verification"| A["Anthropic API"]
    R -->|"web retrieval<br/>(domain-restricted, preferred)"| X["Exa API"]
    X --> W["Trusted sites from<br/>preferred_sites.opml"]
    A -.->|"native web_search tool<br/>(fallback: Exa off or no key)"| W
    R -->|"RSS/Atom + article<br/>full-text fetches"| F["Publisher sites"]
    R -->|"outbound email"| G["Gmail REST API"]
    R -->|"daily DB snapshot"| D["Google Drive"]
    GH["GitHub Actions<br/>backup.yml, daily cron"] -->|"POST /admin/backup-now<br/>(X-Save-Token, direct to<br/>Railway origin — bypasses CF)"| R
```

Notes on the edges:

- **Cloudflare** is infrastructure outside this repo — nothing in the codebase
  references it; its config lives in the Cloudflare dashboard. Current setup
  (verified July 2026): both `bmweis.com` and `www` are **proxied** (orange
  cloud), so Cloudflare is on the request path, not just DNS. SSL/TLS mode is
  **Full** — deliberately not Full (strict), per Railway's guidance about cert
  renewal windows. No custom cache rules or WAF rules and no edge rate
  limiting (stock defaults — HTML isn't cached, so the app's
  `/admin/*` `no-store` behavior is unaffected); **Bot Fight Mode is on**.
  Always Use HTTPS and HSTS are enabled (max-age 6 months, includeSubDomains,
  preload deliberately off). One edge **Redirect Rule** 301s
  `www.bmweis.com/*` → `bmweis.com/$1`, which wins over the app's
  canonical-host middleware for proxied traffic — the middleware still covers
  the legacy `*.up.railway.app` hostname and any traffic that reaches the
  origin directly.
- **The Railway origin is publicly reachable** (`*.up.railway.app` still
  serves, and Railway has no built-in IP allowlisting), so direct requests
  bypass every edge protection — the redirect rule, Bot Fight Mode, HSTS.
  This is an **accepted risk**; the only real fix would be a Cloudflare
  Tunnel, which isn't implemented. Consequence for app code: only
  `CF-Connecting-IP` (set by Cloudflare on proxied requests) is a trustworthy
  client IP — `X-Forwarded-For` can be spoofed by anyone hitting the origin
  directly (see Known limitations).
  **This is also why `.github/workflows/backup.yml` (Phase O) deliberately
  targets `cfo-navigator-production.up.railway.app`, not `bmweis.com`:** the
  first live run against `bmweis.com` got a `403` from Cloudflare's Bot
  Fight Mode before the request ever reached the app (confirmed via the
  app's own auth path, which returns `401` for a bad token, never `403` —
  so this wasn't the app rejecting the token). Cloudflare was never meant to
  gate this one authenticated backend-to-backend call — `X-Save-Token`
  remains the actual auth boundary either way — so the Action routes around
  the CDN on purpose rather than trying to carve out a Bot Fight Mode
  exception for GitHub's rotating runner IP ranges. This is the first
  deliberate consumer of the "accepted risk" above, not an accident.
- **The volume path** is Railway configuration, not code: the app reads
  `LINKLIB_DB` (default `./library.db`); production points it at the mounted
  volume. The DB is deliberately not in git — it's personal reading history.
- **Web retrieval never leaves the allowlist, whichever mechanism handles
  it.** `preferred_sites.opml` (the same file that drives the `/feed`
  reader) restricts both paths: Exa's `includeDomains` on the preferred
  path, and `allowed_domains` on the native `web_search_20250305` tool on
  the fallback path. Exa — a direct `/search` API call from
  `linklib/agent.py` (`retrieve_exa`), not an Anthropic-hosted tool — is
  preferred whenever the `exa_enabled` setting is on and `EXA_API_KEY` is
  set (Phase 2's migration, later made toggleable in Phase 7). Otherwise
  Claude's native tool (Anthropic-hosted, restored in Phase 7 after Phase 2
  had removed it outright) steps in instead. Exactly one of the two runs
  per question — see `linklib.agent._web_provider` for the unified
  condition, and `/admin/exa-settings` for the toggle and its connection
  test.
- **Email is the Gmail REST API, not SMTP** — Railway's Hobby plan blocks SMTP
  ports. Every send is best-effort and must never block the underlying DB
  write; failures land in the `email_failures` table and surface as an admin
  badge instead of dying in a log. At the DNS level (Cloudflare-managed) the
  domain has SPF and DKIM in place, plus DMARC in `p=none` monitoring mode —
  collecting reports, not yet enforcing.
- **The daily (bumped from weekly, 2026-08) Drive backup is triggered by a
  GitHub Action, not a Railway cron service.** `.github/workflows/backup.yml`
  calls `POST /admin/backup-now` on a daily schedule (`X-Save-Token` auth,
  same as RUNBOOK.md's manual curl example) — this is Phase O's fix for the original
  mechanism (`linklib.backup.maybe_backup`, debounced and only fired as a
  side effect of ~18 admin/save routes in `webapp/app.py`) never getting a
  reliable weekly opportunity to run in practice. Those ~18 call sites are
  unchanged and still fire opportunistically as a harmless bonus trigger.
  Every attempt from either path — success or failure — is logged to the
  `backup_log` table (see the Site operations table below) and surfaced on
  `/admin/library/backup`'s status banner + history table; the Action's own
  run history in the repo's Actions tab is a second, independent signal that
  catches the case where the site itself is unreachable and there's no
  in-app record at all.

## 2. Database

**There is exactly one database for the whole app: a single SQLite file,
`library.db`** (path configurable via `LINKLIB_DB`), containing every table
the app uses — content archive, FP&A Buddy, accounts, the entire CFO Toolbox
(including Communities), site operations, and the `/play` game. There is no
separate database for any one feature — in particular, **Communities
(`communities`, `community_categories`, `community_profiles`,
`community_gap_submissions`, `community_profile_views`) lives in this same
`library.db` file, in the same tables list below, not a database of its own.**
Every table in the file, grouped by feature area:

| Group | Tables |
|---|---|
| Content spine | `articles`, `articles_fts`, `articles_vec`, `article_embeddings`, `enrichment_cost`, `library_queue`, `dedupe_decisions`, `read_later`, `content_refetch_log`, `url_correction_log` |
| FP&A Buddy (Ask) | `ask_questions`, `ask_feedback` |
| Chat Matchmaker | `matchmaker_questions` |
| Accounts | `users`, `password_reset_requests` |
| CFO Toolbox | `tools`, `tool_categories`, `tool_audit_log`, `benchmarks`, `tool_leads`, `communities`, `community_categories`, `community_audit_log`, `community_profiles`, `community_gap_submissions`, `community_profile_views`, `field_reviews`, `narrative_review_log` |
| Thought Leadership | `thought_leadership`, `original_content` |
| Site operations | `settings`, `contacts`, `email_failures`, `archive_audit_log`, `contact_audit_log`, `backup_log`, `integrity_check_log`, `job_run_log` |
| "Sail, Don't Row" (`/play`) | `game_rank_settings`, `game_runs` |

`linklib/db.py` defines and migrates all of it — the `Library` class is the
only write path. The schema script runs on every boot (`CREATE TABLE IF NOT
EXISTS`) followed by an **additive-only migration list** of `ALTER TABLE ADD
COLUMN` statements that ignore "already exists" errors. There are no declared
foreign-key constraints — relationships below are by convention (`user_id`,
`tool_id`, `item_id` columns), enforced in code.

**`/admin/system/database`** (System nav group) renders a live Mermaid ER
diagram of this same schema — table names, key (PK/FK) columns, and row
counts — generated by introspecting `sqlite_master`/`PRAGMA table_info` at
request time, never by parsing this document. It's a self-updating visual
complement to the tables below, not a replacement for them: summary-level
only, and since this schema has no real `FOREIGN KEY` constraints, the
relationship lines it draws come from a small hand-maintained map
(`_DB_RELATIONSHIPS` in `webapp/app.py`) that mirrors the "by convention"
relationships described in this section — update both together. FTS5's and
sqlite-vec's shadow tables (`articles_fts_data`/`_idx`/`_docsize`/`_config`,
`articles_vec`'s equivalents) are filtered out of the diagram since they're
SQLite implementation detail, not schema.

The diagram's click-to-expand modal (`_diagram_lightbox_html` in
`webapp/app.py`, shared with the FP&A Buddy flowchart below) got real
pan/zoom in a Phase I follow-up, after direct testing showed a bigger
static view alone didn't solve anything — individual table fields on the
~39-table diagram stayed illegible even fully expanded, because the
problem was density/layout, not size. Pan/zoom/fullscreen-style navigation
is `svg-pan-zoom` (CDN script, MIT, same no-build-step pattern as
Cropper.js and Mermaid itself) wrapping the already-rendered SVG. The
modal also carries a search box: typing a table name finds its erDiagram
entity node (`g[id^="entity-{name}-"]`, matched by stripping non-
alphanumeric characters the same way Mermaid sanitizes the id), gives it a
coral highlight border, and pans/zooms the view to center it — cleared
when the search box empties or the modal is closed. The FP&A Buddy
flowchart below gets the same pan/zoom but not the search box, since a
flowchart has no "find a table" concept.

The Content volume table below the diagram is grouped into collapsible
sections (same `<details class="admin-group">` disclosure the admin hub's
own nav groups use), collapsed by default — the flat 39-row table pushed
the diagram far down the page. `_TABLE_GROUPS` in `webapp/app.py` is the
grouping map; `_grouped_table_sections` buckets the live schema against
it and puts anything the map hasn't caught up with in a trailing "Other"
group rather than dropping it silently. A separate "Cost & spend" section
with dollar totals for `enrichment_cost`/`manual_overhead` used to live on
this page too — removed as a duplicate of `/admin/overhead-spend`, which
already owns cost reporting; the page now just links there, and both
tables appear in the regular grouped listing with their row counts like
any other table.

**`/admin/system/page-index`** (System nav group) is the same live-introspection
pattern applied to routes instead of tables: on every page load it walks
`app.routes`, keeps GET routes whose `response_class` is `HTMLResponse`
(skipping POST-only action routes, redirect stubs, JSON/AJAX APIs, file
downloads, and other non-page endpoints), and reads each page's width tier
(`page-full`/`page-grid`/`page-form`/`page-admin`, or "custom
exception" for `/read` (the merged Reader shell — see the Reader merge
section below) — see BRAND.md §5 for the tier system itself) straight from
that route's own source via
`inspect.getsource` (following one hop into a directly-called helper function
when a route builds its body that way, e.g. `/play` via `_sdr_build_body`).
Any page route whose source carries no recognized tier class is flagged —
this is the actual point of the feature: it turns "did every page get
tiered," a one-time manual audit (Phase 9), into something that catches a
newly added, never-tiered page automatically. `_page_index_snapshot()` in
`webapp/app.py` is the single source; no maintained list of pages or tiers
exists elsewhere.

**`/admin/system/scripts`** (System nav group, Phase N) is the opposite design
choice from the two pages above — a static, hand-maintained registry
(`_SCRIPT_REGISTRY` in `webapp/app.py`), not a live-introspected one. Phase N's
investigation weighed scanning `scripts/` for structured docstring metadata
against a hand-maintained list and picked the latter: the recurring/diagnostic
script corpus is small (a dozen scripts) and changes rarely, so a static list is
cheap to keep honest, while scanning would need a new docstring convention
retrofitted onto every script plus ongoing discipline to keep it valid — more
mechanism than this corpus's size or churn justifies. Same precedent as
`_OPEN_SOURCE`/`/admin/open-source` (§5 below), which is also hand-maintained
reference content. Lists each script's purpose, cadence, required env vars, and
exact invocation, split into two buckets: "Recurring & actively useful" (run by
hand on their own cadence — logo/screenshot backfills, seeders, enrichment
backfills, the MCP server, `generate_brand_docs.py`, orphaned-category/community
reports) and "Reusable diagnostic" (built for one investigation but generalized
— `diagnose_cookie_banner.py`, `verify_screenshot_capture.py`). One-time
migrations and closed-investigation reports that already did their job live in
`scripts/archive/` instead (moved via `git mv`, history preserved) and are
deliberately excluded from this page. CLAUDE.md's Documentation section carries
the standing rule keeping the registry in sync: any PR touching `scripts/`
(new script, or a changed purpose/env var/invocation for an existing one)
updates this page in the same PR, and any PR that finishes a recurring script's
job archives it and drops its entry, also in the same PR.

**`/tools/fpa-buddy/how-it-works`** (added in the Exa migration's Phase 4 as
`/admin/system/how-fpa-buddy-works`; moved from the System nav group into
its own "FP&A Buddy" section in Phase 6; made public and moved off
`/admin/*` entirely in the explainer-page follow-up round) is a
plain-language technical explainer of FP&A Buddy's mechanism — retrieval
tiers (library/feed/web), the Quick/Standard/Deep effort tiers, citation
verification, the archive's own content pipeline, and the per-user dollar
cost cap — written for a technically comfortable reader (a PM, an
engineer, or a CFO) who wants the real mechanism, not marketing copy. It
plays the same reference-doc role `_COMMUNITIES_REFERENCE_HTML` plays for
the Communities feature, but as its own page rather than a collapsible
block on a working admin page, since explaining the mechanism IS this
page's whole purpose. Per-tier source counts read live from
`linklib.agent.EFFORT_SETTINGS` and the default monthly cap reads live
from `Library.get_default_ask_cap()`, so neither can drift out of sync
with the code the way a hand-typed number would; model names are
deliberately described qualitatively (fastest/balanced/most-capable)
rather than pinned to a canonical model ID, since those rotate
independently of this page. Phase 5 added a concept-level Mermaid
`flowchart` above the prose (question → library/feed/web → synthesis →
cited answer, no token counts or API names) — deliberately not the
developer-grade sequence diagram above, which stays the reference for
anyone debugging the actual request flow. Renders via the same
CDN-hosted `mermaid.min.js` used by `/admin/system/database`'s ER diagram,
not a new dependency. **Made public in the explainer-page follow-up
round**, per Brian's ask that the page be a showcase reachable by anyone
with the link (not linked from primary public nav, no `noindex`, same
discoverability tier as a thought-leadership sub-page). A pre-move content
audit found three admin-insider assumptions baked into the page and fixed
each: the "← Admin" breadcrumb (a public visitor has no admin access to
return to) became "← FP&A Buddy", matching every other public sub-page's
own back-link convention; the inline `/admin/exa-settings` and
`/admin/users` links were de-linked to plain prose ("the site admin"),
since a public reader would only ever hit a login wall on either; and the
`ARCHITECTURE.md` link was removed outright, since this repository is
private and the link 404s for exactly the outside audience the page is
now written for. The admin dashboard's FP&A Buddy card (`_FPA_BUDDY_TOOLS`)
now points at this same public URL instead of hosting a separate
admin-only copy — no route lives at the old `/admin/system/*` path
anymore. The "Which engine handled this answer?" and "Why the citations
can be trusted" callouts were also reformatted from dense paragraphs to
bold-lead-in bullets, matching the "Where an answer's sources come from"
list directly above them; and the flowchart's `Quick / Standard / Deep`
annotation node — previously a single dotted edge into `Claude` that
Mermaid's layout rendered as a box disconnected below the main flow — now
fans dotted edges into `Library`/`Feed`/`Web` (the same three tiers its
label actually describes), landing it as a peer of the question node
instead of an orphan; the diagram frame is also wrapped in a
`max-width:680px` container so it no longer stretches to the full page
column regardless of the SVG's actual rendered size. **Width-tier fix
(follow-up):** the page carried `.page-admin` (~1400-1600px per BRAND.md's
layout system) verbatim from the `/admin/system/*` template it was
originally built under, while every other `article-atlantic` long-form
page (Growth Engine Ratio, AI Hackathon Playbook, Connecting Claude to
NetSuite) pairs `article-atlantic` with `.page-full` (~1800-2000px) —
`article-atlantic` itself carries no width of its own, so the mismatch
went unnoticed until the page was compared side by side with those. Fixed
by switching the outer class to `.page-full`, confirmed with a live
bounding-box measurement showing the `.tool-prose` reading column is now
identical in width and position to the reference page.

**`/admin/exa-settings`** (Phase 7, FP&A Buddy nav group) is the Exa kill
switch: an `exa_enabled` toggle (`settings` table, `Library.get_exa_enabled`/
`set_exa_enabled`, defaults on) plus a "Test connection" action that fires
one real, minimal Exa `/search` call and reports pass/fail — manual and
on-demand only, never a background job, via `linklib.agent.test_exa_connection`.
The page also flags when `EXA_API_KEY` isn't set on the host at all, since
that's an independent condition from the toggle and an admin could
otherwise be confused about why Buddy is using the fallback. Not persisted
to a cost ledger — the test's tiny real cost (via `compute_exa_cost`) is
only surfaced in the result, not written to a table, since it's a rarely-
used manual check rather than a per-turn or overhead cost.

### Content spine

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `articles` | The archive: ~1,500+ curated articles. **URL is the natural key** (`UNIQUE`, normalized) — upserts merge tags and fill empty fields, never duplicate. | `url`, `summary` (Claude-generated, the member-facing asset), `content` (fetched full text — internal input only, never served), `tags_json`/`tags_text` (structured list + flattened copy for FTS), `enriched`/`enrich_model`/`enrich_rules` (provenance), `in_scope`/`scope_reason` (off-audience review flags), `needs_content_check`/`content_check_reason` (durability audit item 1: set by `ingest_url` right after a fresh fetch fails `extract.assess_extraction_quality()` — never blocks the save, only flags it; cleared by `set_article_content_html` the moment a later backfill succeeds) |
| `articles_fts` | FTS5 virtual table (`content='articles'`, porter tokenizer) over title/author/source/summary/content/notes/tags_text. | Kept in sync by three triggers (`articles_ai`/`_ad`/`_au`) on insert/delete/update — no manual reindex, ever. |
| `articles_vec` | `sqlite-vec` vec0 virtual table (#93) — one embedding vector per article, `rowid = articles.id` (same external-content-by-rowid idiom as `articles_fts`, minus trigger sync — see §4, "Hybrid retrieval..."). Powers the vector half of hybrid retrieval. | `embedding` (`float[1536]`, OpenAI `text-embedding-3-small`) |
| `article_embeddings` | Companion ledger table (#93): which articles are embedded, with what text, and at what cost. Also **an overhead-cost ledger** for embed-on-save/backfill spend — never summed into `ask_questions`, never counts toward a user's Ask cap. Its sibling ledger, `enrichment_cost` (#105), covers enrichment spend; the two stay separate rather than sharing a schema — see §4, "Embedding cost is split by who pays for it" and "Enrichment cost gets its own ledger, not a shared one" below. | `article_id` (PK), `content_hash` (of the exact embedded text — detects staleness after an edit), `model`, `input_tokens`, `cost_usd` |
| `enrichment_cost` | Overhead-cost ledger for `linklib.enrich.enrich()` calls (#105). Unlike `article_embeddings`, this is **append-only**, not upserted — an article can be enriched more than once (backfill force-reruns, a rules-version bump), and each call's real cost stays in history. `article_id` is nullable: `linklib/queue.py`'s pre-save enrichment (a candidate enriched before it's queued or promoted) has no `articles.id` yet, but the API call still cost real money even if the candidate is later dismissed. | `id` (PK, autoincrement), `article_id` (nullable), `model`, `input_tokens`, `output_tokens`, `cost_usd` |
| `library_queue` | Staging area for proposed additions (RSS scan, sitemap backfill, reader submissions). Candidates arrive enriched-but-unsaved for review; promoting moves the row into `articles`, preserving enrichment already paid for. | `url` (unique, same natural key), `origin` (`feed` \| `backfill:<source>` \| `submission:<who>`), `status` (`pending` \| `dismissed` — dismissed rows stay, so a rejected candidate is never re-proposed) |
| `dedupe_decisions` | Curator verdicts on near-duplicate *pairs*, keyed by the sorted URL pair. Suppresses already-judged pairs from future scans and teaches the Claude verifier. | `pair_key` (unique), `verdict` (`dup` \| `distinct`) |
| `read_later` | Per-user private bookmark list, never shared or mixed into the archive. `content`/`content_html` (2026-08 follow-up) cache a save-time fetch the same way `articles` does — see the "Read Later content caching + manual refresh" write-up below — write-once-on-empty (never blanked by a failed re-fetch), replaceable via the per-item "Refresh" action (`Library.update_read_later_content`). | `user_id` + `url` (unique together — enforced by a post-migration index because the column arrived by migration) |
| `content_refetch_log` | Per-attempt audit trail for the Reader content-structure backfill (Phase 5b) — one row per `linklib.pipeline.backfill_article_content()` call, success or failure, shape mirrors `backup_log`. A re-run after a stop or crash adds new rows rather than overwriting old ones, so a flaky source's full history stays visible; `Library.content_refetch_failure_counts()` reads only the latest attempt per article so a since-fixed failure doesn't keep inflating the tally, and `Library.content_refetch_failure_domains()` groups the same latest-attempt set by URL host so a source-wide problem (one site blocking/throttling this tool) is visible as a cluster, not N identical-looking rows. No SQL-level FK to `articles` (same convention as `tool_audit_log`'s `item_id`). Also backs the "needs manual review" capped-retry tier (Phase 5b follow-up #2, see the write-up below) — `Library._manual_review_article_ids()` counts attempts per article *since its last `url_correction_log` row* (or ever, if never corrected). A THIRD `status` value, `'accepted'` (durability audit item 4), is the "accept as final" override — see the write-up below — and composes with `_manual_review_article_ids()` for free: that query already only looks at the most recent attempt and requires `status='failure'`, so an `'accepted'` row as the latest attempt drops the article out of the manual-review list without any change to that query; `articles_needing_content_backfill()`'s default scope and `count_content_backfill_remaining()` separately exclude the same latest-row-`'accepted'` set (`Library._accepted_content_ids()`) so the override also sticks against future automatic retries, not just the one list. | `article_id` (no FK), `status` (`success` \| `failure` \| `accepted`), `reason` (failure only, or copied from the prior failure onto an `accepted` row for display/undo: `paywall` \| `bot-challenge` \| `too-thin` \| `fetch-error` \| `defunct-service`), `detail` (for `fetch-error`: the specific `PageData.fetch_error` reason — an HTTP status, `timeout`, or a connection/SSL error string, from `extract._describe_fetch_error()`; for a Wayback or migration success, the URL actually used; empty otherwise), `source` (added via migration, default `'direct'`: `'direct'` \| `'wayback'` \| `'migration'` \| `'medium-fetch'` \| `'medium-search'` \| `'save'` — distinguishes a Wayback-archived-snapshot, known-domain-migration, Medium-platform-tier (`'medium-fetch'` for a direct Exa fetch of the article's own URL, `'medium-search'` for a search-by-title match — see the "Medium-platform tier follow-up" note in §3), or save-time (durability audit item 1 — `ingest_url` itself, not a backfill re-fetch) success/failure from a normal live-fetch success; see the "fetch reliability", "retry backoff", and "Medium-platform Exa fetch tier" notes in §3 below) |
| `url_correction_log` | Durable trace of every manual URL correction applied via the manual-review CSV import (Phase 5b follow-up #2) — per CLAUDE.md's "every production data change leaves a trace" rule. Written by `Library.apply_article_url_correction()`, one row per correction, `old_url` snapshotted immediately before the `UPDATE` (same precedent as `tool_audit_log`/`community_audit_log`). No SQL-level FK to `articles`. `admin_id` is nullable and always `NULL` today — this app has no per-admin accounts (a single shared secret), so the column is forward-looking only. | `article_id` (no FK), `old_url`, `new_url`, `source` (default `'csv-import'`), `admin_id` (nullable, unused today) |

### FP&A Buddy (Ask)

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `ask_questions` | One row per conversation **turn**; the single table behind all three surfaces (admin report, a user's own history, the member-public community view). | `conversation_id` (groups follow-up turns; `= str(id)` of the first turn) + `turn_index`; token columns for the answer call; `rewrite_input_tokens`/`rewrite_output_tokens`/`rewrite_cost_usd` for the follow-up query-rewrite call; `embed_input_tokens`/`embed_cost_usd` for embedding the retrieval QUESTION during hybrid retrieval (#93 — a **user-cap** cost, unlike `article_embeddings.cost_usd`, which is embed-on-save overhead); **`cost_usd` is the turn TOTAL (answer + rewrite + query embedding)** so every `SUM(cost_usd)` — the monthly cap, the reports — needs no special handling; `hidden_public`/`anonymized` affect only the community view; `citations_json` is the turn's **API-verified cited-source snapshot** (`[{n, title, url, type, article_id?}]` — `article_id` on library entries only; feed/web sources are transient, so the stored title/url *is* the record, never re-resolved) |
| `ask_feedback` | Member ratings of individual answers — **one row per rated turn per user**, upserted on `(question_id, user_id)` so a changed rating updates in place. Feeds the `/admin/ask-feedback` triage view and, later, a retrieval eval set (flagged questions + the rated turn's citation snapshot). Capture + triage only — feedback never mutates prompts or retrieval automatically. | `question_id` (→ `ask_questions.id`), `rating` (`helpful` \| `inaccurate` \| `not_helpful`), `comment` (optional "what was off?" free text), `updated_at` (`''` until first changed — the empty-string-sentinel idiom) |

Cost figures are computed from **real API token usage** at call time
(`linklib/pricing.py`) — never estimates.

### Accounts

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `users` | Member accounts. Passwords are scrypt-hashed (`linklib/passwords.py`, stdlib only). | `role` (`user` \| `admin`), `active`, `ask_cap_usd` (per-user monthly dollar-cap override; `NULL` = inherit the global default from `settings`) |
| `password_reset_requests` | Self-service "forgot password" requests. | `token_hash` (SHA-256 of the emailed token — never the raw token, so a DB leak alone can't reset a password), `expires_at`, `resolved_at` (`''` = pending — the empty-string-sentinel idiom used throughout) |

### CFO Toolbox

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `tools` | The vendor directory on `/tools/software`. `scripts/seed_tools.py` is re-runnable, not one-shot: run by hand against a database, it adds any tool missing by URL and syncs `name` on existing rows when the script's copy changes (#113). `webapp/app.py`'s `@app.on_event("startup")` hook (`_seed_toolbox`) runs the *sync* half of that same logic — advisor/name on a row already matching by URL — on every process boot, but (after the deleted-tools-reappearing fix below) never the *insert* half: a seed entry with no matching row is skipped, not added, since this hook fires on every restart/crash-recovery, not just a first boot, and `tools` has no soft-delete column, so "missing by URL" can't be told apart from "an admin deleted this on purpose." A tool manually deleted at `/admin/tools/software` before this fix would silently reappear on the very next restart because the startup hook still treated a missing row as unseeded; first-time seeding of a brand-new DB is `scripts/seed_tools.py`'s job alone now, not something the startup hook duplicates. **`description` was in this same sync until a 2026-08 incident**: both call sites also compared/wrote `description` via `Library.update_tool_content`, which meant any deploy or manual re-run after a tool's description was AI-regenerated (or hand-edited to differ from the seed list's short blurb) silently reverted it — 81 of 148 seed-listed tools were caught with reverted descriptions from a single night's deploys before this was found (traced from a stale Abacum profile page). `description` sync is retired entirely, permanently, at both call sites — `name` sync is unaffected, since nothing ever AI-drafts a tool's name. `Library.update_tool_content` itself is left in place (still directly tested) but has no live caller left; see CLAUDE.md's dedicated incident bullet for the full write-up, including the companion fix in `scripts/regen_ai_drafted_fields.py`'s community `voice_core` gap found in the same investigation window. Rendered publicly, one entry at a time, at `/tools/software/{slug}` (Software search overhaul Phase 2) via `get_tool_by_slug` — same only-approved-rows rule as `get_community_by_slug`. | `slug` (unique), `approved` (reader submissions wait for approval), `advisor`, `promoted`, `warm_intro_enabled` + `vendor_name`/`vendor_email` (the intro button needs both), `competitive_differentiation` (Phase 3 — free-text "how this differs from the competition," rendered on the profile page when non-empty; hand-written by Brian via a narrow `update_tool_differentiation`, deliberately kept off the general `update_tool` path so the Software bulk-edit panel — which resaves every field it knows about on every call — can never blank it out), `agent_taxonomy_note` (a Phase 0 decision — standalone feature vs. agent-assisted vs. fully independent agent, deliberately free text rather than a structured/enum field — that went unimplemented through Phases 1-4 and was added in Phase 5 since the comparison matrix is the first thing that needed it rendered; same narrow-update-method pattern as `competitive_differentiation` via `update_tool_agent_taxonomy`, and additionally folded into the public directory's client-side search string on `/tools/software` since Phase 0 required it be searchable, not just decorative), `agent_taxonomy_needs_verification` (added by the automated-research follow-up below — a per-tool confidence flag, cleared automatically whenever a human saves the field through the admin edit form's `update_tool_agent_taxonomy`, and set fresh by an LLM-drafted note via `set_tool_agent_taxonomy_draft`; a dedicated `mark_tool_agent_taxonomy_verified` clears it without touching the text), `screenshot_url`/`screenshot_captured_at` (Phase 5 follow-up, rendered in a bordered sidebar box on the profile page — two write paths land on the same fields: `update_tool_screenshot_url` for a manually pasted external URL from the admin edit form, which clears `screenshot_captured_at` since a hand-pasted image has no known capture time (a 2026-08 incident found the admin edit-form submit route originally called the older `update_tool_screenshot` — which also writes `screenshot_is_product` — with that parameter hardcoded to 0 on every save, silently clearing a legacy `screenshot_is_product=1` flag on any unrelated resave, before `scripts/archive/migrate_app_screenshot_from_product_flag.py` ever got a chance to see it; `update_tool_screenshot` itself is kept only for pre-Phase-E callers/tests, never called from the live app anymore); `set_tool_screenshot_capture` for an automated homepage capture, used by both `scripts/capture_tool_screenshots.py` (bulk backfill, run from the terminal) and the live "Generate homepage screenshot" button on the admin edit page — both go through `linklib/screenshots.py::capture_homepage` (Playwright + Chromium, fixed 1280×800 viewport) so a bulk backfill and a one-off recapture stay visually consistent. Captured images are stored on the persistent volume alongside `library.db` (`_SCREENSHOT_DIR`, not the Docker image's static/ dir) and served via `GET /tools/software/screenshot/{filename}` — same basename-only traversal guard as `/static/{filename}`), `app_screenshot_source_url`/`app_screenshot_url`/`app_screenshot_captured_at` (Phase E — a second, independent screenshot slot for the actual product/app UI, stacked below the homepage screenshot on the profile page rather than replacing it. There's no single reliable URL for "the app" the way there's a homepage URL, so this is deliberately manual/curated per record, not something a backfill script can source: `app_screenshot_source_url` holds whatever login/demo/product-tour page Brian supplies, and either `set_tool_app_screenshot` write path — an auto-capture against that source URL via the same `capture_homepage()`, triggered by the "Generate app screenshot" admin button, or a manual crop-and-upload via a client-side Cropper.js modal (CDN, no server-side image-processing dependency — the browser produces the final fixed-size PNG) — writes the resulting served path to `app_screenshot_url`, with no provenance tracking between the two paths. Saved as `{slug}-app.png` in the same `_SCREENSHOT_DIR` and served by the same `GET /tools/software/screenshot/{filename}` route as the homepage slot — no new serving route needed, just a filename suffix. `screenshot_is_product` (the pre-Phase-E flag that let a manually pasted screenshot stand in as "the product shot," in the single slot that existed then) is retired by this phase — the admin checkbox is gone, and the actual data move (`Library.migrate_app_screenshot_from_product_flag`, moving any pre-existing `screenshot_is_product=1` row's `screenshot_url`/`screenshot_captured_at` into the new `app_screenshot_url`/`app_screenshot_captured_at` slot and clearing the homepage slot, since that's what the row actually had) is deliberately NOT wired into an automatic boot hook — it's a production data write, not a schema backfill, so the standing "human review before a production write" rule (CLAUDE.md) applies: Brian runs `scripts/archive/migrate_app_screenshot_from_product_flag.py` by hand (preview by default, `--apply` to write, write-then-read-back verified — same convention as `scripts/backfill_logos.py`) once he's reviewed the affected-row list it prints. The column itself is left in place, non-destructively (same precedent as the retired `community_profiles` `*_tags` columns below), as a frozen historical marker of which rows the migration touched. Profile-page captions are now per-slot, not provenance-flag-driven: "Homepage screenshot, captured {date}" / "Homepage screenshot (not yet captured)" for the homepage slot, "App screenshot, captured {date}" for the app slot, rendered only when that slot is populated — the old "(no product screenshot available yet)" hedge is gone, since an app screenshot is now a real, separate thing rather than a hoped-for override. Mobile (`<=800px`, the existing `.tp-band` collapse breakpoint) shows one screenshot at a time with a tap-to-toggle button (`.tp-shot-toggle`, the Phase J1 expand/collapse convention) when both slots are populated; a record with only a homepage screenshot renders with no `has-app` class and no toggle, identically to pre-Phase-E), `summary` (description-length follow-up — `description` grew from a short 1-3 sentence blurb into a full ~8-12 sentence profile-page write-up, so `summary` is a new short 2-3 sentence field for the directory card and the client-side search string on `/tools/software`, drafted alongside `description` in one `generate_tool_description` call rather than derived from it, since a proper condensed rewrite reads better than a truncated long-form opening. Every write path that touches `description` also carries `summary` (`add_tool`, `update_tool`, `quick_update_tool`) — including the bulk-edit route, which must echo the row's existing `summary` back on every call the same way it already does for `description`, or the "resaves every field it knows about" hazard would blank it on an unrelated advisor/promoted toggle. A boot-time backfill (`summary=description` for any row where `summary` is still empty) covers every pre-existing row's already-short description, so cards keep showing sensible text until a tool is re-enriched; the compare matrix and card/search surfaces fall back to `description` if `summary` is somehow still empty, the profile page always renders the full `description`), `logo_path` (Phase D — a relative path to a downloaded-and-stored logo asset, e.g. `logos/tools/abacum.svg`, never an external URL; written only by `scripts/backfill_logos.py` via `set_tool_logo`, which fetches Brandfetch's **Brand API** (`api.brandfetch.io/v2/brands/domain/{domain}`, Bearer-token auth) — not the free CDN Logo API a first investigation pass assumed, which turned out to be browser-embed-only and blocked all 216 programmatic requests uniformly (see that script's docstring for the full story). Files are saved next to `library.db` on the persistent volume (`logos/tools/` and `logos/communities/` subdirectories, mirroring `_SCREENSHOT_DIR`'s reasoning exactly, including the same slug-collision risk across the two types), not under `webapp/static/` as originally specified, since that directory ships inside the Docker image and doesn't survive a deploy. The Brand API's free tier is 100 requests/month, well under the 216-record catalog, so the backfill is deliberately split across three ~90-record monthly batches (`--limit`, default 90) rather than a single pass; the script's selection query only ever targets rows where `logo_path` is still empty. Rendered near the name on the profile page and small on each directory card via `GET /tools/software/logo/{filename}` (Phase F — see the Auth/routing table below), with a shared initial-monogram fallback for any record still missing one), `logo_manual_override`/`logo_override_stale` (2026-08 manual logo override, built after Aleph's profile page was found showing Zapier's logo — see the dedicated write-up under the Auth/routing table's `/tools/communities/logo/{filename}` entry above for the full mechanism; in short, `set_tool_logo` now refuses to overwrite a row with `logo_manual_override=1`, `set_tool_logo_manual`/`clear_tool_logo_override` are the admin edit page's write paths, and `logo_override_stale` flags — never silently clears or silently keeps applying — an active override when the tool's URL domain changes), `description_needs_verification`/`competitive_differentiation_needs_verification` (Phase G PR 2 — the same `narrative_review_log`-backed "Mark verified" gate `agent_taxonomy_needs_verification` established, extended to the other two tool-only narrative fields. Unlike Agent taxonomy, neither field has a separate Refresh route — Generate is AJAX-only and the main edit-submit route (`/tools/software/{slug}/edit`) is the only place a draft is ever persisted — so that one route sets the flag directly: `1` when this save's submitted `ai_drafted_fields` names the field (a fresh, unconfirmed AI draft), `0` otherwise (a hand-edited or untouched save is itself a confirmation, same convention `update_tool_agent_taxonomy` already used). `summary` shares `description_needs_verification` rather than getting its own column, since `generateDescription()` drafts and marks both in one click. `update_tool`'s new `description_needs_verification` parameter defaults to `None` — meaning "leave the column alone" via `COALESCE` in the `UPDATE` — since `update_tool` is also the bulk-edit panel's and `scripts/archive/fix_corpay_category.py`'s write path, neither of which should guess at this flag on a save they didn't originate. `mark_tool_description_verified`/`mark_tool_differentiation_verified` clear each without touching its text, mirroring `mark_tool_agent_taxonomy_verified` exactly), `description_ai_confident`/`competitive_differentiation_ai_confident` (2026-08 confidence indicator — a genuine model self-report, distinct from the `*_needs_verification` columns above: verification is human-review status, this is the model's own certainty at generation time, matching the pattern `agent_taxonomy_needs_verification` already established via `generate_tool_agent_taxonomy`'s `"confident"` JSON key. `NULL` means no signal yet; `0`/`1` is written only alongside a fresh generation this save (`admin_tools_edit_submit` parses a second hidden input, `ai_drafted_confidence`, the same "field:1,field2:0" shape `ai_drafted_fields` already uses for names). The UI's "Confidence: Yes/No" line (`_confidence_indicator_html`) renders permanently, regardless of verification state (2026-08 policy revision — Brian's call: the two facts are independent) — the only condition that hides it is a raw `NULL` column value, never cleared on an unrelated resave the way `*_needs_verification` itself must be), `agent_taxonomy_ai_confident` (2026-08 follow-up — the same genuine self-report extended to Agent taxonomy, the field the whole confidence-indicator effort started from. Until this column existed, `generate_tool_agent_taxonomy`'s own `"confident"` JSON key only ever fed `agent_taxonomy_needs_verification` (`needs_verification = not confident`) and was never stored on its own, so Agent taxonomy couldn't carry the same permanent display line Description/Differentiation have. Written by `set_tool_agent_taxonomy_draft` (COALESCE, same convention as the other two columns) alongside every fresh draft, left untouched by `update_tool_agent_taxonomy`'s hand-edit path. Deliberately independent of, and does NOT touch, either of Agent taxonomy's two pre-existing `needs_verification`-driven mechanisms: the `_narrative_verify_widget` badge/button on the edit page, and the public profile page's Abacum-fix publish gate that hides an unverified note from visitors entirely — both still key off `agent_taxonomy_needs_verification` exactly as before), `description_low_confidence`/`competitive_differentiation_low_confidence`/`agent_taxonomy_low_confidence` (item 6, Aug 2026 UI pass — the OTHER quality signal every `generate_tool_*` draft already returns, `draft.low_confidence` — whether the page fetch behind the draft actually succeeded, a mechanical pre-generation fact distinct from `*_ai_confident`'s post-generation model self-report; the two can and do disagree. Previously unpersisted for tools — it only ever flashed once in the Generate-status toast text and was lost on reload, unlike `community_profiles.low_confidence`, which already had a real persisted checkbox. Same `NULL`-means-no-signal, COALESCE-write convention as the `*_ai_confident` columns; carried from the browser via a second hidden input, `ai_drafted_low_confidence` (mirroring `ai_drafted_confidence`'s exact shape), parsed by `_ai_drafted_field_low_confidence`, since Description/Differentiation's Generate call is stateless client-side AJAX with no server persistence point until the submit route; Agent taxonomy's background/on-demand research job (`_run_tool_research`) writes it directly, no hidden input needed. Displayed via a new `_low_confidence_indicator_html`, permanently, right next to each field's existing `_confidence_indicator_html` line — phrased as "Source page fetch: Succeeded/Failed/Not recorded" rather than a literal "Low confidence: Yes/No", since a literal mirror would put two Yes/No lines back to back whose "Yes" means opposite things) |
| `tool_categories` | Controlled vocabulary of filter pills — can exist empty, unlike article tags which are purely usage-derived. Consolidated (Software search overhaul Phase 1) from a 21-tag ad hoc list, grown organically as tools were added, to a fixed, deliberately-designed 15-tag taxonomy — always shown alphabetized in the UI (`ORDER BY sort_order, name`, seeded with `sort_order` already alphabetical): `Accounting`, `BI/Analytics`, `Cloud/IT Spend`, `Equity Management`, `ERP`, `FP&A`, `Headcount Planning`, `Legal and Contracting`, `Neobanking`, `Procurement/Spend`, `Revenue`, `Revenue Operations`, `Tax Management`, `Travel Management`, `Treasury/Cash Management`. `scripts/archive/migrate_software_tags.py` is the one-off, re-runnable migration that remapped every existing tool's `categories_json` from the old vocabulary and rebuilt this table — see its module docstring for the full old-to-new mapping. `_DEFAULT_TOOL_CATEGORIES`/`_DEFAULT_CATEGORY_DESCRIPTIONS` in `webapp/app.py` only matter for a fresh DB's first-time seed now that the migration has run. A tool's own assigned categories (as opposed to this vocabulary table) are sorted alphabetically at read time in `Library._tool_to_dict` — the one choke point every read path (`get_tool`, `list_tools`, `get_tool_by_slug`) goes through — rather than relying on every write path (`add_tool`/`update_tool`/the bulk-edit route) to sort before saving; `Library._community_to_dict` does the same for `communities.categories`. This surfaced as a real gap after a production tag-consolidation dry-run: two straggler category names (`CPQ`, `Finance Agents`) that Brian fixed by hand via the admin UI weren't guaranteed to come back out sorted the same way a migration-touched row would. | `name` (unique), `sort_order` |
| `tool_competitors` | Manually curated competitor cross-links between Software entries (Phase 3), rendered as "Competitors" on `/tools/software/{slug}`. One undirected edge per pair, normalized so `tool_id` is always the smaller id (`Library.add_tool_competitor` sorts before insert) — `UNIQUE(tool_id, competitor_id)` dedupes against that normalized form, and curating from either tool's `/tools/software/{slug}/edit` page links both directions. This table is the source of truth; `Library.suggest_tool_competitors` (shared-category count, most overlap first) is a separate read-only helper that only powers an admin-UI suggestion list — deliberately not a live auto-computed "competitors" feature, since the 15-tag taxonomy is broad enough that pure tag overlap surfaces plenty of non-competitors (see Phase 0's investigation). The AI-first-pass upgrade (Competitors/Similar-communities) added `POST /admin/tools/{tool_id}/competitors/generate-matches`, which runs `linklib.enrich.generate_competitor_matches` over that same suggestion shortlist to judge which candidates are genuine competitors — never written to this table directly; the admin edit page's suggestions section renders as a checkbox list that Generate pre-checks, and only a human clicking "+ Add selected" (`POST /admin/tools/{tool_id}/competitors/add-selected`, batch version of the older single-`competitor_id` `/add`) actually inserts rows, same generate-then-review contract as every other AI-drafted field (see `field_reviews`). | `tool_id`, `competitor_id` (composite unique, normalized pair) |
| `category_features` | **Feature Taxonomy (2026-08, Phase 1 — docs/FEATURE_TAXONOMY.md is canon).** The governed replacement for the retired `tool_features`' flat free text, built category by category as each is curated (see the Key architecture decisions bullet below for the full model; the legacy free-text table and every code path reading or writing it were retired outright in the Feature Taxonomy Phase 1b PR 2). `category_id` FKs to `tool_categories` — the *existing* `/tools/software` filter-pill vocabulary, not a separate feature-only taxonomy; a 2026-08 investigation found the pilot's three planned category names ("ERP & Accounting", "FP&A Planning", "Close Management") didn't match any live pill, resolved by reusing the real `ERP`/`FP&A` pills and adding `Close Management` as a genuine new one. `name` is unique per category, not globally — the same capability name (e.g. "Anomaly Detection") deliberately recurs across categories by design (rules doc §2), each a separate row. `retired_at` (nullable) — features are retired, never deleted; `retire_category_feature` soft-retires without cascading to existing `tool_feature_links` rows. Seeded via `scripts/seed_feature_taxonomy.py` (idempotent, `scripts/seed_data/*.csv`) from a nine-vendor pilot. **Admin CRUD at `/admin/tools/software/features` is now a single pivot table covering every category (Phase 1c, 2026-08), not the earlier category-index + per-category-subpage pattern** — one collapsible group per category (name, feature count, a coral "N pending" badge when that category has pending `feature_review_queue` items), the group's own table with inline Name/Definition/Pointer note/Order columns and per-row Save/Retire, a single "Add a feature" form above the table with a category selector (not a per-group add row), a category filter + expand-all/collapse-all (vanilla JS, `_FEATURE_TAXONOMY_JS`), and pending review-queue items for that category rendered as read-only rows right inside the group (linking out to the queue page — no approve/deny surface here, that stays exclusively on `/admin/tools/software/feature-review-queue`). Collapse/filter state round-trips through `category`/`open_ids` query params across the add/edit/retire POST-redirect-GET cycle rather than a client framework. The old per-category subpage URL (`/admin/tools/software/features/{category_id}`) is gone outright — no redirect, since it was never bookmarked/linked externally. All Feature Taxonomy admin routes follow the "software-directory admin lives under `/admin/tools/software/*`" convention decided in the original Feature Taxonomy PR (see CLAUDE.md); the old `/admin/tools/categories` URL 301-redirected at the time — since removed outright in the Phase 1b admin URL convention PR below, which completed the cutover with no legacy admin URLs left at all. | `category_id`, `name` (unique per category among live rows), `sort_order` |
| `tool_feature_links` | One row per (tool, feature) — the vendor-specific designations (rules doc §6) live here, never on the feature itself, since the same feature is native/rules-based at one vendor and an add-on/AI-driven at another. `availability` is a real `CHECK` constraint (`native`\|`add_on`) — absence of a row is the third state ("not available"), never a stored value. `verified_as_of` is required per link (claims decay fast). Toggled from a checklist on each tool's own `/tools/software/{slug}/edit` page (`Library.upsert_tool_feature_link`/`delete_tool_feature_link`, via `POST /admin/tools/{tool_id}/feature-links/save`) — a direct admin edit, not routed through the review queue below, since the queue exists for scan/public proposals and admin fast-path logging, and a manual checklist toggle on the tool's own page already *is* the admin editing directly. **`Library.delete_tool`'s cascade fix (2026-08, see the `tool_audit_log` row above) deletes a deleted tool's own rows here** rather than leaving them orphaned. | `tool_id`, `feature_id` (composite unique) |
| `feature_review_queue` | The review gate (rules doc §9) — no proposed change reaches `category_features`/`tool_feature_links` without landing here first and being approved by a human, regardless of who or what proposed it. `source` (`admin`\|`scan`\|`public`) distinguishes an admin's own fast-pathed edit, the (later, unscheduled) recurring AI scan, and the (later, unscheduled) public suggestion channel — `category_id`/`tool_id`/`submitter_name`/`submitter_email` are nullable now so those later phases don't need a migration to add them. `payload` is the proposed change as JSON (new feature, new link(s), or an existing-feature link) since the three sources produce structurally different proposals. Approving (`Library.approve_feature_review_queue_item`) applies `payload` through the exact same `add_category_feature`/`upsert_tool_feature_link` methods a manual edit would call — never a direct table write from the approval path itself; passing `override_payload` is "edit-then-approve" (status lands `edited` rather than `approved`) and is the same route/form as a verbatim approval, not a separate mechanism — the admin edit page's approve form doubles as the edit form, pre-filled from the stored payload. `/admin/tools/software/feature-review-queue` lists pending items grouped by source with Approve/Deny actions; seeded with 15 pilot proposals (`source='scan'`) via `scripts/seed_feature_taxonomy.py`. **Merge confirmation step (Phase 1c, 2026-08):** when a `new_feature`-proposal Approve would resolve to an existing feature by exact case-insensitive name match in the category (`Library.find_category_feature_by_name`, extracted from `approve_feature_review_queue_item`'s own merge logic for reuse here) — the exact mechanism confirmed working in production on the Abacum/Aleph merge, 8/20 — the route now interposes a confirmation page (`_feature_merge_confirm_page`) naming the vendors already linked to the existing feature (`Library.list_tools_linked_to_feature`), with Merge (resubmits the identical approve request plus `confirm_merge=1`) and Change name (back to the queue page to edit the proposal's name first) actions, rather than merging silently. **`Library.delete_tool`'s cascade fix (2026-08, see the `tool_audit_log` row above) auto-denies any `pending` row naming a deleted tool** rather than leaving it pointing at nothing. | `status` (`pending`\|`approved`\|`edited`\|`denied`), `source` (`admin`\|`scan`\|`public`) |
| `benchmarks` | The Resources page at `/tools/resources` (renamed from `/tools/benchmarks`/"Benchmarking" in the admin URL convention PR — URL/copy only, table name unchanged), managed at `/admin/tools/resources`. `_DEFAULT_BENCHMARKS` in `webapp/app.py` syncs the same way as `tools`: run by hand it adds any entry missing by URL and syncs `name`/`description` on existing rows via `Library.update_benchmark_content`, leaving `coverage`/`pricing` untouched so admin edits survive a re-sync — but the `_seed_toolbox` startup hook's per-boot pass over `_DEFAULT_BENCHMARKS` only performs that sync, same insert-never fix and same reasoning as `tools` above (no soft-delete column here either). `section` (`'benchmarking'`\|`'books'`, default `'benchmarking'`) splits the page into two headings — see "Resources — Book recommendations" below for the full write-up; `_DEFAULT_BENCHMARKS`/`_seed_toolbox` only ever cover the `'benchmarking'` rows, since a sync-only mechanism can't originate new `'books'` rows. | `coverage` (`Private`\|`Public`\|`Both`), `pricing` (`free`\|`paid`\|`freemium`), `section` (`benchmarking`\|`books`) |
| `tool_leads` | Warm Intro request submissions per tool. | `tool_id`, contact fields |
| `tool_audit_log` | Deletion audit trail for Software entries — one row per hard delete, since `tools` has no `deleted_at` column (unlike `contacts`) and a deleted row leaves nothing else behind. Same shape as `archive_audit_log`/`contact_audit_log` below, but written from inside `Library.delete_tool` itself rather than at each route (`_log_archive_audit`'s pattern) — every current delete path (the admin Delete button, bulk delete, a pending submission's Reject, a name-duplicate merge's "delete the loser" step) already calls `delete_tool`, so logging there guarantees a future new call site can't add a delete without also logging it. `detail` carries a name/url/categories snapshot taken immediately before the `DELETE`, since that's the only record of what was removed once the row is gone. **Cascade fix (2026-08, the Pave/Culpepper/Radford removal investigation):** `delete_tool` used to leave `tool_feature_links` and `entity_citations` rows orphaned (nothing reads them once the tool is gone — a "no dead data" violation), and any `pending` `feature_review_queue` proposal naming the tool stayed pending forever, pointing at nothing. `delete_tool` now also deletes the tool's `tool_feature_links`/`entity_citations` rows and denies (never silently drops) any `pending` `feature_review_queue` row naming the tool, alongside its pre-existing `tool_competitors`/`tool_name_dedupe_decisions`/`field_reviews` cleanup. `tool_leads` and `narrative_review_log` are deliberately still untouched — historical record, same precedent as this table itself surviving a deleted tool. | `admin_id` (nullable, same break-glass-login caveat as `archive_audit_log`), `action` (`'delete'`\|`'reject'`\|`'merge'`), `item_id` (a former `tools.id` — the row no longer exists), `detail` (name/url/categories snapshot) |
| `communities` | The directory on `/tools/communities` — CFO/finance peer groups, associations, and Slack communities (a sibling of `tools`, not a variant of it). `scripts/seed_communities.py` is re-runnable like `seed_tools.py`: run by hand it adds any community missing by URL and syncs `name`/`notes` on existing rows via `Library.update_community_content`, plus `advisor` (a direct `UPDATE`, mirroring `tools.advisor` exactly — see below) — the identical name+description+advisor contract as `tools`. `_seed_toolbox`'s per-boot pass over `COMMUNITIES` carries the identical insert-never fix as `tools` above, for the identical reason (no soft-delete column, hook runs on every restart). Every other field (`reach`, `local_markets`, `featured`, `cost_band`, `cost_note`, `sponsorship_type`, `sponsor_name`, `access`, `format`, `categories_json`, `approved`) is admin-owned, edited at `/admin/tools/communities`, and never touched by a re-sync. | `slug` (unique), `reach` (`Regional`\|`National`\|`Global` — a community's overall footprint), `local_markets` (free text, e.g. "Boston, New York, SF Bay Area" — cities/areas where it has a chapter, hub, or local focus; independent of `reach`, so a National community like FEI can still carry local markets; replaced a fixed 18-city checkbox grid — see "Metros -> free text migration" below), `featured` (pin-to-top + coral badge, same pattern as `tools.promoted`; independent of `reach`/`local_markets`/`advisor`), `advisor` (⭐ marker + "Advisor" filter chip, same pattern as `tools.advisor` — discloses a personal relationship, e.g. The F Suite; independent of `featured`), `cost_band` (one of five fixed bands: `Free`\|`Undisclosed dues`\|`<$1k/yr`\|`<$2,500/yr`\|`$2,500+/yr` — bucketed by individual/base rate, exact dues go in `cost_note`), `sponsorship_type` (`Independent`\|`Vendor-sponsored`\|`Investor-sponsored`), `access` (`Open`\|`Application`\|`Invite-only`\|`Qualification-based` — a fixed `<select>`, converted from free text; see the auto-populate section below for how the option list was derived), `format` (`Hybrid`\|`In-person`\|`Slack`\|`Online`\|`LinkedIn group` — same conversion), `approved`, `screenshot_url`/`screenshot_captured_at`/`app_screenshot_source_url`/`app_screenshot_url`/`app_screenshot_captured_at` (Phase 3b — built from scratch for Communities, mirroring `tools`' homepage-screenshot columns of the same name exactly: `update_community_screenshot_url` for a manually pasted URL from the admin edit form (same `update_community_screenshot`-was-clobbering-`screenshot_is_product` incident and fix as the `tools` row), `set_community_screenshot_capture` for an automated homepage capture via the same `linklib/screenshots.py::capture_homepage`; served at `GET /tools/communities/screenshot/{filename}` from its own `_COMMUNITY_SCREENSHOT_DIR`, kept separate from Software's `_SCREENSHOT_DIR` since the two types' slugs can collide — Phase 0 found `airbase`/`datarails`/`rillet` shared across both. The `app_screenshot_*` trio and `screenshot_is_product`'s retirement (Phase E) mirror the `tools` row exactly — see there for the full design; the app slot's file is `{slug}-app.png`, same directory, same serving route), `logo_path` (Phase D — mirrors `tools.logo_path` exactly, written by `set_community_logo`; communities are the second and lower-priority half of the same three-batch `scripts/backfill_logos.py` backfill, processed only after every tool has one, saved under a separate `logos/communities/` subdirectory for the identical slug-collision reason as the screenshot columns above — see the `tools` row for the full backfill design), `logo_manual_override`/`logo_override_stale` (2026-08 — mirrors `tools.logo_manual_override`/`tools.logo_override_stale` exactly; see the `tools` row and the dedicated manual-logo-override write-up under the Auth/routing table above) |
| `community_categories` | Controlled vocabulary of filter pills for `/tools/communities`, same shape and same reasoning as `tool_categories`. | `name` (unique), `sort_order` |
| `community_audit_log` | Deletion audit trail for Communities entries — exact structural mirror of `tool_audit_log` above, same reasoning (hard delete, no `deleted_at`, logged from inside `Library.delete_community` so every call site is covered). | `admin_id` (nullable), `action` (`'delete'`\|`'reject'`), `item_id` (a former `communities.id`), `detail` (name/url/categories snapshot) |
| `community_competitors` | Manually curated "similar communities" cross-links (Competitors/Similar-communities upgrade), rendered as "Similar communities" on `/tools/communities/{slug}` — built from scratch, since no such concept existed for Communities before this. Exact structural mirror of `tool_competitors` (same normalized-pair-with-smaller-id-first shape, same `UNIQUE` constraint, same OR-both-sides lookup), kept as its own table rather than shared/polymorphic, matching this codebase's convention of keeping Software and Communities schema/routes independent throughout. `Library.suggest_community_competitors`/`add_community_competitor`/`remove_community_competitor`/`list_community_competitors` mirror the Software functions of the same name one-for-one. Curated at `/tools/communities/{slug}/edit`'s "Similar communities" section — same checkbox-suggestions + Generate + "+ Add selected" pattern as Software's Competitors card (`POST /admin/tools/communities/{community_id}/competitors/generate-matches` calls the same shared `generate_competitor_matches` judgment function as Software's equivalent route, since the underlying task — pick genuine matches from a pre-filtered tag-overlap shortlist — is identical for both entity types; `POST .../competitors/add-selected` is the only route that writes, and only once a human submits the reviewed checkbox selection). | `community_id`, `competitor_id` (composite unique, normalized pair) |
| `community_profiles` | Deep, opinionated read per community (Community Profiles, Phase 2) — the qualitative judgment a directory row's cost/access fields can't carry, edited at `/admin/tools/communities/{id}/profile`. 1:1 with `communities` via `community_id` as the primary key (no SQL-level `REFERENCES`, same as `article_embeddings.article_id` — this codebase does cleanup on delete in application code, not via a declared FK; see `delete_community`). Empty/thin until Research content backfills it or an admin generates a draft. Rendered publicly at `/tools/communities/{slug}` (Phase 3) and side by side at `/tools/communities/compare` (Phase 6) — a community with no profile row, or one whose fields are all empty, falls back to a minimal page (or, on Compare, a "Not available yet" cell) rather than an error or empty-looking layout. | `community_id` (PK), `sponsor_relationship_note` (qualitative — value-add or sales funnel? — distinct from the factual `sponsor_name`/`sponsorship_type` on `communities`), `business_model` (added post-launch — how the community structurally sustains itself, e.g. a gated dues-funded peer group vs. a wide-funnel free-to-join community monetized via paid tiers/events/sponsorships; distinct from `sponsor_relationship_note`, which judges whether a *sponsor's* presence feels salesy, not how the community itself makes money), `application_friction` (the real barrier to entry, not just the `access` label), `founded_year` (nullable), `notable_members`/`public_criticism` (nullable — only when verifiably public/reported), `low_confidence`, `primary_purpose`/`cpe_eligible`/`platform_type`/`meeting_format`/`event_style`/`seniority_band`/`resources_included` (added for the bulk community-profile import below — short factual/categorical research fields, deliberately `TEXT` rather than a strict boolean/enum since the source research carries qualifiers like "Yes (NASBA-approved sponsor)"; excluded from the voice-rewrite pass since they're not prose; originally hand-entry-only, folded into `generate_community_profile`'s single Claude call alongside the narrative fields per the AI-first-pass-on-every-field standing principle — see `field_reviews` below), `needs_review` (added by the same import — flags a profile as imported/edited but not yet personally read and approved by Brian; admin-only, independent of `communities.approved`, which controls public visibility rather than content review. Originally manual-only, set only via the admin edit form's checkbox or the bulk import. Phase G PR 2 reused this exact column as the Community profile draft's needs-verification flag rather than adding a new one — `POST /admin/tools/communities/{id}/profile` now also sets it to `1` whenever that save's `ai_drafted_fields` names any of the 23 profile fields (OR'd with the submitted checkbox value, never overriding a manually-set flag to `0`), and the pre-existing "Mark reviewed" action (`mark_community_profile_reviewed`, previously admin-list-only) now also writes a `narrative_review_log` row and — Phase G PR 2 addition — is reachable directly from the profile edit page itself, not just the admin list row, closing the same discoverability gap the original Phase G investigation flagged for Agent taxonomy's missing button), `seniority_band_tags`/`cpe_eligible_tags`/`platform_type_tags` (Recommender best-fit weighting — a JSON array of controlled-vocabulary values per dimension, edited via checkbox groups alongside the free-text field of the same base name; kept separate from that free-text column rather than parsed from it because the research prose is too inconsistent for reliable keyword matching, e.g. a `platform_type` of "not a Slack/forum" would false-match a naive "Slack" substring check — see `webapp/app.py`'s `_WEIGHT_DIMENSIONS` for the fixed vocabulary per dimension and `scripts/backfill_community_weight_tags.py` for the one-off pass that classified the existing corpus), `function_tags` (post-#159 addition, no free-text sibling column — Overall finance org/FP&A/Accounting/Treasury), `looking_for_tags` (post-#159 addition merging the old `primary_purpose_tags`/`resources_included_tags` into one multi-select — Peer discussions/Networking/Learning & education/Vendor connections/Resources & templates), `programming_tags` (post-#159 addition merging the old `meeting_format_tags`/`event_style_tags` into one multi-select, reusing the "Programming" label — Meals/Conferences/Retreats/Virtual Panels — the four retired `*_tags` columns and their free-text siblings still exist on the table but are frozen historical data, no longer written by `upsert_community_profile` or read by `_WEIGHT_TAG_COLUMNS`), `paid_free_tags` (post-#159 addition — Dues moved here from a `communities.cost_band`-derived computation so a freemium community can carry both Free and Paid, independently of the single-value `cost_band`), `industry_tags` (post-#159 addition, no free-text sibling column — Life sciences/Healthcare/Private equity/funds/Industry-neutral), `stage_focus`/`jobs_program`/`team_or_individual` (placeholder factual/categorical columns, same pattern as `business_model` when it was first added — empty until a future research round backfills them; visible in the admin edit form and the generate-profile-draft prompt, but not yet part of the Recommender's weighting), `ideal_member_ai_confident`/`anti_fit_ai_confident`/`value_prop_ai_confident`/`business_model_ai_confident`/`format_reality_ai_confident`/`engagement_level_ai_confident`/`sponsor_relationship_note_ai_confident`/`application_friction_ai_confident`/`cost_value_verdict_ai_confident`/`notable_members_ai_confident`/`public_criticism_ai_confident`/`verdict_summary_ai_confident` (2026-08 confidence indicator — the model's own self-reported certainty per field, distinct from the shared `needs_review` flag above; see CLAUDE.md's "Confidence indicator" bullet for the full write-up, including why the submit route — not `upsert_community_profile` itself — is what carries a field's previous value forward on a save that only regenerated a different field). Item 6 (Aug 2026 UI pass) surfaces both this table's existing `low_confidence` column and these 12 `*_ai_confident` columns on the admin communities LIST page (`/admin/tools/communities`) for the first time — previously both signals were real and fully persisted but only ever shown on the per-profile edit view. No new columns: `Library.community_profile_quality_flags()` is a bulk read-only query (one call for every community, same reasoning as the pre-existing `community_profile_needs_review_ids()` just above it) returning a "Low confidence" badge when the whole-profile flag is set and a "N/12 fields low-confidence" badge counting how many of the 12 tracked fields self-reported `confident=False` — both read-only on this page, since only the per-profile edit view has a save action for either signal) |
| `community_gap_submissions` | Gap-collection (Phase 5): the native replacement for the old `/community` page's Google Form, folded into the live directory rather than a separate parked page. Submitted at `POST /tools/communities/gap`, triaged at `/admin/community-gaps` (mirrors `/admin/ask-feedback`'s layout). No login required — anyone can submit. Also doubles (Phase 7) as the log for every completed Recommender quiz at `/tools/communities/find` — same table, distinguished by `submission_type` rather than a second table, since a zero/thin recommender result is the same kind of gap signal as a zero-result directory search. Doubles a third way (best-fit weighting) for the quiz's optional "What matters most to you?" step: a visitor's checked values, logged only when they set at least one (never on a skip), for Brian's own aggregate insight into what finance leaders say matters most — not shown to other visitors. Doubles a fourth way as the per-listing correction report from `POST /tools/communities/correct`, since it's the same kind of free-text triage signal, just about factual accuracy on one specific listing rather than a gap in the directory. | `current_communities`/`gaps`/`looking_for` (free text, the visitor's own words — always `''` on a `submission_type='recommender'` or `'weight_preferences'` row, since neither collects free text; on a `'correction'` row, only `gaps` is populated, holding the correction report itself), `search_context_json` (on a `'gap'` row: directory search/filter state at submission time, built client-side from JS-only filter state and carried through a hidden form field; on a `'recommender'` row: the quiz answers plus `result_count`; on a `'weight_preferences'` row: `{"weights": {dimension_key: [chosen values]}}`; always `''` on a `'correction'` row, since it isn't a directory search), `viewed_community_ids_json` (server-computed at submission from `community_profile_views`, not client-supplied), `closest_community_id` (nullable, no FK — always `NULL` on a recommender/weight_preferences row; always populated on a `'correction'` row, since a correction is always about one specific listing), `email` (nullable), `reviewed`, `submission_type` (added by migration — `'gap'`\|`'recommender'`\|`'weight_preferences'`\|`'correction'`, defaults `'gap'` so every pre-existing row keeps its meaning) |
| `community_profile_views` | Session-scoped, no-login view tracking for `/tools/communities/{slug}`: which profile pages a visitor opened before (maybe) submitting the gap form above. Keyed by an anonymous `cfo_visitor` cookie (`webapp/app.py`, 30-day TTL, not signed — the first anonymous-session primitive in the codebase; everything else, e.g. `read_later`, requires a logged-in `user_id`). No cleanup job for stale sessions yet — rows are small and carry no PII. | `session_id` + `community_id` (composite PK, dedups repeat views), `viewed_at` |
| `field_reviews` | Review-status audit trail for every AI-drafted field on Software/Communities profiles — the standing principle that AI drafts a first pass into the edit form and nothing publishes without Brian reviewing and saving it. One generic table rather than a `{field}_reviewed_at`/`_by` column pair per field, since there are 15+ generatable fields across two record types (Software's `description`/`summary`/`competitive_differentiation`, Communities' full narrative profile) and more likely to come later. Written by `Library.record_field_review`, called from an edit-submit route whenever the submitted form's `ai_drafted_fields` hidden input names a field — that input is populated client-side by `markAiDrafted()` inside each Generate button's success handler (`_MARK_AI_DRAFTED_JS`, shared across every generate-button script), never inferred from content after the fact. Read by `Library.list_field_reviews` for a future "last reviewed" admin display. Cleaned up on delete alongside `tools`/`communities` rows, no SQL-level FK (same pattern as `community_profiles`). | `entity_type` (`'tool'`\|`'community'`), `entity_id`, `field_name` (composite PK), `reviewed_at`, `reviewed_by` (stored even though there's only one admin today, so the schema doesn't need revisiting if that changes) |
| `entity_citations` | Citations-API grounding fix, Phase 1b (2026-08) — the API-verified citation set for one AI-drafted, grounded field, shared across entity types/fields rather than a `*_citations` column per field (a per-field column would have needed migrating off when Description/Community profile joined in Phase 2/3). Composite natural key, upsert-on-write (current state, not an append-only log the way `narrative_review_log` is — a fresh draft replaces the row wholesale). `citations_json` always holds the FULL deduped-by-url list, uncapped; a 5-source display cap is a render-time-only slice (`webapp.app._citations_list_html(cap=5)` on the public profile page; the admin edit page passes no cap, rendered next to the "Mark verified" action so a reviewer sees every source before publishing). Written by `Library.set_entity_citations` (direct write, not COALESCE'd — a fresh draft's citations always replace a stale prior draft's) from `webapp.app._run_tool_research` alongside every fresh `agent_taxonomy_note` draft; cleared by `Library.clear_entity_citations`, called from `update_tool_agent_taxonomy` when a human hand-edits the field (no citation trace to keep). `entity_type='tool'`/`field_name='agent_taxonomy'` since Phase 1b; `field_name='description'` joined in Phase 2 (2026-08) — written/cleared from the `/tools/software/{slug}/edit` and `/admin/tools/software/new` submit routes instead of a server-side refresh route, since Description's Generate call is stateless AJAX with no `tool_id` at draft time (see the Description grounding fix bullet above for the full browser-round-trip + server-side revalidation mechanism); `'community'`/`'community_profile'` still pending for Phase 3 as one shared row per profile draft (not per-field), per the "one shared citation set per profile" decision. **`Library.delete_tool`'s cascade fix (2026-08, see the `tool_audit_log` row above) deletes a deleted tool's `entity_type='tool'` rows here** rather than leaving them orphaned. | `entity_type`, `entity_id`, `field_name` (composite PK), `citations_json` (`[{n, title, url, type}]`, `'[]'` default), `model` + `generated_at` (the generation run reference — no separate run/log table, since nothing else in this codebase has a run-id concept to reference instead) |
**Phase P column rename.** `tools.differentiation_note`/`differentiation_needs_verification`
were renamed to `competitive_differentiation`/`competitive_differentiation_needs_verification`
(the table above already reflects the new names) to match the Software edit
page's new "Competitive differentiation" field label. A real `ALTER TABLE
... RENAME COLUMN` migration, added to the existing idempotent migration
list in `linklib/db.py` alongside every other schema change there.
`scripts/archive/rename_differentiation_columns.py` provides the same rename as a
standalone dry-run/apply/write-then-read-back script for manually
pre-migrating a database copy outside of an app boot — see its docstring.
`tool_competitors`/`community_competitors` (below) were deliberately NOT
renamed alongside this — see CLAUDE.md's Phase P note for the full
reasoning (no single column to rename, and Communities' "Similar
communities" label never matched Software's "Competitors" in the first
place, so there's no display-label change to track there).

| `narrative_review_log` | Phase G: explicit "Mark verified" audit trail, deliberately separate from `field_reviews` above — that table is a passive by-product of saving the edit form after a Generate click (the save itself counts as "reviewed"), never surfaced in the UI. This table backs a stricter, opt-in gate: an AI-drafted narrative field stays flagged until an admin explicitly clicks "Mark verified"/"Mark reviewed", distinct from just saving the form. One shared table with `field_type`/`entity_type` discriminators rather than a `{field}_review_log` table per field, mirroring `tool_audit_log`/`community_audit_log`/`backup_log`'s `id`/`admin_id`(nullable FK to `users`)/`detail`/`created_at` shape. Append-only — re-verifying after a fresh AI draft writes a new row rather than updating one in place, so `Library.get_latest_narrative_review` (the "Verified by X on Y"/"Reviewed by X on Y" line next to the button) always reflects the most recent confirmation, not the first one ever made. PR 1 (Phase G) wrote only `field_type='agent_taxonomy'`; PR 2 added `'description'` and `'differentiation'` (both `entity_type='tool'`, each backed by its own `tools` column — see above) and `'community_profile'` (`entity_type='community'`, `item_id` a `communities.id` — deliberately reuses the pre-existing `community_profiles.needs_review` column rather than adding a fourth `*_needs_verification` column, since that flag already existed as a working whole-profile "flag for later" mechanism with its own admin-list badge/filter/count; PR 2 only added logging to its "Mark reviewed" click and auto-set it to `1` on a fresh Generate-and-save, where before it was manual-only). Folding all of this into `field_reviews` instead, rather than building this separate table, was considered and rejected — see CLAUDE.md's Phase G note for the full reasoning (field_reviews conflates "saved after Generate" with "a human reviewed it," and for the Community profile draft specifically it's per-field rather than whole-draft). **`superseded_at` (2026-08, stale-stamp fix)** closes a gap left by the append-only design above: `needs_verification` correctly flips back to `1` (the amber badge) the moment a field is regenerated, but nothing used to touch this table, so `get_latest_narrative_review` kept surfacing the PRIOR human's stamp — a real "Verified by X on {old date}" line next to content that admin never actually saw. Rows are still never deleted (`list_narrative_review_log`'s full history is unaffected); a regeneration instead calls `Library._supersede_narrative_review`, which stamps `superseded_at` on every currently-live row for that `(entity_type, field_type, item_id)`, and `get_latest_narrative_review` now filters `WHERE superseded_at IS NULL`, so a superseded row renders as "never verified" until a fresh "Mark verified"/"Mark reviewed" click writes a new, live row. Called from `set_tool_agent_taxonomy_draft` unconditionally (that method is used only for fresh AI drafts, never hand-edits), and from `update_tool`/`update_tool_differentiation`/`upsert_community_profile` via an explicit `clear_description_verification_stamp`/`clear_verification_stamp` parameter — deliberately NOT inferred from the `needs_verification`/`needs_review` value itself, since `scripts/regen_ai_drafted_fields.py` forces that flag to `0` (a different, pre-existing deliberate bypass of the review badge — see that script's own docstring) while still needing the stamp cleared, and since Community profile's `needs_review` can independently be set to `1` by a manual, non-AI checkbox that must NOT clear the stamp. | `admin_id` (nullable, same break-glass-login caveat as `tool_audit_log`), `entity_type` (`'tool'`\|`'community'`), `field_type` (`'agent_taxonomy'`\|`'description'`\|`'differentiation'`\|`'community_profile'`), `item_id` (a `tools.id`/`communities.id`, no SQL-level FK, same as `tool_audit_log`), `detail` (the reviewed text snapshot at verification time — the field's own text for the three tool-side field_types, `community_profiles.verdict_summary` for `'community_profile'` since that one flag covers 23 fields at once), `created_at`, `superseded_at` (nullable — `NULL` means this is the live stamp; a timestamp means a later regeneration superseded it) |

**Domain-derived slugs (Phase 2).** `tools.slug` and `communities.slug` were
originally generated from the entry's *name* (`linklib.db._slugify`,
suffix-numbered on collision). Phase 2 switched slug generation to the entry's
*URL* instead: `_domain_slug_base(url)` takes the hostname, strips a leading
`www.`, and takes the first label before the remaining first dot (e.g.
`https://www.abacum.io` → `abacum`). If that short slug collides with another
row of the *same* type, the colliding rows fall back to `_domain_slug_full(url)`
— the full hostname with dots replaced by hyphens (e.g. `abacum-io`) — rather
than a numeric suffix; a numeric suffix is still the final fallback if even
that collides. `add_tool`/`add_community` apply this at insert time and never
regenerate a slug on `update_tool`/`update_community` — a slug is stable for
the life of the row, same as before. Software and Communities each enforce
slug uniqueness only within their own table (separate `/tools/software/...`
and `/tools/communities/...` prefixes), so a domain shared across a vendor's
software listing and its own branded community — Phase 0 found three:
airbase, datarails, rillet — is not a collision. `scripts/archive/migrate_domain_slugs.py`
is the one-off, re-runnable migration that recomputed every pre-existing row's
slug under this algorithm; per the build plan this was a hard cutover with no
redirect from the old name-based slugs (or from the even-older numeric-ID
edit URLs — see the *Admin* auth bullet above for where the edit routes live
now).

`POST /admin/tools/generate-description` (admin-only) drafts a description
from just a name + URL — used by the "Generate" button on the Add Tool form,
Quick Edit, and Full Edit. It's stateless: fetches the URL via
`linklib/extract.py` (same best-effort fetch as article capture) for
grounding, then one Claude call (`linklib/enrich.py::generate_tool_description`,
`LINKLIB_ENRICH_MODEL`) drafts the description — never auto-saved, and never
asserts an acquisition, since that stays a manual/reviewed call. When the page
fetch comes back empty, the draft is flagged `low_confidence` so the admin UI
can warn that it's working from the model's own knowledge rather than the live
page. The call's cost lands in the same `enrichment_cost` ledger as article
enrichment (`article_id=NULL`) — overhead, not a user-facing budget.

`POST /admin/tools/communities/generate-profile` (admin-only) mirrors that
exact contract for `community_profiles`, sized up for a much larger field
count: one Claude call (`linklib/enrich.py::generate_community_profile`)
drafts all thirteen qualitative fields as structured JSON from a community's
name + URL (plus whatever's already on the edit form, fed back as context so
a regenerate refines rather than starts over), grounded in the same
`linklib/extract.py` page fetch, with the same `low_confidence` rule and the
same never-auto-saved review contract — the draft lands in the
`/admin/tools/communities/{id}/profile` form fields for the admin to check
before saving. Cost lands in the same `enrichment_cost` ledger, `article_id=NULL`.

`POST /admin/tools/communities/generate-listing` (admin-only) is the "Auto-fill
from URL" button on the Add/Edit Community form — the equivalent of
`generate-description` above, but for `communities`' basic directory-listing
fields (`demographic`, `reach`, `local_markets`, `cost_band`, `cost_note`,
`sponsorship_type`, `sponsor_name`, `access`, `format`, `categories_json`)
rather than `community_profiles`' qualitative deep-dive. One Claude call
(`linklib/enrich.py::generate_community_listing`), same page-fetch grounding
and `low_confidence` rule as the other `generate_*` helpers. The five enum
fields (`reach`, `cost_band`, `sponsorship_type`, `access`, `format`) and the
one controlled-list field (`categories_json`) are constrained to the
caller-supplied vocabularies the admin form itself uses (`webapp/app.py`'s
`_COMMUNITY_REACH`/`_COMMUNITY_COST_BANDS`/`_COMMUNITY_SPONSORSHIP_TYPES`/
`_COMMUNITY_ACCESS`/`_COMMUNITY_FORMAT` plus the live `community_categories`
list) and re-validated against them on the way back, since a directory Brian
vets personally can't tolerate a hallucinated value. `local_markets` is free
text (no controlled vocabulary — see the Metros -> free text migration
below) and is only trimmed, not re-validated against a list.
`_COMMUNITY_ACCESS`/`_COMMUNITY_FORMAT` (4 and
5 options respectively) were derived from the leading word/phrase of every
pre-existing community's free-text `access`/`format` value — a deliberately
lossy bucketing (e.g. "Invite-only (~10% acceptance, ~95% referral rate)" ->
"Invite-only") reconciled once via `scripts/archive/backfill_community_access_format.py`,
not an ongoing data-preserving migration. Any field the model isn't confident
about is drafted as the literal sentinel string `"Needs verification"`
(`linklib.enrich.NEEDS_VERIFICATION`) instead of a guess — deliberately a
different mechanism from `community_profiles.needs_review` above (that one is
Brian's manual whole-profile sign-off; this one is a machine-set, per-field
gap marker on the basic listing, so the two get distinct labels rather than
sharing the "Needs review" text). Each of the five enum `<select>`s carries
`"Needs verification"` as a literal, selectable option, distinct from that
field's real default, so a drafted gap is visually a value, not just an
empty/default-looking field. `webapp/app.py::_public_community` is the single
choke point every public-facing community route (`/tools/communities`,
`/tools/communities/compare`, `/tools/communities/{slug}`,
`/tools/communities/find/results`) runs a community dict through before
rendering. **Reversed from the original design** (which blanked the sentinel
so an unreviewed gap never reached a visitor at all): per Brian's call while
the Software/Community richness work was landing, unresearched fields now
render *visibly*, flagged rather than hidden, so visitors can see what's
been researched while a review pass is still in progress —
`_public_community` is now a no-op copy kept as that same choke point in
case a field needs public-side handling again, and the actual rendering
happens through `_verify_html` (server-rendered compare/profile pages) or
the parallel `commVerify` JS helper (the JS-templated `/tools/communities`
directory card), both keyed on the exact `NEEDS_VERIFICATION` sentinel
string and both rendering the same muted, dashed-border "Needs
verification" flag — visually distinct from a confirmed value's vivid
`comm-cost`/`comm-cat` badge styling, so a visitor can't mistake a
still-unresearched field for a real one. `_community_geo_line`
(server) and `commGeoLine` (client) both special-case an unverified
`reach` explicitly, since their normal fallback logic (no `local_markets`
→ guess "National · online") would otherwise present a guess as if it
were confirmed. On `/admin/tools/communities`, a community with any gap
still gets the same passive "N fields need verification" badge (distinct
styling and text from the "Needs review" badge) — a nudge toward Edit, not
a save blocker; there's no dedicated free-text search across the admin
communities table today; the table isn't paginated, so the sentinel text
is reachable with a browser find until/unless pagination is added later.
Cost lands in the same `enrichment_cost` ledger, `article_id=NULL`.

**Automated Software research (search overhaul automation follow-up;
narrowed to agent-taxonomy-only in the Feature Taxonomy Phase 1b PR 2
legacy retirement)** replaced Phase 4b's guessed-path grounding with a real
crawl. `linklib/enrich.py::_discover_nav_pages` fetches a tool's homepage
and parses its own `<a>` nav links for ones whose text matches
product/solution/platform/feature/agent/AI keywords (same-domain only,
deduped, capped at 10) — a vendor's real Product or Agents page can live at
any slug, so parsing the site's own navigation finds it where guessing
`/pricing`, `/solutions`, `/product` (still the fallback when nav discovery
finds nothing, e.g. a blocked fetch) often missed it entirely.
`_fetch_taxonomy_grounding` fetches the homepage plus up to 10 discovered
pages (up to 10k characters each, 60k total) and feeds all of it to one
Claude call, `generate_tool_agent_taxonomy`, which drafts a whole-tool
`agent_taxonomy_note` (3-6 sentences, instructed to name every specific
agent the content mentions — not just the first one noticed — and to say
plainly when "AI-powered" language doesn't actually describe agentic
behavior). It used to also draft 8-15 `tool_features` rows (feature name,
standalone-vs-bundled availability, a substantive note) in the same call —
dropped along with the `tool_features` table itself; curated features are
now managed directly on the tool edit page's governed checklist
(`category_features`/`tool_feature_links`), not LLM-drafted. The
`confident` flag becomes `agent_taxonomy_needs_verification` on write. This
call runs at up to 2000 output tokens (lowered from 4000 once the response
narrowed to the taxonomy summary alone) — a deliberate tradeoff since
profile quality matters more than the per-tool cost here. Two trigger
points, both confirmed deliberately rather than picking one: (1)
automatically, via `BackgroundTasks`, right after a tool is added —
`webapp/app.py::_run_tool_research`, fired from both `/admin/tools/new` and
the public `/tools/submit` form, so a slow/failed research call never
blocks the add from completing; and (2) on demand, from a "Generate
summary" button (renamed from "Refresh AI research," moved next to the
Agent taxonomy field in the Phase 4 edit-page button reorg, then
standardized to the "Generate summary" label shared by every AI-draft-into-
field button on both the Software and Communities edit forms) on
`/tools/software/{slug}/edit` (`POST /admin/tools/software/{tool_id}/research/refresh`
— moved here in the admin URL convention PR, see below),
which runs the same `_run_tool_research` synchronously so the redirect can
show a success/failure banner — for re-running after a vendor redesigns
their site, or backfilling a tool added before this pipeline existed. The
field this writes lands via the needs-verification-flagged draft path
(`set_tool_agent_taxonomy_draft`) — never auto-confirmed; a "Mark verified"
action next to the Agent taxonomy field is how an admin reviews and clears
the flag (or just edits the field directly, which clears it as a side
effect via `update_tool_agent_taxonomy`). **The flag is now a publish gate,
not just a badge (2026-08 fix)** — a confirmed fabrication on Abacum's
record (invented, quoted-sounding language attributed to a page that never
existed, self-flagged `confident: false` by the model but never blocked
from rendering) showed that a badge alone lets an unreviewed, possibly-
fabricated note sit visible to every visitor indefinitely. Both public
render sites (`/tools/software/{slug}`'s "Agent taxonomy" block and the
compare matrix's "How agents are involved" row) now render an unverified
note only to a signed-in admin — labeled "unverified—hidden from
visitors"/"hidden from visitors until reviewed" — and read as absent
("Not documented yet" on Compare, no card at all on the profile page) to
everyone else. A verified note renders with no badge at all — a note
visible to the public is itself the verified signal now, replacing the
old always-visible-with-a-badge behavior. Cost lands in the same
`enrichment_cost` ledger, `article_id=NULL`. The underlying grounding gap
this surfaced — the generation prompt has no citation/quote-tie-back
mechanism the way `linklib/agent.py`'s FP&A Buddy does, so the `confident`
self-report is prompt-discipline only, not mechanically enforced — is
tracked as separate, not-yet-scoped follow-up work; see CLAUDE.md's "Agent
taxonomy publish gate" bullet for the full investigation.

**`scripts/enrich_agent_taxonomy.py`** (renamed from
`enrich_tool_features.py` in the Feature Taxonomy Phase 1b PR 2 legacy
retirement, which dropped this script's feature-drafting half; search
overhaul Phase 4b, extended by the automation follow-up above) is a
different shape from the `generate_*` web routes and the auto-trigger: a
standalone CLI batch job, not an admin-page button or a per-add trigger,
since it's meant to run against many tools at once under Brian's own API
credits rather than one row at a time — this is what re-enriched the
~240-tool existing catalog once the real-crawl grounding replaced the
original guessed-path approach. It calls the exact same
`generate_tool_agent_taxonomy` described above. Re-running is safe: a tool
that already has an `agent_taxonomy_note` is skipped (not re-drafted)
unless `--force`. Requires explicit scope (`--tools` or `--limit`) —
deliberately has no "run against everything" default, and `--dry-run`
reports what would be drafted for the agent-taxonomy note (and a projected
full-catalog cost) without writing. Nothing written by this script is
treated as reliable until the same admin review each row needs anyway
(`/tools/software/{slug}/edit`'s Agent taxonomy field) — the Phase 5
comparison matrix's "How agents are involved" row is what actually reads
this data, and only once reviewed. Curated feature data (Feature Taxonomy
Phase 1) is a separate, hand-curated admin workflow with no bulk-LLM-draft
script of its own — see the `category_features`/`tool_feature_links` schema
row above and the Feature Taxonomy section below.

**`scripts/enrich_community_profiles.py`** is the Communities equivalent of
the script above — a standalone CLI batch job wrapping the exact same
`generate_community_profile` call the "Auto-fill from URL"/"Regenerate"
button on `/tools/communities/{slug}/edit` makes one community at a
time, for running it against many communities in one pass instead of
clicking that button repeatedly. Every profile it writes lands via
`upsert_community_profile` with `needs_review=1` — never auto-confirmed,
same review contract as the admin button and as the Software agent-taxonomy
draft above; the admin communities list's existing "N pending review"
badge/filter is how these get reviewed. Because `upsert_community_profile`
fully replaces every `community_profiles` column rather than partially
patching it, and `generate_community_profile` only drafts sixteen of that
row's fields, this script reads the existing row first and passes every
other column — the retired `primary_purpose`/`cpe_eligible`/`platform_type`/
`meeting_format`/`event_style`/`seniority_band`/`resources_included` fields —
straight through unchanged, the same "echo every field back or it gets silently blanked"
discipline `tools.summary` needed in the Software bulk-edit route. Re-running
is safe: a community whose profile already has a non-empty `ideal_member` is
skipped unless `--force`. Requires explicit scope (`--communities` or
`--limit`) — no "run against everything" default — and `--dry-run` reports
every field that would be drafted (and a projected full-catalog cost)
without writing.

**Software admin rename + column picker/bulk edit (both tables).** The Software
admin page moved from `/admin/tools` to `/admin/software` (hard cutover, no
redirect), then again from `/admin/software` to `/admin/tools/software` — each
step a hard cutover with no redirect; both prior URLs now 404. The second move
also brought the bulk-edit sub-route along (`/admin/software/bulk-edit` →
`/admin/tools/software/bulk-edit`), matching the `/admin/tools/communities/
bulk-edit` naming pattern. Every other `/admin/tools/*` sub-path
(`/admin/tools/communities`, `/admin/tools/benchmarks` — renamed to
`/admin/tools/resources` in the admin URL convention PR, see below —,
`/admin/tools/leads`, etc.) is a distinct admin area under the historical
"Toolbox" URL prefix and was left alone both times. (The per-entry edit page itself moved again in
Phase 2, off `/admin/tools/{id}/edit` and `/admin/tools/communities/{id}/edit`
entirely — see "Domain-derived slugs" below. `/admin/tools/categories` did
later join the `/admin/tools/software/*` convention, but not until the
Feature Taxonomy PR below — see that section's "admin URL convention" note —
with a 301 redirect kept at the old URL, unlike this section's two earlier
hard cutovers.)
Both the Software and Communities approved-rows tables gained a
matching column picker and bulk-edit panel — `_admin_column_picker_html` and
`_admin_bulk_panel_html` in `webapp/app.py` render one shared, table-key-
parameterized UI (`_ADMIN_BULK_EDIT_JS`) reused by both pages, the same
pattern `saveCommunityWeights()` already used for a no-reload settings save.
The column picker persists per-table to `localStorage`
(`cfo_admin_cols_communities` / `cfo_admin_cols_software`) and only toggles
`<td>`/`<th>` visibility client-side — no server round-trip. Bulk edit is
scoped to shared/categorical fields only, never the per-record unique ones
(`name`, `url`, `description`/`notes`, vendor contact fields, `submitted_by`):
`_COMMUNITY_BULK_FIELDS` (`cost_band`, `sponsorship_type`, `access`, `format`,
`reach`, `categories`, `featured`, `advisor`) and `_SOFTWARE_BULK_FIELDS`
(`categories`, `advisor`, `promoted`, `warm_intro_enabled`) are the server-side
allowlists `POST /admin/tools/communities/bulk-edit` and
`POST /admin/tools/software/bulk-edit` check the requested `field` against before
touching the DB — enum fields (`cost_band`/`sponsorship_type`/`access`/
`format`/`reach`) are further checked against their fixed option lists
(`_COMMUNITY_BULK_SELECT_OPTIONS`). Both routes loop the selected ids, fetch
each row's current values, and call the existing `update_community`/
`update_tool` with just the target field overridden — no new `Library`
methods. The confirmation summary ("Set 'Cost band' = 'Free' on 6 rows.") is
built client-side before the POST fires; the apply itself is a single
`fetch()` that reloads the page on success.

**Bulk delete (both tables).** `_admin_bulk_panel_html` takes an opt-in
`show_delete_button` flag (both Software and Communities pass it) that
renders a second "Delete selected" button next to "Edit selected," plus its
own confirm panel — same enable-when-checked wiring as the bulk-edit button
(`updateBulkButton()` now also toggles a `{table}-bulk-delete-btn` if one
exists in the DOM). Clicking it calls `POST /admin/tools/{software|communities}/bulk-delete-check`,
which resolves the selected ids to names and does a lightweight check —
not a full blast-radius report — for whether any selected row is curated as
a competitor/similar-entity on another (non-selected) row's
`tool_competitors`/`community_competitors` row; any hits are shown as a
non-blocking warning listing which entry is referenced and by whom, so the
admin isn't surprised after the fact but nothing stops the delete. Confirming
calls `POST /admin/tools/{software|communities}/bulk-delete`, which loops the
selected ids through the same `Library.delete_tool`/`delete_community` the
single-row Delete button already uses (cascades `field_reviews` and the
competitor-pair table for each deleted id) — no new deletion logic, just the
existing path applied per id in the selection. The response's `tools` key
name is shared by both tables' JSON (the client-side `renderDeleteSelectedPanel`
is table-key-generic, same as the rest of `_ADMIN_BULK_EDIT_JS`), even for
Communities rows.

**Select-all respects the sort/filter toolbar (both tables).** `selectAllRows()`
skips any `.{table}-row-cb` checkbox whose `<tr>` is currently `display: none`
— a filtered-out row `applySortFilter()` hides rather than removes from the
DOM — so checking the header box means "select all visible," not "select
all," on either table. Fixed after #268 shipped bulk delete and a filtered
"select all" was found to silently include hidden rows.

**Sort + filter (both tables).** Same client-only approach as the column
picker above, and for the same reason — neither table paginates, so the full
row set is already in the DOM and there's no need for a server round-trip.
`_admin_sort_filter_toolbar_html` (`webapp/app.py`) renders a "Sort by"
`<select>` + ascending/descending toggle, per-field filter `<select>`s for
scalar/enum columns, and an OR-matched categories checkbox filter, reusing
`_admin_row_data_attrs` to stamp lowercased `data-*` attributes (`data-name`,
`data-cost_band`, `data-categories="cat-a|cat-b"`, etc.) on each `<tr>` —
`_ADMIN_SORT_FILTER_JS`'s `applySortFilter()` reads those attributes rather
than visible `<td>` text, so filtering still works correctly when a column is
hidden by the picker. Default sort is Name ascending on both tables, matching
the existing server-side `ORDER BY name` in `list_communities`/`list_tools` —
the toolbar is a pure client-side view on top of that default, not a
replacement for it (a no-JS load still shows the same alphabetical order).
Sort/filter targets: Communities gets `cost_band`/`access`/`sponsorship_type`/
`format`/`reach` (scalar `<select>`s, AND-combined) plus `categories`
(pill filter, OR-combined) — the same field set the Phase 1 column picker
already exposes. Software has no comparable enum field (Phase 0 found no
pricing/tags column distinct from `categories_json`), so its filter is
`categories` only, with `promoted` (Featured) added as a second sort option
alongside Name. Both tables also get a live name/URL search box. Sorting
re-orders `<tr>`s via repeated `tbody.appendChild()` — appending an
already-attached node moves it rather than duplicating it, so this reorders
in place; filtering toggles `style.display`. A `Showing N of M` counter and a
`Reset` button read the same row set the sort/filter logic does.

`_admin_sort_filter_toolbar_html` takes two opt-in params, both tables use
them: `category_style="pills"` renders the categories checkboxes as
always-visible, multi-select toggle pills (`.admin-cat-pill`, mirroring the
`.tcat-btn` pattern on the public `/tools/software` directory) instead of the
function's default click-to-reveal `<details>` dropdown (kept as the
fallback for any future caller that doesn't opt in), and `search_placeholder`
adds a live `<input type="search">` (no submit button) that AND-filters
against a `data-search` attribute (`_admin_row_data_attrs`, lowercased name +
URL). Software adopted pills + search first (the dropdown was hard to
discover and didn't render reliably); Communities' admin table picked up the
identical treatment right after, per the working agreement that a UI/UX fix
made on one admin table gets checked against the other and ported over where
it applies. Pills and the dropdown both feed the same
`#{table_key}-filter-categories input:checked` read in `applySortFilter()`,
so the OR-within-categories / AND-with-everything-else filtering semantics
are identical either way — only the affordance differs.

**Duplicate-URL blocking on save (both tables, create and edit).**
`linklib.db.DuplicateURLError` and a `_find_tool_by_normalized_url`/
`_find_community_by_normalized_url` lookup on `Library` guard `add_tool`,
`update_tool`, `add_community`, and `update_community` — each compares the
incoming URL's `normalize_url()` value against every other row's, and raises
if it matches. Deliberately placed at the `Library` layer rather than only in
the admin route handlers, so any caller is covered, not just the admin form —
this is what let `scripts/seed_tools.py`/`scripts/seed_communities.py` keep
working: their own re-run idempotency check used to be an exact-string
`WHERE url = ?` lookup, which would have missed a normalized-duplicate (e.g.
a `www.` variant) and then crashed on the newly-enforced `DuplicateURLError`
when it tried to insert it as new — both scripts' lookups were switched to
build an `{normalize_url(url): row}` map up front and compare against that
instead, so they now recognize the same rows `add_tool`/`add_community`
would. On `update_tool`/`update_community`, the check only runs when the URL
is actually changing (compared via `normalize_url()` against the row's
current stored URL) — critical so the bulk-edit routes above, which always
resave a row's own unchanged URL as part of every bulk update, can never trip
on a duplicate that has nothing to do with the field they're actually
changing. `webapp/app.py`'s four submit routes
(`admin_tools_new_submit`/`admin_tools_edit_submit`/
`admin_communities_new_submit`/`admin_communities_edit_submit`) catch
`DuplicateURLError` and turn it into a `400` whose `detail` names the
conflicting entry and links straight to its edit page
(`_duplicate_url_message`) — same `HTTPException(400, detail=...)` mechanism
already used for the existing required-field validation on those routes, not
a new error-rendering pattern.

**Metros -> free text migration.** The directory's geography field started as
an 18-city checkbox grid (`_COMMUNITY_METROS`, backed by `metros_json`, a
controlled vocabulary) with a public click-to-filter `<select>` on
`/tools/communities`. The fixed list was too narrow — a community with a
chapter in a city not on the list had no way to say so — so it was replaced
with a single free-text `local_markets` field ("Boston, New York, SF Bay
Area"), and the click-to-filter region `<select>` was removed rather than
left pointing at nothing: a visitor now finds a specific city through the
existing `comm-search` search bar instead, which already indexed `metros`
client-side and now indexes `local_markets` the same way — filtering moved
from click to search rather than disappearing outright. `metros_json` is
frozen in place rather than dropped (same no-destructive-migration precedent
as the retired `*_tags` columns above); `Library._migrate_community_local_markets`
backfilled every existing community's `local_markets` from whatever
`metros_json` already held, once, on first boot after the migration.
`local_presence` (the Recommender weighting dimension) now derives from
`local_markets` non-empty instead of `metros_json` non-empty — same
semantics, new source column. The Communities Recommender quiz
(`/tools/communities/find`) never asked about location, so it needed no
changes.

**Bulk community-profile import** (`scripts/archive/import_community_profiles.py`,
one-time). Unlike the single-community generate-profile flow above (which
drafts fields from a live page fetch), this imports pre-researched profiles
for many communities at once from `scripts/_community_profile_data.py` — a
static module of researched field values, reconciled against manual
corrections. For each community it runs the 11 narrative fields
(`linklib.enrich.VOICE_REWRITE_FIELDS` — everything except `notable_members`
and the 8 short factual/categorical fields) through one Claude call per
community (`linklib.enrich.voice_rewrite_community_fields`), using the live
`voice_core` setting as the style guide: a **style pass only** — every number,
date, dollar figure, and specific claim must survive unchanged, only tone and
sentence structure are rewritten. Cost lands in the same `enrichment_cost`
ledger as the flows above (`article_id=NULL`). Every imported row is saved
with `needs_review=1`, so the profile is live on its public page immediately
(the directory isn't left thin while Brian works through reviews) but flagged
on the admin communities list — a badge plus a `?filter=needs_review` link,
and a per-row "Mark reviewed" action that clears the flag — until he's
personally read and approved it. The count also folds into the shared admin
badge system (`webapp/tasks.py::open_task_counts`) alongside pending
submissions on the same `/admin/tools/communities` href.

`--only NAME` restricts a run to a single community (exact `name` match) —
for a community whose research lands after the original batch (e.g. a later
research round covering one new addition), so it can be imported without
re-voice-rewriting (and re-spending on) every community already done.

**Targeted research updates** (`scripts/archive/patch_round3_community_profiles.py`,
and any similar later-round script following the same shape) handle the
other case: a later research round that only deepens a handful of fields on
an *already-imported* community, not the whole profile. These use
`Library.update_community_profile_research_fields` — a genuine partial
`UPDATE` (only the columns actually passed are touched) — rather than
`upsert_community_profile`'s full-row replace, so fields outside that
round's scope are never disturbed. The voice-rewrite pass, when one runs, is
scoped the same way: only the specific narrative fields the round actually
re-researched go through `voice_rewrite_community_fields`, not all 11.

**Community submissions.** `GET/POST /tools/communities/submit` mirrors the
tool-submission flow (`/tools/submit`) exactly, deliberately trimmed to just
name + URL (no description/categories, since a pending community is thin
until Brian writes or generates its profile). It's member-gated like the
tool form (`_is_member`), and is reachable from a single subtle text link at
the bottom of `/tools/communities` ("Know a community that belongs here?
Submit it for review →", or "Sign in to submit →" signed out) — the same
auth-aware footer-link pattern used at the bottom of `/tools/software`. The
submission lands via `add_community(...,
submitted_by=..., approved=0)`, fires the internal notification email plus a
`COMMUNITY_SUBMISSION_*`-templated confirmation to the submitter (both
admin-editable at `/admin/emails`, same `_send_email_safely` best-effort
pattern as tool submissions), and waits at `/admin/tools/communities` in a
"Pending submissions" table above the "Approved communities" list — the same
two-section layout as `/admin/tools/software`. `POST
/admin/tools/communities/{id}/approve` calls `approve_community` and redirects
straight to `/admin/tools/communities/{id}/profile` (rather than back to the
list) so the "Generate summary" button is immediately in front of
Brian for a community that would otherwise sit thin in the directory;
`POST .../reject` deletes the row, mirroring `approve_tool`/reject for
tools. Pending-count badging (`lib.count_pending_communities()`) feeds
`webapp/tasks.py::open_task_counts` at `/admin/tools/communities`, the same
mechanism as pending tool submissions.

**Community gap-collection** (Phase 5) is reachable three ways: a subtle text
link at the bottom of `/tools/communities`, directly below the submission
line ("Can't find the right one, or the one you're in isn't quite enough?
I'd love to know what's missing →" — this and the submission line replaced
an earlier seafoam CTA card that carried the same prompt plus a "Suggest a
community" button), a zero-result search state ("No communities match. Tell
me what's missing →", the link inline in the message itself rather than
requiring the visitor to notice a separate CTA elsewhere on the page), and a
per-profile "Not quite the right fit?" mini-CTA on
`/tools/communities/{slug}` that pre-fills `closest_community_id`. The quiz
mention ("Not sure which community's for you? Take the quiz →") is a
separate, unrelated link now folded inline into the subtitle paragraph at
the very top of the page, rather than paired with the gap-form link in a
shared CTA block — the two used to sit together below the filters, but the
gap link's natural home is bottom-of-page alongside the other submission
prompts, while the quiz is a wayfinding aid that belongs with the intro
copy. All three gap-collection entry points
land on `GET /tools/communities/gap`, which builds a transparency note from
whatever session state it can detect — the directory's search/filter state
(passed via query params, since those filters are pure client-side JS state
never otherwise posted to the server) and profiles viewed via the
`cfo_visitor` cookie — telling the visitor what was picked up rather than
capturing it silently. Both the bottom-of-page and zero-result gap links are
built client-side by the same `gapFormHref(isZero)` helper, so the query
params (search/filter state, plus `zero=1`) stay identical regardless of
which entry point a visitor uses. `POST /tools/communities/gap` needs no
login and recomputes `viewed_community_ids_json` server-side from
`community_profile_views` rather than trusting a client value. Triage lives
at `/admin/community-gaps`, and unreviewed submissions feed the shared admin
badge system (`webapp/tasks.py::open_task_counts`) the same way pending tool
submissions and unread contacts do.

**Suggest-a-correction.** `GET/POST /tools/communities/correct?community_id=<id>`
is the "this specific field is wrong" counterpart to the gap form's "this
directory is missing something" — a public, no-login, single free-text field
("What's incorrect or out of date?") plus optional email, linked from the
bottom of `/tools/communities/{slug}` as "Something here out of date?
Suggest a correction →". Unlike the gap form, `community_id` is required
(the route 404s without a valid one, since a correction is never about "none
in particular"); registered ahead of `/tools/communities/{slug}` in
`webapp/app.py` for the same reason `gap`/`submit`/`compare`/`find` are, so
`correct` isn't swallowed as a slug. Rather than a new table, it reuses
`community_gap_submissions` with `submission_type='correction'` — storing
just `gaps` (the free-text report) and `closest_community_id`, leaving
`current_communities`/`looking_for`/`search_context_json` empty since a
correction isn't a directory search — and is triaged alongside gap/recommender
submissions at `/admin/community-gaps`, which renders a "Correction" type
badge and shows the correction text under "What's incorrect or out of date"
instead of the gap-form's three-field layout. Never auto-applied to the
listing; it's capture-and-triage only, same as gap submissions.

**Feature reference.** A collapsible "How this works" block at the top of
`/admin/tools/communities` (`_COMMUNITIES_REFERENCE_HTML` in `webapp/app.py`)
is the durable, in-admin record of every user-facing prompt/CTA across the
feature and how the `cfo_visitor` anonymous-tracking mechanism works —
static reference content, not a DB-backed editable field, since it documents
what the code does rather than something Brian tunes. Any PR that changes
Communities-feature copy or tracking mechanics must update it in the same
PR (see the Documentation rules in `CLAUDE.md`).

**Community compare** (Phase 6). `GET /tools/communities/compare?ids=<id>,<id>,<id>`
renders 2-3 selected communities side by side, reusing the same directory-card
fields (region/access/sponsor/cost) and `_COMMUNITY_PROFILE_PUBLIC_FIELDS`
profile fields as the single profile page. `ids` is a plain comma-separated
query param — deduped and capped at 3 server-side, with unapproved/unknown
ids silently dropped — and carries no session or server-side selection state,
so a compare URL is copy/paste-able and bookmarkable on its own. The
selection itself lives only in the directory page's JS (`compareSelected`, a
capped array persisted across re-renders as the visitor filters/paginates);
a sticky compare bar surfaces the count and the link once 1+ communities are
checked, and disables further checkboxes with an inline message (not a
browser alert) once the cap of 3 is reached. Rows in the comparison table are
per-field: a row renders only if at least one selected community has content
for that field, and any still-empty cell in a rendered row shows "Not
available yet" rather than leaving a blank or erroring — the same
degrade-gracefully contract as a thin/profile-less community on the single
profile page, just applied per cell instead of to a whole page. Registered
before `/tools/communities/{slug}` so "compare" isn't swallowed as a slug,
same reasoning as `/gap` and `/submit`.

**Communities Chat Matchmaker** (replaces the quiz below). `GET
/tools/communities/find` is now a free-type chat, not a 4-question form: the
visitor describes what they're looking for, Claude (`linklib/matchmaker.py`)
asks a small number of clarifying questions, then narrows to 2-3 best-fit
suggestions with links to their profile pages. Unlike FP&A Buddy, there's no
retrieval layer — every approved community's directory listing plus its
Community Profile rides as full context in the system prompt on every turn
(`_build_communities_context`), since the ~38-community dataset is small
enough that this is cheap and simpler than retrieving a subset of it. The
system prompt is cached server-side (Anthropic prompt caching,
`cache_control` on the system block), since that context is identical across
every turn of every visitor's conversation until a community changes.

`POST /tools/communities/find/chat` is the turn-by-turn API, mirroring `POST
/ask`'s server-rebuilds-history-from-DB-rows contract exactly (see the Ask
sequence diagram below) but against its own table, `matchmaker_questions`,
kept separate from `ask_questions` so FP&A Buddy and the matchmaker(s) track
spend against independent monthly dollar caps — a matching conversation can
run more back-and-forth turns than a typical FP&A Buddy question even though
each turn is individually cheaper (no retrieval, no web search, no
citations). Unlike Ask, **this page needs no login** (same as the quiz it
replaced), so `matchmaker_questions.user_id` is nullable and rate limiting
keys off the anonymous `cfo_visitor` session cookie for the common signed-out
case (`matchmaker_cost_this_month_session`), falling back to a per-user cap
(`users.matchmaker_cap_usd` / `settings['matchmaker_default_cap_usd']`,
same override-else-default shape as Ask's cap, admin controls on
`/admin/users`) only when the visitor happens to be signed in. Thumbs
up/down feedback on suggestions is UI-only, session-scoped — no server call,
no persistence, unlike Ask's `ask_feedback` table.

The old quiz's best-fit weighting infrastructure described below (dimensions,
per-community `*_tags` columns, the `/admin/tools/communities` "Recommender
ranking weights" panel) has since been **removed entirely**, by Brian's
explicit call: it was never read by the Matchmaker and had no other
consumer, so the admin panel, its Python helpers
(`_WEIGHT_DIMENSIONS`/`_get_default_community_weights`/`_community_weight_
tags`/etc.), the eight `*_tags` columns those dimensions wrote
(`seniority_band_tags`, `cpe_eligible_tags`, `platform_type_tags`,
`function_tags`, `looking_for_tags`, `programming_tags`, `paid_free_tags`,
`industry_tags`), the four already-retired `*_tags` columns from before it
(`primary_purpose_tags`, `resources_included_tags`, `meeting_format_tags`,
`event_style_tags`), and the one-off classification scripts that populated
them (`scripts/backfill_community_weight_tags.py` and the
`scripts/recategorize_*.py` family) are all gone. `community_profiles`' free-
text columns of the same base name (`seniority_band`, `platform_type`, etc.)
are untouched — the Matchmaker reads those directly, needing no controlled
vocabulary. The dimension-by-dimension design history below is kept as a
historical record of a system that no longer exists, same as the "Community
recommender" section that follows it.

**Software Chat Matchmaker** (Phase 2 — same pattern, applied to Software; no
existing quiz to replace, so this is a new build rather than a route swap).
`GET /tools/software/find` / `POST /tools/software/find/chat` mirror the
Communities matchmaker exactly (same chat UI, same server-rebuilds-history
contract), with `linklib/matchmaker.py::_build_software_context` sending
every approved Software entry's directory fields (summary,
competitive_differentiation, agent_taxonomy_note) plus its governed key-feature links (`tool_feature_links`) as
system-prompt context instead of the Communities dataset — the ~150-tool
dataset is still small enough to send as full context rather than retrieve a
subset. `_build_system(lib, kind)` shares the conversation-shape instructions
and voice layering between both matchmakers, branching only on the dataset,
link format (`/tools/software/<slug>` vs. `/tools/communities/<slug>`), and
the clarifying-question hint text (finance function / integrations / budget
vs. role / stage / access).

Both matchmakers write to the same `matchmaker_questions` table with
`kind='community'`/`kind='software'` distinguishing the rows, and — by
design, not accident — **share one monthly dollar budget**: a session or
user's cap is the SUM of `cost_usd` across both kinds
(`matchmaker_cost_this_month[_session]` never filters by `kind`), rather than
each matchmaker getting its own pool. A `/tools/software`-page CTA ("Not sure
which tool's for you? Software Matchmaker →") mirrors the Communities
directory's own inline CTA ("Community Matchmaker →").

**Community recommender** (Phase 7, historical — the quiz replaced above). A
4-question quiz at `GET /tools/communities/find` (role, budget, access, a
catch-all "anything more specific") routed to `GET
/tools/communities/find/results`, a filtered read of the directory. Each
answer mapped onto one of the directory's existing filterable dimensions —
`community_categories`, `cost_band`, or the access bucket the directory's own
`accessBucket()` JS helper already computes — so `_recommender_filter`
(removed from `webapp/app.py` along with the rest of the quiz's routes) was a
Python port of the directory's `commFiltered()` semantics (categories
OR-matched against each other, every dimension AND-matched against the
others). This filtering step was unchanged by the best-fit weighting below
and stayed the sole gate on which communities appeared at all; weighting only
reordered what had already passed it.
`POST /tools/communities/find` logged every completed quiz — including a zero
or thin result, the same kind of gap signal as a zero-result directory
search — as a `community_gap_submissions` row with `submission_type=
'recommender'`, reusing the `cfo_visitor` session infrastructure from Phase 5
(`get_viewed_community_ids`) rather than a parallel one, then redirected to
the plain, bookmarkable GET results page so a refresh or a shared link never
re-logged.

**Best-fit weighting** (historical — ranking within the quiz's filtered
results; orphaned by the Matchmaker above, see the note there). Filtered
results are sorted by a per-community weighted match score, `featured`
breaking ties same as everywhere else in the directory — replacing the
plain featured-first/alphabetical order the filter alone produced before
this. Scoring runs over 10 dimensions, defined once in `webapp/app.py`'s
`_WEIGHT_DIMENSIONS` (each with a fixed controlled vocabulary, an admin
label, a quiz label, and a `source`):
- 8 are `source: "profile"` — 3 of them correspond to a `community_profiles`
  column (`seniority_band`, `cpe_eligible`, `platform_type`) and read from
  that column's sibling `*_tags` JSON column (`seniority_band_tags`, etc.)
  — a controlled-vocabulary classification kept separate from the
  free-text research column of the same base name, because that prose was
  investigated and found too inconsistent for reliable keyword/substring
  matching (e.g. a `platform_type` of "not a Slack/forum" would
  false-match a naive "Slack" check; `platform_type`'s own vocabulary was
  later re-derived to Slack/Circle/Email/Proprietary, naming the actual
  platform rather than chat/in-person/mix — a community with no matching
  platform, or one on a platform outside this vocabulary like a LinkedIn
  group, gets an empty tag list rather than a forced fit). `function`,
  `looking_for`, and `programming` are post-#159 additions with no
  free-text sibling column of their own:
  - `function` (Overall finance org / FP&A / Accounting / Treasury) is
    classified straight from `ideal_member`/`value_prop`/`categories` into
    `function_tags`, splitting out the controller/accounting-focused
    distinction that `seniority_band` used to carry before its own
    vocabulary narrowed to a pure seniority read (CFO / Senior Exec (VP+) /
    Open to all).
  - `looking_for` ("What you're looking for": Peer discussions / Networking
    / Learning & education / Vendor connections / Resources & templates,
    stored in `looking_for_tags`) retired and merged two of the original 7
    dimensions — `primary_purpose` and `resources_included` — rather than
    relabeling them: the old `primary_purpose` vocabulary bundled "peer
    networking" as one option, this one splits it into Peer discussions
    (an active Q&A/discussion mechanic) vs. Networking (events/dinners/
    conferences), folds the old Resources included into one of five
    checkboxes, and adds Vendor connections as a wholly new concept (a
    vendor/tool-matchmaking service, e.g. GaapSavvy's auditor/tech-stack
    matchmaking) the old vocabulary never captured.
  - `programming` (Meals (dinners, etc.) / Conferences / Retreats / Virtual
    Panels, stored in `programming_tags`) retired and merged the other two
    original dimensions — `meeting_format` and `event_style` — deliberately
    reusing the "Programming" admin label that used to belong to
    `meeting_format` alone (a repurposing to a new vocabulary, not a
    naming collision to preserve). Derived from `format_reality` (the
    narrative field describing actual programming) rather than from
    `meeting_format`/`event_style`, since that's where this level of detail
    actually lives. A 5th originally-proposed option, "Demo Days," has no
    supporting text anywhere in the current research corpus and was left
    out rather than force-fit — a candidate for a future research round,
    same treatment as the `stage_focus` placeholder below.
  - `paid_free` (Dues: Free / Paid, stored in `paid_free_tags`) moved here
    from `source: "derived"` — it used to be computed on the fly as a
    single value (`["free"]` if `communities.cost_band == 'Free'` else
    `["paid"]`), which could never represent a freemium community with
    both a free tier and a paid tier (e.g. Finance Alliance, GaapSavvy,
    Startup CFO, CFO Connect). `paid_free_tags` is a dedicated column,
    independently editable from `cost_band` — a community's `cost_band`
    stays the single-value directory-listing fact shown on its own edit
    form, while `paid_free_tags` can diverge (e.g. `cost_band` reads
    "Free" while `paid_free_tags` is `["free","paid"]` for a freemium org).
    `scripts/recategorize_dues.py` seeded every community's `paid_free_tags`
    from its current `cost_band` (preserving the prior derived behavior)
    before applying the freemium overrides; Proformative is confirmed
    single-tagged (`["free"]`) rather than dual-tagged despite similarly-
    worded freemium language in its own research prose — Brian's read is
    that its free tier (forums, webinars) is the actual community/product,
    and its paid CPE courses are an add-on purchase, not a membership gate
    the way the freemium four's paid tiers are.
  - `industry` (Life sciences / Healthcare / Private equity/funds /
    Industry-neutral, stored in `industry_tags`) is a wholly new dimension,
    no free-text sibling column. Vocabulary drawn from the natural
    categories that emerged from the "Industry-specific"-tagged
    communities' research text during Phase 0 — deliberately narrower than
    that category's own framing (which also gestures at nonprofit and
    tech): no community in the current 38 is nonprofit-focused (the one
    candidate was already removed via `REMOVALS` before this Recommender
    build started), and "tech" overlaps the `stage_focus` placeholder's
    in-flight Research Round 4 work, so neither was added as an option with
    zero or contested matches. Only 4 of the 38 communities get a
    non-neutral tag (Finance & Accounting for Bioscience → life sciences;
    HFMA → healthcare; PECFOA and Private Funds CFO Network → PE/funds);
    the other 34 are `industry_neutral`.

  (Update: all four retired `*_tags` columns above, and the eight still-live
  ones described through the rest of this section, were later dropped
  entirely — see the note at the top of this Matchmaker/Recommender
  subsection for why and when. Their free-text siblings on
  `community_profiles` were never touched.)

  `platform_type`'s own vocabulary was also re-derived post-#159, from
  Slack/chat-based, In-person only, Mix to naming the actual platform:
  Slack / Circle / Email / LinkedIn / Proprietary. A community with no
  matching platform — pure in-person, or coordinating only over a tool
  like Zoom/Luma that isn't itself a community hub — gets an empty
  `platform_type_tags` list rather than a forced fit (the schema and
  scoring already support a dimension with no tags for a given community).
  `linkedin` was added specifically for Modern Finance Forum for CFOs,
  whose actual platform is a LinkedIn group — flagged during the initial
  pass as a real vocabulary gap rather than left untagged or force-fit into
  Proprietary.

  `scripts/backfill_community_weight_tags.py` hand-classified the original
  38 communities into the first 7 (now 3 of the current 7, since 4 were
  retired); `scripts/recategorize_level_function.py` re-classified
  `seniority_band_tags` into its narrowed vocabulary and classified
  `function_tags` for the first time; `scripts/
  recategorize_purpose_resources.py` classified `looking_for_tags`;
  `scripts/recategorize_platform.py` re-classified `platform_type_tags`
  into its new vocabulary; `scripts/recategorize_programming.py` classified
  `programming_tags` for the first time; `scripts/recategorize_dues.py`
  seeded `paid_free_tags` from `cost_band` and applied the freemium
  overrides; `scripts/recategorize_industry.py` classified `industry_tags`
  for the first time. New communities get theirs set via checkbox groups on
  the admin profile edit form (`/admin/tools/communities/{id}/profile`),
  alongside the free-text fields where one exists.
- 2 are `source: "derived"` — computed on the fly from an existing
  `communities` column instead of a stored `*_tags` column: `local_presence`
  (`local_markets` non-empty), and `sponsorship_type` (straight off
  `communities.sponsorship_type`, already a fixed 3-value enum —
  `Independent`/`Vendor-sponsored`/`Investor-sponsored` map onto the
  dimension's own `independent`/`vendor`/`investor` vocabulary).
  `_community_weight_tags` computes all 10 dimensions' tags uniformly
  regardless of source; `source` only changes two things — how that function
  derives the tag, and whether the dimension gets a checkbox group on the
  admin profile-edit form (only `"profile"` ones do, since a `"derived"`
  dimension's value already lives on the directory-listing edit form, e.g.
  `sponsorship_type`'s existing dropdown, so a second control there would
  just invite drift between the two).

For **all 10** dimensions — `"profile"` and `"derived"` alike, no
distinction — Brian sets a default **weight** (0–5, a
`community_weight_<dim>` setting) AND a default **target value** (one or
more of that dimension's vocabulary, a `community_weight_values_<dim>`
setting storing a JSON array) at `/admin/tools/communities` — both are
required for a default to actually rank anything: a weight alone has
nothing to match a community's tags against. The admin UI edits both
together (`_get_default_community_weights`/
`_get_default_community_weight_values`), same no-reload settings pattern as
`/admin/voice`. (An earlier revision of this feature gave the 3 derived
dimensions no admin default at all, on the theory that they're structural
directory facts rather than a research judgment — that shipped as a bug:
Local presence and Cost were silently missing from the admin panel
entirely, and a weight with nothing to match against can't rank anything
regardless of source. Corrected so all 10 dimensions get the identical
default-weight-and-value treatment.)

The quiz's `GET /tools/communities/find` page added one further optional
step after the 4 filter questions: "What matters most to you?", a checkbox
group per dimension (all 10) letting a visitor check every value they'd
accept — not a single-choice control, since e.g. a visitor might find both
"CFO" and "Open to all" acceptable for `seniority_band`.
Skipping the whole step (simply not checking anything) was the same action
as leaving any individual dimension's boxes unchecked: **resolution was
per-dimension, not all-or-nothing** — `_recommender_effective_weights_and_
values` merged a visitor's checked values with Brian's admin default
independently for each of the 10 dimensions, so a visitor who only weighed
in on 2 dimensions got their own preference on those 2 and Brian's defaults
on the other 8. The visitor never set a numeric weight directly — only a
target value — the weight applied was always Brian's admin weight for that
dimension, regardless of which side (visitor or admin default) supplied the
target value. `_recommender_score` then summed the weight for every
dimension whose effective target value(s) intersected the community's
tag(s) for that dimension.

`POST /tools/communities/find` carried the visitor's checked values through
to the results page as repeated query params (`w_<dim key>=<value>`, same
bookmarkable-GET reasoning as the 4 filter answers) and, only when the
visitor checked at least one box (never on a skip), logged a *second*
`community_gap_submissions` row with `submission_type='weight_preferences'`
storing the chosen values as JSON in `search_context_json` — kept as its own
row rather than folded into the `'recommender'` row so this signal wasn't
diluted by the (majority of) quiz completions that didn't set any
preference. The results page stated plainly which weights ranked what was
shown (`_recommender_disclosure_html`): the visitor's own choices restated
back if they set any, "Ranked using Brian's default priorities" otherwise —
by design, no separate methodology explanation beyond stating the weights in
effect.

### Thought Leadership

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `thought_leadership` | Backs all four columns on `/thought-leadership` (Writing, Speaking & Events, Podcasts, Press) and their admin CRUD at `/admin/thought-leadership` (Phase 1 — see CLAUDE.md). Replaces the pre-Phase-1 mechanism, `webapp/thought_leadership_data.py` (33 hardcoded `TLItem`s), which stays in the repo unused as a rollback reference — see `scripts/archive/migrate_thought_leadership.py` for the one-time migration. | `type` (`'writing'`\|`'speaking'`\|`'podcast'`\|`'press'`), `sort_key` (`'YYYY-MM'`; `''` floats an item to the top of its section — **derived automatically from `date_label` on every save**, not a form field, since a follow-up fix; see CLAUDE.md), `display_order` (tiebreaker for items sharing a `sort_key`, or both undated — preserves add/migration order rather than leaving ties to SQLite's row order; blank on the admin add form auto-assigns the next value per type), `needs_synopsis` (a blank `description` is deliberate, pending research, not skipped by accident), `featured_home` (originally "pin into the homepage teaser" — Phase 3 addendum; repurposed by the Homepage Restructure phase to mean "represents this type in the homepage's "Recent highlights" grid", see below; defaults to 0, no retroactive selection) |
| `original_content` | Original Content Phase 1 (2026-08) — card metadata (title/teaser/tag/link label) for the homepage's flagship row and `/thought-leadership`'s featured row, migrated off the hardcoded `_TL_FEATURED_CARDS` tuple in `webapp/app.py` (which stays in the repo, unimported, as a rollback reference — same precedent as `thought_leadership_data.py`) via the one-time `scripts/migrate_original_content.py`. Also the model for any brand-new piece authored entirely from admin going forward (Phase 2/3), with no code change per article. | `slug` (unique, URL segment under `/thought-leadership/`), `body_md` (**nullable, load-bearing**: `NULL` meant "card metadata only" for all three flagship rows at Phase 1 seeding — one of the three hand-built bespoke routes (`growth-engine-ratio`, `ai-hackathon-playbook`, `netsuite-mcp`) rendered the actual piece, and since those three rows' slugs are set to match their existing route path segments exactly, a literal route always wins over the generic `GET /thought-leadership/{slug}` catch-all by FastAPI's registration order, with no separate custom-route column needed; a real markdown string means the shared article template at that catch-all renders it instead. As of Phase 4c, all three flagship pieces — `netsuite-mcp` (4a), `ai-hackathon-playbook` (4b), and `growth-engine-ratio` (4c) — have real `body_md` and are served by the catch-all, their bespoke routes all retired; `growth-engine-ratio`'s own JS calculator moved to a brand-new standalone bespoke route, `/thought-leadership/growth-engine-calculator`, which is not part of this table at all), `status` (`'draft'`\|`'live'` — a draft is never public), `featured_home` (selects which live pieces the homepage's flagship row shows; `/thought-leadership` shows every live piece regardless), `date_label`/`sort_key`/`display_order` (same convention as `thought_leadership` above — `sort_key` is derived from `date_label` via the same `_sort_key_from_date_label`, reused verbatim). Ordering (`Library.list_original_content`) is **`display_order` first, `sort_key` only as a tiebreak** — the opposite priority from `thought_leadership`'s own `_TL_ORDER_SQL`, since this is a handful of curated flagship cards, not a chronological feed. `tag_color` (the small category-tag accent color on each card) was deliberately never promoted to a stored column — `webapp/app.py`'s `_oc_card_tuple` cycles it from the same 3 established colors (`--coral-deep`/`--seafoam-deep`/`--navy-light`) by card position, so the 3 migrated pieces render with their exact original colors and a 4th+ piece still gets a sane one. |

**Original Content Phase 2 (2026-08) — markdown rendering + `GET /thought-leadership/{slug}`.**
`_render_original_content_markdown` runs `body_md` through `python-markdown` with only
`fenced_code` and `tables` enabled (no syntax highlighting/Pygments), then wraps each rendered
`<table>` in the same `overflow-x:auto` container every other table on this site already uses
(`.ger-table-wrap`, the `.ns-table`/`.ns-trouble` wrappers) — markdown's `tables` extension emits
a bare `<table>` with no wrapper of its own, so this is a small regex post-process, not an
extension option; verified live at 390px portrait and 844×390 landscape that a genuinely wide
table scrolls inside its own wrapper without ever forcing the page itself to overflow
horizontally. Raw HTML embedded in `body_md` passes through unescaped — deliberate, since the
field is admin-authored only, never public input, so there's no injection surface to guard
against here. `_original_content_article_body` is the shared article template, matching the
three bespoke pieces' own shell exactly (`page page-full article-atlantic`, the same
`&larr; Thought Leadership` back-link, `.tool-prose`, the same eyebrow/`<h1>`/byline treatment) —
only the rendered markdown itself (wrapped in a scoped `.oc-body` div) differs from page to page.
`.oc-body`'s CSS mirrors the Reader's own `.reader-body` treatment for code/pre/table (the one
place this codebase already had an answer for "how does this site style rendered long-form
content"), with one deliberate difference: `blockquote` reuses this shell's own established Quote
treatment (`.article-pull`'s border-left/italic style, BRAND.md's four-type callout taxonomy)
instead of the Reader's coral box, since a coral-boxed quote would clash with BRAND.md's
"coral: rare warm accent, one per screen" rule inside the exact shell where Quotes are already
established as border-rule-not-box. `GET /thought-leadership/{slug}` is registered immediately
after `netsuite_mcp()` ends and before the unrelated `/play` route — confirmed in the Phase 0
investigation as the only gap where nothing else registers a conflicting route — so the three
literal bespoke routes above it always win by FastAPI's registration order; a row with a slug
colliding with one of the three bespoke pieces (deliberately possible, since nothing in the
schema prevents it) still loses to the bespoke page, proven by
`test_bespoke_literal_routes_win_over_catch_all_even_on_slug_collision`. Serves only a row with
`status='live'` AND a non-null `body_md`; a `body_md IS NULL` row 404s here too even though
route-registration order is what actually protects the three bespoke pieces in practice — that
check is defense in depth, not the primary mechanism. A `status='draft'` row 404s for a
signed-out visitor and renders normally, at its own canonical URL, for an active admin session
(`_is_authed`) — there is no separate preview URL or token.

**Original Content Phase 3 (2026-08) — admin CRUD at `/admin/original-content`.** Same
list/add/edit/delete pattern as `/admin/thought-leadership` (read first, matched, not built
from scratch), with one addition specific to this table: slug validation, since a bad slug here
is a real failure mode (an unreachable page, or a page that silently loses to a bespoke route)
that `thought_leadership`'s free-text title never risked. `_validate_oc_slug` checks, in order,
that the slug is present, well-formed (`_OC_SLUG_RE`, lowercase letters/digits/single hyphens
only — **not auto-lowercased**, an uppercase or malformed slug is rejected outright rather than
silently normalized, so what an admin sees in the URL is exactly what they typed), not one of
the three bespoke pieces' own route path segments (`_OC_RESERVED_SLUGS` — a colliding slug would
save fine but be permanently unreachable, since the literal route always wins registration
order), and not already used by a different row. A rejection re-renders the same form with the
submitted values preserved and an inline coral error banner — the richer pattern
`_feed_form_page`/`admin_feeds_new_submit` already established for exactly this kind of
validation, not `thought_leadership`'s own blunter raise-`HTTPException`-on-bad-input approach.
Slug edits are allowed at any time, including on a live piece — the edit form's helper text
warns that this breaks any existing link, since there's no redirect system (out of scope, same
as Phase 2's own decision). `sort_key` is derived from `date_label` on every save via the same
`_sort_key_from_date_label` `thought_leadership` uses — not a form field. `display_order` left
blank on add auto-assigns the next value (`Library.add_original_content`'s own `COALESCE(MAX...)
+ 1` default); left blank on edit is a deliberate clear, treated as `0` — same convention as
`thought_leadership`'s own edit route. `body_md` left blank keeps the row as card-metadata-only
(`NULL`, not `''` — `_oc_values_from_form` maps an empty textarea to `None`), the same state the
three flagship rows have always been in; a piece created this way still renders as a flagship/
`/thought-leadership` card immediately (`test_blank_body_md_stays_card_only_and_renders_on_homepage`)
and correctly 404s at its own `/thought-leadership/{slug}` page (Phase 2's `body_md IS NULL`
guard) until a later edit fills in a body. The admin index's "Thought Leadership" group gained a
second item pointing here — `count_label` is `len(items)`, so the badge updated with no
additional wiring.

**Original Content Phase 4a (2026-08) — the first bespoke page (`netsuite-mcp`) ported to a real
`body_md`; its Python route retired.** A read-only Phase 4 investigation (all three bespoke
pages, reported to Brian before any code) found none of the three has images, JS, or interactivity
— every custom visual device on all three (numbered step-tracks, use-case cards, colored tables, a
quick-reference box) is inline-styled HTML/CSS with no page-specific `<style>` selector doing
anything a raw HTML block in `body_md` can't reproduce exactly (verified directly: a raw HTML
`<div>`, a raw `<style>` block, and even a raw `<script>` block all pass through
`_render_original_content_markdown()` completely untouched). The one genuine exception — Growth
Engine Ratio's live JS calculator — has no equivalent on this page, which is why NetSuite MCP was
the pilot: closest to a "just structure, no interactivity" port of the three. Approved approach,
a hybrid: genuine prose became real markdown; the visually-designed elements (`.ns-case`/`.ns-tip`
use-case cards, `.ns-step`/`.ns-num` phase tracks, `.ns-table`/`.ns-trouble` tables, `.ns-note`,
`.ns-qr`) were preserved as raw HTML blocks in `body_md`, using their original CSS classes
unchanged. Those classes' CSS moved from the retired route's own `<style>` block into a new
`_OC_NETSUITE_MCP_CSS` constant, every selector rescoped under `.oc-body` (e.g. `.oc-body
.ns-table`) so it only ever applies inside a rendered Original Content article body, never
sitewide — `.oc-body .ns-table`'s two-class specificity deliberately outranks the shared
template's own generic `.oc-body table` rule, so this table keeps its original navy-header/
zebra-striped look instead of falling back to the generic styling. `scripts/
migrate_netsuite_mcp_content.py` (dry-run/`--apply`/write-then-read-back, same convention as every
other one-time content migration here) sets `body_md` on the existing Phase-1-seeded row, copying
the original page's prose verbatim — no rewriting — and also sets `date_label` to "June 2026"
(blank since the Phase 1 seed, since `_TL_FEATURED_CARDS` tuples never carried a date), restoring
the byline the bespoke page always showed. The bespoke `netsuite_mcp()` route function itself was
deleted outright (the `/netsuite-mcp` -> `/thought-leadership/netsuite-mcp` 301 redirect stays —
its target is still a real, correct URL, just served by the catch-all now instead of a Python
function); `"netsuite-mcp"` came out of `_OC_RESERVED_SLUGS`, since it's no longer claimed by a
bespoke route and is now an ordinary admin-editable slug like any other. **Verified before
deleting the route, not assumed**: with the DB row updated but the bespoke route still live, the
shared template's actual output was rendered to a standalone file (bypassing the still-live
bespoke route, which would otherwise win at the real URL) and screenshotted at desktop (1280px)
and mobile (390×844) against the live bespoke page at the same viewports. Content-structure
verification found all 23 headings present, in the same order, with identical text. Visual
verification found every ported designed element (use-case cards, the permission table, the
troubleshooting table, the quick-reference box) pixel-identical, and two small, deliberate,
documented deltas: inline `<code>` spans (e.g. `com.netsuite.mcpstandardtools`) now render with
the shared template's gray-pill background instead of the bespoke page's bare monospace text
(an inherent, expected side effect of `.oc-body pre,.oc-body code`'s generic styling applying —
arguably a consistency win, since it now matches `<code>` everywhere else on the site), and on
mobile, the italic subtitle line now renders just *after* the byline instead of just before it —
the shared template hardcodes the byline immediately following `<h1>`, and the subtitle (a field
`original_content` has no column for) had to become the first line of `body_md`, which renders
after that hardcoded byline. Fixing that ordering would mean changing the shared article
template's shape for every Original Content piece, not just this one — left as a known, minor,
documented deviation rather than a schema/template change bundled into a content port.

**Original Content Phase 4b (2026-08) — the second bespoke page (`ai-hackathon-playbook`,
"Sail, Don't Row") ported the same way; its Python route retired.** Same hybrid approach as
Phase 4a: real prose converted to markdown; the page's designed elements (a 2×2 value/effort
matrix, an Inspire→Sleep→Build flowchart with a `<640px` vertical-arrow responsive variant, a
Ship/Iterate/Park verdict tier strip, a resource-link list, a 6-step phase track used twice, and
a Notion intake-form template box) preserved as raw HTML in `body_md` using their original
`.fah-*` classes. Confirmed dead and dropped, not carried forward: `.fah-verdicts`/
`.fah-verdict`/`.fah-v-*`, `.fah-pull`, `.fah-motif` — defined in the retired route's own
`<style>` block but unused by any element in its actual body. `_OC_HACKATHON_CSS` (`webapp/app.py`)
holds the rescoped `.oc-body .fah-*` CSS, same `_OC_NETSUITE_MCP_CSS` treatment.
`scripts/migrate_hackathon_playbook_content.py` follows the same dry-run/`--apply`/
write-then-read-back convention, setting `body_md`, `date_label` ("June 2026"), and — unlike
Phase 4a — `title` on the existing Phase-1-seeded row.

**Two real bugs caught by screenshot-diff verification before the route was touched, neither
visible from reading the code:**
1. A double-escape bug — the row's `title` was seeded (Phase 1) as the pre-escaped
   `"Sail, Don&rsquo;t Row"`, meant for `_tl_fcard()`'s raw `<h3>` insertion; the shared article
   template's own `_esc(row["title"])` call for the real `<h1>` (correct in general, needed for
   plain-text admin-typed titles) double-escaped it, rendering literal `&rsquo;` text. Fixed with
   an explicit `TITLE = "Sail, Don't Row"` constant in the migration script (matching the retired
   route's own `<h1>` text byte-for-byte) rather than reusing `row["title"]`. Since the homepage
   and `/thought-leadership` flagship cards render straight from this DB row
   (`_oc_featured_cards_html`/`_tl_fcard()`, not the frozen `_TL_FEATURED_CARDS` tuple), this also
   changes those cards from the curly entity to a straight apostrophe — `tests/
   test_thought_leadership_homepage_teaser.py`'s fixture applies this same title fix after its
   Phase-1 seed so the suite doesn't drift stale against production the moment the migration ships.
2. Two CSS specificity gaps, found only by comparing real `element.bounding_box()` values (a
   full-page screenshot glance looked fine even with ~110px of real height difference buried in
   one repeated component). First: `.fah-body h3`/`.fah-template h3` relied on the original page's
   sitewide `body{font:16px/1.65 ...}` inheritance for their line-height, never declaring their
   own — but `_OC_ARTICLE_CSS`'s shared `.oc-body h1,h2,h3...{line-height:1.3}` rule (written for
   real prose section headings) matches these in-card h3s too, and an explicit declaration always
   wins over inheritance regardless of specificity, compounding a 6px collapse across every
   repeated card. Fixed by restating the original's effective `1.65` line-height directly on both
   selectors. Second, and opposite in direction: `.fah-body p`/`.fah-tier p` are each only one
   class + a tag on the original page, so they already lose their own line-height/margin-bottom to
   the sitewide `.article-atlantic .tool-prose p{line-height:1.75;margin-bottom:22px}` rule there
   (two classes always outranks one) — a pre-existing quirk of the live original page. Prefixing
   every selector with `.oc-body` for scoping gave exactly these two selectors a second class,
   tying the sitewide rule's specificity; since the article's own `<style>` tag loads after the
   sitewide one, the tie resolved the *opposite* way, so the port's declared values won where the
   original's never did. Fixed by dropping line-height/margin-bottom from both ported selectors so
   they lose to the sitewide rule again, matching the page's actual rendered behavior rather than
   "fixing" a CSS quirk the live site never showed. (`.fah-template p`, `.fah-r-desc`, and
   `.fah-flow-caption` were checked too and needed no change — none of them ever declared
   line-height/margin-bottom, so they already lost to the sitewide rule on both pages.) Phase 4a's
   near-identical `.ns-body h3`/`.ns-qr h3` selectors carry the same latent line-height gap and
   were not touched here, since NetSuite MCP is out of scope for this PR.

Post-fix, every `bounding_box()` comparison across the matrix, flowchart (both variants), tier
strip, resource list, template box, and both phase tracks matched to within 1-2px, and every
cropped screenshot pair was visually confirmed identical at desktop (1280px), mobile portrait
(390×844), and mobile landscape (844×390), with zero horizontal overflow at any width. Only then
was the bespoke `finops_ai_hackathon()` route deleted (the `/finops-ai-hackathon` 301 redirect
stays, now served by the catch-all) and `"ai-hackathon-playbook"` removed from
`_OC_RESERVED_SLUGS` — leaving only `"growth-engine-ratio"` reserved. The closing bio blurb's
`/play` easter-egg link carried over verbatim into `body_md`.

**Original Content Phase 4c (2026-08) — `growth-engine-ratio`, the last of the three
flagship pieces, ported with a genuine SPLIT rather than a whole-page port.** Unlike Phase
4a/4b, this page couldn't be ported as-is: alongside the prose/formula/table/quotes content,
it embeds a ~380-line live JS calculator (two modes — point-in-time and a bounded -2..+2
timeline — driving two dynamically-generated SVG charts, `contributionSVG()`/`buildChart()`)
that's genuinely interactive, not markdown-representable content. Resolution: the article
half ported the same way netsuite-mcp/ai-hackathon-playbook did (real prose to markdown; the
formula box, 3 pull-quotes, and the tier table preserved as raw HTML in `body_md` using their
original `.ger-pull`/`.ger-table` classes — `.article-cta`/`.article-pull` needed no porting,
since those are sitewide shared classes, not page-specific, already available everywhere).
The calculator moved to a brand-new standalone bespoke route,
`GET /thought-leadership/growth-engine-calculator` (`growth_engine_calculator()` in
`webapp/app.py`) — its markup, CSS, and JS extracted byte-for-byte from the retired route,
zero logic/input/chart change. `_OC_GER_CSS` (`webapp/app.py`) holds the rescoped
`.oc-body .ger-pull`/`.oc-body .ger-table` CSS, same `_OC_NETSUITE_MCP_CSS`/`_OC_HACKATHON_CSS`
treatment — only the article-side rules; the calculator's own CSS
(`.ger-grid-*`/`.ger-mode*`/`.ger-in`/`.qlabel`/`.qhead`/`.qrow-proj`/`.ger-chart`/
`.ger-contrib*`/`.tl-step`/`.tl-ctrl*`/`.ger-card`/`.ger-value-big`) stays on the new
calculator route's own `<style>` tag, since that page is still hand-built Python, never
routed through the markdown template. The retired page's "Methodology note" paragraph
(GTM/R&D GAAP definitions plus a timeline-lookback explanation) stayed on the calculator
page unmodified, in its original position directly below the calculator card, rather than
being split across both pages — it's calculator-specific (references the timeline's
lookback mechanics directly) and moving it verbatim was lower-risk than trying to split its
sentences between the two pages. `scripts/migrate_growth_engine_ratio_content.py` follows
the same dry-run/`--apply`/write-then-read-back convention as Phase 4a/4b's migration
scripts, setting `body_md` and `date_label` ("June 2026", same visual-parity fix) — `title`
needed no fix this time, unlike Phase 4b's hackathon row (`"The Growth Engine Ratio"` has no
HTML entities in it to double-escape).

The retired page's byline carried two lines beyond what the shared template's single
`date_label` field can represent — "Published with [The F Suite]" (a live link) and a
Contributor credit line for Katherine Zhang — both preserved verbatim as the first two lines
of `body_md` itself, the same "extra byline content becomes body_md's leading content"
precedent Phase 4b used for the hackathon piece's italic subtitle line. One genuinely new
piece of content: where the original page's "## Calculate Your Ratio" section held the live
calculator inline, the ported article now shows a CTA box (reusing the sitewide
`.article-cta` class, same visual treatment as the existing F Suite whitepaper CTA already on
the page) linking out to the new standalone calculator page — new copy, surfaced to Brian for
review before merge per the standing em-dash/new-copy process (see the PR description), same
as the calculator page's own new intro blurb.

Verified before either route changed: with the row updated and the new calculator page live
at its new URL but the old bespoke `growth_engine_ratio()` route still in place, screenshot
comparison confirmed visual parity of the article portion (desktop and 390×844 mobile), and
the new calculator page's both modes and both SVG charts were confirmed functioning
identically to how they worked embedded in the old page, at desktop and mobile widths. Only
then was the old bespoke route deleted and `"growth-engine-ratio"` removed from
`_OC_RESERVED_SLUGS` — leaving the set empty for the first time since Phase 1. Deliberately
NOT added to the set: `"growth-engine-calculator"` itself — that page was never part of the
`original_content` system and never will be, so there's no slug an admin could collide with
through the form.

**Phase 4c follow-up (2026-08) — two post-merge rendering bugs, both invisible text, found
live on mobile Safari and confirmed present at every viewport width.** (1) The tier table's
`<thead><tr style="background:var(--navy)">` inline style was silently covered by
`_OC_ARTICLE_CSS`'s generic `.oc-body th{background:var(--accent-light)}` rule — not a
specificity contest, a CSS table background PAINTING LAYER fact (CSS 2.1 §17.5.1): a cell's
own background always paints above its row's, independent of specificity. Fixed the same way
`.ns-table th` (Phase 4a) already solved this for its own table:
`.oc-body .ger-table th{background:var(--navy);color:#fff;}`, giving the cell its own explicit
color rather than relying on the row showing through underneath it. (2) The "Download the full
guide" `<a class="btn">` CTA lost its white text to `.oc-body a{color:var(--navy)}`, which
genuinely does have higher specificity than `.btn`'s own `color:#fff` (one class + one tag
beats one class alone) — a real specificity loss this time, not a layering one. Fixed
generally in `_OC_ARTICLE_CSS` itself (`.oc-body .btn{color:#fff;}`, two classes beats one
class + one tag) rather than narrowly in `_OC_GER_CSS`, since this was the first `body_md`
content anywhere to use the sitewide `.btn` button and any future piece using it would hit the
same bug. The pre-merge screenshot verification pass had a real gap, not just bad luck: it
checked the `<thead><tr>`'s own `getComputedStyle().backgroundColor` (genuinely navy, since
the inline style was never removed) but never checked what actually paints on top of it — same
"verify what's rendered, not a property read in isolation" lesson CLAUDE.md's testing-standard
section already documents from an earlier incident. Verified this time with real mobile-Safari-
viewport (390×844) element-level screenshots of exactly the two flagged elements (not full-page
captures), `getComputedStyle()` diffs before/after, and a new regression test in
`tests/test_original_content_article.py` asserting both fixed CSS rules render in the response.

**Phase 4c second follow-up (2026-08) — an unintentional white gap above/below the tier table,
same root-cause shape as the first follow-up.** `.ger-table-wrap`'s own inline style
(`background:#fff;border:1px solid var(--line);border-radius:12px`, copied verbatim from the
retired bespoke page) bounds a white bordered card with zero padding, meant to fit the table
flush against its edges. But `_OC_ARTICLE_CSS`'s generic `.oc-body table{margin:1.5em 0}` rule
(written for markdown-generated tables, which have no wrapper of their own to own that spacing)
still applied to `.ger-table`, since its own CSS never reset `margin`. Measured live before
fixing: the table sat 21px inset from the wrapper's border on all four sides
(`getComputedStyle(table).margin` → `21px 0px`; a `bounding_box()` diff between the wrapper and
the table confirmed a matching 21px gap top and bottom) — an unintentional blank box, not a
design choice. `.ns-table`'s own wrapper never showed this because it has no background/border
of its own to reveal the identical inherited margin against — the same 21px gap is present
there too, just invisible against the page background. Fixed with `.oc-body .ger-table{margin:0;}`,
so the wrapper (which already carries the correct outer spacing via its own inline
`margin:0 0 32px`) is the single source of the box's outer edge — the same "give the raw-HTML
element its own explicit reset instead of letting a generic markdown-table rule reach it"
pattern the header/CTA fixes both used. Verified with a live `bounding_box()` measurement
before/after (gap 21px → ~1px, the residual being border-width rounding) and a real desktop
screenshot showing the header sitting flush against the wrapper's rounded top corners, plus a
new regression test (`test_growth_engine_ratio_table_wrap_has_no_visible_gap`) asserting the
fixed CSS rule renders in the response.

`Library.get_thought_leadership_representative(type)` (Homepage Restructure phase;
supersedes the Phase 3 addendum's `list_thought_leadership_for_home` pin-then-
recency-backfill panel, which the redesign replaced outright) selects one
representative entry per type for the homepage's "Recent highlights" grid: the most
recently updated `featured_home=1` entry of that type, if any (`updated_at
DESC` — the tie-break when more than one entry of a type is checked); otherwise
the most recent entry by the existing `_TL_ORDER_SQL` ordering, so a type with
no admin selection yet still shows something instead of an empty column.
Returns `None` only when the type has zero entries at all, in which case the
homepage renders no column for it (same convention as `/thought-leadership`'s
own `column()` collapsing when empty).

One Speaking & Events entry (Abacum AI Summit) has photos — a field this
table doesn't carry, since it's the only entry that ever used it. It stays
hardcoded in `webapp/app.py`'s `_TL_PHOTO_ENTRY` instead of migrating, merged
into the `speaking` column's items at render time so it doesn't disappear
from the public page. Not editable via the admin CRUD.

### Site operations

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `settings` | Generic key/value store (global Ask cap default, `matchmaker_default_cap_usd`, editable email copy, tag-style guide, `voice_core`/`voice_fpa_buddy`/`voice_matchmaker` voice guide — plus `voice_prompts_seeded`, the 2026-08 one-time seeding gate for those three, see CLAUDE.md's "voice_core/voice_fpa_buddy/voice_matchmaker visibility" bullet — `backup_drive_folder_id` — the self-created Drive backup folder's id, Phase O, `exa_enabled` — Phase 7 web-search kill switch, `enrich_model` — the live AI model selection for `linklib.enrich`'s generation calls, 2026-08, see CLAUDE.md's "AI model selection", …). | `key`/`value` |
| `contacts` | Contact-form submissions. | `deleted_at` (`''` = live — soft delete for spam, never hard delete) |
| `email_failures` | Durable record of failed outbound-email attempts, so "best-effort" email never means "silent". | `context` (which send path), `resolved_at` |
| `archive_audit_log` | Who did what to the archive: one row per admin add/edit/delete. | `admin_id` (nullable — the break-glass login has no `users` row), `item_id` (an `articles.id`; `NULL` = bulk operation with a summary in `detail`) |
| `contact_audit_log` | Same shape for contact deletions — kept separate so `item_id` is never ambiguous about which table it references. | as above, `item_id` → `contacts.id` |
| `backup_log` | Off-site Drive backup audit trail (Phase O) — one row per `linklib.backup.backup_now()` attempt, success or failure, written from inside `backup.py` itself so it's one code path regardless of which trigger fired (the daily GitHub Action, a manual `/admin/backup-now` click, or one of the ~18 debounced `maybe_backup()` call sites in `webapp/app.py`). No `admin_id`/FK — a scheduled Action run isn't attributable to a person the way an admin edit is. Read by the status banner + history table on `/admin/library/backup`. A backup skipped because the pre-backup integrity check failed (durability audit item 2, see `integrity_check_log` below) also logs a `'failure'` row here, `error` prefixed `"Backup skipped — integrity check failed: ..."`, so the existing status banner surfaces it without a second banner-reading code path. | `status` (`'success'`\|`'failure'`), `drive_file_id` (success only — powers the "Open in Drive" link), `row_count` (`SELECT COUNT(*) FROM articles` on the snapshot at backup time — the sanity check the restore path already runs on upload), `error` (failure only) |
| `integrity_check_log` | Durability audit item 2 (elevated, 2026-08) — one row per `linklib.backup.check_integrity()` run, shape mirrors `backup_log` exactly. Nothing previously ran `PRAGMA integrity_check` against the live DB; corruption would only ever have surfaced at restore time, by which point it would already be baked into every retained snapshot. `check_integrity()` runs `PRAGMA integrity_check` plus the FTS5 self-check (`INSERT INTO articles_fts(articles_fts) VALUES('integrity-check')` — the exact command RUNBOOK.md §4's restore rehearsal already runs by hand) against the live DB, on the same cadence as the backup itself, immediately before every snapshot. **A failure blocks that night's backup upload** (see `backup_now()`'s docstring for the full "block vs. upload-and-flag" reasoning) rather than uploading a possibly-corrupt snapshot anyway. Read by the "Pre-backup integrity check" status banner on `/admin/library/backup`, which sits above the existing backup-status banner — deliberately a separate banner, since "the backup succeeded" and "the DB is structurally sound" are two different facts a single banner would conflate. | `status` (`'ok'`\|`'failure'`), `detail` (the failing `PRAGMA integrity_check` row text, or the FTS5 self-check's exception text; `'ok'` on success) |
| `job_run_log` | Durability audit item 3 (2026-08) — durable start/finish record for each of the three `_JOB_STATE`-backed background jobs (re-enrich, Historical sweep, Reader content backfill), shape mirrors `backup_log`/`integrity_check_log`. `_JOB_STATE` (`webapp/app.py`, an in-process dict) is unchanged and still owns LIVE in-request progress — this table is written only twice per run (`Library.start_job_run` at the top of each job function, `Library.finish_job_run` at every exit path, including a deliberate stop) and exists purely so a Railway redeploy or crash doesn't erase whether a job last succeeded, failed, or ever ran. Read by `_job_run_banner()`, a shared "last run: outcome, N ago" banner rendered on each of the three jobs' own admin-page section (`/admin/library/enrich`, the Historical sweep panel on `/admin/library/queue`, `/admin/library/backfill-content`) — same green/amber/coral posture as the backup/integrity banners. A row stuck at `status='running'` with an empty `finished_at` is exactly what a crash mid-run looks like, and is called out as such rather than shown as live progress — **but only when nothing live actually corresponds to it** (2026-08 wrap-up sprint item 3 fix): `_job_run_banner()` originally rendered the crash interpretation for ANY open row, so it showed "never finished — likely interrupted by a deploy or crash" directly above the same page's own genuinely-in-progress status panel whenever a job happened to still be running, confirmed in production twice. Fixed by checking `_job_get(job_name)["running"]` before assuming an open row means a crash — when the job is actually live, the open row IS that live run, and the banner renders a plain in-progress line instead. | `job_name` (`'enrich'`\|`'backfill'`\|`'content_backfill'`), `status` (`'running'`\|`'success'`\|`'failure'`\|`'stopped'`), `summary` (short human-readable counts, e.g. `'42/50 succeeded'`), `error` (failure only), `started_at`, `finished_at` (`''` while running) |

### Feed subscriptions

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `feed_sections` | The subscription list's top-level groups, one per OPML folder ("News", "Blogs", "Substacks", …). Rendered as the Reader's Sources tree headings and as the section dropdown on `/admin/library/feeds`. **Pure grouping — sections carry no settings of their own.** | `name` (unique), `display_order` |
| `feeds` | One row per RSS/Atom subscription. | `xml_url` (**the natural key**, unique — the same feed can't be subscribed twice; **stored and regenerated verbatim**, see §4), `html_url` (the publication's own site: what `sources.preferred_domains` turns into FP&A Buddy's web-search allowlist, and what the historical sitemap sweep crawls), `section_id` (FK → `feed_sections`), `name` (the label shown in the Reader), `exclude_from_queue` (`1` = read in the Reader, never proposed into the archive queue — replaces the retired `QUEUE_EXCLUDE_CATEGORIES` name-matched env var; see §4), `has_paywall_cookie` (frozen historical value as of 2026-08 — the admin checkbox that wrote it was replaced with a computed live indicator, `extract.has_configured_cookie`; nothing reads this column going forward, same retirement as `paywall_cookie_note`; see §4), `paywall_cookie_note` (retired free-text predecessor, frozen; see §4), `has_active_subscription` (`1` = Brian currently pays for this source — **informational only, nothing reads it**; see §4) |

These two tables are the source of truth; **`preferred_sites.opml` is a derived
cache**, regenerated by `Library.write_opml()` on every mutation and again on
every boot. All four of the file's consumers (`feed.parse_opml`,
`sources.preferred_domains`, `queue.scan_feed_into_queue`, `authcheck`) read the
file unmodified. See §4 for why the file can't be authoritative on Railway.

### "Sail, Don't Row" (the /play game)

| Table | Purpose | Columns that carry meaning |
|---|---|---|
| `game_rank_settings` | One row per difficulty rank — the tunable knobs the game engine reads, editable from `/admin/game-settings` without a redeploy. Contains deliberately-kept dead columns (`row_speed`, `stamina_*`) from a removed mechanic — migrations here are additive-only. | `rank` (PK), speed/obstacle/shark tuning columns |
| `game_runs` | Public leaderboard, one row per submitted run. Client-reported scores (client-authoritative game, bounds-checked at write time only). | `score`, `course_week`, `difficulty_index`/`difficulty_label` (**frozen at write time** — a later admin retune can't relabel past runs) |

```mermaid
erDiagram
    users ||--o{ ask_questions : "user_id"
    users ||--o{ ask_feedback : "user_id"
    ask_questions ||--o{ ask_feedback : "question_id"
    users ||--o{ matchmaker_questions : "user_id (nullable — public page)"
    users ||--o{ read_later : "user_id"
    users ||--o{ game_runs : "user_id"
    users ||--o{ password_reset_requests : "user_id"
    users ||--o{ archive_audit_log : "admin_id (nullable)"
    users ||--o{ contact_audit_log : "admin_id (nullable)"
    users ||--o{ tool_audit_log : "admin_id (nullable)"
    users ||--o{ community_audit_log : "admin_id (nullable)"
    users ||--o{ narrative_review_log : "admin_id (nullable)"
    articles ||--|| articles_fts : "rowid, via triggers"
    articles ||--o| articles_vec : "rowid, written from Python (#93)"
    articles ||--o| article_embeddings : "article_id"
    articles ||--o{ enrichment_cost : "article_id (nullable)"
    library_queue }o--|| articles : "promoted into (by URL)"
    articles ||--o{ archive_audit_log : "item_id (nullable)"
    contacts ||--o{ contact_audit_log : "item_id (nullable)"
    tools ||--o{ tool_leads : "tool_id"
    tools }o--o{ tools : "tool_competitors, normalized pair"
    tools ||--o{ tool_audit_log : "item_id (nullable — row deleted by the time this is read)"
    tools ||--o{ narrative_review_log : "item_id, entity_type='tool'"
    tool_categories }o--o{ tools : "by name in categories_json"
    tool_categories ||--o{ category_features : "category_id"
    category_features ||--o{ tool_feature_links : "feature_id"
    tools ||--o{ tool_feature_links : "tool_id"
    tool_categories ||--o{ feature_review_queue : "category_id (nullable)"
    tools ||--o{ feature_review_queue : "tool_id (nullable)"
    community_categories }o--o{ communities : "by name in categories_json"
    communities ||--o| community_profiles : "community_id"
    communities }o--o{ communities : "community_competitors, normalized pair"
    communities ||--o{ community_audit_log : "item_id (nullable — row deleted by the time this is read)"
    communities ||--o{ narrative_review_log : "item_id, entity_type='community'"
    game_rank_settings ||--o{ game_runs : "rank"
    feed_sections ||--o{ feeds : "section_id"

    feed_sections {
        int id PK
        text name UK
        int display_order
    }

    feeds {
        int id PK
        int section_id FK
        text xml_url UK "natural key, stored verbatim"
        text html_url "-> Buddy allowlist + sitemap sweep"
        text name "label in the Reader"
        int exclude_from_queue "1 = read-only, never queued"
        int has_paywall_cookie "1 = needs the cookie; descriptive only"
        int has_active_subscription "1 = paid for; informational only"
    }

    articles {
        int id PK
        text url UK "natural key, normalized"
        text summary "member-facing asset"
        text content "internal grounding input only"
        text tags_json
        int enriched
        int in_scope
    }
    ask_questions {
        int id PK
        text conversation_id "groups turns"
        int turn_index
        int user_id
        real cost_usd "turn TOTAL: answer + rewrite + embed"
        real rewrite_cost_usd "rewrite's share of cost_usd"
        real embed_cost_usd "query-embed's share of cost_usd (#93)"
        int hidden_public
        int anonymized
        text citations_json "cited-source snapshot per turn"
    }
    article_embeddings {
        int article_id PK
        text content_hash "detects staleness after an edit"
        text model
        real cost_usd "overhead — never in ask_questions"
    }
    enrichment_cost {
        int id PK
        int article_id "nullable — pre-save queue enrichment"
        text model
        int output_tokens "enrichment generates text; embeddings don't"
        real cost_usd "overhead — never in ask_questions"
    }
    ask_feedback {
        int id PK
        int question_id "rated turn, UNIQUE with user_id"
        int user_id
        text rating "helpful | inaccurate | not_helpful"
        text comment "optional free text"
    }
    matchmaker_questions {
        int id PK
        text kind "community | software"
        text conversation_id "groups turns"
        int turn_index
        int user_id "NULL for anonymous — public page, no login"
        text session_id "cfo_visitor cookie; the anonymous rate-limit key"
        real cost_usd "turn TOTAL, no rewrite/embed split (no retrieval)"
    }
    users {
        int id PK
        text username UK
        text role "user | admin"
        real ask_cap_usd "NULL = global default"
        real matchmaker_cap_usd "NULL = global default; tracks separately from ask_cap_usd"
    }
    library_queue {
        int id PK
        text url UK
        text origin "feed | backfill | submission"
        text status "pending | dismissed"
    }
    tools {
        int id PK
        text slug UK
        int approved
        int warm_intro_enabled
        text suite_note "free text: vendor suite membership beyond the CFO's office"
    }
    category_features {
        int id PK
        int category_id FK "tool_categories.id"
        text name "unique per category, not globally"
        text pointer_note "nullable: in-Toolbox overlap comparison"
        int sort_order
        text retired_at "'' = live; retired, never deleted"
    }
    tool_feature_links {
        int tool_id FK
        int feature_id FK "category_features.id"
        text availability "native | add_on"
        int ai_enabled
        text verified_as_of "required, decays fast"
        text note "nullable"
    }
    feature_review_queue {
        int id PK
        text source "admin | scan | public"
        text status "pending | approved | edited | denied"
        int category_id FK "nullable"
        int tool_id FK "nullable"
        text payload "JSON: proposed feature/link(s)"
        text submitter_email "nullable — public channel, later phase"
    }
    communities {
        int id PK
        text slug UK
        text reach "Regional | National | Global"
        text local_markets "free text: cities/areas with a chapter, hub, or local focus"
        int featured "pin-to-top + coral badge, mirrors tools.promoted"
        int advisor "star marker + filter chip, mirrors tools.advisor"
        text cost_band "Free | Undisclosed dues | <$1k/yr | <$2,500/yr | $2,500+/yr"
        text sponsorship_type "Independent | Vendor-sponsored | Investor-sponsored"
        int approved
    }
    community_profiles {
        int community_id PK "1:1 with communities, no SQL FK"
        text sponsor_relationship_note "qualitative — distinct from sponsor_name/type"
        text application_friction "real barrier to entry, not just the access label"
        int founded_year "nullable"
        text notable_members "nullable — only if verifiably public"
        text public_criticism "nullable"
        int low_confidence "drafted without a successful page fetch"
        text cpe_eligible "short factual field, not voice-rewritten"
        int needs_review "imported/edited, not yet personally reviewed"
    }
    game_runs {
        int id PK
        text rank
        int score "client-reported"
        int difficulty_index "frozen at write"
    }
```

(Diagram shows key columns and conventional relationships only; `settings`,
`contacts`, `email_failures`, `benchmarks`, `thought_leadership`, `dedupe_decisions`,
`read_later`, `tool_categories`, `community_categories`, `articles_vec`, and the audit
tables carry no columns beyond what the tables above describe.)

## 3. Key request flows

### FP&A Buddy — `POST /ask`

The retrieval-augmented Q&A flow. The UI exposes only a Quick/Standard/Deep
effort tier; each tier maps internally to a model, retrieval counts, token
budget, and per-source/global grounding-character caps (`EFFORT_SETTINGS` in
`linklib/agent.py`). The server is the source of truth for conversation
history: the client sends only `conversation_id` + the new question, and the
server rebuilds the transcript from the conversation's recorded
`ask_questions` rows — client-fabricated history is impossible, and both the
follow-up cap and the money guards are fully server-side.

```mermaid
sequenceDiagram
    participant B as Browser (/tools/fpa-buddy page)
    participant W as webapp/app.py
    participant DB as SQLite (Library)
    participant AG as linklib/agent.py
    participant H as Anthropic API (Haiku)
    participant O as OpenAI API (embeddings)
    participant X as Exa API (search)
    participant C as Anthropic API (tier model)

    B->>W: POST /ask {question, conversation_id?, effort}
    W->>W: _require_member (cookie or save token)
    opt follow-up turn (conversation_id present)
        W->>DB: load the conversation's ask_questions rows (turn_index order)
        W->>W: ownership: 404 unknown id, 403 someone else's conversation
        W->>W: follow-up cap: count recorded rows (max 7 turns)
        alt turn cap reached
            W-->>B: {capped: true}
        end
        W->>W: rebuild history[] from the rows (question/answer pairs)
    end
    W->>DB: _current_user_id -> effective cap vs SUM(cost_usd) this month
    alt monthly dollar cap reached
        W-->>B: {capped: true, budget message}
    end
    W->>AG: answer_question(question, rebuilt history, effort)
    opt follow-up turn only (history non-empty)
        AG->>H: rewrite follow-up into a standalone search question
        H-->>AG: rewritten query (+ real token usage)
        Note over AG: best-effort - on any failure,<br/>retrieval falls back to the raw question
    end
    AG->>DB: FTS5 search (bm25-ranked) on the retrieval question, 2x max_library
    AG->>O: embed the retrieval question (text-embedding-3-small)
    O-->>AG: query vector (+ real token usage)
    Note over AG: best-effort - vector search unavailable or the<br/>embed call fails -> falls back to FTS5-only silently
    AG->>DB: vec0 KNN search on the query vector, 2x max_library
    AG->>AG: reciprocal rank fusion - merge FTS5 + vector hits,<br/>dedupe by article id, take top max_library
    AG->>AG: optional feed matching (keyword overlap, 30-min cached feed)
    AG->>DB: _web_provider - exa_enabled setting AND EXA_API_KEY set?
    alt Exa is the provider (preferred)
        AG->>X: retrieve_exa: /search, includeDomains from OPML, max_web results
        X-->>AG: results (+ real result count)
        Note over AG: best-effort - the call fails<br/>-> falls back to library/feed-only silently, does NOT re-arm the native tool this turn
        AG->>C: messages.create: library + feed + Exa sources,<br/>all as document blocks with citations enabled (no web tool armed)
    else native tool is the provider (Exa off, or no key)
        AG->>C: messages.create: library + feed as document blocks,<br/>web_search_20250305 tool armed (allowed_domains from OPML)
        Note over C: the model decides whether/how many times<br/>to call the tool (max_uses = max_web), same as pre-Exa
    end
    C-->>AG: text blocks with citation spans<br/>(+ automatic web citations when the native tool fired) + usage
    AG->>AG: reassemble answer - append [n] after each cited span,<br/>one deduped first-use-ordered list across library/feed/web sources;<br/>each web citation tagged provider "exa" or "native"
    AG->>AG: compute_cost from real token usage (pricing.py)
    AG-->>W: Answer {text, citations, cost_usd = answer + rewrite + query embed + Exa (if it ran)}
    W->>DB: record_ask_question - one row per turn<br/>(conversation_id, turn_index, tokens, cost breakdown,<br/>citations_json snapshot of the cited sources)
    W-->>B: {answer, citations, sources, followups_left,<br/>conversation_id, turn_id, usage: {spent, cap}}
    opt member rates the answer
        B->>W: POST /ask/feedback {question_id: turn_id, rating, comment?}
        W->>DB: record_ask_feedback - upsert on (question_id, user_id)
    end
```

Details worth knowing:

- **The system prompt's voice block is DB-backed, two fields, live-editable
  from `/admin/voice`.** `_build_system` (`linklib/agent.py`) reads the
  `voice_core` and `voice_fpa_buddy` settings and concatenates them.
  **2026-08 visibility follow-up**: this used to fall back to each
  setting's code-constant default (`VOICE_CORE_DEFAULT`,
  `VOICE_FPA_BUDDY_DEFAULT`) when empty — see the dedicated bullet below
  for why that silent fallback was retired; both settings are now seeded
  from their defaults once per database instead, and an empty one refuses
  (`VoicePromptMissing`) rather than substituting anything.
  `voice_core` is written persona-neutrally (mechanics + tone only,
  no assistant framing) so it also serves standalone as `/admin/voice`'s
  "General / site copy" reviewer rubric; `voice_fpa_buddy` layers the
  analyst-specific register (third-person, cite-or-name-the-gap, no
  first-person experience claims) on top for both generation and its own
  "FP&A Buddy answer" rubric. No caching — one indexed SELECT on an
  already-open connection is immaterial next to the Claude API round-trip.
- **Voice enforcement + structure, CFO Toolbox Software fields (2026-08) —
  the same `voice_core` DB-backed setting PR #110 wired into FP&A Buddy
  (and, since, `linklib.matchmaker` and `voice_rewrite_community_fields`)
  now also reaches `linklib/enrich.py`'s three Software-directory generation
  functions: `generate_tool_description` (Description + Short summary),
  `generate_tool_agent_taxonomy`, and `generate_tool_differentiation`.**
  Before this, all three ran on prompt text with no voice reference at all —
  the same class of gap PR #110 fixed elsewhere, confirmed by direct
  inspection rather than assumed. Since `enrich.py` has no `Library` handle
  of its own, each function takes an optional `voice_core: str = ""` param;
  the three call sites in `webapp/app.py` (the two AJAX generate routes plus
  `_run_tool_research`) resolve the setting and pass it in — same
  resolve-at-the-caller pattern `scripts/archive/import_community_profiles.py`
  already used for `voice_rewrite_community_fields`. **2026-08 visibility
  follow-up (see the dedicated bullet below):** a bare `voice_core=""` used
  to fall back to `VOICE_CORE_DEFAULT` inside `enrich._resolve_voice_core`
  — that fallback is retired, so a caller now has to actually resolve a
  real value (via `linklib.voice_settings.require_voice_setting`) or the
  function returns `None` rather than silently drafting on no voice
  guidance; every pre-existing test call site was updated to pass a real
  `voice_core` value accordingly.
  Description and Agent taxonomy also gained an explicit structure
  instruction (`enrich._STRUCTURE_GUIDANCE`) — natural paragraph breaks
  instead of one dense block, and a `"- "`-prefixed bulleted list only where
  the content is genuinely list-like (e.g. several named agents) — since
  `voice_core` itself covers tone/mechanics (including the em dash rule) but
  says nothing about paragraph/list structure, and rewriting `voice_core`'s
  own copy was out of scope (Brian's to own). `"summary"` and Competitive
  differentiation are explicitly exempted from the structure guidance: both
  are deliberately short (a card/subhead teaser; a 1-2 sentence callout) and
  stay a single continuous paragraph. Competitive differentiation is the
  field a spaced em dash was actually observed in, so it gets the voice
  reference but no structure instruction. The public profile page's
  Description and Agent taxonomy `<p>` tags gained `white-space:pre-wrap` so
  a structured draft's paragraph breaks and bullet lines actually render —
  previously a bare `<p>` collapsed any embedded newline; this has no visible
  effect on an existing single-block record. Scoped to future generations
  only — no existing stored `description`/`agent_taxonomy_note`/
  `competitive_differentiation`/`summary` was regenerated, rewritten, or
  reformatted by this change; that's a separate, human-supervised bulk
  regeneration Brian plans to run later, test-batch-first, per the pattern
  `scripts/enrich_agent_taxonomy.py`'s own cost-estimate-first convention
  already established.
- **`voice_core`/`voice_fpa_buddy`/`voice_matchmaker` visibility (2026-08) —
  the silent code-constant fallback described in the two bullets above is
  retired for all three settings, not just `voice_core`.** Prompted by the
  spaced-em-dash incident (see CLAUDE.md's matching bullet): the old
  `lib.get_setting(key) or CODE_DEFAULT` pattern made it impossible to tell
  from outside the code whether a given answer was governed by an
  admin-edited `/admin/voice` value or an invisible hardcoded constant.
  Two-part fix, both new: **seeding** — `Library.seed_voice_prompts()`
  populates any currently-empty one of the three settings from its code
  default, gated on a `voice_prompts_seeded` flag (never an emptiness
  check, so a deliberate `/admin/voice` clear-out survives every future
  deploy — same precedent as `seed_paywall_cookie_flags`), wired into a new
  `_seed_voice_prompts` startup hook. **Resolution** — new
  `linklib/voice_settings.py` (`require_voice_setting(lib, key)`, raising
  `VoicePromptMissing` on empty) is the one place every caller resolves a
  voice setting now; it prescribes the raise, not the response, so each
  caller degrades however its own module already handles an unavailable
  precondition — `enrich.py`'s four `generate_*` functions return `None`
  (checked before any page fetch), `agent.ask()`/`matchmaker._answer()`
  return their own `Answer`/`MatchAnswer` with explanatory text (same shape
  as their existing missing-SDK/missing-key branches), a `webapp/app.py`
  route returns a 503. Every real call site — found via a full grep
  inventory, not assumed — was switched: the 3 Toolbox generate AJAX
  routes, `_run_tool_research`, `/admin/voice/review`'s tester,
  `agent.py`/`matchmaker.py`'s own `_build_system`s, and the two live batch
  scripts (`scripts/enrich_agent_taxonomy.py`,
  `scripts/enrich_community_profiles.py`) plus
  `scripts/regen_ai_drafted_fields.py`. `/admin/voice` gained a
  blocked-generation banner (names which setting(s) are empty) and a
  three-way per-field badge (Customized / Default (as seeded) / Not
  configured) computed by comparing the live value against the code
  default rather than just checking truthiness, since a just-seeded field
  is non-empty but not "customized." "Reset to default" now writes the
  real default text (a `{"reset": true}` payload) instead of sending a
  blank value, since blank now means "deliberately cleared, blocking
  generation" rather than "silently uses the default." **Real correction
  found mid-build**: `generate_community_profile` never actually had a
  `voice_core` parameter or any voice-guide content in its prompt at all —
  the citation-tag investigation's original root-cause claim that it did
  was wrong (it matched a different template's `{voice_core}` placeholder,
  belonging to `_VOICE_REWRITE_PROMPT`, used only by an archived script).
  Fixed in this same PR: a real `voice_core` param, a "Voice guide" section
  in its prompt, and the same empty-guard the other three `generate_*`
  functions have, plus fixing its one live caller and
  `scripts/enrich_community_profiles.py` to actually resolve and pass it.
  `generate_community_listing` (the separate "Auto-fill from URL" basic-
  listing generator) still has no voice_core support — flagged as a
  distinct, deliberately out-of-scope enhancement, not a regression.
  **Follow-up correction (post-merge)**: the line above listing
  `scripts/regen_ai_drafted_fields.py` among the switched call sites was
  true only for its `main()`-level resolve (`require_voice_setting` at
  startup) — its separate community code path
  (`_run_communities`/`_regen_community_profile`, and the `--sample`
  branch's community loop) never actually threaded that resolved
  `voice_core` into the `generate_community_profile` call, so it fell
  through to the function's own empty default and aborted every call. A
  real post-merge `--apply` run against 40 communities caught this with
  zero writes (the empty-guard's designed fail-safe, not a bad write) —
  see CLAUDE.md's dedicated bullet for the full root-cause elimination and
  the fix.
- **Two to four API calls can happen per turn.** On follow-ups, a cheap Haiku
  call first rewrites e.g. *"what about at Series A?"* into a standalone
  search question so retrieval sees the conversation's subject. It's
  retrieval-only (the answering prompt always gets the verbatim question plus
  raw history), strictly best-effort (10s timeout, malformed output rejected,
  silent fallback), and its spend is still recorded — folded into the same
  row's `cost_usd` with `rewrite_*` columns breaking out its share. A
  separate OpenAI call embeds that same retrieval question for the vector
  half of hybrid search, and (when `use_web` and Exa is the provider — see
  below) an Exa `/search` call retrieves web results — same best-effort
  contract, same fold-into-`cost_usd` pattern (`embed_*` and `exa_*` columns
  respectively). When the native tool is the provider instead, its own
  per-search fee is billed by Anthropic and isn't captured in `cost_usd` at
  all — a pre-existing gap flagged back in the Phase 0 investigation, not
  introduced or worsened by Phase 7's restoration of the tool.
- **Exactly one mechanism handles the web tier per turn — never both.**
  `linklib.agent._web_provider(lib)` decides once, before retrieval starts:
  Exa when the `exa_enabled` setting is on (default) AND `EXA_API_KEY` is
  set; otherwise the native `web_search_20250305` tool (Phase 7 restored it
  from before Phase 2's removal — same `allowed_domains`/`max_uses` shape).
  This is a one-time decision, not a reactive retry: if Exa is chosen but
  its call fails mid-turn, that turn just gets zero web results (the
  existing best-effort contract) rather than falling back to the native
  tool for the same turn — avoiding any scenario where both could fire.
  Toggle and connection test live at `/admin/exa-settings`.
- **Library retrieval is hybrid: FTS5 keyword search + vector semantic
  search, merged by reciprocal rank fusion (`agent._rrf_merge`, k=60).** Each
  path fetches 2x the tier's `max_library` so the merge has real rank signal
  to work with, not two already-truncated top-N lists. Chosen over blending
  bm25 scores with cosine distances: the two live on incomparable scales with
  no corpus-scale signal (a ~1,500-article library) to calibrate a blend
  weight against, whereas RRF only needs rank position. Every failure mode —
  `sqlite-vec` unavailable, no `OPENAI_API_KEY`, the embed call erroring —
  degrades silently to FTS5-only, never blocking an answer.
- **`Library.search()`'s FTS5 half tries the query as-given before ever
  quoting it — a query-safety fix (Reader Build arc's full QA pass) whose
  ordering specifically protects this retrieval path.** `agent._safe_fts_query()`
  pre-tokenizes a question into deliberately valid FTS5 syntax
  (`"self" OR "serve" OR "churn"`) before handing it to `Library.search()`.
  The fix that stops a raw, unescaped user query from crashing FTS5
  (a hyphen, an apostrophe, or an FTS5 operator keyword like AND/OR/NOT all
  used to throw `sqlite3.OperationalError`, uncaught) tries the query
  exactly as received first, and only falls back to wrapping the whole
  thing as one quoted phrase if that raises — never unconditionally, which
  would otherwise turn `_safe_fts_query()`'s already-valid OR-query into a
  single literal-string search matching nothing, silently taking this
  retrieval path dark. See CLAUDE.md's "Reader Build arc — full QA pass"
  bullet for the full story, including how that regression was caught
  before shipping.
- **Citations are API-verified, not prompted.** When Exa is the provider,
  library, feed, and web sources all ride as Citations-API `document` blocks
  — one uniform citation shape. When the native tool is the provider instead
  (Phase 7 fallback), its automatic URL-based citations are reassembled
  alongside the document-block citations from library/feed — both shapes
  are unified into one continuous, deduplicated, first-use-ordered `[n]`
  list either way, so the answer text and source list look identical
  regardless of which mechanism handled the web tier. Every web-type
  citation additionally carries a `provider` field ("exa" | "native") not
  present on library/feed citations — a separate marker from citation
  `type` (which stays "web" for both), used only to gate the "Powered by
  Exa" caption (Phase 3) on the real mechanism. Any surprise in citation
  metadata degrades to plain text — citation handling can never fail an
  answer.
- **Citation-assembly logic lives in a shared module, not agent.py-private
  (Phase 1a of the enrich.py grounding fix, 2026-08).** `linklib/citations.py`
  (`make_document_block`, `extract_citations`) is a pure extraction of what
  used to be `_build_source_documents`'s per-source document-block builder
  and `_assemble_cited_answer` — this module's own `_assemble_cited_answer`
  is now a one-line wrapper around `extract_citations`. No behavior change:
  proven via `tests/test_citations_refactor_parity.py`, which replays a
  battery of scenarios (`tests/citations_fixtures/scenarios.py`) against
  the post-refactor functions and diffs the result against
  `tests/citations_fixtures/golden_output.json` — output captured from the
  real, unmodified pre-refactor code by
  `scripts/archive/capture_citations_golden_fixtures.py` (run once, before
  any refactor code existed, then archived) — rather than merely "the
  existing tests still pass." `extract_citations`'s `inject_markers` flag
  (True here, the only mode this module uses) exists for a future caller
  that can't accept inline `[n]` markers in its output: `enrich.py`'s
  Agent taxonomy grounding fix (Phase 1b, not yet built) drafts strict
  JSON, where splicing a marker into a field value would corrupt the
  parse — see that module's docstring.
- **Agent taxonomy grounding fix, Phase 1b (2026-08) — the first real
  `enrich.py` caller of `linklib/citations.py`.**
  `generate_tool_agent_taxonomy` no longer flattens
  `_fetch_taxonomy_grounding`'s fetched pages into one plain-text prompt
  string; each page now rides as its own real Citations-API `document`
  block (`linklib.citations.make_document_block`, `sent_docs[i]["type"] =
  "tool_page"`). The model's `document_index` citations are mechanically
  verified by the API, not self-reported the way `confident` still is —
  the two are independent facts, same relationship
  `agent_taxonomy_ai_confident` already has to
  `agent_taxonomy_needs_verification`. **Settles Phase 1a's open
  `inject_markers` question**: this response is strict JSON
  (`{"summary": ..., "confident": ...}`), and the Citations API is
  explicitly documented as incompatible with structured output (`output_
  config.format`, 400 error) — confirmed against current Anthropic docs
  before building, not assumed — and mechanically has no attachment point
  on a `tool_use.input` field either (citations only ever attach to `text`
  content blocks). So `extract_citations(..., inject_markers=False)` is
  used here: the concatenated raw text comes back completely untouched
  (safe to `json.loads()`), while the citation list is still collected
  separately — deterministic parsing, not a second model call (a second
  pass would just reintroduce self-reported citations under a different
  name).
  **Storage: one shared `entity_citations` table, not a per-field column**
  — `entity_type`/`entity_id`/`field_name` composite key (mirrors
  `field_reviews`' shape), `citations_json` (the FULL deduped-by-url list,
  UNCAPPED — a display cap is applied only at render time, never in
  storage), `model` + `generated_at` as the generation run reference (no
  separate run/log table — deliberate call, since nothing else in this
  codebase has a run-id concept to hang one off of). Chosen over a
  `tools.agent_taxonomy_citations` column specifically so Phase 2
  (Description) and Phase 3 (Community profile) don't have to migrate off
  a per-field column later — every future grounded field reuses the same
  table with a different `field_name`. `Library.set_entity_citations`
  writes directly (not COALESCE'd, unlike `agent_taxonomy_ai_confident`) —
  a fresh draft's citations should always replace a stale prior draft's,
  never blend with it. `Library.update_tool_agent_taxonomy` (the human
  hand-edit path) calls `clear_entity_citations` — a hand-typed note has
  no citation trace to keep, and leaving a prior AI draft's sources
  attached to text a person just overwrote would misattribute the human's
  own words as machine-grounded.
  **Rendering, and the standing review-gate rule**: citations never
  bypass the existing review gate — they only ever render alongside the
  note in whichever visibility branch the note itself is already in
  (verified → public; unverified/low-confidence → admin-only, same
  publish gate the Abacum-fix bullet above describes). Public profile
  page (`/tools/software/{slug}`'s Agent taxonomy card): a "Sources" list
  capped at 5 (first-use order, already deduped by url — decision:
  truncate silently, no "+N more" indicator). Admin edit page
  (`/tools/software/{slug}/edit`): the FULL uncapped list, rendered inside
  the same `#gen-host-tool-taxonomy` block as the "Mark verified"
  button/badge — an explicit requirement, confirmed before building rather
  than assumed, since a reviewer deciding whether to publish needs to see
  every source, not just the 5 a visitor would eventually see.
  `webapp/app.py`'s `_citations_list_html(citations, cap=None,
  empty_note="")` is the one shared renderer for both call sites (and every
  future grounded field) — `cap=5` on the public page, no cap on the admin
  page, matching FP&A Buddy's own cited-answer source-list visual pattern
  (`.ask-src-list`) adapted with inline styles since that CSS class is
  scoped to the Ask page's own `<style>` block, not sitewide.
  Description (Phase 2) and Community profile (Phase 3, one shared
  citation set per profile draft rather than per-field) are next;
  Competitive differentiation stays deferred (Phase 4) pending a decision
  on whether it gains real fetched competitor content to ground on, since
  today it has none.
- **Description grounding fix, Phase 2 (2026-08) — the second `enrich.py`
  caller of `linklib/citations.py`, and the first where the AI draft is
  generated statelessly, before persistence even has a `tool_id` to key
  off.** `generate_tool_description` grounds on a single fetched page (the
  tool's own URL — unlike Agent taxonomy's multi-page nav crawl), sent as
  one `document` content block when the fetch succeeds; `citations` is
  empty when it doesn't (`low_confidence=True`), same as Agent taxonomy.
  Same `inject_markers=False` reasoning as Phase 1b (`description` is
  strict JSON).
  **The real structural difference from Phase 1b: Description's Generate
  button (`POST /admin/tools/software/generate-description`) is stateless
  client-side AJAX** — `{name, url}` only, no `tool_id`, callable from a
  brand-new "Add software" form that has no tool row yet at all — unlike
  Agent taxonomy's server-side "Refresh AI research" route, which persists
  `set_entity_citations` in the same call that drafts the note. There is
  no draft-time persistence point to write to. So the citations the AJAX
  response returns travel through the browser instead: a new
  `ai-drafted-citations` hidden input (JSON-encoded, mirroring the
  existing `ai-drafted-fields`/`ai-drafted-confidence` convention) plus a
  parallel `ai-drafted-citations-model` input, both populated by
  `markAiCitations()` alongside the existing `markAiDrafted`/
  `markAiConfidence` calls in `generateDescription()`. Persistence happens
  at the *submit* route — the same place `description_needs_verification`/
  `description_ai_confident` are already computed from `ai_drafted_fields`
  (Phase G PR 2) — never trusting the hidden field's contents as-is:
  `webapp.app._validate_citations_payload` re-validates the whole payload
  server-side (must be a JSON list of objects; `url` must be http(s),
  rejecting `javascript:`/`data:`/etc.; `title` length-capped; a malformed
  entry is dropped rather than failing the save; `n` is renumbered
  sequentially over what survives so a dropped entry never leaves a gap in
  the rendered Sources list). Citations persist only when `"description"`
  is in this submit's `ai_drafted_fields` — any other save (a hand-edit,
  or a resave that never touched Generate) calls `clear_entity_citations`,
  same "editing/saving is itself a confirmation" convention
  `update_tool_agent_taxonomy` already applies to its own citations.
  **A hand-edit to the description textarea after Generate is now also
  detected client-side**, not just at save time: a new one-time `input`
  listener calls `unmarkAiDrafted('description')` (removing it from both
  `ai-drafted-fields` and `ai-drafted-confidence`) and `clearAiCitations()`
  the moment the admin types over a fresh draft, so a save right after
  doesn't submit citations grounding text that no longer exists — the
  listener is re-attached on every successful Generate. `unmarkAiDrafted`,
  `markAiCitations`, and `clearAiCitations` all live in the shared
  `_MARK_AI_DRAFTED_JS` block alongside `markAiDrafted`/`markAiConfidence`.
  **Closes a real pre-existing gap on the "Add software" form as its own
  commit, per explicit direction** (not new-in-this-phase scope creep):
  that form never carried `ai-drafted-fields`/`ai-drafted-confidence`
  hidden inputs at all — `_MARK_AI_DRAFTED_JS`'s own prior comment
  documented this as deliberate ("the stateless Add Tool form has no
  entity_id yet to review against") — so a brand-new tool created straight
  from a Generate-description draft never recorded
  `description_needs_verification`/`description_ai_confident`, regardless
  of what the model actually reported. `Library.add_tool` gained both as
  optional parameters (default `0`/`None` — every other existing caller,
  public `/tools/submit` included, is unaffected), and the form now
  carries the same four hidden inputs (including the two new citations
  ones) as the edit form; `admin_tools_new_submit` computes and persists
  all three (needs_verification, confidence, citations) the same way the
  edit-submit route does, evaluated once at creation instead of on every
  resave.
  **Rendering**: same `_citations_list_html` shared renderer as Agent
  taxonomy — public profile page (`/tools/software/{slug}`'s Description
  card) capped at 5; admin edit page's Description block, uncapped,
  alongside the existing `_narrative_verify_widget` verify action/badge.
  **Deliberately does NOT add a publish gate in THIS phase** — unlike
  Agent taxonomy's Abacum-fix gate (`agent_taxonomy_needs_verification`
  hides an unverified/low-confidence note from public visitors entirely),
  Description had never had one at this point and this phase doesn't add
  one: an unverified or low-confidence Description still rendered
  publicly here, with only a Sources list added alongside it. Confirmed
  explicitly out of scope for this phase rather than an oversight —
  flagged as a known follow-up. **Built in the "Description/Community
  profile publish gates" bullet further down this section** — that
  follow-up has since shipped.
  `entity_type='tool'`/`field_name='description'` in `entity_citations`;
  `summary` shares the row (drafted together, one Generate call, same
  "shares its verification status, not tracked separately" convention
  `description_needs_verification` already uses for `summary`).
- **Community profile grounding fix, Phase 3 (2026-08) — the third
  `enrich.py` caller of `linklib/citations.py`, and the one place this
  grounding fix covers a whole multi-field draft with ONE citation set
  rather than one per field (decision 5, Phase 0 investigation).**
  `generate_community_profile` drafts all 23 `COMMUNITY_PROFILE_FIELDS` in
  a single Claude call from a single fetched page — same shape as
  Description's Phase 2 grounding (one page, one `document` content block
  when the fetch succeeds, `citations` empty when it doesn't), not Agent
  taxonomy's multi-page nav crawl. Since every field in the draft comes
  from that same one page in that same one call, there is exactly one
  citation set to attach — stored as a single `entity_citations` row keyed
  `entity_type='community'`, `field_name='community_profile'`
  (`entity_id`=the community's id), covering the whole draft rather than
  23 near-duplicate rows. `inject_markers=False`, same reasoning as
  Description/Agent taxonomy (the response is strict JSON).
  **Same stateless-AJAX structural shape as Description** — the Generate
  route (`POST /admin/tools/communities/generate-profile`) is `{name, url,
  existing}` only, no `community_id`, so citations travel through the
  browser the same way: `markAiCitations()` (already generic from Phase 2)
  populates the same `ai-drafted-citations`/`ai-drafted-citations-model`
  hidden inputs, now added to the Community profile edit form too;
  persistence happens at the submit route
  (`admin_community_profile_submit`), reusing `_validate_citations_payload`
  unmodified. Citations persist only when at least one of the 23
  `_COMMUNITY_PROFILE_FIELD_IDS` is in the submitted `ai_drafted_fields`
  (the same `profile_ai_drafted` boolean the route already computes for
  `needs_review`); any other save clears the row via
  `clear_entity_citations`.
  **The one real behavioral difference from Description, flowing directly
  from the one-row-per-draft decision**: a hand-edit to ANY of the 23
  fields has to invalidate the whole shared set, not just its own field —
  `generateCommunityProfile()`'s client-side one-time `input` listener
  (mirroring `generateDescription()`'s single-field version) is attached
  to every one of the 23 `cp-<field>` inputs/textareas after a successful
  Generate, and each one's listener calls both `unmarkAiDrafted(field)` and
  the same shared `clearAiCitations()`.
  **Rendering: one "Sources" list per page, not per card.** Unlike Agent
  taxonomy/Description (whose citations render inside the one card the
  grounded field lives in), the Community profile's 23 fields are spread
  across 5 card sections plus the "Bottom line" verdict callout — so
  `_citations_list_html` is called exactly once per page on each side, not
  once per card. Public profile page (`/tools/communities/{slug}`): capped
  at 5, rendered once immediately after the "Bottom line" callout, before
  the profile's card sections. Admin edit page
  (`/admin/tools/communities/{id}/profile`, `_community_profile_form_fields`):
  the full uncapped list, rendered once inside the same needs_review/"Mark
  reviewed" block the verify action already lives in — same "a reviewer
  sees every source before signing off" placement rule Description/Agent
  taxonomy use, just anchored to the one shared verify action instead of a
  per-field one.
  **Deliberately does NOT add a publish gate in THIS phase** — same
  explicit, out-of-scope-for-this-phase call Description's Phase 2 made:
  the Community profile page had never gated an unreviewed
  (`needs_review=1`) or low-confidence draft from public visitors at this
  point, unlike Agent taxonomy's Abacum-fix gate, and this phase doesn't
  add one — only a Sources list. Flagged as a known follow-up in the PR,
  same as Description's. **Built in the "Description/Community profile
  publish gates" bullet further down this section** — that follow-up has
  since shipped.
  No schema change — `entity_citations`'s table comment already
  anticipated this exact shape when it was written in Phase 1b.
- **Description/Community profile publish gates (2026-08) — the two
  follow-ups the two bullets above deliberately deferred, now built the
  same way Agent taxonomy's Abacum-fix gate works.** A Phase 0 read-only
  investigation confirmed the gate's shape first: `agent_taxonomy_
  needs_verification` is read directly (never `agent_taxonomy_ai_
  confident`, a separate permanent-display fact that never gates
  anything), branches at each render site into three cases — verified
  (renders plain), unverified+admin (renders with an inline "unverified—
  hidden from visitors" label), unverified+public (falls into whatever
  branch an empty field already uses) — and both new gates extend exactly
  that pattern, no new mechanism.
  **Description** (`tools.description_needs_verification`, `NOT NULL
  DEFAULT 0` — covers both `description` and `summary`, drafted together)
  gates: the profile page's hero subhead (`summary` or `description`) and
  its Description card; the compare matrix's Description row, per-cell
  (`_desc_cell`, mirroring `_agent_cell`); and the `/tools/software`
  directory card, which the Phase 0 investigation flagged as a real,
  distinct leak Agent taxonomy never had to handle — the card's `ALL_TOOLS`
  JSON keeps the raw text regardless of verification state (the admin
  Quick Edit panel needs it verbatim even when unverified), so gating
  happens client-side instead: a `description_needs_verification` boolean
  rides in each entry, and both the `.tool-desc` render and the search-
  match string check it against the page's existing `AUTHED` global before
  showing/matching the text. Verified live with a real headless-browser
  session (anonymous visitor: blank card, search for drafted text returns
  zero results; admin: full text plus an "Unverified—hidden from visitors"
  label, search matches), per this repo's UI-testing standard.
  **Community profile** (`community_profiles.needs_review`, `NOT NULL
  DEFAULT 0`) gates the ENTIRE drafted profile at once, not per-field —
  `needs_review` is already a single whole-profile flag (see the Phase G
  PR 2 / confidence-indicator bullets elsewhere in this doc), and gating
  only the 12 confidence-tracked narrative fields while leaving `founded_
  year`/`cpe_eligible`/the Details card's Format contribution visible
  would read as a half-reviewed page rather than a clean not-yet-reviewed
  state — confirmed as the intended scope with Brian before building
  (Phase 0 open question). Implemented as one `_display_profile` swap (the
  real `profile` dict when verified or admin, `{}` when unverified+public)
  that every downstream read — the "Bottom line" verdict callout, its
  Sources citation list, the four grouped profile cards, and the
  profile-sourced Details-card lines — reads from instead of `profile`
  directly, so a public visitor sees exactly what they'd see if the
  profile had never been drafted, and an admin sees every field labeled.
  The compare matrix (`/tools/communities/compare`, which had no `authed`
  variable at all before this) gates the same way per community, via a
  `display_profiles` dict and a per-cell `_profile_cell` mirroring
  Description/Agent taxonomy's compare-matrix pattern.
  **Both columns are `NOT NULL DEFAULT 0`** (confirmed by attempting a raw
  `UPDATE ... SET ...=NULL`, which SQLite itself rejects) — so there is no
  real NULL row in production, and a legacy row predating either feature
  already reads as `0`/verified with no migration needed. Every gate still
  reads via `bool(row.get(...))` rather than a bare subscript, so a row
  dict missing the key entirely (not reachable through any real DB read
  today) degrades to the same verified behavior, covered by a monkeypatched
  test in `tests/test_review_state_publish_gates.py`.
  **Deferred, not built, and named together in the PR as one follow-up
  item**: the software/community Chat Matchmakers
  (`linklib/matchmaker.py`) feed raw `description`/`summary` into Claude's
  context unfiltered by `description_needs_verification` — a synthesized
  matchmaker answer could still surface unverified text through that
  channel — and the `/tools/software` directory card's `ALL_TOOLS` JSON
  still carries the raw, unverified `agent_taxonomy_note` text in page
  source for search-matching purposes (pre-existing, since before this
  PR) the same way it now carries unverified `description`/`summary` (new
  in this PR) — both are UI/search-level gates, not data-removal, on a
  payload only ever meant for a signed-in admin's Quick Edit panel to read
  verbatim regardless of verification state.
- **Citation-tag investigation + generation-path fix (2026-08) — supersedes
  Phase 1b/2's `inject_markers=False` decision for Agent taxonomy and
  Description; Community profile (Phase 3) is unchanged and still on the
  old contract.** A throwaway one-off regeneration script
  (`scripts/regen_ai_drafted_fields.py`, run once via `railway ssh`, never
  a permanent feature) surfaced literal `(cite index="D-S">...</cite>`
  pseudo-tag text baked into public `agent_taxonomy_note`/`description`
  fields on 5 of 5 sampled tools with fetched source pages, editor-facing
  asides ("the provided pages," "worth flagging for a directory reader"),
  and a memory-drafted note shipped live with no fetch-failure signal
  anywhere (Coupa). Root-caused by a live diagnostic call
  (`scripts/diagnose_agent_taxonomy_citations.py`, read-only — no write
  calls, confirmed by grepping it for `lib.`), then confirmed against a
  reproduction of the actual bug shape checked out from `origin/main` at
  the time: `.citations` came back empty (the real Citations API never
  fired) on the calls that showed tags, `stop_reason` was `end_turn` (not
  truncation), and Anthropic's own docs confirm citations and structured
  output are mutually incompatible ("citations require interleaving
  citation blocks with text output... incompatible with the strict JSON
  schema constraints of structured outputs"). The `inject_markers=False`
  strict-JSON contract Phase 1b/2 chose to keep citations technically
  legal alongside a JSON response left the model with no clean, API-backed
  way to signal a cited claim inside a single JSON string — free to
  improvise its own pseudo-XML tag notation instead, with nothing anywhere
  in the pipeline recognizing or rejecting it.
  **The fix drops the JSON contract entirely for these two fields — plain
  prose, real `inject_markers=True` citations (the same pattern
  `agent.py`'s FP&A Buddy has trusted since Phase 1a), `confident` (and,
  for Description, `summary`) recovered from trailing `"KEY: value"`
  sentinel lines via a new `linklib.enrich._split_trailing_sentinels`
  rather than `json.loads`.** A real, disclosed side effect of dropping
  `json.loads` as the parsing boundary: the whole class of truncation-
  driven `JSONDecodeError` failures the investigation also found (Opus 5's
  adaptive thinking exhausting `max_tokens` before any visible output)
  can't recur here — a missing or malformed sentinel degrades to a safe
  default (`confident=False`) instead of losing the entire draft.
  `entity_citations`/`set_entity_citations`/`clear_entity_citations` and
  every caller downstream of `ToolDescriptionDraft`/`AgentTaxonomyResult`
  (the script, both admin AJAX routes, `_run_tool_research`,
  `_citations_list_html`, the public/admin publish-gate branches) are
  completely unchanged — the citations list's shape never moved, only how
  it's produced. The four new prompt rules closing the gaps the
  investigation found (no markdown emphasis syntax, no editor-facing
  address, no review-scores/testimonials/logos/reported-results) apply to
  both fields; Community profile's own prompt already had an equivalent
  no-markdown rule (rule 7) but not the other three.
  **Verified two ways, deliberately not conflated**: `tests/
  citations_fixtures/enrich_sentinel_fixtures.py` +
  `tests/test_enrich_sentinel_parsing.py` (28 cases, written against the
  spec before the implementation was wired to it) prove the deterministic
  parsing side — including `OLD_BUG_REPRODUCTION_CASE`, Datarails' actual
  production shape run through the post-fix code and, for comparison,
  through `origin/main`'s pre-fix code loaded from a separate module path:
  the tags leak through in BOTH versions (no code-level filter can safely
  strip an unbounded, unknown bad-output pattern after the fact — the fix
  is preventative, at the prompt level, not corrective), with one verified
  side benefit — the new code can no longer mistake a stray `"confident":
  true"` JSON key for the new `CONFIDENT:` sentinel the way the old code
  did. What no unit test can prove — whether the new prompt actually stops
  the model from reverting to the old shape live — is `scripts/
  diagnose_agent_taxonomy_citations.py`'s job: extended to print the fully
  parsed result (via the same `extract_citations`/`_split_trailing_
  sentinels` calls the real functions make, not a reimplementation) plus
  an explicit `REGRESSION CHECK: PASS/FAIL` line, run post-merge against
  the deployed fix before any bulk regeneration touches the rest of the
  ~73 tools the throwaway script never reached.
  **Community profile (`generate_community_profile`) is explicitly NOT
  touched by this fix** — confirmed to have the identical vulnerable shape
  (document block + citations enabled + `inject_markers=False` + strict
  JSON), just never exercised by the throwaway script's run (communities
  were untouched). Committed as the next PR, not an indefinite follow-up —
  a materially bigger rewrite (23 fields + a 12-key confidence object in
  one response, vs. one or two prose fields).
- **Citation-tag fix, Community profile follow-up (2026-08) — the committed
  next PR from the bullet above; `generate_community_profile` now shares
  the same plain-prose/real-citations contract, closing the last
  `inject_markers=False` holdout.** Same root cause, same fix shape (drop
  the JSON contract, `inject_markers=True`, the same four new D1 prompt
  rules) — but 23 fields in one response instead of one or two meant the
  existing `_split_trailing_sentinels` parser (a backward scan for a short
  tail of `KEY: value` lines) wasn't enough on its own. New
  `linklib.enrich._parse_labeled_blocks(text, keys, terminal_key=None)` is
  a forward-scanning, order-tolerant parser for `FIELD_NAME:`-headed
  multi-line body blocks: it walks the response line by line, opening a
  new field whenever a line matches `^KEY:` for a key in `keys`, and
  appending every subsequent line to that field's body until the next
  recognized header. `_split_trailing_sentinels` is unchanged and still
  does the actual work for the 12-key confidence block's own body, once
  `_parse_labeled_blocks` has isolated it as the `"confidence"` field.
  **`terminal_key="confidence"` is the one genuinely new mechanism, and
  it exists to prevent real data corruption, not just to simplify
  parsing**: the `CONFIDENCE:` block's 12 sub-key lines (`IDEAL_MEMBER:
  true`, etc.) share literal names with 12 of the 23 top-level fields —
  without a hard stop, `_parse_labeled_blocks` would keep recognizing
  headers past `CONFIDENCE:` and treat a sub-key line as a fresh top-level
  field, silently overwriting the real, already-parsed `ideal_member`
  narrative text with the literal string `"true"`. Passing
  `terminal_key="confidence"` tells the parser to stop all further header
  recognition the moment it opens the `CONFIDENCE:` block and just
  consume every remaining line as that block's body. **Proven load-bearing,
  not just present**, via a matched positive/negative test pair in
  `tests/test_community_profile_sentinel_parsing.py`:
  `test_terminal_key_protects_data_integrity_not_just_formatting` (with the
  guard, real fields survive uncorrupted) and
  `test_without_terminal_key_the_collision_would_actually_corrupt_data` (the
  identical input, parsed *without* `terminal_key`, asserted to actually
  corrupt `ideal_member` — a negative control proving the guard does real
  work, not decoration).
  A response with no recognized field header at all (`_parse_labeled_blocks`
  returns `{}`) is treated as a hard failure — `generate_community_profile`
  returns `None` — deliberately stricter than Description/Agent taxonomy's
  "some text beats none" tolerance, because `upsert_community_profile` is a
  full replace of all 23 `community_profiles` columns on every save: silently
  persisting an all-empty draft wouldn't just carry forward stale text, it
  would blank the community's entire profile outright.
  **Two smaller correctness fixes landed in the same pass, both found while
  building the fixtures rather than assumed away:** (1)
  `_parse_community_confidence`'s old logic compared `bool(raw.get(...))`,
  which is `True` for any non-empty string including the literal text
  `"false"` — a real Python footgun once confidence values started arriving
  as plain-text sentinel strings instead of JSON booleans. Fixed to compare
  the raw value against the literal string `"true"` (case-insensitive), with
  a dedicated regression test
  (`test_confidence_string_false_does_not_evaluate_truthy`). (2) The old
  JSON prompt stored `null` for "unknown" on several fields
  (`notable_members`, `public_criticism`, and most of the short
  factual/categorical fields), normalized to `""` at parse time
  (`data.get(f) or ""`); plain text has no `null`, so the new prompt asks
  the model for a literal placeholder phrase instead
  (`"Unclear"`/`"None reported"`/`"None publicly reported"`). A new
  `_field_or_placeholder_empty` helper coerces those phrases back to `""`
  at parse time (case-insensitive, tolerant of a trailing period the model
  might add out of habit) — restoring the original storage contract rather
  than silently changing what an "unknown" field looks like in the DB.
  Deliberately **not** applied to `cpe_eligible`, whose `"Unclear"` value
  was already a real, literal answer in the *original* JSON prompt (not a
  null-placeholder) — coercing it to `""` there would be a genuine
  regression, not a fix.
  **Verified the same two ways as the first fix, deliberately not
  conflated**: `tests/citations_fixtures/community_profile_sentinel_fixtures.py`
  + `tests/test_community_profile_sentinel_parsing.py` (21 cases, written
  and confirmed failing against unwired code before the implementation
  existed — including `COMMUNITY_OLD_BUG_REPRODUCTION_CASE`, a JSON blob
  with an embedded pseudo-citation tag, asserted to parse to `None` under
  the new code — stricter than the tool-side fix's "partial recovery,"
  since nothing in a JSON blob matches the new `FIELD_NAME:` header format
  at all) prove the deterministic parsing side. Every pre-existing test that
  mocked `generate_community_profile`'s old JSON contract
  (`tests/test_community_profile_citations.py`,
  `tests/test_community_confidence_indicator.py`) was rewritten to the new
  plain-prose format and to the same realistic multi-block response mocking
  (`_mock_anthropic_citing(monkeypatch, blocks)`, one SDK text block per
  citation boundary — mirroring how the real Citations API actually splits
  a response, not one block carrying an entire cited-and-uncited response
  the way the original single-block mocks did) established for the first
  fix; `tests/test_enrich_community_profiles.py` needed no changes, since it
  mocks `generate_community_profile` at the function level rather than the
  Claude API. What no unit test can prove — whether the new prompt actually
  stops the model from reverting to the old shape live — is the job of a
  new sibling script, `scripts/diagnose_community_profile_citations.py`
  (a genuine sibling to `diagnose_agent_taxonomy_citations.py`, not an
  extension of it — the response shape is different enough, per Brian's
  explicit call, to justify a separate script rather than branching one
  script two ways): it makes the raw Anthropic call directly, dumps every
  content block's `.text`/`.citations`, then prints the fully parsed result
  (all 23 fields, the 12-key confidence dict, and the citation list) via the
  exact same `extract_citations`/`_parse_labeled_blocks`/
  `_split_trailing_sentinels`/`_parse_community_confidence` calls the real
  function makes, plus an explicit `REGRESSION CHECK: PASS/FAIL` line — for
  Brian's post-merge `railway ssh` verification against real communities,
  same convention as the first fix's diagnostic re-run.
- **Spaced-em-dash deterministic backstop (2026-08) — closes a gap the
  citation-tag fixes above didn't touch: a prompt-only voice rule can't
  guarantee compliance, however emphatically worded.** After the citation-
  tag fixes shipped and a full 157-tool + 40-community regeneration ran
  clean on cite-tag pollution, 63 rows / 52 tools still carried a spaced em
  dash — `voice_core`'s own "HARD MECHANICAL RULES" say "never violate,"
  and the rule was confirmed present in every one of the four generation
  prompts (`generate_tool_description`/`generate_tool_agent_taxonomy`/
  `generate_tool_differentiation`/`generate_community_profile`, all four
  interpolating `{voice_core}` via `_resolve_voice_core`), so this is not a
  missing-instruction bug — an LLM's compliance with a natural-language rule
  is probabilistic, not a guarantee, and prompt wording alone can't make a
  violation rate literally zero. New `linklib/voice_mechanics.py`
  (`fix_spaced_em_dashes`/`normalize_voice_mechanics`, pure regex
  substitution, no model call) is wired into every `Library` write method
  that persists a prose-capable field — both tables' AI-drafted fields
  (`tools.description`/`summary`/`agent_taxonomy_note`/
  `competitive_differentiation`/`suite_note`, every prose column in
  `community_profiles`) and the `communities` table's own AI-draftable
  listing fields (`demographic`/`cost_note`/`notes`/`local_markets`, from
  `generate_community_listing`). Applied at the `Library` layer rather than
  inside each `generate_*` function specifically so it also covers a
  hand-edit save and any future write path — every one of `webapp/app.py`'s
  15 real call sites into these methods, and `scripts/
  regen_ai_drafted_fields.py`'s own writes, is covered automatically with no
  per-call-site change, confirmed by grepping every write path before
  wiring the fix in rather than assumed. A companion one-off cleanup,
  `scripts/fix_spaced_em_dashes.py` (preview/`--apply`, write-then-read-back
  per row/column), fixes content already written before the backstop
  existed — deliberately narrow raw single-column `UPDATE`s rather than
  reusing `Library.update_tool_agent_taxonomy`/`upsert_community_profile`,
  since those methods' real side effects (clearing
  `agent_taxonomy_needs_verification`, clearing `entity_citations`) assume a
  human just made a real edit, which a pure whitespace fix is not.
  `scripts/report_regen_blast_radius.py` (previously tools-only) was
  extended in the same pass to analyze `community_profiles`' 22 prose
  columns for cite-tag pollution and legacy-shape, since communities are
  logged under one shared field name (`"community_profile"`, all 23 columns
  drafted in a single call) and the report now breaks a hit down to the
  actual underlying column; it also accepts multiple `--log-file` inputs,
  deduplicated to each entity's LAST logged status, to match the real shape
  of a run spanning several dropped SSH sessions.
- **`report_regen_blast_radius.py` made log-independent (2026-08 follow-up)
  — a real gap found the moment the em-dash cleanup above actually ran in
  production.** The cleanup itself succeeded (456/456 fields fixed, verified
  via its own write-then-read-back), but the blast-radius report couldn't
  verify it afterward the way CLAUDE.md's own sequencing described: the
  JSONL log the regen run wrote lived only in that container's local
  filesystem, and the em-dash PR's merge triggered a fresh Railway deploy —
  a new container, so the log never reached persistent storage. The report
  now scans the full current catalog directly by default (every `tools` row
  regardless of `approved`, every `community_profiles` row) instead of
  requiring `--log-file` — pollution/legacy-shape are properties of stored
  content, not of a particular run, so the log was only ever a scoping
  convenience. `--log-file` still works unchanged when passed (same
  multi-file dedup), for the one thing a full-catalog scan structurally
  can't reconstruct — per-attempt failure detail — which DB-scan mode
  reports as "not tracked" rather than a misleading zero. Implementation is
  additive, not a rewrite: DB-scan mode synthesizes a `status="success"` row
  per (entity, field) for every catalog row and feeds it through the exact
  same `_analyze`/`_print_sections`/`_print_dedup_summary` pipeline the
  log-scoped path already used.
- **Cost guards are layered**: per-turn grounding-character caps, a max-tokens
  budget per tier, a follow-up cap (6 extra turns, counted from the
  conversation's recorded `ask_questions` rows — never from anything
  client-supplied), a history-character cap carried into the prompt, and the
  authoritative monthly per-user dollar cap checked against real recorded
  spend before any API call. The monthly cap SUMs `cost_usd`, which already
  includes the query-embedding cost — but never embed-ON-SAVE cost, which
  lives on a separate table entirely (`article_embeddings`, Brian's overhead,
  never a user's).
- **Conversations resume across reloads and devices.** The `/tools/fpa-buddy` page offers
  a "Recent conversations" list on load (`GET /ask/conversations` — the
  user's last 5, first question as the label) and loads a full transcript
  from `GET /ask/conversations/{id}`: per-turn question, answer, the
  `citations_json` snapshot (parsed server-side so the client re-renders each
  turn's `[n]` markers as links against that turn's own list), and the user's
  existing feedback state, ready to re-rate. Both endpoints enforce the same
  ownership contract as `POST /ask` (404 unknown, 403 someone else's); a
  conversation at the turn cap loads read-only with the "start a new
  question" affordance. Resume is offered, never forced — a fresh question
  starts a new conversation exactly as before.
- **Two compatibility notes.** A stale pre-deploy tab that still sends a
  `history` field in the `POST /ask` payload is tolerated: the field is
  ignored (untrusted), and the request proceeds on the server-rebuilt
  history. And token-only access (`X-Save-Token`) is now effectively
  one-shot: its turns were never recorded (there's no `users` row to
  attribute them to), so there is nothing server-side to continue — it gets
  `conversation_id: null` back and any `conversation_id` it sends is
  rejected. Turns recorded before `conversation_id` existed (stored as `''`)
  are likewise not resumable; they still appear in `/ask/history`.
- **Each turn's cited sources are persisted, and answers can be rated.** The
  API-verified citation list is stored on the turn's row
  (`ask_questions.citations_json`) as a snapshot — feed and web sources are
  transient, so the stored title/url is the record and is never re-resolved;
  library entries additionally carry their `articles.id`. Under each answer on
  `/tools/fpa-buddy`, quiet 👍/⚠️/👎 controls post to `POST /ask/feedback` (same auth as
  `/ask`; you can only rate turns from your own conversations), upserting one
  `ask_feedback` row per turn per user — a changed rating updates in place. The
  `/admin/ask-feedback` page triages ratings with the question, answer, and
  cited sources; nothing feeds back into prompts or retrieval automatically.
  The snapshot shape is deliberately per-turn — it's what the resume flow
  replays to re-render past turns' `[n]` markers.
- **Server-rendered surfaces share one citation renderer.** Every
  server-rendered view of a stored answer — `/ask/history`, the
  "search past questions" section on `/tools/fpa-buddy`, and
  `/admin/ask-feedback` — calls
  `_render_cited_answer(answer, citations_json, truncate=?)` in
  `webapp/app.py`: it linkifies each `[n]` marker against that turn's own
  snapshot (same marker contract as the client — 1–2 digits, not followed by
  `(`, only in-range numbers link, so a literal `[2026]` stays text),
  truncates without ever splitting a marker, and returns the matching
  numbered source list. Legacy rows (backfilled `citations_json='[]'`)
  degrade to plain literal markers with no source list — never fabricated
  links, never an error. **Any future server-rendered answer surface must
  call this helper**, and it is deliberately *not* unified with
  `/tools/fpa-buddy`'s own client-side JS rendering (`mdInline`/`srcListHtml`
  over live API responses) — that's a different layer; keep them separate. The
  admin CSV export deliberately keeps raw literal `[n]` markers (no HTML in a
  CSV) and instead appends a plain-text `citations` column resolving them.

### Archive save / enrichment pipeline

All capture paths converge on `linklib/pipeline.py::ingest_url` or the
`library_queue` review flow:

- **Direct saves** (trusted — go straight into `articles`): the bookmarklet →
  `POST /save` (token auth), `POST /feed/save` from the feed reader (admin),
  and the CLI (`scripts/add_link.py`). `ingest_url` fetches the page
  (trafilatura preferred, BeautifulSoup fallback — `linklib/extract.py`),
  upserts by normalized URL, then (durability audit item 1) runs the fresh
  fetch through `extract.assess_extraction_quality()` — the exact same check
  the Reader content backfill uses — and, only when the fetch's content is
  what actually ended up stored (never overriding the write-once merge that
  protects existing good content), flags the row via
  `Library.set_content_check_flag` and writes a `content_refetch_log` row
  (`source='save'`) on failure. Never blocks or rejects the save; a flagged
  row simply enters the same backfill/manual-review scope with a real reason
  attached, surfaced as its own tile on `/admin/library/backfill-content`,
  instead of looking indistinguishable from a good save. Then enriches: one
  Haiku call
  (`linklib/enrich.py`) generates a summary and tags biased toward the
  existing tag vocabulary (`linklib/tagstyle.py` learns the curator's tagging
  style and feeds the prompt). Enrichment is **additive and optional** — a
  save without an API key just leaves `enriched=0` for
  `scripts/enrich_backfill.py` to fill later. `ingest_url` then calls
  `pipeline.embed_article` (#93) — same additive-and-optional shape: no
  `OPENAI_API_KEY` or a failed call just leaves the article FTS5-searchable
  but not yet in `articles_vec`, for `scripts/embed_backfill.py` to catch
  later.
- **Queued candidates** (reviewed — land in `library_queue` first): the RSS
  scan and one-time sitemap backfill (`linklib/queue.py`), and member reader
  submissions (`POST /library/submit`, honeypot-protected, deliberately
  un-enriched until review). `linklib/suggest.py` adds an advisory
  Claude-predicted keep/skip. The admin reviews at `/admin/library/queue`;
  **promoting** moves the row into `articles` preserving any enrichment
  already paid for, **dismissing** keeps the row so it's never re-proposed.
  Embedding happens after promotion too, off-request (`background_tasks`,
  same pattern as the post-promotion DB backup) since promotion itself has no
  other network call to piggyback the latency on.
- FTS5 stays in sync automatically via the triggers — every insert/update
  cascades into the index. `articles_vec` does **not**: a SQL trigger can't
  make a network call, so embeddings are written from Python instead
  (`embed_article`, `embed_backfill.py`), making vector search **eventually
  consistent by design** rather than trigger-synchronous like FTS5 — an
  article is always immediately findable via FTS5, and via vector search
  once its embed call (inline or backfilled) has actually completed.

### Reader merge (Phase 5)

`GET /read` is a single three-pane admin tool that replaces the two
previously separate `/library/archive` (search + browse the saved Archive)
and `/library/feed` (the RSS reader) pages — both retired outright, no
compatibility redirect (nothing was bookmarked, same precedent as every
other retired-route call in this doc). The three panes:

- **Left rail** — navigation and filter vocabularies only, never search
  (see "Search placement" below). Quick views (Feed / Archive / Read Later,
  each with a live count), plus one per-view vocabulary: a Sources tree in
  the Feed view (OPML category → per-source counts) driving client-side
  filtering over one already-loaded batch of feed items — same approach the
  old Feed page's source checkboxes used, just restyled — and a "Tags" bar
  in the Archive view, its direct counterpart. Switching quick views is a real
  page load (`/read?view=feed|saved|readlater`); filtering *within* the Feed
  view is client-side JS, no round trip.
- **Middle list pane** — server-rendered rows for whichever view is active,
  with that view's search in the pane's own header (`.rr-list-search`),
  directly above the rows it filters. Archive search is a plain GET reload
  (`/read?view=saved&q=...`), matching this codebase's existing
  server-rendered-search convention rather than a client-side SPA search;
  Feed search filters client-side through `rrApplyFilter`. Read Later has
  no search and renders none. A subtle coral alert renders here (via
  `authcheck.stale_domains`) only when a subscriber cookie has actually gone
  stale — same underlying `authcheck.check_auth_cookies`/`get_auth_status`
  mechanism `/admin/library`'s banner already used, just a second,
  conditional surface for it. `/admin/library`'s own manual re-check control
  (`POST /admin/auth/recheck`) is untouched; the Reader's alert link posts to
  the same route.
- **Right reader pane** — empty state until a row is clicked, then loaded
  via `GET /api/read-article?id=...` or `?url=...` (JSON, admin-gated) with
  no full page navigation. That endpoint, and the standalone
  `GET /read/{article_id}` page (what `GET /read?id=...` used to be, now a
  path param — for a direct link to one saved article), both call
  `_resolve_reader_content`, one shared helper for "look up by id in the DB
  first, using cached `content` when it's substantial, else fetch the URL
  live" — previously duplicated inline in the pre-merge `/read` route, now a
  single implementation.

**What got removed, deliberately, in this merge:**
- The old Archive page's inline "ask your archive a question" box (a
  duplicate, lesser FP&A Buddy surface) is gone outright, not migrated and
  not pointed at `/tools/fpa-buddy` — `POST /ask` and `linklib/agent.py` are
  untouched, so FP&A Buddy itself is unaffected.
- Every per-item Edit tags / Delete / Archive-management control that used
  to live inline on the Archive page and the old `/read?id=...` view is
  gone. Those stay exclusively in `/admin/library`'s dedicated tools (Tag
  cleanup, Remove content, etc.) — the Reader is a reading surface, not a
  curation one. Save-to-library (`POST /feed/save`) and the Read Later
  toggle (`POST /feed/read-later`) are the only actions still exposed, both
  reused verbatim. (**Tags alone came back in Phase 5c** — a deliberate,
  narrow exception documented below. Delete/archive did not, and this
  bullet still governs them.)
- The old bare paste-a-URL empty state (`GET /read` with no `id`/`url`) is
  gone — the merged page's list-driven UX (click a row, the reader pane
  loads it) replaces that need. There's no standalone way to read an
  arbitrary not-yet-saved URL outside the Feed/Read Later list rows anymore.

`/admin/library`'s `_LIBRARY_TOOLS` gained a ninth entry, "Open Reader" →
`/read`, first in the list — the entry point Phase 1 deliberately deferred
to this phase. **Superseded by the later Layout/Admin Nav/Library Cleanup
phase** (see that section below): "Open Reader" was pulled back out of
`_LIBRARY_TOOLS` (now 8 entries) in favor of a dedicated, more prominent
callout at the top of `/admin/library` itself, plus a new direct link from
`/admin`'s CFO Toolbox group — `/admin/library`'s own tool list is no
longer the only, or the primary, way to reach `/read`.

**Reader fixes/follow-ups (post-launch pass):**
- **Paywalled Feed items now open in-app like any other row.** They used to
  be wrapped in a real `<a target="_blank">` instead of getting `rrOpen()`'s
  normal click handler — a genuine bug (the click hijacked straight to an
  external tab before the reader pane, and its own "Original →" toolbar
  link, ever got a chance to render), not a deliberate "no in-app reader for
  paywalled sources" design. Extraction still runs and gracefully falls back
  to "Content could not be extracted, Open original →" when a paywall blocks
  it, same as any other fetch failure — the "🔒 Paywalled" badge stays, only
  the click behavior changed.
- **Article content is now real structured HTML for a live fetch, not
  flattened plain text.** `linklib/extract.py` gained `extract_reader_html()`
  — a BeautifulSoup-based sanitizer that keeps paragraphs/headings/lists,
  absolute-izes and preserves `<img>`/`<a>`, and strips everything else
  (chrome tags, all non-safelisted attributes) — used only by
  `_resolve_reader_content`'s live-fetch branch, over `PageData.raw_html` (a
  new field on the existing dataclass; the fetched HTML, kept only so a
  caller wanting structure doesn't need a second HTTP round trip).
  `_extract_content()` itself — the plain-text extractor the ingest/search/
  enrichment pipeline depends on staying plain text (`articles.content`,
  FTS5, the Claude enrichment prompt, `looks_paywalled()`'s length check) —
  is deliberately untouched in contract, only its always-active BeautifulSoup
  fallback (trafilatura isn't a declared dependency, so in practice this is
  the path that runs) was fixed to actually preserve paragraph breaks
  (`\n\n`-joined blocks) instead of `get_text(" ", strip=True)` flattening
  everything into one line. **Known gap:** a saved article's cached
  `content` in the DB is still whatever plain text ingest-time extraction
  produced — this fix doesn't retroactively restore images/links for
  already-saved articles (no backfill shipped in this pass; would need a
  live re-fetch per article, out of scope here), though newly-ingested or
  re-enriched articles going forward at least get real paragraph breaks in
  their plain-text `content`.
- **Reader body column widened** 640px → 700px, matched against Instapaper's
  own desktop reading column width (~680–700px, measured off the reference
  screenshots in the original design handoff) — the original build brief's
  target, which the initial implementation undershot.
- **Two new reader-pane features**, both Instapaper-parity asks:
  - **Find in article** — a separate, article-scoped text search (distinct
    from Archive-view's list search) via a toggleable find bar in
    `.rr-reader-actions`; walks `#rr-reader-body-text`'s text nodes with a
    `TreeWalker`, wraps matches in `<mark>`, next/prev navigation, closes
    and clears on Escape.
  - **Distraction-free reading** — a header toggle (outward/inward diagonal-
    arrow icon, immediately next to the close button, matching Instapaper's
    own icon and placement) that hides `.rr-rail` and `.rr-list-pane`
    outright (and their resize handles) and lets the reader pane take the
    freed width, full width, centered. **Corrected 2026-08** (Reader
    cleanliness pass): the original version only shrank the list pane to a
    220px "sliver" (title/source/live time-remaining) while leaving the left
    rail fully visible on desktop — confirmed live as an actual bug against
    real Instapaper reference screenshots, not the intended design: in
    Instapaper's own expand view, BOTH the rail and the list disappear
    completely, leaving just the centered reading pane with the sticky
    action bar still visible and a collapse-back arrow in its top-left
    corner. The sliver mechanism (`.rr-sliver`, `rrUpdateSliver`, the
    scroll-driven time-remaining tracker) is removed entirely — the existing
    `#rr-reader-expand` button in the sticky `.rr-reader-header` (it already
    swapped between expand/collapse icons) doubles as that collapse-back
    affordance, since there's no sliver left to click through. Keyed only to
    whether an article is open, not to which quick view it came from, so it
    behaves identically for Feed and Archive.
- **Thousands separators** (`:,` format spec) added to every large-count
  render sitewide that was missing one — the Reader's quick-view badges and
  list-pane item counts (inherited the gap from the pre-merge Archive page),
  plus `/admin/system/page-index`'s stat cards and the Tag cleanup admin
  table's per-tag counts. An audit of every other `lib.count()`/large-list
  count site found no other gaps.
- **Feed view now has a real search box.** Confirmed against the original
  `Feed.dc.html` design export first (its list-pane header only ever had a
  decorative magnifying-glass *shape* — no `onClick`, no search-state
  variable, never wired to anything even in the design tool itself, so this
  was never a shipped-vs-designed gap) and flagged for a decision rather
  than built; built in the very next round once the decision came back yes.
  `#rr-feed-search`, client-side, same mechanism the existing category/
  source filtering already used (`rrApplyFilter`) rather than a second
  parallel filter — `rrFeedCat`/`rrFeedSrc` became sticky module-level state
  (previously passed as one-shot function args from `rrSelectCategory`/
  `rrSelectSource`) so a search keystroke can re-run the same active
  category/source combination without needing to know it externally.
  Matches a row against its whole visible text (title, source, excerpt,
  tags), same broad-match spirit as Archive view's server-rendered search.

**Second follow-up round — a real mobile bug, root-caused before fixing (not
assumed from the bug report's own guess):** "clicking an article on mobile
does nothing" turned out not to be a broken click handler or a hidden touch
target at all — `rrOpen()` fired correctly every time, confirmed live via a
real touch-enabled mobile-viewport session (`element.tap()`, not just
`.click()`) that hit-tested the row's own coordinates and found nothing
overlapping it. The actual mechanism: on the mobile stacked layout
(`.rr-shell{display:block}`), `#rr-reader` sits at the bottom of the DOM,
after the full item list — often 600px+ below the fold — and nothing ever
scrolled the page to it, so the update was real but invisible. Fixed with an
unconditional `pane.scrollIntoView(...)` at the end of `rrRenderArticle`
(a no-op on desktop, where `.rr-shell` is already viewport-height-
constrained with its own internal scroll) plus, going further than a pure
scroll fix per the follow-up ask below, an auto-focus-mode default.

**Responsive default: mobile portrait now opens straight into distraction-
free reading; landscape (with room) gets the real 3-pane layout.**
`rrMobileNoRoom()` — `window.matchMedia('(max-width:900px) and
(orientation:portrait), (max-width:699px)')` — gates both the CSS stacking
breakpoint and a call in `rrRenderArticle` to auto-`rrSetFocusMode(true)`
when it matches. Deliberately orientation-aware rather than reusing the
homepage's own `1024px` mobile-stacking breakpoint (`.home-grid`, same
file) — that number solves the *mirror-image* problem: it's pushed *up*
so a landscape phone (~930px) still gets the homepage's mobile stacked
order instead of flipping into its 2-column desktop grid on pure rotation.
The Reader wants the opposite outcome (a landscape phone *should* get the
real 3-pane layout, since it has the width for it), so a single shared
number can't serve both goals — this stays a combined width+orientation
query instead. The `699px` fallback (any orientation) is the hard floor
below which 3 panes can't fit even at their own CSS min-widths
(150+300+240px); only the smallest common landscape phones (iPhone
SE-class, ~667px) fall under it and stay stacked in landscape too, correctly.
In the forced-focus state this reaches the same `.rr-shell.rr-focus-mode`
rule desktop expand mode uses (see the corrected "Distraction-free reading"
bullet above) — since 2026-08 that rule hides the rail on every viewport, not
just mobile, so there's no separate mobile-only rail-hide rule to maintain
here any more.

### Search placement: list-pane header, not the left rail

Both views' search boxes render in the list pane's header. They spent from
the Phase 5 merge until this correction in the **left rail** instead, which
was drift rather than a decision — nothing in the repo ever evaluated the
placement. The evidence, since a claim like that should be checkable:

- The pre-merge `/library/archive` page put search in a page-width header
  bar **directly above the list**, in the same 960px column as the cards.
  That page had no rail at all.
- Phase 5's own ARCHITECTURE.md (added in the very commit that moved it)
  describes Archive search under its **middle list pane** bullet, and its
  exhaustive left-rail inventory — quick views, plus a Sources tree in Feed
  view only — never mentions search. The same commit's code put the form in
  the rail. A doc/code contradiction inside one commit.
- The mechanism was assignment to a variable named `sources_html`, which
  exists to hold Feed's Sources tree and is only ever interpolated into
  `rail_html`. No commit message, comment, or doc anywhere says a relocation
  happened, and the merge's "what got removed, deliberately" list doesn't
  mention it either.
- The Feed search box's CSS (`.rr-search-form{padding:14px 22px 0}`) carried
  the **list pane's** 22px gutter while rendering inside a 232px rail whose
  own gutter is 14px, and every sibling in that rail uses rail-scale insets.
  The box was styled for a pane it wasn't in.

**Caveat, recorded rather than papered over:** the `Feed.dc.html` design
export is not in this repo and could not be re-read when this was corrected
(`/design-login` is unavailable in a headless session). Every second-hand
account of it in git history places its magnifying glass in the **list-pane
header**, and none describes a search box in a rail — but that is testimony
about the file, not the file itself.

Only placement changed. Each view keeps its own mechanism (Feed client-side
via `rrApplyFilter`, Archive a server GET), and the rail keeps the Sources
tree and tag bar, which are filter vocabularies rather than search. The
Archive rail's heading was "Search" — describing the form that used to sit
above the tag bar — so with the form moved it reads "Tags", matching Feed's
"Sources". Covered by `tests/test_reader_search_placement.py`, which asserts
DOM ancestry rather than geometry: on the stacked mobile layout the rail and
list pane both span the full width, so bounding boxes can't tell them apart.

### Reader cleanliness pass (2026-08) — sponsor/ad/cookie-banner stripping

A backlog item ("Read Later saves aren't scrubbed like Archive saves")
turned out to have a different root cause than its own premise assumed.
Read Later's bare-metadata `/save-later` insert was never the gap — every
Read Later item is resolved through `_resolve_reader_content`'s live-fetch
branch (`extract_reader_html`) on open, the same function an unsaved Feed
item or a not-yet-backfilled Archive article goes through. Investigation
found **neither extraction path had ever had any class/id-based content
filtering** — `_READER_JUNK_TAGS`/`_BLOCK_TAGS` only recognize chrome by TAG
NAME (`nav`/`header`/`footer`/`script`/...), so an ordinary
`<div class="sponsor-block">` or `<div id="cookie-consent-banner">` was
either collected as an ordinary block (`_extract_content`) or unwrapped,
keeping its content (`extract_reader_html`) — confirmed live via a saved
OnlyCFO newsletter rendering a full Brex sponsor block inline, and confirmed
by inspection that the identical content would have rendered the same way
via Archive, not just Read Later.

Fixed once, shared by both paths: `linklib/extract.py` gained
`strip_promotional_chrome(soup)`, called before either path's own tag-name
junk stripping/block collection. It decomposes (removes entirely) any
element whose class/id/`data-testid`/`data-test-id`/`data-qa` — normalized
(lowercased, non-alphanumeric stripped) — contains one of a curated set of
markers (`_PROMO_CHROME_MARKERS`: sponsor/advertisement/ad-slot/native-ad/
newsletter-signup/subscribe-widget/cookie-banner/cookie-consent/consent-
banner/gdpr/onetrust/... — deliberately multi-character, word-ish tokens,
never a single common word like "ad" that would also match "advice" or
"gadget"). Matched only against element attributes, never text content, so
a real paragraph that happens to mention "sponsor" as a plain word is never
touched. Covered by `tests/test_promo_chrome_stripping.py` — including a
reproduction of the exact reported bug shape, a false-positive guard for
common words containing "ad", and mixed-case/separator normalization.

No change was needed to `/save-later` itself, or to Read Later's schema for
THIS fix — the scrubbing lives entirely in the shared extraction layer both
paths already funnel through. (The follow-up PR below covers Read Later's
own always-live-fetch design, a separate, deliberately deferred decision.)

### Read Later content caching + manual refresh (2026-08 follow-up)

The sponsor-stripping fix above closed the scrubbing gap but left Read
Later's actual design unchanged: `POST /save-later` was (and, before this
PR, still is) a bare metadata insert — no fetch, no cache — so every open of
an unread item re-fetches live via `_resolve_reader_content`, every time,
with nothing ever stored. Per Brian's explicit scope decision (the Phase 0
report's tradeoff question), this PR caches Read Later's fetch at save time
the same way Archive does, rather than leaving it always-live:

- **`read_later` gains `content`/`content_html` columns** (migration +
  fresh-DB schema, both default `''`). `POST /save-later` now runs the same
  `linklib.extract.fetch_page` + `extract_reader_html` pair `ingest_url`
  uses for Archive — reusing PR 1's `strip_promotional_chrome` for free,
  since it lives inside `extract_reader_html` itself, not a separate call.
  Best-effort throughout: a fetch failure never blocks or rejects the save
  (same durability-audit precedent `ingest_url` already set) — the row still
  saves with empty content, and `_resolve_reader_content` falls through to
  its pre-existing live-fetch-on-open behavior exactly as before this PR, so
  a failed save-time fetch is never a regression. A missing caller-supplied
  `title` now also falls back to the fetched page's own title (the same
  `page.title` fallback `ingest_url` uses), narrowing how often the Read
  Later view falls back to its own "(no title)" placeholder.
- **`Library.add_read_later`'s `ON CONFLICT` write for `content`/
  `content_html` is CASE-guarded, never overwriting a good cache with an
  empty one** — a re-save whose own fetch happens to fail can't destroy a
  previously-cached good copy, the same write-once-on-empty guard
  `Library.upsert()` already uses for `articles.content`.
- **`_resolve_reader_content` gained a new cache tier and a `user_id`
  parameter.** Priority order: an Archive article's own `content_html`/
  `content` (unchanged, always wins — a real Archive save's backfilled
  content is a strict upgrade over anything Read-Later-specific) →
  **Read Later's own cached `content_html`/`content` for this user+url** →
  the pre-existing live-fetch-and-fallback chain (Wayback, Medium recovery,
  etc.), unchanged. `GET /api/read-article` resolves the signed-in user's id
  via `_current_user_id` and passes it through; `GET /read/{article_id}`
  (by-id only, can never resolve to a Read Later row) doesn't need it. The
  returned payload gained `is_read_later` (True only when a Read Later cache
  exists AND there's no Archive article — an Archive article's content isn't
  what a Read Later refresh would change, so the flag stays False there even
  if the URL also happens to be queued to Read Later).
- **`POST /read-later/refresh`** — the manual per-item "Refresh" action from
  the build brief: re-fetches the URL, replaces the cached copy via a new
  narrow `Library.update_read_later_content` (deliberately not a call
  through `add_read_later`, whose other fields have no fresh values to offer
  a content-only refresh and must never be touched by one). Session-gated
  (`_is_authed`/`_current_user_id`), NOT token-only — this is a button
  inside the already-authenticated `/read` UI, not a cross-origin
  bookmarklet call, so it uses the same session-cookie gate `/api/read-
  article` and `/library/{id}/tags` already do. Deliberately no automatic
  staleness detection — it only ever runs on a click. Non-destructive on
  failure: `update_read_later_content` is the same empty-guard as the
  `add_read_later` CASE clause, so a failed refresh leaves the previously-
  cached copy untouched and the route reports `{"ok": false}` (not a 5xx) —
  live-verified against both a genuinely unreachable URL and (given this
  sandbox's outbound network restrictions) network-blocked real domains,
  both correctly leaving the prior cache intact rather than blanking it.
- **Reader toolbar**: a new "Refresh" button (`RR_ICON_REFRESH`, same
  labeled-icon treatment as Find/Remove) in `.rr-reader-actions`, gated on
  `d.is_read_later` — Read-Later-only for now, per the build brief's
  explicit scope call (Archive's own content is managed by the admin's
  dedicated backfill/refetch tools, not this button). `rrRefreshReadLater()`
  POSTs to the new route, then reloads via the existing `rrLoadArticle` path
  so the refreshed content renders in place without a full page reload.

Deliberately NOT built: automatic staleness detection or a "last refreshed"
timestamp — the build brief was explicit that this is a manual-only action,
not automatic. See `tests/test_read_later_caching.py` for the full
regression coverage (Library-layer write-once guards, save-time fetch +
scrubbing, refresh success/failure/404, the new cache tier and
`is_read_later` flag, and the toolbar markup).

### Reader content-structure backfill (Phase 5b)

Closes the "Known gap" the Reader-fixes section above flagged: `extract_reader_html()`
only ever ran against a live fetch (an unsaved Feed item, or a saved article whose
cached `content` was too thin to use as-is), never against the ~4,500 articles already
saved as flattened plain text from the original ingest. No raw HTML is stored anywhere
for those rows, so restoring structure requires a genuine re-fetch of every one — this
phase is that re-fetch, run as a resumable, rate-limited, observable admin batch job.

- **New column, not a reuse of `content`.** `articles.content_html` (`TEXT NOT NULL
  DEFAULT ''`) holds the backfill's structured HTML per article. `content` stays plain
  text — it's a load-bearing contract (FTS5 indexing, the enrichment prompt,
  `looks_paywalled()`'s length check) that must not start holding markup. An empty
  `content_html` doubles as "not yet backfilled," the same not-yet-done signal
  `unenriched()` gets from `enriched=0` — just via an empty string instead of a flag.
  `_resolve_reader_content` now prefers a populated `content_html` over the plain-text
  `content` cache (a strict upgrade, never triggers a live fetch on its own).
- **Content sanity check beyond HTTP status.** `linklib/extract.py`'s
  `assess_extraction_quality(html, plain_content, blocked)` runs after every fetch,
  before anything is stored — a 200 response with a bot-challenge or paywall-preview
  body must never overwrite `content_html` with garbage. Three ordered reasons, most
  specific first: `'paywall'` (reuses the existing `looks_paywalled()`/`PageData.blocked`
  signal), `'bot-challenge'` (new — `looks_like_bot_challenge()` against a marker list of
  Cloudflare/PerimeterX/DataDome/generic-CAPTCHA interstitial phrases, since those also
  return HTTP 200 and have no relationship to `looks_paywalled()`'s publisher-subscribe
  phrases), and `'too-thin'` (a blunt word-count floor, catching anything neither marker
  list names). A `fetch-error` reason (request-layer failure, or `fetch_page`'s own
  empty-`PageData`-on-failure contract) is logged by the pipeline caller, not this
  function, since it never gets HTML to assess in the first place.
- **Glue lives in `linklib/pipeline.py`** (`backfill_article_content(lib, article)`),
  matching `ingest_url`'s existing "fetch via extract.py, store via db.py" convention —
  fetches, runs the sanity check, and on success runs `extract_reader_html()` and
  `Library.set_article_content_html()`; on any failure, stores nothing and only logs.
  **Never destructive**: a failed re-fetch cannot touch `articles.content` or
  `articles.content_html` — the row is left exactly as it was.
- **`content_refetch_log`** (new table, shape mirrors `backup_log`): one row per attempt,
  success or failure, with a `reason` column so failures are groupable/countable by
  cause on the admin page. A re-run after a stop or a crash adds new rows rather than
  overwriting old ones, so a flaky source's full history stays visible.
- **Admin job**: `/admin/library/backfill-content` — same background-thread/
  `_JOB_STATE["content_backfill"]` pattern as re-enrich and Historical sweep (see those
  sections above), plus two things neither of those has:
  - **Stoppable**, not just crash-recoverable. A `stop_requested` flag on the job state,
    checked once per article (between fetches, never mid-fetch) — `POST
    .../backfill-content/stop` sets it, the loop notices on its next iteration and exits
    cleanly. Resuming (pressing Start again) picks up exactly where it left off, because
    stopping and crash-recovery share the same resumability mechanism: both rely on
    `Library.articles_needing_content_backfill()`'s default scope (`content_html=''`),
    which skips whatever a prior run — complete, stopped, or crashed — already succeeded
    on. `force=True` re-runs every row regardless, the same escape hatch the re-enrich
    job's own `force` option provides.
  - **Rate-limited.** Nothing else in this codebase throttles outbound crawling
    (Historical sweep's sitemap fetches and the queue scanner both hit sources
    back-to-back) — a fixed ~1.5s delay between fetches here is a deliberate new
    convention for this tool specifically, not a reuse of an existing one, since a
    full run means several thousand requests against sites Brian doesn't want to hammer.
  - The admin page's Limit field defaults to a small batch (25) so a first run can be
    verified before a full pass is even offered, and shows live success/failure counts
    plus a failure-reason breakdown (`Library.content_refetch_failure_counts()`, latest
    attempt per article only, so a since-fixed failure doesn't keep inflating the tally)
    and a recent-attempts log table.

### Dashboard clarity pass (`/admin/library/backfill-content`, 2026-08)

`/admin/library/backfill-content`'s seven summary tiles accumulated across six PRs
(the original backfill feature, the Medium fetch tier, and PRs #351-354's durability
sprint) with no single place stating how they actually relate. Brian correctly
inferred six of seven relationships by reading the copy, but one real question
surfaced — where do "Accepted as final" articles get counted once that count is
nonzero — and a separate rendering bug (the crash-banner reading above the live
in-progress box instead of below it) went unnoticed because both counts involved
were zero at the time.

**Phase 0 finding: the partition is real, not a bug — the page just never said so.**
Tracing every query behind the tiles (`Library.count_structured_content`,
`count_content_backfill_remaining`, `count_articles_needing_manual_review`,
`count_permanently_excluded_content`, `count_content_accepted`) confirms that for
articles with a saved URL (`url!=''`), these five buckets are a true,
mutually-exclusive partition: **Structured + Remaining + Needs review + Defunct
service + Accepted as final = every article with a URL.** Each non-Structured
bucket is keyed off a single fact — `content_html=''` AND the article's *latest*
`content_refetch_log` row is either absent (Remaining, never attempted), a failure
under `Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD` in a row (also Remaining),
a failure at/over that threshold (Needs review), `reason='defunct-service'`
(Defunct service), or `status='accepted'` (Accepted as final) — and a row's latest
attempt can only be one of those at a time, so no article can land in two buckets
at once. `accept_article_content()` never sets `content_html`; it only marks the
article's existing (short but real) plain-text `content` as good enough, which is
the direct answer to Brian's question: an accepted article is correctly excluded
from **both** Structured and Remaining, because it's its own fifth bucket — visible
in its own tile and its own "Accepted as final" table, never silently folded into
either. `Total articles` is a different, wider count (`Library.count()`, no URL
filter) that also includes articles with no saved URL at all, which never enter
this pipeline — the page now shows that split explicitly rather than leaving
"Total" looking like it should equal the sum of the other six.

**"Flagged at save" is deliberately not a sixth partition member.**
`articles.needs_content_check` is an orthogonal tag set by `ingest_url` at save
time (see the durability-audit-item-1 bullet above) and cleared only by
`set_article_content_html` — so it can sit on top of an article in Remaining,
Needs review, Defunct service, or Accepted as final at the same time as one of
those, but never on a Structured one. Adding it as a sixth bar segment would
double-count the same article past 100%; it's rendered as a separate "not one of
the five buckets above" overlay callout instead, with its own count and its own
short explanation of why it's excluded from the partition total.

**New: `Library.remaining_content_backfill_breakdown()`** (Phase 0 item 3, "how many
of Remaining were never attempted vs. attempted-and-failed"). Reuses the exact same
id set `count_content_backfill_remaining()` counts (factored out as
`_content_backfill_remaining_ids()`, so the count and the breakdown can never
disagree) and groups the attempted-and-failed subset by each article's latest
failure reason. Surfaced in the Remaining tile's tooltip.

**Tile layout**: a lone "Total articles" tile at the top, a proportional
segmented bar (plain HTML/CSS divs, no charting library — the live distribution is
heavily skewed toward Structured, and a pie chart makes the small categories
illegible) directly below showing the five-bucket split, the five tiles themselves
underneath (color-matched to their bar segment), and the Flagged-at-save overlay
box last, visually set apart with a dashed border. Every tile gets a short
`<details>`/`<summary>` tap-to-reveal tooltip (the sitewide disclosure convention,
BRAND.md's own choice for exactly this — see the FP&A Buddy Depth-tier note
above about why a positioned/absolutely-anchored tooltip was rejected there for
mobile-overflow risk; the same reasoning applies here, so this reuses `<details>`
instead of a custom popover) rather than a `title` attribute, since `title` doesn't
reliably tap-reveal on mobile. `Needs review` and `Accepted as final` link (via
their value, wrapped in an anchor) to their own detail table further down the page
— but only when that table actually has rows to show, since an anchor to an
absent/empty section would jump nowhere useful. `Flagged at save` and `Defunct
service` have no existing per-article list view anywhere in the admin (confirmed
by inventory before building), so neither tile links anywhere this pass — building
those list views was deliberately left out of scope (see the PR description) rather
than added as new list views in a display-only PR.

**Banner order bug, fixed**: `_job_run_banner`'s own docstring already says a
currently-running job should read "see the live status above" — but the route
rendered `_job_run_banner(...)` (which can show either the live "still in
progress" summary or the historical "started X ago, never finished" crash
message) **above** `<div id="poll-container">` (the actual live progress box with
its percentage bar), backwards from what that text assumes. A concurrently running
job showed its own "see live status above" line pointing at nothing above it — the
live box was below. Fixed by a plain reorder (`poll-container` first, then
`_job_run_banner`) — no logic change; `_job_run_banner`'s own conditions (checking
`_job_get(job_name)["running"]` before rendering the crash interpretation, per the
2026-08 wrap-up sprint item 3 fix already in place) are untouched.

### Reader tag editing (Phase 5c)

A deliberate, **tags-only** exception to the merge's "no inline management"
rule above. Delete/archive stay admin-only and unchanged; this is not a
precedent for bringing the rest back.

**Almost none of this is new backend.** The investigation that opened the
phase found both write paths already present and simply unreachable:

- `POST /library/{article_id}/tags` (JSON or form body, `_require_api`,
  writes an `archive_audit_log` row) survived Phase 5 **with zero callers** —
  the old Archive page's "Edit tags" button was removed, its endpoint wasn't.
  The reader pane's editor calls exactly this route rather than adding a
  second one.
- `POST /feed/save` **already accepted a `tags` field** and passed it to
  `ingest_url`. What was missing was a UI that sent anything useful: both
  save paths collected tags through a blocking `window.prompt()`, which is
  neither inline nor able to autocomplete. Its only change here is returning
  `{"ok", "id", "tags"}` instead of `{"ok"}`, so the reader can switch into
  its saved state (and know where to POST later edits) without a reload.

`/admin/library`'s two tag tools were confirmed to be **vocabulary-level, not
per-article**: "Tag cleanup" (`/admin/library/tags`) merges/renames/deletes a
tag across the whole library (`Library.rename_tag`/`delete_tag`), and
"Tagging style" (`/admin/library/tag-style`) learns Brian's tagging style to
feed the enrichment prompt. Neither edits one article's tags, so nothing here
duplicates them — they read and write the same `articles.tags_json` through
the same `Library` methods.

**Propagation matches what those Admin tools already do.**
`Library.update_tags` hard-replaces the list (stripped, de-duplicated,
sorted) and writes `tags_text`, so the `articles_au` FTS trigger reindexes
automatically — no manual reindex, same as rename/delete. Tags are also part
of the embedded document text (`linklib/embeddings.py::document_text`), and,
exactly like rename/delete, nothing re-embeds inline: `article_embeddings`'
content hash means the next `scripts/embed_backfill.py` run picks up the
changed text. Vector search is eventually consistent by design.

**Surfaces:**

- **Save-time, on a Feed row.** "+ Save" expands `.rr-row-tagform` in place
  (an input backed by the shared datalist, plus Save/Cancel) instead of
  opening a prompt. Tags stay optional — committing an empty input saves
  exactly as the old one-click action did. The form is `flex:0 0 100%` inside
  a now-`flex-wrap:wrap` `.rr-row` so it takes its own line; as a plain third
  flex child it became a third column and squeezed the row's title into a
  sliver (caught in live verification, guarded by a test).
- **Inline editor in the reader pane**, for already-saved articles only.
  Collapsed by default: the toolbar shows just a tag glyph with a count
  badge, modelled on Instapaper's own reader-toolbar tag icon. Clicking it
  opens `.rr-tag-panel` (sticky under the header, same treatment as the find
  bar) with removable chips and an autocomplete input. **Every add/remove
  writes through immediately** — no separate submit step. Only `#rr-tag-chips`
  and `#rr-tag-status` re-render, never the input, so focus survives a save
  and tags can be typed one after another. Chip removal passes an index, not
  the tag text, so an apostrophe in a tag name can't break out of the inline
  handler. `rrSyncRowTags` keeps the Archive-view row's own chips in step, so
  the two panes never disagree without a reload.
  The input carries a 220px width floor (`min(220px,100%)`), not the 110px it
  launched with: below that, chips on the same line could squeeze it too narrow
  to type in, and the floor makes flex-wrap drop it onto its own full-width line
  instead. The `min()` keeps the floor from overflowing a container narrower
  than itself — the reader pane is ~219px in the 3-pane mobile-landscape layout.
- **Autocomplete** is a native `<datalist id="rr-tag-vocab">` built from
  `Library.all_tags()` — the same vocabulary Tag cleanup curates, and the
  same `<datalist>` pattern the overhead-category admin inputs already use
  (no JS autocomplete widget, no new dependency). `all_tags()` is now read
  **unconditionally** in the `/read` route rather than only inside the
  `view == "saved"` branch, since a Feed item can be tagged at save time.

**`_resolve_reader_content` now resolves a url to an existing row.** A Feed
or Read Later row carries only a url, so an article already in the library
used to open as unsaved and offer "+ Save" again — which would have left the
inline editor unreachable for exactly the "any Feed item that's been saved"
case this phase is meant to cover. It now falls back to
`SELECT * FROM articles WHERE url=?` (url is the natural key) and surfaces
that row's `id` and `tags`. **A url-matched row skips only the plain-text
`content` cache**, deliberately: using that here would silently strip images
and links from a Feed item that currently reads with them intact, whereas an
explicit by-id open accepts it. Phase 5b's `content_html` is **not** skipped —
it's real structured HTML, so it's a strict upgrade over both the plain-text
cache and a live re-fetch, however the row was reached. (This is the merge
point between the two phases: 5c was written against the pre-5b premise that
a cached article's content is always flattened plain text, which 5b's backfill
makes false for any row it has processed.)

**One mobile bug, found only by live verification.** Focusing the tag input
let the browser scroll the document far enough to clip the panel's first chip
row — and the whole toolbar — off the top of a portrait viewport: the panel
opened somewhere the user couldn't fully see it, the same shape as the Phase 5
"tapping an article does nothing" bug. Fixed with `focus({preventScroll:true})`
plus an explicit `pane.scrollIntoView`, which moves only the document and
never the pane's own `scrollTop`, so a part-read article keeps its position.

**First-real-batch follow-up: the `fetch-error` reason had no detail behind it.** The
initial 25-article verification batch Brian ran turned up 12 failures, all labeled
just `fetch-error` — `extract.fetch_page()` catches its own request exception with a
bare `except Exception:` (no `as exc`), so `backfill_article_content()` genuinely had
nothing to log beyond the category. Fixed non-destructively: `PageData` gained a
`fetch_error` field (empty string by default — every existing caller of `fetch_page()`
already treats a failed fetch as "nothing usable" and ignores the new field, so this
changes nothing for them), populated by a new `extract._describe_fetch_error()` that
turns the caught exception into `"HTTP {status}"`, `"timeout"`, or a connection/SSL
error string. `content_refetch_log.detail` now carries that value for every `fetch-error`
row going forward. Also added `Library.content_refetch_failure_domains()` — the same
latest-attempt-per-article de-dupe `content_refetch_failure_counts()` uses, grouped by
URL host instead of reason — so a source-wide problem (one site systematically
blocking or throttling this tool) shows up as a visible cluster on the admin page
(a coral banner, only rendered once a host has ≥2 failures — a single failure is an
ordinary dead link, not a signal) rather than N identical-looking rows a human has to
notice share a domain by eye. The recent-attempts log table also now links each row's
title to its article URL and shows the `detail` text inline. **Not retroactive**: the
already-logged rows from that first batch still have an empty `detail` (the exception
was never captured for them) — the fix only changes what future attempts record.

**Second follow-up — fetch reliability: a real-request investigation found the
identifiable bot UA doesn't matter, and reshaped the plan around what actually does.**
The domain-clustering work above surfaced that 17 of 18 failures in a follow-up batch
traced to 4 domains — `bothsidesofthetable.com`, `continuations.com`, `medium.com`,
`pointsandfigures.com` — splitting into a suspected UA-blocking problem (403s) and
genuine link rot (404s, the "Board Effectiveness Tip" series). **Investigated with real
requests before building anything** (per the standing gate), via `railway ssh` +
one-off scripts, not assumed:
- **Browser User-Agent swap: confirmed it does NOT fix these four domains.** Tested one
  real failing URL per domain with both the old bot UA (`Mozilla/5.0 (compatible;
  linklib/1.0)`) and a standard Chrome UA. Three of four (`bothsidesofthetable.com`,
  `medium.com`, `pointsandfigures.com`) returned the byte-identical Cloudflare "Just a
  moment..." bot-management challenge regardless of which UA was sent — Cloudflare
  fingerprints the TLS handshake/connection behavior, not the UA string, so no UA swap
  alone gets past it (defeating that is explicitly out of scope — see
  `looks_like_bot_challenge()`, unchanged). `continuations.com` returned an identical
  404 with either UA, confirming its failures are genuine link rot, unrelated to
  blocking. **Kept as the new default anyway** (`extract._BROWSER_HEADERS`, now used by
  `fetch_page()` unconditionally rather than only on the auth-cookie path) — no
  downside, and it may still help against a site doing a naive UA-string check
  somewhere in the wider ~4,500-article corpus outside this one flagged batch — but
  the PR states plainly that it's confirmed *not* to solve this specific batch.
- **Wayback Machine fallback: the request/parsing logic is built and tested, but real
  end-to-end content retrieval was never verified at build time — archive.org's own
  Availability API was found to rate-limit (HTTP 429) broadly and unpredictably.**
  Three rounds of investigation, each ruling out a narrower theory: repeated 429s from
  Railway's production container even with exponential backoff (~60s waits); the exact
  same URL request from a completely different residential network (ruling out
  "Railway's shared egress IP is blocked"); a *never-before-queried* URL, including a
  totally unrelated well-known page (Wikipedia), from that same residential network,
  429'd immediately too (ruling out "one URL got hammered by testing"). Conclusion:
  archive.org's Availability API was rate-limiting for anyone, on any URL, at
  investigation time — an external condition unrelated to this codebase's approach,
  IP, or request pattern, that further testing that day wouldn't have resolved.
  **`linklib/wayback.py` is built defensively around exactly that finding**: every
  function (`find_snapshot`, `fetch_snapshot`) returns `None`/`""` on ANY failure — a
  429, a timeout, a malformed response, a network error — and never raises or retries;
  a retry loop would just add load against a service already observed to be
  struggling. See the module's own docstring for the full finding, restated at the
  point anyone would next touch this code.

Both fixes ship in the same PR, with the Wayback side explicitly building to be
*wired correctly whether or not archive.org happens to be healthy that day* rather
than a mechanism whose success depends on today's outage clearing:
- **`linklib/wayback.py`** (new module) — `find_snapshot(url)` queries the
  Availability API and returns a snapshot URL or `None`; `fetch_snapshot(snapshot_url)`
  fetches that snapshot's HTML or returns `""`. Both use `extract._BROWSER_HEADERS`
  and a short, deliberately conservative timeout (`_TIMEOUT = 8`) — this sits on both
  the backfill's per-article loop AND the Reader's *interactive* live-fetch path
  (`_resolve_reader_content`), and real Wayback latency is itself unverified pending
  archive.org's rate limiting clearing, so this stays conservative rather than
  generous until that's confirmed.
- **`extract._page_data_from_html(html)`** (new, factored out of `fetch_page()`) —
  builds a `PageData` (title/content/blocked/published) from already-fetched HTML,
  so a Wayback snapshot runs through the *exact same* title/content/paywall-detection
  logic a live fetch would, rather than a second, parallel implementation that could
  drift from it.
- **`pipeline.backfill_article_content()`** now tries Wayback as a last resort after
  ANY direct-fetch failure — deliberately not scoped to 404 only, since a stubborn
  403 a UA swap couldn't get past may still have a usable archived snapshot (the
  Internet Archive's own crawler generally isn't subject to the same per-request bot
  gate a generic scraper hits). The Wayback snapshot has to clear the identical
  `assess_extraction_quality()` bar a live page would (so an archived paywall preview
  still fails, exactly as a live one would). If Wayback comes up empty at any stage —
  no snapshot, the snapshot fails to fetch, or it fails the sanity check — the
  function logs and returns the **original** direct-fetch reason/detail, never a
  synthetic "wayback also failed" reason, so the admin failure-reason breakdown stays
  meaningful and comparable to a pre-Wayback run.
- **`content_refetch_log.source`** (new column, migration, default `'direct'`) —
  distinguishes a Wayback-sourced success from a direct-fetch success, since a
  Wayback-archived version can be stale or differ from what the live page shows
  today. `/admin/library/backfill-content` shows a "via Wayback" badge on the
  relevant log rows and a count of how many currently-structured articles are
  running on an archived copy (`Library.count_wayback_content()`, same latest-
  attempt-per-article de-dupe as the failure-count methods).
- **`_resolve_reader_content`** (the Reader's live-fetch path) gets the identical
  fallback for consistency — an unsaved Feed item hitting a dead/blocked link gets
  the same benefit a backfill run would. The resolved dict gains `content_via`
  (`'cache' | 'direct' | 'wayback'`), and the reader pane shows a coral notice
  ("The live page couldn't be reached, so this is a Wayback Machine archived
  copy...") whenever `content_via === 'wayback'`, so a reader isn't confused by
  content that might not match what's live today. **Added latency on this
  interactive path from the Wayback fallback is unverified**, for the same
  archive.org-rate-limiting reason the content-retrieval verification itself is —
  flagged explicitly rather than silently shipped as "confirmed fine."
- **Verification of "real content actually comes back" is deferred to Brian**, via
  the backfill tool's own existing small-batch-first convention
  (`/admin/library/backfill-content`, Limit field), once archive.org's rate limiting
  clears — not something this PR claims to have confirmed itself. This was an
  explicit, discussed trade-off (see CLAUDE.md's matching bullet for the full
  decision point), not an oversight.

**Third follow-up — the first real production batch surfaced a genuine logging gap
and a new failure class, both fixed.** Brian's first real batch after the fetch-
reliability PR (25 articles, 23 failures) showed zero "via Wayback" successes, even on
the Cloudflare-blocked domains the fallback was built for — and a new domain,
`feedproxy.google.com` (Google's discontinued FeedBurner proxy), clustering 3
failures. Diagnosed live via `railway ssh` one-off scripts rather than guessed at:
- **Wayback genuinely was being attempted for every failure — the code had no bug —
  but `content_refetch_log` couldn't say so**, because `_finish_backfill_via_wayback`
  only ever re-logged the *original* direct-fetch failure, discarding whatever
  Wayback itself returned (no snapshot? a fetch failure? a sanity-check miss?). A
  manual replay of the exact fallback logic against real failing URLs found archive.org
  was still unreachable — but this time as `ConnectionResetError`/`ConnectTimeout`,
  not the `HTTP 429` the original investigation found. **Different symptom, same
  underlying story** (archive.org unreliable from wherever this runs), and proof the
  plain `find_snapshot()`/`fetch_snapshot()` contract wasn't enough to diagnose from
  the log alone. Fixed with `find_snapshot_verbose()`/`fetch_snapshot_verbose()`
  (same never-raises guarantee, plus a short outcome string) — `find_snapshot()`/
  `fetch_snapshot()` stay as thin wrappers for callers (the Reader's live-fetch path)
  that only care whether a fallback is available, not why one isn't.
  `_finish_backfill_via_wayback` now appends that outcome to the logged `detail`
  (e.g. `"HTTP 403 (wayback: connection error: ...)"`), so the next time this question
  comes up the log already has the answer instead of needing another `railway ssh`
  round-trip.
- **`feedproxy.google.com` confirmed as a genuinely new failure class: a permanently
  discontinued service, not a recoverable block or a moved page.** A live request to
  one of the failing URLs returned Google's own `"Error 404 (Not Found)!!1"` page
  directly — the redirect-shim service itself is gone, not blocking or throttling.
  `linklib.pipeline._DEFUNCT_SERVICE_DOMAINS` is a small, hand-curated, comment-
  documented set (starting with just this one entry — each addition requires the
  same kind of live confirmation, not a hunch, since the consequence is permanent).
  `backfill_article_content()` checks a URL's host against this set *before* any
  fetch or Wayback attempt — both are skipped entirely for a match, since neither
  can ever succeed and a Wayback attempt would spend its own scarce rate-limit
  budget on something already known unrecoverable. Logged with the new
  `reason='defunct-service'`.
- **`articles_needing_content_backfill()`'s default (non-force) scope now excludes
  any article whose most recent `content_refetch_log` attempt was
  `defunct-service`** — otherwise every future default-scope batch would keep
  re-attempting a URL already confirmed permanently dead, burning both a fetch
  attempt and Wayback's rate-limit budget for a known outcome. `force=True` still
  reaches them (e.g. to re-check after a domain is removed from the defunct list).
  `count_content_backfill_remaining()` mirrors the same exclusion, so the admin
  page's "Remaining" stat reads as "what the next default run will actually
  attempt," not an inflated count.
- **A real bug caught before shipping, not after**: `done_count` (the "Structured"
  stat) used to be derived as `total - remaining`. Once `remaining` started
  excluding `defunct-service` articles too, that subtraction would have silently
  mis-attributed an excluded-but-never-structured article as "done." Fixed with a
  dedicated `Library.count_structured_content()` query (`content_html != ''`
  directly) instead of a derived subtraction — caught by a regression test
  written specifically for this failure mode, not discovered live.
  `Library.count_permanently_excluded_content()` (same latest-attempt-per-article
  pattern as the failure-count methods) surfaces the exclusion count itself, with
  an explanatory note on the admin page and a "Defunct service" pill in the
  failure-reason breakdown, so the exclusion is visible, not a silent scope change.

**Fourth follow-up — retry backoff (a distinct, non-permanent exclusion tier) + manual
URL correction, plus a known-domain-migration fetch tier tried before Wayback.**
Everything that wasn't `defunct-service` (a Cloudflare-blocked domain, genuine 404
link rot) stayed in the default-scope retry pool *forever* — every future batch
re-attempted it indefinitely, burning both time and Wayback's own scarce rate-limit
budget for an outcome that had already failed the same way several times running.
Two investigation gates ran before any of this was built, per Brian's explicit ask:
(1) confirmed no existing admin capability lets an article's stored `url` be edited
anywhere in the codebase — `tools`/`benchmarks`/`thought_leadership`/`communities` all
have this, `articles` never does, even `Library.upsert()` only uses `url` as a dedup
match key — so `apply_article_url_correction` is new, not a reuse; (2)
confirmed `content_refetch_log` is genuinely one-row-per-attempt (a plain `INSERT`, no
upsert) with no existing raw per-article attempt-count query, only latest-attempt-only
aggregates — so a new counting query was needed.

- **"Needs manual review" is a second, DELIBERATELY SEPARATE exclusion tier from
  `defunct-service` — not merged into it.** A Cloudflare block can lift; a 404 can be
  relinked; neither is "confirmed permanently dead" the way a discontinued service is.
  `Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD = 3` (Brian's own assumption, flagged
  plainly rather than silently picked): an article whose most recent attempt is a
  failure (reason != `defunct-service`) and has failed at least 3 times **since its
  last URL correction** (or ever, if never corrected) is pulled out of default-scope
  auto-retry. Deliberately **query-time-derived**, not written as its own
  `content_refetch_log` reason the way `defunct-service` is: unlike `defunct-service`
  (a fact knowable from a single attempt), "3rd failure in a row" is a judgment about
  accumulated history only computable by looking at several rows at once
  (`Library._manual_review_article_ids()`, a `WITH cutoffs ... attempts ... counts`
  CTE). This also means the admin page's needs-review list shows the article's REAL
  last failure reason/detail (e.g. `bot-challenge`), not a synthetic tag.
- **A URL correction resets the count WITHOUT deleting history.** `Library.
  apply_article_url_correction(article_id, new_url)` updates `articles.url` and writes
  one `url_correction_log` row (durable trace, per CLAUDE.md's one-off-fix rule) — it
  never touches `content_refetch_log` at all. `_manual_review_article_ids()`'s cutoff
  logic (only counting attempts strictly after the article's most recent
  `url_correction_log.created_at`) is what makes the reset happen: a corrected article's
  post-correction attempt count starts at zero, so it naturally re-qualifies for
  default-scope retry the moment the correction lands, while its full pre-correction
  failure history stays intact and queryable (non-destructive, same precedent as every
  other correction path in this codebase).
- **`articles_needing_content_backfill()`/`count_content_backfill_remaining()` now
  exclude needs-manual-review too**, alongside the pre-existing `defunct-service`
  exclusion — "Remaining" reads as "what the next default-scope run will actually
  attempt." `force=True` still reaches needs-manual-review articles (unlike
  `defunct-service`, this was never in question — it's the whole point of the tier not
  being permanent).
- **Export/import CSV round trip is the correction mechanism** (`/admin/library/
  backfill-content`'s new "Needs manual review" section): **Export**
  (`GET .../manual-review/export.csv`) — one row per needs-manual-review article,
  keyed by the stable `article_id` (the URL itself is what's changing, so it can't be
  the match key), with title/current URL/failure reason/attempt count/last-attempted
  for reference and a blank `corrected_url` column to fill in. **Import** is a
  preview-then-confirm pair, same convention as the pre-existing `/admin/
  overhead-spend/csv/preview`+`/commit` routes (`linklib/manual_review_csv.py` mirrors
  `linklib/overhead_csv.py`'s shape): `POST .../manual-review/import/preview` parses the
  re-uploaded CSV into a three-way split — `updates` (a well-formed, changed
  `corrected_url`), `skipped` (blank or identical — a normal no-op, not an error), and
  `errors` (malformed URL, or an `article_id` that isn't a recognized needs-manual-review
  row) — and renders a **nothing-written-yet preview** with the valid corrections
  round-tripped as hidden form fields (same state-carry mechanism as the overhead-CSV
  pair: this app has no server-side session store). `POST .../manual-review/import/
  commit` is the only route that actually calls `apply_article_url_correction`, one
  call per row, tolerant of a bad row rather than aborting the batch.
- **Known-domain-migration fetch tier** (`linklib/domain_migration.py`, tried by a new
  `linklib.pipeline._finish_backfill_after_direct_failure` orchestrator BEFORE the
  pre-existing Wayback fallback): `_DOMAIN_MIGRATIONS` is a small, hand-curated map
  (`pointsandfigures.com` → `jeffreycarter.substack.com`, `avc.com` → `avc.xyz`,
  `karenroterdavis.com` → `karenroterdavis.wordpress.com`), same
  discipline as `_DEFUNCT_SERVICE_DOMAINS` — each entry requires live confirmation, not
  a hunch (the `karenroterdavis.com` entry, 2026-08, is the one exception on record: added
  on Brian's reported fact after the building session's own network couldn't reach either
  domain to verify directly — see `linklib/pipeline.py`'s comment on that entry). A domain-count diagnostic run against the full production archive (not a test
  batch) before building found 20 `pointsandfigures.com` articles (2 with a logged
  failure — consistent with that domain's known Cloudflare block) and 55 `avc.com`
  articles (0 logged failures yet, since most hadn't been attempted in a batch since the
  fetch-reliability work landed — the domain move is still independently confirmed by
  direct observation, just not yet exercised against a real failing article here). For a
  matching domain with a title to search with, `domain_migration.find_migrated_url()`
  reuses the exact Exa integration FP&A Buddy's `retrieve_exa()` already uses (same
  endpoint, same `EXA_API_KEY`/`exa_enabled` kill switch, a plain `requests.post`),
  restricted via `includeDomains` to just the destination domain, searching for the
  article's stored TITLE — a hit is only accepted if its own title plausibly matches
  (word-overlap ratio, not exact string match, since a migrated post's title can be
  lightly reformatted by the new platform). The candidate still has to clear
  `extract.assess_extraction_quality()`, the identical bar a direct fetch or Wayback
  snapshot has to clear — this module only finds a candidate URL, it never decides the
  content is good. **This is explicitly NOT a general search fallback** — it's only
  trusted because the destination domain is already confirmed as the legitimate
  continuation of that specific source, not because Exa found *something* plausible.
- **Preserves the "exactly one `content_refetch_log` row per
  `backfill_article_content()` call" invariant** — confirmed true before building, since
  the migration tier and the retry-cap attempt-counting design would otherwise conflict.
  `_finish_backfill_after_direct_failure` tries the migration tier first; on success it
  logs its own single row (`source='migration'`) and returns immediately without ever
  calling `_finish_backfill_via_wayback`; on any migration-tier miss (no domain match, no
  title, no Exa hit, or the candidate fails its own sanity check) it logs NOTHING and
  simply delegates to the pre-existing `_finish_backfill_via_wayback`, which does its own
  single log — never a double-log, whichever tier ultimately succeeds or all three fail.
- **Admin page**: the 3-tile stats grid became 5 tiles (Total / Structured / Remaining /
  Needs review / Defunct service) — CSS changed from a fixed `repeat(3,1fr)` to
  `repeat(auto-fit,minmax(130px,1fr))`, per the standing CSS-Grid-blowout lesson (Phase
  P) rather than hardcoding a new fixed column count. A "via Migration" badge (mirroring
  the existing "via Wayback" badge) appears on a migration-sourced success row in the
  attempts log, plus a `Library.count_migration_content()` note (mirrors
  `count_wayback_content()`) when nonzero.
- **Out of scope, deliberately**: no automated search integration replaces the manual
  CSV workflow for needs-manual-review articles generally — only the specific,
  pre-confirmed domain migrations above get an automated tier; whether to broaden that is
  a separate future decision. `defunct-service` logic and its permanent exclusion are
  completely untouched by this change — regression-covered by the pre-existing
  `tests/test_fetch_reliability.py` suite passing unmodified.

### "Accept as final" manual-review override (durability audit item 4)

The needs-manual-review tier had a false-positive dead end: an article with real-but-short
content (under `extract._MIN_CONTENT_WORDS`) fails `assess_extraction_quality()` identically
forever, and a URL correction can't help since the URL is already correct — the only escape
was a direct DB edit. `Library.accept_article_content(article_id)` writes a new
`content_refetch_log` row with a third `status` value, `'accepted'` (see the table row
above for how it composes with `_manual_review_article_ids()` and the backfill-scope
exclusions for free, via the same latest-attempt-per-article idiom every other query in
this table already uses). The prior failure `reason` is copied onto the accepted row for
display and so `unaccept_article_content()` can restore it without a second query.
`POST /admin/library/backfill-content/{article_id}/accept` and `.../unaccept` are the two
routes, both **per-article only — deliberately no bulk/select-all form**, since this is a
one-at-a-time override for a specific false positive, not a backfill mechanism. The admin
page gained a new "Accepted as final" section (mirrors "Needs manual review") with an Undo
button per row, and a new stats tile. Reversal is fully additive — `unaccept_article_content`
appends a new `'failure'` row rather than deleting the `'accepted'` one, so the fact that an
article was accepted and later reversed stays visible in the log, same non-destructive
precedent as everywhere else in this table.

### "Snapshot on Wayback" guidance link (2026-08 wrap-up sprint item 4)

A link and a sentence, nothing more — no Save Page Now API integration, no
automation, no tracking of whether a snapshot was ever taken. Brian proved out
a manual workaround in production: for an article whose live page loads fine
in a browser but is bot-blocked to this app's own fetcher, manually opening
`https://web.archive.org/save/<url>` creates a fresh archive.org snapshot,
since archive.org's own crawler isn't subject to the same Cloudflare
fingerprint block that stops ours — the next Wayback-tier attempt (or a
manual re-check) can then retrieve it. Each "Needs manual review" row with a
known current URL now shows a "Snapshot on Wayback ↗" link pointing at that
exact Save Page Now URL, opening in a new tab (`webapp/app.py`'s
`_review_row`), plus one guidance sentence in the section's existing
explainer text. Nothing else changed — this documents an existing manual
trick in the UI so it isn't tribal knowledge, it doesn't make the trick
smarter.

### Bare-domain `www.` retry (2026-08)

`scripts/diagnose_reader_backfill_failures.py` confirmed against production that
`codingvc.com` refuses the connection at its bare domain while `www.codingvc.com`
serves the identical page fine — a real fetcher gap (6 affected saved articles: ids
182, 187, 190, 457, 993, 1476), not a URL/data-correction issue, since nothing about
the stored URL is wrong.

Fixed with a single-point change in `extract.fetch_page()` — confirmed by inventory
before building that it's the sole low-level HTTP entry point every caller
(`pipeline.py`, `enrich.py`, `queue.py`, `authcheck.py`, `webapp/app.py`) goes
through, so there was no risk of duplicating the retry across call sites. A
`requests.exceptions.ConnectionError` on the bare-domain attempt (DNS failure,
connection refused, unreachable) now triggers exactly one retry against the same
URL with a `www.` host prepended (`extract._with_www`), only when the URL doesn't
already have one. **Deliberately scoped to connection-level failures only** — an
`HTTPError` (403, 404, ...) is never retried this way, confirmed the same day that
`inc.com` 403s identically on both the bare and www hosts, so that's a different
problem this fix doesn't address. Sits underneath every existing fallback tier in
`linklib/pipeline.py` (manual-review exclusion, domain-migration, Medium-platform,
Wayback) — a bare-domain connection failure this retry resolves never reaches any
of them, since `fetch_page()` itself now returns a successful `PageData`.

### Article purge flow (durability follow-up, 2026-08)

A permanent-deletion escape hatch for the narrow set of articles with genuinely nothing
useful saved — `Library.articles_eligible_for_purge()` returns articles whose plain-text
`content` is under `extract._MIN_CONTENT_WORDS` AND whose `content_html` was never
backfilled either, tagged with `content_check_reason` (durability audit item 1) when set.
**Deliberately not the same set as the Remaining tile** on `/admin/library/backfill-content`
— Remaining is every article without `content_html` yet, the vast majority of which have
perfectly good plain-text content just waiting on a structure-backfill pass; purge
candidates are the much narrower "nothing was ever really saved for this URL" set.

Mirrors the manual-review corrected-URL CSV round trip exactly (`linklib/purge_csv.py`,
same shape/discipline as `linklib/manual_review_csv.py`): `GET .../purge/export.csv`
exports every current candidate with a blank `confirm_purge` column;
`POST .../purge/import/preview` re-validates each `article_id` against a FRESH read of
`articles_eligible_for_purge()` (never the CSV's own stale columns — same
"whatever's in the database now is authoritative" rule) and renders a preview table
(title/URL/word count) with nothing written yet; `POST .../purge/import/commit` executes
it. Recognized `confirm_purge` markers: `yes`/`y`/`1`/`x`/`purge`/`confirm`; blank or
`no`/`n`/`0` is a normal skip; anything else unrecognized is an ERROR (typo protection —
never silently skipped, same "no silent caps" standard as everywhere else in this file).

**Two independent guards before anything is deleted**, on top of the preview-then-confirm
step itself: (1) `purge_csv.MAX_PURGE_PER_RUN` (50) fails the WHOLE import if the confirmed
count exceeds it — enforced again, defense-in-depth, on the raw POST at commit time, so a
hand-crafted request can't bypass the CSV-parsing check; (2) the preview screen renders a
required "type N to confirm" text field, and the commit route rejects the request outright
if the typed value doesn't exactly match the number of `article_id` rows posted — a second,
independent check that the admin actually looked at how many rows they were about to delete,
not just that the file happened to parse.

**The nightly/weekly backup is the ultimate net, but deliberately not the first one**: the
commit route calls `backup.backup_now()` (a real, unconditional snapshot — NOT the
debounced `maybe_backup()` every other bulk-delete flow in this codebase uses) immediately
before the delete loop, when backups are configured at all; if that snapshot attempt fails,
**the entire purge is aborted** rather than proceeding without a fresh net. Every article is
then re-validated against a fresh candidate set immediately before its own delete (TOCTOU
guard — an article backfilled with real content between preview and commit is skipped, not
deleted) and removed via the new `Library.purge_article()`, which snapshots title/url/word
count immediately before calling `delete_article()`, then reads the row back and raises if
it's somehow still present — write-then-read-back, per CLAUDE.md's one-off-admin-fix
discipline, applied here as a standing check since every call is genuinely destructive.
Each deletion is logged to `archive_audit_log` (`action='delete'`, `detail='purge: N words,
<url>'`) via the existing `_log_archive_audit` helper, same as every other admin delete path.

**`Library.delete_article()` itself was extended** (a general fix benefiting all four
existing callers — dedupe removal, review-removals, the member Reader's own delete, and now
purge — not something purge-specific) to also remove `content_refetch_log` and
`url_correction_log` rows for the deleted article; those have no meaning once the article
is gone. **Deliberately NOT deleted**: `enrichment_cost` (a real-money spend ledger — the
Claude API call cost actual dollars whether or not the article survives) and
`archive_audit_log` itself (the historical "what happened" record, which gets a new row
for the delete FIRST, via the caller, before `delete_article()` even runs — same
non-destructive precedent as `tool_audit_log`/`community_audit_log` outliving a deleted
tool/community elsewhere in this codebase). `FTS` rows are removed automatically by the
existing `articles_ad` trigger; the embedding/vector rows (`article_embeddings`,
`articles_vec`) were already cleaned up by the pre-existing `delete_article()`.

### Medium-platform Exa fetch tier (2026-08)

A third fetch tier for the same backfill pipeline, following three read-only
diagnostic rounds (`scripts/medium_platform_scale_check.py`) that measured real
scale (147 Medium-platform articles in the archive) and Exa-recovery feasibility
before any of this shipped — see CLAUDE.md's Medium-platform investigation
bullets for that history.

- **Why a dedicated tier, not the general domain-migration one:** medium.com and
  its custom-domain lookalikes (`bothsidesofthetable.com`) return an identical
  Cloudflare block to every fetch attempt this tool makes — no user-agent swap
  gets past it (confirmed against real 403s during the fetch-reliability work
  above) — so unlike a `_DOMAIN_MIGRATIONS` entry, there's no single destination
  domain to redirect to. Medium articles resolve to many different real hosts
  (a publication's own site, a syndication partner, a Substack) or stay on
  Medium itself under a different author/URL, so the tier searches broadly via
  Exa (no `includeDomains` restriction, unlike the domain-migration tier's
  single confirmed target) and validates whatever it finds.
- **Host recognition is suffix-based** (`linklib/medium_platform.py`,
  `is_medium_platform_host()`): `medium.com` itself, any `*.medium.com`
  subdomain (an author's own `<handle>.medium.com`, or the `link.medium.com`
  short-link redirector — both covered by the same suffix check, no separate
  entries needed), and a short, hand-curated `_MEDIUM_CUSTOM_DOMAINS` set
  (currently just `bothsidesofthetable.com`, confirmed live via its Medium
  post-ID-hash URL slug format). The second diagnostic round specifically found
  author subdomains slipping through an earlier exact-match carve-out to a
  guaranteed-403 live re-fetch — the suffix check exists to close that gap.
- **Candidate search and validation** (`find_medium_candidate()`, reusing
  `linklib.domain_migration._titles_match()` — never reimplemented): the same
  Exa Search API integration the domain-migration tier already uses (same
  endpoint, `EXA_API_KEY`/`exa_enabled` kill switch, plain `requests.post`),
  querying by title (+ author, if the article has one). Every candidate is
  validated by title-match against the query results in order; the first that
  clears the threshold wins.
- **A same-domain carve-out splits validation into two paths**
  (`pipeline._try_medium_platform()`, mirroring the exact split
  `medium_platform_scale_check.py`'s diagnostic found necessary): if the Exa
  candidate resolves to some OTHER host, it's live re-fetched and run through
  the identical `extract_reader_html()` + `assess_extraction_quality()` gate a
  direct fetch or a domain-migration candidate has to clear. If the candidate
  resolves BACK onto a Medium-platform host itself, a live re-fetch would just
  re-hit the same block that failed the original URL — not a real test of the
  candidate — so it's validated against Exa's own already-returned
  `contents.text` instead: a word-count floor matching
  `extract._MIN_CONTENT_WORDS`, then converted to Reader HTML (below). The
  diagnostic's revision #3 found 3-4 of an early sample's "unrecovered"
  results were exactly this circular case, not genuinely bad candidates.
- **`extract.paragraphs_html_from_text()`** turns Exa's plain-text Medium
  extract into the same kind of structural HTML `extract_reader_html()`
  produces from real markup — so a Medium-sourced article reads identically to
  any other source in the Reader. This follows a standing design principle
  established for this tier: **the Reader delivers a consistent house reading
  experience regardless of publisher — a source is an input to normalize, not
  a style to preserve.** Rules (resolved with Brian): markdown headings become
  `<h2>`/`<h3>` (capped — Exa's plain text doesn't reliably distinguish deeper
  levels; an unmarked short Title-Case line with no trailing punctuation is
  also treated as a subheading, since Medium's own in-article section headers
  arrive this way with no markdown syntax surviving the conversion), emphasis
  markers (`*`/`**`/`_`/`__`) are stripped rather than converted to
  `<em>`/`<strong>` (goal is the cleanest possible read, not markdown-parity
  rendering), markdown links are reduced to their visible text only (the URL
  adds nothing in a plain-text extract), paragraphs split on blank lines. A
  leading run of known Medium navigation chrome ("Open in app", "Sign up",
  "Get app", a lone "Follow"/"Listen"/"Share", the "N min read" byline) is
  stripped before the real body — deliberately **conservative**: it only ever
  eats from the top and stops for good at the first line that isn't a known
  chrome phrase, so a real paragraph that happens to echo one of those words
  later in the piece (e.g. a sentence about signing up for a newsletter) can
  never be dropped. The "N min read" byline pattern is the one exception,
  stripped wherever it lands (not just while still in the leading run) since
  it's an unambiguous regex match no real sentence is ever literally equal to.
  Out of scope, deliberately: publisher boilerplate on the *live-refetch* path
  (e.g. a TechCrunch promo banner) — a future Reader-quality pass, not this
  tier's job.
- **Tried before Wayback, after domain-migration** in
  `_finish_backfill_after_direct_failure`: domain-migration first (unrelated
  host set, checked first purely by convention), then the Medium-platform tier
  if the URL's host is recognized, then Wayback as the last resort — Wayback is
  currently unreliable due to archive.org-side rate-limiting (see
  `linklib.wayback`'s module docstring), while this tier's hit rate on real
  diagnostic data was strong enough to go first. Same "exactly one
  `content_refetch_log` row per `backfill_article_content()` call" invariant as
  the other two tiers: a Medium-tier success logs its own row
  (`source='medium-search'`, `detail`=the candidate URL actually used) and
  returns immediately; a miss logs nothing and falls through to Wayback, which
  does its own single log.
- **Known gap, not fixed here**: a Medium-tier candidate on a different host
  often duplicates the original article's title/byline inline in its own body
  (a syndication republish convention) — `paragraphs_html_from_text()` doesn't
  detect or dedupe that against the article's own stored title, so a synced
  article can occasionally show its title twice. Flagged rather than
  silently accepted; not a correctness bug (the content itself is real), just
  a minor cosmetic duplication left for a future pass if it turns out to
  matter in practice.
- **Admin page**: a `Library.count_medium_search_content()` note (mirrors
  `count_wayback_content()`/`count_migration_content()`) and a "via Medium
  search" badge (mirroring the existing "via Wayback"/"via Migration" badges)
  on a Medium-tier success row in the attempts log.
- **`host_suffixes` scoping** (`Library.articles_needing_content_backfill()`,
  new optional parameter, plus a "Scope to host(s)" text input on
  `/admin/library/backfill-content`): narrows a backfill run to articles whose
  URL host matches one of the given suffixes and, for those matching hosts
  ONLY, bypasses the needs-manual-review exclusion — most of the point of
  scoping a run to a specific host is re-attempting exactly the articles that
  got stuck in manual review because a new fetch tier (like this one) didn't
  exist yet when they were last tried. The defunct-service exclusion still
  applies even when host-scoped — a confirmed-dead host can't be un-dead by
  narrowing the run to it. Not Medium-specific — any host suffix works — but
  this is the change that motivated building it.

### Medium-platform tier follow-up — fetch-by-URL before search-by-title, plus a non-Medium blocked host (2026-08 wrap-up sprint item 1)

Production evidence from the manual-review corrected-URL workflow above
surfaced a real gap in the Medium-platform tier as originally built: ~20
stuck articles now have an exact, human-confirmed URL (imported via the
`corrected_url` CSV round trip), and the tier never used it — search-by-title
was the only mode, so a generic title could find the wrong candidate or none
at all even when the correct URL was already known.

- **`linklib.medium_platform.fetch_content_by_url()`** calls Exa's `/contents`
  endpoint (not `/search`) for the article's own current (post-correction) URL.
  There's no candidate to disambiguate the way a title search has, so a
  substantive result is accepted on the word-count floor
  (`extract._MIN_CONTENT_WORDS`) alone — no title-match check. Tried first in
  `pipeline._try_medium_platform()`; a miss or a too-thin result falls through
  to the pre-existing search-by-title flow unchanged. Logged with
  `source='medium-fetch'`, distinguishable from a search-by-title success
  (`source='medium-search'`) in `content_refetch_log`, the admin attempts-log
  badge ("via Medium fetch" vs. "via Medium search"), and a matching
  `Library.count_medium_fetch_content()` stat — see `content_refetch_log`'s
  table row above for the updated `source` enum.
- **Fetch-by-URL needs no title** — unlike search-by-title, which is skipped
  outright for a title-less article, the fetch-by-URL attempt runs regardless
  (there's nothing to search for, only a URL to fetch).
- **`is_recognized_blocked_host()`, not `is_medium_platform_host()`, is the
  actual gate** for this tier (both the fetch-by-URL/search-by-title entry
  point and the same-domain carve-out inside search-by-title validation) —
  the same production evidence surfaced `shockwaveinnovations.com`, a host
  Cloudflare-blocked exactly like the Medium-platform hosts but **not**
  actually Medium underneath. Rather than mislabel it into
  `_MEDIUM_CUSTOM_DOMAINS` (a factual claim about being Medium's own
  publishing platform), it lives in a separate, honestly-named
  `_OTHER_BLOCKED_HOSTS` set; `is_recognized_blocked_host()` is the union of
  the two. `is_medium_platform_host()` itself is unchanged and still means
  exactly what it always meant — nothing that reads "is this really Medium"
  had its meaning altered.

**Live-proof follow-up — the logged detail couldn't distinguish "the tier ran
and missed" from "the tier was never reached."** A pre-merge live-proof round
(two production articles, both recognized blocked hosts) came back with
`content_refetch_log` rows that looked byte-for-byte like the pre-fetch-by-URL
flow: `source='direct'`, the original `direct_reason`, and a `detail` only
ever showing what Wayback itself did (`"HTTP 403 (wayback: no snapshot
archived)"`) — there was no way to confirm from the log alone whether
`_try_medium_platform()` had actually executed, since a genuine miss and a
skipped tier produce an identical row. Code-tracing confirmed both articles'
hosts do match `is_recognized_blocked_host()` (a plain `medium.com` netloc,
and `shockwaveinnovations.com` after the `www.` strip), so the tier was in
fact reached both times — but that was an inference from reading the code,
not something the log itself could show. Fixed before merge, not deferred:
`_try_medium_platform()` now returns a 5th element, `note` — a short,
ALWAYS-populated diagnostic trace of exactly what it attempted and why
(`"fetch-by-url: too-thin (12 words); search-by-title: no candidate"`, etc.),
success or failure. `_finish_backfill_after_direct_failure()` accumulates a
`tier_notes` list from every tier it actually reaches (migration too, same
gap, same fix — not Medium-specific plumbing) and passes it to
`_finish_backfill_via_wayback()` as `tier_trace`, which appends it to the
final logged `detail` in square brackets regardless of Wayback's own
outcome. An article whose host isn't recognized by any tier logs identically
to before this fix — the trace is additive, appearing only when a tier
genuinely ran. `scripts/trace_medium_tier.py` (new, "Reusable diagnostic" in
the scripts registry) gives a second, independent confirmation path: it
calls `_try_medium_platform()` directly (never the write path) against a
specific stuck article ID, printing its existing log history alongside a
live re-trace, plus a Wayback-snapshot content inspector (raw HTML length,
extracted word count, `assess_extraction_quality()`'s verdict, a text
preview) for the "is the stored snapshot an empty client-side-rendered
shell" hypothesis raised during the same live-proof round.

### Admin nav restructure, Library page cleanup, and page-width fixes (Phase 6)

Three related but distinct pieces, shipped as one PR because the second and
third are coupled (Library's own management entry point moves as part of
the nav restructure).

**Page-width/centering.** `/about`'s bio column had a real bug: bare inline
`style="max-width:760px;"` instead of `.tool-prose`, so it never inherited
the sitewide centering `.page{margin:0 auto}` rule provides — fixed by
switching to `.tool-prose`. A live pixel-measurement check (`getBoundingClientRect`/
`getComputedStyle` at 1920px and 1440px) of `/tools` and `/admin` — both
flagged as possibly having the same bug — found they were already correctly
centered (symmetric margins at 1920px, full-width with 0 margin at 1440px);
no fix was needed there. The homepage and `/thought-leadership` stay
`.page-full` (1900px), unchanged by design. A fresh sweep for the same
bare-inline-style anti-pattern elsewhere in `webapp/app.py` found no other
occurrence — `/read`/`/read/{article_id}` are a documented custom-exception
layout (see `_page_index_snapshot()`), not an instance of the bug.

Two different "this is admin-only" visual treatments had drifted apart:
`/tools`' 5th "Library" tile (white background, `var(--seafoam)` border —
the original Phase 3 spec) vs. the homepage's "Reader access" box (filled
`var(--seafoam)` background, `var(--seafoam-deep)` border — the later
design file's own treatment, never reconciled back onto `/tools`).
Standardized on the homepage's filled treatment via two new shared
constants, `_ADMIN_ONLY_BG`/`_ADMIN_ONLY_BORDER`, both call sites now pull
from — `_toolbox_tile()` grew a `background` kwarg to make this possible
without duplicating its markup.

**Admin nav restructure.** `/admin`'s right column (`_ADMIN_GROUPS`) is
reordered to mirror the public site's own nav order: Thought Leadership
first (unchanged content, just repositioned), then CFO Toolbox — now an
expandable parent, same native `<details>`/`<summary>` disclosure every
other admin group already uses — containing Software / Toolbox categories /
Resources / Communities / Sail, Don't Row settings (the
existing 5 `_TOOLBOX_TOOLS`, kept as-is rather than trimmed to match a
shorter prose description) plus two new nested items: a recursive **FP&A
Buddy** sub-group (`_FPA_BUDDY_TOOLS`, its own expand/collapse and its own
aggregate badge — `_group_html()` calls itself, no new mechanism) and a
plain **Library** link (what used to be a standalone top-level card,
relocated). Then "Brand, voice, and content" (renamed from "Brand &
Voice"), holding exactly Site copy / Verbal identity / Email templates /
Brand standards in that order. **System** (7 tools) stays a fourth,
unchanged group — it has no public-nav counterpart, so it remains its own
catch-all rather than being folded into one of the three above.

The parent "CFO Toolbox" badge aggregates pending-task counts across its
*entire* subtree — all 5 direct tools plus all 4 FP&A Buddy tools plus all
8 Library tools (19 hrefs total via `badge_hrefs`) — not just its 5 direct
children, so the header reflects the true nested count. `_ADMIN_GROUPS`
itself only stores the 5 direct `_TOOLBOX_TOOLS` tuples for "CFO Toolbox";
the FP&A Buddy sub-group and the Library link are built and appended inside
`admin_page()` at render time, not present in the static list — code/tests
that need every admin-linked route should read `_ADMIN_SECTIONS` (a flat
view that folds `_LIBRARY_TOOLS` + `_FPA_BUDDY_TOOLS` + every
`_ADMIN_GROUPS` item together) rather than iterating `_ADMIN_GROUPS`
directly, or they'll silently miss the two nested groups.
`_group_html()` was generalized to accept either `(href,title,desc)` tuples
(rendered as cards) or pre-rendered HTML strings (for nesting a sub-group
or a plain link card), plus a `nested` flag for the lighter visual
treatment (smaller padding/font, `var(--bg)` fill) a sub-group needs so it
doesn't compete visually with its parent.

**Library page cleanup (`/admin/library`).** "Open Reader" is no longer a
listed tool here — it's reachable via the admin nav restructure above
(`/admin` → CFO Toolbox → Library, which lands back on this page) and via a
new, prominent, non-numbered callout at the very top of `/admin/library`
itself linking straight to `/read` — added because neither of the other two
paths is locally obvious enough on the one page that used to list Open
Reader as tool #1. `_LIBRARY_TOOLS` is now 8 entries (down from 10):
"Open Reader" is gone (moved to the callout) and "Historical sweep" is gone
(merged into Archive Queue, next paragraph). Two descriptions were rewritten
for clarity: **Archive backup**'s now explicitly says automated backups
already run daily via the GitHub Action (Phase O) and that this manual
tool is for an on-demand snapshot right before something risky, not a
day-to-day safety net; **Reader content backfill**'s now explicitly
differentiates itself from Archive Queue's Historical sweep panel
("re-processes articles you've *already* saved for better structure; it
never finds new ones").

**Historical Sweep + Archive Queue merge.** These read as two pages doing
one job (a one-time sitemap producer and the queue that consumes it) once
`_content_flow_diagram()` existed to show that relationship explicitly —
so `GET /admin/library/backfill` (Historical sweep's old standalone page)
is retired as a page and now 301-redirects to `/admin/library/queue`,
which embeds the entire sweep form/report/poller as a collapsible
`<details class="admin-group">` panel at the top of the page (open by
default only when there's something to show — running, error, or a prior
report). The underlying mechanism is untouched: `POST
/admin/library/backfill/start` and `GET /admin/library/backfill/status`
keep their exact paths and behavior, both redirect targets on the POST
route were repointed from `/admin/library/backfill` to
`/admin/library/queue`, and the background-thread `_job_get("backfill")`/
`_job_set("backfill", ...)` job-state pattern is unchanged. One real bug
surfaced and fixed during the merge: the sweep panel's own poll script was
calling `fetch('/admin/backfill/status')` — missing the `/library` path
segment, silently 404ing forever, so the UI never live-updated without a
full page reload. The redirect (not a hard removal) is deliberate: this was
a real bookmarked admin tool, not a public URL nobody had saved.

The remaining 7 `/admin/library` tools are grouped into three labeled
sections:
- **Archive additions & backup** — bringing new content in, plus protecting
  what's already there: Archive Queue (which now contains the merged
  Historical sweep panel) and the ongoing feed-scan button on the same
  page, plus Archive backup.
- **Existing archive management** — working with what's already saved:
  Reader content backfill, Content de-dupe, Remove content.
- **Tagging** — how tags get created, taught, and kept tidy: Tag cleanup,
  Tagging style, Enrich archive.

Two placements were genuinely ambiguous and decided by judgment rather than
silently. **Archive backup** first shipped as its own standalone,
headingless card above the three sections — it applies to the whole
archive, not just "existing" content, so folding it into "Existing archive
management" would misrepresent its scope — but a live-preview follow-up
flagged that as visually odd once every other tool had a labeled section
above it. Folded into "Archive additions" instead (renamed "Archive
additions & backup" so the heading still says what's inside it): it still
doesn't fit "existing" content, but reads fine alongside "bringing new
content in" as one shared idea — both are about keeping the archive intact
and current, not a single curation pass over content that's already there.
**Enrich archive** stayed in Tagging rather than moving to "Existing
archive management" — it drafts both summaries and tags, but tags are the
vocabulary the other two Tagging tools curate and teach, and that
relationship felt like the stronger fit.

**Page layout — a 2x2 grid, added in the same live-preview follow-up.**
The page's four content blocks (Open Reader + the flow diagram; "Saving
articles from anywhere"; "Archive additions & backup"; "Existing archive
management" + "Tagging") originally rendered as one long single column.
Reflowed into a `.lib-two-col` CSS grid (`grid-template-columns:1fr 1fr`,
plain DOM-order auto-placement — no explicit `grid-column`/`grid-row`
needed since 4 items in a 2-column grid naturally fill row-by-row) pairing
Open Reader/flow with "Saving articles from anywhere" on top, and "Archive
additions & backup" with "Existing archive management"/"Tagging" below.
Collapses to a single column below 900px, same breakpoint convention as
every other responsive section on the site — DOM order (top-left,
top-right, bottom-left, bottom-right) is also the sensible mobile reading
order, so no separate mobile-order override was needed (unlike the
homepage's `.home-grid`, which needed one because its desktop and mobile
orders genuinely diverge).

**Superseded: the two-up top row is gone.** An intermediate round had the
seafoam Open Reader callout sharing a `.lib-top-row` grid with the flow
diagram, where it rendered 22px taller than the diagram beside it — the
diagram card's own `margin:0 0 22px` (correct on the Archive Queue page, where
it sits above body copy) becoming phantom space inside a stretch-aligned grid,
so the row sized to card-height plus margin while the callout's `height:100%`
filled all of it. That was fixed by matching the margin on the row's last cell,
and then the row itself was removed in the restructure below, which retires the
whole class of problem: nothing shares a row with the diagram any more.

### /admin/library page restructure: header action, full-width flow, four quadrants

This supersedes the layout described above rather than extending it.

**Open Reader is a header action, not a box.** The seafoam callout card is
removed. In its place, a ghost button beside the `<h1>`, using the same
flex + `.btn` header pattern `/admin/tools/software` and
`/admin/tools/resources` already use for their "+ Add" links.

*Colour:* stock `.btn.btn-ghost` with **no colour override** — navy border,
navy text, transparent fill, `--navy-wash` hover, 10px radius, which is exactly
BRAND.md §"Buttons"' secondary style. An intermediate round of this build
tinted it `--seafoam-deep`; that was wrong (BRAND.md: "Buttons navy or ghost" /
"Make a seafoam or coral button") and has been reverted. Note `--accent-light`,
which `.btn-ghost:hover` uses, is a legacy alias for the same hex as
`--navy-wash`, so the stock hover is already the navy wash.

**The flow diagram runs full width**, on its own, between the intro paragraph
and the grid.

**The four quadrants are collapsible, closed by default.** Each is a native
`<details>`/`<summary>` carrying the same `.disclosure-caret` span the
bookmarklet and Share-Sheet accordions on this page already use, so landing on
the page shows a tidy 2x2 of four headers (measured: the whole grid is 98px
tall closed, vs 1362px with all four open). No JS, no persistence between
loads, and each opens independently. `display:flex` on the summary is what
suppresses the browser's own marker so the caret isn't doubled — the existing
convention here, not a new trick. The title stays an `<h2>` inside the summary
so heading semantics survive the wrapping. Nesting is native and safe: the New
content quadrant contains the two capture-path `<details>`, and
`.disclosure-caret`'s rotate rule is scoped `details[open] > summary`, so an
inner accordion can never rotate the outer quadrant's caret (verified live).

**Two independent flowing columns, NOT a row-coupled grid.** `.lib-cols` is a
flex row of two `.lib-col` flex columns: left holds New content then Tag
management, right holds Existing archive management then Archive additions &
backup. Each column lays out on its own, so **expanding a quadrant in one
column never moves anything in the other** (verified live in both directions:
opening New content leaves both right-column headings at their exact Y, and
opening Existing archive management leaves both left-column headings put).

*This reverses an earlier round of this build*, which used
`grid-template-areas` with explicit rows to guarantee that row-1 and row-2
headings shared a Y. That guarantee is deliberately dropped: a real 2-row grid
makes both cells in a row share that row's height, so expanding one quadrant
pushed the next row down in **both** columns at once, which read wrong in
practice. Row-2 heading alignment ("Tag management" vs "Archive additions &
backup") is explicitly no longer required.

- **Upper-left, "New content"** — merges the former Feed management section with
  "Saving articles from anywhere". The Manage feeds `_lib_card` sits above the
  two capture-path accordions under one quadrant heading, with an `<h3>`
  separating them, so the two halves stay visually distinct rather than blending
  into one block.
- **Upper-right** — Existing archive management (unchanged content).
- **Lower-left** — Tag management (unchanged content).
- **Lower-right** — Archive additions & backup (unchanged content).

`/admin/library/feeds` remains a `_LIBRARY_TOOLS` entry (so the Admin hub's
Library card counts 9 tools and the link picks up badge support); only where its
card renders changed.

**Mobile** collapses to one column at the same 900px breakpoint. DOM order is
column-major (new, tags, existing, backup) but the required reading order is
new, existing, tags, backup, so the two `.lib-col` wrappers get
`display:contents` below the breakpoint — dissolving them so all four quadrants
become direct flex children of `.lib-cols`, which is what lets `order` interleave
them across the columns. Verified live at 390px portrait and 844px landscape:
single column, correct order, no horizontal overflow, taps still toggle.

**Quadrant headers reuse the /admin index's disclosure row.** Rather than a
bespoke header, the four quadrants render through `_disclosure_group` — a
module-level component extracted from `admin_page()`'s former inner
`_group_html` closure so both surfaces share one implementation: bordered box,
bold all-caps label with a muted tool count and any task badge on the left,
caret right-aligned (`justify-content:space-between`), pointing right collapsed
and down expanded.

The extraction was verified non-destructive by diffing `/admin`'s full rendered
HTML before and after — byte-for-byte identical — before the Library page was
switched over to it.

Two variants exist and are now documented in BRAND.md §"UI components": this
group-level row, and the item-level box (caret left of its label) used by the
nested capture-path toggles, which deliberately keep their own treatment. An
earlier round of this build gave the quadrants a bare heading with a
left-aligned caret and then chased glyph parity between the two levels; that's
superseded — the hierarchy distinction is the point, and each level now uses
the variant that belongs to it.

**Reader rename: "Saved" → "Archive".** The Reader's own quick-view label,
list-pane header, and every related admin-facing description previously
called this view "Saved" — inconsistent with every admin reference to the
same content as "the archive"/"Archive" (Archive Queue, Archive backup,
Archive additions, and this very page's own name). Renamed the
user-visible label only — `view=saved` stays the URL param (`GET
/read?view=saved`), and every internal identifier (`saved_rows`,
`saved_total`, `saved_tags`, the `view == "saved"` branches) is unchanged,
to keep the diff purely cosmetic and avoid touching any tested route
contract.

### Admin URL convention, Phase 1b PR 1 (2026-08)

Completes the software side of the `/admin/tools/{software|communities|
resources}/*` convention started in the Feature Taxonomy PR — see CLAUDE.md's
"Admin URL convention, Phase 1b PR 1" bullet for the full rationale
(Communities already conformed; why the legacy `tool_features` CRUD routes
and the public `/tools/*/edit` pages were deliberately excluded).

**Software, full cutover, no redirects** (these are POST-only action routes
and a few admin-nav-linked pages, not bookmarked pages):
`/admin/tools/new` (GET+POST), `/admin/tools/generate-description`,
`/admin/tools/leads`, `/admin/tools/name-duplicates` (+ `/merge`, `/resolve`),
and every `/admin/tools/{tool_id}/*` action route except the legacy features
CRUD — `approve`, `reject`, `delete`, `quick-edit`, `screenshot/recapture`,
`app-screenshot/recapture`, `app-screenshot/upload`, `research/refresh`,
`agent-taxonomy/verify`, `description/verify`, `differentiation/verify`,
`competitors/add`, `competitors/{id}/remove`, `competitors/generate-matches`,
`competitors/add-selected`, `generate-differentiation`,
`feature-links/save` — all moved to the equivalent `/admin/tools/software/*`
path. `/admin/tools/{tool_id}/features/*` (add, `{feature_id}/edit`,
`/verify`, `/delete`) is the one exception — untouched here, deleted outright
in Phase 1b PR 2.

**`/admin/tools/categories`'s redirect (shipped in the Feature Taxonomy PR)
is removed outright** — no legacy `/admin/tools/*` admin URL survives after
this PR.

**Benchmarking Resources renamed to Resources** (URL/copy only — the
`benchmarks` table, its columns, and every `Library.*_benchmark*` method are
unchanged): `/tools/benchmarks` → `/tools/resources` **with a 301 kept** at
the old URL (public, indexable — the one redirect added in this PR, mirroring
the categories redirect's own reasoning from the other side); `/admin/tools/
benchmarks` → `/admin/tools/resources` (no redirect, same as every other
admin move above). Every "Benchmarking"/"Benchmarking resources" page title,
`<h1>`, nav label, and admin-page copy string is relabeled "Resources"
(sentence case) — the `/tools` landing page's 2x2 tile grid, the admin nav's
`_TOOLBOX_TOOLS` entry, and the standalone add/edit/delete admin pages.

**Sanctioned, unmoved**: `/tools/software/{slug}/edit` and
`/tools/communities/{slug}/edit` are a deliberate resource-adjacent URL
pattern — same-resource "/edit" suffix, directly discoverable from the
public profile page they edit (`/tools/software/{slug}` →
`/tools/software/{slug}/edit`) — not admin-tree stragglers of the same kind
as the routes above. Investigated and explicitly kept outside `/admin/*`;
not a gap, a documented exception.

### Feature Taxonomy, Phase 1b PR 2 (2026-08) — public rendering + full legacy `tool_features` retirement

Two things in one PR, stacked on Phase 1b PR 1's route moves: the public
Software profile page finally renders the governed feature model
(`category_features`/`tool_feature_links`, live in the schema since Phase 1
but never rendered publicly until now), and the legacy `tool_features` table
— along with every route, admin section, and public-render path that read
or wrote it — is retired outright, per CLAUDE.md's "no dead data" rule.

**Public "Key features" card** (`webapp/app.py::_software_key_features_card`,
called from the `/tools/software/{slug}` profile route) **always renders**,
for every tool, unlike the legacy `tool_features` card it replaces (which
rendered nothing for a tool with no rows). A tool with `tool_feature_links`
shows real feature names — sentence-cased from the curated Title-Case
`category_features.name` via `_sentence_case_feature_name` (known, accepted
limitation: this also lowercases a genuine brand name that happens to
appear mid-name, e.g. "Slack" in "Slack / Email Collaboration Triggers" —
proper-noun detection is out of scope for this pass) — grouped by category
only when the tool's links span more than one seeded category (a single
list otherwise, since a heading for one group reads as noise). Each feature
row carries an "Add-on" tag when `availability='add_on'` and an "AI" tag
when `ai_enabled`. A tool with zero links renders a coming-soon state
instead ("Coming soon—we're mapping this tool against our curated feature
taxonomy.") rather than an empty card or no card at all — an honest "not
mapped yet" statement, not a gap to hide from visitors. The read path is
`Library.list_tool_feature_links_with_details`, a new join
(`tool_feature_links` → `category_features` → `tool_categories`) that drops
a soft-retired feature (`category_features.retired_at != ''`) from the
result — the same `retired_at`-filtering convention
`list_category_features(include_retired=False)`'s admin default already
uses, so a retired feature disappears from public rendering the moment it's
retired, with no separate cleanup step.

**Legacy `tool_features` retirement — every code path enumerated and
disposed of, nothing orphaned:**
- **Schema** (`linklib/db.py`): the `CREATE TABLE tool_features` and its
  index are removed from the schema string (replaced with an explanatory
  comment) — a fresh DB never creates the table again. The five CRUD
  methods (`add_tool_feature`/`list_tool_features`/`get_tool_feature`/
  `update_tool_feature`/`delete_tool_feature`) are deleted outright.
- **Admin CRUD routes** (`webapp/app.py`): `POST /admin/tools/{tool_id}/
  features/add`, `GET`+`POST /admin/tools/{tool_id}/features/{feature_id}/
  edit`, `POST .../verify`, `POST .../delete` are all deleted — this is the
  one route family Phase 1b PR 1 deliberately left at its legacy
  `/admin/tools/{tool_id}/*` path specifically so it could be deleted here
  rather than moved and then deleted.
- **Tool edit page**: the legacy `<details class="features-group">` section
  (the free-text feature list + "+ Add feature" form) is removed from
  `/tools/software/{slug}/edit` entirely — `_governed_feature_row`'s
  category-checklist section (Phase 1) is now the only feature editor on
  the page. The now-dead `_feature_row`/`_features_list_html`/`_n_features`/
  `_n_features_needs_verify`/`_features_badge_html` helpers are deleted with
  it.
- **Needs-verification banner fix**: `_research_banner_html`'s
  `_research_needs_review` flag used to OR in `_n_features_needs_verify >
  0` — with that source gone, it now tracks
  `tool.agent_taxonomy_needs_verification` alone, and the banner copy
  dropped its now-inaccurate plural ("drafted feature rows and agent
  taxonomy below" → "agent taxonomy below").
- **`/tools/software/compare`**: the legacy Features comparison row
  (`list_tool_features` per tool, `_feature_cell`, a union-of-feature-names
  row build) is removed, not migrated to the governed model — a real
  design decision, not an oversight: `docs/BUILD_PLAN.md` Phase 8 already
  reserves "a governed-model Compare view" as later, separate work, and
  this PR's brief only specified the profile page's card. The compare
  page's AI/Agent-involvement and Description/Differentiation rows are
  untouched.
- **`linklib/enrich.py`**: `generate_tool_features` is narrowed to
  `generate_tool_agent_taxonomy` — it keeps the real-crawl grounding
  mechanism (`_discover_nav_pages`/`_fetch_taxonomy_grounding`, now under
  those names) since that's still worth it for the agent-taxonomy summary
  alone, but the prompt/response/dataclass no longer draft feature rows at
  all (`ToolFeatureDraft` is deleted; `ToolFeaturesResult` is replaced by a
  narrower `AgentTaxonomyResult` with no `features` field). `max_tokens`
  dropped from 6000 to 2000 to match the smaller response.
  `webapp/app.py::_run_tool_research` (the shared background-task drafting
  function used by both add-tool auto-trigger points and the on-demand
  "Generate summary" button) calls the renamed function and only writes
  `set_tool_agent_taxonomy_draft` — the feature-row-writing loop is gone.
- **`scripts/enrich_tool_features.py` → `scripts/enrich_agent_taxonomy.py`**:
  renamed (`git mv`), rewritten to drop all feature-drafting/dedup logic —
  the bulk/backfill CLI now only drafts agent-taxonomy notes, calling
  `generate_tool_agent_taxonomy`. `scripts/enrich_community_profiles.py`'s
  docstring cross-reference to the old filename is updated.
- **`linklib/matchmaker.py`**: `_build_software_context` reads
  `list_tool_feature_links_with_details` instead of `list_tool_features`,
  formatting each link as `"{feature name}{ (add-on/AI suffix)}"` for the
  Software Chat Matchmaker's system-prompt context.
- **`/admin/system/database`**: `"tool_features"` is removed from
  `_TABLE_GROUPS`'s "Toolbox — Software" list. `_grouped_table_sections`
  degrades gracefully for a listed-but-absent table (`present = [n for n in
  names if n in schema]`), so removing it here is enough — no crash risk
  either way, but a live production DB still carrying the table until the
  drop script runs would otherwise show it in the wrong group.
- **`_SCRIPT_REGISTRY`** (`/admin/system/scripts`): the `enrich_tool_
  features.py` entry is renamed/rewritten to describe the narrowed
  `enrich_agent_taxonomy.py`.
- **Docs**: this ARCHITECTURE.md section, the `tool_features` schema-table
  row (removed), the "Automated Software research" prose section (rewritten
  for the agent-taxonomy-only pipeline), the Software Chat Matchmaker
  section (rewritten to cite `tool_feature_links`), and the Mermaid ER
  diagram's `tools ||--o{ tool_features` edge (removed — the
  `tool_feature_links` edges were already present from Phase 1) are all
  updated in this same PR, per the standing schema-change documentation
  rule.

**Human-run drop script, delivered but not executed**:
`scripts/drop_legacy_tool_features.py` follows the same guarded pattern as
the Article purge flow — prints the live row count, requires typing that
exact count to confirm, prompts to confirm a same-day backup exists (reads
`backup_log`'s most recent entry as a hint, doesn't verify it itself), then
drops the table and its index, runs `PRAGMA integrity_check`
(`linklib.backup.check_integrity`, the same mechanism the pre-backup
integrity check uses) and logs the result via `record_integrity_check`.
Brian runs this by hand via `railway ssh` once this PR is deployed and
verified live — never wired into a boot hook or deploy step, consistent
with every other destructive one-off script in this codebase.

### Feature Taxonomy scan tool, Phases 1-2 (2026-08) — no schema/route changes yet

A Phase 0 investigation (docs/FEATURE_TAXONOMY.md §10, "origination mode")
scoped the recurring/one-shot AI scan tool that backfills a curated feature
list for the 14 (of 17 live) `tool_categories` with no `category_features`
rows yet. Phases 1-2 are read-only/library-only — no schema change, no new
route, no `feature_review_queue` writes — so this note exists for
completeness rather than because the standing ARCHITECTURE.md-update rule
requires it.

**Phase 1** — `scripts/report_feature_taxonomy_coverage.py`, a read-only
diagnostic (same shape as `scripts/report_orphaned_categories.py`) that
reports, per `tool_categories` row: tool count, live/retired
`category_features` counts, and pending `feature_review_queue` counts by
source. Confirmed the real numbers the "~22 categories" estimate elsewhere
in this repo's history was guessing at: 17 categories total, 14 with no
curated list yet, 0 pending queue items anywhere. Registered in
`/admin/system/scripts`.

**Phase 2** — `linklib/feature_scan.py`, the per-tool origination-mode
research + drafting function (no roster-wide accumulation, no §7
don't-collapse merge, no queue write — those are Phase 3, still to be
scoped). Two pieces:

- `research_vendor_domain(tool_name, tool_url)` — an Exa `/search` call per
  §8 sourcing-hierarchy tier (changelog, help center, product page, press
  release), each restricted to the tool's OWN domain via `includeDomains`
  — deliberately not `linklib.agent.retrieve_exa`'s OPML-trusted-sites
  allowlist, since a vendor's changelog/docs live on the vendor's own
  domain, not Brian's curated third-party site list. Each hit is tagged
  with an inferred §8 tier from its URL path (`_infer_tier`) since Exa's
  index doesn't return a tier of its own, and deduped by URL in hierarchy
  order so a URL matching more than one tier's keywords keeps its
  strongest (lowest-numbered) classification. §8's tiers 5-6 (independent
  reviews, vendor-authored comparisons) are inherently mostly off the
  vendor's own domain and are out of scope for a domain-restricted search
  by construction — flagged in the module docstring as a known scope limit
  a future freshness-mode pass could revisit, not silently narrowed.
- `draft_tool_features_for_category(...)` — one Claude call per tool,
  grounded on the fetched content, returning a `ToolOriginationDraft`
  (proposed features, each with availability/ai_enabled/confident/
  source_url/source_tier, plus real token/cost accounting for both the Exa
  and Claude legs). Reuses `linklib.enrich`'s Anthropic-call idiom
  (try/except ImportError, a `DEFAULT_MODEL` constant, a JSON-only prompt,
  `pricing.compute_cost` accounting) rather than inventing a new one. The
  prompt embeds §10's two Phase-0/1-approved rules directly: no cap on how
  many features the scan proposes (Brian's review at the eventual queue is
  the curation gate, not the scan), and the thin-roster rule (`roster_size
  < 4` drops the differentiator criterion, keeping only table-stakes/
  standout candidates) — the caller passes the category's real roster size,
  not how many tools have been researched so far in a run.
- `verified_as_of` is stamped with the date the research actually ran — a
  sensible default for the eventual `tool_feature_links.verified_as_of`
  once Phase 3 builds the actual queue payload; still editable at
  edit-then-approve time like any other field.

**Tested with mocked Exa/Anthropic calls** (`tests/test_feature_scan.py`).
No live run against real vendor content happened in this session — no
`ANTHROPIC_API_KEY`/`EXA_API_KEY` were available, and using the app's own
production keys from a Code building session is against the standing
CLAUDE.md billing-note rule. `scripts/test_feature_scan_origination.py`
(same "manual QA, makes real API calls, writes nothing" shape as
`scripts/enrich_compare.py` — deliberately not in the scripts registry,
matching that precedent) is built for Brian to run by hand against 1-2
real Neobanking tools (the agreed first test category) to judge output
quality before Phase 3 is scoped.

**Phase 2 follow-up (2026-08) — the first real run (Mercury/Neobanking)
crashed on a truncated response, and the fix is structural, not a
one-off patch.** `draft_tool_features_for_category` hard-crashed with a
`JSONDecodeError` ("unterminated string") that fell into the generic
`except Exception` handler and silently returned `None`, losing the
tool's research entirely. Root cause: the response got cut off
mid-generation before finishing the JSON — Opus 5's on-by-default
adaptive thinking shares the same `max_tokens` budget as the response
text (the same failure class `MIN_GENERATE_MAX_TOKENS` exists to guard
the *floor* of in `linklib/enrich.py`), and Phase 0's own approved rule —
no cap on how many features the scan may propose — put an uncapped-length
response and a finite token ceiling on a collision course by
construction. A feature-rich vendor like Mercury is exactly the case most
likely to hit it, so this will recur on other vendors even after any
single token-budget bump, not just this one call. Fixed two ways,
per Brian's explicit ask that both land together, not either/or:
1. `max_tokens` raised from 4000 to `_ORIGINATION_MAX_TOKENS = 8000` —
   sized above `generate_community_profile`'s existing 6000 (23 fields in
   one call), since an uncapped-length array is the whole point of this
   call.
2. `_salvage_feature_objects` recovers as many COMPLETE JSON objects as
   `json.JSONDecoder.raw_decode`'s incremental parsing can pull from the
   `"features"` array before the first truncated/malformed element,
   rather than losing every earlier feature the model had already fully
   described. `draft_tool_features_for_category` tries a full `json.loads`
   first and only falls back to salvage on a parse failure — a clean
   response never touches the salvage path at all (`ToolOriginationDraft.
   truncated` stays `False`). When the first attempt DOES truncate, one
   automatic retry runs at `_RETRY_MAX_TOKENS = 16000`; token/cost
   accounting sums both calls, and the retry's result (parsed cleanly or
   salvaged again) always wins over the first attempt's — no infinite
   retry loop, no silently doubling cost on every call, only on the
   failure path. `truncated=True` on the returned draft is a visible
   signal to whatever consumes this (Phase 3, and the manual QA script's
   own printed warning) that the feature list may be incomplete, rather
   than that being indistinguishable from a vendor that genuinely has
   few features. Covered by 6 new regression tests reproducing the exact
   reported crash shape (`tests/test_feature_scan.py`).

**Phase 2 follow-up 2 (2026-08) — Mercury/Neobanking's first CLEAN run
surfaced "tier 0" in citations, undefined anywhere in §8's hierarchy
(which starts at 1).** Confirmed as a real, intentional sentinel — not a
hierarchy bug — that was simply never labeled anywhere a human could see
it: `source_tier=url_to_tier.get(source_url, 0)` falls back to `0`
whenever the model's cited `source_url` doesn't match any URL actually
fetched as grounding (a hallucinated/paraphrased citation, or a real page
the model saw referenced but this run never fetched itself) — a
citation-verification signal worth a human's attention, not a §8 tier to
compare against 1-4. Two real fixes, not just a label change:
1. **`UNCITED_TIER = 0`** is now a named constant with its own
   `_TIER_LABELS` entry ("Uncited (source URL not in fetched grounding
   set)"), and `ProposedFeature` gained `source_tier_label` so a caller
   never has to re-derive what a bare tier number means.
   `scripts/test_feature_scan_origination.py`'s printed output now shows
   the label, not a bare `[tier 0]`.
2. **`_normalize_url`** (case/trailing-slash/fragment-insensitive
   equality) fixes a real bug the labeling alone wouldn't have caught: a
   model-cited URL that's genuinely the same page as a fetched hit, just
   differing by trailing slash or case, was falling through to
   `UNCITED_TIER` on a trivial formatting mismatch rather than resolving
   to its real tier. Used only for citation matching, not for the Exa
   domain-restriction logic elsewhere in this module. The prompt was also
   tightened to tell the model to copy a cited URL exactly as it appears
   in the content block's own `--- Section (URL) ---` headers, to reduce
   how often a citation drifts from the literal fetched URL in the first
   place. A genuinely different/hallucinated URL still correctly resolves
   to `UNCITED_TIER` after normalization — this only recovers trivial
   formatting mismatches, not real inconsistencies. 4 new regression
   tests (`tests/test_feature_scan.py`).

### Feature Taxonomy scan tool, Phase 3 (2026-08) — roster-wide accumulation, §7 clustering/judgment, and the queue write

Closes the loop the Phase 2 build brief deferred: everything from "run
Phase 2's per-tool drafting across a category's whole roster" through the
actual `feature_review_queue` write. Approved shape, per the Phase 3
scoping proposal Brian signed off on:

- **Clustering is ONE Claude call over the whole roster's candidate
  list** (`linklib.feature_scan.cluster_candidate_features`), not
  embeddings-similarity (would judge on surface wording — exactly what
  §7 warns against: "never match on shared buzzwords") and not full
  pairwise comparison (infeasible at the ~400-450 candidates a real
  10-tool category produces at Mercury/Rho's observed ~44
  features/tool rate). It's a deliberately LOOSE grouping pass — a
  candidate gets grouped in on any plausible match, since the real
  merge-or-split decision happens next, per-cluster, with more detail.
- **Per-cluster judgment** (`judge_cluster`) makes the actual call,
  few-shot off §7's own worked examples verbatim
  (`_UNIFY_TEST_EXCERPT` — Automated Flux Analysis, Real-time
  Spreadsheet Sync vs. Automated Working Paper Generation), with an
  explicit **conservative bias instructed directly in the prompt**:
  split, don't merge, when genuinely uncertain. Reasoning stated to the
  model itself, not just implied: a false split is a cheap, visible
  queue-review fix; a false merge silently buries a real distinction
  inside a link `note` where it's much easier to miss. A judgment call
  can partition its cluster into more groups than the loose clustering
  pass produced — clustering groups too broadly on purpose, so a
  3-candidate cluster might correctly merge 2 and split the 3rd rather
  than forcing an all-or-nothing call.
- **Every failure mode degrades toward MORE separate features, never
  toward losing research or silently over-merging** — the conservative-
  bias philosophy applied structurally, not just in the prompts:
  clustering failing outright falls back to every candidate as its own
  singleton cluster; a judgment call failing outright falls back to
  every candidate in that cluster staying unmerged; a model's
  cluster/group output dropping or duplicating an index
  (`_validate_partition`) recovers a dropped index as its own singleton
  (never lost) and keeps a duplicated index's FIRST group membership
  only (never double-counted). A `tool_id` appearing twice within one
  merge group (that tool proposed two candidates that got merged
  together) is de-duplicated before the queue write, keeping the first
  and logging the rest — `upsert_tool_feature_link`'s
  `UNIQUE(tool_id, feature_id)` would otherwise silently let the second
  overwrite the first.
- **Queue-write wiring reuses `Library.add_feature_review_queue_item`'s
  existing payload shape exactly** — one call per final feature (post-
  cluster-and-judge), `source='scan'`, one `links` entry per
  contributing tool (each tool's own availability/ai_enabled/note/
  source_url/verified_as_of — §7's "the AI flag and link note carry the
  difference" lands here). Zero new surface for the approval UI
  (`/admin/tools/software/feature-review-queue`) to handle — a merged
  proposal's exact-name collision with an existing feature still hits
  the same Phase 1c merge-confirmation flow as any other proposal.
  `proposal_type` follows the existing seed-data convention exactly
  (`new_feature+link` / `new_feature+N links`).
- **`linklib.feature_scan.originate_category_features(lib, category_id,
  category_name, tool_roster, ..., dry_run=...)`** is the orchestration
  entry point — runs Phase 2's drafting across the roster, accumulates
  `CandidateFeature`s (a `ProposedFeature` tagged with its originating
  tool + the draft's `verified_as_of`), clusters, judges each
  multi-member cluster, and either writes to the queue or — when
  `dry_run=True` — only populates `OriginationSummary.queued_payloads`
  with what WOULD have been written, so a caller gets the same preview
  guarantee whether calling the library function directly or through
  the script below. Returns `None` only if EVERY tool's research
  failed; a partial-failure run still returns a summary covering what
  succeeded, with `tools_failed` counting the rest.
- **`_call_and_parse_array`/`_call_claude`/`_salvage_json_array`** — the
  Mercury truncation fix's call+parse+one-retry-on-truncation machinery
  was generalized (from a single inline closure inside
  `draft_tool_features_for_category`) so clustering and judgment reuse
  the exact same resilience rather than two more copies of it.
  `_salvage_feature_objects` survives as origination drafting's own
  named wrapper (asserted against directly in tests) over the
  generalized `_salvage_json_array(raw, "features")`.
- **`scripts/originate_category_features.py`** — the manual-run entry
  point, registered in `/admin/system/scripts`. Unlike
  `scripts/backfill_logos.py`'s preview (which lists already-known
  un-fetched rows for free), there's no cheap way to preview this
  pipeline — even a preview run makes the full real Exa/Claude research/
  cluster/judge calls, since that IS the work; the script is explicit
  about this in its own docstring and `--help` rather than implying a
  free preview the way the `--apply` convention usually does elsewhere.
  Default (no `--apply`) prints every payload that WOULD be queued and
  writes nothing; `--apply` also calls the same code path with
  `dry_run=False`.
- **Review-queue near-duplicate nudge, built alongside per Brian's
  explicit call (not a separate phase)** —
  `webapp.app._find_near_duplicate_queue_items` (pure Python,
  `difflib.SequenceMatcher`, no LLM call) groups pending
  `feature_review_queue` items by `category_id` and flags pairs whose
  proposed feature names cross a similarity threshold
  (`_NEAR_DUPLICATE_NAME_THRESHOLD = 0.6`, a starting point not yet
  tuned against real data). Rendered as an amber warning line on
  `/admin/tools/software/feature-review-queue`'s existing card
  (`_feature_review_queue_item_card`'s new `near_duplicates` param),
  reusing the same amber pair (`#fef3c7`/`#92400e`) that card already
  uses for the "public" source badge rather than introducing a new
  color. **Deliberately source-agnostic** — it has no dependency on the
  scan having run at all, and catches a near-dupe regardless of
  whether it came from the scan's own conservative split, two separate
  scan runs over time, or an admin's own manual entry; this is what
  makes it a genuine complement to Phase 3's clustering rather than
  scan-specific tooling bolted onto the queue page.
- **Tested with mocked Exa/Anthropic calls throughout**
  (`tests/test_feature_scan.py`, 19 new cases covering
  `_validate_partition`'s repair behavior, clustering/judgment parsing
  and failure-mode fallbacks, and a full `originate_category_features`
  integration test against a real temp `Library` confirming the queued
  payload's exact shape; `tests/test_feature_taxonomy.py`, 6 new cases
  for the near-duplicate nudge). No live run against real vendor
  content happened in this session — same `ANTHROPIC_API_KEY`/
  `EXA_API_KEY` gap as Phase 2 — so a genuine 10-tool Neobanking
  origination run is still Brian's to do by hand via the script above.

### Feature Taxonomy scan tool, Phase 3 follow-up (2026-08) — the first real live run broke clustering, and the fix is a redesign, not a bigger constant

The first real run (Neobanking, 10 tools, 364 candidates) produced **zero
merges** despite obvious, repeated near-verbatim duplicates across the
roster (e.g. "Accounting software sync" appearing 7 times, "Business
checking account" ×4, "Webhook event notifications" ×4). Root cause,
confirmed from the run's own logged call behavior: the whole-batch
`cluster_candidate_features()` (shipped in the initial Phase 3 PR) asked
the model to emit ONE JSON array covering all 364 indices in a single
response. Its logged `_salvage_json_array` recovery on the first attempt
was **zero items** — not "some, then a cutoff," but the `"clusters"` key
never being reached at all — meaning Opus 5's on-by-default adaptive
thinking consumed the ENTIRE `max_tokens` budget reasoning about 364
items simultaneously, before writing a single output token. The retry at
double the ceiling didn't fix it either. `_validate_partition`'s
conservative-bias repair then did exactly what it's designed to do:
every unmatched index became its own singleton — silently, with nothing
distinguishing "clustering ran cleanly and found no overlap" from
"clustering structurally failed." Because every cluster came back a
singleton, `judge_cluster`'s `n<=1` short-circuit meant the judgment
stage never even ran — this was squarely a clustering-stage failure.

**Why a bigger `max_tokens` ceiling was rejected as the fix**: the task's
required reasoning length scales with total roster candidate count — a
20-tool category would just hit the same wall again at a higher
constant. No fixed ceiling is "big enough" for every future category.

**Fix: clustering is now INCREMENTAL, folded into the existing per-tool
research loop** — not a separate whole-batch step. As each tool's
candidates are drafted, `match_candidates_to_representatives()` checks
them against the RUNNING set of distinct-capability representatives
found so far (one representative per cluster), not the whole roster's
history:
- Tool 1 finishes → nothing to compare against yet, every candidate
  becomes its own new cluster/representative, no API call needed.
- Tool 2+ finishes → one call: "here are this tool's new candidates,
  here are the representatives found so far — which new candidates
  match an existing representative (§7), which are genuinely new?"
  Output is one integer-or-null entry per NEW candidate only.
- **The critical property**: every call's OUTPUT is bounded by ONE
  tool's candidate count (~30-50 entries, a trivial array), regardless
  of how large the representative set or total roster grows. The
  representative set only ever appears as INPUT context — the model's
  context window holds that comfortably without competing against
  `max_tokens` the way response generation does. A category with
  genuinely little cross-vendor overlap (representatives approaching
  total candidate count) still keeps every call's output small; only a
  category's real duplicate rate affects representative-list *length*,
  never the *output* size of any single call.
- Every failure mode still degrades toward MORE separate features,
  never toward losing research or silently over-merging: a structural
  match-call failure treats that tool's candidates as all-new (same
  conservative-bias default as before) but now sets
  `OriginationSummary.clustering_degraded=True` (plus
  `clustering_degraded_tools`, naming which tool) — the exact signal
  the original bug's silence was missing.
  `scripts/originate_category_features.py` prints a loud warning banner
  when this flag is set, so a degraded run can never be mistaken for a
  clean "no overlap found" result again.
- `judge_cluster` and the queue-write wiring are UNCHANGED — they
  already operated per-cluster with small cluster sizes; only the
  upstream clustering step was broken at scale.
- The old whole-batch `cluster_candidate_features()`/`_CLUSTER_PROMPT`
  were removed outright (never shipped to a working state, no back-compat
  concern) rather than left as dead code alongside the new mechanism.

**Test-coverage gap named and closed**: the original Phase 3 test suite
only ever exercised 2-3 candidate clusters — nothing near real scale,
which is exactly why this shipped broken. Added
`test_originate_category_features_large_roster_merges_correctly_and_stays_bounded`
(`tests/test_feature_scan.py`) — a synthetic 8-tool/160-candidate run
(same order of magnitude as the real failure) with one duplicate
recurring across every tool, mocked deterministically, asserting
directly on the property that matters: total Anthropic call count scales
with TOOL count (16 calls: 8 drafts + 7 match calls + 1 judgment call),
never with total candidate count, and every match call's own stated
output contract names exactly that tool's candidate count — never
anything approaching the running representative total. 7 further
regression tests cover the new `match_candidates_to_representatives`/
`_validate_matches` functions directly (parsing, the no-representatives/
no-new-candidates no-call shortcuts, out-of-range/bool-guard repair) and
the `clustering_degraded` flag's both states (net: 43 → 48 tests in
`tests/test_feature_scan.py` — 3 whole-batch-clustering tests removed
alongside the code they covered, 8 added).

**Cleanup — `scripts/deny_pending_scan_proposals.py`**: the 364 bad
singleton proposals from the broken run needed clearing before a
corrected re-run, but per CLAUDE.md's "no dead data" / always-leave-a-
trace discipline, the right move is to DENY them (preserving the record
that the run happened and why it was thrown out) rather than delete the
rows. A small reusable script — preview-by-default/`--apply` like every
other admin script here — bulk-denies every pending item matching a
category and source (`scan` by default) with a shared resolution note.
Reusable for any future botched run, not a one-off hack. Registered in
`/admin/system/scripts` alongside `originate_category_features.py`.

**Also fixed in the same pass**: `originate_category_features.py`'s own
docstring and registry entry previously read as "preview, then
`--apply`" — misleading, since preview runs the exact same full pipeline
and real API calls as `--apply` (there's no cheap preview path the way
`backfill_logos.py`'s is), so running both on the same category pays for
the whole run twice. Corrected to say plainly: go straight to `--apply`
once the pipeline is trusted.

### Feature Taxonomy scan tool — framework remap (2026-08)

A DIFFERENT job from origination mode's own `judge_cluster`/
`match_candidates_to_representatives`, which invent their own groupings as
they go: `scripts/remap_queue_to_framework.py` matches an already-queued
category's pending `source='scan'` proposals against a FIXED, human-defined
target feature list — the groupings are given (Brian reviews the raw
origination output by hand and decides exactly what the category's bucket
list should be), only the per-item assignment (or "belongs to none of
them") is a judgment call. Built for the real Neobanking incident (2026-08):
202 pending proposals from the 8/23 corrected origination run needed
remapping against a 41-bucket target list.

**Mechanism** (`linklib/feature_scan.py`): `match_items_to_framework`
batches the pending items (default 50/call — the bucket list is small and
fixed, so only the item side needs chunking, unlike origination's own
representative-set-growth problem) and asks Claude to return, per item,
either an index into the fixed bucket list or `null`. Malformed/out-of-
range/missing entries degrade to `null` ("needs a human look"), never to a
guessed match — same conservative-bias convention as
`match_candidates_to_representatives`' own `_validate_matches`. Where more
than one pending item maps to the same bucket, `synthesize_bucket_definition`
makes one more Claude call to write a merged definition from every
contributing item's own definition (best-effort — falls back to the single
longest contributing definition, zero cost, on any SDK/key/call failure);
the bucket's NAME is never synthesized, always the framework's own exact
string, so it can't drift from what Brian approved.

**The consolidation write** is new: `Library.update_feature_review_queue_payload`
rewrites a *pending* item's `payload`/`proposal_type`/`articulation` in
place (raises if the item isn't pending — a resolved item's payload is a
historical record, not something a later script may silently rewrite),
distinct from `approve_feature_review_queue_item`/`deny_feature_review_queue_item`,
neither of which touches payload post-write. The script picks the first
matched item's row as the "primary" row to rewrite, unions every
contributing item's `payload.links` deduped by `tool_id` (keeping whichever
copy has a real `source_url`, then a `verified_as_of` date, then the
longer note, when the same tool appears more than once), and denies the
other now-redundant rows with a reason naming what they were folded into
("Consolidated into '&lt;bucket name&gt;' during framework remap 8/24") —
never deletes, per CLAUDE.md's no-dead-data/always-leave-a-trace
discipline. An item matching none of the framework's buckets is denied as
out of scope. A bucket with no matching pending item at all is simply
skipped — no placeholder feature/link is invented for a capability no real
tool in the roster actually offers.

**`articulation` is deliberately NOT part of the merge, unlike name/
definition/links — confirmed with Brian rather than silently assumed.**
The rewritten row's `articulation` is left exactly as it already was on
whichever item the script picked as primary (the call passes no
`articulation` argument, and `update_feature_review_queue_payload` treats
that as "leave it alone"); every other contributing item's own
articulation text is neither copied over nor concatenated in — it simply
stays on that item's now-`denied` row, still fully readable there, with
the denial reason naming exactly which bucket it was folded into. Nothing
is lost (the sibling row and its reasoning both still exist), it's just
not unioned into one place the way links are. Rationale: articulation is
internal scan-run provenance/reasoning, not public-facing data — the
denied sibling rows already give full traceability, so concatenating
wasn't judged worth the added complexity.

**Hard rule, enforced by construction**: this script never calls
`add_category_feature`/`upsert_tool_feature_link` and never sets a queue
item's status to `approved` — every affected item ends the run either
`pending` (rewritten, ready for a human's final approve/deny pass on
`/admin/tools/software/feature-review-queue`) or `denied`. It's a
queue-to-queue remap; final approval into `category_features`/
`tool_feature_links` stays a human action, per docs/FEATURE_TAXONOMY.md §9.

**The target framework is a JSON file, not hardcoded** — `--framework`
(default `scripts/seed_data/neobanking_feature_framework.json`) so a
different category's ~30-40-line bucket list can be handed in without
touching the script. Preview by default (runs the real Claude matching/
synthesis calls — no cheaper way to preview a judgment call, same reasoning
as `originate_category_features.py`'s own preview mode — but writes
nothing); `--apply` commits the plan, then write-then-read-backs the
category's post-write pending count against what the plan predicted, per
CLAUDE.md's one-off-admin-fix discipline.

**Neobanking framework revision (2026-08)** — Brian's review of the first
real preview run against production corrected the 41-bucket
`scripts/seed_data/neobanking_feature_framework.json`: dropped "Mobile
banking" (general app access, not CFO-relevant), added "Mobile check
deposit" (Core banking — the specific useful mobile capability) and
"General ledger / accounting software sync" (Accounting — kept distinct
from the existing "Accounting services" bucket, which is bookkeeping/
tax-adjacent, a different concept), bringing the framework to 42 buckets.
Two denied-as-out-of-scope preview items (already multi-tool merges from
the original origination pass) were confirmed to belong in the two new
buckets instead, once they existed. No code change — the framework file's
own `_comment` field records the revision and why.

**Feature Review Queue card follow-up (2026-08) — labeled, narrowed "Feature
(NEW)" fields, and a new articulation-coverage warning.** Two findings from
that same review session, both fixed on
`_feature_review_queue_item_card` (`webapp/app.py`):

1. The card's "Feature (NEW)" section stacked the feature-name and
   pointer-note text inputs with no visible labels — distinguishable only
   by position — and both ran full-width, wider than either field's
   typical content needs. Fixed with a real `<label>` above each ("Name"
   / "Pointer note (optional)", matching the label style the Manage
   Features "Add a feature" form already used) and a narrowed `320px`
   width on both inputs. The Manage Features pivot table's own per-row
   inputs (`_feature_row`) were checked and are unaffected — that surface
   already has real `<th>` column headers above every input, so it never
   had this problem.
2. Reviewing a denied Neobanking item ("Financing services") surfaced a
   genuine mismerge that a text-coverage check could have flagged before
   approval time: the item's merge `articulation` justified only 2 of its
   5 linked tools (meow, lili), with the other 3 (Mercury, Pipe, Novo)
   unexplained — those 3 turned out to already be correctly linked to a
   separately-approved "Credit underwriting" feature, confirming the
   5-tool merge itself was wrong, not a real distinct feature. New
   `_articulation_tool_coverage(articulation, tool_names)` splits a queue
   item's linked-tool names into (mentioned, unmentioned) by a
   case-insensitive substring check against the articulation text —
   deliberately a coarse heuristic, not NLP, good enough to prompt a human
   to look and never used to auto-deny or block anything. The card now
   shows an amber warning (same style as the existing near-duplicate
   banner) whenever an item has ≥2 linked tools, a non-empty articulation,
   and PARTIAL coverage (some tools mentioned, some not) — full coverage
   and zero coverage both render nothing, since neither is the suspicious
   pattern; only "explains some, silent on the rest" is. This is a
   general Feature Review Queue admin-page check, not specific to the
   framework-remap script — it applies to any pending item regardless of
   `source` (`admin`/`scan`/`public`) or which pipeline produced it.

### Resources — Book recommendations (2026-08)

Splits the flat `/tools/resources` card list into two headed sections:
"Benchmarking" (the existing cards, unchanged) and "Book recommendations"
(a new, sparse-by-design personal reading list). `benchmarks.section`
(`'benchmarking'` | `'books'`, default `'benchmarking'`) is the new
discriminator — added via the standard idempotent `ALTER TABLE` migration,
so all 20 existing rows land in `'benchmarking'` with no backfill needed.

**Read/write path**: `Library.list_benchmarks(section=...)` takes an
optional filter — `/tools/resources` and `/admin/tools/resources` both call
it twice (once per section) rather than fetching everything and filtering
in Python. `add_benchmark`/`update_benchmark` both grew a `section`
parameter (default `'benchmarking'` for backward compatibility with every
pre-existing caller). `add_benchmark`'s `next_order` computation is scoped
to `WHERE section=?`, so the two sections order independently — adding a
benchmarking card never shifts a book's `sort_order` and vice versa.

**Public rendering** (`/tools/resources`): `_bench_card` (existing markup,
unchanged) renders Benchmarking rows with their pricing/coverage badges;
a new `_book_card` renders Book recommendations rows with the same
`.bench-card` layout minus those badges — they encode data-access tiers
("Private"/"Public"/"Both" coverage, "$ Paid" pricing) that don't map onto
a personal reading list. The Book recommendations section always renders,
even when empty ("Coming soon.") — sparse-by-design, per the build brief,
not a state to hide.

**Admin page** (`/admin/tools/resources`): the single table becomes two
(`_admin_resource_table`, one shared helper called per section), plus a
new "Section" `<select>` on the add/edit form (`_RESOURCE_SECTIONS`,
`_benchmark_form_fields`) — an unrecognized/missing value on submit falls
back to `'benchmarking'` rather than erroring, same defensive pattern as
every other admin form field with a fixed vocabulary in this codebase. A
form hint notes Coverage/Pricing are ignored on the public page for Book
recommendations rows, since the fields stay on the form (unused, not
hidden) rather than adding conditional JS to a form this small.

**Seeding the ten initial book rows** — deliberately NOT via
`_DEFAULT_BENCHMARKS`/`_seed_toolbox`: that pipeline only ever *syncs* an
existing row by URL match (name/description), it never inserts one — the
same "a missing row might be a deliberate admin delete" reasoning that
governs every other `_seed_toolbox` sub-loop (`tools`, `communities`).
Ten brand-new rows have nothing to sync against, so they need a genuine
one-time insert: `scripts/seed_book_recommendations.py`, guarded the same
way as every other production-data script in this codebase — preview by
default, `--apply` to write, write-then-read-back verified, idempotent by
URL match against the whole `benchmarks` table (any section) so a partial
or repeated run never duplicates a row. Lives in `scripts/` (not
`scripts/archive/`) until Brian actually runs it, per the standing "a
script moves to archive/ once its job is done, never before" convention.

**"Suggest a resource" — reuses `/contact`, no new route or spam-guard
code.** Investigated first, per the build brief's explicit ask: two
"suggest something" mechanisms already exist on the site.
`/tools/communities/gap` (the Community gap-feedback flow) is public,
structured, admin-triaged at `/admin/community-gaps` — but has **no rate
limiting, honeypot, or spam filtering at all**, unlike `/contact`. `/contact`
itself has the full stack (per-IP rate limit, honeypot, a time-trap, a
keyword-based spam auto-reject) and — per `docs/BUILD_PLAN.md`'s own
"public feedback/suggestion UI" note — was already investigated and
confirmed reusable for exactly this kind of public suggestion surface, just
never built against. `/tools/resources` gets a plain "Suggest a resource
→" link straight to `/contact?context=resource-suggestion` — no dedicated
form, no new database column. `_CONTACT_CONTEXT_PREFIXES` (a small dict,
extensible to future contexts) maps `context=resource-suggestion` to a
prefilled message prefix ("Resource suggestion: ") via `/contact`'s
existing `message` query-param prefill mechanism (previously accepted by
the route but never actually passed by any caller) — the prefix rides
along into the saved `contacts.message` text itself, so it's visible in
the admin inbox's message preview with **no schema change to `contacts`**
and no new admin-page code. An explicit `?message=` still wins over
`context` if both are somehow passed, so nothing already relying on
`message` breaks.

**Known follow-up, explicitly out of scope for this PR**: the Community
gap-feedback flow's missing rate-limit/honeypot/spam-filter coverage
(confirmed by this investigation, not new) is a real gap on a public,
unauthenticated, no-login-required surface — flagged for its own PR, not
fixed here.

### Feed management (`/admin/library/feeds`)

The admin surface for the RSS subscription list. Before this, feeds and their
sections could only be changed by hand-editing `preferred_sites.opml` and
deploying. See §2, "Feed subscriptions" for the tables and §4 for why the OPML
file is generated rather than edited.

**Subscriber-access re-check lives here.** The "Re-check subscriber access"
control sits at the top of this page, above the H1. It moved from
`/admin/library` once this page existed: it probes a recent post per paywalled
source (`authcheck.check_auth_cookies`) to confirm that source's subscriber
cookie still fetches full text, which is feed-specific work. Only rendered when
at least one `LINKLIB_COOKIE_<DOMAIN>` variable (see `linklib.extract._COOKIE_DOMAINS`)
is configured; dormant otherwise.

`POST /admin/auth/recheck` keeps its path — the Reader's own subscriber-access
banner posts to it as well, and the path isn't library-page-specific, so moving
it under `/admin/library/feeds/...` would make that second caller read oddly.
Only its redirect target moved, from `/admin/library` to `/admin/library/feeds`.

*What the check actually probes, traced live:* for a configured domain it reads
the OPML to find that domain's feed, requests **the stored feed URL verbatim**,
takes the first item's article URL from the result, and probes **that article
URL** with the cookie attached. So the feed URL is the input it routes through,
not the thing it fetches for the access test — see the Mostly Metrics note in
§4.

**Layout: one flat feed table, plus a separate sections area.** `GET
/admin/library/feeds` renders every feed as a row in a single table (Name, URL,
Section, Read only, Edit, Remove) rather than grouping them into a bordered box
per section. Section and Read only are per-row controls that post on change
(`POST .../feeds/{feed_id}/section`, `POST .../feeds/{feed_id}/read-only`), so
a feed's grouping and its queue eligibility are edited in place. Below the
table, a plain "Manage sections" area handles section add/rename/remove as
simple rows, with no per-section box and no settings beyond the name.

**Routes.** Feeds get the `/admin/tools/resources` treatment — separate
`GET|POST /admin/library/feeds/new` and `GET|POST
/admin/library/feeds/{feed_id}/edit` pages sharing one `_feed_form_fields()`
helper. Sections are handled inline (`POST .../feeds/sections/new`, `POST
.../feeds/sections/{section_id}/rename`, `POST
.../feeds/sections/{section_id}/delete`), matching `/admin/tools/software/categories`.
Destructive actions are inline forms with an `onsubmit="return confirm(...)"`
guard. All are admin-gated; outcomes come back through the `?msg=`/`?error=`
banner convention.

The read-only toggle is the one mutation that does **not** regenerate the OPML:
the file has no field for that flag (the queue reads it straight from the DB),
so rewriting it would be a no-op write that only churns the file's mtime.

**Every mutation runs the same three steps**: write to the DB, regenerate the
OPML, clear the allowlist cache. The last two are one call (`_publish_feeds()` →
`Library.write_opml()`), deliberately, so no route can do the first without the
others — see §4.

**Section delete is blocked while the section holds feeds.** The page shows how
many feeds must move first instead of rendering a button that would fail, and
`Library.delete_feed_section()` raises independently rather than cascading, so a
section's feeds can't be destroyed along with it by any caller.

**Propagation is immediate, with one pre-existing exception.** `parse_opml` is
not cached, so the Reader reflects a change on the next page load;
`preferred_domains`' cache is cleared on write, so FP&A Buddy reflects it on the
next question. The exception is `feed.py`'s 30-minute per-feed item cache, which
is keyed on `xml_url` and unchanged by this work: a newly added feed is a cache
miss and fetches immediately, and a removed feed's entry is simply orphaned, but
an *edited* feed that keeps its URL still serves cached items until the TTL
expires.

**Known asymmetry, surfaced in the page copy rather than fixed.** The Reader's
Sources rail is built from *fetched items*, not from the subscription list, so a
feed that's quiet, unreachable, or beyond the Reader's `max_total=120` cap
appears on this admin page and not in the rail. That's inherent to how the rail
is built (out of scope here — this phase deliberately doesn't touch `/read`), so
the page says so in a bullet instead of leaving it to read as a sync bug.

**Mobile.** The feed table switches from five columns to stacked blocks below
820px (`.ff-table` rules), with CSS `::before` labels on the Section and Read
only cells so those controls stay identifiable outside table context. The
breakpoint is 820px rather than 720px because five columns run out of room
sooner than three did. Live-verified at 390px portrait, where an earlier
three-column version squeezed the URL column narrow enough that
`word-break:break-all` wrapped feed addresses one character per line and turned
the page into a 9,700px scroll.

### Auth: three tiers, one cookie

Implemented with the stdlib only (`hmac`/`hashlib`/scrypt) — deliberately no
`itsdangerous`/SessionMiddleware dependency.

- **Login** (`POST /login`): username + password verified against `users`
  (scrypt). One **break-glass** path: the host password
  (`LINKLIB_PASSWORD`, falling back to `LINKLIB_SAVE_TOKEN`) with a reserved
  admin username works even with an empty `users` table, so a lost password
  can't lock the owner out. Success sets `cfo_session`: an HMAC-SHA256-signed
  value `exp|role|username` (key: `LINKLIB_SECRET_KEY`, falling back to the
  password — unset means restarts invalidate sessions), HttpOnly,
  SameSite=Lax, 30-day TTL.
- **Post-login/logout redirect**: `GET/POST /login` carries an optional
  `next` query param/hidden field, validated by `_safe_next` (must be a
  same-app relative path — rejects absolute URLs, `//host` scheme-relative,
  and `/\host` backslash tricks). A present, valid `next` wins after a
  successful login regardless of role. With no `next`, the default is
  role-based: admins land on `/admin`, everyone else on the homepage (`/`) —
  never `/library`, which isn't every signed-in user's home. Any private page
  that redirects to `/login` (`_login_redirect`) already round-trips through
  `next` this way. `GET /logout` always redirects to `/` for every role.
  The Software/Community Matchmaker links (`/tools`, `/tools/communities`)
  use this: signed out, the link is replaced with a "Sign in for access…"
  prompt pointing at `/login?next=<matchmaker path>`; the matchmaker routes
  themselves stay public (see below) — this is a soft, discovery-level nudge
  toward signing in, not a hard gate on the chat itself.
- **Three surfaces**:
  - *Public* — no auth: `/`, `/thought-leadership`,
    `/thought-leadership/growth-engine-ratio`, `/thought-leadership/growth-engine-calculator`
    (Original Content Phase 4c — the standalone interactive calculator, still a hand-built
    Python route since it's genuinely interactive, not markdown-representable content; the
    article half moved to the catch-all like netsuite-mcp/ai-hackathon-playbook below),
    `/thought-leadership/ai-hackathon-playbook`,
    `/thought-leadership/netsuite-mcp`, `/tools`, `/tools/software`,
    `/tools/software/{slug}` (the profile page, Software search overhaul Phase 2;
    opened in a new tab via each card's "Full profile →" link — 404s for an
    unknown or unapproved slug, same rule as the communities equivalent below),
    `/tools/software/compare` (the comparison matrix, Phase 5 — 2-4 tools via
    `?ids=`, registered *before* `/tools/software/{slug}` so "compare" isn't
    swallowed as a slug, same fix as `/tools/communities/compare` below;
    fewer than 2 approved ids among those given renders an instructional
    empty state rather than an error),
    `/tools/software/screenshot/{filename}` (serves a captured homepage
    screenshot from `_SCREENSHOT_DIR` on the persistent volume — same
    basename-only traversal guard as `/static/{filename}`, kept as a
    separate route/directory since these don't ship inside the Docker
    image),
    `/tools/software/logo/{filename}` (Phase F — serves a Brandfetch-sourced
    tool logo from `_LOGO_DIR` on the persistent volume, same basename-only
    traversal guard and filename-based URL shape as the screenshot route
    above; `tools.logo_path` stores a path like `logos/tools/abacum.svg`
    relative to the database's parent directory, written by
    `scripts/backfill_logos.py` — only the basename is used to build the
    served URL. Rendered near the name on the profile page and small on each
    directory card; a record with no `logo_path` yet falls back to a shared
    initial-monogram placeholder rather than a broken image),
    `/tools/resources`,
    `/tools/communities`, `/tools/communities/{slug}` (the profile page, redesigned
    in Phase 3b against the same card system as Software's Phase 3 page — a
    "Bottom line" callout, a Details card for cost/access/sponsorship/format/reach/
    founded/CPE, four themed Community Profile cards ("Who it's for" / "What you
    get" / "How it works" / "Cost & structure") grouping the qualitative narrative
    fields instead of one long flat scroll, and a screenshot card built from
    scratch — opened in a new tab from a directory card),
    `/tools/communities/screenshot/{filename}` (Communities equivalent of
    `/tools/software/screenshot/{filename}` above, serving from its own
    `_COMMUNITY_SCREENSHOT_DIR` rather than sharing `_SCREENSHOT_DIR` — Software
    and Communities slugs can collide, e.g. `airbase`/`datarails`/`rillet`),
    `/tools/communities/logo/{filename}` (Communities equivalent of
    `/tools/software/logo/{filename}` above, serving from its own
    `_COMMUNITY_LOGO_DIR` rather than sharing `_LOGO_DIR`, for the same
    slug-collision reason as the screenshot route),
    **manual logo override (2026-08)** — Aleph's public profile was found
    showing Zapier's logo (live screenshot evidence). Investigation confirmed
    `scripts/backfill_logos.py` is the only logo writer (a manual monthly
    batch script, never triggered on tool add or by any periodic/background
    refresh), and traced Aleph's own domain extraction as correct
    (`https://www.getaleph.com` → `getaleph.com`) with no Zapier entry
    anywhere in this codebase's own data — ruling out a same-repo slug/domain
    collision. The live root cause on Brandfetch's side couldn't be
    independently reproduced from this session (its outbound network egress
    is blocked to both `api.brandfetch.io` and the tool's own site), so the
    fix ships two things regardless of that unconfirmed root cause: (1) a
    domain-echo guard in `linklib.brandfetch.fetch_logo_asset` (originally
    `backfill_logos._fetch_logo_asset`; relocated in the follow-up described
    below) — the Brand API response's own `domain` field is now checked
    against the domain requested before its logo asset is ever trusted,
    closing the exact class of gap (accepting whatever a fuzzy/mismatched
    API response returns with no cross-check) that would explain Aleph's
    case whether or not it's the actual cause; and (2) a real manual-override
    mechanism, since a wrong logo needs to be fixable without a code deploy
    regardless of why it went wrong. `tools.logo_manual_override`/`tools.logo_override_stale` (and the
    same two columns on `communities`) back it: `Library.set_tool_logo_manual`/
    `set_community_logo_manual` write an admin-supplied logo (fetched
    server-side from a pasted URL, or uploaded directly — jpeg/png/webp only,
    no SVG, since this is now untrusted admin/user input rather than a vetted
    third-party API response) and set the override flag; `set_tool_logo`/
    `set_community_logo` (the automated Brandfetch writers) refuse to
    overwrite a row with that flag set unless called with `force=True` — the
    one choke point every automated write goes through, so the protection
    holds regardless of what selection query got a caller there (the
    backfill script's own `_select_candidates` query also excludes these
    rows, as a second belt, so a --apply run never spends Brand API quota on
    a row it can't write anyway). `clear_tool_logo_override`/
    `clear_community_logo_override` ("Revert to automatic") blank `logo_path`
    and drop the flag so the next backfill run repopulates it — a brand-new
    row from `add_tool`/`add_community` (a re-add after deletion, for
    example) starts with no override, by construction, since neither insert
    method accepts or defaults these flags to anything but 0.
    `update_tool`/`update_community` flag (never silently clear) an active
    override as stale (`logo_override_stale=1`) when the row's URL changes to
    a genuinely different domain (`_url_domain_changed`, via the existing
    `_slug_host` helper — a path/query/scheme-only edit to the same site
    doesn't trip it), surfaced as a coral banner on the edit page with
    "Still correct—dismiss" and "Clear override" actions
    (`dismiss_tool_logo_stale`/`dismiss_community_logo_stale`) — a stale
    override is never silently kept applying to what's now effectively a
    different company's site, and never silently dropped either. The admin
    UI (`_logo_admin_section`, both edit pages) reuses the App screenshot
    section's own conventions — a URL-fetch text input, a plain file-upload
    `<form>` (no crop needed, unlike the app-screenshot upload), and a
    badge distinguishing "Manual override" from "Auto-fetched (Brandfetch)".
    See `tests/test_logo_override.py` for the full coverage (override wins
    over a fresh automated fetch, a normal tool still auto-fetches
    unaffected, revert-to-automatic, staleness on a domain change but not a
    path-only edit, the backfill script's own selection-query exclusion, and
    the admin routes end to end).
    **"Revert & re-fetch from Brandfetch" (2026-08 follow-up)** — the
    "Revert to automatic" action originally only reset the DB row, leaving
    the actual re-fetch to whenever `scripts/backfill_logos.py --apply` was
    next run by hand; Brian asked for the button to also trigger a live
    Brand API call for that one row immediately. Two things had to happen
    for this to be safe. First, the Brand API call/asset-selection/download
    logic (`extract_domain`, `best_logo_asset`, `fetch_logo_asset` — with
    its domain-echo guard — `download_asset`) moved out of
    `scripts/backfill_logos.py` into a new shared `linklib/brandfetch.py`,
    so the script's own monthly batch run and the admin route call exactly
    one implementation rather than risk two copies drifting apart; the
    script now imports from there, with no behavior change (verified with
    the same preview/read-back conventions, plus a new
    `tests/test_brandfetch.py` covering the pure logic — including the
    domain-echo guard — against a fake HTTP session). Second, a new
    `webapp.app._live_refetch_logo(lib, kind, entity_id, entity)` helper
    powers the route: called AFTER `clear_tool_logo_override`/
    `clear_community_logo_override` (never before — the override flag is
    always dropped first), it makes one real, synchronous Brand API call for
    that row's domain and, on success, writes the result via the normal
    `set_tool_logo`/`set_community_logo` path (now safe again since the
    override was just cleared). On ANY failure along the way — no
    `BRANDFETCH_API_KEY` configured, the 100/month quota hit, no usable
    asset for the domain, or the asset download itself failing — the row is
    simply left reverted to automatic (already cleared moments earlier), so
    it can never end up worse off than a plain revert would have been; the
    next `backfill_logos.py` batch run can still pick it up later. Each
    failure mode gets its own explanatory message, shown as a banner on the
    edit page via new `logo_refetched`/`logo_refetch_msg` query params on
    `admin_tools_edit`/`admin_communities_edit` (green on success, coral on
    failure — same convention as the existing screenshot/research banners).
    The button's own label and helper copy were updated to say plainly that
    clicking it spends one real Brand API call right now, not a free,
    scheduled action. See `tests/test_logo_override.py`'s "Revert &
    re-fetch" section for the full coverage (success, no API key, quota,
    no-usable-asset, download failure, and the route end to end via a
    mocked `linklib.brandfetch`).
    `/tools/communities/gap` (the "not
    quite the right fit?" CTA on a profile page — currently a stub that redirects
    into `/contact` with the community pre-filled as context, pending the real
    Phase 5 gap-collection flow), `/tools/communities/correct` (per-listing
    "suggest a correction" — 404s without a valid `community_id`, since a
    correction is always about one specific listing), `/contact`, `/privacy`, `/play`, `/login`,
    `/static/*`, `/health`. (The old flat `/growth-engine-ratio`, `/finops-ai-hackathon`,
    `/netsuite-mcp` URLs 301-redirect to the nested paths above.)
  - *Member* (`_is_member` — any valid session): `/tools/fpa-buddy`,
    `/library/submit`. HTML pages redirect to `/login`; APIs return 401.
    (`/library/ask` and `/library/past-questions` were retired outright in
    the Library/Toolbox restructure's Phase 2 — FP&A Buddy moved to
    `/tools/fpa-buddy`, Past Questions folded into that same page as a
    "search past questions" section. No compatibility redirect, since
    nothing was bookmarked; the old flat `/ask` and `/questions` redirect
    stubs that used to point at them are gone too — `/questions` 404s,
    `/ask` 405s instead since `POST /ask`, the Q&A API, still lives at that
    exact path.)
  - *Admin* (`_is_authed` — session with `role=admin`): everything under
    `/admin/*`, plus admin-only actions on shared pages, plus **the digital
    Library**, now the merged Reader — `GET /read` (the three-pane shell:
    Feed/Archive/Read Later) and `GET /read/{article_id}` (the standalone
    single-article view) — tightened from member to admin-only back in
    Phase 1, carried into the Phase 5 Reader merge below. The old flat
    `/archive`, `/feed` URLs, and the pre-merge `/library/archive` and
    `/library/feed` routes they redirected to, are all gone outright as of
    the Phase 5 merge — no compatibility redirect, nothing was bookmarked.
    The `/library` hub route (a landing page linking to Archive/Feed/FP&A
    Buddy) was removed outright in Phase 1 — no redirect, nothing points to
    it anymore. This tier also includes two
    routes that live on the public `/tools/*` prefix rather than under
    `/admin/*` — `GET/POST /tools/software/{slug}/edit` and
    `GET/POST /tools/communities/{slug}/edit` (Phase 2), the full edit forms
    for a Software/Community entry. They moved off the old
    `/admin/tools/{id}/edit` / `/admin/tools/communities/{id}/edit` paths so
    a profile page's Edit button and its edit form share one slug-based URL
    family; `_is_authed` gates them exactly like every other admin route
    (redirect to `/login` on the GET, 401 on the POST) — the prefix changed,
    not who can reach it. Every other per-entry admin action (screenshot
    recapture, research refresh, agent-taxonomy verify, competitors,
    features, delete) stayed on its existing numeric-ID `/admin/tools/{id}/*`
    sub-route at the time and redirected back to the new slug-based edit URL
    on success. Those routes (except the legacy `tool_features` CRUD routes,
    later deleted outright rather than moved — Phase 1b PR 2) all later
    moved to `/admin/tools/software/{tool_id}/*` in the admin URL convention
    PR — see "Admin URL convention, Phase 1b PR 1" below.
- **Token auth in parallel**: `POST /save` is token-only
  (`X-Save-Token`/`?token=`) because the bookmarklet calls it cross-origin
  where the cookie can't be sent; member/admin APIs (`/ask`, `/api/search`,
  `/feed/save`) accept the token as an alternative to the cookie. All token
  comparisons are constant-time (`hmac.compare_digest`).
- If **no password is configured at all**, private routes are open — a
  local-development convenience, never the hosted configuration.
- Three middlewares wrap everything: a canonical-host 301 (www + legacy Railway
  hostname → apex, guarded so dev instances, `/health`, and `/admin/backup-now`
  never redirect — for proxied www traffic Cloudflare's edge Redirect Rule
  fires first, so this middleware is the backstop for the legacy hostname and
  direct-origin hits), `Cache-Control: no-store` on `/admin/*` (so task
  badges are never served stale from the back-forward cache), and (2026-08
  wrap-up sprint item 2) a `/save`-only CORS middleware (`_save_cors`) —
  confirmed broken in production, a syntax-corrected bookmarklet run from a
  real third-party origin (bolster.com) failed with `TypeError: Failed to
  fetch`, the classic CORS-rejection signature, since `/save` had never sent
  any `Access-Control-*` headers. It answers the JSON POST's real preflight
  `OPTIONS` request directly (204, `Access-Control-Allow-Origin: *`) and
  stamps the same allow-origin header onto the actual `/save` response
  (success or error), scoped to exactly this one path — no other route picks
  up a CORS header, since everything else on the site is same-origin
  cookie-authenticated. A permissive `*` origin is safe here specifically
  because `/save` already requires a valid save token to do anything (see
  `_check_token`) — same trust model as any other bearer-token API, and a
  `*`-origin response can never carry credentials anyway. The `/bookmarklet`
  snippet itself had an independent bug fixed in the same PR: one unbalanced
  closing brace (`body:JSON.stringify({url:u,tags:t})}}` closed the fetch
  options object twice before `.then` ever ran) made every copy of the
  snippet a silent no-op — a syntax error, never thrown anywhere visible.
  Fixed, plus a `.catch` added to the fetch chain so a network/CORS failure
  now alerts visibly instead of silently doing nothing.
- **A second capture path, `POST /save-later` + `/read-later-bookmarklet`,
  mirrors `/save`/`/bookmarklet` into `read_later` instead of `articles`.**
  Same token auth (`_check_token`), same CORS treatment (`_save_cors` now
  matches either path via `_TOKEN_ONLY_SAVE_PATHS`), same
  admin-gated-page/token-refreshed-per-request bookmarklet pattern. The real
  difference: `read_later` is `user_id`-scoped and a token-only request has
  no session to resolve one from (`_current_user_id` returns `None` for
  token-only access by design), so the write is attributed to
  `Library.default_admin_user_id()` — the earliest admin account, same
  query and reasoning `_migrate_read_later_user_scope`'s one-time backfill
  already used for the table's pre-multi-user rows. No enrichment happens —
  `read_later` has no tags concept, so the bookmarklet skips the tags prompt
  entirely and sends `document.title` along instead. **Content caching
  (2026-08 follow-up, see the "Read Later content caching + manual refresh"
  section above) — `add_read_later` is no longer a plain metadata insert**:
  `/save-later` now runs the same `fetch_page`/`extract_reader_html` pair
  `ingest_url` uses, best-effort, so an unread item opens instantly from its
  own cache instead of always live-fetching, and a missing `document.title`
  now also falls back to the fetched page's own title before ever reaching
  the Reader's "(no title)" placeholder.
- **`/admin/backup-now` is a deliberate, narrowly-scoped exception to the
  canonical-host redirect (Phase O).** The daily backup GitHub Action calls
  this one route directly on the legacy Railway hostname on purpose, to
  route around Cloudflare's Bot Fight Mode (see the "publicly reachable
  Railway origin" note above) — without this exception, the 301 the
  canonical-host middleware would otherwise issue silently defeats that,
  since the Action's `curl -f` treats a 3xx as success and never follows
  it. This is exactly what happened on the first live run after the Action
  was pointed at the Railway origin: `curl` reported success, but
  `backup_log` stayed empty, because the redirect meant `backup_now_route`
  never executed at all — caught only by checking `/admin/library/backup`'s
  banner directly rather than trusting the Action's exit code. The
  exemption is scoped to this exact path, not a general carve-out for
  token-authenticated routes — widening it needs the same deliberateness as
  adding it did.

## 4. Design decisions and their reasons

Short entries: what was decided, and why. Rationale below is taken from code
comments, docstrings, `CLAUDE.md`, and PR history — where the "why" isn't
recorded anywhere, it's flagged rather than invented.

- **Feed subscriptions live in the DB; `preferred_sites.opml` is generated from
  them, not edited.** `feed_sections` + `feeds` are the source of truth;
  `Library.write_opml()` regenerates the file on every mutation made from
  `/admin/library/feeds`, and the startup hook regenerates it again on every
  boot. *Why the inversion rather than editing the file in place:* the file
  lives inside the Docker image at `/app/preferred_sites.opml`, which Railway
  rebuilds on every deploy, so anything the running app wrote there would be
  destroyed on the next deploy and silently revert to the git copy — the same
  ephemeral-container trap that the Brandfetch logo backfill hit with
  `webapp/static/logos`. Regenerating at boot makes the file a pure cache of
  the database, so its ephemerality stops mattering, and all four consumers
  (`feed.parse_opml`, `sources.preferred_domains`, `queue.scan_feed_into_queue`,
  `authcheck`) keep reading it completely unmodified. Two guards keep the
  inversion safe in both directions: seeding from the existing file is
  **settings-flagged, not emptiness-checked** (an emptiness check looks
  identical on a fresh DB but would re-import the whole file on the next
  restart, resurrecting a deliberately deleted feed — the exact bug
  `_seed_toolbox` shipped and had to fix), and `write_opml` **no-ops on an
  empty feeds table** so a fresh deploy can't overwrite the curated repo copy
  before seeding runs. It also no-ops when the generated content matches
  what's already on disk, which keeps an ordinary boot from rewriting the file
  at all and keeps a test run from touching the repo's working copy.
- **`preferred_domains.cache_clear()` lives inside `write_opml()`, not at each
  call site.** `sources.preferred_domains` is `@lru_cache`d and read once per
  process, so regenerating the file without clearing that cache would leave
  FP&A Buddy's web-search allowlist stale until the next deploy, with no error
  anywhere. *Why fold it into the writer:* "the file changed, so the cache is
  stale" is an intrinsic invariant, not a per-route responsibility — six
  mutation routes each remembering to call it is six chances to miss one.
  `write_opml` skips the clear only in the branch where content was unchanged,
  where the cached value is by definition still correct.
- **Queue exclusion is a stored per-FEED boolean, not a name match and not
  per-section.** `feeds.exclude_from_queue` replaces the
  `QUEUE_EXCLUDE_CATEGORIES` set built from `LINKLIB_QUEUE_EXCLUDE_CATEGORIES`
  and matched against a section's *name*. *Why not a name match:* once sections
  became renameable from an admin page, the old shape meant renaming "News"
  would silently start funnelling News items into the archive queue — a
  data-affecting side effect of an edit that looks purely cosmetic. *Why per
  feed rather than per section:* a section is a display grouping, while "should
  this source be proposed into the archive queue" is a judgment about the
  source itself, so one feed can be read-only without dragging its
  section-mates along, and moving a feed between sections can't change its
  queue eligibility. `queue.scan_feed_into_queue` matches each item back to its
  originating feed via `feed_url` (the feed's own `xml_url`, carried on every
  item by `feed.get_feed_items`) rather than the item's `category` string;
  `scan_sitemaps_into_queue` matches each `FeedMeta.xml_url` the same way.
  `queue._excluded_feed_urls()` falls back to the two original News feed URLs
  when `Library.has_feeds()` is False (an unseeded DB or a fresh test fixture),
  since an empty exclusion set is otherwise indistinguishable from "nothing is
  excluded."
- **A feed's `xml_url` is stored and regenerated verbatim.** Nothing in the
  add, edit, move-section, or read-only path normalizes, trims, re-encodes, or
  rewrites it; the only transformation anywhere is `.strip()` for surrounding
  whitespace. *Why:* a paid subscription's feed URL can carry a per-subscriber
  token as a query parameter, and a "cleaned up" token is a silently dead feed
  with no error to notice. The per-row section dropdown and read-only checkbox
  are deliberately backed by narrow update methods
  (`Library.move_feed_to_section`, `Library.set_feed_excluded`) that touch one
  column each, so regrouping or flagging a feed cannot rewrite its URL in
  passing. `probe_feed` requests the URL exactly as entered and treats a query
  string as ordinary. Covered by round-trip tests in
  `tests/test_feed_management.py` against both the real stored URLs and a
  synthetic tokenized one.
- **`feeds.has_paywall_cookie` is a descriptive boolean, never a credential.**
  Three subscribed feeds (Mostly Metrics, Stratechery, Public Comps) return
  full text only when `extract.fetch_page` sends a cookie from
  `LINKLIB_AUTH_COOKIES`. That dependency was invisible from the admin table:
  a source could quietly stop returning full text with nothing on the page
  saying which env var to check. The column stores a boolean and the feed table
  renders it as a plain checkbox, visually identical to Read only and
  Subscriber. *The cookie value itself never enters the database*: it stays in
  the host env, nothing in the app writes it back here, and both the form's
  helper copy and the page footnote say so explicitly.
  *Why a note rather than migrating `feed.PAYWALLED_DOMAINS` itself:* that set
  still drives the Reader's paywall badge and `authcheck`'s probe list, and
  moving it into the DB is a behavioural change to three consumers; this column
  only adds an admin-visible label pointing at existing configuration. The two
  coexist — the note is seeded *from* `PAYWALLED_DOMAINS`, so the codebase's own
  list stays the single source of which domains are paywalled.
  *Why seeding is flag-guarded (`paywall_cookie_notes_seeded`) rather than
  emptiness-checked:* an empty note and a deliberately cleared one are
  indistinguishable, so an emptiness check would resurrect a cleared note on the
  next restart — the `_seed_toolbox` bug again. It also no-ops without burning
  its flag when the feeds table is still empty, so one boot with an unreadable
  OPML can't permanently skip every feed seeded afterwards.
  *Not in the OPML:* the file has no field for it, exactly like
  `exclude_from_queue`, so `opml_xml()` ignores it and ticking the box never
  churns the generated file.
  *Free text became a boolean.* The column started as `paywall_cookie_note`,
  per-row free text naming where the cookie lives. With exactly one cookie
  mechanism in the app, every row restated the same sentence, and free text
  invited typos and drift between rows. `has_paywall_cookie` records only
  *that* a feed depends on the cookie; the "where" is stated once in the page
  footnote. A boolean also makes
  it structurally impossible to paste a cookie value into the database, which
  the old free-text field could only warn against.
  `paywall_cookie_note` is **not dropped** — same non-destructive-retirement
  precedent as `screenshot_is_product` and `field_reviews`: it is the source
  `seed_paywall_cookie_flags` migrates from, and it stays as frozen history.
  Nothing reads it after that one-time conversion.
  *That seeder both migrates and seeds*, taking any feed with a non-empty
  legacy note **or** any feed on a `PAYWALLED_DOMAINS` domain. On an existing
  database both arms select the same three feeds; the domain arm exists so a
  fresh deploy isn't left with every box unticked.
- **Subscriber-cookie health is a persisted, always-on summary; the Cookie
  column is a computed declaration.** Two different things on the same page, so
  they are deliberately different shapes in different places. The column says
  "this domain currently has a cookie configured"; the summary panel under the
  page header says "here is whether that cookie still works". `authcheck.check_auth_cookies`
  probes one recent post per domain in `linklib.extract._COOKIE_DOMAINS` and
  persists the result to `settings.auth_cookie_status`, so the panel shows the
  last known result across reloads rather than only after a Re-check click.
  **2026-08 update — the `has_paywall_cookie` bullet above describes the
  feature as it originally shipped (a manually-ticked checkbox); it has since
  been superseded.** The checkbox recorded a feed's *declared* need for a
  cookie, never whether one was actually configured — a hint text on the form
  itself admitted "it changes nothing about how pages are fetched," and the
  two facts (declared vs. configured) could silently drift apart with nothing
  on the page able to tell you which. It's replaced with a computed, read-only
  indicator (`extract.has_configured_cookie(domain)`, checked live against the
  host environment on every page load) — there's no checkbox to tick any more.
  The `has_paywall_cookie` DB column and `paywall_cookie_note` before it are
  both left in the schema, unread going forward, per this repo's non-destructive-
  retirement precedent (`screenshot_is_product`/`field_reviews`) — see
  CLAUDE.md's `LINKLIB_AUTH_COOKIES` split note for the full write-up,
  including why the single `LINKLIB_AUTH_COOKIES` blob was itself split into
  one `LINKLIB_COOKIE_<DOMAIN>` variable per domain in the same change.
  *Why it exists:* previously only `ok: False` rendered anything — the page
  showed a coral panel when `stale_domains()` was non-empty and nothing
  otherwise, so a working cookie and one that could not be probed were both
  invisible. "Nothing on screen" meant both "healthy" and "no idea".
  Three states, one row each: `ok: True` -> green `#15803D`, `ok: False` -> red
  `#b91c1c`, `ok: None` -> amber `#CA8A04`. True stoplight values, a **sanctioned
  brand exception** registered in `brand_check.AUX_COLORS` and documented in
  BRAND.md §6 — the semantic `--good`/`--caution`/`--alert` triple was tried
  first and `--good` is navy, the site's dominant colour, so a healthy cookie
  read as ordinary text rather than a signal. Scoped to these three dots only.
  The red deliberately reuses the destructive-action `#b91c1c` rather than
  introducing a second red. **The amber is dot-only**: `#CA8A04` as text on
  `--surface` measures 2.94:1, under AA (4.5) and AA-large (3.0), so the state
  word beside it stays `--ink-soft` and the colour lives on the indicator.
  *Amber is strictly "could not be tested".* A passing check stays green
  however old it is — `checked_at` is rendered as relative text ("3h ago") so
  staleness is visible, but it is never promoted to its own colour. Loading the
  page still kicks a background re-probe when the stored record is missing or
  over 12 hours old, which is the actual staleness mechanism.
  *Keyed by domain, not by feed*, which is why it is a summary panel rather
  than a column in the feed table: cookies are configured per domain while the
  table is keyed per feed, so a per-row light would misstate the relationship
  the moment two feeds shared a domain. The coral expired panel still appears
  on top when something is broken, but now carries only the refresh steps — the
  per-domain detail it used to repeat is in the summary above it.
- **`feeds.has_active_subscription` is informational only — nothing reads it.**
  A per-feed note about whether Brian currently pays for that source. It does
  not gate fetching, does not reach the Reader, and is independent of both
  `paywall_cookie_note` and `feed.PAYWALLED_DOMAINS`. Wiring it to
  cookie-apply behaviour was discussed and deliberately dropped; that remains
  separate, later work. *Why record it at all:* "paywalled" and "subscribed"
  are different facts, and the table previously couldn't express the
  difference — Stratechery and Public Comps are paywalled (both carry a cookie
  note) but not currently subscribed, while Mostly Metrics is both. Seeded to
  exactly that state, flag-guarded (`active_subscriptions_seeded`) on the same
  reasoning as the cookie notes: an unchecked box and a deliberately unchecked
  one are indistinguishable, so a per-boot re-run would silently re-check a
  feed just unchecked. Also absent from the OPML.
  `tests/test_paywall_cookie_note.py::test_nothing_outside_the_admin_surface_reads_the_flag`
  pins the no-readers contract, so making it functional later has to be a
  deliberate change rather than something inherited from the column existing.
- **The Mostly Metrics feed carries no token, and the subscriber-access check
  confirms the stored URL is used verbatim.** A lot of care in this build went
  into guaranteeing a tokenized feed URL would survive seed -> DB -> OPML
  regeneration untouched. Worth recording plainly: **no feed in
  `preferred_sites.opml` has a query string at all**, Mostly Metrics included —
  it is stored as `https://www.mostlymetrics.com/feed` (34 characters). Its
  paywall is handled by a **cookie** (`LINKLIB_COOKIE_MOSTLYMETRICS_COM`, applied by
  `extract.fetch_page` on article pages), not by anything in the feed URL. The
  verbatim-URL guarantee is built and tested regardless, so it holds if a
  tokenized URL is ever added. Tracing `authcheck.check_auth_cookies` with its
  outbound requests recorded shows it requesting exactly
  `https://www.mostlymetrics.com/feed` — character-for-character the value in
  the `feeds` table — before probing a discovered article URL with the cookie.
- **Adding a feed validates the URL server-side before saving.**
  `feed.probe_feed()` fetches the candidate and confirms it parses as RSS or
  Atom, and rejects `feedly.com/web/...` proxy links by name. *Why the special
  case:* `feed._fetch_feed` skips those silently (they need a Feedly session),
  so one would otherwise save cleanly and then produce nothing forever, with
  the admin page showing a subscription that looks fine. A feed that parses but
  is currently empty is accepted — low-volume sources legitimately sit empty
  between posts. Editing re-probes **only when the URL actually changed**, so a
  rename or a section move doesn't fail because the source happens to be down
  that day.
- **Inline HTML in Python strings; no template framework.** The whole UI lives
  in `webapp/app.py` (~130 routes) as f-strings, with vanilla JS only where a
  page needs interactivity. *Why:* not explicitly recorded in the repo. It is
  consistent with the codebase's documented minimal-dependency ethos (see the
  stdlib-only auth note below), and it keeps every page greppable in one
  file — but treat that as inference, not recorded rationale.
- **SQLite + FTS5 on a Railway volume, not a hosted database.** *Why:* the
  scale is one curator plus a small member base; a single file needs zero
  operational overhead, backs up by copying (`/admin/library/backup/download-db`, daily
  Drive snapshots), and FTS5 gives ranked full-text search for free.
  `db.py`'s docstring records the exit path: the same schema works on
  libSQL/Turso/D1 later — only the connection changes.
- **Dollar-based rate limiting, computed from real token usage.** Each Ask
  turn records exact USD cost from the API response's token counts against a
  published-rate table (`linklib/pricing.py` — "no approximation"), and the
  monthly cap compares `SUM(cost_usd)` to a per-user cap. *Why:* query counts
  misprice reality — a Deep Opus answer costs ~60× a Quick Haiku one. Caps
  are data (a settings row + a per-user column), not logic, so a future paid
  tier is a different row, not a code branch.
- **Hybrid retrieval (FTS5 + vector search) inside the same SQLite file,
  merged by reciprocal rank fusion.** `sqlite-vec`'s vec0 virtual table lives
  in `library.db` alongside `articles_fts` — no separate vector database, no
  migration off SQLite. RRF (`agent._rrf_merge`, k=60) merges the two ranked
  lists rather than blending bm25 scores with cosine distances. *Why:* the
  two score types are on incomparable scales with no corpus-scale signal (a
  ~1,500-article library) to calibrate a blend weight against; RRF needs only
  rank position, so it's scale-free and deterministic without tuning. A
  network call can't run inside a SQL trigger the way FTS5 sync does, so
  embeddings are written from Python (`embed_article`, `embed_backfill.py`)
  and vector search is eventually consistent by design, not
  trigger-synchronous — an accepted tradeoff, not an oversight (issue #93).
- **Embedding cost is split by who pays for it.** Embed-on-save/backfill cost
  lives on `article_embeddings.cost_usd` and is never summed into
  `ask_questions`; embedding the retrieval QUESTION at ask-time is a
  user-cap cost and folds into `ask_questions.cost_usd` exactly like the
  follow-up rewrite's cost already does. *Why:* one is Brian's overhead (he
  chose to build the archive), the other is spend triggered by a member's own
  question — conflating them would either overcharge members for the archive
  existing or undercount what a heavy asker actually costs.
- **Enrichment cost gets its own ledger, not a shared one with embeddings
  (#105).** `linklib.enrich.enrich()`'s real Claude usage is recorded in
  `enrichment_cost`, a separate table from `article_embeddings` rather than a
  generalized `overhead_costs` schema covering both. *Why:* #105's Phase 0
  investigation found only one real precedent (`article_embeddings`) to
  generalize from, and it already differs from enrichment's needs in ways
  that matter — enrichment calls produce `output_tokens` (embeddings never
  do), and `enrichment_cost` is append-only (an article can be re-enriched)
  where `article_embeddings` is upserted (only the latest vector matters).
  Building a shared schema from one real case would have meant guessing at
  the shape of a second. Other Claude-calling modules (`dedupe.py`,
  `suggest.py`, `tagstyle.py`, `voice_review.py`) spend real API money with
  no cost capture at all today, but were deliberately left out of this
  ledger too — none share `enrich()`'s per-article entity shape (they're
  batch- or free-text-scoped), and two of them need bigger plumbing changes
  first (`dedupe.py` doesn't receive `Library` today and raises on failure;
  `voice_review.py` has no `Library` param and doesn't wrap its API call in
  try/except at all). A generalized overhead-cost schema stays deferred
  until a third real consumer needs one — see `/admin/overhead-spend` for
  the admin view surfacing both ledgers today.
- **`/admin/overhead-spend`'s "total cost of the site" is hand-entered, not
  derived from any ledger.** `manual_overhead` is a plain vendor/date/amount/
  category/note table, one row per real charge, filled in by hand from
  receipts — tax-inclusive, exactly what hit the card. It is the *only*
  input to the page's headline total. The token-cost ledgers
  (`article_embeddings`, `enrichment_cost`, `ask_questions.cost_usd`,
  surfaced together as "Toolbox usage") are shown alongside it but never
  summed in and never reconciled against it. *Why:* an earlier design tried
  to build the total out of the ledgers plus a tax markup, which meant
  picking a markup convention and made the total only as trustworthy as
  that reconciliation — deriving nothing means there's nothing to get
  wrong. `category` on `manual_overhead` is a free-text display/filter tag
  only (e.g. "Infrastructure" / "AI & API" / "Other"); it never partitions
  the total into sub-sums, so a new tag can't silently change what's
  counted. Vendor coverage, so a newly-added vendor has a checklist:
  Railway (hosting), Cloudflare (DNS/CDN), Google Workspace (the domain's
  email seat), domain registration, Anthropic (`ANTHROPIC_API_KEY` — funds
  both enrichment and FP&A Buddy), OpenAI (`OPENAI_API_KEY` — embeddings),
  Exa (web search) — every one of these gets its charges typed into
  `manual_overhead` by hand as bills arrive; none is auto-populated from a
  vendor API.
- **Citations link to original external URLs only; stored full text is never
  served to members.** The archive's `content` column is an internal
  grounding/search input; the member-facing surface is summary + tags +
  a link out. The Ask system prompt forbids verbatim reproduction, and
  `/admin/library` states the rule explicitly. *Why:* resale-safety — the
  archive is built from other people's articles, so the product is the
  curation and synthesis, never republication.
- **Server-held conversation history, reconstructed per request.** The
  `/tools/fpa-buddy` client sends only `conversation_id` + the new question; the server rebuilds
  the transcript from the conversation's `ask_questions` rows (which were
  already recording every turn) and enforces the follow-up cap by counting
  those rows. There is still no session store or in-memory conversation
  state — the DB rows *are* the state, re-read on each turn. *Why:*
  client-supplied history was both the resume blocker (a reload orphaned the
  conversation) and the trust gap (#96 — a client could fabricate or trim
  history); reconstruction closes both without adding any new
  infrastructure. History previously lived in the browser and was echoed
  back each turn — chosen then as the simplest stateless thing that worked.
- **Per-turn citation numbering.** Each answer's `[n]` markers resolve against
  that turn's own citation list (the frontend re-scopes `CITES` per response,
  and the resume flow replays each turn's `citations_json` snapshot the same
  way); numbering restarts every turn rather than accumulating across the
  conversation. *Why:* each turn's list is verified against that API
  response's citation metadata, and the persisted snapshot is per-turn —
  conversation-global renumbering would mean rewriting stored answers'
  markers whenever a conversation continues.
- **Additive-only schema migrations.** Boot runs `CREATE TABLE IF NOT EXISTS`
  then a list of `ALTER TABLE ADD COLUMN`s that swallow "duplicate column"
  errors; columns are never dropped (dead game columns are kept and labeled).
  Indexes on migrated-in columns must go in `_POST_MIGRATION_INDEXES`, never
  in the schema script — the comment in `db.py` records the real incident: an
  index in the schema referencing a not-yet-migrated column crashed every
  boot against pre-existing DBs. *Why:* no migration tooling, one writer, and
  a production DB that predates most columns — additive is the only shape
  that's always safe to re-run.
- **URL is the natural key, aggressively normalized.** `normalize_url` forces
  https, strips `www.`, fragments, tracking params, and trailing slashes
  before any insert; upserts merge tags and fill blanks. *Why:* the same
  article arrives from Feedly boards, share links, and feeds — idempotent
  re-imports and safe double-saves matter more than surrogate-key purity.
- **Single-process in-memory state.** Long-running admin jobs
  (enrich/backfill progress), the `/contact` rate limiter, and the 30-minute
  feed cache are all module-level dicts behind locks. *Why:* the deployment
  is one Railway instance; a queue or Redis would be pure overhead. This is a
  standing constraint: the app does not scale horizontally without moving
  that state.
- **Best-effort everywhere an AI or email call rides along a user action.**
  Enrichment, the follow-up rewrite, embed-on-save/query-embedding, citation
  assembly, and every outbound email are wrapped so failure degrades
  (unenriched row, raw-question retrieval, FTS5-only retrieval, uncited text,
  logged failure) instead of blocking the save or the answer. Failures that
  need a human land in durable tables (`email_failures`) with admin badges —
  best-effort must not mean silent.
- **Seed once for some fields, sync forever for others.** Tool categories,
  community categories, and game tuning are seeded on first boot from source
  constants and never re-synced — admin edits survive every deploy, source
  constants are just day-one data. Tools, benchmarks, and communities are
  different: their `name`/`description` (or `name`/`notes` for communities,
  plus the `advisor` flag on both tools and communities) re-sync from the
  source constants (`scripts/seed_tools.py`'s `TOOLS`, `webapp/app.py`'s
  `_DEFAULT_BENCHMARKS`, `scripts/seed_communities.py`'s `COMMUNITIES`) on
  every startup, while every other field (categories, promoted/featured,
  vendor/warm-intro, coverage, pricing, reach, local_markets, cost_band,
  access, sponsorship) stays admin-owned and untouched. *Why:* content
  (name/description) and the advisor disclosure are meant to be maintained
  in the source list and pushed live by deploying, per #113; everything else
  is meant to be edited live via the admin UI. Each narrow sync method
  documents which fields it touches.
- **Effort tiers instead of a model picker.** `/tools/fpa-buddy` exposes
  Quick/Standard/Deep; the model behind each tier is an implementation detail
  (`EFFORT_SETTINGS`). *Why:* members shouldn't need model literacy to make a
  cost/quality choice (PR #84 collapsed the previous model+effort UI). The
  tiers render as single-select buttons in an `.ask-controls` two-column grid
  beside the Sources multi-select, both built from the same `.ask-tag`
  component so the two carry equal visual weight; each tier's retrieval counts
  and token estimate live in a hover `title` rather than persistent subtext.
  *Why:* three tall descriptive cards spent a lot of vertical space on a
  one-of-three choice. The shared component carries one hazard worth knowing
  about: Depth's buttons share `.ask-tag.active` with Sources, so anything
  reading the selected sources must scope to `.ask-tag[data-source].active` or
  it sweeps the selected tier in as a null source.
  Elsewhere, enrichment model pickers stay curated (no auto-surfacing of new
  models) so a whole-archive re-enrich can't accidentally target a pricey new
  model.
- **stdlib-only auth primitives.** HMAC-signed cookie, scrypt password
  hashing, constant-time comparisons — no `itsdangerous`, no
  SessionMiddleware. *Why:* recorded in `CLAUDE.md`: one fewer dependency for
  a small, well-understood surface.
- **The game is client-authoritative.** `/play` is a DOM+CSS game; scores are
  client-reported and only bounds-checked. *Why:* recorded in the schema
  comment — server-side simulation isn't worth it for a leaderboard among
  members; the trust model is stated, not accidental.
- **Voice is two DB-backed settings, not a hardcoded constant (#95).**
  `voice_core` (mechanics + tone, persona-neutral) and `voice_fpa_buddy`
  (FP&A Buddy's analyst-specific register, appended after core) replace the
  old `BRIAN_VOICE_CORE` append that `_build_system` used to pull from
  `linklib/social.py`. *Why:* Phase 0 investigation (#95) found first-person
  Brian phrasing — asserted personal experience, personal-interest metaphors
  — actively conflicted with the prompt's citation-grounding rules; an
  assistant citing someone else's saved articles can't also claim to have
  personally done the thing it's citing. Splitting into two fields (rather
  than one combined constant) lets `voice_core` double as the `/admin/voice`
  reviewer's "General / site copy" rubric on its own, while `voice_fpa_buddy`
  stays specific to the analyst persona. Both are editable live from
  `/admin/voice` with no redeploy, each falling back to a code-constant
  default when the field is empty — the reviewer, the fallback panel on
  `/admin/voice`, and `_build_system` all read the same live settings, so
  there's no separate hardcoded copy to drift out of sync. LinkedIn/social
  generation (`linklib/social.py`, `scripts/post.py`) was removed entirely in
  the same change — superseded by Brian's `write-like-brian` skill used
  directly in Claude, so the app no longer needs its own drafting surface.
- **The backup trigger is a scheduled GitHub Action, not a Railway cron
  service or an in-process scheduler (Phase O).** A Phase O investigation
  found the Drive backup mechanism itself (`linklib/backup.py`) was real and
  working, but had never actually been *scheduled* — it only fired as a
  debounced side effect of ~18 unrelated admin/save routes, which in
  practice went weeks without tripping. *Why a GitHub Action over the
  alternatives:* it reuses the existing `POST /admin/backup-now` route and
  auth verbatim (no new code path), needs no second Railway service, and its
  own run history in the Actions tab is a second, independent visibility
  layer beyond the in-app `backup_log` table — if the site itself is down,
  the Action still fails visibly even though the app never got the chance
  to write a log row. `/admin/backup-now` now returns a real non-2xx status
  on failure (`503` not configured, `502` upload failed) instead of always
  `200`, specifically so `curl -f` in the Action (and any future monitoring)
  can tell success from failure without parsing HTML. **Targets the Railway
  origin, not `bmweis.com`** — see the "publicly reachable Railway origin"
  bullet above for why; the first live verification run against the
  Cloudflare-fronted hostname got a `403` from Bot Fight Mode before ever
  reaching the app.
- **The Drive backup folder is created and owned by the app, never a
  folder made by hand (Phase O).** The OAuth refresh token is minted with
  the `drive.file` scope — deliberately the narrowest Drive scope, not
  full `drive` access — which only grants visibility into files/folders
  *the app itself created via the API*. The original setup pointed
  `GOOGLE_DRIVE_FOLDER_ID` at a folder created by hand in the Drive web
  UI; every upload against it got a `404` (Google's Drive API returns 404,
  not 403, for a resource the caller can't see — deliberately, to avoid
  confirming it exists), even though the account and folder id were both
  correct. Fixed by having `backup_now()` create and remember its own
  folder instead: `linklib.backup._resolve_folder_id` reuses an id already
  persisted in `settings` (`backup_drive_folder_id`) if one exists, or
  creates a folder named "CFO Navigator — Library Backups" in My Drive
  root on first use and persists its id for every run after.
  `GOOGLE_DRIVE_FOLDER_ID` still overrides this if set (e.g. a folder
  explicitly granted to the app some other way, such as a Drive Picker
  consent flow), but the default is now a folder the app can actually
  write into. `linklib.backup.known_folder_id` is the read-only lookup
  `/admin/library/backup` uses to show the live folder link — it never
  creates a folder as a side effect of a page view, only `backup_now()`
  does, mid-upload.
- **Phase G: the Agent taxonomy "unverified" banner's call-to-action was
  unconditional, the button it referred to wasn't.** The green banner shown
  after `/admin/tools/{id}/research/refresh` used to always say "review the
  drafted feature rows and agent taxonomy below before marking them
  verified," regardless of whether anything actually ended up flagged
  `needs_verification` — `agent_taxonomy_needs_verification` is set per-run
  from the LLM's own self-reported confidence
  (`agent_taxonomy.get("confident")` in `linklib/enrich.py`), so a confident
  run left the flag (and the "Mark verified" button, and every public
  "unverified" badge) at 0 while the banner still promised a step with
  nothing on the page to do it. Fixed by making the clause conditional on
  the same flags `_features_badge_html`'s "N needs verification" text
  already checks (`tools.agent_taxonomy_needs_verification` OR any feature
  row's `needs_verification`), rather than only on whether the refresh
  itself succeeded. Also added `narrative_review_log` (see §2) so the "Mark
  verified" click leaves an auditable "Verified by X on Y" trail, the same
  way `tool_audit_log`/`community_audit_log`/`backup_log` already do for
  their respective actions — investigated but explicitly not built in this
  pass: extending that same flag-plus-log pattern to Description,
  Differentiation, and the Community profile draft, since those three
  already have a *different*, pre-existing review mechanism
  (`field_reviews`, added Aug 1 2026) that a straight copy of the
  Agent-taxonomy pattern would duplicate rather than extend — see CLAUDE.md's
  Phase G note for the reconciliation question this raises before that
  follow-up gets built.
- **Phase G PR 2: field_reviews retired (frozen, not dropped) for
  Description/Differentiation/Community profile; Community profile draft
  reuses `needs_review` instead of a fourth column.** Resolution of the PR 1
  reconciliation question above, confirmed by Brian after reviewing a
  production row count: no backfill of `field_reviews`' existing rows into
  `narrative_review_log` (the two tables record structurally different
  things — "saved after a Generate click" isn't "a human confirmed it," and
  translating one into the other would misrepresent history) — `tools.
  description_needs_verification`/`competitive_differentiation_needs_verification`
  (new columns, default 0, same non-retroactive-flagging precedent as
  `agent_taxonomy_needs_verification`'s own migration) start every existing
  row unflagged, and only the *next* AI draft of each field trips the gate.
  `field_reviews` itself stays in the schema, frozen as of this cutover for
  the fields it no longer tracks — same non-destructive-retirement
  precedent as `screenshot_is_product` (§2) — while still growing normally
  for everything not covered by this phase (the Community "Auto-fill from
  URL" listing fields, competitor-match suggestions on both entity types).
  A second structural finding surfaced mid-build, specific to the Community
  profile draft: `community_profiles.needs_review` already existed as a
  working whole-profile "flag for later" mechanism (its own "Mark reviewed"
  button, admin-list badge, count, and `?filter=needs_review` view) —
  manual-only, never auto-set by AI generation. Building a fourth,
  separate `community_profile_needs_verification` column alongside it would
  have put two near-identical badges on the same admin row for related
  concerns; Brian's call was to reuse `needs_review` instead (see §2's
  `community_profiles` entry for the exact mechanics) rather than build
  past a working mechanism a second time — the same "flag it, don't build
  around a bad fit silently" instinct that caught the `field_reviews`
  overlap in the first place. `_narrative_verify_widget` (`webapp/app.py`)
  generalizes the badge/button/hidden-form/review-line markup PR 1 built
  once for Agent taxonomy into one shared helper, reused for Description
  and Differentiation directly and, in reduced form (button + review line
  only, no badge — the existing checkbox already shows state), for the
  Community profile draft.
- **Library/Toolbox restructure, Phase 2 — FP&A Buddy relocated to
  `/tools/fpa-buddy`; Past Questions folded in as a helpful-only search.**
  Second phase of the Phase 1 restructure (§4's Member/Admin tier list
  above has the full route/redirect accounting). FP&A Buddy moved out from
  under Library into the Toolbox area at a new URL, replacing
  `/library/ask` — stays member-gated, unlike Archive/Feed's Phase 1 move
  to admin-only, since it's meant for a small group of signed-in friends,
  not just Brian. `/library/past-questions` (a standalone browse page, no
  rating filter) was retired and its functionality folded into a "search
  past questions" section on the same page — but with a real behavior
  change, not a straight copy: `Library.list_public_ask_questions` gained a
  `helpful_only` parameter (an `EXISTS` subquery against `ask_feedback`,
  not a join, so a question with several raters — some possibly rating it
  `inaccurate` — still surfaces exactly once as long as any single rater
  called it `helpful`) that the new section always passes `True`, so a
  member searching there only ever finds answers someone already vouched
  for. No dedup on repeated question text — deliberately deferred; the
  existing page never deduped either, and doing it well would need more
  than exact-string matching (near-duplicate phrasing, or two genuinely
  different answers to a similarly-worded question) to be worth the
  complexity. `POST /ask` (the API) didn't move — only the page that calls
  it did. The route move also caught a stale leftover from Phase 1: the
  dead `nav.site-nav a[href="/ask"]` CSS selector (Phase 0 flagged it,
  Phase 1 explicitly deferred cleanup here since this phase already
  touches that code) is gone now too.

## 5. Directory map

```
webapp/
  app.py                    # THE app: ~130 routes, all inline HTML/CSS/JS, auth, admin hub
  checks.py                 # aggregates automated checks for /admin/checks (mirrors CI)
  tasks.py                  # open-task badge counts for the admin hub
  thought_leadership_data.py# curated content for /thought-leadership
  static/                   # served assets (headshot etc.) via GET /static/{filename}
linklib/                    # the core library — everything durable lives here
  db.py                     # SQLite + FTS5 + sqlite-vec schema, migrations, Library class — the spine
  agent.py                  # FP&A Buddy: hybrid retrieval (FTS5+vector, RRF), rewrite, Citations
                            #   API, cost capture; VOICE_CORE_DEFAULT/VOICE_FPA_BUDDY_DEFAULT (#95)
  embeddings.py             # OpenAI text-embedding-3-small: document text, content hash, embed calls (#93)
  pricing.py                # exact per-call USD cost from real token usage (Claude + embeddings)
  pipeline.py               # shared ingest (fetch → upsert → enrich → embed) for CLI and web
  enrich.py                 # Claude summary + auto-tags per article
  extract.py                # full-text fetch (trafilatura preferred, BS4 fallback)
  archive.py                # parses the one-time Feedly "Download your data" export
  feed.py                   # RSS/Atom reader over the OPML list (concurrent, 30-min cache)
  sources.py                # preferred_sites.opml → web-search domain allowlist
  queue.py                  # fills the Archive Queue from RSS (ongoing) + sitemaps (backfill)
  suggest.py                # advisory Claude keep/skip predictions for queue candidates
  dedupe.py                 # near-duplicate detection (similarity + Claude verification)
  tagstyle.py               # learns the curator's tagging style; feeds enrichment
  models.py                 # curated model registry reconciled with the live Models API
  passwords.py              # scrypt hashing (stdlib only)
  email_utils.py            # outbound email via Gmail REST API (Railway blocks SMTP)
  backup.py                 # off-site DB snapshot to Google Drive; every attempt logged to backup_log (Phase O)
  authcheck.py              # probes paywall auth cookies so a stale one surfaces
  brand_check.py, voice_review.py  # deterministic BRAND.md palette/voice checks
scripts/                    # CLI entry points (import, add_link, enrich_backfill,
                            #   embed_backfill, ask, seed_tools, backfill_queue,
                            #   mcp_server, …) — full inventory of the recurring/
                            #   diagnostic set (purpose, cadence, env vars, invocation)
                            #   is the live registry at /admin/system/scripts (Phase N)
  archive/                  # one-time migrations + closed-investigation reports,
                            #   job done, kept only for git history (Phase N)
tests/                      # pytest suite run by CI (.github/workflows/qa.yml)
preferred_sites.opml        # dual-purpose: web-search allowlist AND /read's Feed view subscriptions
Dockerfile, Procfile, railway.toml  # Railway deploy (uvicorn, /health healthcheck)
CLAUDE.md, BRAND.md         # working agreements: context for agents, design system
```

## 6. Known limitations / deferred work

- **Citation markers in the admin CSV export are literal text, on purpose.**
  The server-rendered surfaces (`/ask/history`, `/tools/fpa-buddy`'s
  past-questions section, `/admin/ask-feedback`) now linkify `[n]` markers via the shared
  `_render_cited_answer` helper (see §3), but the CSV keeps raw `[1]`/`[2]`
  markers deliberately — no link conversion in a CSV — with a trailing
  plain-text `citations` column resolving them. Don't "fix" the CSV markers
  later. Turns recorded before the snapshot existed (`citations_json='[]'`)
  still render their markers as plain text everywhere — those citations were
  never stored and can't be recovered.
- **Library retrieval is hybrid (FTS5 + vector, #93); feed matching is still
  keyword-only.** `agent.retrieve` merges FTS5 keyword search with
  `sqlite-vec` semantic search via reciprocal rank fusion, but
  `retrieve_feed`'s keyword overlap is unchanged — feed items are transient
  (30-minute cache, no stable IDs) and were deliberately excluded from
  embedding, so a feed-only question phrased entirely in synonyms can still
  miss relevant items even though library retrieval no longer has that gap.
- **Vector search is eventually consistent, not trigger-synchronous like
  FTS5.** A SQL trigger can't make a network call, so an article is
  immediately findable via FTS5 on save but only findable via vector search
  once its embed call has actually completed — inline (embed-on-save,
  best-effort, can fail silently) or via `scripts/embed_backfill.py`. An
  article whose embed-on-save call failed (no `OPENAI_API_KEY`, a transient
  API error) stays FTS5-only until the next backfill run; there's no retry
  queue or admin visibility into which articles are in that state yet.
- **Overhead cost is tracked for embeddings and enrichment; other
  Claude-calling modules aren't yet (#105).** `article_embeddings.cost_usd`
  and `enrichment_cost.cost_usd` cover embed-on-save/backfill and enrichment
  spend, surfaced together on `/admin/overhead-spend`. `dedupe.py`,
  `suggest.py`, `tagstyle.py`, and `voice_review.py` still spend real API
  money with zero cost capture — deliberately out of scope for #105's build
  (see §4, "Enrichment cost gets its own ledger, not a shared one"), a
  natural follow-up if/when tracking their spend matters enough to justify
  the plumbing each one needs.
- **The `/contact` rate limiter can be evaded via the origin.** `_client_ip`
  prefers `CF-Connecting-IP` (set authoritatively by Cloudflare on proxied
  traffic — Cloudflare only *appends* to `X-Forwarded-For`, so XFF's first
  hop is client-forgeable even through the proxy), falling back to the first
  `X-Forwarded-For` hop, then the socket peer. The residual gap: the Railway
  origin is directly reachable (bypassing Cloudflare — see the deployment
  notes), and a direct hit can forge either header. Accepted risk, same as
  the origin-exposure note above; a Cloudflare Tunnel is the real fix.
- **Single-instance assumptions.** Job progress, the contact rate limiter,
  and the feed cache are in-process memory; SQLite is a local file. Scaling
  beyond one instance means externalizing all of that.
- **Model pricing is a manual table.** `pricing.py` has no live pricing API to
  reconcile against; a stale row silently mis-records spend. Standing example:
  Sonnet 5's introductory pricing row must be hand-edited after 2026-08-31.
- **iOS Share Sheet shortcut** (from the original migration plan) remains
  unbuilt; capture from a phone goes through the bookmarklet.
