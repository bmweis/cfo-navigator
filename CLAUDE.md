# CFO Navigator — Claude Code context

## What this is

A personal finance research tool for Brian Weisberg (brian.weisberg@gmail.com).
Two capabilities, both backed by a single SQLite database (`library.db`):

1. **CFO Library** — searchable archive of saved articles (imported from Feedly or
   captured going forward). SQLite + FTS5 full-text search is the spine.
2. **CFO Navigator** — FP&A Q&A chatbot. Retrieval-augmented: pulls the most relevant
   saved articles + fresh web results from trusted sites, then synthesizes a cited answer
   via Claude.

(A third capability, LinkedIn post drafting, had a web UI at `/admin/social` and `/draft`
— both were removed, and the underlying generator (`linklib/social.py`, `scripts/post.py`)
was removed entirely in #95 (superseded by Brian's `write-like-brian` skill used directly
in Claude). There's no LinkedIn/social generation surface anywhere in the app anymore.)

The site is live at **bmweis.com** (custom domain on Railway, July 2026): a
public-facing bio/thought-leadership section and a login-gated private section for
the library, chatbot, and feed.

## Project layout

```
linklib/           # core library (the only thing that matters long-term)
  db.py            # SQLite + FTS5 schema, Library class, Article dataclass — the spine
  archive.py       # parses Feedly "Download your data" bookmark HTML export
  extract.py       # best-effort full-text fetch (trafilatura preferred, BS4 fallback)
  enrich.py        # Claude API: generates summary + auto-tags for each article
  pipeline.py      # shared ingest used by CLI and web app; also embed-on-save (embed_article)
  agent.py         # FP&A Buddy Q&A: hybrid library retrieval (FTS5 + vector, RRF-merged) + web
                   #   search, cited answer; also holds the voice_core/voice_fpa_buddy defaults
  embeddings.py    # OpenAI text-embedding-3-small: document-text builder, content hashing, embed calls
  sources.py       # parses preferred_sites.opml → domain allowlist for web search
  feed.py          # RSS/Atom reader over the OPML list: concurrent fetch, 30-min cache
  models.py        # curated Claude model registry, reconciled with the live Models API
  pricing.py       # per-call USD cost table → per-user FP&A Buddy budget caps
                   #   (queue.py/suggest.py — the Archive Queue's RSS/sitemap scan +
                   #   advisory keep/skip predictor — retired 2026-09, see below)
  dedupe.py        # near-duplicate detection (similarity + Claude verification)
  tagstyle.py      # learns Brian's tagging style; feeds the enrichment prompt
  passwords.py     # scrypt password hashing (stdlib only)
  authcheck.py     # probes subscriber-auth cookies so a stale paywall cookie surfaces
  backup.py        # off-site snapshot to Google Drive (OAuth refresh token, no SDK); logs
                   #   every attempt (success/failure) to backup_log — see Phase O below
  email_utils.py   # outbound email via the Gmail REST API (NOT SMTP — Railway Hobby
                   #   blocks SMTP ports; same OAuth client as backup.py)
  brand_check.py   # deterministic scanner for BRAND.md palette/font rules
  voice_review.py  # mechanical + Claude voice-drift checks against BRAND.md

scripts/           # CLI entry points
  import_archive.py   # one-time Feedly archive import
  add_link.py         # save a single URL
  enrich_backfill.py  # backfill Claude summaries/tags over imported rows
  enrich_compare.py   # manual QA: compare enrichment quality across models on one article
  embed_backfill.py   # one-time, batched: embed existing articles for semantic search
  eval_retrieval.py   # manual QA: replay flagged Ask questions through FTS5-only vs.
                      #   hybrid retrieval side by side (no Claude calls)
  backfill_queue.py   # one-time sitemap sweep to queue historical articles
  seed_tools.py       # seed the CFO Toolbox vendor list — run once, by hand, against a
                      #   fresh DB. TOOLS is also imported by webapp/app.py's startup
                      #   hook, but only to sync name/description/advisor on rows that
                      #   already exist; the hook never inserts, so a deliberately
                      #   deleted tool doesn't come back on the next restart
  ask.py              # FP&A Buddy from the terminal
  voice_review.py     # check a file/stdin against the voice standards
  mcp_server.py       # stdio MCP server wrapping GET /api/search for Claude Desktop/Code
                      #   (untouched by the /mcp remote server below — see that bullet
                      #   for how the two relate; this one stays as-is for now)
  mint_api_token.py   # mint/list/revoke a /mcp API token — human-run via `railway ssh`
  archive/            # (Phase N) one-time migrations and closed-investigation reports
                      #   whose job is done — kept for history via `git mv`, never run
                      #   again in the ordinary course

# The full recurring/actively-useful script inventory (purpose, cadence, required env vars,
# exact invocation) lives at /admin/system/scripts, not here — that admin page is the live
# reference; this tree only sketches scripts/'s shape. See "Documentation" below for the
# standing rule that keeps that page in sync with scripts/.

webapp/
  app.py           # FastAPI, ~110 routes, all HTML/CSS/JS inline: public site
                   #   (/, /thought-leadership [+ /thought-leadership/growth-engine-calculator,
                   #   the one remaining literal bespoke /thought-leadership/* route — see
                   #   Original Content Phase 4c],
                   #   /tools, /contact, /play) + private tools
                   #   (/tools/fpa-buddy, /save, /api/search, /bookmarklet — plus
                   #   the merged Reader, /read and /read/{article_id}, admin-only)
                   #   + auth (/login, /logout) + the /admin back office (~40 pages)
                   #   + /mcp (Phase 1) — the remote MCP server, mounted in-process
  mcp_server.py    # webapp's own module (distinct from scripts/mcp_server.py above) —
                   #   builds the FastMCP instance + its three read-only introspection
                   #   tools; webapp/app.py mounts it at /mcp (Phase 1)
  checks.py        # aggregates the automated checks for /admin/checks (mirrors CI)
  tasks.py         # open-task badge counts for the admin hub
  thought_leadership_data.py  # curated content for /thought-leadership's lists
  static/          # served assets (e.g. headshot.jpg) via GET /static/{filename}

preferred_sites.opml  # subscription list: web-search allowlist AND the /feed reader source
library.db            # NOT in git (personal data, large). Lives beside the code locally.
```

## Key architecture decisions

- **URL is the natural key.** Every upsert merges tags and fills empty fields rather
  than duplicating. Re-running the import or saving an article from two different boards
  is always safe.
- **FTS5 triggers keep the index in sync** — inserts/updates/deletes on `articles`
  cascade automatically; no manual reindex needed.
- **Enrichment is additive.** `enriched=0` rows get a Claude summary + tags later via
  `enrich_backfill`. The import does not require an API key.
- **Web search is domain-restricted, whichever mechanism handles it.** Exa
  is preferred — `agent.py`'s `retrieve_exa()` passes `preferred_sites.opml`
  domains as Exa's `includeDomains`, riding as Citations-API document blocks
  like library/feed retrieval, not a model-invoked tool. But it's a kill
  switch, not the only mechanism (Phase 7): when `exa_enabled` is off
  (`/admin/exa-settings`) or `EXA_API_KEY` is missing, Claude's native
  `web_search_20250305` tool steps in instead, restricted by the same OPML
  list via `allowed_domains` — restored to exactly its pre-Phase-2 shape,
  not rebuilt from scratch. Exactly one mechanism runs per turn
  (`agent._web_provider`); either way the chatbot only cites sources Brian
  already trusts.
- **Library retrieval is hybrid: FTS5 + vector search, merged by reciprocal rank
  fusion.** `sqlite-vec` adds a vec0 virtual table (`articles_vec`) inside `library.db`
  — no separate vector database. Embeddings (OpenAI `text-embedding-3-small`) can't be
  written from a SQL trigger the way FTS5 is (a network call can't run inside a
  trigger), so vector search is eventually consistent by design: embed-on-save
  (`pipeline.embed_article`, best-effort, never blocks a save) handles new articles,
  and `scripts/embed_backfill.py` is the one-off batched pass for the existing corpus
  and for anything embed-on-save missed. A content hash on `article_embeddings` detects
  when an article's embeddable text has changed, so re-running the backfill only
  re-embeds what's actually stale.
- **FP&A Buddy Published-Content Ingestion (2026-09) — Brian's own writing joins
  retrieval by mirroring `original_content` into `articles`, not a parallel
  index or a fourth retrieval branch.** Investigated first: neither
  `original_content` (the 3 native `/thought-leadership` pieces) nor
  `thought_leadership` (the ~30 rows describing externally-hosted work) was
  ever in Buddy's Library/Feed/Web retrieval path, and bmweis.com can't be
  self-fetched (Cloudflare Bot Fight Mode), so a URL-fetch ingestion path
  can't reach the 3 native pieces at all — `linklib/original_content_sync.py`'s
  `sync_original_content_article()` mirrors `original_content.body_md`
  directly into `articles` instead, called synchronously right after each
  admin save (`POST /admin/thought-leadership/original/new`/`{id}/edit`), same
  "regenerate at the mutation point" convention `write_opml()` established.
  A new `original_content.mirrored_article_id` tracks the mirror; re-syncing
  on edit is a direct overwrite (`Library.update_mirrored_article`), never
  `Library.upsert()`'s merge-keeps-existing-content semantics, which are
  right for an external re-fetch but wrong for a deliberate edit. Clearing
  `body_md` back to `NULL` (card-metadata-only) deletes the mirror outright
  rather than orphaning it, and the admin delete route cascades the same
  way — CLAUDE.md's "No dead data" rule. Indexed text comes from
  `plain_text_from_body_md()`: the same `python-markdown` render the public
  page uses, then stripped with BeautifulSoup using a plain `" "` separator
  — deliberately not the newline-losing-paragraphs bug the Reader's own
  `get_text(" ", strip=True)` had (that was about *display* text; this is
  *index* text, where a space separator is actually correct — it keeps an
  inline run's words together while still preventing adjacent block
  elements from gluing at a tag boundary). **Provenance, not priority — the
  rule this feature exists to enforce**: a new `articles.is_own_content`
  flag is read ONLY by `linklib.agent._build_source_documents`/
  `linklib.citations.extract_citations`, for citation labeling — nothing in
  `retrieve()`/`_rrf_merge()`/`Library.search()`/`Library.vector_search()`
  reads it, so a mirrored article surfaces and ranks purely on merit, same
  as any other. A cited own-content source gets a small "(own writing)"
  label in the citation list (both `srcListHtml`'s client-side render and
  `_render_cited_answer`'s server-side one) — **deliberately not an inline
  first-person prose mention** ("as I wrote…"): flagged and explicitly
  rejected as a real voice-integrity risk during design, since first-person
  narration about Brian's own writing is exactly the kind of thing that
  could land off-register in front of a real reader; revisit only if the
  citation label alone proves too quiet. The provenance flag is also set
  generically for later use: `pipeline.ingest_url()` checks every save's
  URL against `thought_leadership.url` (`Library.is_thought_leadership_url`,
  normalized both sides) and flags a match — this is how the ~9
  externally-hosted, text-fetchable `thought_leadership` pieces will get the
  same label once Brian bookmarklet-saves them (unchanged save flow — no
  code path beyond this generic match needs his attention). Set-only, like
  `needs_content_check` — never cleared once learned. See ARCHITECTURE.md's
  "Published-content ingestion" write-up (under FP&A Buddy) for the full
  mechanism and `tests/test_original_content_ingestion.py` for coverage,
  including the RRF-non-interference regression.
- **Mirror-consistency gap, found and closed (2026-09) — the sync stays in
  the routes, not the data layer; a real invariant on `/admin/checks`
  closes the gap instead.** Two ported pieces (`ai-hackathon-playbook`,
  `netsuite-mcp`) sat live with real `body_md` and no working `articles`
  mirror for roughly three weeks: `sync_original_content_article()` only
  runs from the two admin save routes, and both `scripts/archive/migrate_
  hackathon_playbook_content.py`/`migrate_netsuite_mcp_content.py` wrote
  `body_md` directly via `Library.update_original_content()`, bypassing it.
  Both rows self-healed the instant an admin opened them and clicked
  Save. **Considered and rejected: moving the sync into
  `Library.update_original_content()`/`add_original_content()` itself** —
  a real circular import (`db -> original_content_sync -> db`, workable
  only via a lazy import, unlike the one existing precedent for a
  write-time side effect baked into `db.py`,
  `voice_mechanics.normalize_voice_mechanics`, which is pure/synchronous/
  zero-I/O and categorically simpler than "insert/update/delete a row in a
  different table, cascade FTS/vector/citation rows, attempt an OpenAI
  embedding call"); at least seven test fixtures call `add_original_content`/
  `update_original_content` directly to seed unrelated tests, with no
  interest in a mirroring side effect; and every other cross-cutting side
  effect of this shape in this codebase (`write_opml()`, the AI-drafted-field
  generate-then-persist flows) is triggered from the route layer, not
  automatically inside the write method. **Instead: a real, mechanically-
  enforced, no-judgment-call check** — "Original content mirrored for
  retrieval" on `/admin/checks` (`webapp.checks.original_content_mirror_
  problems()`, backed by `Library.list_unmirrored_original_content()`),
  same shape as `hub_nav_orphan_problems()`, not the dated manual-
  attestation shape the pricing/model-freshness banners use — any row with
  non-empty `body_md` must have a `mirrored_article_id` pointing at a real
  `articles` row, checked with a plain SQL query, no external truth or
  judgment call involved. A future non-route write can still leave a row
  briefly unmirrored, but never silently — the fix is always the same
  (open it in `/admin/thought-leadership/original`, click Save) and needs
  no code. **Separate finding, informational, not fixed here**: a no-op
  admin save on `netsuite-mcp` changed `body_md` from 18,527 to 18,766
  chars — the edit form's textarea normalizes LF to CRLF on every submit,
  so every save rewrites the full body regardless of whether anything
  changed. Confirmed this doesn't worsen the sync-frequency question above
  — `plain_text_from_body_md()`'s markdown-render-then-strip step produces
  byte-identical output for LF vs. CRLF input, so `embed_article`'s
  content-hash guard still dedupes correctly — but it's a real, separate
  save-path bug worth its own fix later. Both dead migration scripts
  (`migrate_hackathon_playbook_content.py`, `migrate_netsuite_mcp_content.py`)
  were `git mv`'d into `scripts/archive/` in this same PR, per the standing
  "archive a one-time script once its run is confirmed" rule —
  `migrate_growth_engine_ratio_content.py` stays in `scripts/`, unconfirmed
  as run against production, same as before. `scripts/refetch_lopsided_
  logos.py` was investigated as a possible rider and found to already
  import `linklib.logodev`, not `linklib.brandfetch` — its own docstring
  says so — with no repo evidence its `--apply` run has ever been
  confirmed, so it was left alone rather than archived on a premise that
  didn't hold. See ARCHITECTURE.md's matching bullet (under Published-
  content ingestion) for the full write-up and
  `tests/test_original_content_ingestion.py`'s
  `test_list_unmirrored_*`/`test_admin_checks_surfaces_an_unmirrored_row`
  for the regression coverage. **Follow-up (2026-09, durability audit) —
  the invariant now also catches DRIFT, not just a missing mirror**:
  `Library.list_drifted_original_content_mirrors()` flags a row whose
  `mirrored_article_id` is valid but whose mirrored `articles.content` no
  longer matches the current `body_md` — the gap a future non-route write
  (any script calling `update_original_content()` without also calling
  `sync_original_content_article()`) can still produce even with the
  missing-mirror check in place. Compares content directly (re-deriving
  the expected text via `plain_text_from_body_md()`, the same function the
  sync itself uses), never `updated_at` timestamps — deliberately, since
  `scripts/normalize_original_content_tags.py` calls `update_original_
  content()` with `body_md` UNCHANGED but the method still bumps
  `updated_at` on every call, which would false-positive a timestamp-based
  check on that exact already-shipping script. `original_content_mirror_
  problems()` reports both missing and drifted rows together, each naming
  the fix (`/admin/thought-leadership/original/{id}/edit`, click Save —
  identical for both failure modes). See
  `tests/test_original_content_ingestion.py`'s `test_list_drifted_*` for
  the coverage, including the tag-only-edit false-positive guard.
- **Embedding costs are split by who pays for them.** Embed-on-save/backfill cost is
  Brian's overhead (`article_embeddings.cost_usd`) and never touches a user's Ask
  budget. Embedding the retrieval QUESTION at ask-time is a user-cap cost — it folds
  into `ask_questions.cost_usd` the same way the follow-up query-rewrite's cost already
  does (`embed_cost_usd` breaks out its share). A general ledger covering overhead
  spend more broadly (enrichment included) is deferred — see issue #105.
- **Exa cost tracking, pre-dashboard foundation (2026-09) — closes two real gaps
  the AI usage/cost dashboard's own Step 0 investigation found: a computed cost
  that was never persisted, and two Exa call sites with no cost tracking at all.**
  (1) `Answer.exa_result_count`/`exa_cost_usd` (`linklib.agent.retrieve_exa`) were
  always computed on every Buddy turn but silently dropped before reaching
  `ask_questions` — unlike `embed_cost_usd`/`rewrite_cost_usd`, which each already
  had their own column and were passed through. Two new `ask_questions` columns
  (`exa_result_count`, `exa_cost_usd`) mirror that exact pattern; `exa_cost_usd`
  is already folded into `cost_usd` (the turn total the monthly cap sums), same as
  the other two breakout columns. `webapp.ask_orchestrator.run_ask`'s call to
  `Library.record_ask_question` now actually passes `ans.exa_result_count`/
  `ans.exa_cost_usd` through — previously computed and then dropped. (2) The
  Reader content backfill's fallback tiers (`linklib/domain_migration.py`,
  `linklib/medium_platform.py`) call Exa's `/search` and `/contents` endpoints
  with zero cost computation anywhere — not tracked at all, not even in
  `content_refetch_log`. `find_migrated_url`/`fetch_content_by_url`/
  `find_medium_candidate` now each return a real `pricing.compute_exa_cost()`
  figure alongside their existing result (0.0 whenever Exa was never actually
  billed — missing key, toggle off, blank input, network failure — same
  best-effort contract every other Exa path in this codebase already follows),
  billed even on a miss since Exa still charges for a returned-but-unmatched
  result. A `/contents` call (`fetch_content_by_url`) is billed at `compute_exa_cost`'s
  existing "search" fallback rates — `EXA_PRICING` only models the `search` tier
  (see that dict's own comment on adding a row only once a caller needs one); this
  is a deliberate approximation for now, not a claim the two endpoints cost the
  same. A new `content_refetch_log.exa_cost_usd` column (added via migration, same
  `source` precedent) is the durable home — `linklib.pipeline._try_domain_migration`/
  `_try_medium_platform` both now thread cost through, accumulating it across every
  Exa call one `backfill_article_content()` attempt actually makes (a tier's miss
  still spent real money, and that spend lands on whichever single row the attempt
  ultimately logs — a later tier's success, or the final Wayback/failure row —
  preserving the existing one-row-per-attempt invariant). `medium_recovery()`
  (the same tier reused by `pipeline.ingest_url`'s save-time recovery path) returns
  its own cost the same way, logged onto `ingest_url`'s existing `content_refetch_log`
  write (`source="save"`); the Reader's live-read recovery path
  (`webapp._resolve_reader_content`) stays ephemeral/unlogged, unchanged — it never
  persisted this content at all, cost included. **`/admin/exa-settings`' copy was
  also corrected** — it used to describe only Buddy's web tier, which left an admin
  with no way to know that turning Exa off also silently disables both backfill
  tiers, with no fallback the way Buddy's own web tier has one (Claude's native
  `web_search_20250305`). The page now names all three call sites and says
  plainly that the two backfill tiers have no substitute — a miss there just
  falls through to the pre-existing Wayback fallback, same as any other miss.
  No changes to `EXA_PRICING` itself and no dashboard UI in this PR — both are
  scoped separately; see `tests/test_exa_cost_tracking.py` for the coverage.
- **`preferred_sites.opml` is dual-purpose.** It's both the web-search allowlist and the
  subscription list behind `/read`'s Feed quick view. Use direct RSS/Atom URLs — Feedly
  proxy URLs (`feedly.com/web/...`) are skipped because they require auth. Paywalled
  sources are tagged in `feed.py` (`PAYWALLED_DOMAINS`) and shown with a badge; the
  in-app reader is disabled for them.
- **Feed management (`/admin/reader/feeds`) — the OPML file is now GENERATED from
  `feed_sections`/`feeds`, not hand-edited, and it is no longer the source of truth.**
  Feeds and their sections are managed from an admin page instead of by editing
  `preferred_sites.opml` and deploying. The file survives as a derived cache:
  `Library.write_opml()` regenerates it on every mutation and the startup hook
  regenerates it again on every boot, so all four consumers (`feed.parse_opml`,
  `sources.preferred_domains`, `queue.scan_feed_into_queue`, `authcheck`) keep reading
  it completely unmodified. **Editing the file in place was rejected, not overlooked**:
  it lives inside the Docker image at `/app/preferred_sites.opml`, which Railway
  rebuilds on every deploy, so an edit made through the web UI would be destroyed on
  the next deploy and silently revert to the git copy — the same ephemeral-container
  trap the Brandfetch logo backfill hit with `webapp/static/logos`. Regenerating at
  boot makes that ephemerality irrelevant. Four decisions inside that are worth not
  re-litigating: (1) **seeding from the existing file is settings-flagged, not
  emptiness-checked** — an emptiness check looks identical on a fresh DB but re-imports
  the whole file on the next restart, resurrecting a deliberately deleted feed, the
  exact bug `_seed_toolbox` shipped and had to fix; (2) **`write_opml` no-ops on an
  empty feeds table**, so a fresh deploy can't overwrite the curated repo copy before
  seeding runs, and no-ops again when the generated content already matches disk, which
  keeps an ordinary boot (and any test that boots the app) from rewriting the file at
  all; (3) **`preferred_domains.cache_clear()` lives inside `write_opml()`**, not at
  each of the six mutation routes — that cache is read once per process, so a
  regenerated file with a stale cache leaves FP&A Buddy's allowlist wrong until the
  next deploy with no error anywhere, and making the two inseparable means no write
  path can forget one; (4) **`QUEUE_EXCLUDE_CATEGORIES` is retired** in favor of
  `feeds.exclude_from_queue`, a stored per-FEED boolean — the old set was built from
  `LINKLIB_QUEUE_EXCLUDE_CATEGORIES` (env var, now unused) and matched against a
  section's *name*, so once sections became renameable, renaming "News" would have
  silently started funnelling News into the archive queue. It's per feed rather than
  per section because a section is a display grouping while queue eligibility is a
  judgment about the individual source, so one feed can be read-only without dragging
  its section-mates along and moving a feed between sections can't change it.
  `scan_feed_into_queue` matches each item to its originating feed via the `feed_url`
  key now carried on every item by `feed.get_feed_items`, not the `category` string;
  `queue._excluded_feed_urls()` falls back to the two original News feed URLs when
  `Library.has_feeds()` is False, so an unseeded DB keeps the pre-migration behavior
  instead of defaulting to "nothing is excluded." (**Retired 2026-09, PR 3, along with
  the Archive Queue itself**: `exclude_from_queue`, the "Read only" checkbox, its
  `POST /admin/reader/feeds/{id}/read-only` route, and `Library.set_feed_excluded`/
  `excluded_feed_urls`/`has_feeds` are all gone from the codebase — see the Archive
  Queue retirement bullet below. The column stays in the schema, frozen at whatever
  value each row last had, same non-destructive-retirement precedent as
  `has_paywall_cookie`.) **A feed's `xml_url` is stored and
  regenerated verbatim** (only `.strip()` for surrounding whitespace) because a paid
  subscription's feed URL can carry a per-subscriber token, and a normalized token is a
  silently dead feed; the per-row section dropdown is backed by a
  deliberately narrow one-column update method (`move_feed_to_section`)
  so it can't rewrite a URL in passing. **Sections are pure
  grouping** — one flat feed table on the page, with a separate "Manage sections" area
  for add/rename/remove and no per-section settings at all. Adding a feed validates it
  server-side first
  (`feed.probe_feed()`), rejecting Feedly proxy links by name since those save cleanly
  and then produce nothing forever; editing re-probes only when the URL actually
  changed, so a rename doesn't fail because the source is down that day. **Known
  asymmetry, stated in the page copy rather than fixed:** the Reader's Sources rail is
  built from fetched items, not from the subscription list, so a quiet or unreachable
  feed shows on the admin page and not in the rail. **"Re-check subscriber access"
  moved here from `/admin/library`** (it probes a recent post per paywalled source, which
  is feed-specific); `POST /admin/auth/recheck` keeps its path because the Reader's own
  banner posts to it too, only the redirect target moved. **On Mostly Metrics
  specifically:** despite repeated concern about preserving a tokenized URL, no feed in
  the OPML has a query string at all — Mostly Metrics is stored as the plain
  `https://www.mostlymetrics.com/feed`, and its paywall is cookie-based
  (`LINKLIB_AUTH_COOKIES`), not URL-token-based. The verbatim-URL guarantee is built and
  tested anyway so it holds if one is ever added; tracing the access check shows it
  requesting exactly the stored URL before probing a discovered article URL.
- **`feeds.has_paywall_cookie` makes the cookie dependency visible without storing the
  cookie.** (Started life as `paywall_cookie_note`, per-row free text; converted to a
  boolean once it was clear every row restated the same sentence about the app's single
  cookie mechanism. The old column is retired-not-dropped, same precedent as
  `screenshot_is_product`/`field_reviews`, and is what `seed_paywall_cookie_flags`
  migrates from. A boolean also makes pasting a cookie value structurally impossible
  rather than merely discouraged.) Follow-up to the Mostly Metrics finding above: the cookie mechanism works,
  but nothing on `/admin/reader/feeds` showed that a feed depended on one, or which env
  var to check when it stopped returning full text. The column holds a human-readable
  boolean, rendered as a plain checkbox visually identical to Read only and Subscriber,
  plus a page-level footnote naming `LINKLIB_AUTH_COOKIES` once for the whole page. (An
  earlier round kept a seafoam lock badge beside the checkbox; it was the free-text era's
  tooltip trigger and duplicated what the checkbox already says, so it was removed.) **The cookie value never enters the database** — it stays in
  `LINKLIB_AUTH_COOKIES` where `extract.fetch_page` reads it, and nothing writes it back.
  Three decisions worth not re-litigating: (1) **seeded from `feed.PAYWALLED_DOMAINS`,
  so all three paywalled feeds get the note, not just Mostly Metrics** — leaving
  Stratechery and Public Comps blank would read as "these need no cookie", the exact
  false signal the column exists to remove, and it keeps `PAYWALLED_DOMAINS` the single
  source of which domains are paywalled rather than forking that knowledge into the DB;
  (2) **seeding is settings-flagged (`paywall_cookie_notes_seeded`), not
  emptiness-checked** — an empty note and a deliberately cleared one are
  indistinguishable, so an emptiness check would resurrect a cleared note on the next
  restart, the same `_seed_toolbox` bug the feed seeding itself had to avoid; it also
  no-ops *without* burning its flag while the feeds table is still empty, so one boot
  with an unreadable OPML can't permanently skip every feed seeded afterwards;
  (3) **`update_feed` writes the column on every call**, so the edit form round-trips the
  current value — a save that only renames a feed would otherwise blank the note.
  Migrating `PAYWALLED_DOMAINS` itself to be DB-backed stayed out of scope: it also
  drives the Reader's paywall badge and `authcheck`'s probe list, so that's a behavioural
  change to three consumers, not a label. **The note points at the cookie; the procedure
  for actually refreshing an expired one is `RUNBOOK.md` §5** — including the still-open
  question of the real per-domain cookie names, which no commit in the repo records.
- **`LINKLIB_AUTH_COOKIES` split into one `LINKLIB_COOKIE_<DOMAIN>` env var per
  domain (2026-08), and a full `LINKLIB_`-prefixed env var naming convention got
  documented (`RUNBOOK.md` §6) in the same pass.** The single JSON blob had
  already caused one silent-breakage near-miss — a hand-edited comma/semicolon
  typo anywhere in it makes the whole thing fail to parse, and `_auth_cookies()`
  catches that and returns `{}`, which reads downstream as "no cookies
  configured at all" rather than "one domain's cookie is malformed." A
  per-domain raw-string var can't have this failure mode: there's nothing to
  parse, so a typo in one domain's value can't take another's down with it.
  `linklib.extract._COOKIE_DOMAINS` (a small hand-maintained tuple, same
  precedent as `feed.PAYWALLED_DOMAINS`) is the explicit registry of which
  domains are checked — a deliberate choice over scanning `os.environ` for a
  `LINKLIB_COOKIE_*` prefix and reverse-parsing the domain back out, since a
  domain with both dots and hyphens isn't unambiguously reversible from its
  normalized variable name. `_auth_cookies()` keeps its exact pre-split return
  shape (`dict[domain -> cookie string]`), so every caller — `_cookie_for`,
  `authcheck.check_auth_cookies` (which still derives its probe-domain list
  from `_auth_cookies().keys()`), the admin cookie-status panel — is
  unaffected by the split. **The feed table's Cookie column stopped being a
  manually-ticked checkbox in the same PR**: it recorded a feed's declared
  need for a cookie, not whether one was actually configured, and the two
  could silently drift apart (the checkbox never read `LINKLIB_AUTH_COOKIES`
  or its successor at all — see `_feed_form_fields`'s own former hint text,
  "it changes nothing about how pages are fetched"). Replaced with a computed,
  read-only indicator (`extract.has_configured_cookie(domain)`) checked live
  against the host environment on every page load. The `has_paywall_cookie`
  DB column and its write path in `Library.update_feed`/`add_feed` are left
  in the schema, frozen at whatever value each row last had — a deliberate,
  narrower non-destructive retirement than dropping the column outright, same
  precedent as `screenshot_is_product`/`field_reviews`; removing it is a
  separate future decision, not bundled into this migration. The old
  `LINKLIB_AUTH_COOKIES` variable itself is not deleted from Railway as part
  of this change — it's dead once the new variables are confirmed working,
  but the rollback (a code revert makes it live again with zero Railway
  edits) only holds while it's still set, so removing it from Railway is a
  manual follow-up once the new variables are verified. Mostly Metrics'
  cookie value migrated verbatim (untrimmed) — it's a paste-everything
  cookie-jar dump with likely only 1-2 cookies actually carrying auth,
  flagged as a real but separate follow-up: trimming it requires live-session
  testing (log in, strip cookies one at a time, see when the paywall
  reappears) that only Brian can do, not something derivable from source.
- **The feeds page shows cookie HEALTH separately from the cookie DECLARATION.**
  The Cookie checkbox is static ("this feed needs one"); a summary panel under the
  page header is dynamic ("it still works / it expired / it could not be tested"),
  read from the persisted `settings.auth_cookie_status` record so it survives
  reloads. Built because only the expired state used to render anything at all:
  a healthy cookie and an unprobeable one were both blank, so an empty page meant
  both "fine" and "unknown". Colours are true stoplight values
  (`#15803D`/`#b91c1c`/`#CA8A04`), a sanctioned exception in
  `brand_check.AUX_COLORS` and BRAND.md §6 — the semantic triple was tried first
  and `--good` is navy, too close to the site's dominant colour to register as a
  status signal. The red reuses the destructive-action `#b91c1c` rather than
  adding a second red. **Amber is dot-only** (2.94:1 as text, under AA), so the
  state word stays `--ink-soft`. **Amber means only `ok: None`**: a passing check stays green no
  matter how old, with age shown as relative text; the real staleness mechanism
  is the existing 12-hour background re-probe, not a colour. It's a per-domain
  panel rather than a table column because cookies are keyed by domain and the
  table is keyed by feed.
- **The cookie domain registry stopped being a hardcoded tuple (2026-09) —
  it's derived live from `preferred_sites.opml` instead, so a new paid
  subscription is a data event, not a deploy.** Found live: Cautious
  Optimism's cookie was set in Railway, deployed, and the row still showed
  "No cookie configured" — the `www` normalization the edit page displays
  the variable name with (`_cookie_env_var`) already matched
  `_cookie_for`'s own www-stripping exactly, so that wasn't it. The real
  cause was `linklib.extract._COOKIE_DOMAINS`, the "small hand-maintained
  tuple" described in the bullet above: it only ever held
  `("mostlymetrics.com", "onlycfo.io")`, so a domain never added to that
  literal list — regardless of what env var is set for it — was never
  checked at all, silently. Brian's own understanding ("every
  Subscriber-flagged feed gets checked for a matching variable") was false;
  the Cookie column's own help text ("only which domains to check is baked
  into the code") was the accurate description all along, just easy to
  read as a footnote rather than the actual gate. Fixed by replacing
  `_COOKIE_DOMAINS` with `extract._opml_feed_domains()` — parses
  `preferred_sites.opml` (the file `Library.write_opml()` already
  regenerates from the feeds table on every mutation) for every
  `xmlUrl`/`htmlUrl` domain, www-stripped the same way `_cookie_for`
  already was, on every call. Deliberately uncached (unlike
  `sources.preferred_domains`'s `lru_cache`, which needs `write_opml()` to
  remember to clear it): a fetch already dwarfs an OPML parse, so there's
  no performance reason to cache, and skipping it sidesteps a whole class
  of test/process cross-contamination the cache-plus-clear pattern would
  otherwise need to manage. `linklib.extract._COOKIE_DOMAINS` no longer
  exists — the `has_active_subscription`/"Subscriber" checkbox is still
  purely descriptive and reads nothing (see the CLAUDE.md Subscriber
  bullet near the feeds-page help text), the fix is only in which domains
  `_auth_cookies()`/`has_configured_cookie()` ever look at.
- **The Reader's Feed view caches per-feed for 30 minutes** (`feed.py`, in-memory). Cached
  item dicts are shallow-copied before mutation — never mutate a cached entry in place.
  Editing the OPML won't show up live until the cache expires or the app restarts.
- **The CFO Toolbox startup sync (`_seed_toolbox` in `webapp/app.py`) never inserts —
  only syncs.** It runs on every process boot (any deploy, restart, or crash recovery,
  not just a first run) and re-syncs `name`/`description`/`advisor` on any `tools` /
  `communities` / `benchmarks` row that already matches a seed entry by URL. It used to
  also insert a row when no match was found, on the assumption that only meant "never
  seeded" — but since none of those three tables has a soft-delete column, a manually
  deleted tool/community/benchmark looked identical to an unseeded one, so it silently
  reappeared on the very next restart. Fixed: a seed entry with no matching row is now
  skipped, never inserted. First-time seeding of a brand-new DB is `scripts/seed_tools.py`
  / `scripts/seed_communities.py`'s job, run once by hand — this hook no longer
  duplicates that. New tools/communities are added going forward exclusively through the
  admin UI's "+ Add tool" / equivalent flow, never by editing the seed files directly.
  Every `tools`/`communities` deletion (single-row delete, bulk delete, a pending
  submission's Reject, a name-duplicate merge) now also writes a row to
  `tool_audit_log`/`community_audit_log` — snapshotting name/url/categories immediately
  before the hard `DELETE`, since that's the only record of what was removed once the row
  is gone. See `ARCHITECTURE.md`'s CFO Toolbox table for the full shape.
- **CFO Toolbox logos (Phase D) come from Brandfetch's Brand API, not its free CDN Logo
  API, and are stored beside `library.db`, not under `webapp/static/`.** The original
  investigation assumed `cdn.brandfetch.io?c={client_id}` (500K free requests/month) —
  that turned out to be browser-embed-only and explicitly disallows programmatic/backend
  access per Brandfetch's own docs and ToS; a dry-run against it (`scripts/
  report_brandfetch_coverage.py`) came back with a uniform blocked-request response for
  all 216 records, not real per-company misses. The correct product is the **Brand API**
  (`api.brandfetch.io/v2/brands/domain/{domain}`, `Authorization: Bearer
  BRANDFETCH_API_KEY` — a separate credential from the unrelated `BRANDFETCH_CLIENT_ID`),
  which returns real logo asset URLs meant for exactly this kind of one-time server-side
  fetch, but whose free tier is only 100 requests/month — well under the 216-record
  catalog. `scripts/backfill_logos.py` is deliberately built around that limit: it
  processes software tools before communities, `--limit` (default 90) records per run,
  and is meant to be run three separate times a month apart via `railway ssh`, never in
  bulk. Downloaded assets are saved to a `logos/` directory next to `library.db` on the
  Railway volume (`logos/tools/` and `logos/communities/` subdirectories — the two
  entity types' slugs can collide, same reasoning as `_SCREENSHOT_DIR`/
  `_COMMUNITY_SCREENSHOT_DIR`), not under `webapp/static/logos/` as the build prompt
  originally specified — that directory ships baked into the Docker image and is wiped
  on every deploy, which would have silently destroyed each month's backfill progress.
  `tools.logo_path`/`communities.logo_path` store the resulting relative path; actually
  rendering a logo on a profile page or directory card, and the fallback UI for a record
  that never resolves one, is deferred to a later phase.
- **Manual logo override (2026-08) — a wrong or missing Brandfetch logo is now fixable
  from the admin edit page, without a code deploy, and can never be silently reverted by
  a future automated re-fetch.** Triggered by finding Aleph's public profile showing
  Zapier's logo (live screenshot evidence). Phase 0 investigation confirmed
  `scripts/backfill_logos.py` is the only writer of `logo_path` (a manual, occasional
  batch script — never triggered on tool add, and there's no periodic/background
  refresh at all), and ruled out a same-repo cause for Aleph specifically: its stored
  URL (`https://www.getaleph.com`) extracts to the correct domain (`getaleph.com`), and
  there's no Zapier tool/community anywhere in this codebase's data for a slug/domain
  collision to explain it. The live Brandfetch-side root cause couldn't be independently
  reproduced from this session — its outbound network egress is blocked to both
  `api.brandfetch.io` and Aleph's own site — so this ships two things regardless of
  which theory is right: (1) a domain-echo guard in `backfill_logos._fetch_logo_asset`,
  which now checks the Brand API response's own `domain` field against the domain
  requested before trusting its logo asset — closing the real code-level gap this
  investigation found (nothing previously cross-checked a mismatched/fuzzy API response),
  whether or not it's what actually happened to Aleph; and (2) the override mechanism
  itself, since a wrong logo needs a fix path regardless of why it went wrong.
  `tools.logo_manual_override`/`tools.logo_override_stale` (mirrored on `communities`)
  back it, following the same "human edits win over automation" principle the
  `seed_tools.py` description-sync incident established: `set_tool_logo`/
  `set_community_logo` (the only calls a Brandfetch fetch ever makes) refuse to overwrite
  a row with `logo_manual_override=1` unless explicitly forced — the one choke point
  every automated write goes through, so the protection holds regardless of what
  selection query got a caller there (the backfill script's own selection query also
  excludes these rows, as a second belt, so a run never burns Brand API quota on a row
  it can't write anyway). The admin edit page (`_logo_admin_section`, both Software and
  Communities) reuses the App screenshot section's own conventions — a URL-fetch text
  input plus a plain file-upload `<form>` (jpeg/png/webp only, no SVG — admin-supplied
  content is untrusted input in a way a vetted third-party API response isn't) — and a
  "Revert to automatic" action that blanks `logo_path` and drops the flag so the next
  backfill run repopulates it. A URL change to a genuinely different domain (not a
  path/query/scheme-only edit to the same site) flags an active override as stale
  (`logo_override_stale=1`) rather than silently keeping it applied to what's now a
  different company's site, or silently dropping a real correction — surfaced as a
  banner with "still correct—dismiss" and "clear override" actions. A brand-new row
  (e.g. delete-then-re-add) starts with no override, by construction — neither `add_tool`
  nor `add_community` sets these flags to anything but their schema default of 0.
  **Open question, not independently confirmed by this investigation**: whether
  Brandfetch's Brand API genuinely returned mismatched data for `getaleph.com`, or the
  issue is something else on Brandfetch's side — this session had no network access to
  either `api.brandfetch.io` or `getaleph.com` to check directly. Worth a quick manual
  check (or a session with broader network access) if it matters to pin down exactly;
  the fix above doesn't depend on the answer.
- **Manual logo override, "Revert & re-fetch from Brandfetch" (2026-08 follow-up)** —
  confirmed live by Brian (Aleph's logo corrected, badge showing "Manual override"), then
  asked for one more thing: "Revert to automatic" used to only reset the row for the *next*
  monthly `scripts/backfill_logos.py` run — no button anywhere actually called the Brand
  API live. Now it does both in one click: clears the override, then makes one real,
  synchronous Brand API call for that row right then. The Brand API call/asset-selection/
  download logic moved out of `scripts/backfill_logos.py` into a new shared
  `linklib/brandfetch.py` first, so the batch script and this button call exactly one
  implementation (including the domain-echo guard above) rather than risking two copies
  drifting apart — the script's own behavior is otherwise unchanged, re-verified against a
  temp DB. The new `webapp.app._live_refetch_logo` helper always runs after the override is
  already cleared, and on any failure (missing `BRANDFETCH_API_KEY`, the 100/month quota
  exhausted, no usable asset, a download error) simply leaves the row reverted to automatic
  — it can never end up worse off than a plain revert, and the monthly batch run can still
  pick it up later. The button's own copy now says plainly that clicking it spends a real
  Brand API call immediately, not a free scheduled action, since quota is capped. See
  ARCHITECTURE.md's matching bullet for the full write-up and `tests/test_brandfetch.py`/
  `tests/test_logo_override.py` for the coverage.
- **CFO Toolbox profile pages show two screenshots (Phase E): homepage and app/product,
  independently sourced.** The homepage slot (`screenshot_url`/`screenshot_captured_at`)
  is unchanged — always captured from the record's own `url`. The app slot
  (`app_screenshot_source_url`/`app_screenshot_url`/`app_screenshot_captured_at`) is
  additive, not automatable the way the homepage slot is: there's no single reliable
  "the app's URL" the way there's a homepage URL, so it's inherently manual/curated —
  Brian supplies a login/demo/product-tour URL per record (or skips it; most records have
  none at launch), then either auto-captures against it (same `linklib/screenshots.py::
  capture_homepage`, now genuinely URL-agnostic rather than homepage-only in practice) or
  crops-and-uploads his own image via a client-side Cropper.js modal (CDN script, no
  server-side image-processing dependency — the browser produces the final fixed-size PNG
  before it ever reaches the server, so no Pillow was needed). Both write paths land on
  the same `app_screenshot_url`, with no provenance tracking between them. Saved as
  `{slug}-app.png` in the same `_SCREENSHOT_DIR`/`_COMMUNITY_SCREENSHOT_DIR` and served by
  the existing homepage screenshot routes — no new serving route, just a filename suffix.
  The pre-Phase-E `screenshot_is_product` flag (a single slot doing double duty as
  "homepage or product, whichever's pasted") is retired: `Library.
  migrate_app_screenshot_from_product_flag` moves a legacy `screenshot_is_product=1` row
  into the new app slot and clears the homepage slot, and the column itself stays in the
  schema, unused, as a non-destructive historical marker. This is a one-time PRODUCTION
  DATA write, so — per the standing "human review before a production write" rule below —
  it is deliberately **not** wired into an automatic boot hook the way schema/column
  backfills are; it only runs when Brian invokes
  `scripts/archive/migrate_app_screenshot_from_product_flag.py` by hand (preview by default,
  `--apply` to write for real, write-then-read-back verified — same convention as
  `scripts/backfill_logos.py`), after reviewing the affected-row list it prints. Desktop
  stacks both screenshots in one card when the app slot is populated; mobile shows one at
  a time with a tap-to-toggle button (the existing Phase J1 expand/collapse convention,
  not a new swipe-gesture pattern). A record with only a homepage screenshot — the common
  case at launch — renders identically to pre-Phase-E, no toggle, no second frame.
- **Off-site backup automation (Phase O) — the mechanism was real, the scheduling wasn't.**
  A 2026-08 investigation (triggered by finding the "Library Backup" Drive folder empty)
  confirmed `linklib/backup.py` (SQLite online-backup API for a consistent snapshot, raw
  multipart upload to Drive, refresh-token auth — no new dependency) was complete and
  working, but had never actually been scheduled: it only ran as a debounced side effect
  of ~18 admin/save routes in `webapp/app.py` (article save, tag edits, dedupe, enrichment
  backfill, feed scan, …), and Brian's actual admin usage doesn't touch any of them, so it
  had essentially no opportunity to fire. Fixed with a weekly GitHub Action
  (`.github/workflows/backup.yml`) calling the existing `POST /admin/backup-now` route
  (`X-Save-Token` auth, a repo secret named `LINKLIB_SAVE_TOKEN`) as the reliable primary
  trigger — the ~18 call sites stay as-is, a harmless bonus trigger. The Action targets the
  Railway origin (`*.up.railway.app`), not `bmweis.com`: the first live verification run
  against the Cloudflare-fronted hostname got a `403` from Bot Fight Mode before ever
  reaching the app — confirmed as Cloudflare, not the app, since a bad token gets `401`,
  never `403`. See ARCHITECTURE.md's "publicly reachable Railway origin" note. **That fix
  was incomplete on its own** — the app's own canonical-host middleware (`_LEGACY_HOSTS`)
  already 301-redirects that exact Railway hostname back to `bmweis.com` for every path
  except `/health`, so the very next live run "succeeded" (`curl -f` doesn't fail on a
  3xx, and the Action didn't pass `-L`) while silently never reaching `backup_now_route`
  at all — `backup_log` stayed empty despite a green Action run. Fixed with a narrowly-
  scoped exemption: `/admin/backup-now` is now excluded from the legacy-host redirect
  alongside `/health`, and only that one path — deliberately not a blanket exemption for
  every token-authenticated route (see `webapp/app.py`'s `_canonical_host_redirect`
  docstring). **Lesson carried forward: a green CI/Action run is not verification** — the
  only way this was caught was checking `/admin/library-backup`'s banner and history table
  directly against what actually landed, not trusting an exit code. Two more gaps closed
  in the same phase: (1) every backup attempt, success or failure, now writes a row to the
  new `backup_log` table (`Library.record_backup_attempt`/`list_backup_log`) from inside
  `backup.py` itself, rather than only `print()`ing to stdout where nothing in the app
  could see it; `/admin/library-backup` reads that table for a status banner (green/amber/
  red — "off" and "configured but failing" are deliberately different colors, not
  collapsed into one) and a history table. (2) `POST /admin/backup-now` now returns a real
  `503`/`502` on failure instead of always `200`, so the Action (and `curl -f`) can tell
  success from failure without parsing HTML. **The actual root cause of the empty folder,
  found once the trigger/redirect issues above stopped masking it:** the refresh token is
  minted with the `drive.file` OAuth scope — deliberately the narrowest Drive scope, which
  only grants visibility into files/folders *the app itself created via the API*. The
  original setup pointed `GOOGLE_DRIVE_FOLDER_ID` at a folder made by hand in the Drive
  web UI ("Library Backup"); every upload against it 404'd (Google's Drive API returns
  404, not 403, for a resource the caller can't see, to avoid confirming it exists) even
  with the correct account and folder id — `drive.file` simply can't see a folder it
  didn't create. Fixed by having `backup_now()` create and own its own folder instead of
  targeting a pre-existing one: `linklib.backup._resolve_folder_id` reuses an id already
  persisted in `settings` (`backup_drive_folder_id`) if a prior run created one, or
  creates a folder named "CFO Navigator — Library Backups" in My Drive root on first use.
  `GOOGLE_DRIVE_FOLDER_ID` still overrides this if set — normally left unset now.
  `/admin/library-backup` shows a live link to whichever folder is currently in use. The
  old hand-made "Library Backup" folder is abandoned, not deleted or referenced anywhere.
- **Backup trigger moved from GitHub Actions to a Railway Cron Service (2026-08) — the
  GitHub-Actions dependency is gone entirely.** The GitHub Action above worked, but its
  schedule silently stopped firing for 9 straight days when the account's GitHub Actions
  spending limit blocked every workflow run (no spending limit configured, wouldn't
  self-resolve until the next billing cycle) — an outage in a billing system that has
  nothing to do with Railway or this app. Fixed by moving the trigger onto a native
  Railway Cron Service in the same Railway project: a tiny standalone service with no
  app code of its own, configured with a cron schedule (`0 9 * * *`, same daily
  09:00 UTC slot the Action used) whose only job is one `curl -sS -w '\n%{http_code}'
  -X POST https://cfo-navigator-production.up.railway.app/admin/backup-now -H
  "X-Save-Token: $LINKLIB_SAVE_TOKEN"` call, checking the trailing status code and
  `exit 1`-ing on anything outside 200-299 so a failed run shows up red in Railway's own
  run history — same non-2xx contract `/admin/backup-now` already provided for the
  Action's `curl -f`, just read by a shell check instead. `LINKLIB_SAVE_TOKEN` is
  referenced from the project's existing Railway variable (the same one the app itself
  runs with), not duplicated as a second copied secret. Still targets the Railway
  origin, not `bmweis.com`, for the identical Cloudflare Bot Fight Mode reason the
  Action's own comment documented. `.github/workflows/backup.yml` is deleted outright
  (not kept as a manual `workflow_dispatch` fallback — a fallback that itself depends on
  Actions quota isn't a real fallback for an Actions-quota outage). This is a
  platform-level Railway configuration, not application code — there's no new Python,
  no new repo dependency, and no new committed service definition; the cron service's
  schedule and command live in the Railway dashboard, same as every other
  project-level Railway setting (the healthcheck path, the build command) that already
  isn't in this repo. See RUNBOOK.md §7 for the exact dashboard setup steps and the
  curl command, and the "Off-site backup" note in ARCHITECTURE.md's deployment diagram
  for the updated trigger edge.
- **Post-migration cleanup + a real failure-logging gap closed (2026-08).** A
  follow-up session swept the repo for leftover "weekly"/"GitHub Action" backup
  references beyond what the migration PR above already updated (found and fixed
  a handful more — `_canonical_host_redirect`'s docstring, the Archive backup
  admin-tool description, a `backup_log` schema comment in `linklib/db.py`, and
  two test-file docstrings — all describing-the-mechanism prose, no behavior
  change) and fixed the now-stale `/admin/library-backup` status banner copy
  ("Scheduled weekly via GitHub Action…" → describes the daily Railway Cron
  Service and points at its Railway dashboard run history instead of a
  nonexistent in-app "Actions tab" equivalent). Also confirmed `LINKLIB_SAVE_TOKEN`
  is no longer referenced by any GitHub Actions workflow (`qa.yml`,
  `secret-scan-weekly.yml` — neither uses `secrets.*` at all) now that
  `backup.yml` is gone, so the matching GitHub repo secret is safe to delete —
  flagged for Brian to remove himself, not done automatically.
  **The more consequential finding**: `backup_now()`'s own docstring has always
  claimed "every attempt — success or failure — is logged to backup_log... before
  returning or re-raising," but the "Drive not configured" path was the one
  exception — it raised immediately with no log call, and `webapp/app.py`'s
  `backup_now_route()` had its own separate `is_configured()` pre-check that
  returned a `503` without ever calling `backup_now()` at all, so this failure
  left zero trace in `backup_log` from either code path. A pre-existing test
  explicitly asserted this was intentional ("not being configured isn't a real
  attempt — nothing should be logged"), reasoning that the status banner's own
  live `is_configured()` check already shows "Backups are off" independent of
  `backup_log`. True for the banner, but it meant the history table stayed
  completely silent for the entire span of any misconfiguration — a lapsed OAuth
  grant would leave the daily Railway Cron Service pinging a broken instance for
  days with nothing to show for it anywhere but Railway's own run log. Reversed,
  flagging the reversal explicitly rather than silently overriding a documented
  prior decision (same precedent as the homepage "🚧 building" sticker mix-up
  elsewhere in this doc): `backup_now()` now logs this path too, and
  `backup_now_route()` was simplified to always call `backup_now()` (its
  separate pre-check removed) so there is one logging code path instead of two
  that could silently diverge — the route infers its `503`-vs-`502` response by
  re-checking `is_configured()` inside the `except` block, strictly after the
  failure has already been logged. See ARCHITECTURE.md's `backup_log` schema-table
  row for the full write-up and `tests/test_backup.py`'s
  `test_backup_now_logs_before_raising_when_not_configured`/
  `test_backup_now_route_logs_a_failed_row_when_not_configured` for the
  before/after regression coverage.
- **Phase G — the Agent taxonomy "unverified" banner promised a step that
  sometimes had no button behind it; fixed, plus a real "Mark verified" audit
  trail.** An investigation (2026-08) confirmed the green banner on a
  Software tool's edit page after "Generate summary"/Refresh AI research —
  "review the drafted feature rows and agent taxonomy below before marking
  them verified" — fired unconditionally on a successful refresh
  (`research_refreshed=1`), while the actual "Mark verified" button (and
  every public "unverified" badge — profile page, compare-tools table,
  directory admin table) is gated on `tools.agent_taxonomy_needs_verification`,
  which is set per-run from the LLM's own self-reported confidence
  (`agent_taxonomy.get("confident")` in `linklib/enrich.py`'s
  `generate_tool_features`). A confident run zeroes the flag, so the button
  and every badge disappear — but the banner kept telling the admin to go
  mark something verified regardless. Not a missing feature or copy drift:
  the mechanism (`agent_taxonomy_needs_verification`,
  `mark_tool_agent_taxonomy_verified`, `POST /admin/tools/{id}/agent-taxonomy/verify`)
  is real and was already wired to a real button — the banner's condition and
  the button's condition were just two different flags the copy assumed were
  the same. Fixed by making the "before marking them verified" clause
  conditional on the same flags `_features_badge_html`'s "N needs
  verification" text already checks (`agent_taxonomy_needs_verification` OR
  any feature row's `needs_verification`) — same conditional-clause pattern,
  not a new one. Also added `narrative_review_log` (a shared table with
  `field_type`/`entity_type` discriminators, mirroring
  `tool_audit_log`/`community_audit_log`/`backup_log`'s shape) so clicking
  "Mark verified" leaves an actual "who verified it and when" trail, shown as
  a "Verified by X on Y" line on the edit page — the flag existed before, but
  nothing recorded who cleared it. **Mid-build discovery that reshapes the
  planned follow-up:** extending this same needs-verification-boolean +
  "Mark verified" pattern to Description, Differentiation, and the Community
  profile draft (the other three AI-drafted narrative fields, per the
  investigation's Part 4 inventory) turns out to collide with a *different*,
  already-shipped mechanism — `field_reviews` (added 2026-08-01, "AI-first-pass-then-review:
  Bottom Line generate, review tracking, Communities field-coverage
  extension"), a generic `entity_type`/`entity_id`/`field_name`/`reviewed_at`/
  `reviewed_by` table already written on every save-after-Generate for
  exactly those three fields (`markAiDrafted()`/`_MARK_AI_DRAFTED_JS` +
  `_record_ai_drafted_reviews`). It predates this investigation and wasn't
  surfaced by it, because it isn't wired to Agent taxonomy or to the banner
  bug at all — it only turned up while tracing the actual save-submit paths
  during the build. It's real but inert: written faithfully, `list_field_reviews`
  exists, but nothing in `webapp/app.py` ever calls it, so none of it is
  displayed anywhere today — same "logged but never surfaced" shape as
  `tool_audit_log`/`community_audit_log` before this phase. It also
  conflates "saved after clicking Generate" with "a human actually
  scrutinized it," which is a looser bar than Agent taxonomy's explicit,
  separate "Mark verified" click — and for the Community profile draft
  specifically, it's already per-field (23 rows), not the single whole-draft
  flag the Phase G build brief calls for. Building `description_needs_verification`
  and friends as literally specified would leave two overlapping, semantically
  different tracking mechanisms live for the same three fields going forward.
  Flagged to Brian before writing that code rather than either silently
  duplicating `field_reviews` or silently redesigning around it — the
  Description/Differentiation/Community-profile-draft rollout is paused
  pending that call; Agent taxonomy's fix above is unaffected and shipped on
  its own.
- **Phase G PR 2 — Description, Differentiation, and the Community profile
  draft join the "Mark verified" gate; `field_reviews` goes frozen, not
  dropped.** Resolution of the reconciliation question above. Confirmed
  (Option 3): no backfill of `field_reviews`' existing rows into
  `narrative_review_log` — the two tables record different things (a save
  that happened to follow a Generate click vs. a deliberate confirm click),
  and translating one into the other would fabricate confirmations that
  never actually happened. `field_reviews` itself is **not dropped** —
  same non-destructive-retirement precedent as `screenshot_is_product`
  (see the Key architecture decisions bullet above): it stays in the schema
  as a frozen historical record for the fields it no longer tracks
  (Description, `summary`, `competitive_differentiation`, the 23 Community profile
  fields — `_RETIRED_FIELD_REVIEW_FIELDS` in `webapp/app.py`), while it
  keeps growing normally for everything this phase didn't touch (the
  Community "Auto-fill from URL" listing fields, competitor-match
  suggestions on both entity types). New columns `tools.
  description_needs_verification`/`competitive_differentiation_needs_verification`
  default to 0 for every existing row (no retroactive flagging, same
  precedent `agent_taxonomy_needs_verification`'s own migration set) — only
  the *next* AI draft of each field trips the gate. `summary` shares
  `description_needs_verification` rather than getting a column of its own,
  since `generateDescription()` drafts and marks both fields in one click.
  Since neither field has a separate Refresh route the way Agent taxonomy
  does — Generate is AJAX-only, and the main edit-submit route is the only
  place a draft is ever saved — that one route sets the flag directly at
  save time: `1` when this submit's `ai_drafted_fields` names the field, `0`
  otherwise (a hand-edited or untouched save is itself a confirmation, the
  same convention `update_tool_agent_taxonomy` already used). Regenerating
  an AI-drafted field always re-flags it for review on save, even if it was
  previously marked reviewed — this is intentional, not a bug: fresh AI
  output always needs a fresh human look, regardless of what the field's
  prior state was. **A second
  structural discovery surfaced mid-build, specific to the Community
  profile draft:** `community_profiles.needs_review` already existed as a
  working whole-profile "flag for later" mechanism — its own "Mark
  reviewed" button, an admin-list badge, a count, and a `?filter=
  needs_review` view — built for flagging profiles from a bulk import,
  manual-only, never auto-set by AI generation. Adding a fourth, separate
  `community_profile_needs_verification` column alongside it would have put
  two similar-looking badges on the same admin row for related concerns.
  Flagged before building past it; Brian's call was to reuse `needs_review`
  instead: `POST /admin/tools/communities/{id}/profile` now OR's it to `1`
  whenever the save's `ai_drafted_fields` names any of the 23 profile
  fields (never overriding a manually-set `1` to `0`), and the pre-existing
  "Mark reviewed" action now also writes a `narrative_review_log` row and —
  new in this phase — is reachable directly from the profile edit page
  itself (`/admin/tools/communities/{id}/profile`), not only the admin-list
  row, closing the same discoverability gap the original Phase G
  investigation flagged for Agent taxonomy's missing button in the first
  place. `_narrative_verify_widget` (`webapp/app.py`) generalizes the
  badge/button/hidden-form/review-line markup PR 1 built once for Agent
  taxonomy into one shared helper — reused directly for Description and
  Differentiation, and in reduced form (button + review line only, no
  badge, since the existing checkbox already shows state) for the Community
  profile draft.
- **Agent taxonomy publish gate (2026-08) — `agent_taxonomy_needs_verification`
  now gates public visibility, not just a badge.** A tool-edit-consistency
  investigation found Abacum's live agent-taxonomy note referenced "the
  retail page" and quoted specific-sounding invented language ("Abacum
  Intelligence flags the anomaly, drafts the...") attributed to it — no such
  page exists in the site's real crawled content. A follow-up audit of
  `generate_tool_agent_taxonomy` (`linklib/enrich.py`) confirmed this is a
  systemic grounding gap, not an isolated bad output: the generation prompt
  is well-worded against fabrication ("ground this strictly in the page
  content below," told to set `"confident": false` rather than guess), but
  has no mechanical grounding/citation enforcement the way `linklib/agent.py`'s
  FP&A Buddy does (real Citations-API document blocks with verified source
  attribution) — a prompt instruction alone is not a backstop against
  invented specificity. Worse, the model's own `confident` boolean — the one
  signal meant to catch exactly this — never gated anything: even a
  correctly self-flagged `confident: false` draft still saved and rendered
  publicly, with only a small "unverified" badge as the visible difference.
  Since this is the same shared prompt/pipeline for every tool in the
  directory (~150+ vendors), triggered automatically on tool creation with
  no required human review before publish, Abacum was simply the instance
  where the gap became visible — not a one-off. Fixed as an immediate
  publish gate, shipped ahead of (and independent from) the citation/
  grounding mechanism itself, which stays separately scoped: on both the
  Software profile page's Agent taxonomy card and the software-compare
  matrix's "How agents are involved" row, a note with
  `agent_taxonomy_needs_verification=1` is now shown only to a signed-in
  admin, explicitly labeled "unverified—hidden from visitors until
  reviewed"/"hidden from visitors" — never to a public visitor, however
  plausible it reads. A verified note renders exactly as before, with no
  badge at all now (a note visible to the public is itself the verified
  signal). The real structural fix — a citation/grounding mechanism for
  `generate_tool_agent_taxonomy` (and, per the same audit, `generate_tool_description`
  and `generate_tool_differentiation`, which share the no-citation
  architecture and the confidence-flag-not-gate pattern) — remains future,
  separately-scoped work.
- **Citations-API grounding fix — the real structural fix the publish-gate
  bullet above deferred, built in phases (2026-08).** Phase 1a:
  `agent.py`'s existing FP&A Buddy citation mechanism
  (`_build_source_documents`/`_assemble_cited_answer`) was extracted,
  behavior-unchanged, into a new shared `linklib/citations.py`
  (`make_document_block`, `extract_citations`) — proven byte-identical
  against real pre-refactor output captured to a golden fixture file
  *before* the refactor code existed, not regenerated after (see
  `tests/test_citations_refactor_parity.py`). Phase 1b: `enrich.py`'s
  `generate_tool_agent_taxonomy` became the first real caller — each
  fetched vendor page now rides as a genuine Citations-API `document`
  block instead of flattened prompt text, so its citations are
  API-verified, not self-reported (a separate, independent fact from the
  `confident` self-report the publish gate above already used). Checked
  against current Anthropic docs before building: citations are
  explicitly incompatible with structured output (`output_config.format`,
  400 error) and have no attachment point on `tool_use.input` either
  (citations only ever attach to `text` blocks) — so the strict-JSON
  `agent_taxonomy_note` contract stays untouched, with citations extracted
  via `extract_citations(..., inject_markers=False)` (deterministic
  parsing, not a second model call) alongside the unmodified JSON text.
  Stored in a new shared `entity_citations` table (`entity_type`/
  `entity_id`/`field_name` composite key, full deduped-by-url citation
  list, uncapped) rather than a per-field column — chosen specifically so
  Description (Phase 2) and Community profile (Phase 3, one shared
  citation set per profile draft) don't have to migrate off one later.
  Public profile page shows a capped-at-5 "Sources" list; the admin edit
  page shows the full uncapped list in the same `#gen-host-tool-taxonomy`
  block as the "Mark verified" action, by explicit requirement, so a
  reviewer sees every source before a note (and its capped citations) goes
  public — citations never bypass the existing review gate, they only
  ever render alongside the note in whichever visibility branch it's
  already in. Competitive differentiation stays deferred (Phase 4) pending
  a decision on whether it gains real fetched competitor content to
  ground on, since it currently has none. See ARCHITECTURE.md's citations
  bullets (under FP&A Buddy) and the `entity_citations` schema-table row
  for the full write-up.
- **Citations-API grounding fix, Phase 2 (2026-08) — Description joins
  Agent taxonomy's grounding, with one real structural difference: there's
  no server-side draft-time persistence point to write to.** Description's
  Generate button is stateless client-side AJAX (`{name, url}`, no
  `tool_id` — callable from the brand-new "Add software" form, which has
  no tool row yet at all), unlike Agent taxonomy's server-side "Refresh AI
  research" route that persists citations in the same call that drafts the
  note. So the citations a Generate call returns travel through the
  browser as a new `ai-drafted-citations` hidden input (JSON, mirroring
  the existing `ai-drafted-fields`/`ai-drafted-confidence` convention) and
  get validated server-side before persisting, in both submit routes
  (`/tools/software/{slug}/edit` and `/admin/tools/software/new`) —
  `webapp.app._validate_citations_payload` never trusts the hidden field's
  contents as-is: must be a JSON list of objects, `url` must be http(s)
  (rejecting `javascript:`/`data:`/etc.), `title` length-capped, a
  malformed entry dropped rather than failing the save, `n` renumbered
  over what survives. Citations persist only when `"description"` is in
  that submit's `ai_drafted_fields`; any other save clears them, same
  "editing/saving is a confirmation" convention `update_tool_agent_taxonomy`
  already applies. A new client-side guard closes the gap a purely
  server-side check couldn't: a one-time `input` listener on the
  description textarea clears both the AI-drafted flag and the citations
  the moment the admin types over a fresh draft, so a save right after
  hand-editing doesn't ship citations grounding text that no longer
  exists. **Also fixes a real pre-existing gap, as its own commit, per
  explicit direction rather than silently bundled in**: the "Add software"
  form never carried the `ai-drafted-fields`/`ai-drafted-confidence`
  hidden inputs at all, so a brand-new tool created straight from a
  Generate-description draft never recorded
  `description_needs_verification`/`description_ai_confident` — `Library.
  add_tool` gained both as optional parameters (default 0/None, every
  other caller unaffected). **Deliberately does not add a publish gate at
  this point in time**: unlike Agent taxonomy's Abacum-fix gate,
  Description had never hidden an unverified/low-confidence draft from
  public visitors, and this phase keeps it that way — only a Sources list
  is added. Flagged here as a known follow-up, not an oversight: a future
  phase could extend the same `agent_taxonomy_needs_verification`-style
  publish gate to Description if that's ever decided worth doing. **That
  follow-up has since shipped — see the "Description/Community profile
  publish gates" bullet below.** See ARCHITECTURE.md's Description
  grounding fix bullet and the `entity_citations` schema-table row for the
  full write-up.
- **Citations-API grounding fix, Phase 3 (2026-08) — the Community profile
  draft joins the grounding fix, as ONE shared citation set for all 23
  drafted fields, not one row per field (decision 5, Phase 0
  investigation).** Same stateless-AJAX structural shape as Description
  (`generate_community_profile`'s Generate route is `{name, url, existing}`
  only, no `community_id`), so citations travel through the browser the
  same way — `markAiCitations()` (already generic from Phase 2) now also
  populates the Community profile edit form's own
  `ai-drafted-citations`/`ai-drafted-citations-model` hidden inputs, and
  `_validate_citations_payload` is reused unmodified at the submit route.
  Persist only when at least one of the 23 profile fields is in the
  submitted `ai_drafted_fields` (the profile submit route's own existing
  `profile_ai_drafted` boolean, already computed for `needs_review`); any
  other save clears the row. **The one real behavioral difference from
  Description, following directly from "one row per draft, not one per
  field": a hand-edit to ANY of the 23 fields has to invalidate the whole
  shared set**, so the client-side clear-on-edit guard Description built
  for its single textarea is now attached to all 23 `cp-<field>` inputs
  after a Generate call, each one clearing the same shared
  `ai-drafted-citations` field. **Rendering is also one-per-page, not
  one-per-card**, since the 23 fields spread across 5 card sections plus
  the "Bottom line" verdict callout share one citation set: the public
  profile page (`/tools/communities/{slug}`) renders a single capped-at-5
  Sources list once, right after the Bottom line callout; the admin edit
  page (`/admin/tools/communities/{id}/profile`) renders the full uncapped
  list once, inside the same needs_review/"Mark reviewed" block the
  profile's one shared verify action already lives in. **Deliberately does
  not add a publish gate at this point in time**, same explicit
  out-of-scope call Description's Phase 2 made: the Community profile page
  had never hidden an unreviewed or low-confidence draft from public
  visitors, and this phase doesn't change that — flagged here as the same
  kind of known follow-up Description's own bullet above flags. **That
  follow-up has since shipped — see the "Description/Community profile
  publish gates" bullet below.** No schema change — `entity_citations`'s
  table comment already described this exact shape when Phase 1b wrote it.
  See ARCHITECTURE.md's Community profile grounding fix bullet for the
  full write-up.
- **Confidence indicator (2026-08) — genuine self-reported "Claude
  confidence: Yes/No" fields, tool Description/Competitive differentiation
  first, then extended to 12 of the Community profile draft's 23 fields.**
  A distinct fact from `*_needs_verification` (human review status) —
  the two combine (an unverified AND low-confidence field is the
  highest-risk state a reader can see, Abacum's case exactly). Tool side:
  `generate_tool_description`/`generate_tool_differentiation` gained a real
  `"confident": true|false` JSON key (matching `generate_tool_agent_taxonomy`'s
  existing pattern), stored in new `tools.description_ai_confident`/
  `competitive_differentiation_ai_confident` columns (NULL = no signal,
  written only alongside a fresh Generate this save — a new
  `ai_drafted_confidence` hidden input, parsed by
  `_ai_drafted_field_confidence`, mirrors `ai_drafted_fields`'s existing
  "field:1,field2:0" shape). **Displays permanently (2026-08 policy
  revision)** — originally shown only while `needs_verification=1`; Brian's
  explicit call reversed that: verification status and confidence are
  independent facts and both stay visible at all times, side by side,
  regardless of review state. The one condition that still hides the line
  is a raw `None` column value (no generation has ever reported a signal
  for that field) — never cleared on an unrelated resave, same as before.
  **Community profile draft — Phase 0 inventory + Brian's approval**
  identified 12 of the 23 fields as genuinely long-form/narrative and
  fabrication-risky (`linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS`:
  ideal_member, anti_fit, value_prop, business_model, format_reality,
  engagement_level, sponsor_relationship_note, application_friction,
  cost_value_verdict, notable_members, public_criticism, verdict_summary),
  explicitly excluding `founded_year` and the 7 short factual/categorical
  fields (not prose) — and, per Brian's explicit call, also excluding
  `stage_focus`/`jobs_program`/`team_or_individual` despite their being in
  `VOICE_REWRITE_FIELDS`: matching that pass's existing narrative boundary
  wasn't the goal, matching actual fabrication risk was, and those three
  read as categorical. `generate_community_profile`'s single Claude call
  (all 23 fields drafted together) now also returns one `"confidence"`
  object with exactly those 12 boolean keys, judged independently per
  field rather than one blanket verdict. 12 new `community_profiles`
  columns (`{field}_ai_confident`), same NULL-means-no-signal convention.
  **One real structural difference from the tool side, driven by
  `upsert_community_profile` being a full replace on every save (not a
  narrow COALESCE-based update)**: the submit route itself must decide
  every tracked field's confidence value on every save — the fresh
  model-reported value for a field (re)drafted this save, else that
  field's own previous value read back via `get_community_profile` first
  and carried forward unchanged, never silently cleared just because a
  *different* field on the same profile was regenerated. **Display is
  permanent, same 2026-08 policy revision as the tool side** — not gated
  on the shared whole-profile `needs_review` flag at all anymore (an
  earlier version of this feature gated it there, since the Community
  profile draft has never had per-field verification — see the
  `field_reviews`/Phase G reconciliation above — but confidence turned out
  not to need that gate either way: it's independent of review status).
  Since confidence is no longer tied to the whole-profile review flag,
  there's no all-or-nothing flattening to worry about — each field always
  shows its own real confidence value independent of the profile's review
  status.
- **Confidence indicator, Agent taxonomy follow-up (2026-08) — the same
  permanent "Claude confidence: Yes/No" line, added to the field this whole
  effort started from (the Abacum finding).** Agent taxonomy already had a
  `"confident"` self-report in `generate_tool_agent_taxonomy`'s JSON
  response, but it was never stored on its own — it only ever fed
  `agent_taxonomy_needs_verification` (`needs_verification = not
  confident`), a review-status flag, not a display fact. A new
  `tools.agent_taxonomy_ai_confident` column (same NULL-means-no-signal,
  COALESCE-write convention as `description_ai_confident`/
  `competitive_differentiation_ai_confident`) now stores the raw signal
  separately, written by `set_tool_agent_taxonomy_draft` alongside every
  fresh research draft, and rendered via the same `_confidence_indicator_html`
  helper — permanent, never gated on verification state, exactly like
  Description/Differentiation. **Deliberately does NOT touch either of
  Agent taxonomy's two pre-existing `needs_verification`-driven mechanisms**,
  both of which stay exactly as they were: the `_narrative_verify_widget`
  "Needs verification" badge/"Mark verified" button on the edit page (this
  still disappears once verified, same as it always has — only Description/
  Differentiation's confidence LINE is what became permanent, not every
  verification-status UI element on every field), and — more importantly —
  the public profile page's Abacum-fix publish gate, which still hides an
  unverified/low-confidence note from visitors entirely and shows it to an
  admin only, explicitly labeled "hidden from visitors." Flagging this
  distinction explicitly rather than silently narrowing scope: an instruction
  to "remove the gate" here could be misread as removing that publish gate
  too, which would undo the actual anti-fabrication fix the Abacum
  investigation produced — that gate is a different mechanism from the
  admin-edit-page confidence-display gate the other two fields had, and only
  the latter was ever in scope for the permanent-display policy change.
- **Confidence indicator — a third "Not yet assessed" state for NULL
  (2026-08 follow-up).** Live testing on Abacum's own edit page (its
  Description predates the confidence column by three days, so
  `description_ai_confident` is genuinely `NULL`) found the confidence line
  rendering nothing at all for a NULL value — confirmed as the intended
  original behavior (`confident is None` returned `""`), but wrong on the
  same reasoning the confidence-indicator feature itself was built on:
  silence is indistinguishable from broken. `_confidence_indicator_html`
  now renders three states, not two — `True` → "Yes", `False` → "No",
  `None` → "Not yet assessed" (a neutral `var(--muted)` color, no sanctioned
  green/amber pair fits "no signal") — so every field with the capability
  always shows some state. This is a superset change to the shared helper,
  so it applies uniformly to all three surfaces (tool Description/
  Differentiation/Agent taxonomy, all 12 Community profile fields) with no
  per-field code — every "hidden when no signal" test across
  `test_confidence_indicator.py`/`test_community_confidence_indicator.py`
  was renamed and rewritten to assert the new text instead of absence.
- **Description/Community profile publish gates (2026-08) — the two known
  follow-ups flagged above (Citations-API grounding fix Phases 2 and 3)
  now ship, extending Agent taxonomy's Abacum-fix publish gate to both
  fields at every public render site, no new mechanism.** A read-only
  Phase 0 investigation (no production DB access from this session, per
  the standing limitation noted elsewhere in this doc) confirmed the
  gate's exact shape to copy — reads `agent_taxonomy_needs_verification`
  directly, never the separate `agent_taxonomy_ai_confident` display fact
  — and both new gates follow it exactly: verified renders plain,
  unverified+admin renders with an inline "unverified—hidden from
  visitors" label, unverified+public falls into whatever branch an empty
  field already uses. Both review-state columns
  (`tools.description_needs_verification`, `community_profiles.
  needs_review`) are `NOT NULL DEFAULT 0` — confirmed by attempting a raw
  `UPDATE ... SET ...=NULL`, which SQLite itself rejects — so a legacy row
  predating either feature already reads as verified with no migration
  needed, and every gate still reads via `bool(row.get(...))` rather than
  a bare subscript as a defensive habit, not because a real NULL row is
  reachable. **Description** gates the profile page's hero subhead and
  Description card, the compare matrix's Description row (per-cell,
  mirroring the existing `_agent_cell` pattern), and — a genuinely new
  leak Agent taxonomy never had, found in Phase 0 — the `/tools/software`
  directory card: its `ALL_TOOLS` JSON keeps the raw description/summary
  text regardless of verification state (the admin Quick Edit panel needs
  it verbatim even when unverified), so the gate runs client-side instead,
  checking a `description_needs_verification` boolean against the page's
  existing `AUTHED` global before rendering `.tool-desc` or including the
  text in the search-match string — live-verified with a real
  headless-browser session (anonymous: blank card, zero search results for
  drafted text; admin: full text, an "Unverified—hidden from visitors"
  label, and a working search match), per this repo's UI-testing standard.
  **Community profile gates the ENTIRE drafted profile at once, not
  per-field** — confirmed as the right scope with Brian before building,
  since `needs_review` is already a single whole-profile flag and hiding
  only the 12 confidence-tracked fields while leaving `founded_year`/`cpe_
  eligible`/the Details card's Format contribution visible would read as a
  half-reviewed page rather than a clean not-yet-reviewed one. One
  `_display_profile` swap (the real profile dict when verified or admin,
  `{}` when hidden) feeds the Bottom line callout, its Sources list, all
  four grouped cards, and the profile-sourced Details-card lines alike;
  the compare matrix (which had no `authed` check at all before this)
  gates the same way per community. **Deferred, named together in the PR
  as one follow-up item**: the software/community Chat Matchmakers
  (`linklib/matchmaker.py`) feed raw description/summary into Claude's
  context unfiltered by `description_needs_verification`, and the
  directory card's `ALL_TOOLS` JSON still carries unverified
  `agent_taxonomy_note` text in page source for search purposes
  (pre-existing, not touched by this PR) the same way it now carries
  unverified description/summary — both are UI/search-level gates on a
  payload meant for a signed-in admin to read verbatim, not full data
  removal. See ARCHITECTURE.md's matching bullet for the full write-up.
- **Radical-transparency review standard (Gate-Extraction Phase 0/PR A,
  2026-09) — "nothing ever disappears," replacing every hide-from-visitors
  gate above, and fixing a real gap the Phase 0 gate inventory found:
  Competitive differentiation had no gate at all, on any surface, for
  either viewer.** Ratified by Brian as a fixed 3-state × 2-viewer table:

  | State | Visitor | Admin |
  |---|---|---|
  | Verified | content | content |
  | Populated, pending review | content + "under review" label | content + "unverified, visible to visitors" badge |
  | Empty | "{Field} not available." placeholder (was "not yet available" until 2026-09) (two deliberate contextual variants: "Description coming soon." and "This section hasn't been researched yet.") | same placeholder + a "go fill this in" prompt |

  Applied identically everywhere a gate existed: tools' Description/Agent
  taxonomy (per-field, unchanged column) now always render, badge-only;
  Competitive differentiation joins them for the first time (previously the
  one field the Phase 0 investigation found completely ungated — an
  unreviewed "Bottom line" rendered identically to a verified one, to every
  visitor, with no admin badge either); Communities' whole-profile
  `needs_review` no longer swaps `_display_profile` to `{}` — it's always
  the real profile, with the same badge applied per-card (the verdict
  eyebrow, each of the 4 group cards, the Founded/CPE-eligible Details
  lines) — **one flag driving N badges, deliberately kept as the one
  cross-entity divergence from tools' N-independent-flags-driving-N-
  independent-badges shape**, since collapsing the two into one mechanism
  would be a real data-model change (a future per-field community
  verification schema, sketched but explicitly parked below, not this PR's
  scope) rather than a copy change. Sources render alongside pending
  content now (previously suppressed by the same swap). The compare
  matrix's three per-field cells (`_agent_cell`/`_desc_cell`, plus a new
  `_diff_cell` — Differentiation's own compare row used to share the
  fully-generic, gate-blind `_row`/`_cell` with every other directory-level
  field) share one `_reviewed_cell` helper; Communities' `_profile_cell`
  mirrors it. **A real, previously-invisible bug this fixes as a side
  effect**: a populated-pending cell and a genuinely-empty cell used to
  render byte-identical text ("Not documented yet"/"Not available yet") to
  a visitor — indistinguishable, and the reason Competitive differentiation's
  missing gate went unnoticed for as long as it did. Since pending content
  now always shows its real text, this collision structurally can't happen
  anymore; the empty-only strings were also reworded for word-order parity
  ("Not yet available."/"Not yet documented.") as part of the same pass.
  Tier-2's "No details available." (a single empty field inside an
  otherwise-populated card) is unchanged — already visitor-visible, no
  admin prompt added, since the surrounding populated card already implies
  the edit page is one click away.
  **Matchmaker** (`linklib/matchmaker.py`) — the deferred follow-up named in
  the bullet above is now fixed by inclusion-with-disclosure, not exclusion:
  `_build_communities_context`/`_build_software_context` both return
  `(context, has_unverified)`; Software marks each unverified field inline
  (`"How it differs from competitors (unverified): ..."`); Communities
  (one whole-profile flag) adds a single leading note per unreviewed
  community (`"Note: this community's profile is unverified; treat the
  following details as provisional."`) rather than marking all nine
  profile lines individually. `_build_system` appends a standing
  disclaimer to the system prompt — `"Some catalog details above are
  marked unverified. Treat them as provisional, and say so if you
  reference them in your answer."` — only when at least one marker is
  actually present, plus an instruction to call out unverified content
  inline in the synthesized answer rather than presenting it as confirmed.
  **Read-only sketch, parked, not scoped for any PR** (per Brian's explicit
  call — the sketch obligation is satisfied by this note, not a future
  ticket): moving Communities to per-field verification, matching tools'
  shape, would need N new `*_needs_verification` columns (one per narrative
  field, replacing the single `needs_review`), a per-field "Mark
  reviewed"/badge UI (`_narrative_verify_widget` already generalizes to
  this — it was built for exactly this on the tools side), and a real
  regen-auto-trigger redesign: `generate_community_profile` drafts all 23
  fields in ONE Claude call, so "field X was freshly redrafted" is
  currently an all-or-nothing fact from that call, not independently
  knowable per field the way tools' three separate generation calls make
  it — the real trigger for ever doing this is Community profile
  generation itself moving to per-field calls, which hasn't happened and
  isn't scheduled.
  **Purely cosmetic/copy, no new gating mechanism**: every
  `*_needs_verification`/`needs_review` column, every write path, and
  `tools.needs_review` (the separate whole-record admin bookkeeping pill,
  confirmed during Phase 0 investigation to never gate visitor-facing
  content and untouched by this standard) are all unchanged — only what
  renders for a given (state, viewer) pair changed. See ARCHITECTURE.md's
  matching bullet for the full per-surface write-up and
  `tests/test_review_state_publish_gates.py`/
  `tests/test_matchmaker_publish_gate.py` for the regression coverage.
- **PR A.1 — empty-state consistency + visual QA fixes (2026-09), a fast-
  follow to the radical-transparency review standard above, from Brian's
  own post-deploy review of the live site.** No gate/state logic, no copy
  changes beyond one string, no schema changes — six small visual-QA items.
  1. **Key features' "Suggest one" link no longer renders against an empty
     list** — it made no sense when there's nothing shown for a missing
     feature to be missing *from*.
  2. **One empty-state visual treatment, everywhere — the dashed floating
     box (`_profile_admin_nudge`) is retired entirely**, replaced by
     `_empty_state_card(title, text)`: every empty profile-page section
     (Software: Competitors, Bottom line, Agent taxonomy, Description;
     Communities: Bottom line, each of the 4 profile-group cards, Similar
     communities) now renders as its OWN normal `.tp-card` + `<h2>` header
     holding one muted italic placeholder line — the same container it'd
     use if it had content — rather than a headerless dashed box. Per
     Brian's own ratified framing for this call: an accent (the dashed
     border before, the seafoam Bottom Line callout) marks real content,
     not its absence — empty now goes quiet instead of drawing a second
     kind of attention to itself. A Community profile-group card shows its
     own group title even when empty now, closing the one site of the
     seven where a visitor previously couldn't tell which section was
     missing (there was no header of any kind on that dashed box). Key
     features' pre-existing coming-soon prose (already `.tp-card`-wrapped)
     picked up the same `font-style:italic` for one visual language across
     all eight sites.
  3. **A real CSS bug on the Key features card, root-caused live before
     fixing**: `.tp-feature-flag-btn` (the per-row flag icon, invisible at
     rest via `opacity:0`) was an ordinary flex item pushed to the row's
     end via `margin-left:auto`, inside a `<li>` that's `display:flex;
     flex-wrap:wrap`. When a row's visible content (name + an Add-on/AI
     tag) didn't leave room for the button on the same line, flex-wrap
     pushed it — invisible, but still a real box with real height — onto a
     line of its own, rendering as a "phantom" blank row between features
     (reported on Numeric's card, between "Continuous reconciliation
     monitoring [ADD-ON]" and the next feature). Confirmed via a real
     Playwright bounding-box height comparison (a row with a tag measured
     59px vs. 35px for a plain row) before writing any fix — not assumed
     from a data explanation, since the MCP introspection tool available
     this session has no WHERE/offset support and couldn't pull the exact
     production rows to check for a data artifact directly. Fixed by
     taking the button out of the flex flow entirely
     (`position:absolute`, top-right of the `<li>`, which reserves the
     space via a new `padding-right`) — re-verified the same way post-fix:
     the phantom line is gone, genuine text-wrap for a long feature name is
     unaffected.
  4. **Equal 22px spacing above and below the tool profile page's "Bottom
     line" seafoam callout** — it had `margin-bottom` but no `margin-top`,
     so the category chips row directly above it sat flush against its top
     edge. Applied to both the populated and empty states.
  5. **Community Description's "No description yet." joins the approved
     cross-entity string family** — the one empty-state string in the
     whole standard that never matched any of the others — now
     "Description coming soon." / "...Add one from the edit page.", same
     as Software's, inside the same `.tp-card` it always used.
  6. **Logo audit (investigate-first, per Brian's explicit amendment to the
     original ask)** — `_logo_box()`'s CSS (`object-fit:contain`, correctly
     upscale-capable at every on-site tile size) was confirmed NOT the bug:
     a logo rendering tiny inside its tile (Airbase, Airwallex reported on
     the directory) is a property of the source asset — a low-resolution
     raster, or (more likely for a wordmark) a lot of built-in transparent
     padding around a small mark, which `contain` faithfully preserves.
     Brandfetch itself is presumed working, and a manual logo-override
     process already exists as the designed fallback, so this ships an
     audit, not a pipeline change: `scripts/audit_tool_logo_dimensions.py`
     — read-only, **no new dependency** (parses PNG/JPEG/GIF/WEBP/ICO
     headers and SVG width/viewBox attributes by hand — the project has
     deliberately avoided Pillow before, see the App screenshot Phase E
     note on choosing client-side Cropper.js for the same reason) — flags
     any already-downloaded logo whose smaller pixel dimension is below a
     floor or whose aspect ratio is too lopsided for a square tile, as a
     hand-replacement worklist for the existing manual-override admin UI.
     Makes no Brand API calls, no file writes, no DB writes. **Genuinely
     unverified against the real corpus from this session** — the logo
     files live on the Railway volume beside `library.db`, which this
     session has no filesystem or network path to (confirmed: this
     session's `/mcp` DB-introspection access covers tables, not the
     filesystem) — only the header-parsing logic itself is unit-tested,
     against small hand-built sample files in each format. Registered in
     `/admin/system/scripts` per the standing scripts-registry rule. Run it
     against the real database via `railway ssh` (opens a shell in the
     service container, same convention as `mint_api_token.py`'s own
     docstring — the absolute `/data/library.db` path matters, since a
     relative `--db` silently resolves against whatever directory the
     shell happens to be in and fails with no error), then inside that
     shell: `python -m scripts.audit_tool_logo_dimensions --db
     /data/library.db` (add `--csv /data/logo_audit.csv` for a full
     per-row export, or `--min-px`/`--max-ratio` to tune the two
     thresholds — see the script's own module docstring for the reasoning
     behind each).

  See ARCHITECTURE.md's matching bullet for the same write-up in that
  doc's own voice, and `tests/test_empty_state_visual_qa.py` (13 tests,
  covering all six items end-to-end via TestClient) /
  `tests/test_audit_tool_logo_dimensions.py` (10 tests, covering the
  header-parser against hand-built PNG/GIF/JPEG/WEBP/SVG samples) for the
  regression coverage.
- **Logo Tile Fit fix (2026-09) — the PR A.1 logo audit's own "undersized-or-
  padded, presumed Brandfetch-side, not our bug" read turned out to be
  half right: the real cause of most of the 69/75 flagged logos was a
  wrong ASSET-TYPE preference in `linklib/brandfetch.py`, not source-image
  padding.** A follow-up investigation (Step 0, reported and approved
  before building) found `best_logo_asset()` ranked Brandfetch's `"logo"`
  type (the full wordmark/lockup) above `"icon"`/`"symbol"` (the square
  mark) — backwards for every on-site render, which is always a fixed
  square tile (`_logo_box()`: 64px directory, 56px profile header, 32px
  Competitors table). A wide wordmark SVG shrunk by `object-fit:contain`
  to fit a square box leaves most of the box empty — the actual mechanism
  behind Airbase (4:1), Airwallex (7.3:1), NetSuite (14:1), and ~45 more
  flagged assets. Only Cube and Kintsugi are the genuinely-undersized-
  raster case PR A.1 originally described; everything else is this
  asset-type bug. **Fixed at the one shared choke point** (`linklib/
  brandfetch.py`'s own docstring: "there is exactly ONE implementation of
  how we talk to Brandfetch," used by `scripts/backfill_logos.py`'s
  monthly batch AND `webapp/app.py`'s `_live_refetch_logo` admin
  "Revert & re-fetch" button) — a new `TYPE_PREFERENCE = ("icon", "symbol",
  "logo")` replaces the old `type_rank`'s binary "logo"-first check, so
  BOTH ongoing fetch paths get the fix automatically, not just the
  historical backlog. The wordmark is still selected as a fallback when a
  brand publishes no square asset at all — never worse than the old
  behavior. `best_logo_asset()`/`fetch_logo_asset()` grew a third return
  value, `asset_type` (`"icon"`/`"symbol"`/`"logo"`/`""`), so a caller can
  tell "found a real square mark" from "fell back to the wordmark" without
  a second API call — both existing call sites' unpacking updated in the
  same PR. **New run-once script, `scripts/refetch_lopsided_logos.py`**,
  re-fetches the historical backlog: deliberately does NOT read
  `/data/logo_audit.csv` (a point-in-time snapshot this session had no
  filesystem access to anyway — same Railway-volume limitation as the
  audit script itself) — it re-derives the "lopsided" candidate set LIVE
  by reusing `audit_tool_logo_dimensions.py`'s own dimension-probing logic
  against whatever's actually on disk right now, which is both more
  correct (never stale) and, as a side effect, a clean non-hardcoded way
  to keep Cube/Kintsugi out of scope: they're flagged `"undersized"`, not
  `"lopsided"`, so the lopsided-only scope structurally excludes them —
  kept alongside a defensive by-name skip too, per explicit instruction
  ("do not touch Cube or Kintsugi"), belt and suspenders. **Preview mode
  makes zero Brandfetch calls** (same "safe by default" discipline as
  `backfill_logos.py` itself — the free tier is 100 requests/month, and a
  preview that spent quota just to show "what would change" would double
  the cost of every run); it lists exactly which records are currently
  flagged and why. `--apply` calls the (now icon-preferring)
  `fetch_logo_asset` for real and only overwrites when it actually gets a
  different (icon/symbol) asset — a record where Brandfetch still returns
  only the `"logo"` wordmark is left completely untouched (never
  re-downloaded with the same shape) and reported as a residual case for
  Brian's existing manual logo-override process; per the standing
  write-then-read-back practice, every write this run makes is re-SELECTed
  and verified before the run reports success. **Never registered in
  `_SCRIPT_REGISTRY`/`/admin/system/scripts`**, matching this repo's actual
  practice for a genuinely one-time backlog-clearer (confirmed by
  precedent — none of `fix_spaced_em_dashes.py`, `migrate_original_
  content.py`, and the repo's many other one-off scripts are registered
  either; the registry is for ongoing-cadence recurring/diagnostic tools)
  rather than the registry rule's literal wording — it'll be `git mv`'d
  into `scripts/archive/` once its one apply run is confirmed, per the
  standing "archive a one-time script as soon as its run is confirmed"
  rule. **Renderer untouched, deliberately**: `_logo_box()`'s CSS
  (`object-fit:contain`) was already correct per PR A.1's own finding —
  this is a fetch/asset-selection fix, not a renderer fix; a residual
  wordmark-only logo after re-fetch relies on that same existing,
  already-correct fallback (no force-crop) rather than any new rendering
  code. **Not run yet as of this writing**: Brandfetch's free-tier quota
  (100/month) needed to reset (Sept 9) before `--apply` could run without
  risking starving the routine monthly `backfill_logos.py` batch of the
  same shared quota — Brian ran the (zero-cost) preview beforehand to
  confirm the candidate list, and holds `--apply` until quota resets, per
  his own explicit call. See ARCHITECTURE.md's matching bullet and
  `tests/test_brandfetch.py`/`tests/test_refetch_lopsided_logos.py` for
  the full write-up and regression coverage.
- **Logo.dev replaces Brandfetch as the CFO Toolbox logo source (2026-09) —
  Brandfetch's one-time 100-credit free tier confirmed permanently
  exhausted (non-resetting, not a monthly cap), affecting not just the
  50-tool backlog at the time but every future tool/community submission
  `scripts/backfill_logos.py` would otherwise handle.** Investigated first:
  Hunter.io and NinjaPear (the two "no signup" free alternatives) were both
  ruled out — each returns exactly one image per domain with no way to
  request or identify a square asset, the same architectural gap Brandfetch's
  own pre-PR-479 default already burned this project once on. Logo.dev's
  free, uncapped (500K requests/month, no credit card) image endpoint
  (`img.logo.dev/:domain`) is different: per Logo.dev's own docs, it's
  explicitly scoped to return "the symbol (also called the icon or mark)"
  — the square asset, by design — a real spot-check (Brian, RightRev)
  confirmed this live before the switch was built. **Genuinely a swap, not
  a cascade**: Brandfetch's Brand API is guaranteed to fail forever, so
  trying it first and falling through to Logo.dev on every call would just
  be a doomed HTTP round-trip added to every request, not a real fallback.
  New `linklib/logodev.py` is a from-scratch module (not a Brandfetch
  wrapper) — simpler than `linklib/brandfetch.py` by design, since
  Brandfetch's Brand API returned a JSON document with several logo variants
  to rank (`best_logo_asset()`'s whole `TYPE_PREFERENCE`/theme/format
  cascade), while Logo.dev's plain image endpoint already IS the square-icon
  request — one HTTP call, no ranking logic needed. `fallback=404` is always
  forced on every request; without it, a domain with no real logo returns a
  200 with a generated monogram, which would silently write fake placeholder
  art into the database as if it were a real vendor logo. **`linklib/
  brandfetch.py` is untouched, byte-for-byte, and unused by default** — kept
  as a dormant reference, not deleted, per the explicit call that restoring
  Brandfetch (if credits are ever renewed) should mean swapping an import
  back, not reconstructing anything. Both existing callers —
  `scripts/backfill_logos.py`'s batched run and `webapp/app.py`'s
  `_live_refetch_logo` (the admin "Revert & re-fetch" button) — switched
  their import from `linklib.brandfetch` to `linklib.logodev`, with no
  other restructuring beyond `download_asset`'s call shape changing from
  `(src_url, dest_path, session)` to `(image_bytes, dest_path)`, since
  Logo.dev's one HTTP call already carries the image bytes — there's no
  second request left for `download_asset` to make. `DEFAULT_LIMIT` in
  `backfill_logos.py` moved from 90 (a safety margin under Brandfetch's
  100/month cap) to 500 (comfortably the whole catalog in one pass, since
  Logo.dev's free tier has no comparable monthly ceiling to batch around).
  **Attribution**: Logo.dev's free tier requires a single site-wide credit
  link for commercial use, not something per-logo — confirmed from their
  own docs, which explicitly name a page footer as an acceptable placement.
  Added once, in `_page()`'s shared footer, rather than resolving the
  genuinely ambiguous "is a personal site with a working directory
  commercial" question — the link costs nothing either way, so it ships
  regardless of which answer is technically correct. The same footer row also
  carries "Source on GitHub" (`SOURCE_REPO_URL` in `webapp/app.py`, a hardcoded
  constant, new tab, no styling of its own; there is one `<footer>` in the app, so
  every `_page()` page has it). See ARCHITECTURE.md's
  matching bullet and `tests/test_logodev.py`/`tests/test_logo_override.py`
  for the full write-up and regression coverage.
- **Gate-Extraction PR B (2026-09) — the radical-transparency review-state
  decision (verified / populated-pending-review / empty, PR A above)
  extracted into `linklib/gates.py`, the single source of truth for the
  state, badge copy, empty-state placeholder copy, and the matchmaker's
  unverified-content markers/disclaimer.** Route-level gate logic is
  retired — `webapp/app.py`'s `_review_state_badge`/`_empty_state_card`/
  `_empty_state_text` are now thin HTML-side wrappers over `gates.state_for`/
  `gates.badge_text`, and the compare matrices' near-duplicate `_reviewed_cell`/
  `_profile_cell` logic collapsed into one shared `_compare_cell_html`.
  Behavior-identical to the post-PR-A baseline — see ARCHITECTURE.md's
  matching bullet for the full write-up, including: the module-split
  rationale (linklib stays HTML-free so MCP Phase 3's tools can import
  `gates` directly without risking an HTML fragment leaking into a tool
  result); the two consolidations from the approved Phase 0 inventory
  (#10, row-existence vs. per-cell gating, now `gates.any_populated` +
  `_compare_cell_html` shared by both entity types; #6, corrected mid-PR
  from a mis-targeted "stale PR A comment" to its real target —
  `_public_community()`, a confirmed-no-op community-dict choke point,
  retired outright, plus a genuinely stale comment elsewhere that had
  claimed it still stripped a sentinel); and the one real, pre-existing
  copy divergence found and deliberately preserved rather than unified
  (the Software directory card's client-side JS badge has always used a
  capitalized variant of the profile-page/compare-matrix badge copy —
  frozen as a second named constant, not merged, per "zero copy changes").
  Deliberately out of scope, per the approved plan: the whole-record
  admin review-workflow mechanism (`_narrative_verify_widget`,
  `_review_status_pill_html`/`_action_html`/`_block_html`,
  `_confidence_indicator_html`/`_confidence_badge_html`) — none of it
  implements this display gate, PR A never touched it, and this
  extraction doesn't either — and the `tp-verify`/`cc-verify`/
  `tool-desc-verify` CSS class names (a rename rider, explicitly
  deferred). See `tests/test_gates.py` and `tests/
  test_gates_compare_equivalence.py` for the new coverage; PR A's own
  `tests/test_review_state_publish_gates.py`/
  `tests/test_matchmaker_publish_gate.py` pass unchanged.
- **Community profile edit page — grouped into 5 labeled sections, a
  consistent width rule, and confidence badges moved inline (2026-08
  follow-up).** Live testing found the page's 23 fields rendering as one
  flat, ungrouped list, mixing full-width single-column textareas (the top
  ~12) with a paired 2-column grid (the bottom ~9, plus Founded year
  sitting alone outside any grid) with no visible logic distinguishing
  which fields got which treatment. Restructured, proposed and approved
  before building (not a mechanical fix):
  - **5 section headers**, splitting cleanly along `COMMUNITY_CONFIDENCE_FIELDS`'
    own boundary — "Who it's for" (Ideal member, Anti-fit, Value
    proposition), "The member experience" (Format in practice, Engagement
    level, Application friction), "Business & sponsorship" (Business model,
    Sponsor relationship, Cost vs. value verdict), "Reputation & verdict"
    (Notable members, Public criticism, Verdict), "Quick facts" (the 11
    structured/miscellaneous fields — named "Quick facts" rather than
    "Program details" since Founded year/CPE/etc. aren't thematically
    "program" details, just the catch-all bucket of short factual fields).
    Headers reuse the exact `<h2>`/border-top style the Software edit page
    already uses between its own sections (`_section_header`), not a new
    pattern.
  - **One consistent width rule, applied to all 23 fields, not just the
    bottom section**: narrative/qualitative fields (the 12 confidence-bearing
    ones) stay full-width textareas; short factual/categorical fields become
    one shared paired 2-column grid (`_short_field`/a new `_num_field` for
    Founded year, which now joins the grid instead of sitting alone outside
    it). Resources included stays full-width — it's a described list, not a
    categorical value, so it's the "Quick facts" section's intro field
    rather than being squeezed into the grid with the truly short fields.
  - **Confidence badge moved from a block-level paragraph below the textarea
    to a compact inline badge beside the label** (`_confidence_badge_html`,
    Community-profile-only — the 3-field Software profile keeps
    `_confidence_indicator_html`'s block treatment unchanged, since crowding
    was never reported there). 12 stacked "Claude confidence: Not yet
    assessed" sentences read as noisy once the page was grouped; the inline
    badge matches the "Needs verification" badge's own existing
    inline-next-to-label precedent (`_narrative_verify_widget`) rather than
    inventing a new position. Text is unchanged, still naming "Claude"
    specifically — only position/styling changed.
  - **Verified, not just built**: the label/badge row uses `flex-wrap:wrap`
    specifically so the badge can't crowd a required field's `*` (Ideal
    member, Verdict) on a narrow viewport — confirmed with a real Playwright
    render at 390px and 320px (the widest badge text, "Claude confidence:
    Yes/No", on both required fields) showing zero overlap at either width,
    not just asserted from the CSS.
- **"Save and continue" near Description (tool-edit-consistency item #7,
  2026-08 follow-up) — the mechanism existed, the second button didn't.**
  `save_action=continue` (redirect back to the same tool's edit page with
  fresh data, instead of the admin list) was already wired up and already
  had a button — but only in the page-bottom action row alongside "Save
  changes," on a Software edit page that's long enough (Business summary,
  Agent taxonomy, Screenshots, Key features, Competitors) that reaching it
  means scrolling past everything else. A live check on Abacum's edit page
  found no second button near Description and no PR summary mentioning one
  shipped there — approved item #7 was specifically a second button placed
  right after Description, not a relocation of the existing one. Added:
  a second `<button form="tool-edit-form" name="save_action"
  value="continue">`, same form/name/value as the bottom one, right after
  Description's verify action/confidence line/review line. No route change
  — `admin_tools_edit_submit` already branches on `save_action=="continue"`
  regardless of which button posted it. Scoped to the Software edit page
  only, matching what was actually reported (Abacum); the Community profile
  edit page's own "Save and continue" (a from-scratch, separate mechanism —
  see `admin_community_profile_submit`) already existed at the bottom of
  that page too and wasn't part of this report, so it's untouched.
- **Phase P — edit-page layout reorg, and why `tool_competitors`/
  `community_competitors` did NOT get renamed alongside `differentiation_note`.**
  Both Software's and Communities' edit pages were reorganized into labeled
  sections (Company/Community Details, Categories, Business summary/Program
  details, Competition, Screenshots) matching new display labels on the
  Software profile page: "Competitors" -> "Core competition" and "How this
  differs from the competition" -> "Competitive differentiation." The
  Part 0 investigation before this build treated those two renames very
  differently. `differentiation_note` is a single free-text column with one
  obvious backing field, so it and its companion
  `differentiation_needs_verification` were renamed to
  `competitive_differentiation`/`competitive_differentiation_needs_verification`
  via a real migration (`ALTER TABLE tools RENAME COLUMN`, added to the
  existing migration list in `linklib/db.py` — SQLite raises the same
  `OperationalError` on a column that's already been renamed as it does for
  `ADD COLUMN` on a column that already exists, so this fits the established
  idempotent-migration-list pattern with no new mechanism). "Competitors" is
  different: there's no single column to rename, only a join table
  (`tool_competitors`/`community_competitors`) plus a cluster of function/
  route names (`list_tool_competitors`, `suggest_tool_competitors`,
  `generateCompetitorMatches`, `/admin/tools/{id}/competitors/*`, and their
  Communities mirrors) — renaming all of that to track a Software-profile-
  page display-label change would be broad, code-only churn with no
  corresponding user-facing payoff, and Communities' "Similar communities"
  label was deliberately never "Competitors" in the first place (communities
  don't compete for a buyer's dollar the way software tools do), so a
  mechanical `tool_competitors` -> `tool_core_competitors` rename would have
  had to either also relabel Communities' internals to match (wrong — no
  Community display-label changed) or leave the two entity types' internal
  naming inconsistent with each other for no benefit. Kept as-is: only the
  Software edit page's on-screen copy changed ("Competitors" heading ->
  "Core competition" subheading); the schema, every function name, and every
  route path are untouched. `scripts/archive/rename_differentiation_columns.py`
  (dry-run/apply/write-then-read-back, same convention as
  `scripts/backfill_logos.py`/`scripts/archive/migrate_app_screenshot_from_product_flag.py`)
  exists alongside the automatic migration for manual pre-migration of a
  standalone DB copy via `railway ssh`, not as a substitute for it — see
  that script's docstring for the distinction. A stray `repeat(4,1fr)` CSS
  Grid on the Categories checklist (both the Software "Add" and "Edit" forms)
  was the actual cause of the pre-existing mobile-portrait horizontal-scroll
  bug on the edit page — `1fr` tracks have an implicit min-width based on
  each cell's own min-content size, so four columns of checkbox labels
  couldn't shrink below their un-wrapped text width and forced the whole
  page wider than a narrow viewport ("CSS Grid blowout"); fixed by switching
  to `repeat(auto-fit,minmax(150px,1fr))`, whose explicit non-`auto` minimum
  overrides that implicit behavior. The reported footer gap on mobile was
  the SAME root cause, not a separate bug: `.site-footer` has no `max-width`
  of its own, so once the grid blowout forced the page to scroll
  horizontally, the footer rendered at the page's normal (un-scrolled)
  width while everything else extended past it — confirmed by the fix
  eliminating both symptoms together.

- **Feature Taxonomy (2026-08, Phase 0 + 1) — replaces `tool_features`' flat
  free text with a governed Category → curated feature list → tool-feature
  link model, starting with three seeded categories.**
  `docs/FEATURE_TAXONOMY.md` (committed verbatim from Brian's draft rules
  doc) is canon for naming, curation, designations, sourcing, and the
  review gate — read it before touching any of this, rather than
  re-deriving the rules from the schema. **Phase 0 investigation surfaced
  one real blocker before any code was written**: the pilot's three
  category names (`ERP & Accounting`, `FP&A Planning`, `Close Management`)
  matched none of the live 15-tag `tool_categories` vocabulary — live has
  separate `ERP`/`Accounting`/`FP&A` pills and no Close Management pill at
  all (confirmed by code archaeology against `scripts/seed_tools.py` and
  the `tool_categories` table's own `ARCHITECTURE.md` enumeration; this
  session had no access to the production `library.db` itself, which lives
  only on the Railway volume). Flagged rather than guessed past — Brian's
  resolution: `category_features.category_id` FKs to the real
  `tool_categories` (not a parallel feature-only taxonomy), reusing `ERP`
  and `FP&A` as-is, and adding `Close Management` as a genuine new pill —
  created by the seed script if missing, tagged additively onto exactly the
  three tools the seed CSV data itself names (FloQast, Numeric, Ledge; no
  existing tags removed, and no other tool was hunted down or retagged).
  Also found: NetSuite's live `tools.name` is `"NetSuite (acquired by
  Oracle)"`, not the pilot's plain `"NetSuite"` — the replacement seed CSVs
  Brian supplied use the live name directly, so no alias/fuzzy-match logic
  was needed. **Schema** (`linklib/db.py`): `category_features`
  (category_id FK, name unique per category — not globally, since the same
  capability legitimately recurs across categories per rules-doc §2 —
  definition, pointer_note, sort_order, retired_at for soft-retirement);
  `tool_feature_links` (one row per tool×feature, UNIQUE(tool_id,
  feature_id), availability CHECK'd to `native`/`add_on`, ai_enabled,
  verified_as_of required, note, source_url — every vendor-specific
  designation lives on the link per rules-doc §6, never on the feature);
  `feature_review_queue` (source CHECK'd to `admin`/`scan`/`public`, status
  `pending`/`approved`/`edited`/`denied`, payload as JSON, submitter fields
  nullable now for the not-yet-built public channel). `tools.suite_note`
  (nullable free text) is the rules-doc §5 "beyond the office of the CFO"
  suite-membership notation — independent of the feature tables, no home
  existed for it among the tool's other narrow-update text fields.
  **Seeding**: `scripts/seed_feature_taxonomy.py` reads three CSVs in
  `scripts/seed_data/` (derived from a real nine-vendor pilot — Rillet/
  Campfire/NetSuite → ERP, Runway/Abacum/Aleph → FP&A, FloQast/Numeric/
  Ledge → Close Management), aborts loudly on any category/tool/feature
  name it can't resolve rather than guessing, and is fully idempotent
  (re-running skips already-seeded `category_features` rows, upserts
  `tool_feature_links`, skips already-queued `feature_review_queue`
  entries) — verified locally by seeding a fresh DB twice and confirming
  the second run added zero new rows. **The queue launches populated, not
  empty**: `scripts/seed_data/seed_review_queue.csv`'s 15 verified-but-
  unapproved proposals (`source='scan'` — one is a 3-tool "Headcount &
  Workforce Planning" proposal that expands into 3 links, per its own
  `payload.links`) seed straight into `feature_review_queue` alongside the
  category/link tables, a deliberate part of the design so the Feature
  Review Queue admin page isn't a blank state on day one. Also seeds
  NetSuite's placeholder `suite_note` (`Library.set_tool_suite_note`,
  Brian's own wording from the build brief) — write-once, not
  re-clobbering: skipped on any re-run once the column is non-empty, so a
  later hand-edit through the admin edit form survives a re-seed. Prints
  resolved category ids and a write-then-read-back row count per table
  (`category_features`/`tool_feature_links`/`feature_review_queue`, plus
  the Close Management tag count and NetSuite's `suite_note` value), per
  the standing production-fix rule. **Admin** (all under
  `/admin/tools/software/*` — see the admin URL convention note below —
  admin-only, no public
  rendering changes this phase): Manage Features
  (`/admin/tools/software/features`, per-category list/add/edit/retire, same
  inline-editable-row pattern as `/admin/tools/software/categories`); Manage Tool
  Features (a checklist section on each tool's own
  `/tools/software/{slug}/edit` page, one sub-section per category the tool
  belongs to that actually has a curated list yet — checking a box
  upserts a link, unchecking deletes it, a direct admin edit that bypasses
  the review queue on purpose, the same way editing any other tool field
  on that page does); Feature Review Queue
  (`/admin/tools/software/feature-review-queue`, grouped by source, Approve/Deny —
  the approve form doubles as the edit form, every proposed value a real
  editable input pre-filled from the payload, so "edit-then-approve" is the
  same action as a verbatim approval rather than a second mechanism).
  **Legacy coexistence**: `Library.category_has_features(category_id)`
  is the read-time branch the public Software profile page's Features card
  will use once rendering is wired up (Phase 2, out of scope this phase,
  needs real brand/visual spec work) — a tool's category with a curated
  list renders the governed model, everything else keeps rendering the
  untouched legacy free-text `tool_features` Features card exactly as
  before. `docs/BUILD_PLAN.md`'s Phase 8 (previously "Feature Families",
  never built) is rewritten to describe this actual model — **there are no
  Feature Families**: a flat curated list per category, ordered by
  `sort_order`, is the whole grouping mechanism. Out of scope this phase,
  unchanged from the original plan's intent: public profile-page
  rendering, the recurring AI scan tool, the public feedback/suggestion UI,
  and a governed-model Compare view. **Admin URL convention, decided in
  this PR**: software-directory admin lives under
  `/admin/tools/software/*` (communities admin will follow the same
  `/admin/tools/communities/*` convention later, in Phase 1b). The two
  brand-new Feature Taxonomy routes (Manage Features, Feature Review
  Queue) launched directly under that prefix — they never existed on
  `main` before this PR, so there was nothing to redirect. The
  pre-existing `/admin/tools/categories` page moved to
  `/admin/tools/software/categories` in this same PR to match, WITH a
  301 redirect kept at the old URL (same precedent as
  `/admin/library/backfill`'s own redirect stub) since it was a real
  bookmarked admin tool, not a brand-new route. Finishing this convention
  across the rest of the Toolbox admin (communities admin routes, the
  per-tool edit pages, and any other stragglers) is Phase 1b — a
  separate, investigate-first PR.
- **Admin URL convention, Phase 1b PR 1 — the rest of the Software stragglers
  moved, the categories redirect removed, Benchmarking renamed to
  Resources.** The Phase 0 investigation for this PR inventoried every
  admin-gated Toolbox route against the convention above and found
  Communities already fully conformed (no changes needed there) — the real
  gap was ~28 Software route definitions still living at bare
  `/admin/tools/{tool_id}/*` and a handful of flat `/admin/tools/new`,
  `/admin/tools/leads`, `/admin/tools/name-duplicates*`,
  `/admin/tools/generate-description` pages. All of those moved to
  `/admin/tools/software/*` in this PR — **full cutover, no redirects**,
  unlike the categories move in the Feature Taxonomy PR: these are
  POST-only, form/JS-driven action routes (approve, reject, delete,
  quick-edit, screenshot/app-screenshot recapture+upload, research refresh,
  agent-taxonomy/description/differentiation verify, competitors add/
  remove/generate-matches/add-selected, feature-links save,
  generate-differentiation) plus a few admin-nav-linked list/form pages —
  not the kind of thing anyone bookmarks the way a page like
  `/admin/tools/categories` plausibly was. The categories redirect itself
  (`/admin/tools/categories` → `/admin/tools/software/categories`, shipped
  in the Feature Taxonomy PR) was removed outright in this same PR, per
  Brian's explicit call — no legacy `/admin/tools/*` URL survives at all
  after this PR. **One deliberate exception, carried over unchanged**: the
  legacy `tool_features` CRUD routes (`/admin/tools/{tool_id}/features/*`)
  were NOT renamed — they're deleted outright in Phase 1b PR 2 (full legacy
  retirement), so moving them first would just be churn on code about to be
  deleted. `tool_feature_links`' own checklist-save route
  (`/admin/tools/{tool_id}/feature-links/save` — a different mechanism,
  the governed model, not the legacy one) DID move, since it isn't going
  anywhere. **Benchmarking Resources renamed to Resources** — URL/copy only,
  no schema change: `/tools/benchmarks` → `/tools/resources` (a real 301 kept
  at the old URL, unlike the admin moves, since the public page is
  indexable — same reasoning as the Feature Taxonomy PR's categories
  redirect, just on the public side instead of admin), `/admin/tools/
  benchmarks` → `/admin/tools/resources` (no redirect, same as every other
  admin move in this PR), and every "Benchmarking"/"Benchmarking resources"
  page title, h1, nav label, and admin-page copy relabeled to "Resources"
  (sentence case). The `benchmarks` table, its columns, and every
  `Library.*_benchmark*` method keep their original names — this is a
  user-facing rename only, not a data-model one. **Two things investigated
  and deliberately left alone**: the public `/tools/software/{slug}/edit`
  and `/tools/communities/{slug}/edit` pages are a sanctioned
  resource-adjacent URL pattern (same-resource "/edit" suffix, discoverable
  right next to the public profile page it edits), not admin-tree
  stragglers of the same kind as the routes above — moving them under
  `/admin/*` was considered and rejected as a materially bigger, riskier UX
  change than a mechanical rename, outside what the convention was meant to
  cover. The convention itself is now stated as
  `/admin/tools/{software|communities|resources}/*`.

- **Feature Taxonomy, Phase 1b PR 2 — the public "Key features" card ships,
  and legacy `tool_features` is retired completely from code.** The
  governed model (`category_features`/`tool_feature_links`) has been live
  in the schema and admin-editable since Phase 1, but nothing public ever
  rendered it — the Software profile page still showed the legacy flat
  free-text `tool_features` card, or nothing at all for the ~90% of tools
  with no legacy rows. Fixed with `_software_key_features_card`, which
  ALWAYS renders now: real feature names (sentence-cased via
  `_sentence_case_feature_name` — a known, accepted limitation of that
  heuristic is that it also lowercases a genuine brand name landing
  mid-name, e.g. "Slack" in "Slack / Email Collaboration Triggers"; proper-
  noun detection was judged out of scope for this pass) grouped by category
  only when a tool's links span more than one seeded category, with
  Add-on/AI tags per link, for a tool that has links — a directional
  coming-soon state ("Coming soon—we're mapping this tool against our
  curated feature taxonomy.") for the rest, so "not mapped yet" reads as an
  honest statement rather than an empty card. Same PR retires the legacy
  table completely: the schema definition, all five CRUD methods, the five
  admin CRUD routes (`/admin/tools/{tool_id}/features/*` — the one route
  family Phase 1b PR 1 deliberately left unmoved specifically so it could
  be deleted here instead), the edit page's legacy Features section, the
  needs-verification banner's now-dead feature-count clause, and the
  compare page's legacy Features comparison row (removed outright, not
  migrated to the governed model — a governed-model Compare view is
  already reserved as later/out-of-scope work in `docs/BUILD_PLAN.md`
  Phase 8, and this PR's brief only specified the profile-page card).
  `linklib.enrich.generate_tool_features` is narrowed to
  `generate_tool_agent_taxonomy` — it keeps the real-crawl grounding
  mechanism (still worth it for the agent-taxonomy summary alone) but no
  longer drafts feature rows in the same call, since curated features are
  now a hand-curated admin checklist, not an LLM-drafted first pass.
  `scripts/enrich_tool_features.py` is renamed to
  `scripts/enrich_agent_taxonomy.py` (`git mv`) and rewritten to match.
  `scripts/drop_legacy_tool_features.py` is delivered (guarded per the
  Article purge flow's own preview/typed-confirm/same-day-backup pattern,
  ending in `PRAGMA integrity_check`) but deliberately not executed as part
  of this PR — Brian runs it by hand via `railway ssh` once this PR is
  deployed and verified live, per the standing "human-run, never a boot
  hook" rule for destructive one-off scripts. See ARCHITECTURE.md's
  "Feature Taxonomy, Phase 1b PR 2" section for the full enumeration of
  every remaining `tool_features` code reference and its disposition — the
  standing rule this phase established (see "No dead data" below) starts
  here as the reference case for what a real, complete retirement looks
  like: everything that once read or wrote the table is gone before the
  data itself is dropped, not the other way around.

- **Feature Taxonomy, Phase 1c — Manage Features becomes a single pivot
  table; admin dashboard "Software" sub-group; sentence-case copy standard;
  pending-count badges scoped down; review-queue merge gets a real
  confirmation step; feature names get recased.** Managing ~22 categories
  across N per-category subpages meant constant page-hopping — Phase 0
  investigation confirmed the redesign target (one collapsible group per
  category, all on one page) and audited every "awaiting review" entity in
  the database as a review-flow consistency pass (deferred to a future
  harmonization decision, not acted on here). Ships as one PR:
  - **`/admin/tools/software/features` pivot table** — see the
    `category_features` row in ARCHITECTURE.md for the full mechanism
    (collapsible groups, single add form with a category selector, filter +
    expand/collapse-all, pending queue items as read-only in-group rows,
    `category`/`open_ids` query-param state). Old per-category subpage URL
    is gone outright, no redirect — Brian's explicit call: it was never
    bookmarked/linked externally, so a hard cutover cost nothing. The two
    internal links that pointed at Manage Features (the tool edit page, the
    old category index itself) already targeted the base
    `/admin/tools/software/features` URL, so nothing needed repointing.
  - **Admin dashboard "Software" sub-group** — the four software-directory
    cards (renamed "Software vendors"/"Software categories"/"Software
    features"/"Feature review queue", sentence case) nest inside CFO
    Toolbox as their own collapsible sub-group, `_SOFTWARE_TOOLS` +
    `software_subgroup_html` in `webapp/app.py`, the exact same
    `_group_html(..., nested=True)` mechanism `_FPA_BUDDY_TOOLS` already
    used — no new plumbing needed, confirmed by Phase 0 before building.
    The name-duplicates page (no dashboard card of its own) folds its
    pending count into this sub-group's aggregate badge via `badge_hrefs`,
    same as Library's aggregate badge already covers hrefs with no direct
    card. Page H1s/titles on the underlying pages updated to match
    ("Software vendors", "Software categories").
  - **Sentence-case copy standard** (BRAND.md §3.2) — page titles, headers,
    section labels, and buttons are sentence case, with named
    products/features (FP&A Buddy, CFO Toolbox, Sail Don't Row), proper
    nouns, and acronyms (ERP, FP&A, ASC 606, RBAC, AI) exempted. Applied
    only to the surfaces this PR touched (dashboard cards, the pivot page,
    the review-queue title) — a sitewide casing sweep is separate, later
    work, stated explicitly in BRAND.md rather than implied.
  - **Pending-count badges, scoped down from the full Phase 0 inventory of
    ~20 review-queue-like mechanisms** — Brian's explicit scope-down after
    reviewing that audit: badged now are Feature Review Queue
    (`Library.count_feature_review_queue`, new — a cheap indexed COUNT),
    and the Reader content backfill's `needs_content_check` +
    manual-review counts (both already had `count_*` methods, just weren't
    wired into `webapp/tasks.py`). The tool name-duplicates page was
    initially held back pending a cheapness answer — its own docstring
    already answered it ("O(n log n) grouping over ~190 rows — fine to run
    live on every page load, no caching needed"), so it's included too,
    folded into the Software sub-group's aggregate badge (no dashboard card
    of its own to badge individually). **Deliberately left out**: FP&A
    Buddy feedback ratings and the three `tools.*_needs_verification` flags
    — none of them has a pending/reviewed concept at all yet (no `reviewed`
    column, no aggregating list page), so badging them would be new-feature
    design work, not badge-wiring; logged in the Phase 0 audit for a future
    flow-harmonization decision, untouched here.
  - **Review-queue merge confirmation** — the exact-name-match merge inside
    `approve_feature_review_queue_item` (confirmed working in production,
    the Abacum/Aleph merge, 8/20) previously executed the instant an admin
    clicked Approve, with no visible signal a merge had even happened. The
    underlying merge logic is unchanged (and now covered by
    `test_approve_reuses_existing_feature_by_name_instead_of_duplicating`,
    which already existed before this PR); what's new is a confirmation
    step interposed in the *route*: a name collision now renders a "Confirm
    merge" page naming the vendors already linked, with Merge (resubmits
    the same approve request plus `confirm_merge=1`) and Change name (back
    to the queue to edit the name first) actions — no silent merges. See
    ARCHITECTURE.md's `feature_review_queue` row for the mechanism.
  - **Tool edit page's Feature Taxonomy checklist becomes a table**, grouped
    by category — same columns/fields, same single-save-all form action
    (`POST .../feature-links/save`, unchanged), just restyled from stacked
    checkbox rows into a table matching the pivot/Software-vendors visual
    pattern. Save mechanics were left as single-save-all rather than
    switched to per-row: the brief left that decision to this PR's
    judgment, and per-row would have meant a materially bigger route
    change with no corresponding ask.
  - **Feature-name recasing** — `docs/FEATURE_TAXONOMY.md` §3 gained three
    naming rules (no "AI" in any form in a feature name; prefer short names
    with qualifiers in `definition` instead; sentence case). A new
    `scripts/archive/recase_feature_names.py` (preview-by-default, `--apply`
    to write, write-then-read-back verified — same convention as
    `scripts/archive/rename_differentiation_columns.py`) recased every live
    `category_features.name` to sentence case, preserving already-uppercase
    tokens (acronyms: ASC, SOX, GRC, IFRS, GAAP, AI, ...) and any token
    containing a digit (ASC 606, 1099). Run against production by Brian via
    `railway ssh` and confirmed complete — moved into `scripts/archive/` (via
    `git mv`) the same session the run was confirmed, per the "Archive a
    one-time script as soon as its run is confirmed" standing rule above.
  - **BRAND.md's coral "never for status" section gained a documented
    exception for pending-count badges** — `.task-badge`/`.task-badge-dot`/
    `.task-dot` have used `var(--coral)` since the admin hub's original
    notification-badge build; this PR documents that existing usage as
    sanctioned (alongside the pre-existing glanceable-health-indicators
    exception) rather than introducing anything new — no new hex, so
    nothing to add to `brand_check.py`'s allowlist.

- **Key features card follow-up — "Slack" no longer gets lowercased.**
  The card's `_sentence_case_feature_name` heuristic protects real
  acronyms/codes (RBAC, ASC, AI, KPI, SOX, GAAP, IFRS, AWS, MCP, ...)
  algorithmically, by checking whether a word is stored all-uppercase or
  contains a digit — not via a literal list, so a mixed-case brand name
  like "Slack" (in "Slack / Email Collaboration Triggers," live on FP&A
  and Close Management) fell straight through it and rendered as lowercase
  "slack." Fixed with a small hand-curated `_PRESERVED_FEATURE_WORDS` set,
  checked before the algorithmic heuristic — `{"Slack"}` today, extended
  as more mixed-case brand names turn up. The broader known limitation
  (proper-noun detection is still out of scope; any *other* unlisted
  mixed-case brand name still gets lowercased) is unchanged and still
  flagged in the function's own docstring.

- **Feature definitions render on the public Key features card (2026-09).**
  `category_features.definition` was collected and never shown, so each
  feature row on `/tools/software/{slug}` now shows a short line derived at
  render time (`_feature_definition_short`: first sentence if 170
  characters or fewer, else a word-boundary cut near 140 with an
  ellipsis), expandable through a native `<details>`/`<summary>` to the
  full stored text plus the category's `pointer_note`. Nothing is stored
  and nothing is shortened in the database. No title tooltips, no
  JavaScript. A feature with no definition shows
  `gates.EMPTY_COPY["feature_definition"]` ("Definition not available.";
  a separate PR is moving the rest of that dict off "not yet").
  **`tool_feature_links.note` is never rendered publicly or sent via MCP**:
  it's a curation log mixing quoted vendor copy with reviewer caveats.
  **Publishable vendor text is a separate column, `public_note`**, empty
  for every row at launch and never copied from `note` automatically;
  Brian curates it on the tool edit page's Key features table, where the
  internal note sits in the column beside it. On the profile it follows the
  definition, the two labeled "Definition" and "In {tool}". `get_software`'s
  `key_features` items carry `definition` (the `_gated_field` shape),
  `pointer_note`, and `public_note`. Limit:
  `Library.FEATURE_LINK_PUBLIC_NOTE_MAX` (1,000), shared with the
  textarea's `maxlength` like `CATEGORY_FEATURE_TEXT_MAX`; an over-limit
  save is refused whole, never shortened. `upsert_tool_feature_link`'s
  `public_note=None` default keeps stored text, so queue approval and the
  seed script can't wipe it. See ARCHITECTURE.md's matching paragraph and
  `tests/test_feature_definitions.py`.

- **Resources — Book recommendations (2026-08).** `/tools/resources` splits
  from one flat card list into two headed sections: "Benchmarking" (the
  existing cards, unchanged) and a new "Book recommendations" — a personal
  reading list, sparse by design, not benchmarking data. `benchmarks`
  gained a `section` column (`'benchmarking'`\|`'books'`, default
  `'benchmarking'` — no backfill needed for the 20 existing rows) via the
  standard idempotent migration. `Library.list_benchmarks(section=...)`
  filters; `add_benchmark`/`update_benchmark` both take an optional
  `section` (default `'benchmarking'`, backward compatible with every
  pre-existing caller); `add_benchmark`'s sort-order auto-increment is
  scoped per section, so the two lists order independently. Public
  rendering: `_bench_card` (unchanged) keeps its pricing/coverage badges,
  a new `_book_card` renders without them — those badges encode
  data-access tiers that don't map onto a reading list. Book
  recommendations always renders, even empty ("Coming soon.") — sparse is
  the expected state, not a gap to hide. Admin page: one table becomes two
  (`_admin_resource_table`), plus a Section dropdown on the add/edit form
  (an unrecognized value falls back to `benchmarking`, same defensive
  pattern as every other fixed-vocabulary admin field). **The ten initial
  book rows shipped via a one-off script, not `_DEFAULT_BENCHMARKS`** — that
  sync-only pipeline (`_seed_toolbox`) only ever updates an existing row by
  URL match, it never inserts one (a missing row might be a deliberate
  admin delete), so ten brand-new rows need a genuine insert:
  `scripts/seed_book_recommendations.py`, guarded like every other
  production-data script here (preview by default, `--apply` to write,
  write-then-read-back verified, idempotent by URL match against the whole
  table) — lives in `scripts/`, not `scripts/archive/`, until Brian
  actually runs it. **"Suggest a resource" reuses `/contact` outright — no
  new route, no new spam-guard code.** Investigated first: the Community
  gap-feedback flow (`/tools/communities/gap`) is public and structured but
  has **no rate limiting, honeypot, or spam filtering at all**; `/contact`
  has the full stack (rate limit, honeypot, time-trap, keyword spam
  filter) and was already flagged in `docs/BUILD_PLAN.md` as investigated
  and reusable for exactly this, just never built against. The page links
  straight to `/contact?context=resource-suggestion` —
  `_CONTACT_CONTEXT_PREFIXES` prefills the message textarea with a
  distinguishing prefix ("Resource suggestion: ") via `/contact`'s
  existing (previously unused) `message` query-param mechanism, so the
  admin inbox preview shows which surface a submission came from with
  **no schema change to `contacts`**. **Explicit follow-up, not fixed
  here:** the Community gap-feedback flow's missing rate-limit/honeypot/
  spam-filter coverage — a real gap on a public, no-login surface,
  confirmed by this investigation, flagged for its own PR.

- **Sail, Don't Row — water reflections, re-added (reverses an earlier decision).**
  The original build removed reflections outright: "unrealistic inverted-building
  duplicate, not worth fading," and a test (`test_play_no_skyline_reflection`)
  codified that as a design-review constraint. Brian later signed off on
  re-adding them, but only on the condition that the technique actually be fixed,
  not just made subtler — the prior version was rejected for being a hard-edged,
  full-opacity mirror with no fade or blur, not for being too intense. The
  re-added version (`webapp/app.py`'s `.sdr-reflection-wrap`/`.sdr-reflection-inner`)
  reuses the same skyline SVG markup as `.sdr-skyline-wrap` (so both crossfade
  together automatically off the same `.sdr-skyline-layer`/`data-cp` selectors —
  no separate JS toggle needed), flipped with `scaleY(-1)` pivoting exactly on
  its own bottom edge so reflected distance below the waterline always equals
  true distance above it, then clipped to a band within the first ~27% of the
  stage below the waterline with a real blur, a single fade-to-transparent mask,
  reduced opacity, and a `::after` color-blend tint toward the water's own hue
  so it reads as color reflected in water rather than a building floating under
  it. A slow `skewX` wobble on the flip stands in for rippling distortion
  (true per-pixel distortion isn't reachable in DOM+CSS), reinforced by
  `.sdr-water-shine`'s soft diagonal light-streak glints drifting across both
  the live water and the reflection band. One non-obvious placement bug worth
  noting for future water-layer work: `.sdr-band1` (the water's base color) is
  fully opaque, so a reflection layer painted *before* it in DOM order is
  completely hidden underneath it — the reflection has to live inside
  `.sdr-water`, painted immediately after `.sdr-band1` and before the
  `.sdr-band2`/`.sdr-band3` texture layers, not as a sibling of
  `.sdr-skyline-wrap`. The old test was replaced with
  `test_play_has_water_reflection`, asserting the mechanism is present.
  Same PR also gave the water itself a color progression along the route —
  Charles River (brackish green) -> Boston Harbor (open, grayer blue) -> Cape
  Cod Bay (clearer turquoise) -> Martha's Vineyard Sound (deep ocean blue) ->
  Nantucket (deepest indigo) — five `.sdr-water-tint[data-cp]` layers
  crossfaded by the same `updateCheckpointLayer`/`data-cp` mechanism the
  skyline backdrops already use (`waterTintLayers` in the JS, toggled
  alongside `skylineLayers` at both call sites), `mix-blend-mode:color` so
  the existing wave texture and reflection stay visible through the tint
  rather than being painted over. `test_play_has_water_color_progression`
  covers it.

- **Phase 1 — the digital Library (Archive + Feed) moved from member-visible to
  admin-only, and the `/library` hub route was removed outright.** It's Brian's
  personal reading stash, not a member-facing feature, and he doesn't want it
  discoverable by anyone but him. `/library/archive` and `/library/feed` now gate
  on `_is_authed` instead of `_is_member` — same URLs, no redirect changes,
  just a stricter gate (a signed-in non-admin member is bounced to login exactly
  like an anonymous visitor). The `/library` hub page itself (a landing page
  linking to Archive/Feed/FP&A Buddy) had no reason to exist once Archive/Feed
  went admin-only and FP&A Buddy stayed member-facing — removed with no
  compatibility redirect, since nothing external pointed to it. Every internal
  reference was repointed by the same rule: a still-member-facing page's
  back-link (Ask, Past Questions) goes to `/`, never an admin-gated destination
  a signed-in non-admin member would hit a login wall on; an admin-only
  destination's back-link (Archive, Feed, Read) goes to `/admin/library`. The
  homepage's "Digital Library" card was deleted outright, not repointed, and
  "Library" came out of the public nav bar entirely — FP&A Buddy (still
  member-gated) was reachable directly at `/library/ask` at this point, with
  no nav entry yet (see the Phase 2 bullet below: it moved to
  `/tools/fpa-buddy` and `/library/ask` no longer exists). **FP&A Buddy's own
  access level was untouched in this phase** — it stayed member-gated, not
  admin, since it's meant for a small group of signed-in friends, not just
  Brian; that's still true post-Phase-2, only the URL changed. On the admin
  side, the Admin hub's
  "Archive" section (the card + its own `/admin/library` management page,
  covering the same 8 existing tools in the same order) was renamed
  "Library" — deliberately **without** adding links to Archive/Feed above
  those 8 tools yet: Archive and Feed are merging into a single Reader page
  in Phase 5, and adding two separate links now just means deleting them
  again almost immediately, so `/library/archive`/`/library/feed` stay
  reachable by direct URL only until Phase 5 gives the merged page its own
  permanent entry point here. The coral "Before opening the archive to paid
  subscribers—read this" banner (zero dependents, confirmed by investigation)
  and the permanent green "Subscriber access" status box were both deleted
  from the Admin hub outright — the manual re-check trigger and the underlying
  `authcheck.check_auth_cookies` logic are unchanged, just relocated to
  `/admin/library` as a compact "Re-check subscriber access" control that
  only expands into a colored detail panel when a cookie has actually gone
  stale, rather than sitting there as a permanent status display. Phase 5
  owns building a real conditional subscriber-access alert on the merged
  Reader page — deliberately not built here, since Feed is being retired into
  `/read` in that same phase and building it twice would be wasted work.
- **Library/Toolbox restructure, Phase 2 — FP&A Buddy moves to
  `/tools/fpa-buddy`; Past Questions folds in as a helpful-only search.**
  FP&A Buddy moved out of Library into the Toolbox area, at a new URL
  replacing `/library/ask` — still member-gated exactly as before (unlike
  Archive/Feed's Phase 1 move to admin-only), since it's for a small group
  of signed-in friends, not just Brian. `/library/ask` and
  `/library/past-questions` are both gone outright — no compatibility
  redirect, since nothing was bookmarked. Every internal reference was
  found and fixed instead: the nav comment, the old Library hub card
  (already gone as of Phase 1), the Archive admin page's inline "quick ask"
  widget and its "More options" link/JS, `/ask/history`'s links back to the
  ask page and out to Past Questions, the "How FP&A Buddy works" admin
  explainer, the scripts registry's `mcp_server.py` blurb, and the
  Community Matchmaker's "How this works" reference doc — all repointed to
  `/tools/fpa-buddy`. The old flat `/questions` redirect stub (which used
  to 301 to `/library/past-questions`) is gone the same way, straight 404
  now. `/ask` is the one exception: `GET /ask` used to 301 to
  `/library/ask`, and that redirect is gone too, but the bare path isn't —
  `POST /ask` (the Q&A API, untouched by this phase) still lives there, so
  `GET /ask` now 405s instead of 404ing. Past Questions itself — previously
  a standalone browse page showing every non-hidden question, no rating
  filter — folded into a "Search past questions" section on
  `/tools/fpa-buddy`, placed above the ask box (a genuine "search before you
  ask" flow), with a real behavior change: it now shows only questions with
  at least one `ask_feedback.rating='helpful'` row.
  `Library.list_public_ask_questions` grew a `helpful_only` parameter (an
  `EXISTS` subquery against `ask_feedback`, not a join — a question with
  several raters, possibly including an `inaccurate` one, still shows up
  exactly once as long as any single rater called it `helpful`) rather than
  becoming a second near-duplicate query method. No dedup on repeated
  question text for v1 — the old page never deduped either, and doing it
  well would need more than exact-string matching to be worth the
  complexity (near-duplicate phrasing, or two genuinely different answers
  to a similarly-phrased question), so it's left as a possible future
  improvement, not a v1 gap being silently accepted. The admin-only
  hide/anonymize controls on each past-question row carried over unchanged
  (same `/questions/{id}/hide` and `/questions/{id}/anonymize` POST routes,
  just redirecting back to `/tools/fpa-buddy#past-questions` now instead of
  the removed page). This phase also cleaned up the dead
  `nav.site-nav a[href="/ask"]` CSS selector Phase 0's investigation flagged
  and Phase 1 explicitly deferred — removed here since this phase already
  touches that exact code.
- **Library/Toolbox restructure, Phase 5 — `/library/archive` and
  `/library/feed` merge into one three-pane Reader at `/read`.** The route
  split: `GET /read` is the merged shell (left rail with Feed/Saved/Read
  Later quick views plus, in the Feed view, a Sources category/source tree;
  a middle list pane for whichever view is active; a right reader pane that
  loads an article via AJAX with no page navigation). `GET /read/{article_id}`
  is what `GET /read?id=...` used to be — the standalone single-article view,
  now a path param — and `GET /api/read-article?id=...`/`?url=...` is a new
  admin-gated JSON endpoint the merged page's reader pane calls into. Both
  routes resolve their content through one shared helper
  (`_resolve_reader_content` in `webapp/app.py`) instead of the old `/read`
  route's inline id-then-url-fallback logic duplicated in two places.
  `/library/archive` and `/library/feed` are retired outright — no
  compatibility redirect, same "nothing was bookmarked" precedent Phase 1
  and Phase 2 both used — and so are the flat `/archive`/`/feed` redirect
  stubs that used to point at them. Three things were deliberately dropped
  in the merge, not carried forward: the old Archive page's inline "ask your
  archive a question" box (a lesser duplicate of FP&A Buddy — confirmed
  `POST /ask` and `linklib/agent.py` are untouched, so `/tools/fpa-buddy`
  isn't affected); every per-item Edit tags/Delete/Archive-management
  control that used to sit inline on the Archive page and the old
  `/read?id=...` view (those stay exclusively in `/admin/library`'s
  dedicated tools — Save-to-library and the Read Later toggle are the only
  actions still exposed on the Reader, both reused verbatim from
  `POST /feed/save`/`POST /feed/read-later`); and the old bare
  paste-a-URL empty state on `/read` with no `id`/`url` (the merged page's
  list-driven click-to-open UX replaces that need — there's no standalone
  way to read an arbitrary not-yet-saved URL outside a Feed/Read Later row
  anymore). The subscriber-access alert Phase 1 explicitly deferred is
  built here for real: a coral banner in the list pane's header, rendered
  only when `authcheck.stale_domains(authcheck.get_auth_status(lib))` is
  non-empty, naming the failing domain and linking to the same
  `POST /admin/auth/recheck` route `/admin/library`'s own manual re-check
  control already used (that control is untouched — both surfaces trigger
  the same underlying `authcheck.check_auth_cookies`). `_LIBRARY_TOOLS`
  gained a ninth, first-listed entry, "Open Reader" → `/read` — the entry
  point Phase 1 deferred to this phase rather than adding two separate
  Archive/Feed links that would've just been deleted again once the merge
  landed.
- **Reader follow-up pass — a live post-launch walkthrough surfaced a real
  click-hijack bug, a content-flattening bug, and a few gaps.** Paywalled
  Feed items were wrapped in a real `<a target="_blank">` instead of getting
  the normal `rrOpen()` click handler, so a click hijacked straight to an
  external tab before the reader pane's own "Original →" link ever got a
  chance to render — fixed by removing the special-casing entirely (the
  paywall badge stays; the click behavior no longer differs from any other
  row). Article content rendered as one flattened paragraph with no images
  or links because `linklib/extract.py`'s always-active BeautifulSoup
  fallback (trafilatura isn't a declared dependency, so this is the path
  that actually runs) used `get_text(" ", strip=True)`, which drops
  paragraph breaks entirely — fixed there, plus a new `extract_reader_html()`
  gives the Reader's live-fetch path real structured HTML (paragraphs,
  headings, lists, absolute-ized images/links) via a new `PageData.raw_html`
  field, without changing `_extract_content()`'s plain-text contract that
  ingest/search/enrichment depend on. See ARCHITECTURE.md's Reader-merge
  section for the full list, including the known gap (already-saved
  articles' cached content isn't retroactively restructured), the reader
  body's width fix (640px → 700px, matched to Instapaper's own reading
  column), the two new Instapaper-parity features (in-article find, a
  distraction-free reading toggle), and the sitewide thousands-separator
  sweep.
- **Reader follow-up, second round — real Feed search, a root-caused mobile
  bug, and a responsive default.** Feed view's search box (confirmed absent
  from the original design file, even decoratively, in the round above) got
  built for real once the call came back yes — `#rr-feed-search`, reusing
  `rrApplyFilter`'s existing category/source filtering rather than a second
  parallel mechanism. Separately, "clicking an article on mobile does
  nothing" turned out not to be a broken click handler at all — confirmed
  live with a real touch-enabled mobile-viewport session before writing any
  fix, not assumed from the bug report's own guess — `rrOpen()` fired every
  time; `#rr-reader` was just landing 600px+ below the fold on the stacked
  mobile layout with nothing scrolling the page to it. Fixed with an
  unconditional `scrollIntoView`, plus a responsive default beyond the pure
  scroll fix: mobile portrait now opens straight into distraction-free
  reading (`rrMobileNoRoom()`, an orientation-aware breakpoint — deliberately
  *not* reusing the homepage's `1024px` mobile-stacking number, which solves
  the mirror-image problem of keeping landscape phones on the *mobile*
  layout, the opposite of what the Reader wants here), while landscape at
  real width still gets the genuine 3-pane layout. See ARCHITECTURE.md's
  Reader-merge section for the full breakpoint reasoning. This round also
  set the standing testing-standard bullet above (live headless-browser
  verification, mobile viewports included, for every UI-facing change going
  forward) — the mobile bug above is exactly the kind of thing a desktop-only
  verification pass structurally cannot catch.
- **Reader search moves from the left rail into the list-pane header —
  correcting Phase 5 drift, not redesigning.** Both views' search boxes had
  been rendering in the left rail since the Phase 5 merge. Investigation
  (prompted by Brian noticing it didn't match the old Archive page) found no
  commit anywhere that evaluated the placement: the pre-merge Archive page put
  search in a page-width bar directly above the list with no rail at all;
  Phase 5's own ARCHITECTURE.md describes Saved search under its **middle list
  pane** bullet while its exhaustive left-rail inventory never mentions search
  — and the same commit's code put it in the rail; the mechanism was assignment
  to a variable named `sources_html`, which exists for Feed's Sources tree and
  only ever lands in `rail_html`; and the Feed search box's CSS carried the
  *list pane's* 22px gutter while rendering in a rail whose gutter is 14px.
  That last one is the tell worth remembering — **a component styled for a
  container it isn't in is good evidence it was moved without being
  reconsidered.** Only placement changed: Feed keeps client-side
  `rrApplyFilter`, Saved keeps its server GET, and the rail keeps the Sources
  tree and tag bar (filter vocabularies, not search), with the Saved rail's
  now-orphaned "Search" heading relabelled "Tags" to match Feed's "Sources".
  **Caveat kept in the docs rather than glossed:** `Feed.dc.html` isn't in the
  repo and `/design-login` is unavailable headless, so this rests on git
  archaeology plus every second-hand account of that file (all of which put its
  magnifying glass in the list-pane header) — testimony about the design file,
  not the file itself. Test note: `tests/test_reader_search_placement.py`
  asserts **DOM ancestry, not geometry** — on the stacked mobile layout the
  rail and list pane both span the full width, so a bounding-box containment
  check passes for either and proves nothing.
- **Reader Build arc — full QA pass, and the three findings it produced.**
  With Phase 5/5b/5c and the search-placement fix all shipped individually,
  a first pass looking at the whole Reader/backfill surface *together* (not
  per-PR) surfaced one real bug and two live design refinements — exactly
  the kind of interaction individual-PR verification structurally can't
  catch, the same lesson the 5b/5c merge conflict taught earlier in this
  arc.
  - **`Library.search()` 500'd on ordinary text — a hyphen, an apostrophe,
    an unmatched quote, or a bareword that collides with an FTS5 operator
    keyword (AND/OR/NOT) all threw `sqlite3.OperationalError`, uncaught.**
    The raw query was passed straight into `articles_fts MATCH ?` with zero
    escaping. Reproduced live via the Reader's own Saved search box
    (`self-serve`, `well-being`, `brian's` all crashed to a blank page) and
    independently against a bare FTS5 table with no app code involved, to
    confirm it's intrinsic to unescaped `MATCH`, not this schema. Hit two
    real call sites: the Reader's Saved search and `/api/search` (also
    `scripts/mcp_server.py`'s path for Claude Desktop/Code search) — so
    ordinary Claude-side search was affected too. Pre-existing (`git blame`
    traces the method to the repo's root commit, well before Phase 5); the
    Reader's own search box just gave it a direct, synchronous, easy-to-hit
    path for the first time.
    **Fix has two layers, in a specific order that matters**: try the query
    exactly as given first; only if that raises, retry with the whole thing
    wrapped as one FTS5 phrase (quoted, internal quotes doubled per FTS5's
    own escaping rule); if even that somehow fails, return `[]` rather than
    raise. **The "try raw first" ordering isn't cosmetic — it's what saved
    this from becoming a second regression before it ever shipped.**
    `Library.search()` has a THIRD caller besides the two above:
    `linklib.agent.retrieve()` (FP&A Buddy's library retrieval) already
    pre-sanitizes its own query via `_safe_fts_query()`, which tokenizes a
    question into deliberately valid FTS5 syntax like
    `"self" OR "serve" OR "churn"`. An earlier draft of this fix
    unconditionally wrapped every query in an outer phrase-quote — which
    turns that already-valid OR-query into one literal string search for
    the doubly-quoted text verbatim, matching nothing. That would have
    silently taken FP&A Buddy's library retrieval dark on every single
    question, with no existing test to catch it (nothing exercised
    `Library.search()` against `_safe_fts_query()`'s actual output shape).
    Caught before shipping by tracing every existing caller of the method
    being changed, not just the two that motivated the fix, and confirmed
    live against the real `linklib.agent.retrieve()` function (not just the
    helper) with realistic multi-word questions. Fixed by trying the raw
    query first — a caller handing in already-valid syntax just succeeds on
    that first attempt and is never touched by the quoting at all; only
    genuinely raw, uncontrolled text (which fails to parse) falls through to
    the safe quoted retry. New `tests/test_fts_search_query_safety.py`
    covers both directions: every crash-inducing query from the QA report
    now returns real results or `[]`, never raises; and
    `_safe_fts_query()`'s OR-query shape still retrieves real matches
    (`test_safe_fts_query_output_is_not_double_wrapped`, a regression test
    for the exact bug caught mid-build).
  - **Reader body width was pinned to a flat `700px` regardless of how much
    room the reader pane actually had** — confirmed live: on a wide
    viewport, or in distraction-free mode (where the pane gets
    meaningfully wider), the text column stayed capped at 700px, leaving
    large fixed empty margins the design brief flagged as reading nothing
    like Instapaper's own adaptive column. Changed `.rr-reader-body`'s
    `max-width` from a flat `700px` to `min(92%,880px)` — scales with the
    pane's real width (confirmed at 1280/1600/2000px viewports: body width
    genuinely differs at each, 521px/815px/880px, and grows further again
    in distraction-free mode), while the `880px` upper cap still keeps line
    length readable on an extreme-width pane rather than letting it run
    edge-to-edge. Verified at 390px mobile too — no overflow, body still
    fits inside the narrower pane's own 92%.
  - **Find-in-article was a bare icon among a row of bare icon buttons
    (Tag, Expand) — confirmed working, not a bug, but easy to miss by
    design, not by accident.** Gave the toggle a visible "Find" text label
    alongside its icon (`.rr-find-toggle`), matching the visual weight the
    "Aa" and "Read later" buttons already carry in that same row, rather
    than inventing a new highlight treatment. No behavior change — same
    `rrToggleFind()` handler, confirmed still opens the find bar and still
    finds real matches after the styling change, on both desktop and a
    390px mobile tap.
  See `tests/test_fts_search_query_safety.py` for the search-fix
  regression coverage; the width and find-toggle changes are pure CSS/markup
  with no new test file (verified live per the standing testing standard
  above, same as every other UI-facing change in this arc).
- **Phase 5b — Reader content backfill: reprocessing the ~4,500 already-saved
  articles for real structure, not just live fetches.** The Reader
  follow-up pass above shipped `extract_reader_html()`, but it only ever ran
  against a live fetch — every already-saved article was still flattened
  plain text from ingest time, and no raw HTML was ever kept for them, so
  restoring structure needs a genuine re-fetch of each one. Investigated
  first, per the standing gate: confirmed no raw HTML exists anywhere to
  reprocess offline, so a live re-fetch of all ~4,516 URLs is unavoidable;
  Historical sweep and the re-enrich job (near-identical background-thread/
  `_JOB_STATE` patterns) are the right admin-batch-job template to copy,
  not Archive Queue (a review UI, not a fetch loop) or Content de-dupe
  (synchronous/foreground, wrong for a multi-hour job); and nothing in the
  codebase rate-limits outbound crawling today, so a new ~1.5s delay between
  fetches is a deliberate first, not a reuse. Built as
  `/admin/reader/backfill-content`: a new `articles.content_html` column
  (never reusing `content`, which stays plain text — see `_resolve_reader_
  content`, now preferring `content_html` when populated) and a new
  `content_refetch_log` table (shape mirrors `backup_log` — one row per
  attempt, success or failure, so a re-run's history stays visible).
  **Never destructive**: a failed re-fetch never touches `articles.content`
  or `articles.content_html`, only the log. Two refinements added after the
  initial proposal, both requested before implementation began: (1) a real
  content sanity check beyond HTTP status — `extract.
  assess_extraction_quality()` catches a bot-challenge or paywall
  interstitial that returned 200 with garbage instead of the real page,
  logging `paywall`/`bot-challenge`/`too-thin` as distinct, groupable
  reasons rather than silently storing a bad result; `bot-challenge` (a new
  Cloudflare/PerimeterX/DataDome marker list, `looks_like_bot_challenge()`)
  is a genuinely new detection path with no prior track record in this
  codebase, unlike `paywall`, which reuses the existing `looks_paywalled()`/
  `PageData.blocked` signal — so it got its own dedicated test, not just
  incidental coverage riding behind the paywall case. (2) A real stop
  control, not just crash-recovery resumability — neither Historical sweep
  nor re-enrich had one (confirmed by direct grep, not just relayed from an
  investigation report); added a `stop_requested` flag on the job state,
  checked once per article between fetches, sharing the exact same
  resumability mechanism a crash-recovery restart already used (both just
  skip whatever `content_html` is already populated). See ARCHITECTURE.md's
  "Reader content-structure backfill" section for the full technical
  write-up.
- **Phase 5b follow-up — the first real verification batch (25 articles, 12
  failures) turned up an opaque `fetch-error` with no detail behind it, plus
  no way to tell independent dead links from one source systematically
  failing.** Root cause: `extract.fetch_page()`'s `except Exception:` never
  bound the exception, so there was genuinely nothing for
  `backfill_article_content()` to log beyond the category. Fixed
  non-destructively (`PageData.fetch_error`, defaulted empty — every
  existing caller already ignores a failed fetch, so this changes nothing
  for `ingest_url`/the Reader's live-fetch path) via a new
  `extract._describe_fetch_error()` that turns the caught exception into an
  HTTP status / `timeout` / connection-error string, now stored in
  `content_refetch_log.detail`. Also added
  `Library.content_refetch_failure_domains()` (same latest-attempt de-dupe
  as `content_refetch_failure_counts()`, grouped by URL host instead of
  reason) and a coral clustering banner on the admin page, shown only once a
  host has 2+ failures. **Not retroactive** — the already-logged rows from
  that first batch keep an empty `detail`; only future attempts capture it.
  See ARCHITECTURE.md's "Reader content-structure backfill" section for the
  full write-up.
- **Phase 5b second follow-up — fetch reliability (browser UA + Wayback
  fallback), and a real investigation finding that reshaped what got
  built.** The domain-clustering work above surfaced 17/18 failures in a
  follow-up batch tracing to 4 domains. **Investigated with real requests
  before writing any code**, per the standing gate — not assumed:
  - The proposed browser-User-Agent swap was tested against real 403 URLs
    from all four domains and **confirmed it does NOT fix any of them**.
    Three of four return the byte-identical Cloudflare "Just a moment..."
    challenge with the old bot UA or a real Chrome UA — Cloudflare
    fingerprints the TLS/connection layer, not the UA string, so no UA swap
    alone gets past it (defeating that stays explicitly out of scope). Kept
    as the new sitewide default anyway (`fetch_page()` now always sends
    `extract._BROWSER_HEADERS`, not just on the auth-cookie path) since
    there's no downside and it may help elsewhere in the corpus — but the
    PR says plainly it's confirmed *not* to solve this specific batch.
    `continuations.com` returning an identical 404 either way usefully
    confirmed its failures are genuine link rot, not blocking.
  - The Wayback Machine fallback's investigation ran into archive.org's own
    Availability API rate-limiting (429) **broadly and unpredictably** —
    confirmed across three rounds from two independent networks (a Railway
    production container and a residential connection), on both a
    previously-queried URL and one nobody had ever queried before (even an
    unrelated Wikipedia page 429'd immediately). That ruled out "Railway's
    IP is blocked" and "one URL got hammered by testing," leaving "archive.org
    itself is having a rough stretch, for anyone" as the only theory left
    standing — an external condition unrelated to this codebase.
    `linklib/wayback.py` (new module) is built defensively around exactly
    that: every function returns `None`/`""` on ANY failure, never raises,
    never retries — a retry loop would just add load against a service
    already struggling.
  - **Explicit, discussed trade-off, not a shortcut**: rather than block on
    archive.org recovering, Brian chose to ship both fixes now, with the
    Wayback fallback wired correctly regardless of today's outage
    (`pipeline.backfill_article_content()` tries it as a last resort after
    ANY direct-fetch failure — not just 404s, since a stubborn 403 may
    still have a real archived snapshot the Internet Archive's own crawler
    could reach even though a generic bot request couldn't), but "a real
    snapshot's content actually comes back correctly" is explicitly
    **unverified at merge time** — deferred to Brian, via the backfill
    tool's own existing small-batch-first convention, once archive.org's
    rate limiting clears.
  - `content_refetch_log.source` (new column, migration, default
    `'direct'`) distinguishes a Wayback-sourced success from a direct
    fetch — a "via Wayback" badge on the admin page's attempts log, plus a
    count of currently-Wayback-sourced articles
    (`Library.count_wayback_content()`). The Reader's live-fetch path
    (`_resolve_reader_content`) gets the identical fallback for
    consistency, with a `content_via` field and a coral in-reader notice
    when content came from an archived snapshot rather than the live page
    — added interactive-path latency from this is **also flagged as
    unverified**, for the same archive.org reason, rather than silently
    assumed fine.
  See ARCHITECTURE.md's "Reader content-structure backfill" section
  (fetch-reliability sub-section) for the full investigation write-up.
- **Phase 5b third follow-up — the first real production batch surfaced a
  genuine logging gap (fixed) and a real new failure class
  (`defunct-service`, fixed), diagnosed live rather than guessed at.**
  Brian's first real batch (25 articles, 23 failures) showed zero "via
  Wayback" successes, even on the Cloudflare-blocked domains the fallback
  was built for. Root cause, found via `railway ssh` one-off scripts
  against real failing URLs: Wayback genuinely was attempted every time —
  no code bug — but `content_refetch_log` only ever logged the *original*
  direct-fetch failure, discarding whatever Wayback itself returned. A
  manual replay found archive.org still unreachable, but this time as
  `ConnectionResetError`/`ConnectTimeout`, not the `HTTP 429` the original
  investigation found — different symptom, same underlying story. Fixed
  with `wayback.find_snapshot_verbose()`/`fetch_snapshot_verbose()` (same
  never-raises guarantee, plus a short outcome string appended to the
  logged `detail`) — the plain versions stay as thin wrappers for the
  Reader's live-fetch path, which doesn't need the reason. Also confirmed
  a new domain, `feedproxy.google.com` (3 failures) — Google's
  discontinued FeedBurner proxy — is genuinely, permanently dead (a live
  request returned Google's own real `Error 404` page, not a block).
  Added `linklib.pipeline._DEFUNCT_SERVICE_DOMAINS` (small, hand-curated,
  each entry requires live confirmation) — `backfill_article_content()`
  skips BOTH the fetch and the Wayback attempt entirely for a match
  (`reason='defunct-service'`), since neither can ever succeed and Wayback's
  own rate-limit budget is too scarce to spend on something already known
  unrecoverable. `articles_needing_content_backfill()`'s default scope now
  permanently excludes these going forward (Brian's explicit ask — no
  point re-burning fetch/Wayback attempts on a confirmed-dead domain every
  batch), reachable again only under `force=True`. **Caught a real bug
  before shipping, not after**: the "Structured" stat used to be derived
  as `total - remaining`, which would have silently mis-attributed an
  excluded-but-never-structured `defunct-service` article as "done" once
  `remaining` started excluding it too — fixed with a dedicated
  `count_structured_content()` query instead of a derived subtraction, and
  a regression test written for exactly this failure mode. See
  ARCHITECTURE.md's fetch-reliability sub-section for the full write-up.
- **Phase 5b follow-up #2 — retry backoff (a distinct, non-permanent
  exclusion tier) + manual URL correction, plus a known-domain-migration
  fetch tier tried before Wayback.** Everything that wasn't
  `defunct-service` stayed in default-scope retry forever — every future
  batch kept re-attempting a Cloudflare-blocked domain or a genuinely dead
  link, burning time and Wayback's scarce rate-limit budget. Two
  investigation gates ran first, per Brian's ask: confirmed no existing
  admin capability lets `articles.url` be edited anywhere (unlike
  `tools`/`benchmarks`/`thought_leadership`/`communities`, which all have
  this), and confirmed `content_refetch_log` is genuinely one-row-per-attempt
  with no existing raw attempt-count query. **"Needs manual review" is a
  second, deliberately separate exclusion tier from `defunct-service` — not
  merged into it**: unlike a confirmed-dead service, a Cloudflare block can
  lift and a 404 can be relinked, so this tier isn't permanent.
  `Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD = 3` (Brian's own assumption,
  flagged rather than silently picked) — an article failing 3+ times in a
  row (reason != `defunct-service`), counted *since its last URL
  correction* (or ever, if uncorrected), is pulled from default-scope
  auto-retry; `force=True` still reaches it. Deliberately query-time-derived
  (`Library._manual_review_article_ids()`, a CTE), not its own stored
  `content_refetch_log` reason, since "3rd failure in a row" is a judgment
  about accumulated history, not a fact from a single attempt — so the
  admin page's needs-review list shows the article's REAL last failure
  reason (e.g. `bot-challenge`), not a synthetic tag.
  `Library.apply_article_url_correction()` updates `articles.url` and
  writes a durable `url_correction_log` trace (per the one-off-fix rule
  above) — it never touches `content_refetch_log` itself; the reset falls
  naturally out of `_manual_review_article_ids()`'s cutoff logic (only
  counting attempts after the correction), so the full pre-correction
  failure history stays intact and non-destructive. The correction
  mechanism is an export/import CSV round trip on `/admin/library/
  backfill-content` (`linklib/manual_review_csv.py`, mirroring
  `linklib/overhead_csv.py`'s shape and the pre-existing overhead-spend
  preview-then-confirm route pair/state-carry convention): export is keyed
  by the stable `article_id` (the URL itself is what's changing) with a
  blank `corrected_url` column; import previews a three-way split
  (valid updates / skipped blank-or-unchanged / errors for a malformed URL
  or unrecognized `article_id`) with nothing written until a separate
  confirm step. Also added a known-domain-migration fetch tier
  (`linklib/domain_migration.py`, tried by
  `linklib.pipeline._finish_backfill_after_direct_failure` BEFORE the
  pre-existing Wayback fallback): a small hand-curated
  `_DOMAIN_MIGRATIONS` map (`pointsandfigures.com` →
  `jeffreycarter.substack.com`, `avc.com` → `avc.xyz`), same
  live-confirmation discipline as `_DEFUNCT_SERVICE_DOMAINS` — a real
  domain-count diagnostic against the full production archive (not a test
  batch) found 20 `pointsandfigures.com` articles (2 already showing a
  logged Cloudflare-consistent failure) and 55 `avc.com` articles (0 logged
  failures yet, since most hadn't been attempted in a batch since the
  fetch-reliability work landed). Reuses the exact Exa integration FP&A
  Buddy's `retrieve_exa()` already uses, restricted to the one destination
  domain, searching for the article's stored title — a hit is only accepted
  if its own title plausibly matches (word-overlap, not exact string), and
  the candidate still has to clear `assess_extraction_quality()` like any
  other fetch. **Explicitly not a general search fallback** — trusted only
  because the destination domain is already confirmed as that specific
  source's legitimate continuation. Confirmed before building that this
  can't conflict with the retry-cap attempt-counting design: the migration
  tier still writes exactly one `content_refetch_log` row per
  `backfill_article_content()` call (`source='migration'` on success; on
  any miss it logs nothing and falls through to the existing Wayback path,
  which does its own single log — never a double-log). Admin page's stats
  grid grew from 3 to 5 tiles (Total/Structured/Remaining/Needs
  review/Defunct service), CSS switched from a fixed `repeat(3,1fr)` to
  `repeat(auto-fit,minmax(130px,1fr))` per the standing CSS-Grid-blowout
  lesson (Phase P) rather than hardcoding a new column count. `defunct-
  service` logic and its permanent exclusion are untouched — the
  pre-existing `tests/test_fetch_reliability.py` suite passes unmodified as
  the regression check. See ARCHITECTURE.md's Reader content-structure
  backfill section for the full write-up.
- **Medium-platform Exa fetch tier (2026-08) — a third fetch tier for the
  same backfill pipeline, targeting medium.com and its blocked lookalikes.**
  Preceded by three read-only diagnostic rounds
  (`scripts/medium_platform_scale_check.py`) that measured real scale (147
  Medium-platform articles) and Exa-recovery feasibility before any of this
  shipped — no user-agent swap gets past medium.com's Cloudflare block
  (confirmed against real 403s), and unlike a `_DOMAIN_MIGRATIONS` entry
  there's no single destination domain to redirect to, so this tier
  (`linklib/medium_platform.py`) searches Exa broadly (no `includeDomains`
  restriction) and validates whatever it finds via
  `domain_migration._titles_match()` (reused, not reimplemented). Host
  recognition (`is_medium_platform_host()`) is suffix-based — `medium.com`
  itself, any `*.medium.com` subdomain (including author subdomains and the
  `link.medium.com` short-link redirector), plus a hand-curated
  `_MEDIUM_CUSTOM_DOMAINS` set (`bothsidesofthetable.com`, confirmed via its
  Medium post-ID-hash URL slug) — the second diagnostic round specifically
  found author subdomains slipping through an earlier exact-match carve-out
  to a guaranteed-403 live re-fetch. **A same-domain carve-out splits
  validation into two paths**, mirroring the exact split the diagnostic
  found necessary: a candidate on some OTHER host is live re-fetched and run
  through the standard `extract_reader_html()` + `assess_extraction_quality()`
  gate; a candidate that resolves back onto a Medium-platform host itself
  would just re-hit the same block on a live re-fetch — not a real test of
  the candidate — so it's validated against Exa's own already-returned text
  instead (a word-count floor), then converted to Reader HTML via the new
  `extract.paragraphs_html_from_text()`. **Design principle behind that
  formatter, and for any future fetch-tier work**: the Reader delivers a
  consistent house reading experience regardless of publisher — a source is
  an input to normalize, not a style to preserve. Concretely: markdown
  headings become `<h2>`/`<h3>` (capped, plus an unmarked short Title-Case
  line with no trailing punctuation is treated as a subheading too, since
  Medium's own in-article headers arrive that way with no markdown surviving
  the conversion), emphasis markers are stripped rather than converted
  (cleanest read over markdown-parity), markdown links keep their visible
  text and drop the URL, and a leading run of known Medium chrome ("Open in
  app", "Sign up", "Get app", a lone "Follow"/"Listen"/"Share", the "N min
  read" byline) is stripped — **conservative by design**: it only ever eats
  from the top and stops for good at the first line that isn't chrome, so a
  real paragraph that happens to echo a chrome phrase later in the piece
  (e.g. "sign up for our newsletter") can never be dropped; the "N min read"
  pattern alone is stripped wherever it lands, since no real sentence is
  ever literally equal to it. Publisher boilerplate on the *live-refetch*
  path (e.g. a TechCrunch promo banner) is explicitly out of scope for this
  tier — a future Reader-quality pass, not this one's job. Tried before
  Wayback (currently unreliable — archive.org rate-limiting), after
  domain-migration, in `_finish_backfill_after_direct_failure` — same
  "exactly one `content_refetch_log` row" invariant, `source='medium-search'`
  on success. **Known gap, not fixed here**: a Medium-tier candidate on a
  different host sometimes duplicates the article's own title/byline inline
  (a syndication convention) — not deduped against the stored title, so a
  synced article can occasionally show its title twice; flagged, not a
  correctness bug. Also added: `Library.articles_needing_content_backfill()`'s
  new `host_suffixes` parameter (plus a "Scope to host(s)" admin input) —
  narrows a run to matching hosts and, for those hosts only, bypasses the
  needs-manual-review exclusion (the point being to re-reach articles
  stuck in manual review because this tier didn't exist yet when they were
  last tried), while the defunct-service exclusion still applies regardless
  of scope. See ARCHITECTURE.md's "Medium-platform Exa fetch tier" section
  for the full write-up.
- **Medium-platform tier follow-up (2026-08 wrap-up sprint) — fetch-by-URL
  tried before search-by-title, plus a non-Medium recognized blocked
  host.** ~20 manual-review articles now have an exact, human-confirmed
  URL (via the corrected-URL CSV import above) that the tier never used —
  search-by-title was the only mode, so a generic title could miss or
  mismatch even when the real URL was already known.
  `linklib.medium_platform.fetch_content_by_url()` calls Exa's `/contents`
  endpoint (not `/search`) for the article's own current URL first — no
  title-match needed, since there's no candidate to disambiguate, just the
  one true URL; a miss or too-thin result falls through to search-by-title
  unchanged. Logged `source='medium-fetch'`, distinguishable from a
  search-by-title success (`source='medium-search'`) in
  `content_refetch_log` and the admin badge/count. Also added
  `shockwaveinnovations.com` — Cloudflare-blocked the same way, but not
  actually Medium underneath, so it lives in a separate
  `_OTHER_BLOCKED_HOSTS` set rather than being mislabeled into
  `_MEDIUM_CUSTOM_DOMAINS`; the tier's actual gate is now
  `is_recognized_blocked_host()` (the union of both sets), while
  `is_medium_platform_host()` keeps its original, narrower meaning
  unchanged. **Pre-merge live-proof follow-up:** two production traces came
  back with a `content_refetch_log` row indistinguishable from the
  pre-fetch-by-URL flow — no way to tell "the tier ran and missed" from
  "the tier was never reached" just from the log. Fixed before merge:
  `_try_medium_platform()` now always returns a 5th `note` element (a short
  trace of what it actually attempted), and `_finish_backfill_after_direct_failure`
  appends a `tier_notes` trace (migration and Medium both) to the final
  logged `detail` whenever a tier is genuinely reached — an unrecognized
  host still logs identically to before. `scripts/trace_medium_tier.py` is
  a new manual-QA script (registered in `/admin/system/scripts`) for a
  second, independent live confirmation path, plus a Wayback-snapshot
  content inspector for the "is the stored snapshot an empty
  client-side-rendered shell" question the same live-proof round raised.
  See ARCHITECTURE.md's "Medium-platform tier follow-up" section for the
  full write-up.
- **Durability audit item 1 — `ingest_url()` now runs the same content
  sanity check the Reader backfill uses, at save time.** Previously a bad
  fetch (paywall preview, bot-challenge interstitial, a fetch failure, real
  content under the 60-word floor) was stored exactly like a good one, with
  no signal anywhere — the same blind spot that let 54% of Medium-platform
  articles sit empty and unnoticed. `ingest_url` now runs
  `extract.assess_extraction_quality()` on every real fetch and, when it
  fails, flags the row (`articles.needs_content_check`/`content_check_reason`
  — additive columns, default 0/'') and logs a `content_refetch_log` row
  (`source='save'`) — never blocking or rejecting the save itself. The flag
  is only trusted when this fetch's content is what actually got stored
  (the write-once merge in `upsert()` can silently keep better pre-existing
  content on a resave — see `tests/test_content_downgrade_guard.py`), so a
  bad resave of an already-good article never mis-flags it. Clears
  automatically the moment `set_article_content_html` later succeeds for
  that article (a real backfill, or a resave that gets good content).
  Surfaced as a "Flagged at save" tile on `/admin/reader/backfill-content`,
  distinct from the existing Remaining tile (which is every row without
  `content_html` yet — true of the entire corpus by default, and says
  nothing about whether the original save itself looked suspect).
- **Durability audit item 4 — a per-article "Accept as final" override for
  the manual-review tier's one real false-positive.** An article with
  real-but-short content fails `assess_extraction_quality()` identically
  forever (no URL correction can fix it — the URL is already correct), with
  no exit short of a direct DB edit. `Library.accept_article_content()`
  writes a `content_refetch_log` row with a third `status` value,
  `'accepted'` — chosen specifically because it composes for free with
  `_manual_review_article_ids()`'s existing latest-attempt-is-`'failure'`
  check, no query changes needed there. `articles_needing_content_backfill()`
  and `count_content_backfill_remaining()` separately exclude the same
  latest-row-`'accepted'` set, so the override is durable against future
  automatic retries too, not just hidden from one admin list.
  `POST /admin/reader/backfill-content/{id}/accept` and `.../unaccept` are
  per-article only — **no bulk/select-all form exists on purpose**, this is
  a one-at-a-time escape hatch, not a backfill mechanism. Undo is fully
  additive (a new `'failure'` row, never deleting the `'accepted'` one), so
  an accept-then-reverse stays visible in the log. New admin-page section
  ("Accepted as final", with Undo per row) and stats tile. See
  ARCHITECTURE.md's "'Accept as final' manual-review override" section for
  the full write-up.
- **Durability audit item 2 (elevated) — a pre-backup integrity check,
  because the daily Drive backup is now the ONLY recovery path.**
  Railway-native volume snapshots turned out unavailable on Brian's plan
  (see the Phase O bullet above), and nothing in this app had ever
  run `PRAGMA integrity_check` against the live database — corruption
  would only ever have surfaced at restore time, by which point it would
  already be baked into every retained snapshot (14 daily + 8 weekly).
  `linklib.backup.check_integrity()` runs `PRAGMA integrity_check` plus the
  FTS5 self-check RUNBOOK.md §4's restore rehearsal already runs by hand
  (`INSERT INTO articles_fts(articles_fts) VALUES('integrity-check')`)
  against the live DB, wired into `backup_now()` immediately before every
  snapshot — same cadence as the backup itself, whichever trigger fired it.
  Every result (ok or failure) is logged to a new `integrity_check_log`
  table, shape mirrors `backup_log`. **Decision, flagged rather than
  decided silently: a failed check BLOCKS that night's upload**, logging a
  `backup_log` failure row too (so the existing status banner picks it up
  with no second code path) rather than uploading a possibly-corrupt
  snapshot anyway. Reasoning: the whole point of checking first is to keep
  corruption out of Drive; uploading it anyway would let it get pruned into
  the "kept" set on a later run and, eventually, become what the restore
  procedure reaches for — the exact failure mode this item exists to close.
  Skipping the upload leaves every already-retained good snapshot untouched
  (`prune_old_backups` only ever runs after a successful upload). A new
  "Pre-backup integrity check" banner on `/admin/library-backup` — coral on
  failure (not amber; a blocked backup isn't a routine/expected state),
  seafoam on ok — sits above the existing backup-status banner rather than
  merging into it, since "the backup succeeded" and "the DB is structurally
  sound" are two different facts. See ARCHITECTURE.md's `integrity_check_log`
  table row and `backup_now()`'s docstring for the full write-up.
- **Disk-space visibility on `/admin/checks`, and a real stale-marker bug
  fixed alongside it (2026-09) — a production incident (a script ran out of
  space copying `library.db` on a volume that was only 58% full) traced to
  nothing anywhere reporting where the volume actually stood.** Nothing in
  the app had ever read `/data`'s own usage — the pre-backup integrity check
  above validates the DB's structure, not the disk it lives on, and a
  volume genuinely running low would have shown no symptom until a copy or
  backup failed mid-write. `webapp.checks.disk_space_status()` reads
  `/data`'s usage live via `shutil.disk_usage` (never shells to `df`) and
  returns `None` — not a failure — when `/data` doesn't exist, so dev/CI
  environments (which have no Railway volume) degrade cleanly instead of
  erroring. A new "Disk space" section on `/admin/checks`
  (`_disk_space_banner`) always states the real numbers when the volume
  exists (green under 75%, amber at 75%, `var(--alert)` red at 85% —
  never coral, matching every other health-signal banner on this page) and
  says plainly that this check can't run outside the volume when it
  doesn't, mirroring `/admin/library-backup`'s own plain-sentence-plus-
  colored-box register rather than inventing a new one. **A related,
  separately-confirmed bug fixed in the same pass**: `maybe_backup()`'s
  debounce used to read a standalone `.last_backup` marker file, written
  only by that function's own successful runs — but the real trigger
  keeping backups current is the daily Railway Cron Service (see Phase O
  above), which calls `backup_now()` directly and never touches this file,
  so the marker had gone stale (confirmed 15 days out of date) and was
  quietly misrepresenting how recently a backup actually ran. Fixed by
  reading the debounce's "last success" straight from `backup_log` (via
  `Library.list_backup_log`) instead — the same table the status banner
  already treats as the single source of truth for backup history, so
  there's nothing left to drift out of sync with it. The marker file and
  its own `_marker_path()` helper are removed outright, not left dormant.
  See `tests/test_checks.py`'s disk-space section and `tests/test_backup.py`'s
  new `maybe_backup` tests for the regression coverage.
- **Durability audit item 3 — a durable start/finish record for the three
  `_JOB_STATE`-backed background jobs (at the time — now two, see below), so
  a redeploy or crash doesn't erase whether re-enrich, Historical sweep, or
  the Reader content backfill last
  succeeded, failed, or ever ran.** (**Superseded 2026-09, PR 3**: Historical
  sweep — the Archive Queue's own sitemap-backfill job — was retired along
  with the queue itself, so only re-enrich and the Reader content backfill
  remain live; `job_run_log`'s "backfill" job-name value is kept only as a
  historical/test pin at the storage layer, not a still-running job — see
  ARCHITECTURE.md's `job_run_log` schema-table row and
  `tests/test_job_run_log.py`'s own module docstring for the corrected,
  current shape.) `_JOB_STATE` (`webapp/app.py`) is an
  in-process dict — correct and unchanged for LIVE progress polling, but
  wiped silently on every Railway redeploy with no trace left behind. New
  `job_run_log` table (shape mirrors `backup_log`/`integrity_check_log`),
  written twice per run — `Library.start_job_run()` at the top of each of
  the three job functions, `Library.finish_job_run()` at every exit path
  (success, failure, and — for the content backfill, the one job with a
  stop control — a deliberate stop too). A shared `_job_run_banner()`
  helper renders "last run: outcome, N ago" on each job's own admin-page
  section, reusing the already-shipped `_relative_age()` helper for the
  "N ago" text — same green/amber/coral posture as the backup/integrity
  banners above. A row stuck at `status='running'` with no `finished_at` is
  exactly what a crash mid-run looks like, and the banner says so
  explicitly rather than rendering it as ordinary live progress. **Fixed
  (2026-08 wrap-up sprint item 3): that interpretation only holds when
  nothing live actually corresponds to the open row** — confirmed in
  production twice, the banner was rendering "never finished — likely
  interrupted by a deploy or crash" directly above the same page's own
  genuinely-in-progress status panel, because it never checked
  `_JOB_STATE` before assuming an open row meant a crash. Fixed by checking
  `_job_get(job_name)["running"]` first; when the job is actually live, the
  banner renders a plain in-progress line instead. See ARCHITECTURE.md's
  `job_run_log` table row for the full write-up.
- **"Snapshot on Wayback" guidance link (2026-08 wrap-up sprint item 4) — a
  link and a sentence, nothing more.** Brian proved out a manual workaround
  in production: for an article whose live page loads fine in a browser
  but is bot-blocked to this app's own fetcher, manually triggering
  archive.org's Save Page Now creates a fresh snapshot the Wayback tier can
  then retrieve, since archive.org's own crawler isn't subject to the same
  Cloudflare fingerprint block. Each "Needs manual review" row with a known
  URL now shows a "Snapshot on Wayback ↗" link to that exact Save Page Now
  URL, plus one guidance sentence in the section's explainer text — no
  automation, no tracking of whether a snapshot was taken. See
  ARCHITECTURE.md's "'Snapshot on Wayback' guidance link" section for the
  full write-up.
- **Bare-domain `www.` retry (2026-08) — a narrow, single-point fix in
  `extract.fetch_page()`.** `scripts/diagnose_reader_backfill_failures.py`
  confirmed against production that codingvc.com refuses the connection at
  its bare domain while www.codingvc.com serves the identical page fine (6
  affected saved articles) — a real fetcher gap, not a URL/data-correction
  issue, since nothing about the stored URL is wrong. Fixed once, inside
  `fetch_page()` itself (the sole low-level HTTP entry point every caller —
  `pipeline.py`, `enrich.py`, `queue.py`, `authcheck.py`, `webapp/app.py` —
  goes through, confirmed by inventory before building rather than assumed):
  a `requests.exceptions.ConnectionError` on the bare-domain attempt (DNS
  failure, connection refused, unreachable) triggers exactly one retry
  against the same URL with a `www.` host, only when the URL doesn't already
  have one. **Deliberately scoped to connection-level failures only** — an
  HTTP-status failure (403, 404, ...) is never retried this way, confirmed
  the same day that inc.com 403s identically on both the bare and www hosts,
  so that's a different problem this fix doesn't touch. No change to the
  manual-review/domain-migration/Medium-platform/Wayback fallback tiers in
  `linklib/pipeline.py` — this sits underneath all of them, so a bare-domain
  connection failure that this retry resolves never even reaches those
  tiers.
- **Article purge flow (durability follow-up) — a permanent-deletion escape
  hatch for the narrow "genuinely nothing was ever saved" set, mirroring
  the manual-review corrected-URL CSV round trip exactly.**
  `Library.articles_eligible_for_purge()` (plain-text `content` under
  `extract._MIN_CONTENT_WORDS` AND no `content_html` ever backfilled) is
  deliberately NOT the same set as the Remaining tile — that's the vast
  backlog of articles with perfectly good text just waiting on structure
  backfill; this is only the much narrower real-purge candidates.
  Export/preview/commit CSV round trip
  (`linklib/purge_csv.py`), never deleting anything before an explicit
  confirm. Two independent guards on top of preview-then-confirm:
  `MAX_PURGE_PER_RUN` (50, enforced both at CSV-parse time and again on
  the raw commit POST) and a required "type N to confirm" field the
  commit route validates against the actual posted row count. The commit
  route also runs a real, unconditional `backup.backup_now()` immediately
  before the delete loop (not the debounced `maybe_backup()` every other
  bulk-delete flow uses) and aborts the whole purge if that snapshot
  fails — the nightly backup is the ultimate net, but shouldn't be the
  first one for something this irreversible. Each delete goes through a
  TOCTOU re-check (an article backfilled with real content between
  preview and commit is skipped) and `Library.purge_article()`, which
  write-then-read-back verifies the delete actually took, per CLAUDE.md's
  one-off-admin-fix discipline, applied here as a standing check.
  `Library.delete_article()` itself was extended to also clean up
  `content_refetch_log`/`url_correction_log` — a general fix benefiting
  all four existing callers (dedupe removal, review-removals, the member
  Reader's own delete, and now purge), not something purge-specific.
  Deliberately NOT deleted: `enrichment_cost` (a real-money spend ledger)
  and `archive_audit_log` (the historical record, which gets a new
  `'delete'` row for the purge FIRST, before the delete itself runs) —
  same non-destructive precedent as `tool_audit_log`/`community_audit_log`
  outliving a deleted tool/community. See ARCHITECTURE.md's "Article
  purge flow" section for the full write-up.
- **Reader tag editing (Phase 5c) — a deliberate, tags-only exception to
  Phase 5's "no inline management in the Reader" rule; delete/archive stay
  admin-only and unchanged.** The investigation that opened the phase found
  that **both write paths already existed and were simply unreachable**, so
  this is overwhelmingly a UI phase, not a backend one.
  `POST /library/{article_id}/tags` survived Phase 5 **with zero callers** —
  the old Archive page's "Edit tags" button was removed, its endpoint wasn't
  — and `POST /feed/save` **already accepted a `tags` field**. What was
  actually missing was a usable UI: both save paths collected tags through a
  blocking `window.prompt()`, which is neither inline nor able to
  autocomplete. (The build brief's premise that there was "no way to add tags
  at all when saving from Feed" was therefore slightly off — a clunky path
  existed; it's been replaced, not invented.) The only backend changes are
  `/feed/save` returning `{"ok","id","tags"}` instead of `{"ok"}` so the
  reader can flip into its saved state without a reload, and
  `_resolve_reader_content` falling back to `SELECT * FROM articles WHERE
  url=?` so a Feed item that's already in the library opens as saved (it used
  to offer "+ Save" again, which would have left the new editor unreachable
  for exactly the "any Feed item that's been saved" case). That url-matched
  row skips only the **plain-text `content` cache** — using it there would
  silently strip images/links from a Feed item that currently reads with them
  intact, whereas an explicit by-id open accepts it. **Phase 5b's
  `content_html` is deliberately not skipped**, since it's real structured
  HTML and therefore a strict upgrade over both the plain-text cache and a
  live re-fetch, however the row was reached. That distinction is the merge
  point between the two phases and is worth remembering: 5c was written
  against the pre-5b premise that a cached article's content is *always*
  flattened plain text — true when 5c was built, and made false for any
  backfilled row the moment 5b landed. The two phases were developed in
  parallel and reconciled at merge, not sequentially. Investigation also resolved the
  never-settled question about the two Admin tag tools: both are
  **vocabulary-level, not per-article** — "Tag cleanup" merges/renames/deletes
  a tag across the whole library, "Tagging style" learns the tagging style for
  the enrichment prompt — so nothing here duplicates them; they read and write
  the same `articles.tags_json` through the same `Library` methods, and
  autocomplete is a native `<datalist>` built from the same `all_tags()`
  vocabulary Tag cleanup curates. **Propagation matches those tools exactly:**
  `update_tags` writes `tags_text`, so the `articles_au` FTS trigger reindexes
  automatically, and — like rename/delete — nothing re-embeds inline even
  though tags are part of the embedded document text; `article_embeddings`'
  content hash means the next `embed_backfill` picks up the change. Two real
  bugs were caught **only** by the standing live-verification rule, neither of
  which a rendered-HTML assertion could have surfaced: (1) the save-time form,
  as a plain third flex child of `.rr-row`, became a third column and squeezed
  the row's title into an unreadable sliver (fixed with `flex-wrap` on the row
  and `flex:0 0 100%` on the form); (2) on mobile portrait, focusing the tag
  input let the browser scroll the document far enough to clip the panel's
  first chip row *and the entire toolbar* off the top — the panel opened where
  the user couldn't see it, the same shape as the Phase 5 "tapping an article
  does nothing" bug (fixed with `focus({preventScroll:true})` plus an explicit
  `pane.scrollIntoView`, which moves only the document, never the pane's own
  `scrollTop`, so a part-read article keeps its position). Both have
  regression tests. Also worth carrying forward: the Reader's `<script>` is
  built **inline in the route**, not as a module-level `*_JS` constant, so
  `webapp.checks.script_syntax_problems()` does **not** cover it —
  `tests/test_reader_tag_editing.py` node-checks the rendered response
  directly, per the standing "validate what the browser actually receives"
  lesson.
- **Thought Leadership Admin CRUD, Phase 1 — the four `/thought-leadership`
  columns (Writing, Speaking & Events, Podcasts, Press) are now admin-managed,
  not hardcoded.** Phase 0 investigation found all four columns reading from
  one shared Python module, `webapp/thought_leadership_data.py` (33 hand-
  written `TLItem` dataclass instances across four module-level lists, no DB
  table, no admin surface — edited directly by hand each time). Phase 1
  replaces that with a `thought_leadership` table (see `linklib/db.py`'s
  table comment and `ARCHITECTURE.md`'s Thought Leadership section for the
  schema) and a new admin section, `/admin/thought-leadership/third-party` — add/edit/
  delete across all four types from one filterable list, same CRUD pattern
  as `/admin/tools/resources` (`/admin/tools/benchmarks` at the time this
  phase shipped — renamed in the admin URL convention PR, see the Phase 1b
  bullet below). `scripts/archive/migrate_thought_leadership.py`
  (dry-run by default, `--apply` to write, write-then-read-back verified —
  same convention as `scripts/backfill_logos.py`) is the one-time migration;
  it moved 32 of the 33 entries. Two decisions carried over unchanged from
  the Phase 0 sign-off: **role/capacity stays free-text inside `title`** (no
  separate column — every existing "Host"/"Co-Chair"/"Guest" etc. is already
  a trailing parenthetical or em-dash suffix on the title string, and no
  rendering path reads a separate field), and **photos are out of scope**.
  The one entry that used them (Abacum AI Summit, 2 photos + a caption) is
  the 33rd entry, excluded from the migration and from the admin form — it
  stays hardcoded as `_TL_PHOTO_ENTRY` in `webapp/app.py`, merged into the
  Speaking & Events column at render time so it doesn't disappear from the
  public page, but it isn't editable via `/admin/thought-leadership/third-party`.
  `webapp/thought_leadership_data.py` itself is **not deleted** — it stays in
  the repo, unimported, as a rollback reference (same non-destructive-
  retirement precedent as `screenshot_is_product`/`field_reviews` above).
  The "Show all N" expand/collapse on Speaking & Events and Podcasts
  (client-side JS, cap of 6) needed no changes — it operates on whatever
  list of items the route hands it, DB-backed or not. Ordering also carried
  over unchanged: undated items (`sort_key == ''`) float to the top of their
  section, everything else sorts newest-first by `sort_key`; a new
  `display_order` column is the tiebreaker within each group (assigned as
  each entry's original list index during the migration) so items that
  shared a `sort_key` don't reshuffle against the pre-migration page. Out of
  scope for this phase, same as Phase 0 flagged: a "Show all" full-listing
  page (the toggle already covers the practical need), and the three
  Framework/Playbook/Setup Guide featured cards above the four columns
  (separate, hardcoded `fcard(...)` mechanism in `webapp/app.py`, untouched).
- **Thought Leadership Admin CRUD, follow-up — `sort_key` is now derived
  from `date_label`, not hand-typed.** After Phase 1 shipped, two separately
  editable free-text fields encoding the same date (`date_label` for display,
  `sort_key` as `YYYY-MM` for ordering) caused real confusion twice: a
  suspected mismatch between the two, and a blank `sort_key` on an entry
  that was correct-by-design (the undated "Cash Flow Show — Full Episode
  Feed" podcast link, which intentionally floats to the top of its section).
  Investigation confirmed all 32 migrated `date_label` values fit
  `"Mon YYYY"` (e.g. "Jun 2026") except that one intentional blank, and that
  every `date_label`/`sort_key` pair was already internally consistent — so
  no backfill was needed, just removing the redundant manual field going
  forward. The `sort_key` input is gone from both admin forms; `date_label`
  is now the only field an admin fills in, and `webapp/app.py`'s
  `_sort_key_from_date_label` derives `sort_key` server-side on every add/
  edit save ("Mon YYYY" or "Month YYYY" -> "YYYY-MM"; blank or unparseable
  input -> `""`, the same floats-to-top convention, not a save-blocking
  error — `date_label` itself still isn't validated/constrained, deliberately
  out of scope here). Because a typo silently floating an entry to the top
  would be a *worse* version of the exact confusion this fix set out to
  solve, an inline warning appears both on the edit form and as a small
  icon in the `/admin/thought-leadership/third-party` list row whenever a non-blank
  `date_label` fails to parse — distinguishing "this admin meant to leave
  the date blank" from "this admin's date didn't parse." `sort_key` itself
  stays a real column (queries need a plain sortable string, not a
  `date_label` to reparse on every read) — this is a save-path change, not
  a schema change. Also folded into this same round: relabeled
  `display_order` to "Display order (tiebreaker)" with explicit helper
  copy, and fixed the admin add form always submitting `display_order=0`
  instead of triggering `Library.add_thought_leadership`'s intended
  per-type auto-assign (`MAX+1`) — a blank field now correctly parses to
  `None` and flows through to that auto-assign path.
- **Thought Leadership — click-to-expand descriptions.** `description` has
  been editable via `/admin/thought-leadership/third-party` since Phase 1, with 32+
  entries carrying real synopsis text, but the public `/thought-leadership`
  page never rendered it — confirmed via git history that no commit, in
  this build or the pre-migration `thought_leadership_data.py` version,
  ever wired `description` into the column-rendering path
  (`col_preview_item` only ever read `title`/`venue`/`date_label`/`url`).
  Fixed with a small per-entry toggle, not a default-visible change: each
  entry still renders exactly as before (title, source/venue, date only)
  until its chevron button is clicked, which reveals `description` inline
  beneath it — independent per entry, so multiple can be expanded at once,
  and it collapses back on a second click. **Deliberately a small button,
  not a full-row click handler:** most entries' title is itself an outbound
  link to the piece, so a row-level click target would fight that link for
  the click. Suppressed entirely (no affordance rendered) when there's
  nothing to reveal — an empty `description`, or `needs_synopsis` (a
  deliberate placeholder, not real content) — rather than inviting a click
  that does nothing. Vanilla JS (`toggleTLDesc`, alongside the pre-existing
  `toggleTLCol`); degrades safely without JS since the description `<div>`
  is hidden via the `hidden` attribute server-side, not a CSS class a
  disabled-JS page would still need to override. Same `hidden`-attribute
  pattern extends across all four columns and both mobile/desktop layouts —
  no new responsive CSS needed since the toggle/description block is a
  plain stacked element inside the existing `.tl-col-item`. Independent of,
  and unaffected by, the pre-existing "Show all N" cap/expand mechanism —
  the two toggles coexist on the same entries without conflict.

- **Original Content, Phase 1 — the 3 flagship pieces' card metadata moves
  off the hardcoded `_TL_FEATURED_CARDS` tuple into a new `original_content`
  table, and the schema is built to let a brand-new piece be authored
  entirely from admin later with no code change per article.** A Phase 0
  investigation (read-only, reported before any code) confirmed: the three
  bespoke routes (`/thought-leadership/growth-engine-ratio`,
  `/thought-leadership/ai-hackathon-playbook`, `/thought-leadership/netsuite-mcp`)
  all share the same shell (`page page-full article-atlantic` +
  `.tool-inner`/`.tool-prose` + a `&larr; Thought Leadership` back-link) —
  the article template a future Phase 2 will match; `_TL_FEATURED_CARDS`
  (a 6-tuple: href/tag/tag_color/title/desc/cta) is the single shared source
  `_tl_fcard()` renders on both the homepage and `/thought-leadership`, with
  `test_flagship_cards_content_shared_between_homepage_and_thought_leadership`
  as the drift guard; `webapp/thought_leadership_data.py`'s Writing column
  (still unused, kept as a rollback reference — see the Phase 1 Thought
  Leadership entry above) has 3 entries whose `url` already points at those
  same three `/thought-leadership/*` paths — genuine overlap with this
  table's own rows, flagged and left alone per the investigation's scope,
  Brian's call to make later; and `netsuite_mcp()`'s route function ends
  right before the unrelated "Sail, Don't Row" game code begins, the correct
  (and only safe) insertion point for a future `GET /thought-leadership/{slug}`
  catch-all — it must be registered after all three literal routes so they
  keep winning by FastAPI's registration order, with no separate
  custom-route column needed since the three migrated rows' `slug`s are set
  to match their existing route path segments exactly. No markdown parser
  was in `requirements.txt` (confirmed, matching Brian's own expectation).
  **Schema** (`original_content` in `linklib/db.py`): `slug` (unique),
  `title`, `teaser`, `tag_label`, `link_label`, `body_md` (nullable —
  `NULL` is load-bearing, meaning "card metadata only, one of the three
  bespoke routes renders the real piece"; a real markdown string means the
  future shared article template renders it), `status` (`'draft'`\|`'live'`),
  `featured_home`, `date_label`/`sort_key`/`display_order` (same convention
  as `thought_leadership`'s own columns — `sort_key` derived from
  `date_label` via the same `_sort_key_from_date_label`, reused verbatim —
  except ordering here is **`display_order` first, `sort_key` only a
  tiebreak**, the opposite priority from `thought_leadership`'s own
  `_TL_ORDER_SQL`, since this is a handful of curated flagship cards, not a
  chronological feed). `tag_color` (each card's small category-tag accent)
  was deliberately never promoted to a stored column — `_oc_card_tuple`
  cycles it from the same 3 established colors
  (`--coral-deep`/`--seafoam-deep`/`--navy-light`) by card position, so the
  3 migrated pieces render with their exact original colors and a 4th+
  piece still gets a sane one with no admin decision required. **Migration**
  (`scripts/migrate_original_content.py`, not yet archived since it hasn't
  run against production — same dry-run/`--apply`/write-then-read-back
  convention as `scripts/archive/migrate_thought_leadership.py`) reads
  `_TL_FEATURED_CARDS` directly (`planned_rows()`, shared with its own test
  file so the test asserts against the same source the script would insert,
  not a duplicated copy) and seeds all 3 rows with `status='live'`,
  `featured_home=1`, `body_md=NULL`, `slug` = each href's last path
  segment, idempotent against a non-empty table. **Rendering**: the
  homepage's flagship row and `/thought-leadership`'s featured row both
  call the new `_oc_featured_cards_html(rows)` — same underlying
  `_tl_fcard()`/`.tl-card`/`_TL_SHARED_CSS` markup as before, now fed by
  `Library.list_original_content_for_home()` (homepage: `status='live' AND
  featured_home=1`) and `Library.list_original_content(status="live")`
  (`/thought-leadership`: every live piece, regardless of `featured_home` —
  the `.tl-featured` grid already wraps past 3 via
  `repeat(auto-fit,minmax(220px,1fr))`, no layout change needed as more
  pieces are added). `_TL_FEATURED_CARDS` itself is **not deleted** — it
  stays in the repo, unimported by any route, purely as a rollback
  reference (same precedent as `thought_leadership_data.py`); the drift-guard
  test now asserts identical rendering from the DB-backed source instead.
  Fresh-DB tests (a new tempfile per test, same as every other test in this
  suite) have zero `original_content` rows by default now that seeding is a
  manual migration, not automatic schema setup — `tests/
  test_thought_leadership_homepage_teaser.py`'s `env` fixture now seeds the
  3 flagship rows via the migration script's own `planned_rows()` before
  yielding, so every existing test in that file still exercises the
  post-migration state it always assumed. Phase 2 (markdown rendering +
  the `GET /thought-leadership/{slug}` catch-all route) and Phase 3 (admin
  CRUD at `/admin/thought-leadership/original`, plus an "Original Content" box beside
  the existing Thought Leadership box on the admin index — its tool-count
  badge is just `len(items)`, no new badge mechanism needed) are separate,
  sequential PRs.

- **Original Content, Phase 2 — markdown rendering + the shared article
  template at `GET /thought-leadership/{slug}`.** New dependency
  `python-markdown` (`import markdown`, aliased `_markdown` in
  `webapp/app.py` to keep it visually distinct from the three pre-existing
  hand-rolled, zero-dependency JS markdown renderers already in this file —
  those serve a different purpose, live chat-produced markdown rendered
  client-side, and are untouched), with only the `fenced_code` and `tables`
  extensions enabled — no syntax highlighting/Pygments, out of scope per
  the build brief. `_render_original_content_markdown` also wraps each
  rendered `<table>` in the same `overflow-x:auto` container every other
  table on this site already uses, since markdown's `tables` extension
  emits a bare `<table>` with no wrapper of its own — a small regex
  post-process, verified live (Playwright, 390px portrait and 844×390
  landscape) with a genuinely wide table to confirm it scrolls inside its
  own wrapper without ever forcing the page itself to overflow
  horizontally. Raw HTML in `body_md` passes through unescaped, per the
  approved out-of-scope decision — the field is admin-authored only, never
  public input. `_original_content_article_body` is the shared template,
  matching the three bespoke pieces' shell exactly (confirmed by reading
  all three first, not guessed at) — `page page-full article-atlantic`,
  the same back-link, `.tool-prose`, the same eyebrow/`<h1>`/byline
  treatment — with only the rendered markdown itself, wrapped in a scoped
  `.oc-body` div, differing per page. `.oc-body`'s CSS mirrors the
  Reader's own `.reader-body` treatment for code/pre/table (the one
  existing precedent in this codebase for "how does this site style
  rendered long-form content"), except blockquote reuses this shell's own
  established Quote treatment (`.article-pull`'s border-left/italic style)
  instead of the Reader's coral box — a coral-boxed quote would clash with
  BRAND.md's "coral: rare warm accent, one per screen" rule inside the
  exact shell where Quotes are already border-rule-not-box, not a boxed
  callout. `GET /thought-leadership/{slug}` is registered immediately
  after `netsuite_mcp()` ends, before the unrelated `/play` route — the
  gap the Phase 0 investigation confirmed was the only safe insertion
  point — so the three literal bespoke routes always win by FastAPI's
  registration order; proven, not just asserted, by a test that inserts an
  `original_content` row with a slug colliding with each of the three
  bespoke pieces and confirms the bespoke page's own content renders, not
  the DB row's. Serves only `status='live'` rows with a real `body_md`; a
  `body_md IS NULL` row 404s here too (defense in depth — registration
  order is what actually protects the three bespoke pieces day to day, not
  this check) and an unknown slug 404s the same way. A `status='draft'`
  row 404s for a signed-out visitor and renders normally, at its own
  canonical URL, for an active admin session — no separate preview URL or
  token. `requirements.txt` and `webapp/app.py`'s `_OPEN_SOURCE` list both
  gained the new dependency in this same PR, per the docs discipline.
  Spot-checked all three bespoke pages still load unaffected before
  merging, per the process brief.

- **Original Content, Phase 3 — admin CRUD at `/admin/thought-leadership/original`.**
  Read `/admin/thought-leadership/third-party`'s existing list/add/edit/delete routes
  first and matched their pattern (list layout, form styling, auth check) —
  not built from scratch. One real addition beyond that pattern: slug
  validation. A bad slug here is a genuine failure mode (an unreachable
  page, or a page that silently loses to a bespoke route) that
  `thought_leadership`'s free-text title never risked, so `_validate_oc_slug`
  checks the slug is present, well-formed (`_OC_SLUG_RE` — lowercase
  letters/digits/single hyphens only, **not auto-lowercased**: an uppercase
  or malformed slug is rejected outright, not silently normalized, so what
  an admin sees in the URL is exactly what they typed), doesn't collide
  with one of the three bespoke pieces' own route path segments
  (`_OC_RESERVED_SLUGS` — a colliding slug would save fine but be
  permanently unreachable, since the literal route always wins registration
  order), and isn't already used by a different row. A rejection re-renders
  the same form with the submitted values preserved and an inline coral
  error banner — the richer pattern `_feed_form_page`/
  `admin_feeds_new_submit` already established for exactly this kind of
  validation (chosen over `thought_leadership`'s own blunter
  raise-`HTTPException`-on-bad-input approach, since a slug collision is
  exactly the kind of mistake an admin needs to see and fix in place, not
  get bounced to a bare error page for). Slug edits are allowed at any
  time, including on a live piece — the form's helper text warns this
  breaks any existing link, since there's no redirect system (out of
  scope, same call Phase 2 made). `sort_key` is derived from `date_label`
  on every save via the same `_sort_key_from_date_label`
  `thought_leadership` already uses — not exposed as a form field.
  `display_order` left blank on add auto-assigns the next value; left
  blank on edit is a deliberate clear, treated as `0` — same convention as
  `thought_leadership`'s own edit route. `body_md` left blank keeps the
  row as card-metadata-only (`NULL`, not `''` — `_oc_values_from_form`
  maps an empty textarea to `None`), the same state the three flagship
  rows have always been in; verified this doesn't regress Phase 1's
  card-rendering path (a piece created this way still renders as a
  flagship/`/thought-leadership` card immediately) and correctly 404s at
  its own `/thought-leadership/{slug}` page (Phase 2's `body_md IS NULL`
  guard) until a later edit fills in a body — and verified the reverse
  integration too: a piece with a real `body_md`, set `status='live'`, is
  immediately reachable at its own page. The admin index's "Thought
  Leadership" group gained a second item pointing at
  `/admin/thought-leadership/original` — `count_label` is `len(items)`, so the badge
  auto-updated with no additional wiring, confirming the Phase 0
  investigation's finding. Mobile-verified (Playwright, 390px portrait,
  real `.tap()` interaction) that the list view and the add/edit form both
  render with no horizontal overflow and are actually fillable by touch,
  not just present in the markup.

- **Original Content, Phase 4 investigation — read-only assessment of
  porting the three bespoke pages, reported to Brian before any code.**
  Found all three pages have zero images and zero JS/interactivity except
  Growth Engine Ratio's live calculator (~380 lines of JS driving two
  dynamically-generated SVG charts) — a genuine, structural blocker to a
  clean markdown port for that one page specifically, since it's real
  computation, not content. Confirmed directly, not assumed: raw HTML, a
  raw `<style>` block, and even a raw `<script>` block all pass through
  `_render_original_content_markdown()` completely untouched, so every
  custom visual device on the other two pages (numbered step-tracks,
  colored tables, use-case cards, 2×2 matrices, tier strips) is
  preservable at full fidelity as a raw HTML block in `body_md` — the real
  choice per page is an authoring-ergonomics trade-off (raw HTML/CSS
  blocks keep the exact look but aren't prose-editable; simplifying to
  plain markdown lists/tables is genuinely easy to edit but loses the
  custom visual), not a hard technical wall, except for GER's calculator.
  Also surfaced the structural fact that applies to porting any of the
  three: none can be served at its *current* URL without also retiring its
  bespoke Python route, since `_OC_RESERVED_SLUGS` (and, underneath that,
  route-registration order) is what keeps a literal bespoke route from
  ever losing to the generic catch-all — "port the content" and "free up
  the slug" are two separate steps, not one.

- **Original Content, Phase 4a — NetSuite MCP ported to `body_md`; its
  bespoke route retired, the first of the three literal routes to go.**
  Followed the Phase 4 investigation's hybrid approach: prose became real
  markdown; the five visually-designed elements (`.ns-case`/`.ns-tip`
  use-case cards, `.ns-step`/`.ns-num` phase tracks, `.ns-table`/
  `.ns-trouble` tables, `.ns-note`, `.ns-qr`) were kept as raw HTML blocks
  in `body_md`, verbatim, using their original CSS classes — copy was
  extracted exactly as published, no rewriting, no paraphrasing. Those
  classes' CSS moved out of the retired route's own `<style>` block into a
  new `_OC_NETSUITE_MCP_CSS` constant, every selector rescoped under
  `.oc-body` (`.oc-body .ns-table` etc.) so it only ever applies inside a
  rendered Original Content article, never sitewide — and so `.ns-table`/
  `.ns-trouble`'s two-class specificity keeps outranking the shared
  template's own generic `.oc-body table` rule, preserving their original
  navy-header/zebra-striped look instead of falling back to the generic
  styling. `scripts/migrate_netsuite_mcp_content.py` (dry-run/`--apply`/
  write-then-read-back, standard convention) sets `body_md` on the
  existing Phase-1-seeded row and also sets `date_label` to "June 2026"
  (blank since Phase 1, since `_TL_FEATURED_CARDS` tuples never carried a
  date) — restoring the byline the bespoke page always showed, a
  visual-parity fix bundled into the same script rather than a separate
  change. **Verified before deleting the route, not assumed**: with the
  DB row updated but the bespoke route still live, the shared template's
  real output was rendered to a standalone file (bypassing the still-live
  bespoke route, which would otherwise win at the real URL) and
  screenshotted at desktop (1280px) and mobile (390×844) against the live
  bespoke page. All 23 headings matched, same order, same text. Every
  ported designed element came back pixel-identical. Two small, expected,
  documented deltas, not fixed: inline `<code>` spans now render with the
  shared template's gray-pill background (an inherent side effect of
  `.oc-body pre,.oc-body code`'s generic styling applying — arguably a
  consistency win, matching `<code>` everywhere else on the site now), and
  on mobile, the italic subtitle line now renders just *after* the byline
  instead of just before it, since the shared template hardcodes the
  byline immediately after `<h1>` and the subtitle (a field
  `original_content` has no column for) had to become the first line of
  `body_md`, which renders after that hardcoded byline — fixing the
  ordering would mean changing the shared article template's shape for
  every Original Content piece, not just this one, so it's left as a
  documented deviation rather than bundled into a content port. Only after
  that visual-parity check did the bespoke `netsuite_mcp()` Python
  function get deleted outright — the `/netsuite-mcp` -> `/thought-
  leadership/netsuite-mcp` 301 redirect stays (its target is still a real,
  correct URL, just served by the catch-all now); `"netsuite-mcp"` came
  out of `_OC_RESERVED_SLUGS`, since it's no longer claimed by a bespoke
  route. Growth Engine Ratio and Sail Don't Row are untouched, still fully
  bespoke — separate, not-yet-scoped phases (4b/4c), and per the Phase 4
  investigation, GER's port specifically still needs a decision on what
  happens to the calculator before it can proceed the same way.
- **Original Content, Phase 4b — "Sail, Don't Row" (the AI hackathon
  playbook) ported the same way, plus two real specificity bugs the
  process caught before shipping.** Same hybrid approach as Phase 4a:
  prose became real markdown (12 H2 sections); the page's designed
  elements — the 2×2 value/effort matrix, the Inspire→Sleep→Build
  flowchart (including its `<640px` vertical-arrow variant), the
  Ship/Iterate/Park verdict tier strip, the resource-link list, the
  6-step phase track (used twice), and the Notion intake-form template
  box — were kept as raw HTML in `body_md` using their original `.fah-*`
  classes verbatim, moved into a new `_OC_HACKATHON_CSS` constant scoped
  under `.oc-body` exactly like `_OC_NETSUITE_MCP_CSS`. Confirmed dead and
  deliberately NOT carried forward: `.fah-verdicts`/`.fah-verdict`/
  `.fah-v-*`, `.fah-pull`, and `.fah-motif` — defined in the retired
  route's own `<style>` block but never referenced by any element in its
  body. `scripts/migrate_hackathon_playbook_content.py` follows Phase 4a's
  exact script shape (dry-run default, `--apply`, write-then-read-back).
  **Two real bugs surfaced by the screenshot-diff verification step,
  neither visible from reading the code — both fixed before the route was
  touched:**
  1. **A double-escape bug, the third known instance of this pattern**
     (see "Speaking &amp; Events" and the Phase 3 admin-edit-page `<title>`
     tag elsewhere in this doc): the row's `title` was seeded (Phase 1)
     as `"Sail, Don&rsquo;t Row"` — pre-escaped for `_tl_fcard()`'s raw
     `<h3>` insertion, the call site Phase 1 was built for. Phase 2's
     `_original_content_article_body()` correctly calls `_esc()` on
     `title` for the real `<h1>` (needed for genuinely plain-text
     admin-typed titles), which double-escaped this one, rendering the
     literal text `Sail, Don&rsquo;t Row` in the browser. Fixed by adding
     `TITLE = "Sail, Don't Row"` (plain, matching the retired route's own
     `<h1>` text byte-for-byte) to the migration script rather than
     reusing `row["title"]` — a deliberate, documented side effect: since
     the homepage/`/thought-leadership` flagship cards render straight
     from this same DB row via `_oc_featured_cards_html`/`_tl_fcard()`
     (not the frozen, unimported `_TL_FEATURED_CARDS` tuple), fixing the
     stored title also changes those cards from the curly entity to a
     straight apostrophe — `tests/test_thought_leadership_homepage_teaser.py`'s
     fixture was updated to apply this same title fix after its Phase-1
     seed, so the suite models actual post-migration production state
     rather than silently drifting stale the moment the migration ships.
     Phase 4a's netsuite-mcp migration never touched `title`, so it never
     needed an equivalent fixture update.
  2. **Two CSS specificity gaps, found only by comparing real bounding
     boxes (`element.bounding_box()`), not screenshots** — a full-page
     screenshot glance looked fine even with a ~110px real height
     difference buried in one repeated component. First: `.fah-body h3`/
     `.fah-template h3` never declared their own `line-height` on the
     original bespoke page, relying on `body{font:16px/1.65 ...}`'s
     sitewide inheritance — but `_OC_ARTICLE_CSS`'s shared
     `.oc-body h1,h2,h3...{line-height:1.3}` rule (written for real prose
     section headings) matches these same in-card h3 tags too, and an
     explicit declaration always wins over inheritance regardless of
     specificity, collapsing each step/field-card title by 6px and
     compounding across every repeated card in the phase track and intake
     form. Fixed by restating the original's effective `1.65` directly on
     both selectors. Second, smaller and easy to miss precisely because it
     runs the opposite direction: `.fah-body p`/`.fah-tier p` are each only
     one class + a tag on the original page, so they already lose their
     own `line-height`/`margin-bottom` to the sitewide
     `.article-atlantic .tool-prose p{line-height:1.75;margin-bottom:22px}`
     rule there (two classes always outranks one) — a pre-existing quirk
     of the original page's own CSS. Prefixing every selector with
     `.oc-body` for scoping (this whole file's convention) incidentally
     gave exactly these two selectors a second class, tying the sitewide
     rule's specificity; since the article's own `<style>` tag loads after
     the sitewide one, the tie then resolved the *opposite* way, so the
     port's declared values won where the original's never did. Fixed by
     dropping `line-height`/`margin-bottom` from both ported selectors so
     they lose to the sitewide rule again, same as the live original —
     matching the page's actual rendered behavior rather than "fixing" a
     CSS quirk the live site never showed. (`.fah-template p`, `.fah-r-desc`,
     `.fah-flow-caption` were checked too and already lose to the same
     sitewide rule on both pages without any change, since none of them
     ever declared `line-height`/`margin-bottom` in the first place.)
     Phase 4a's near-identical `.ns-body h3`/`.ns-qr h3` selectors have the
     same latent line-height gap and were not audited or touched here,
     since NetSuite MCP is out of scope for this PR — worth a follow-up
     check there. Post-fix, every `.bounding_box()` comparison across the
     matrix, flowchart (both the desktop and `<640px` vertical-arrow
     variants), tier strip, resource list, template box, and both phase
     tracks matched to within 1-2px (subpixel/rounding), and every cropped
     screenshot pair was visually confirmed identical at desktop
     (1280px), mobile portrait (390×844), and mobile landscape (844×390),
     with zero horizontal overflow at any width. Only after that
     confirmed parity did the bespoke `finops_ai_hackathon()` route get
     deleted — the `/finops-ai-hackathon` 301 redirect stays (its target
     is still real, just served by the catch-all now);
     `"ai-hackathon-playbook"` came out of `_OC_RESERVED_SLUGS`, leaving
     only `"growth-engine-ratio"` reserved. The closing bio blurb's
     `/play` easter-egg link ("Sail, Don't Row is also a game") carried
     over verbatim into `body_md`.

- **Original Content, Phase 4c — Growth Engine Ratio, the last of the three
  flagship pages, SPLIT rather than ported whole — resolving Phase 4's
  open question about its live JS calculator.** Unlike Phase 4a/4b, this
  page couldn't be ported as one piece: alongside prose/formula/table/quote
  content it embeds a ~380-line live JS calculator (point-in-time and a
  bounded -2..+2 timeline mode, each driving a dynamically-generated SVG
  chart) — genuinely interactive, not markdown-representable. Brian's
  approved resolution: split the page. The article half ported the same
  way netsuite-mcp/ai-hackathon-playbook did — prose to markdown; the
  formula box, 3 pull-quotes, and the tier table preserved as raw HTML in
  `body_md` using their original `.ger-pull`/`.ger-table` classes, rescoped
  under `.oc-body` into a new `_OC_GER_CSS` constant, same
  `_OC_NETSUITE_MCP_CSS`/`_OC_HACKATHON_CSS` treatment (`.article-cta`/
  `.article-pull` themselves needed no porting — sitewide shared classes,
  not page-specific, already available on every page). The calculator
  moved to a brand-new standalone bespoke route,
  `GET /thought-leadership/growth-engine-calculator`
  (`growth_engine_calculator()` in `webapp/app.py`) — markup, CSS, and JS
  extracted byte-for-byte from the retired route, zero logic/input/chart
  change; only new content on that page is the back-link, eyebrow/H1, and
  a short intro blurb. The retired page's "Methodology note" paragraph
  (GTM/R&D GAAP definitions plus a timeline-lookback explanation) stayed
  whole on the calculator page, unmodified, in its original position
  directly below the calculator card — it's calculator-specific (the
  lookback sentence directly references the timeline mechanic) and porting
  it whole was lower-risk than splitting its sentences across both pages.
  `scripts/migrate_growth_engine_ratio_content.py` follows the same
  dry-run/`--apply`/write-then-read-back convention as Phase 4a/4b's
  scripts, setting `body_md` and `date_label` ("June 2026", same
  visual-parity fix Phase 4b used) — `title` needed no fix this time,
  unlike Phase 4b's hackathon row: `"The Growth Engine Ratio"` has no HTML
  entities in it, so there was no double-escape bug to work around.

  The retired page's byline carried two lines beyond what the shared
  template's single `date_label` field can represent — "Published with
  [The F Suite]" (a live link) and a Contributor credit line for Katherine
  Zhang — both preserved verbatim as the first two lines of `body_md`
  itself, the same "extra byline content becomes body_md's leading
  content" precedent Phase 4b used for the hackathon piece's italic
  subtitle line. **One genuinely new piece of content**: where the
  original page's "## Calculate Your Ratio" section held the live
  calculator inline, the ported article now shows a CTA box (reusing the
  sitewide `.article-cta` class — same visual treatment as the page's
  existing F Suite whitepaper CTA) linking out to the new standalone
  calculator page. This CTA copy, and the calculator page's own intro
  blurb, are both new copy — not ported — and were surfaced to Brian for
  review before merge per the standing new-copy/em-dash process; neither
  uses an em dash.

  Verified before either route changed, per this phase's own explicit
  process requirement: with the row updated and the new calculator page
  live at its new URL but the old bespoke `growth_engine_ratio()` route
  still in place, screenshot comparison confirmed visual parity of the
  article portion (desktop and 390×844 mobile), and the new calculator
  page's both modes and both SVG charts were confirmed functioning
  identically to how they worked embedded in the old page, at desktop and
  mobile widths. Only then was the old bespoke route deleted and
  `"growth-engine-ratio"` removed from `_OC_RESERVED_SLUGS` — leaving the
  set empty for the first time since Phase 1: all three original flagship
  pieces (netsuite-mcp, ai-hackathon-playbook, growth-engine-ratio) are now
  served by the catch-all, their bespoke routes retired. Deliberately NOT
  reserved: `"growth-engine-calculator"` itself — that page was never part
  of the `original_content` system and never will be, so there's no slug
  an admin could ever collide with through the form; confirmed explicitly
  rather than assumed, per this phase's own instruction.

- **Original Content, Phase 5 — cleanup: a double-escape fix landed, two
  proposed rollback-file deletions turned out NOT to be safe and were left
  in place, and a Writing-column duplication was investigated (not
  deleted) pending Brian's own action.**
  - **Fixed:** `admin_thought_leadership_edit` had the identical
    double-escape bug Original Content Phase 3 found and fixed in
    `admin_original_content_edit` — an already-`_esc()`'d title passed into
    `_page()`, which escapes its own title argument internally, producing
    `&amp;amp;` for any title containing `&`. Same fix, same pattern: pass
    the raw title straight through. `test_edit_page_title_is_not_double_escaped`
    added to `tests/test_thought_leadership_admin.py`, mirroring Phase 3's
    own test of the same name.
  - **NOT removed, contrary to the initial plan — `_TL_FEATURED_CARDS` and
    `webapp/thought_leadership_data.py` are both still live dependencies,
    not dead rollback references.** The premise that both are "unimported"
    only holds for the *render* path (`_oc_featured_cards_html()`/the
    `original_content` table did replace them there, per Phase 1) — a
    direct search found `_TL_FEATURED_CARDS` is still imported by
    `scripts/migrate_original_content.py` (the seed source for
    `planned_rows()`) and by `tests/test_migrate_original_content.py`, and
    `webapp/thought_leadership_data.py` is still imported by
    `scripts/archive/migrate_thought_leadership.py` (`from
    webapp.thought_leadership_data import SECTIONS`), which
    `tests/test_thought_leadership_admin.py::test_migration_script_moves_32_of_33_entries`
    still runs live against a fresh temp DB. Deleting either file today
    would break real, currently-passing tests, not just an unused rollback
    copy. The blocker is really `scripts/migrate_original_content.py`
    itself: unlike `scripts/archive/migrate_thought_leadership.py` (already
    `git mv`'d once its run was confirmed, the precedent this repo's own
    "archive a one-time script as soon as its run is confirmed" rule sets),
    `scripts/migrate_original_content.py` is still sitting in `scripts/`,
    not `scripts/archive/` — CLAUDE.md's own Original Content Phase 1 entry
    still reads "not yet archived since it hasn't run against production"
    as of this writing. Whether it has in fact already run in production
    (the live site's flagship cards visibly render from `original_content`,
    which only has data if it has) is Brian's call to confirm, not this
    session's to assume — this session has no access to `library.db`
    itself (Railway-volume-only, same limitation noted in earlier Original
    Content phases). **Recommended follow-up, not done here:** once Brian
    confirms the migration ran, archive `scripts/migrate_original_content.py`
    (`git mv` into `scripts/archive/`, matching `migrate_thought_leadership.py`'s
    own precedent) and update/retire `tests/test_migrate_original_content.py`
    accordingly — only then does `_TL_FEATURED_CARDS` (and, separately,
    `webapp/thought_leadership_data.py` once nothing archived needs it
    either) actually become dead code safe to delete under this repo's own
    "no dead data"/no-dead-code discipline.
  - **2026-09 update: confirmed and archived.** Brian confirmed the migration
    ran in production — `original_content` has been in active use for weeks
    (ingestion, MCP Phase 4/5, and the Buddy citation work all built on top of
    it working) — so `scripts/migrate_original_content.py` was `git mv`'d into
    `scripts/archive/migrate_original_content.py`, matching
    `migrate_thought_leadership.py`'s own precedent, and
    `tests/test_migrate_original_content.py`/
    `tests/test_thought_leadership_homepage_teaser.py`'s imports were updated
    to the new path. `_TL_FEATURED_CARDS` and `webapp/thought_leadership_data.py`
    are still live dependencies of the now-archived script and its tests, so
    neither is dead code yet — the "actually become dead code" condition above
    is still unmet, just one script closer.
  - **Investigated, not deleted — the 3 Writing-column `thought_leadership`
    rows duplicating the flagship pieces.** This session has no access to
    the live `library.db` (same Railway-volume-only limitation as above),
    so the exact row `id` values weren't confirmed directly — Brian can
    find them at `/admin/thought-leadership/third-party?type=writing` by matching the
    three titles/URLs in the task description. A full codebase search found
    no sitemap generator, RSS/Atom feed, or other producer for the site
    itself (the only "sitemap" code in this repo is the unrelated Archive
    Queue's historical-backfill sitemap *crawler*, over an entirely
    different table). No test asserts a specific count or title against the
    *production* `thought_leadership` table tied to these 3 rows —
    `test_migration_script_moves_32_of_33_entries`'s `writing: 6` count
    (mentioned above) is a fresh-temp-DB migration test, unrelated to
    production row counts. The only two live readers beyond
    `/admin/thought-leadership/third-party`'s list/edit views are `/thought-leadership`'s
    own Writing column (`list_thought_leadership(type="writing")`) and the
    homepage's "Recent highlights" grid
    (`get_thought_leadership_representative("writing")`) — deleting the 3
    rows is safe for both: the Writing column just shows one fewer
    (duplicate) entry, and if one of the 3 happens to currently be the
    `writing` representative, deletion falls back to the most-recent
    remaining entry per that function's existing fallback rule (not a bug,
    just a different pick). **Confirmed safe for Brian to delete via
    `/admin/thought-leadership/third-party` himself — no code change needed for this
    item.**

- **Original Content, Phase 4c follow-up — two real rendering bugs found
  live post-merge, both invisible text, neither caught by pre-merge
  review.** Brian caught both on mobile Safari; investigation found they
  reproduced at every viewport width, not just mobile — the pre-merge
  screenshot pass that should have caught this had a real gap, not just
  bad luck: it checked the `<thead><tr>`'s own computed `background-color`
  (which genuinely was navy, since the inline style was never removed) but
  never checked what actually paints on top of it, the same "verify what's
  rendered, not a property in isolation" lesson this file's own testing-
  standard section already documents elsewhere.
  1. **Tier table header, near-invisible.** The retired page's
     `<thead><tr style="background:var(--navy);">` inline style survived
     the port verbatim, but `_OC_ARTICLE_CSS`'s generic
     `.oc-body th{background:var(--accent-light)}` rule (written for
     markdown-generated tables, which have no per-row inline style to
     preserve) painted over it — not a specificity loss, a CSS table
     BACKGROUND PAINTING LAYER fact (CSS 2.1 §17.5.1): a `<th>`'s own
     background always paints above its parent `<tr>`'s, regardless of
     which rule has higher specificity. White header text landed on a
     near-white cell background — read as "near-invisible, with an
     unexplained gap of white space above it," which turned out to be one
     bug, not two, exactly as Brian's report guessed it might be. The
     original bespoke page never hit this, since it had no competing
     `.oc-body th` rule to paint over it — this could only surface once
     the table moved under the shared template. Fixed the same way
     `.ns-table th` (Phase 4a) already solved this for its own table:
     `.oc-body .ger-table th{background:var(--navy);color:#fff;}`, giving
     the cell itself the right color directly instead of relying on the
     row showing through underneath it.
  2. **"Download the full guide" CTA button, invisible text.** `.btn`'s own
     `color:#fff` (one class, 0-1-0 specificity) lost to
     `.oc-body a{color:var(--navy)}` (one class + one tag, 0-1-1 —
     genuinely higher specificity, this one **is** a real specificity
     loss) — navy text on a navy background. This was the first `body_md`
     content anywhere to use the sitewide `.btn` button inside an
     admin-authored piece, so the interaction had never been exercised
     before. Fixed generally, in `_OC_ARTICLE_CSS` itself rather than
     narrowly in `_OC_GER_CSS`, since any future piece using this same
     button would hit the identical bug:
     `.oc-body .btn{color:#fff;}` — two classes beats one class + one tag
     by CSS's class-count-first specificity comparison.

  Both fixed, then verified with real mobile-Safari-viewport (390×844)
  element-level screenshots of exactly the two elements Brian flagged —
  not full-page captures, not just described — confirmed via
  `getComputedStyle()` before AND after (before: `th` background
  `rgb(238,241,247)`/color white; button background navy/color navy;
  after: `th` background/color navy/white; button background navy/color
  white) alongside the visual screenshots, plus a new regression test
  (`test_growth_engine_ratio_table_header_and_cta_button_styled_correctly`
  in `tests/test_original_content_article.py`) asserting both fixed CSS
  rules are present in the rendered response.

- **Original Content, Phase 4c second follow-up — an unintentional white
  gap above/below the tier table, same root-cause shape as the first
  follow-up (a generic markdown-table CSS rule bleeding into a raw-HTML
  table it wasn't written for).** Brian caught this live too, after the
  header/CTA fix landed. `.ger-table-wrap`'s own inline style
  (`background:#fff;border:1px solid var(--line);border-radius:12px` —
  copied verbatim from the retired bespoke page) bounds a white bordered
  card with zero padding, meant to fit the tier table flush against its
  edges. But `_OC_ARTICLE_CSS`'s generic `.oc-body table{margin:1.5em 0}`
  rule (written for markdown-generated tables, which have no wrapper of
  their own to own that spacing) still applied to `.ger-table`, since its
  own CSS never reset `margin`. Measured live before fixing, not guessed
  at: the table sat exactly 21px inset from the wrapper's border on all
  four sides (`getComputedStyle(table).margin` → `21px 0px`,
  `bounding_box()` diff between the wrapper and the table confirming a
  21px gap top and bottom) — reading as an unintentional blank box, not a
  design choice, exactly as Brian described it. `.ns-table`'s own wrapper
  never showed this same bug because it has no background/border of its
  own to reveal the identical inherited margin against — the 21px gap is
  present there too, just invisible against the page background. Fixed
  with `.oc-body .ger-table{margin:0;}`, so the wrapper (which already
  carries the correct outer spacing via its own inline `margin:0 0 32px`)
  is the single source of the box's outer edge — the same "give the
  raw-HTML element its own explicit reset instead of letting a generic
  markdown-table rule reach it" pattern the header/CTA fixes both used.
  Verified with a live `bounding_box()` measurement before/after (gap
  21px → ~1px, the residual being border-width rounding) and a real
  desktop screenshot showing the header sitting flush against the
  wrapper's rounded top corners, plus a new regression test
  (`test_growth_engine_ratio_table_wrap_has_no_visible_gap`) asserting the
  fixed CSS rule renders in the response.

- **Original Content admin — width fix + Preview link.** `/admin/thought-leadership/original/{id}/edit`
  (and `/admin/thought-leadership/original/new`) rendered in `.page-form` (640px) — the
  same tier used for one-column public-facing forms like `/contact` — even
  though every other admin data-management page (`/admin/tools/software`,
  `/admin/thought-leadership/original`'s own list view) uses the wider `.page-admin`
  (1500px). Investigated first: the list page itself was already correctly on
  `.page-admin` — only the add/edit form was narrow, contrary to this task's
  initial premise that both pages needed the fix. Switched the form page to
  `.page-admin` too, with the `<form>` element itself capped at
  `max-width:900px` so single-line inputs (Title, Slug, Teaser) don't stretch
  to the full 1500px container — the width discipline here is "match the
  page shell" rather than "let text inputs run the full container." With that
  headroom, the two separate 2-column field grids (Tag label/Link label, then
  Date label/Display order) merged into a single
  `repeat(auto-fit,minmax(190px,1fr))` row (not a hardcoded `repeat(4,1fr)`,
  per the standing CSS-Grid-blowout lesson — Phase P above) — all four fields
  render on one line at desktop width and collapse gracefully on narrower
  viewports. A "Preview →" link/button was added next to Save/Cancel on the
  edit form only (never the Add form — `_oc_form_page`'s new `show_preview`
  parameter defaults `False`), linking to `/thought-leadership/{slug}` (the
  row's currently-persisted slug, not an unsaved edit) with `target="_blank"`
  — no new rendering logic needed, since Phase 2's `GET /thought-leadership/{slug}`
  route already serves a `status='draft'` row at its canonical URL for an
  active admin session. When `body_md` is blank (a card-metadata-only row,
  which 404s at that route per Phase 2's own guard), the link renders instead
  as a disabled, non-clickable `<span>` with a `title` tooltip explaining why
  — same `.tool-intro-btn:disabled` muted-color/`cursor:not-allowed`
  convention used elsewhere, chosen over hiding the affordance entirely so an
  admin editing a metadata-only row still sees the option exists and why it's
  off. Verified live: 4-field row confirmed on one line via matching
  `getBoundingClientRect()` y-coordinates; Preview link's `href`/`target`
  confirmed correct for a row with body content; the disabled state's `title`
  text confirmed correct for a row without.

- **Original content — tag taxonomy, semantic colors, derived link label
  (2026-09).** `tag_label` was free text and the flagship-card eyebrow color
  came from a positional cycle (`_OC_TAG_COLORS`, cycling coral-deep/
  seafoam-deep/navy-light by card index — a separate, unrelated cycle from
  `_CARD_ICON_STYLES`, confirmed by direct inspection before changing
  anything, so this fix never touched the Toolbox/Software/Communities card
  grids' own icon-color cycle) — two cards tagged "Setup Guide" could render
  in different colors purely by slot, and nothing kept `tag_label`/`teaser`/
  `link_label` pointed at the same idea: `chart-of-accounts` shipped tagged
  "Setup Guide" with a "Read the playbook" link and a teaser opening "A
  playbook for…". Fixed with a closed three-value taxonomy
  (`_OC_TAG_INFO` in `webapp/app.py` — the one dict every reader, the form
  dropdown, the card color, and the derived link label, all resolve
  through, so adding a fourth tag later is a one-line change to it):

  | Tag | Meaning | Color |
  |---|---|---|
  | Guide | Instruction manuals, reference material | `--navy` |
  | Playbook | Steps for how to do something, an action | `--seafoam-deep` |
  | Framework | A model or metric for thinking about something | `--coral-deep` |

  Semantic, not decorative — blue is something that stays, green is
  something you can run, coral is meant to jump. All three are the
  text-capable ramp shades (BRAND.md §2.3 bans plain `--coral`/`--seafoam`
  text under 18px, and this is small uppercase eyebrow text) — though
  Guide is a substitution, not a carry-forward: the old positional cycle's
  third shade was `--navy-light` (5.9:1), darker `--navy` (12.1:1) binds to
  Guide now. Both are existing tokens, so still no new palette entry and no
  contrast regression. `--seafoam-deep` (4.7:1) and `--coral-deep` (4.9:1),
  now carrying Playbook and Framework, are the two tightest contrast
  ratios in the whole palette — see BRAND.md §2.3's guardrail against
  lightening either one for an unrelated reason. The admin form's free-text Tag label input became a
  `<select>` with exactly these three options and a leading disabled
  placeholder (`<option value="" disabled selected>`) — never submittable,
  so a fresh Add form can't silently default to whichever tag sorts first;
  `required` still blocks a submission that leaves it selected, and the
  server independently rejects any `tag_label` outside the three
  (`v["tag_label"] not in _OC_TAGS`) regardless of what the form allows.
  **Link label is no longer a form field at all** — the input was replaced
  with static, non-editable text (`Set automatically from the tag.`) and
  `_oc_values_from_form` derives `link_label` from `tag_label` server-side
  on every save, ignoring whatever a raw POST might supply under that key
  — the two fields can never desync again the way chart-of-accounts did.
  `Library.add_original_content`/`update_original_content` are unchanged
  (still take `tag_label`/`link_label` as plain strings; only the caller
  changed) — no schema migration, since `tag_label`/`link_label` were
  already TEXT columns and the closed set is enforced at the form/route
  layer, not a DB constraint. `_TL_FEATURED_CARDS` (the frozen, unimported
  rollback-reference tuple `scripts/archive/migrate_original_content.py`
  seeds from) is deliberately untouched — it's historical record of what
  was migrated once, not live-rendering content, same precedent as every
  other frozen-tuple entry in this doc. **`scripts/normalize_original_content_tags.py`**
  (preview/`--apply`/write-then-read-back, not yet run against production
  or archived) moves any row still carrying a legacy free-text tag (e.g.
  "Setup Guide") onto the closed set. **Tags are Brian's editorial call**:
  a row that already carries one of the three valid tags is never retagged,
  whether the script set it or Brian did by hand (`chart-of-accounts` is
  Live as Guide, set by hand, and stays Guide). The only thing the script
  may change on a valid-tag row is a `link_label` out of step with its tag,
  since that label is derived, not chosen. A legacy-tag row with a slug the
  script has no mapping for stops the run with no writes. (Corrected
  2026-09: the first version mapped `chart-of-accounts` to Playbook
  unconditionally, so a run after the hand edit would have overwritten it;
  reproduced against a copy of the live tags before fixing. As of
  2026-09-23 all four live rows already carry valid tags, so a production
  preview should report nothing to do.) See
  BRAND.md §2.3 for the full color-semantics write-up and
  `tests/test_original_content_admin.py`'s tag-taxonomy section for the
  regression coverage (invalid-tag rejection, each tag's derived link
  label, the dropdown's no-default-selected state, a direct POST
  attempting to set `link_label` being ignored).

- **Original content — every live piece must have its own social share
  card, enforced at publish (2026-09).** The OG-card system (see the Social
  share cards bullet above) always falls back to `default.png` for a piece
  with no card of its own — safe as a rendering default, but nothing
  stopped a Live `original_content` piece from actually shipping without
  one, which is exactly the gap this closes. `_oc_publish_gate_error(status,
  slug)` (`webapp/app.py`) is the single function both
  `admin_original_content_new_submit` and `admin_original_content_edit_submit`
  call — one shared validation path, not duplicated per route, precisely
  because this feature exists to stop the kind of drift a second copy of
  the check would risk. It rejects a save that would leave `status="live"`
  with no matching `webapp/static/og/<slug>.png` (checked via the same
  `_og_image_slugs()` cache the OG-card system already uses), with an error
  naming the exact expected path and the only way through: "This piece
  needs its own share card before it can go live. Commit a 1200×630 PNG at
  `static/og/<slug>.png`, wait for the deploy, then set this to Live. Save
  as Draft in the meantime." **No override exists** — Draft is the sole
  escape valve. The same check fires on an edit that changes a Live piece's
  slug to one with no card, not just on first publish.

  Three supporting pieces, all reading the identical `_og_image_slugs()`
  lookup so nothing can disagree with the block itself: (1)
  `_oc_card_status_html(slug)` renders a note directly under the form's
  Status field — "Share card found at static/og/&lt;slug&gt;.png." or "No
  share card yet. Commit a 1200×630 PNG at static/og/&lt;slug&gt;.png
  before setting this to Live." — computed server-side from the form's
  current slug on every page load, deliberately not JS: a slug can change
  before save, and a JS guess reading a stale value would be worse than a
  render-time-accurate one. (2) The admin list at
  `/admin/thought-leadership/original` badges a Live row with no card —
  investigated for an existing precedent first and found
  `_review_status_pill_html` (Software/Communities' review tracking) is a
  heavier, unrelated mechanism, so it wasn't reused; instead this follows
  `/admin/system/page-index`'s own plain muted-text "⚠ No tier assigned"
  treatment — a small `--alert`-colored "No share card" line, no colored
  pill, matching this codebase's standing rule that admin surfaces stay
  undecorated for this class of flag. (3)
  `webapp.checks.og_card_missing_problems()` is a new `/admin/checks` row
  ("Every live piece has a share card"), the same shape as
  `og_url_threading_problems()`/`original_content_mirror_problems()`, so a
  card deleted after publish is still caught mechanically, not only at the
  moment of the original save. `GET /static/og/{filename}`'s own
  `default.png` fallback is completely unchanged — this feature only ever
  decides whether a *save* is allowed, never how a card is served. See
  ARCHITECTURE.md's matching entry and `tests/test_original_content_admin.py`'s
  publish-gate section (12 tests) plus `tests/test_social_share_cards.py`'s
  `og_card_missing_problems()` section for the regression coverage.

- **Library/Toolbox restructure, Phase 4 — FP&A Buddy's Sources/Depth controls
  compact into two columns, and Depth stops being a card stack.** On
  `/tools/fpa-buddy`, Sources (a multi-select row of `.ask-tag` buttons) sat
  above a vertical stack of three tall `.ask-tier` cards, each carrying
  persistent subtext (archive count, web-search count, token estimate) plus a
  "Recommended" badge on Standard. That's a lot of vertical space for a
  one-of-three choice, so both groups now sit side by side in an
  `.ask-controls` two-column grid (Sources left, Depth right), and Depth's
  tiers are rebuilt from the *same* `.ask-tag` component Sources uses —
  identical padding/radius/border/font/active-fill, verified by comparing
  computed styles in a live browser, not by reading the CSS. The whole
  `.ask-tier*` CSS block and the badge are gone; the per-tier detail moved
  into a `title` hover tooltip (Standard's also says "Recommended"), which is
  deliberately native `title` rather than a custom CSS tooltip — a positioned
  tooltip anchored to a wrapping button is exactly the kind of thing that
  overflows a narrow viewport, and the standing mobile-verification rule
  exists because of that class of bug. Default selection is unchanged
  (Standard for admin, Quick otherwise). **The one non-obvious hazard the
  shared component introduces**, and the reason `.ask-tag[data-source]` now
  appears in two places: Depth's buttons carry `.ask-tag.active` exactly like
  Sources', so `doAsk()`'s original unscoped `'.ask-tag.active'` query would
  have posted the selected tier to `/ask` as a fourth, null source. Confirmed
  live rather than reasoned about — an unscoped query in the running page
  returns `['library','feed','web',null]`, the scoped one returns the three
  real sources. Anything added later that reads the selected sources has to
  scope the same way. Section label went from "How deep should I go?" to
  "Depth" so the two column headers read as a matched pair. Below 640px the
  grid collapses to one column and the groups stack; verified with real
  headless-browser sessions at 1280 (side by side), 844x390 landscape (side
  by side), and 390x844 portrait (stacked, no horizontal overflow), tapping
  rather than clicking on the touch contexts.

- **Library/Toolbox restructure, Phase 3 — CFO Toolbox landing page and the homepage
  both move to a 4-tile 2x2 grid, plus a 5th admin-only tile.** `/tools`'s old 3-card
  `repeat(3,1fr)` list (Software, Benchmarking, Communities) is replaced by a 2x2 grid
  of Software/Benchmarking/Communities/FP&A Buddy tiles, sourced from one shared
  `_TOOLBOX_TILES` tuple in `webapp/app.py` — the FP&A Buddy tile is the first link
  into `/tools/fpa-buddy` from the Toolbox itself since it moved there in Phase 2. The
  same tuple drives a new, more compact "Everything in the toolbox" teaser section on
  the homepage (mini icon + heading + one-liner, no per-tile links — one "See the full
  toolbox" link out), which replaces the homepage's old standalone "CFO Toolbox" card
  in the top card row outright (redundant with the new teaser directly below it); with
  only the Thought Leadership card left in that row, `.home-cards` dropped its 2-column
  breakpoint and caps at a single-card width instead of stretching edge-to-edge. FP&A
  Buddy's tile reuses the existing `_ICON_BRAIN` glyph (already drawn for the
  homepage's Thought Leadership card) rather than drawing a new one — the build brief
  assumed no brain icon existed yet, but this one already matches the flat, two-tone,
  stroke-width-2 look the design spec called for, so reusing it was a straight
  simplification, not a spec deviation. A 5th tile — seafoam-bordered (vs. the other
  four's navy-wash border), linking to `/admin/library`, using a newly-drawn
  `_ICON_BOOK` glyph (nothing existing fit "Library") — appears only on `/tools`, and
  only when `_is_authed(request)`: it's built conditionally in Python, not hidden by
  CSS, so it's absent from the response HTML entirely for a signed-out or non-admin
  visitor. The homepage teaser never includes it under any auth state — it's built from
  the public-only `_TOOLBOX_TILES` tuple, which the 5th tile was deliberately kept out
  of. **Design-file caveat:** the build brief pointed at a Claude Design project
  (`Toolbox Illustration Concepts.dc.html`, option 2a) as the source of truth for exact
  markup/spacing over the written spec, but the design MCP requires an interactive
  `/design-login` unavailable in this headless session, and a direct fetch of the
  claude.ai/design URL 403'd — the design file itself was never actually checked
  against. Implemented straight off the written spec instead (which was pixel-specific:
  exact hex colors, badge/icon sizes, padding), flagged here rather than silently
  assumed equivalent — worth a visual diff against the design file next time someone
  can reach it.
- **Phase 3 addendum — a matching homepage Thought Leadership teaser, a
  `featured_home` pin field, and a real schema change (unlike Phase 3 itself).**
  Removing the redundant "CFO Toolbox" homepage card left the top card row with
  just one lone card (Thought Leadership) sitting above a full teaser section for
  Toolbox alone — a visible asymmetry once the Phase 3 preview was actually
  looked at. Fixed by giving Thought Leadership the identical compact-teaser
  treatment: same eyebrow style, same panel, same mini-tile grid — the CSS
  classes backing both sections were renamed from `.home-toolbox-*` to the
  shared `.home-teaser*` to make that a literal shared component rather than
  two near-duplicates. Only the content between the heading and the panel
  differs: Toolbox keeps its one-line subline, while Thought Leadership gets a
  four-item bulleted list (generous line-height/spacing, deliberately not
  compressed) since its themes don't compress to a single sentence the way
  Toolbox's four nouns do. Tile selection needed a real decision, not just
  most-recent-3: `thought_leadership.featured_home` is a new column — a
  "Feature on homepage" checkbox on both the add and edit admin
  forms — with `Library.list_thought_leadership_for_home` picking pinned
  entries first (reusing the exact same `_TL_ORDER_SQL` recency rule
  `/thought-leadership`'s own columns already sort by, not a reinvented one),
  backfilling with the most-recent unpinned entries until 3 tiles are filled,
  and truncating rather than overflowing if more than 3 are pinned at once.
  Defaults to 0 for every existing row — no retroactive pinning, same
  precedent as every other needs-verification-style column's migration. Each
  tile reuses the exact per-type icon already drawn for the `/thought-leadership`
  columns (`_TL_COLUMN_ICONS`, via a new `_TL_TYPE_ICON` lookup keyed off
  `_TL_TYPES`' existing order) rather than drawing anything new, and links
  straight to the piece using the same venue/date_label metadata the full page
  already shows. **Also fixed while in the same template:** the "Speaking &
  Events" section header was rendering literally as "Speaking &amp;amp; Events"
  — the section-title string was hardcoded as the already-HTML-escaped
  `"Speaking &amp; Events"` in Python, then passed through `_esc()` a second
  time by `column()`, which escapes every title it's given. Fixed by storing
  the plain, unescaped `"Speaking & Events"` at the source (matching
  `_TL_TYPES`' own label, which was never double-escaped) and letting `_esc()`
  do its one intended escaping pass — checked the rest of `/thought-leadership`
  for the same pattern (any hardcoded `&amp;`-containing string later run
  through `_esc()`) and found no other instance. The four teaser list items'
  copy was supplied verbatim by Brian in the build brief for this addendum,
  em dashes included — flagging that per the em-dash policy above, but noting
  the copy was pre-approved by the person the policy asks it be flagged to,
  not independently written and shipped.
- **Homepage Restructure — the Thought Leadership addendum's recency-pin panel is
  gone; the homepage gets one consolidated Thought Leadership section instead, and
  `featured_home` is repurposed rather than left orphaned.** The redesign replaced
  the addendum's "3 tiles, pinned-then-recency-backfilled" panel outright with a
  fixed, curated section: the same eyebrow/heading, an intro line (reusing
  `/thought-leadership`'s own intro copy verbatim), the **3 flagship pieces**
  (Growth Engine Ratio, Sail Don't Row, Connecting Claude to NetSuite) in their
  existing card treatment, a **4-column type breakdown** (Writing/Speaking &amp;
  Events/Podcasts/Press, one representative entry each), and the 4 existing
  bullets — replacing both the old standalone homepage "Thought Leadership" card
  and the addendum's separate lower teaser, which are both gone now (not two
  sections stacked). Investigated first, per the build brief's explicit ask,
  rather than guessed at: `featured_home` (the addendum's "pin to homepage
  teaser" checkbox) had no role in a hardcoded-flagship-pieces design, so its
  recency-backfill purpose was genuinely dead — but the checkbox mechanism itself
  was still useful, just for a different question ("which entry represents this
  type?"), so it's **repurposed, not removed**: no new column, no migration,
  same admin form location, only the meaning and helper copy changed. Resolution
  rule (`Library.get_thought_leadership_representative`, replacing
  `list_thought_leadership_for_home`): the most recently updated `featured_home=1`
  entry of that type wins if more than one is checked (`updated_at DESC`); if
  none is checked, falls back to the most recent entry by the existing
  `_TL_ORDER_SQL` ordering, so an admin who hasn't curated a type yet still sees
  something instead of a broken/empty column; a type with zero entries at all
  renders no column, same empty-collapse convention `/thought-leadership`'s own
  `column()` already uses. The flagship-card markup/CSS and the per-type-column
  markup/CSS are now shared module-level constants (`_TL_FEATURED_CARDS`/
  `_tl_fcard`/`_TL_SHARED_CSS`, `_tl_type_column`) used by both
  `/thought-leadership` and the homepage, rather than two copies that could
  drift — `/thought-leadership` itself is otherwise unchanged (still full capped
  lists with "Show all", not single representatives). **Hero polish:** avatar
  sized to 176px (80% of the prior 220px), the headline capped to a 520px
  max-width so it wraps more deliberately instead of stretching the full copy
  column, and the status box's two copy blocks now share one style call (the
  expanded-copy block previously fell through to the sitewide default
  `p{{margin:0 0 16px}}` while the teaser block above it used an explicit
  `margin:0 0 8px`, so the two paragraph groups inside one box read with two
  different rhythms). **Toolbox teaser sticker regression, caught and fixed
  here:** the "🚧 building" sticker was on the old homepage "CFO Toolbox" card
  before Phase 3, but never carried over when Phase 3 rebuilt that card into
  the current full-width Toolbox teaser section — it had been silently missing
  since that phase shipped until this build brief's acceptance criteria called
  for verifying it explicitly. Re-added to the teaser section's corner (same
  `_sticker()` component, same rotate/positioning convention as its other
  sitewide uses). **Design-file caveat (same limitation as Phase 3, still
  unresolved):** the build brief pointed at a second Claude Design project
  (`Homepage Restructure.dc.html`, importing `image-slot.js`/`support.js`) as
  the source of truth for the hero/Toolbox-teaser-placement/section layout —
  `/design-login` is still unavailable in this headless session and the direct
  claude.ai/design URL still 403s, so this file was never actually checked
  against either. In particular, the Toolbox teaser's placement (moved into the
  hero's right column vs. kept full-width and built out further — both
  explicitly named as live possibilities in the build brief) was decided by
  judgment, not verified: kept full-width, since restructuring the hero grid
  to absorb it is a materially bigger, riskier change to guess at blind than
  polishing the section in place. Flagged for a visual diff against the design
  file, same as Phase 3's still-open caveat above.
- **Homepage Restructure, design-fidelity pass — the actual design file
  (previously unreachable) supersedes both judgment calls above; the Toolbox
  teaser really does move into the hero's right column, and the "Recent
  highlights" grid replaces the 4-column breakdown outright.** The headless
  `/design-login` limitation flagged in the two bullets above turned out to
  be a session limitation, not a permanent one — Brian supplied the design
  as a self-contained bundled HTML export instead (a runtime-unpacking
  artifact, not plain static markup; read by rendering it in the
  pre-installed headless Chromium via Playwright and diffing the resulting
  DOM, since the bundler's JS reconstructs the real page client-side). That
  changed several calls made blind in the two bullets above:
  - **Layout is a real two-column grid**, not a stack of full-width
    sections: a `1fr / ~360px` grid, left column holding the hero copy and
    the entire consolidated Thought Leadership section, right column (the
    sidebar) holding the photo/status-box block, the Toolbox panel, and the
    Reader-access placeholder — collapsing to a single stacked column below
    900px, the same convention every other responsive section on this page
    already uses.
  - **The Toolbox panel moved into that right column**, resolving the
    previous bullet's judgment call the other way — and its whole
    eyebrow/heading/subline/tile-list/link now lives inside *one* white
    bordered card (previously two nested layers: a bare eyebrow/heading
    above a separately-bordered tile panel).
  - **The Thought Leadership section's "4-column breakdown" is actually a
    "Recent highlights" 2-column grid** (`_tl_recent_highlight_item`,
    replacing `_tl_type_column`) — visually and structurally different from
    `/thought-leadership`'s own `.tl-cols` per-type columns (this reuses
    `Library.get_thought_leadership_representative`'s existing selection
    logic unchanged, just a different renderer for the result): a divider,
    a plain "Recent highlights" label, then one tile per type with an
    icon+type-label row, linked title, venue/date metadata, **and the
    entry's own description** (the earlier `_tl_type_column` didn't surface
    description at all). Order also changed to match the file exactly:
    eyebrow → heading → intro → bullets → flagship cards → divider +
    Recent Highlights → "See all" link (bullets used to come after the
    breakdown, not before the flagship cards).
  - **The bullet list is a manual flex layout with a "•" glyph**
    (`.home-tl-bullet*`), not a native `<ul><li>` — matching the file's own
    treatment (14px item gap, 15.5px text) rather than the more generous
    spacing an earlier round guessed at without the file to check against.
  - **The 3 flagship cards' content stays shared between the homepage and
    `/thought-leadership`'s own featured row — a short-lived un-sharing
    detour, reverted per Brian's explicit call.** The design file shows the
    homepage's cards with shorter copy, a single consistent seafoam-deep tag
    color (vs. `/thought-leadership`'s 3-color cycle), and a "Read the
    piece →" CTA. A first pass at this design-fidelity round took that at
    face value and split `_TL_FEATURED_CARDS` into two diverged tuples (one
    per page) — but the single-shared-tuple design was deliberate from the
    original build specifically so the two surfaces *can't* drift apart in
    content, and that intent still holds: the design file's placeholder copy
    doesn't override it. Reverted back to one `_TL_FEATURED_CARDS`, rendered
    on both pages through the same `_tl_fcard()`/`.tl-card` markup — title,
    description, and link label are now guaranteed identical (test-enforced:
    `test_flagship_cards_content_shared_between_homepage_and_thought_leadership`
    renders every card via `_tl_fcard()` and asserts the exact markup appears
    on both pages). Card *sizing* is still free to differ per page — each
    page's own `.tl-featured` grid track width naturally narrows the cards
    on the homepage's tighter column vs. `/thought-leadership`'s full-width
    row — only the content itself is pinned.
    **Sail Don't Row correction (Brian's explicit call before building):**
    the design file's copy for that card describes the arcade game itself
    ("Interactive" tag, "a game about strategic leverage", "Play it →") —
    wrong; that card is meant to represent the AI hackathon playbook
    article. `_TL_FEATURED_CARDS`' entry for it already carries the real
    "Playbook" tag, description, title, and "Read the playbook →" link —
    that was true before this round and stays true now that both pages
    share it again — instead of the design file's placeholder copy.
  - **Avatar is 200px** (not the ~176px estimated in the earlier hero-polish
    bullet) with a repositioned, rotated status box overlapping it — closer
    to the file's own 265px-at-1720px-wide layout, scaled down slightly to
    fit this site's narrower right-column width.
  - **The "Status:" label switches to `var(--font-wordmark)`** (Permanent
    Marker, already loaded sitewide for the nav logo — no new font) instead
    of `var(--font-sticker)` (Caveat), matching the file exactly; the "hi,
    I'm Brian" sticker keeps its existing 🤙 emoji rather than the file's
    plain-text placeholder, since that emoji is established site copy this
    file wasn't asking to change.
  - **The "🚧 building" sticker's absence from the design file turned out to
    be an export omission, not an intentional removal — confirmed by Brian
    after this was flagged, and restored.** The design file the panel was
    rebuilt from didn't show the sticker, so the first pass at this round
    dropped it, reversing the "must not drop it" regression guard the two
    prior bullets above established — and said so explicitly rather than
    silently. That flag is what surfaced the omission: Brian confirmed the
    sticker was the one thing missing from the file itself, not a deliberate
    design change, so it's back on the rebuilt Toolbox panel and
    `test_toolbox_panel_present_and_matches_design` asserts its *presence*
    again. Kept here as the concrete case for why flagging a reversal
    explicitly (instead of just making the call and moving on) is worth the
    friction — it's what let a real omission get caught and corrected in one
    round-trip instead of shipping silently wrong.
  - **New: an admin-only "Reader access" placeholder box** in the sidebar,
    below the Toolbox panel — seafoam background, navy border, the reused
    `_ICON_NEWSPAPER` glyph, static copy ("Shown here only when logged in as
    admin. Links into the Reader—build pending."), gated on `_is_authed`
    the same way the `/tools` 5th tile is (built conditionally in Python,
    absent from the HTML entirely for non-admins, not CSS-hidden). No
    actual link yet — it's explicitly a placeholder per the file, and stays
    one; wiring it to `/read` is future work, not this round's.
  - **Expected, not a bug:** the same Thought Leadership entry can appear
    in both the flagship cards and "Recent highlights" (e.g. if Growth
    Engine Ratio is also Writing's `get_thought_leadership_representative`
    pick) — the two sections pull from different, independent data sources
    (a hardcoded tuple vs. a per-type DB query) with no dedup between them,
    same as the file shows no such guard either.
- **Homepage Restructure — mobile-only DOM reorder: the photo/status card
  moves between the hero and Thought Leadership sections, not to the very
  bottom with the rest of the sidebar.** A follow-up request from Brian after
  seeing the mobile render: on narrow viewports the photo/status card should
  sit right after the hero copy (ending "...CFO Toolbox and Digital Library I
  built along the way.") and before the "Thought Leadership / What I write
  about" section — not stacked at the bottom alongside the Toolbox panel and
  Reader-access placeholder, which is where plain DOM order had put the whole
  former `.home-side` sidebar bundle. Fixed by splitting the homepage's single
  two-item grid (`.home-hero-copy`+`.home-tl-section` / `.home-side`) into
  four independent top-level grid children — `.home-hero-block`,
  `.home-photo-wrap`, `.home-tl-section`, `.home-sidebar-rest` (Toolbox panel
  + Reader-access box) — in that exact DOM order. Below the 900px breakpoint
  `.home-grid` is a plain `flex-direction:column` stack with no per-item
  overrides, so DOM order *is* the rendered mobile order for free. At 900px+
  the existing two-column desktop layout is restored via explicit
  `grid-column`/`grid-row` placement on each of the four children (hero at
  column 1 row 1, Thought Leadership at column 1 row 2, photo card at column
  2 row 1, sidebar-rest at column 2 row 2) — completely independent of DOM
  order, so the desktop design-file layout is unaffected by this change.
  `test_mobile_dom_order_photo_card_between_hero_and_thought_leadership`
  asserts the DOM order directly.
- **Homepage Restructure — a parallel session's stray PR landed a second,
  independent homepage rebuild on `main` mid-flight; reconciled by keeping
  this branch's fuller redesign and adopting one piece from the other.**
  While this branch's design-fidelity work was in progress, a different
  session (working off a different, narrower design export —
  `CFO_Navigator_Feed_Redesign`, not `Homepage_Restructure_Standalone.html`)
  independently rebuilt the same `homepage()` function and merged straight
  to `main` as its own PR — a duplicate/stray session, confirmed by Brian,
  not the intended direction. Its version never touched the Thought
  Leadership section at all (the old standalone card was still live, not
  duplicated with this branch's consolidated section — checked directly
  against the then-live homepage before reconciling, since two rewrites of
  the same page landing separately raised the real possibility of visibly
  broken output), moved the Toolbox teaser into the right column as a
  narrower vertical list, resized the avatar to 265px, and — the one piece
  worth keeping — wired the "Reader access" box to a real `/read` link
  instead of the placeholder text it launched with, since the Phase 5
  Reader merge had landed by the time that session built it. Reconciled via
  a real merge (lower-risk than a from-scratch rebuild given it was really
  only two conflicting functions, `_avatar()` and `homepage()`): kept this
  branch's full consolidated Thought Leadership section, flagship-card
  sharing, Sail Don't Row correction, and mobile-order/breakpoint work;
  adopted the real `/read` link and `_avatar()`'s new `border_width` param
  (set to 4 here, matching the design file's own 4px ring — a discrepancy
  this branch hadn't caught before the param existed to fix it); kept this
  branch's 200px avatar size and narrower Toolbox-panel-as-single-white-card
  treatment rather than the other session's 265px/vertical-list version,
  since neither was specifically requested to be adopted. The other
  session's now-superseded `_toolbox_row`/`_ICON_FPA_BUDDY`/`toolbox_card`/
  `reader_access_card` helpers were dead code after the merge and removed
  outright rather than left unused. Same brand-check false positive as
  before recurred here too (a `PR #320`/`#321` reference this time, not
  `#318`) — reworded away from the `#NNN` pattern again rather than adding
  a general suppression, consistent with the earlier fix's approach.
- **Homepage Restructure — a direct post-merge cross-check against the
  design file (not memory of it) caught real drift the reconciliation merge
  introduced, beyond the two deliberately-approved deviations.** After
  reconciling with the stray parallel-session PR above, Brian asked for the
  final markup to be checked against the actual design file again, the same
  way the Sail Don't Row and shared-card issues were originally caught —
  re-rendered the bundled export fresh (same Playwright approach as the
  original build) rather than relying on the first pass's notes. Found and
  fixed genuine, non-shared-CSS drift: the Toolbox panel's tile-row icon
  badges were reusing `_card_icon()`, whose `margin-bottom:14px` (meant for
  a badge stacked *above* a title) and `1.8` stroke-width don't match the
  design's own badges for this icon-*beside*-text row — replaced with a
  dedicated `.home-toolbox-icon` class built to the file's exact spec (34px,
  radius 8, stroke-width 2, 16px icon, no margin). Also fixed three
  typography/spacing values that had drifted from the file with no
  justification for the drift: the "What I write about" heading (28px ->
  30px), the bullet list's bottom margin (28px -> 37px), and the "Recent
  highlights" grid's gap (`24px` uniform -> `32px 40px` row/column, per the
  file), plus restructuring the "Recent highlights" label from a grid item
  sharing the items' own gap into its own element with the file's explicit
  20px margin-bottom and 28px divider padding-top. **Explicitly NOT
  "fixed" back to the file, and confirmed as deliberate on this pass, not
  overlooked:** the flagship-card CSS (`.tl-card` padding, `.tl-featured`
  gap) and the "Recent highlights" label/type-label color both differ from
  the file's literal values — the former because that CSS is intentionally
  shared with `/thought-leadership`'s own pre-existing card treatment (see
  the flagship-cards-shared bullet above; changing it to match the design
  file would also change the live `/thought-leadership` page, which Brian's
  instruction was specifically protecting), the latter because the file's
  `rgb(138,143,153)` isn't an established site token and `--muted`
  (`#6F6A60`) already covers this role elsewhere on the page — introducing
  a new off-brand gray to chase an exact pixel match would trip
  `brand_check.py`'s own off-palette check. **The "🚧 building" sticker
  stays, per explicit standing instruction, even though the design file
  still doesn't show it** — this is the second time this exact point has
  come up (first as the Homepage Restructure regression bullet above), and
  the file's omission is confirmed to be a known gap in the export, not a
  design decision to match.
- **Homepage Restructure — Brian re-exported the design file with the
  building sticker added, and its position/rotation differs from the
  earlier guess.** A follow-up upload of `Homepage_Restructure_Standalone.html`
  (re-rendered fresh via Playwright, same as every prior cross-check round)
  turned out to be an updated export, not a duplicate: the Toolbox panel now
  genuinely includes a sticker in the file itself — confirming the earlier
  "known gap in the export" bullet above was correct, and Brian has since
  closed that gap at the source rather than leaving it a standing exception.
  Position/rotation differs from what this branch had been using since
  Phase 3 (`rotate(-4deg)`, `right:14px`): the updated file shows
  `rotate(4deg)`, `right:20px` — corrected to match exactly. The file's
  sticker text is plain "building" with no emoji (likely the same design-
  tool text-field limitation noted for the "hi, I'm Brian" sticker
  elsewhere), but the emoji is kept — established sitewide copy
  (`"🚧 building"`, identical wording already live on `/tools`), not
  something this pass should silently change based on an export artifact.
- **Homepage Restructure — a live-preview check (not a design-file re-read)
  caught four real layout/logic bugs the prior design-fidelity passes
  missed, all confirmed against actual rendered output rather than guessed
  at.** Brian flagged that the Railway preview didn't match what had been
  signed off; since this session's sandbox can't reach `*.up.railway.app`
  directly (network policy blocks it), verification used a locally-served
  copy of the exact same commit instead — comparing element bounding boxes
  (`getBoundingClientRect()`) between the live app and the bundled design
  export at matching viewport widths, not just eyeballing screenshots.
  Four distinct bugs surfaced this way, each already covered by a
  regression test:
  1. **Hero headline/subhead were capped at `max-width:640px`** — a
     leftover from an earlier hero-polish pass that predates the current
     two-column grid giving the hero column ~975px to work with. The cap
     made the text wrap narrower than the design (design lets it fill the
     column) and left a large unused gap next to the sidebar. Removed
     outright.
  2. **The avatar `<img>` didn't scale with its own wrapper.** `_avatar()`
     bakes a fixed pixel `width`/`height` into its inline style — correct
     for every other call site, which use one fixed size throughout. The
     homepage hero photo is the one place the *wrapper* itself resizes
     (200px -> 240px at the desktop breakpoint), so without an override the
     photo stayed pinned at 200px inside a now-larger frame, opening a gap
     before the status box that isn't in the design. Fixed with a CSS
     override scoped to `.home-avatar-wrap>img,.home-avatar-wrap>div[aria-label]`
     specifically — not every child div, since the "hi, I'm Brian" sticker
     is a sibling div in that same wrapper and must not get stretched.
  3. **`.home-status` was `position:absolute` with a hardcoded top offset**,
     so its actual rendered height was invisible to `.home-photo-wrap`
     (also hardcoded, 340/400px) — any status copy longer than that guess
     (this text is admin-editable via `/admin/copy`) would silently
     overflow into the Toolbox panel in the next grid row. Moved into
     normal flow, pulled up under the avatar with `margin-top:-50px`
     instead of an absolute offset — tuned to land at the exact same visual
     position the old absolute values produced (avatar flow height minus
     the old top offset, -50px at both breakpoints) — so
     `.home-photo-wrap`'s height now genuinely reflects its content and the
     grid row sizes itself correctly regardless of copy length.
  4. **The wrong word was underlined in the hero headline.** The existing
     `_underline_last_word()` helper (by design, for arbitrary admin-edited
     heading text) always accents whichever word ends the sentence — for
     the current default copy that's "scorekeeper.", not the design's
     intended "strategic partner" mid-sentence accent. This was wrong on
     both breakpoints, not just mobile — easy to miss at full-page zoom,
     which is likely why earlier design-fidelity passes didn't catch it.
     Added `_underline_phrase()`, which targets a specific phrase
     case-insensitively and falls back to `_underline_last_word()`'s
     behavior if the phrase isn't found (e.g. an admin rewrites the
     headline without "strategic partner" in it) — same underline
     mechanism, just phrase-targeted instead of always-last-word. Homepage
     hero heading now calls `_underline_phrase(homepage_headline,
     "strategic partner")`; the unrelated `_underline_last_word("Brian
     Weisberg", ...)` use elsewhere is untouched.
  **Also confirmed, not a bug:** this session's headless Chromium can't
  actually load the sitewide Google Fonts (`fonts.googleapis.com` requests
  measured taking 12+ seconds before failing in this sandbox, vs. a
  same-second success from plain `curl` — a sandbox networking quirk, not
  a CSS issue), so local screenshots taken here render the nav
  wordmark/stickers in a fallback system font rather than Permanent
  Marker/Caveat. Checked via `getComputedStyle().fontFamily`, which
  correctly returns the intended `@font-face` stack — confirming the CSS
  itself is right and this is purely a rendering limitation of the
  comparison tooling, not something to chase in the app.
- **Homepage Original content block is a capped 2x2 grid with a header
  (2026-10).** The flagship cards rendered three across plus one: measured
  cause is the shared `.tl-featured` rule (`auto-fill`, 220px floor) fitting
  only 3 tracks in the ~816-836px homepage column. The homepage now passes a
  `tl-featured-home` modifier (fixed 2 columns from 560px, 1 below; the
  `/thought-leadership` row is untouched), the query is capped at 4
  (`Library.HOME_ORIGINAL_CONTENT_CAP`, newest by the existing
  `display_order, sort_key DESC` order; the brief's "already capped at
  four" did not hold, there was no cap), and an "Original content" label
  (the admin's own name, a fixed label, same style as "Recent highlights")
  heads it, omitted with the grid when nothing is live. Because the cap
  would otherwise drop a fifth flagged piece silently, `/admin/thought-leadership/original`
  shows a note naming the live flagged pieces past the cap (none at four or
  fewer). See `tests/test_homepage_original_content_grid.py`.
- **Homepage "Recent highlights" — a hand-curated 4-slot featured set,
  any mix of types, replacing the deleted per-type
  `get_thought_leadership_representative` fallback (2026-09).** The
  per-type mechanism above had a real, production-confirmed defect: its
  featured branch was `LIMIT 1 ORDER BY updated_at DESC` per type, so when
  two entries of the same type were both checked "Feature on homepage,"
  only the more recently *saved* one ever rendered — the other's checkbox
  had no visible effect. Both currently-featured production rows were
  `type='writing'` (ids 34/35), so id 34's checkbox had been silently
  inert since it was created. Its fallback branch (when nothing of a type
  was checked) used `_TL_ORDER_SQL`, whose undated-first clause is correct
  for a chronological feed but wrong for a hand-picked set — an undated
  entry could silently jump ahead of a newer dated one with no admin lever
  to move it. `get_thought_leadership_representative` is deleted outright
  (confirmed via grep as its only caller besides its own tests) and
  replaced by `Library.list_thought_leadership_featured_home()`: up to 4
  `featured_home=1` rows, any mix of types, `LIMIT 4` — never an assert,
  so a theoretical 5th featured row (a direct DB write, a race between two
  admin tabs) still renders as exactly 4 on the public homepage rather
  than taking the page down over an admin data condition; the cap of 4 is
  enforced at the two write routes instead, via a new
  `Library.count_featured_home(exclude_id=None)`. Ordered by a new
  `_TL_FEATURED_ORDER_SQL = "sort_key DESC, display_order ASC"` —
  deliberately **not** `_TL_ORDER_SQL`, since it drops the undated-first
  clause that caused the defect above. **`exclude_id` is the case most
  likely to be built wrong**: an edit save that keeps an already-featured
  row's own `featured_home=1` must not count itself against the cap, or
  every edit to any of the 4 currently-featured rows would be refused
  against itself forever — the edit route passes its own `item_id`, has
  its own regression test. A refused save (attempting to feature a 5th
  piece) re-renders the add/edit form with the submitted values intact and
  a visible inline error via a new shared `_tl_form_page` helper — the
  same `--alert-wash`/`--alert` error-banner convention `_oc_form_page`/
  `_ai_surface_form_page`/`_feed_form_page` already use, never coral. The
  route's *other* validations (missing title, invalid type, a non-numeric
  `display_order`) are unchanged and still raise `HTTPException` directly
  — only the featured-home cap refusal goes through the re-render path.
  **No lock guards the check-then-act race** between the count check and
  the write — not worth building for a single-admin tool, per the standing
  lesson from two prior over-engineered concurrency fixes in this codebase
  (the coral-check `ContextVar` guard, `_failing_checks_count`'s
  double-checked-locking near-miss); the `LIMIT 4` is what makes the race
  harmless regardless. Icon and label per tile are now derived from the
  piece's own `type` (`_TL_TYPE_ICON`/`_TL_TYPE_LABELS`), not from a fixed
  per-column type — all 4 slots can be the same type now (and are, in
  production), and repeated icons/labels down the grid are expected,
  deliberate, and not deduplicated, varied by position, or flagged
  anywhere in the admin UI; which four pieces to feature is Brian's own
  curation call per occasion. Zero featured rows omits the whole
  `.home-tl-highlights-wrap` (heading + grid together, not just the
  tiles) — the "See all thought leadership →" link is a sibling of that
  wrap, not inside it, so it survives either way. The admin list at
  `/admin/thought-leadership/third-party` gained a Featured column showing
  the homepage **render slot** (1-4, computed from the same
  `list_thought_leadership_featured_home()` query the homepage itself
  renders from, so it can't disagree with what's live) rather than a
  plain yes/no badge — per the standing "a control that edits/reflects an
  ordering has to show that ordering" lesson the feeds Order-arrows work
  established (see `/admin/reader/feeds` above) — plus a live "Homepage
  highlights: N of 4 slots used" line. Deliberately **not built**: order
  (up/down) arrows for the featured set — this PR only makes the render
  order visible (the slot column), which is the precondition for adding
  arrows cheaply later if wanted, not the arrows themselves.
- **Phase 6 — Layout Width Fixes, Admin Nav Restructure, Library Admin
  Cleanup.** Three coupled pieces in one PR (Library's management entry
  point moves as part of the nav restructure, so splitting wasn't clean).
  `/about`'s bio column had a real centering bug (bare inline
  `style="max-width:760px;"` instead of `.tool-prose`) — fixed. A live
  pixel-measurement check found `/tools` and `/admin`, also flagged as
  possibly affected, were already correctly centered — no fix needed there,
  confirmed rather than assumed. `/admin`'s right column now mirrors the
  public nav's order (Thought Leadership, then an expandable CFO Toolbox —
  Software/Toolbox categories/Resources/Communities/Sail Don't
  Row settings plus a nested FP&A Buddy sub-group and a Library link, then
  "Brand, voice, and content", then System unchanged). `/admin/library`
  dropped "Open Reader" from its tool list (now reachable via a dedicated
  callout at the top of the page, and via Admin's CFO Toolbox &rarr;
  Library) and merged Historical Sweep into Archive Queue as a collapsible
  panel — `/admin/library/backfill` now 301s to `/admin/library/queue`; the
  underlying `POST .../backfill/start` and `GET .../backfill/status` routes
  are unchanged. **(Superseded 2026-09, PR 3 — this whole Historical
  Sweep/Archive Queue merge, and the `/admin/library/backfill` redirect
  stub, are gone: the Archive Queue was retired outright, see the Archive
  Queue retirement bullet below.)** The remaining 7 Library tools are
  grouped into three
  sections (Archive additions &amp; backup / Existing archive management /
  Tagging), with Archive backup folded into the first section (a live-preview
  follow-up moved it there from a standalone headingless card, which read
  oddly once every other tool had a section heading above it) and Enrich
  archive kept in Tagging (it drafts the vocabulary the other Tagging tools
  curate). That same follow-up also reflowed the page into a 2x2 CSS grid:
  Open Reader/flow diagram pairs with "Saving articles from anywhere" on
  top, "Archive additions &amp; backup" pairs with "Existing archive
  management"/"Tagging" below, collapsing to one column under 900px. The
  Reader's own "Saved" quick view/list-pane label is renamed "Archive" to
  match every admin reference to the same content — display text only;
  `view=saved` stays the URL param and every internal identifier is
  unchanged. See ARCHITECTURE.md's "Admin nav restructure, Library page
  cleanup, and page-width fixes (Phase 6)" section for the full write-up.

- **FP&A Buddy explainer, follow-up round — public layout restructure, and
  "How FP&A Buddy works" moves off `/admin/*` outright.** On `/tools/fpa-buddy`:
  the 5-item feature list (previously a top-of-page 2-column grid that split
  unevenly for an odd item count) moved to the bottom of the page, after the
  ask interaction, rebuilt as a single always-visible column so item count can
  never force an uneven split again — the old mobile-only `<details>` collapse
  is gone with it, since a bottom-of-page recap doesn't need to hide behind a
  toggle the way a top-of-page block competing for attention did. A teaser
  line under the intro ("Curious how this works? Scroll down or read the full
  breakdown →") links to both the relocated section (`#fpa-features`) and the
  new deep-dive page. A clearly-labeled illustrative example (a realistic
  sample question + a mocked cited answer, reusing the real `.ask-q-bubble`/
  `.ask-answer`/`.ask-src-list` components) sits near the top — there's no
  real usage yet to pull a genuine example from, so it's explicit about being
  mocked rather than reading as a captured real answer. **The deep-dive page
  is now public**, moved from `/admin/system/how-fpa-buddy-works` to
  `/tools/fpa-buddy/how-it-works` — reachable by anyone with the link (no
  `noindex`, not linked from primary nav, same discoverability tier as a
  thought-leadership sub-page) rather than admin-gated. A content audit
  before the move found the page assumed an admin-insider reader in three
  places, all fixed: the "← Admin" breadcrumb (a public visitor has no admin
  access to return to) became "← FP&A Buddy", matching every other public
  sub-page's own back-link convention; the inline `/admin/exa-settings` and
  `/admin/users` links were de-linked to plain prose ("the site admin"),
  since a public reader would only ever hit a login wall on either; and the
  `ARCHITECTURE.md` link was removed outright, since the repo is private and
  the link 404s for exactly the outside audience the page is now written
  for. The admin dashboard's FP&A Buddy card now points at this same public
  URL instead of hosting a separate admin-only copy — no route survives at
  the old `/admin/system/*` path. The "Which engine handled this answer?"
  and "Why the citations can be trusted" callouts were reformatted from
  dense paragraphs into bold-lead-in bullets, matching "Where an answer's
  sources come from" directly above them. The flow diagram's `Quick /
  Standard / Deep` annotation node — previously one dotted edge into
  `Claude` that Mermaid's layout rendered as a box disconnected below the
  main flow — now fans dotted edges into `Library`/`Feed`/`Web`, the same
  three tiers its own label describes, landing it as a peer of the question
  node instead of an orphan; the diagram frame is also wrapped in a
  `max-width:680px` container so it stops stretching to the full page
  column regardless of the SVG's actual size. **Known limitation, flagged
  rather than silently assumed fine:** this session's sandboxed headless
  Chromium can't reach the `cdnjs.cloudflare.com` CDN that serves
  `mermaid.min.js` (same class of sandbox-networking gap as the documented
  Google Fonts one), so the retiled diagram's actual rendered pixel layout
  was never visually confirmed here — only the container-width fix and the
  edge-connectivity fix, both verified structurally (HTML/CSS inspection,
  a live Playwright screenshot showing the narrower frame). Worth a quick
  look at the live PR preview to confirm the diagram reads well before
  merging.
- **"How FP&A Buddy works" width-tier fix, follow-up to the above round.**
  The page rendered noticeably narrower than the other `article-atlantic`
  long-form pages (Growth Engine Ratio, AI Hackathon Playbook, Connecting
  Claude to NetSuite) — all three pair `article-atlantic` with the
  `.page-full` width tier (~1800-2000px per BRAND.md's five-tier layout
  system), but this page still carried `.page-admin` (~1400-1600px)
  verbatim from the `/admin/system/*` template it was originally built
  under, and nobody updated it when the page went public in the prior
  round. `article-atlantic` itself only sets paragraph line-height/spacing
  — it carries no width of its own — so the mismatch was silent until
  someone compared the two side by side. Fixed by switching the outer
  class to `.page-full`, confirmed with a live measurement (not just a
  screenshot glance, per the standing computed-value-over-screenshot
  lesson): the `.tool-prose` reading column's bounding box is now
  byte-identical between this page and `/thought-leadership/growth-engine-ratio`
  at the same viewport width.

- **Tools whole-record profile signoff (2026-08, amended same phase) — a new
  `tools.needs_review` column, mirroring `community_profiles.needs_review`'s
  concept as closely as sensible for tools' shape, sitting alongside (never
  replacing) the three existing per-field flags.** A same-night investigation
  mapped Software vendors' and Communities' verification workflows side by
  side: tools have three independent per-field flags
  (`description_needs_verification`, `agent_taxonomy_needs_verification`,
  `competitive_differentiation_needs_verification`), each with its own "Mark
  verified" route/button, all writing to the shared `narrative_review_log`
  table — but no whole-record rollup, no single "is this vendor's profile
  fully verified" view or action anywhere. Communities have exactly the
  opposite shape: one whole-record `community_profiles.needs_review` flag
  covering all ~23 profile fields at once, settable via a manual checkbox
  independent of any AI generation, plus a dedicated "Mark reviewed" button
  (`mark_community_profile_reviewed()`). Built as a genuinely additive,
  higher-level signoff for tools: `tools.needs_review INTEGER NOT NULL
  DEFAULT 0` (same literal column name as Communities', not
  `*_needs_verification`-prefixed, so it reads as the same cross-entity
  concept while staying unambiguous next to the three field-scoped columns,
  which are always field-prefixed).

  **The initial build (same night, same phase) shipped this as purely manual
  and default-0, deliberately diverging from Communities on both counts —
  Brian reversed both calls before this PR ever merged**, so the two
  divergences below are stated as the FINAL behavior, not the original one
  (worth knowing if an old draft PR description or an early commit message
  is ever read literally): (1) **auto-linked to per-field regeneration after
  all** — mirrors Communities' `needs_review = checkbox OR profile_ai_drafted`
  pattern as closely as tools' three-independent-fields shape allows: a fresh
  Generate/Refresh draft that lands ANY of the three per-field flags on 1
  also forces `tools.needs_review` to 1, regardless of the checkbox's prior
  state; the checkbox/"Mark reviewed" button can still clear it to 0 at any
  time, and that manual 0 persists across any later save that doesn't itself
  draft one of the three fields. Structurally, Communities has one submit
  route for all 23 fields, so one OR expression covers everything; tools'
  three flags are written from three different places — Description/
  Differentiation share a request with the whole-record checkbox (inside
  `admin_tools_edit_submit`, where the OR is literal:
  `needs_review = checkbox=="1" or description_needs_verification or
  competitive_differentiation_needs_verification`), while Agent taxonomy's
  own fresh-draft trigger fires from an entirely separate request
  (`_run_tool_research`, called either as a background task right after tool
  creation or synchronously from the "Refresh AI research" button) with no
  checkbox of its own to OR against — that path force-sets `needs_review` to
  1 directly (never forces to 0) whenever the fresh draft's own
  `agent_taxonomy_needs_verification` comes back true. Keying the trigger on
  the per-field flag actually landing on 1 — not merely "a field was
  drafted" — has a useful side effect: `scripts/regen_ai_drafted_fields.py`'s
  own pre-existing, deliberate bypass (it always passes
  `needs_verification=0` for all three fields on every regen) correctly
  never trips the auto-link either, with no special-casing needed. (2)
  **defaults to `1` (needs review) on tool creation, not `0`** — a brand-new
  profile should read as "needs review" until someone actually signs off on
  it, matching Communities' own "unreviewed until confirmed" intent even
  though the mechanism differs (Communities has no profile row at all until
  content is drafted; tools have a live row from creation). The SQL column
  default itself stays `0` (the safe, non-retroactive-flagging value for
  migration backfill of any already-existing row, and for a raw INSERT that
  doesn't pass a value) — the "1 by default" behavior lives instead as
  `add_tool()`'s own Python-level default parameter (`needs_review: int =
  1`), same "SQL default is the floor, Python default is the real behavior"
  split `description_needs_verification` already uses.

  Mirrors Communities' mechanics exactly everywhere else: `Library.
  set_tool_needs_review(tool_id, needs_review)` (the checkbox's write path,
  called from `admin_tools_edit_submit`) and `Library.mark_tool_reviewed
  (tool_id)` (mirrors `mark_community_profile_reviewed`, a plain single-column
  clear, no-op on a missing tool) plus `Library.count_tools_needing_review()`;
  `POST /admin/tools/software/{tool_id}/mark-reviewed` mirrors
  `admin_communities_mark_reviewed`'s shape exactly, including its
  `redirect_to` handling (defaults to `/admin/tools/software` for the admin
  list's inline button, validated against an allowlist that also accepts
  the tool's own edit page for the edit page's hidden form — the same
  allowlist convention `admin_tools_delete` already uses) — logs to
  `narrative_review_log` with a new `field_type='profile'` discriminator
  (`detail` snapshots `tools.description`, the single most representative
  field, same reasoning the Community route gives for snapshotting
  `verdict_summary`). The checkbox and "Mark reviewed" widget (reusing
  `_narrative_verify_widget`) render in a new "Profile signoff" section on
  `/tools/software/{slug}/edit`, just above the Save changes footer.

  **New scope added in the same amendment: a "Needs review" filter on both
  admin list pages.** `/admin/tools/communities` already had this exact
  pattern (a `?filter=needs_review` query param, a count/"Show all" link
  pair, a `--caution`-colored row badge, an inline "Mark reviewed" button) —
  nothing needed adding there. `/admin/tools/software` gained the identical
  pattern (not the separate client-side `_admin_sort_filter_toolbar_html`
  dropdown mechanism used for the AND-matched scalar fields below it — that
  fits a category/cost-band-style filter, not this single boolean toggle,
  and Communities' existing pattern already solves exactly this case).
  **A real, pre-existing bug in Communities' own filter, found and fixed by
  this amendment's verification pass**: both list routes' empty-state
  fallback was written as `"".join(rows) or MSG_A if filter != "needs_review"
  else MSG_B` — Python parses this as `("".join(rows) or MSG_A) if ... else
  MSG_B`, so whenever `filter=="needs_review"` the whole expression
  unconditionally evaluated to `MSG_B` ("Nothing left to review"),
  discarding any real matching rows regardless of whether there were any —
  the filtered view on `/admin/tools/communities` had silently never shown a
  single row since it shipped. Fixed on both pages with explicit
  parentheses: `"".join(rows) or (MSG_A if ... else MSG_B)`, which evaluates
  the join first and only chooses between the two empty messages when it's
  genuinely empty — covered by a regression test on each page (one proving
  real rows render under the filter, one proving the correct empty message
  shows when there truly are none).

  **Public gating (the one open question raised before building): admin-only
  bookkeeping, no visitor-facing effect** — confirmed with Brian rather than
  assumed either way, and unchanged by the reversals above. Unlike
  Communities' `needs_review`, which hides the entire profile draft
  (Bottom-line callout, Sources list, every grouped card) from a public
  visitor, `tools.needs_review` changes nothing on `/tools/software/{slug}`
  or the compare matrix — the three existing per-field flags already do that
  gating job for tools, so a second, coarser gate would be redundant. A
  regression test asserts the public profile page renders byte-identical
  with the flag on or off.

  **"Flag for review" quick-toggle (2026-08 follow-up, same PR before merge)**
  — Brian's post-review ask: a way to flag a profile "needs review" in the
  moment while just looking at it, not only while actively editing.
  Investigated first: the `needs_review` checkbox rendered in exactly two
  places (the tool edit form, the Community profile edit form) — nowhere
  else, confirmed by direct grep. Added the mirror action of the existing
  "Mark reviewed" list-row button, on both admin lists: `POST /admin/tools/
  software/{tool_id}/flag-for-review` (`Library.set_tool_needs_review
  (tool_id, 1)`, reusing the existing method) and `POST /admin/tools/
  communities/{community_id}/flag-for-review` (`Library.
  flag_community_profile_needs_review`, a new narrow single-column `UPDATE`
  mirroring `mark_community_profile_reviewed`'s own shape — including its
  no-op-on-missing-row precedent: a community with no profile draft yet has
  nothing to flag, so this deliberately does NOT fabricate a row). Both
  buttons render only when the row is NOT already flagged (the inverse of
  "Mark reviewed"'s own only-when-flagged guard), same `_is_authed` admin
  gating and `redirect_to` allowlist pattern as every other action in this
  system — never reachable by a public visitor. **Deliberately does NOT
  write to `narrative_review_log`** — that table's whole purpose is
  recording an explicit human confirmation ("I reviewed this"); flagging is
  the opposite signal, so logging it there would misrepresent the table's
  meaning. It also does not clear an existing "Reviewed by X on Y" stamp —
  same as the checkbox-driven path already didn't, kept consistent between
  the two ways of setting the flag rather than introducing a new asymmetry.

- **Review-status consolidation (2026-08 follow-up) — a real scope
  correction, not a small tweak: "Flag for review" comes OFF both admin
  lists entirely, moves to exactly two places (profile VIEW page, edit
  page), and ONE shared visual "Review status" pill component replaces
  every scattered whole-record/per-field/confidence indicator across all
  three surfaces (admin list, profile view, edit page top) for both
  Software vendors and Communities.** Phase 0 investigation confirmed: (1)
  the per-field `*_needs_verification` badges (Description/Agent taxonomy)
  already rendered on the tool profile VIEW page, admin-only, unchanged by
  this pass — but Competitive differentiation had **no publish gate or
  visibility label there at all**, a real, pre-existing, undocumented gap
  (flagged, not fixed in this pass — out of scope, since this pass is about
  the whole-record signal, not adding a new per-field gate); (2) the
  "View profile →" link Brian recalled is actually labeled "Full profile
  →" on the public directory cards (`/tools/software`, `/tools/communities`)
  and works correctly there, unconditionally, for both authed and anon
  visitors — but **no equivalent link existed on either admin list at
  all**, only "Edit" (which opens the edit form, not a read-only view);
  fixed by adding a "View profile" link to both admin list rows, pointing
  at the tool/community's own public profile URL (which already renders an
  admin-enhanced view when signed in — the Edit button, the meta line, the
  per-field unverified labels — this pass's new pill just joins that
  existing admin-enhancement pattern, not a new page).

  **Shared component** (`_review_status_pill_html`/`_review_status_action_html`/
  `_review_status_block_html` in `webapp/app.py`): a real pill shape
  (`border-radius:999px`, distinct from every other 4px-radius admin chip
  on these pages), solid `#15803D` "Reviewed" / solid `var(--coral)` "Needs
  review" — true stoplight green, the same sanctioned BRAND.md exception
  the auth-cookie-status dots use, since `--good` (navy) is the site's
  dominant color and unusable as a health signal; coral for the negative
  state is Brian's own explicit call for this one component, not a general
  license to use coral for status elsewhere. The coral state can carry an
  optional "(n/total)" breakdown: Communities reuses the existing
  `unconfident_count`/12 (Claude's self-reported low-confidence count on
  the 12 tracked profile fields) — the one dataset explicitly named to
  carry over; Software counts how many of its own 3 per-field
  `*_needs_verification` flags are currently set, giving "(n/3)". The
  action half is the exact mirror of the pill's own state — "Flag for
  review" when currently reviewed (green), "Mark reviewed" when currently
  needs review (coral) — a real polarity bug (the branches were swapped)
  was caught and fixed by this build's own verification pass before it
  shipped, now pinned down by `test_review_status_action_html_polarity`.

  **Admin list**: both `/admin/tools/software` and `/admin/tools/communities`
  gained a dedicated "Review status" column (pill + "Mark reviewed" only —
  "Flag for review" is deliberately never offered here, Brian's explicit
  call: "I never want to flag something from a list of many rows"),
  replacing what used to be scattered inline badges in the name cell:
  Software's plain "Needs review" chip; Communities' plain "Needs review"
  chip, its brown "N field(s) needs verification" auto-fill-gap badge
  (`gap_badge` — a genuinely different signal, per-field data `_NEEDS_
  VERIFICATION`-sentinel-blank left by "Auto-fill from URL", not a review-
  status fact; not lost, still visible per-field on the profile edit view,
  just no longer duplicated on the list), and its separate "N/12 fields
  low-confidence" badge (`unconfident_badge` — its count IS the new pill's
  breakdown now). `low_conf_badge` (the whole-profile "drafted without a
  fetch" boolean, a third, distinct concept from either retired badge) was
  not named for retirement and stays as its own badge. The `?filter=
  needs_review` links needed **no logic change on either page** — both
  already keyed off exactly `tools.needs_review`/`community_profiles.
  needs_review`, the same signal the new pill displays, confirmed rather
  than assumed.

  **Profile VIEW page** (`/tools/software/{slug}`, `/tools/communities/{slug}`):
  the pill+action block renders admin-only (`if authed`), placed near the
  existing admin-only meta line at the bottom of the page. Communities'
  version is additionally gated on a profile draft actually existing (`if
  authed and profile`) — a community with nothing drafted yet has nothing
  to review or flag, so the block is simply absent, not shown empty.

  **Edit page, moved to the TOP** (was a checkbox + "Mark reviewed" widget
  in a "Profile signoff" section at the bottom of the tools edit page, and
  a checkbox near the bottom of the Community profile field list) — **the
  checkbox is gone entirely on both entity types**, replaced by the same
  pill+action block, now an immediate one-click route action like every
  other surface, not tied to clicking Save. This is a real mechanism
  change, not just a relocation: `admin_tools_edit_submit`'s and
  `admin_community_profile_submit`'s own `needs_review` computation used to
  read a submitted checkbox field; with no checkbox on the form to read,
  each route now instead carries the tool's/profile's CURRENTLY-persisted
  `needs_review` value (fetched before the write, already available as
  `tool`/`existing_profile`) forward, OR'd with a fresh draft on the
  tracked fields this same submit — so a plain resave can never again
  accidentally clear a manually-set flag (the old checkbox-based design
  could, if a resubmission simply omitted the field); only the dedicated
  "Mark reviewed" action clears it now. `_community_profile_form_fields`'s
  return type changed from `tuple[str, str]` (fields HTML + a hidden verify
  form the caller had to render separately) to a single string, since the
  new component is self-contained (`_review_status_action_html` already
  produces its own `<form>`, no external hidden-form pairing needed).

  **Copy tightened** (item 7): both entity types' explanatory line under the
  pill is now one short sentence, entity-specific rather than one sentence
  forced to cover both — Tools: "set automatically when a new tool is added
  or any tracked field is refreshed, or manually anytime here" (matches
  tools' two real triggers: the creation-time default and the per-field
  auto-link); Communities: "set automatically whenever the profile is
  drafted or refreshed via Generate, or manually anytime here" (Communities
  has no creation-time default — profile_ai_drafted is genuinely one
  mechanism covering both "first draft" and "later refresh," not two).

  **Redirect allowlists extended**: `admin_tools_mark_reviewed`/
  `admin_tools_flag_for_review`/`admin_communities_mark_reviewed`/
  `admin_communities_flag_for_review` all gained the plain profile-view
  URL (`/tools/software/{slug}`, `/tools/communities/{slug}`) alongside
  their existing admin-list/edit-page allowlist entries, since the action
  button now also lives on the view page and needs to redirect back to it.

  **Item 5 (blank field rendering) and item 6 (the "View profile" link)
  were investigation-only in this pass, not built** — see the findings
  above (folded in since item 6's finding is what motivated adding the new
  "View profile" list-row link) and the session's own report to Brian for
  the full write-up on item 5 (the `c['field'] or '—'` pattern is
  consistent and bug-free everywhere checked; whether "Beyond the Books"
  specifically has genuinely-empty underlying data needs Brian's own check
  against production, which this session has no access to).

- **Review-status consolidation, brand-fidelity follow-up (2026-08) — Brian's
  review of the first draft's screenshots found real BRAND.md violations,
  not preference; fixed against BRAND.md directly (via the
  cfo-navigator-brand skill) rather than approximated, plus a scope
  expansion to every "needs verification"/"unverified" indicator on both
  entity types, not just the three original surfaces.**
  1. **Green isn't in the palette at all** — the pill's "Reviewed" state
     (`#15803D`, the auth-cookie-status dots' own sanctioned exception) was
     never re-confirmed against BRAND.md for this component. Replaced with
     `var(--seafoam)` fill + `var(--navy)` text — the exact "seafoam fill,
     navy text, radius 6px" tag/badge convention every other pill on these
     pages already uses (the category "FP&A" tag).
  2. **Coral as a solid-fill "button-shaped" pill violates the standing
     "coral is a rare accent, never a button" rule** — restyled to a light
     `var(--coral-wash)` fill. Text color needed a second correction beyond
     what item 2 literally asked for: `var(--coral-deep)` text reads as the
     obvious "colored text on light background" pairing, but
     `tests/test_brand_standards.py`'s own mechanical CI check
     (`small_coral_text_spans`, BRAND.md §2.3) bans coral/coral-deep text
     under 18px outright — checked directly against the live test, not
     assumed from the ramp table's "text-capable coral (AA)" description
     for `--coral-deep`, which reads as an exception this stricter
     mechanical rule doesn't actually carve out. So `--coral-wash` pairs
     with `var(--navy)` text instead — literally BRAND.md's own coral-wash
     row ("Callout blocks — pair with navy text (11:1 contrast)"), and the
     same pairing already repeated at every other real `--coral-wash` call
     site in this codebase (`error_banner`s, the community-gap-feedback
     dismiss banner, etc.) — confirmed by grep before choosing it, not
     invented. The whole-record pill and every per-field badge below both
     use this identical `coral-wash`+`navy` pairing now.
  3. **Consolidated every "unverified—hidden from visitors"/"needs
     verification" indicator across both Software and Communities**, not
     just the three original surfaces — a full audit (not just the two
     pages Brian's screenshots showed) found the concept rendered in
     **six** different places, four of them off-palette amber
     (`#92400e`/`#fef3c7`, no BRAND.md token at all): `.tool-desc-verify`
     (Software directory card — recolored, and unified from plain text to
     the same small badge-box treatment every other surface uses),
     `.tp-verify` (tool profile page's Description/Agent taxonomy cards),
     `.cc-verify` (Software compare page), and `_narrative_verify_widget`'s
     inline badge (the tool EDIT page's own per-field "Needs verification"
     flag next to Description/Agent taxonomy/Differentiation — found only
     by tracing `_narrative_verify_widget`'s remaining callers, not
     mentioned in Brian's screenshots at all). Two more were found broken,
     not just off-brand: **the Community profile page's own `.tp-verify`
     was never defined in that page's `<style>` block at all** — a real
     pre-existing bug, so the whole-profile "unverified—hidden from
     visitors" badge on the Bottom line callout and each of the 4 grouped
     section cards was rendering completely unstyled; and **the Communities
     compare page's `_profile_cell` reused `.comm-verify`** (the unrelated,
     intentionally-muted-dashed "field was never auto-fill-researched"
     data-completeness flag, `_verify_html`'s `_NEEDS_VERIFICATION`
     sentinel) **for this different, publish-gated concept too** — two
     genuinely different meanings sharing one style. Split into a proper
     `.cc-verify` for the publish-gate meaning, leaving `.comm-verify`
     untouched for its own, correct, separate meaning. Every one of these
     six/eight surfaces now shares the identical `coral-wash`+`navy` small
     badge (uppercase, 10-11px, radius 5px) — visually distinct from the
     whole-record pill (999px pill radius, sentence case, larger padding)
     specifically so a reader can tell "this one field's status" from "the
     whole profile's status" at a glance, per Brian's explicit ask, even
     though both now share one color language. `_confidence_badge_html`/
     `_confidence_indicator_html` (Claude's self-reported confidence — a
     deliberately separate, already-on-brand-adjacent concept, not a
     verification-status indicator) were confirmed out of scope and left
     untouched.
  4. **Placement**: the whole-record pill moved from the bottom of both
     profile VIEW pages (after all card content) to the hero, computed
     before `hero_text` is built and spliced in right after the name/
     subhead — above the category pills on the Software page (which has
     hero-level category pills) and above the Categories card on the
     Communities page (which has no hero-level cats at all, so this reads
     as "above the Categories card, and above everything else on the
     page," the closest honest match to Communities' different layout).
  5. **Edit page**: both edit pages' bordered "Verification status" panel
     now carries an actual `<h2>Verification status</h2>` label above the
     pill/action pair — previously unlabeled, reading as abrupt.
  Verified against 10 screenshots covering every affected surface (both
  entity types × admin list, profile view, edit page top, compare page,
  plus the reviewed/green state and the Beyond the Books blank-field row)
  — including confirming, via a direct authenticated HTTP request
  (independent of the screenshot tooling), that the whole-record pill and
  every per-field badge render with the corrected colors and copy exactly
  as designed. `tests/test_brand_standards.py`/`tests/test_checks.py` both
  pass clean against the corrected version — they did not against the
  first draft's solid-coral pill, which is what surfaced the CI-check
  detail this whole correction turned on.

- **Admin list table-width investigation (2026-08, PR 465 fast-follow) —
  the Communities admin list's Review status/Actions columns ran off the
  right edge at ordinary desktop widths; root cause was column count, not
  missing responsive handling, and the real fix ended up being a shared
  minimal-default column state rather than the reorder/scroll-shadow
  approach first proposed.** Phase 0 investigation (Playwright, computed
  styles, real scroll widths — not assumed): both admin lists already wrap
  their table in `<div style="overflow-x:auto;">`, and that container was
  already working correctly — `document.body.scrollWidth` never exceeded
  the viewport at either 1280px or 1024px on either page, so the *page*
  was never actually breaking BRAND.md's "wide content scrolls in its own
  container" rule. The real problem was narrower: Software's table (7
  columns) genuinely fits at both widths and never needs scroll; Communities'
  (11 columns, several with explicit `min-width`s) measured 1549px wide and
  needed internal scroll at *both* widths, with zero visual cue that more
  columns existed off-screen — and the two columns that vanish first
  (Review status, Actions) are exactly the ones an admin acts on. Communities
  also has no mobile-card fallback below 700px at all (Software's
  `admin-table-responsive` class, confirmed absent from Communities' HTML).
  **First proposed fix (reorder Review status/Actions earlier + a CSS
  scroll-shadow affordance) was reported to Brian but never built** — a
  follow-up message proposed something better before implementation started,
  and Brian confirmed it should replace rather than supplement the reorder
  work. **What shipped instead**: both admin lists now default to showing
  only Name, Review status, and Actions (Name/Actions have no `data-col` at
  all on either table, so they're always visible regardless; Review status
  is the one optional column marked default-visible) — unless the admin has
  explicitly saved a wider view. A new "Save view for next time" button
  (reusing the existing `_admin_column_picker_html`/`initColPicker`/
  `toggleColumn` mechanism — already the single shared implementation for
  both tables, confirmed before extending rather than assumed) persists the
  *current* checkbox state to `localStorage`; `toggleColumn` itself no
  longer auto-persists on every checkbox change (it used to) — a column
  toggle is now session-only exploration, and only the explicit Save click
  commits it, so peeking at a wider view can never silently overwrite (or
  silently revert away from) a view someone already saved. `initColPicker`
  falls back to the shared `ADMIN_DEFAULT_VISIBLE_COLS = ['review_status']`
  constant only when nothing is saved yet; a stored view is always honored
  exactly as saved. Verified live (not just reasoned about) that under the
  new default, Communities' table's `scrollWidth` exactly equals its
  container's `clientWidth` at both 1280px and 1024px — the 11-vs-7-column
  overflow this investigation started from simply doesn't occur for the
  default view any more. **This is why the reorder/scroll-shadow proposal
  was dropped, not kept alongside the new default**: a wider *saved* view
  can still need to scroll on a narrow viewport, but the existing
  `overflow-x:auto` container already handles that correctly (confirmed
  both before this fix and unmodified by it) — building a scroll-shadow
  affordance for that comparatively rare case was judged premature/
  speculative rather than a real gap, and can be added later if it's ever
  actually needed. **Communities' missing mobile-card breakpoint was
  explicitly scoped OUT of this PR** (per its own author's ask, not
  silently dropped) — recommended as worth doing but as a separate,
  single-purpose follow-up: the new minimal default (4 columns, well under
  700px) substantially closes the everyday mobile gap already, and building
  the stacked-card CSS treatment is a large enough, separable change to
  deserve its own PR rather than being bundled in reactively. A real
  brand-check false positive recurred during this work (the third time
  this exact pattern has hit this repo, per this doc's own earlier
  entries): a `#465` PR-number reference in a code comment parsed as a
  valid 3-digit hex color and normalized to `#446655`, tripping
  `tests/test_brand_standards.py`; fixed the same way as every prior
  instance — reworded to "PR 465" in prose, not suppressed.

  **Two same-PR follow-ups, both requested live during review rather than
  planned up front.** (1) **Software's Actions column (View profile/Edit/
  Delete) was on a 2-column CSS grid, wrapping the three buttons onto two
  rows** — Communities' own Actions column already used a single-row flex
  layout for the identical three buttons, so this was a real inconsistency
  between the two tables' otherwise-matching designs, not a new decision.
  Switched Software to the same `display:flex;flex-wrap:nowrap` treatment
  Communities already had, with the `<700px` card-layout override changed
  from forcing a 1-column grid to `flex-direction:column` + full-width
  children so the stacked mobile card view is unaffected — verified live at
  both 1280px (one row now) and a real 390px mobile viewport (unchanged
  stacking, no overflow). (2) **The mobile-card fallback explicitly scoped
  OUT above got built after all**, once Brian offered to fold it in and a
  live 390px check of the *new minimal default* found it still genuinely
  needed: even at just 4 columns, Communities' plain (non-card) table forced
  the `overflow-x:auto` container into a real internal horizontal scroll at
  390px, with Review status/Actions cut off exactly as before — the minimal
  default closed the *page-breaking* version of the problem, not the
  *mobile-usability* one. Software's `admin-table-responsive` treatment
  (already shipped, already tested) was extended to Communities' approved-
  communities table rather than building a second implementation: every
  `<td>` gained `class="admin-table-cell"` and (for the previously-`data-col`-
  only optional columns) a matching `data-label`, the `<table>` gained
  `class="admin-table-responsive"`, and the identical `@media(max-width:700px)`
  block Software's own `<style>` tag carries was added to the Communities
  admin route's `<style>` tag — this page had never had one before, since
  nothing on it needed page-specific CSS until now. Verified live: the
  default 4-column view stacks as clean full-width cards on a real 390px
  viewport (matching Software's card treatment exactly), AND a wider
  *saved* column view (Cost band + Format checked and saved) still stacks
  correctly at 390px too — the stress case for the whole mobile-card
  mechanism, not just the default state. Desktop (1280px) confirmed
  unaffected by either change. **Communities' separate "Pending
  submissions" table (a different table, different columns, no
  column-picker mechanism) was investigated and found to have the identical
  unresponsive-table gap on BOTH admin lists** — deliberately left alone in
  this pass: it's usually empty, structurally separate from the
  column-picker/review-status work this whole thread is about, and fixing
  it doubles the surface area of an already-two-part follow-up for a
  rarely-visited state — flagged here as a genuine, real, still-open gap
  rather than silently found and dropped.

  **Third same-PR follow-up (2026-08) — desktop and mobile deliberately
  invert each other for both the Review status pill/action pair and the
  three Actions buttons, on both tables.** Requested live, after the
  mobile-card fallback above shipped: Delete should break onto its own row
  below View/Edit on desktop (mirroring the Review status pill/Mark
  reviewed button's own always-stacked "break" — which stays exactly as it
  was on desktop, unchanged), while on mobile both pairs go the other
  way — Review status's pill+button sit side by side, and all three Actions
  buttons share one row, since a mobile card's full width has the room a
  narrow desktop table cell doesn't. Implemented with the same CSS-only,
  no-JS-change discipline the rest of this admin-list work has used:
  `.admin-table-actions-grid`'s desktop styling reverted to
  `display:grid;grid-template-columns:repeat(2,auto)` (the exact 2-column
  grid Software had before the Actions-single-row fix two follow-ups
  earlier in this same PR — that fix wasn't wrong, it just turned out not
  to be the shape wanted once Brian saw it live; this is a genuine reversal
  of a prior commit in this PR, not a new decision layered on top) — 3
  items in row-major order on a 2-column grid land View+Edit on row 1 and
  Delete alone on row 2 automatically, no manual grouping needed. The
  `<700px` mobile override changed from forcing 1-column stacking to
  `grid-template-columns:repeat(3,1fr)` — one row, three equal columns,
  comfortably fitting "View profile"/"Edit"/"Delete" at real mobile card
  width. A new `.admin-review-status-group` class (added to the pill+button
  wrapper `<div>` on both tables, no visual change to its own default
  behavior) is what the mobile override targets to flip it from
  `flex-direction:column` to `row` — column stays the un-overridden desktop
  default. Communities' Actions cell also lost a redundant extra wrapper
  `<div>` around `.admin-table-actions-grid` (a leftover from an earlier
  structure, harmless but unnecessary once the grid itself does the
  layout work) while this was already being touched. Verified live at both
  breakpoints on both tables — 1280px shows the 2-row desktop break on
  both; 390px shows both pairs correctly flipped to one row each, no
  horizontal overflow on either page.

  **Fourth same-PR follow-up (2026-08) — Actions buttons were stretching
  wider on desktop than the mobile screenshots, a real CSS Grid gotcha, not
  a design choice to fix by eye.** Brian flagged it by comparing screenshots
  directly ("the buttons should never be wider than they are in those
  portrait mobile screenshots, even on desktop"); confirmed and root-caused
  by measuring real `getBoundingClientRect()` widths rather than eyeballing
  — "View profile" measured 255px and "Edit" 197px on desktop, both far
  wider than their visible text needs. Cause: `grid-template-columns:
  repeat(2,auto)` (the desktop break-into-2-rows layout from the previous
  follow-up) has no `fr` track to absorb leftover space, and a CSS Grid
  container's default `justify-content` computes to `normal`, which for
  `auto`-sized tracks with no flexible tracks present behaves as `stretch`
  — so the two `auto` columns silently grew to consume all the free width
  in the Actions cell instead of staying content-sized. Fixed with one
  added property, `justify-content:start`, on both tables' `.admin-table-
  actions-grid` (the desktop grid only — the mobile `repeat(3,1fr)`
  override is unaffected, since `1fr` tracks are supposed to fill their
  container). Re-measured after the fix: desktop "View profile" is now
  113px, identical to its mobile width; desktop "Edit" (55px) and "Delete"
  (74px) are both narrower than their mobile versions (95px each, since
  mobile's three equal `1fr` columns size to the widest label). Review
  status's pill/button pair needed no fix — already `align-items:flex-
  start` on its flex-column container, so it was never stretching in the
  first place; confirmed by the same measurement (143px/134px, identical
  at both breakpoints). Full regression: 2364 passed, 0 failed.

  **Fifth same-PR follow-up (2026-08) — "narrower than mobile" wasn't
  good enough either; Brian's actual ask was genuine uniformity
  ("They should be a consistent size regardless"), and getting there
  surfaced two more real CSS Grid/specificity bugs, both caught only by
  measuring the grid's own box, not the page's.** The Fourth follow-up's
  fix left desktop Edit/Delete narrower than their mobile widths (55px/
  74px vs mobile's 95px each) — mobile's `repeat(3,1fr)` sizes every
  column to the widest label's own min-content width, so "View profile"
  (95px) forced Edit/Delete to match it too, but that's incidental
  uniformity within one breakpoint, not the same width at every
  breakpoint. Replaced both the desktop `auto`-track sizing and the
  mobile `1fr`-stretch with one unconditional fixed width (no media
  query, same value everywhere) on `.admin-table-actions-grid a`/`form`
  plus `width:100%` on the form's own button. First attempt used 113px
  (View profile's natural unconstrained width) and appeared to pass a
  first measurement pass (`getBoundingClientRect()` reporting 113px at
  all four breakpoint/table combinations, `document.body.scrollWidth`
  never exceeding the viewport) — but the actual mobile screenshot
  showed Delete visibly clipped at the card's right edge, exposing a
  real gap in that check: page-level overflow can read `False` while a
  child element overflows its OWN box, invisibly, as long as nothing
  widens the page itself. A targeted follow-up measurement
  (`.admin-table-actions-grid`'s own `clientWidth` vs `scrollWidth`,
  not the page's) confirmed it: `316` vs `351` — the grid's real box
  genuinely wasn't wide enough for 3×113px+gaps. Two distinct bugs,
  found in this order: (1) the mobile `repeat(3,1fr)` override needed
  its own explicit `justify-content:normal!important` — the Fourth
  follow-up's desktop `justify-content:start` lives in an inline style,
  which a non-`!important` external rule can never outrank regardless
  of which media query it's in, and `start` (unlike the default
  `normal`) makes `1fr` tracks stop filling the row and collapse to
  their content size instead — exactly backwards for a 3-across mobile
  layout that needs its `1fr` columns to actually consume all available
  width. (2) Once that was fixed, the grid's real available width at a
  390px card (~316px, after the row's own padding) was still too narrow
  for three 113px buttons plus gaps (351px needed) — "View profile"'s
  own unpadded text needs ~89px at the existing 13px font/12px
  horizontal padding, confirmed by measuring a cloned, unconstrained
  copy of the element rather than guessing. Fixed by shrinking these
  three buttons' padding (12px→8px horizontal) and font-size (13px→
  12px — which also brings them in line with every other admin list-row
  Edit/Delete button on the site, nearly all of which already use 12px;
  13px here was the odd one out) and the fixed width itself (113px→
  100px), sized with a real ~3px margin over the newly-measured natural
  text width (98.5px), not tuned to fit exactly. Re-verified after the
  fix: all three buttons render at a literal 100px at both 1280px and
  390px on both tables, and `.admin-table-actions-grid`'s own
  `clientWidth`/`scrollWidth` are equal (no internal overflow) —
  confirmed by measurement, not just a clean screenshot. Full
  regression: 2364 passed, 0 failed.

  **Sixth same-PR follow-up (2026-08, two rounds) — "+ Add software"/
  "+ Add community" overlapped the page h1 on mobile; the first fix's
  own width still wrapped in Brian's real browser, so the button moved
  again, below the view-links line, at every breakpoint, sized with a
  mechanism that can't wrap regardless of font.** Round 1: the header
  row (`display:flex;align-items:center;justify-content:space-between`,
  no mobile override) put the Add button vertically centered next to the
  h1 — fine at desktop width, but at a narrow mobile viewport the h1 can
  wrap to two lines and the centered button sat on top of the wrapped
  second line. Fixed with a `.admin-header-row`/`.admin-header-add-btn`
  class pair and a `@media(max-width:700px)` override dropping the
  button below the h1 (`flex-direction:column`), sized to a fixed
  186px (measured against "+ Add community"'s natural width in this
  sandbox's own headless Chromium, plus a small margin). **Round 2:**
  Brian reported the button still wrapping to two lines and reading too
  tall in his real browser — the 186px measurement held in this sandbox
  but not in his, because a non-fallback font can render the same text
  wider than this sandbox's font-loading-impaired Chromium measured it
  (the already-documented Google Fonts sandbox-networking gap). Rather
  than re-tune another brittle exact pixel value against a font this
  session can't fully trust, the button moved a second time — below the
  "View public directory / ..." links paragraph entirely, at every
  breakpoint, not just mobile, so it no longer competes with the h1 (or
  anything else) for horizontal space and there's nothing left to
  overlap; the `admin-header-row` class and its mobile-only override are
  gone, replaced by a plain `<h1>` and a standalone `<p>` holding the
  button. Sizing switched from a fixed `width` to
  `min-width:200px;white-space:nowrap` — mathematically guaranteed never
  to wrap regardless of which font actually renders (a narrower real
  font just leaves extra padding inside the same floor; a wider one
  grows past it instead of wrapping), so both pages' buttons still render
  the same size in the common case without depending on a font
  measurement this sandbox can get wrong. Verified live (both rounds):
  round 1 via element-level bounding-box overlap checks between the h1
  and the button at 390px/320px; round 2 via direct
  `getBoundingClientRect()` on the button itself at both 1280px and
  390px on both pages, confirming a consistent single-line 200×34px
  render and no page-level overflow. Full regression: 2364 passed, 0
  failed, both rounds.

- **`delete_tool()` cascade fix (2026-08) — surfaced by the Pave/Culpepper/Radford
  comp-benchmarking-vendor removal investigation, fixed as its own PR before any
  tool was actually deleted.** `Library.delete_tool()` already cascaded
  `tool_competitors`/`tool_name_dedupe_decisions`/`field_reviews`, but left
  `tool_feature_links` and `entity_citations` rows orphaned — nothing reads
  either once the tool is gone, a real "no dead data" violation — and left any
  `pending` `feature_review_queue` proposal naming the tool stuck pointing at
  nothing forever. Fixed generally (every future tool deletion benefits, not
  just tonight's three vendors, same "general fix, not purpose-specific"
  precedent as the `delete_article()` cascade fix): `delete_tool()` now also
  deletes the tool's `tool_feature_links`/`entity_citations` rows and denies
  (never silently drops) any `pending` `feature_review_queue` row naming it,
  with a `resolution_note`. Deliberately NOT touched: `tool_leads` (an
  intro-request log, still read via a `tool_name` snapshot with no join back
  to `tools`) and `narrative_review_log` (append-only verification trail) —
  both survive a deleted tool on purpose, same precedent as `tool_audit_log`
  itself surviving. This PR shipped and merged before any of the pending
  admin-UI deletions (Pave, Culpepper, Radford, the Revenue Operations
  cleanup, FinQuery, Gong) — the fix is what makes the existing single-row
  admin Delete button safe to use for all of them, so no separate one-off
  deletion script was needed for the tools themselves. See ARCHITECTURE.md's
  `tool_audit_log`/`tool_feature_links`/`entity_citations`/
  `feature_review_queue` schema-table rows for the cross-referenced write-up.

- **CFO Toolbox voice enforcement + structure (2026-08) — the four
  AI-generated Software directory fields now reference the DB-backed
  `voice_core` setting, and Description/Agent taxonomy are instructed to
  use paragraph breaks and bullets where genuinely list-like.** Investigated
  first, per this pass's own instructions: `generate_tool_description`
  (Description + Short summary), `generate_tool_agent_taxonomy`, and
  `generate_tool_differentiation` (`linklib/enrich.py`) were all still on
  prompt text with no voice reference at all — the same class of gap PR
  #110 fixed for FP&A Buddy/LinkedIn drafting, just never extended here.
  `voice_core` itself already covered the em-dash rule (the exact defect
  originally observed in a generated Competitive differentiation callout)
  but said nothing about paragraph/bullet structure — that instruction was
  added directly in the generation prompts (`enrich._STRUCTURE_GUIDANCE`),
  not by rewriting `voice_core`'s own copy, which stays Brian's to edit.
  Each function takes an optional `voice_core: str = ""` param (enrich.py
  has no `Library` handle of its own); the three `webapp/app.py` call sites
  resolve `lib.get_setting("voice_core") or VOICE_CORE_DEFAULT` and pass it
  in, same pattern `scripts/archive/import_community_profiles.py` already
  used for `voice_rewrite_community_fields`. `"summary"` (a card/subhead
  teaser) and Competitive differentiation (a deliberate 1-2 sentence
  callout) are explicitly exempted from the structure instruction — both
  stay a single continuous paragraph; differentiation still gets the voice
  reference, since that's the field the spaced em dash was seen in. The
  public profile page's Description and Agent taxonomy `<p>` tags gained
  `white-space:pre-wrap` so a structured draft's breaks/bullets actually
  render (a bare `<p>` collapses embedded newlines) — a pure display
  change, invisible on any existing single-block record. Scoped to future
  generations only: no existing stored field was regenerated, rewritten, or
  reformatted by this PR. Brian's planned bulk regeneration across both
  Software and Community profiles is separate, human-supervised work with
  its own test-batch-first pass, not folded into this change. See
  ARCHITECTURE.md's `/ask` sequence-diagram notes (the `voice_core`/
  `voice_fpa_buddy` bullet) for the cross-referenced write-up.
- **Citation-tag investigation + generation-path fix (2026-08) — a
  throwaway one-off regeneration script surfaced literal
  `(cite index="D-S">...</cite>` pseudo-tag text baked into public
  Description/Agent taxonomy fields, plus editor-facing asides and a
  memory-drafted note that shipped live with no fetch-failure signal.**
  Investigated read-only first (Phase 0), then root-caused live: a
  diagnostic script confirmed `.citations` came back empty on the calls
  that showed tags (the real Citations API never fired) and
  `stop_reason` was `end_turn`, not truncation — the model was writing
  the tags itself, as literal text, because `generate_tool_description`/
  `generate_tool_agent_taxonomy` asked for strict JSON while citations
  were enabled, a combination Anthropic's own docs confirm is
  incompatible ("citations require interleaving citation blocks with
  text output... incompatible with the strict JSON schema constraints of
  structured outputs") — the model had no clean, API-backed way to
  signal a cited claim inside one JSON string and improvised its own tag
  notation instead. Fixed by dropping the JSON contract for these two
  fields entirely: plain prose, real `inject_markers=True` citations
  (the same pattern `agent.py`'s FP&A Buddy already trusts), `confident`/
  `summary` recovered from trailing `"KEY: value"` sentinel lines via a
  new `linklib.enrich._split_trailing_sentinels` rather than
  `json.loads` — which also closes the truncation-driven
  `JSONDecodeError` failure class the investigation found (a missing/
  malformed sentinel now degrades to a safe default instead of losing
  the whole draft). Four new prompt rules close the gaps found (no
  markdown emphasis syntax, no editor-facing address, no review-scores/
  testimonials/logos/reported-results). `entity_citations` and every
  caller downstream of the two drafts' dataclasses are unchanged — only
  how the citations are produced moved, never their shape.
  **Verified two ways, deliberately not conflated**: a golden-fixture
  suite (`tests/citations_fixtures/enrich_sentinel_fixtures.py` +
  `tests/test_enrich_sentinel_parsing.py`, written against the spec
  before the implementation was wired to it) proves the deterministic
  parsing — including a reproduction of the actual production bug shape
  (Datarails' real text) run through both the post-fix code and, loaded
  separately, `origin/main`'s pre-fix code: the tags leak through in
  BOTH, confirmed rather than assumed, since no code-level filter can
  safely strip an unbounded, unknown bad-output pattern after the fact —
  the fix is preventative, at the prompt level, not corrective. What no
  unit test can prove — whether the new prompt actually stops the model
  from reverting to the old shape live — is
  `scripts/diagnose_agent_taxonomy_citations.py`'s job (extended with a
  `REGRESSION CHECK: PASS/FAIL` line), run post-merge against the
  deployed fix before any bulk regeneration touches the rest of the ~73
  tools the throwaway script never reached. **Community profile
  (`generate_community_profile`) has the identical vulnerable shape,
  just never exercised by the throwaway script's run — explicitly not
  fixed in this pass, committed as the immediate next PR, not an
  indefinite follow-up.** See ARCHITECTURE.md's matching bullet (under
  the Citations-API grounding fix section) for the full write-up.
- **Citation-tag fix, Community profile follow-up (2026-08) — the
  committed next PR from the bullet above, same vulnerable shape, same
  fix pattern, one real structural difference.** `generate_community_profile`
  had the identical strict-JSON-plus-citations incompatibility, just never
  exercised by the throwaway script's original run. Fixed the same way:
  dropped the JSON contract, plain prose, real `inject_markers=True`
  citations, same four new D1 prompt rules. The real difference from the
  two-field fix is scale and shape — 23 fields in one response, not two,
  so a single trailing-sentinel scan isn't enough. New
  `linklib.enrich._parse_labeled_blocks(text, keys, terminal_key=None)`
  is a forward-scanning, order-tolerant parser for `FIELD_NAME:`-headed
  multi-line body blocks — `_split_trailing_sentinels` (the first fix's
  parser) stays exactly as it was, reused only for the 12-key confidence
  block's own body once `_parse_labeled_blocks` has isolated it.
  **`terminal_key="confidence"` exists specifically to prevent a real data-
  integrity bug, not just to simplify parsing**: the `CONFIDENCE:` block's
  12 sub-key lines (e.g. `IDEAL_MEMBER: true`) share names with 12 of the
  23 top-level fields, so without a hard stop, header recognition would
  keep running past `CONFIDENCE:` and mistake a sub-key line for a fresh
  top-level header, corrupting the real narrative text already parsed
  earlier in the response. Verified as load-bearing, not just present, via
  a matched positive/negative test pair
  (`test_terminal_key_protects_data_integrity_not_just_formatting` /
  `test_without_terminal_key_the_collision_would_actually_corrupt_data`) —
  the negative control runs the exact same input through the parser
  *without* `terminal_key` and asserts the corruption actually happens,
  proving the guard isn't decorative. A response with literally no
  recognized field header at all now parses to an empty `blocks` dict and
  is treated as a hard failure (`generate_community_profile` returns
  `None`) — deliberately stricter than Description/Agent taxonomy's "some
  text beats none," since `upsert_community_profile` is a full replace of
  all 23 `community_profiles` columns: silently saving an all-empty draft
  wouldn't carry stale garbage forward, it would blank a community's
  entire profile. Also fixed in the same pass, found while writing the
  fixtures: `_parse_community_confidence`'s old `bool("false") == True`
  Python footgun (any non-empty string is truthy) — now compares the raw
  sentinel value against the literal string `"true"`. And a genuine
  storage-semantics preservation issue: the old JSON prompt used a real
  `null` for "unknown" on several fields (`notable_members`,
  `public_criticism`, and the short factual fields), stored as `""`; plain
  text has no `null`, so the new prompt asks for a literal placeholder word
  instead (`"Unclear"`, `"None reported"`, `"None publicly reported"`), and
  a new `_field_or_placeholder_empty` helper (tolerant of a trailing period
  the model might add) coerces those words back to `""` at parse time —
  restoring the original storage contract rather than silently changing it.
  Deliberately NOT applied to `cpe_eligible`, whose `"Unclear"` was already
  a real, literal stored value in the *original* prompt, not a
  null-placeholder. **Verified the same two ways as the first fix**: a
  golden-fixture suite
  (`tests/citations_fixtures/community_profile_sentinel_fixtures.py` +
  `tests/test_community_profile_sentinel_parsing.py`, written and confirmed
  failing against unwired code before the implementation existed) plus a
  reproduction of the old bug shape run through both pre- and post-fix
  code, confirmed to still leak through unfixed code (proving the fix is
  preventative, not a code-level filter). New sibling diagnostic
  `scripts/diagnose_community_profile_citations.py` (not an extension of
  the Agent taxonomy one — different enough response shape to justify a
  separate script, per explicit direction) makes the raw API call directly
  and prints the same `REGRESSION CHECK: PASS/FAIL` line, plus all 23
  parsed fields and the 12-key confidence dict, for post-merge
  `railway ssh` verification. See ARCHITECTURE.md's matching bullet for
  the full write-up.
- **Citation-tag investigation, blast-radius + re-run hardening (2026-08) —
  the next steps after both generation-path fixes shipped.** A read-only
  diagnostic confirmed both fixes working live on real tools/communities
  (Concourse/GoClose for Description/Agent taxonomy; CFO Alliance/The F
  Suite/Off The Ledger for Community profile — citation firing varies
  legitimately per source, not a regression). `scripts/
  report_regen_blast_radius.py` (new, read-only, zero write calls) then
  answered the actual blast-radius question by cross-referencing the
  throwaway `regen_ai_drafted_fields.py` run's own JSONL log against
  current DB content, rather than a blind `id <= N` sweep — 84 tools
  processed, 21 failures, 75 cite-tag-pollution rows, 25 legacy-shape
  (spaced em dash/markdown bold) rows, with real overlap across all three
  (e.g. Ordway hit by all three for different fields). A follow-up PR
  added a deduplicated summary section (the real "needs regeneration"
  count is the union across sections, not their sum) — confirmed live:
  **76 distinct tools, 105 (tool, field) pairs** (agent_taxonomy 74,
  description 28, competitive_differentiation 3), 27 tools needing 2+
  fields, 2 needing all 3 (Ordway, Everest). **Brian's decision: full
  re-run of all 157 approved tools, not just the 84 already touched** —
  the cost delta is trivial regardless of scope (an estimate, not a
  measured number — see below) and the catalog-consistency argument
  (every tool on the identical current pipeline, no mixed-vintage rows to
  reason about later) holds either way.
  **`regen_ai_drafted_fields.py` itself was then hardened**, closing two
  real gaps found along the way plus the two parity gaps the original
  Phase 0 report's A1 flagged:
  1. **`--ids`** (comma-separated, requires `--only tools` or `--only
     communities` — a tool ID and a community ID are different ID spaces,
     so allowing both at once would silently misapply the list) targets
     specific items directly by ID, bypassing the approved-only list —
     a non-approved targeted ID is still processed with a printed note,
     never silently dropped.
  2. **`--sample N`** closes a real gap in the original preview mode: it
     only ever showed counts and a rough cost/time estimate, never actual
     generated text — no way to eyeball whether a fix produces good output
     before committing to `--apply`. `--sample N` makes N REAL generation
     calls (costs real money, the one non-free thing about this mode) and
     prints the FULL content of each, with zero DB writes; logged as a
     third JSONL status, `"preview"`, so the resume logic
     (`_load_done`, which only ever counted `"success"`) can't mistake a
     preview draw for a completed regeneration.
  3. **Real per-item logging**: every JSONL row now also carries
     `input_tokens`/`output_tokens`/`cost_usd`/`citations_count`/
     `low_confidence` — not just success/failure, the only two facts the
     original log captured.
  4. **Cost/spend logging closes a confirmed real gap**: the ORIGINAL
     84-tool throwaway run never called `Library.record_enrichment_cost`
     at all, so its real spend was never recorded anywhere and is now
     permanently unrecoverable except by re-running — this is *why* the
     $ estimate given to Brian for the full-vs-targeted decision above had
     to be a labeled estimate (Opus 5's real per-token pricing combined
     with each field's known `max_tokens` ceiling and grounding-fetch
     shape, not a measured number) rather than a real historical figure.
     Every real generation call the hardened script makes — in `--apply`
     mode AND in `--sample` mode — now calls `record_enrichment_cost` the
     moment a draft comes back, before any write/guard logic runs, since
     the cost was already incurred at that point regardless of what
     happens next.
  5. **Empty-result guards** (Phase 0 report A1, first parity gap): the
     script used to write `draft.description`/`result.agent_taxonomy_note`/
     `draft.competitive_differentiation` unconditionally once the
     generator returned non-`None`, unlike the live `_run_tool_research`
     background job's own `if result.agent_taxonomy_note.strip():` guard.
     Description/Agent taxonomy are now DEFENSIVE-only — both generators
     already raise internally on a totally-empty parsed result as of the
     citation-tag fix itself, so this should be unreachable there in
     practice; the guard exists so the script's structure actually matches
     the live route's, not just its outcome. **Competitive differentiation
     is a REAL fix**: `generate_tool_differentiation` has no citations
     mechanism at all (still strict JSON, untouched by the citation-tag
     fix) and no internal empty-result protection — an empty result could
     genuinely come back and, pre-hardening, would have silently blanked
     the field.
  6. **Citations-validation parity** (Phase 0 report A1, second parity
     gap): Description's and the Community profile's citations now
     round-trip through the same `_validate_citations_payload` (imported
     from `webapp.app`) the live submit routes apply — both fields' real
     Generate call is stateless AJAX with no entity id at draft time, so
     their citations normally travel through the browser as a JSON
     hidden-input value and get re-validated server-side (URL-scheme
     check, title length cap, sequential renumbering) before persisting.
     This script calls the generators directly, server-side, with no
     browser round-trip — `draft.citations` is already well-typed Python,
     not untrusted client JSON — but running it through the same
     validation closes the parity gap defensively rather than assuming a
     server-computed list can never need it. **Agent taxonomy's citations
     are unchanged** — its live path (`_run_tool_research`) writes
     `result.citations` directly with no validation call either, so the
     script already matched it correctly there.
  **Verified without spending real API money**: every new/changed
  behavior (the empty-differentiation guard actually blocking a write and
  preserving old content, `enrichment_cost` rows actually appearing for
  both a successful and a guard-blocked call, the JSONL log carrying the
  new cost fields, `--sample` mode printing full content while writing
  nothing to the DB, and `_validate_citations_payload`'s round-trip
  correctly dropping a malformed citation and renumbering the rest) was
  smoke-tested against a temp DB with mocked `generate_*` functions —
  zero real Claude calls, zero real spend, per this repo's own
  CI-quota-exhaustion-era discipline of local verification over hoping a
  real call happens to demonstrate the fix. Sequenced next: the
  8-tool spot-check re-run (`--ids`, `--apply`, against GoClose, Klarity,
  Expensify, Concourse, Datarails, Coupa, Maxima, Ode — the tools with
  specific, already-diagnosed defects from the original incident) via
  `railway ssh`, full output reviewed, before the full 157-tool run.
- **Blast-radius spot-check findings (2026-08) — the `--sample` preview
  (21 real calls: description/agent_taxonomy/competitive_differentiation
  ×7 tools) confirmed the citation-tag fix holds clean (zero pollution,
  real `[n]` markers, no editor-facing asides) and surfaced a real,
  more urgent bug: Description was truncating mid-response on 6 of 7
  sampled tools (Concourse, Coupa, Expensify, Datarails, GoClose, Maxima
  — only Ode, presumably the thinnest source page, finished under
  budget).** Root-caused against the codebase's own prior incident with
  this exact failure class — `MIN_GENERATE_MAX_TOKENS`'s own comment
  documents `generate_tool_differentiation`'s original `max_tokens=400`
  once let Opus 5's adaptive thinking (which shares the same budget as
  the visible response) consume the *entire* allowance, PR 260. Description's
  1600-token ceiling predates the citation-tag fix's move to verbose
  prose + paragraph/bullet structure guidance + a trailing sentinel
  block — it was never revisited when the format changed, and cleared
  the 1200-token floor by only 400 tokens, nowhere near enough margin
  to absorb adaptive-thinking variance on a content-rich page (which is
  exactly what correlated with the truncation: richer source content
  gives the model more to reason about before it starts writing).
  Truncation effect varied — description prose cut off mid-sentence on
  some, `SUMMARY` truncated mid-word or came back blank on others — and
  in every case `CONFIDENT` fell through to its safe-fallback default
  of `False` since the parser's backward scan never reached that
  sentinel, silently marking well-grounded content as low-confidence
  for a truncation reason unrelated to actual grounding quality.
  **Fix: raised to 3000** (real parity-plus-margin over Agent
  taxonomy's working 2000-token ceiling, which showed no truncation in
  the same sample) — deliberately **not** a reorder-the-sentinels-first
  fix (would only protect `CONFIDENT`/`SUMMARY`, not the actual worse
  defect of a truncated public-facing description, and would need new
  leading-sentinel parsing code the trailing-only `_split_trailing_sentinels`
  wasn't built for) and **not** a two-call split (doubles cost — the
  15K-char grounding page would be sent twice — and latency, to solve
  what's fundamentally a budget-sizing problem, not an inherent
  content-type conflict). To be verified empirically via a `--sample`
  re-run against the three worst-truncating tools (Concourse, Coupa,
  GoClose) before this is considered closed, per the same
  local/live-verification split every fix in this investigation has
  followed. **Separately flagged, explicitly deferred (not urgent,
  already on the post-157-run follow-up list)**: `generate_tool_differentiation`
  was never brought into the D1 no-reported-results/testimonials content
  rules, since it predates the citation-tag fix and has no citations
  mechanism of its own to have motivated including it — the same sample
  surfaced exactly that gap (Maxima's differentiation cited a vendor-
  reported stat). **Two catalog decisions, unrelated to the code fix**:
  Klarity/Within (real-world rebrand, not a code bug — see the earlier
  bullet) and Ode (a services/consulting firm, not a software product —
  "Ode doesn't ship AI agents. It's a services firm," per its own
  generated profile) are both being deleted from the Toolbox; neither is
  in `scripts/seed_tools.py` (confirmed by direct grep, not assumed), so
  neither deletion needs a seed-file removal to stay durable across a
  deploy. Concourse is staying — real distinction from Klarity/Ode: it
  ships a defined roster of named, purpose-built agents customers
  configure and run themselves (not a bespoke-build services engagement);
  the "ex-CFOs and forward-deployed engineers" language in its profile
  describes onboarding support, not the core offering.
- **Voice-core ampersand rule was too narrow — "T and E" instead of "T&E"
  (2026-08, found in the max_tokens-fix spot-check's live review).**
  Concourse's regenerated Agent taxonomy spelled out a standard finance
  abbreviation the voice guide should have protected. Root cause: `VOICE_CORE_DEFAULT`'s
  own "HARD MECHANICAL RULES" already had an ampersand rule — "Spell out
  'and'; never '&' except in terms like FP&A" — but it hardcoded exactly
  one exception. T&E (and, by the same narrowness, any other real
  finance-shorthand term that legitimately uses '&') wasn't covered, so the
  model correctly followed the letter of the rule and produced the wrong
  result. Fixed by generalizing the exception from a single hardcoded term
  to a stated principle — standard finance/business abbreviations that use
  '&' as part of the term itself keep their normal form — with FP&A/T&E/R&D
  as examples, not an exhaustive list. This is a `linklib.agent.VOICE_CORE_DEFAULT`
  fix, not a field-specific prompt fix: `voice_core` is the single shared
  source every generation surface resolves through
  (`linklib.enrich._resolve_voice_core`, same PR #110 pattern FP&A Buddy/
  LinkedIn drafting/tool-and-community generation all already share), so a
  mechanical-rule fix here reaches every field in one place rather than
  needing to be duplicated into `_TOOL_DESC_PROMPT`/`_AGENT_TAXONOMY_PROMPT`/
  etc. individually. **Caveat that matters for whether this fix actually
  takes effect live**: `_resolve_voice_core`/`_build_system` both read
  `lib.get_setting("voice_core") or VOICE_CORE_DEFAULT` — if Brian's
  production `settings` table already has a saved `voice_core` value (via
  `/admin/voice`), this code-constant edit has no effect until that stored
  value is updated too, since a non-empty DB value always wins over the
  fallback. Checked whether the equivalent gap exists for A/R, A/P, and
  similar slash-based shorthand (also flagged in the same live review): no
  comparable narrow rule was found constraining those — the only over-narrow
  mechanical rule in `voice_core` was this ampersand one — but the fix is
  now a stated principle rather than a memorized exception, which should
  generalize better if a similar case turns up in a future field. To be
  verified empirically the same way as the `max_tokens` fix, before this is
  considered closed: a single-tool `--apply` re-run against Concourse
  (`--ids 10 --only tools`) confirming "T&E" renders correctly, rather than
  trusting the wording change to hold across the full 157-tool catalog
  untested.
- **Spaced-em-dash cleanup + a permanent deterministic backstop (2026-08) —
  the real fix for "zero spaced em dashes, permanently," not just a lower
  violation rate.** After the full 157-tool + 40-community regeneration run
  (0 failures, 0 cite-tag pollution on the tool side), Brian flagged 63 rows
  / 52 distinct tools (mostly `agent_taxonomy_note`) still violating
  `voice_core`'s own "HARD MECHANICAL RULES" em-dash rule ("no surrounding
  spaces... never violate"). **Root cause, confirmed by direct code read,
  not assumed**: the rule was never missing from any prompt path.
  `generate_tool_description`, `generate_tool_agent_taxonomy`,
  `generate_tool_differentiation`, and `generate_community_profile` all four
  call `_resolve_voice_core()` and interpolate `{voice_core}` into their
  prompt template (confirmed at each call site — e.g.
  `linklib/enrich.py`'s `_STRUCTURE_GUIDANCE` comment explicitly notes
  "voice_core covers tone/mechanics (including the em dash rule)"). The
  violations happened anyway because a natural-language "never" in a prompt
  is an instruction, not an invariant — an LLM's per-token compliance with
  prose rules is probabilistic, however emphatically worded, so prompt text
  alone can reduce the violation rate but can never guarantee zero. This is
  the same class of gap the Phase 0 investigation's F3 finding names:
  "nothing in the current pipeline can catch or repair a voice/structure
  violation after the fact." **One residual unknown this session cannot
  close without production access**: if Brian has ever saved a custom
  override at `/admin/voice`, that stored `settings.voice_core` value (not
  the `VOICE_CORE_DEFAULT` code constant) is what every prompt actually
  uses — this session confirmed the DEFAULT copy has always had the rule,
  but cannot inspect a possible custom override's exact wording.
  **Fix has three parts.** (1) A new `linklib/voice_mechanics.py` module
  (`fix_spaced_em_dashes`/`normalize_voice_mechanics`) — pure, idempotent
  regex substitution, no API call — collapsing a spaced em dash (or a
  spaced ASCII `--` used as a dash) to an unspaced real em dash. (2) The
  **permanent backstop**: wired into `linklib/db.py` at every `Library`
  write path that persists a prose-capable field, not just the bulk regen
  script — `add_tool`/`update_tool`/`update_tool_content`/
  `quick_update_tool` (description, summary), `update_tool_agent_taxonomy`/
  `set_tool_agent_taxonomy_draft` (agent_taxonomy_note),
  `update_tool_differentiation` (competitive_differentiation),
  `set_tool_suite_note` (suite_note), `add_community`/`update_community`/
  `update_community_content` (demographic, cost_note, notes,
  local_markets), and `upsert_community_profile`/
  `update_community_profile_research_fields` (all 22 prose-capable
  `community_profiles` columns). Every one of the 15 real call sites in
  `webapp/app.py` (the admin Generate-then-save AJAX routes, every
  hand-edit save form, bulk-edit) and every script (the regen script
  included) already funnels through these same `Library` methods —
  confirmed by direct grep before wiring the fix in, not assumed — so this
  is the one choke point that guarantees coverage regardless of what
  produced the text or what future code writes it. (3) A one-off cleanup
  script, `scripts/fix_spaced_em_dashes.py` (preview-by-default, `--apply`
  to write, write-then-read-back verified per row/column — same convention
  as `scripts/archive/rename_differentiation_columns.py`), for the rows
  already written before the backstop existed. Deliberately narrow raw
  single-column `UPDATE`s, NOT `Library.update_tool_agent_taxonomy`/
  `upsert_community_profile` — reusing those higher-level methods for a
  pure text fix would trigger their real side effects (clearing
  `agent_taxonomy_needs_verification`, clearing `entity_citations`) on the
  false premise that a mechanical whitespace fix is a human edit; a
  regression test (`test_apply_does_not_clear_needs_verification_or_citations`)
  covers exactly this. Companion fix in the same PR: `scripts/
  report_regen_blast_radius.py` (previously tools-only, since the
  throwaway run that motivated it never touched communities) now also
  analyzes `community_profiles`' 22 prose columns for cite-tag pollution
  and legacy-shape — communities are logged under one shared field name,
  `"community_profile"` (all 23 columns drafted in one call), so the
  extension reports which underlying COLUMN(S) are affected, not just that
  the field as a whole is dirty, and accepts multiple `--log-file` inputs
  (deduplicated to each entity's LAST logged status), matching the real
  shape of Brian's run — 98 community log lines across sessions dropped by
  SSH, some communities regenerated more than once. Not yet run against
  production by this session (no production access) — Brian runs both the
  extended blast-radius report and, if it finds anything, `scripts/
  fix_spaced_em_dashes.py --apply` via `railway ssh`; re-running the
  blast-radius report afterward is the actual "0 legacy-shape rows"
  verification, not merely asserted here.
- **Spaced-em-dash incident, closed — and `report_regen_blast_radius.py`
  made log-independent (2026-08) after the incident's own verification
  exposed the gap.** Brian ran the full sequence via `railway ssh`:
  `fix_spaced_em_dashes.py` fixed 456/456 flagged fields, 0 failures, 0
  read-back mismatches — but verification had to fall back to that
  script's own preview/apply/read-back cycle rather than a blast-radius
  re-run, because the JSONL log from the em-dash PR's own merge-triggered
  redeploy never made it to the persistent volume (a fresh container on
  merge; the log lived only in the old container's local filesystem). This
  closes the full citation-tag-through-em-dash investigation: citation
  pollution, the Community profile generator, Description truncation, the
  ampersand rule, and spaced em dashes are all confirmed clean at full
  catalog scale. **Fixed the log-fragility gap directly, not just noted
  it**: `scripts/report_regen_blast_radius.py` now defaults to scanning
  the full current `tools`/`community_profiles` catalog directly (every
  row, `approved` or not — a pending tool's drafted content matters before
  approval too) when no `--log-file` is passed, rather than requiring one
  — pollution/legacy-shape are properties of what's actually stored right
  now, not of any particular regen run, so a log was only ever a
  scoping convenience, never a structural requirement. `--log-file`
  still works exactly as before when passed (repeatable, same
  dropped-SSH-session dedup), for the one thing DB-scan mode genuinely
  can't reconstruct — per-attempt FAILURE detail, which needs real
  attempt-status data no full-catalog scan has; that section prints "not
  tracked in DB-scan mode" instead of a false zero. Implemented as the
  "small, fairly mechanical change" it was scoped as: the existing
  `_analyze`/`_print_sections`/`_print_dedup_summary` machinery is
  untouched — DB-scan mode just synthesizes a `status="success"` row per
  (entity, field) for every tool and every community with a profile row,
  feeding the same pipeline the log-scoped path always used.
- **`voice_core`/`voice_fpa_buddy`/`voice_matchmaker` visibility — the silent
  code-level fallback is retired for real (2026-08).** Every resolution path
  used to do `lib.get_setting(key) or CODE_DEFAULT_CONSTANT`, which made it
  genuinely ambiguous from outside the code whether a given answer was
  governed by an admin-edited `/admin/voice` value or a hardcoded constant
  nobody could see without reading source — the same ambiguity the em-dash
  incident above exposed for `voice_core` specifically. Fixed in three parts,
  applied identically to all three settings, not just `voice_core`:
  1. **Seeding**: `Library.seed_voice_prompts()` populates any CURRENTLY EMPTY
     one of the three settings from its code-default constant, gated on a
     `voice_prompts_seeded` settings flag — never an emptiness check, so a
     deliberate clear-out through `/admin/voice` stays cleared across every
     future deploy (same precedent as `seed_paywall_cookie_flags`/
     `seed_feeds_from_opml`). An admin who already customized a field before
     this shipped keeps their own text — seeding only fills in what's blank.
     Wired into a new `@app.on_event("startup")` hook (`_seed_voice_prompts`)
     alongside the existing seeding hooks, never blocking boot on failure.
  2. **New `linklib/voice_settings.py`** — `require_voice_setting(lib, key)`
     is the one place every caller resolves a voice setting from now on; it
     raises `VoicePromptMissing` on an empty value instead of substituting
     anything. Deliberately doesn't prescribe one response shape — each
     caller catches it and responds however its own module already handles
     an unavailable precondition: `enrich.py`'s four `generate_*` functions
     return `None` (with a `_logger.warning`, same convention as their
     missing-SDK/missing-key branches, placed before any page fetch so a
     missing setting doesn't waste one); `agent.py`'s `ask()` and
     `matchmaker.py`'s `_answer()` return their own `Answer`/`MatchAnswer`
     with an explanatory `text`, matching their existing missing-SDK/
     missing-key branches exactly; every `webapp/app.py` AJAX route returns a
     JSON/HTTPException 503 naming which setting is missing. Every call site
     across `webapp/app.py` (3 Toolbox generate routes, the `_run_tool_research`
     background job, the `/admin/voice/review` tester), `linklib/agent.py`,
     `linklib/matchmaker.py`, and the two still-active batch backfill scripts
     (`scripts/enrich_agent_taxonomy.py`, `scripts/enrich_community_profiles.py`,
     plus `scripts/regen_ai_drafted_fields.py`) was found via a full grep
     inventory and switched, per Brian's explicit ask ("full inventory... before
     removing anything as an active fallback").
  3. **`/admin/voice` UI**: a coral banner names exactly which setting(s) are
     empty and what's blocked when any is; each field's badge is now
     three-way (Customized / Default (as seeded) / Not configured—generation
     blocked, vs. the old binary Customized/Built-in default) computed by
     comparing the live value against the code default, not just checking
     truthiness — a just-seeded, unedited field now correctly reads "Default
     (as seeded)" rather than "Customized" even though the settings table
     technically holds non-empty text for it. **"Reset to default" now
     writes the real default text into the setting** (a new `{"reset": true}`
     payload the three save routes honor) instead of sending a blank value —
     under the new model a blank save is a distinct, still-supported
     "deliberately clear it" action (correctly shows the blocked banner),
     not what "reset" means any more.
  **Real correction found mid-build, not a regression**: while wiring
  `generate_community_profile` into this, discovered the citation-tag
  investigation's original root-cause report (and this CLAUDE.md's own
  matching bullet above) incorrectly claimed that function already
  interpolated `{voice_core}` — it never had a `voice_core` parameter at
  all, and its prompt template had zero voice-guide content; the `{voice_core}`
  match that produced the original claim was actually inside a different
  template (`_VOICE_REWRITE_PROMPT`, used only by an archived one-off
  script). Fixed in this same PR: `generate_community_profile` gained a
  real `voice_core` parameter, a "Voice guide" section in its prompt (no
  `structure_guidance` — the field's own rule 7 plus the labeled-block
  format already constrain structure), and the same empty-guard the other
  three functions have; its one live caller
  (`/admin/tools/communities/generate-profile`) and
  `scripts/enrich_community_profiles.py` both updated to actually resolve
  and pass it, closing a real, separate gap (community profile drafts had
  never received ANY voice guidance, not just no visibility into which
  source was governing it). Two similar batch-script gaps found by the same
  inventory (`scripts/enrich_agent_taxonomy.py`,
  `scripts/enrich_community_profiles.py` never resolved `voice_core` at
  all, relying entirely on the now-removed internal fallback) were fixed
  the same way. `generate_community_listing` (the "Auto-fill from URL"
  basic-listing generator) still has no voice_core support at all — flagged
  as a separate, deliberately out-of-scope enhancement, not a regression
  from this pass, since it's never had one to begin with.
- **`scripts/regen_ai_drafted_fields.py`'s community path never threaded
  `voice_core` through at all — a real gap the fallback-retirement PR above
  missed, caught by the fail-safe it built rather than by a bad write.**
  A post-merge full re-run (40 communities, real `railway ssh` execution)
  failed every single call with `generate_community_profile() aborted:
  voice_core is empty`, zero writes — confirmed via the log and this
  script's own before-write guard (`draft is None` returns before
  `upsert_community_profile` is ever called), the exact non-corrupting
  behavior the empty-guard was designed to produce. Root-caused by
  elimination against the three plausible explanations before touching any
  code, per Brian's explicit ask: `Library.get_setting`/`set_setting` do a
  plain uncached `SELECT`/`UPDATE` on every call (no in-memory cache, no
  `lru_cache`, so a stale-read-after-save theory doesn't hold); the script
  resolves `voice_core` once via `require_voice_setting` at startup exactly
  like the web app, and threads it correctly into every **tool** call
  (`_run_tools`/`_regen_tool_description` etc. all take and pass it) — but
  `_run_communities`/`_regen_community_profile`, and the `--sample`
  branch's own community loop, simply had no `voice_core` parameter at
  all, so `generate_community_profile(...)` fell through to its own
  `voice_core: str = ""` default and tripped its empty-guard every time,
  regardless of what was actually saved in the DB or when. Not a timing
  issue either — the bug is structural, so it would have failed identically
  on any run, any container, any delay after the save. This is a real,
  separate gap from the "two batch-script gap callers" the fallback PR
  actually fixed (`scripts/enrich_agent_taxonomy.py`,
  `scripts/enrich_community_profiles.py`) — this script's own community
  path was a third, unrelated caller that got missed. Fixed by threading
  `voice_core` through `_run_communities`, `_regen_community_profile`, and
  `_run_sample`'s community branch, mirroring the tool path exactly.
  Verified with a mocked-generator smoke test (temp DB, `--sample 1 --only
  communities`, capturing the actual `voice_core` value the mock receives)
  before any real API call — confirms the fix reaches the generator, not
  just that the code compiles. Per Brian's explicit sequencing: a real
  `--sample` run against a couple of communities (genuine API calls, output
  pasted back for review) is required before the full `--apply` re-run
  against all 40 — the pre-merge spot-check that validated the fallback PR
  itself only ever sampled tools, never communities, so this is treated as
  a real verification gap to close, not a formality.
- **`_seed_toolbox()` was silently reverting AI-regenerated tool
  descriptions back to the seed blurb on every deploy — 81 of 148
  seed-listed tools caught, description sync retired entirely (2026-08).**
  Found when Abacum's live Description/Agent taxonomy still showed
  pre-incident content despite the full 157-tool regen run above
  reportedly completing clean. Root cause, confirmed by direct code read
  before any live-data check: `webapp/app.py`'s `_seed_toolbox()` runs on
  **every process startup** (every deploy, restart, or crash recovery) and
  did, unconditionally, for every tool in `scripts/seed_tools.py`'s
  `TOOLS` list — `if row["name"] != t["name"] or row["description"] !=
  t["description"]: lib.update_tool_content(row["id"], t["name"],
  t["description"])` — reverting the live description back to the seed
  value whenever they differed, which is exactly what a successful AI
  regeneration produces (a long grounded description vs. the seed list's
  1-2 sentence marketing blurb). `scripts/seed_tools.py`'s own re-run path
  had the identical bug independently — a second, separate blast-radius
  vector if it were ever run by hand again. Confirmed at production scale
  via `scripts/diagnose_seed_sync_overwrite.py` (the read-only diagnostic
  built to answer this): **81 of the 148 seed-listed tools** had a live
  description currently matching the seed value exactly — the reversion
  fingerprint — with Abacum's own regen log (2026-08-26 success,
  `tools.updated_at` matching a deploy this morning) confirming the exact
  mechanism live, not just in theory. Fixed by retiring `description` from
  the sync entirely, permanently, at both call sites — `name` sync is
  unaffected and kept (nothing ever AI-drafts a tool's name, so it carries
  none of the same risk, and Brian still occasionally renames a
  seed-listed tool by editing `scripts/seed_tools.py` directly, e.g. an
  "(acquired by ...)" suffix). `Library.update_tool_content` is left in
  place (still directly unit-tested) but has zero live callers after this
  fix — a candidate for a future no-dead-code cleanup pass, not bundled
  into this urgent fix. The `/admin/tools/software` caption claiming
  "editing `scripts/seed_tools.py` updates a tool's name and description"
  was itself part of the problem (accurate description of a behavior that
  should never have existed) — corrected to say description is
  database-only, with the incident named inline so a future reader
  doesn't wonder why the caption changed. Two regression tests added
  (`tests/test_seed_toolbox_startup.py`) — proven to actually catch the
  bug by running them against the pre-fix code first and confirming they
  fail there, not just pass post-fix. **Recovery, not a DB restore**:
  rolling back to a pre-incident snapshot would also roll back the same
  day's Differentiation regen, the em-dash cleanup, and the community
  profile regen — a much worse trade for fixing 81 stale descriptions.
  Recovery plan instead: with this fix merged and deployed first (so the
  very next deploy can't re-revert anything), re-run
  `diagnose_seed_sync_overwrite.py` for the current, real affected-tool-ID
  list, then a targeted `scripts/regen_ai_drafted_fields.py --field
  description --ids <list> --apply` pass against just those tools, using
  the already-fixed generation pipeline (citation grounding, the raised
  `max_tokens`, the generalized ampersand rule, the em-dash backstop, and
  real `voice_core` all already fixed and verified clean at full catalog
  scale before this incident was found).
- **`regen_ai_drafted_fields.py`'s post-write verify step was comparing
  against the wrong string — false "VERIFY FAILED"s on perfectly good
  writes, caught live during the description-sync recovery run
  (2026-08).** ~8 of the first 27 tools targeted by `--field description
  --ids <list> --apply` (Tabs, Zip, Maxio, Pigment, Orb, Coupa, Datarails,
  Rillet, ...) logged `VERIFY FAILED — post-write read-back did not
  match`. Root cause, confirmed by direct code read before touching
  anything: `Library.update_tool()` (and every other write method this
  script calls — `set_tool_agent_taxonomy_draft`,
  `update_tool_differentiation`, `upsert_community_profile`) runs every
  prose field through `normalize_voice_mechanics` (the spaced-em-dash
  mechanical backstop from earlier tonight's PR) before writing — but all
  four of this script's verify blocks compared the freshly re-fetched
  (and therefore already-normalized) DB value against
  `draft.<field>.strip()`, the RAW, pre-normalization string the model
  returned. Whenever a draft's raw text contained a spaced em dash, the
  two strings genuinely differed, so the comparison tripped false even
  though the write succeeded and the stored content was correctly
  formatted — the write was never the problem, only the check. Reproduced
  end-to-end with a mocked generator against a temp DB before writing any
  fix: the exact `VERIFY FAILED` message on spaced-em-dash content, with
  the actual stored row confirmed correct
  (`description`/`description_needs_verification=0`) despite the false
  failure. Fixed by wrapping the draft-side comparison in the same
  `normalize_voice_mechanics()` call in all four verify blocks — apples
  to apples against what's actually stored, not the model's raw output.
  **The already-"failed" tools from the recovery run needed no retry** —
  their content was already correct; only the log's status line was
  wrong. Confirmed by direct inspection rather than re-spending API cost
  regenerating already-good content.
- **`generate_tool_differentiation` joins the D1 content-exclusion rules
  (2026-08) — the gap the blast-radius spot-check bullet above flagged and
  deferred.** Investigated first: `generate_tool_differentiation` already
  resolved and used `voice_core` correctly (unlike Community profile's own
  gap, fixed earlier), and its em-dash/marketing-language rules were
  already in place — what was missing was the citation-tag investigation's
  D1 content rules (no vendor-reported stats/proof-points, no
  testimonials/review-scores/logos, no editor-facing asides), which
  Description and Agent taxonomy both got as part of that fix and this
  field never did, since it predates that fix and has no citations
  mechanism of its own (it's still plain JSON output — Citations-API
  grounding for this field stays deferred, unchanged by this pass) to have
  motivated including it. Two new rules (5-6) added to
  `_TOOL_DIFFERENTIATION_PROMPT`, reusing Description/Agent taxonomy's own
  reported-results/testimonials wording and editor-facing-address ban,
  adapted for this field's short 1-2-sentence JSON format: rule 5 bans
  vendor-reported stats/testimonials/review-scores/logos as the basis for
  the comparison **even when one already appears in the `description` or
  competitor context fed into the prompt** — a real path, since a legacy,
  not-yet-regenerated description can still carry a stat predating the
  Description-side fix, exactly what the sampled output turned out to be
  pulling from: a sampled Differentiation output for **Maxima** included
  "Scale AI's CAO reports closing two to three days faster at over 98%
  automation," attributing that vendor-reported result to Scale AI as a
  third-party comparison example named *within* Maxima's own text — Scale
  AI is not itself a Toolbox entry (confirmed: no tool by that name exists
  in the DB), so this is one finding on one tool (Maxima), not two; rule 6
  bans referencing "the description above"/"the competitor context"/the
  model's own research process. **Same disclosed limitation as the original citation-tag
  fix, verified rather than assumed**: this is preventative (prompt-level)
  only — nothing after the `json.loads()` call can recognize and strip a
  vendor stat the model decided to include anyway, since a legitimate
  comparison claim and an excluded marketing stat aren't mechanically
  distinguishable after the fact. `tests/citations_fixtures/
  differentiation_fixtures.py` + `tests/test_differentiation_content_
  exclusions.py` cover both sides of that, mirroring
  `enrich_sentinel_fixtures.py`'s own two-sided pattern: a clean/compliant
  fixture (description carries the stat, mocked response — a model that
  complied — doesn't) proving the pipeline stores exactly what a compliant
  model returns, and an old-bug-reproduction fixture (mocked response DOES
  include the stat) proving it still leaks through unfiltered if the model
  reverts, plus static prompt-content assertions that the new rules are
  actually present. **Verified live and closed (2026-08)**: Brian ran
  `regen_ai_drafted_fields.py --ids --only tools --sample 1` against
  Maxima — the same tool the original finding traced to — via
  `railway ssh`. Output came back clean — no vendor-reported stat, no
  editor-facing language, appropriately hedged where information (pricing)
  wasn't available — confirming the new rules hold on real model output,
  not just in the mocked fixtures above. **Known follow-up, not fixed by
  this PR**: Maxima's own live `competitive_differentiation` field still
  carries the pre-fix leaked stat (the prompt fix only governs future
  generations, never rewrites what's already stored) — tracked in issue
  #445 for a targeted re-run, alongside anything else surfaced during
  content review, rather than a one-off fix here. (An earlier draft of
  this note and of #445 mistakenly treated "Scale AI" as a second Toolbox
  entry needing its own re-run — corrected: it's the third-party example
  named inside Maxima's own leaked text, not a tool in the DB, so Maxima is
  the only entry #445 needs to cover unless content review turns up
  another.)
- **`scripts/regen_ai_drafted_fields.py` gained a `--field` flag (2026-08
  follow-up)** — requested to make #445's re-run genuinely cheap: a
  full-catalog `--only tools --field competitive_differentiation --apply`
  pass (no `--ids`) regenerates just Differentiation across all ~157 tools,
  instead of the original design's 3-fields-per-tool cost, and doubles as a
  sweep for any OTHER tool with a similar leaked-vendor-stat pattern that
  content review hasn't surfaced yet — not just Maxima. Repeatable and/or
  comma-separated (`--field description --field agent_taxonomy`,
  `--field description,agent_taxonomy` — both forms combine), normalized
  back to the real per-tool regeneration order regardless of command-line
  order. Omitting it regenerates all three fields, exactly as before this
  flag existed — a true no-op for every prior invocation, covered by
  `tests/test_regen_field_flag.py` (this script's first real committed
  test file — its earlier hardening rounds were smoke-tested by hand
  against a temp DB, per those bullets above, but never given a permanent
  test). Has no effect on Communities' single `community_profile` draft,
  which has no field concept to narrow — `--field` with `--only
  communities` prints a note and changes nothing, rather than silently
  doing nothing with no signal.

- **Stale "Verified by X on Y" stamp fix (2026-08) — regenerating a field
  now clears its narrative_review_log stamp, not just its needs_verification
  badge.** An investigation into tonight's `regen_ai_drafted_fields.py`
  recovery run found the "Verified by bmw on {old date}" line on a
  regenerated tool's edit page was a real, standing gap, not unique to the
  script: the stamp is derived entirely from `narrative_review_log`
  (`Library.get_latest_narrative_review`), written only by the four
  dedicated "Mark verified"/"Mark reviewed" routes — no Generate/Refresh/
  Save path, live or scripted, had ever touched it. The live admin UI's
  ordinary Generate+Save flow correctly sets `needs_verification=1` (so the
  amber badge does show, unlike tonight's script which forced `0`), but
  even there the OLD stamp kept rendering alongside the fresh badge until
  someone explicitly clicked Mark verified again — confusing, not just a
  one-off artifact of tonight's incident. Fixed with a new
  `narrative_review_log.superseded_at` column (nullable, `NULL` = still the
  live stamp) rather than deleting rows — the table's own schema comment
  already commits to being append-only, so `Library._supersede_narrative_review`
  marks every currently-live row for a field superseded instead, and
  `get_latest_narrative_review` filters `WHERE superseded_at IS NULL`; the
  full history stays intact in `list_narrative_review_log` for a future
  history view, only the *live* stamp reads as cleared. Wired into the same
  choke points that already write `needs_verification`/`needs_review`:
  `set_tool_agent_taxonomy_draft` clears unconditionally (it's used only
  for fresh AI drafts — hand-edits go through the separate
  `update_tool_agent_taxonomy`, untouched); `update_tool` (Description),
  `update_tool_differentiation`, and `upsert_community_profile` each gained
  an explicit `clear_description_verification_stamp`/`clear_verification_stamp`
  parameter, deliberately NOT inferred from the `needs_verification`/
  `needs_review` value passed alongside it. That distinction matters for two
  real reasons found during investigation: `regen_ai_drafted_fields.py`
  forces `needs_verification`/`needs_review=0` directly (its own pre-existing,
  documented bypass of the review badge — see the script's own docstring) on
  a call that is nonetheless still a fresh, not-yet-human-reviewed draft, so
  clearing had to be driven by an independent explicit flag (now `True` on
  all three of the script's write calls) rather than by the value `0`/`1`
  itself; and Community profile's `needs_review` can independently be set to
  `1` by an admin manually ticking a "needs review" checkbox with no fresh
  AI draft at all (a genuinely unrelated action), so inferring "clear" from
  `needs_review==1` there would have wrongly wiped a still-accurate stamp —
  the live submit route instead passes the already-computed
  `profile_ai_drafted` boolean directly. An ordinary hand-edit save (no
  fresh draft this session) is unaffected either way — it was never in
  scope, since the decision was specifically "whenever needs_verification
  flips 0→1 or is freshly set to 1 on a draft," and a hand-edit sets it to
  `0`. See `tests/test_stale_verification_stamp.py` for the full coverage
  (Library-layer clear/no-clear behavior for all four write paths including
  the script's own bypass pattern, the live route end to end, and Mark
  verified/Mark reviewed still working correctly — with a fresh, current
  stamp — immediately after a regeneration clears the old one) and
  ARCHITECTURE.md's `narrative_review_log` schema-table row for the full
  mechanism write-up.

- **Reader cleanliness pass (2026-08) — sponsor/ad/cookie-banner stripping,
  shared by every extraction path.** A backlog item framed as "Read Later
  saves aren't scrubbed like Archive saves" turned out to have a different
  root cause: Read Later's bare-metadata save was never the gap — every
  Read Later item is resolved through the same live-fetch extraction
  (`extract_reader_html`) an unsaved Feed item or a not-yet-backfilled
  Archive article uses. The real finding: **neither extraction path
  (`_extract_content`'s plain-text fallback, or `extract_reader_html`'s
  structured Reader HTML) had ever had any class/id-based content
  filtering** — both only recognize chrome by TAG NAME
  (`nav`/`header`/`footer`/`script`/...), so an ordinary
  `<div class="sponsor-block">` or `<div id="cookie-consent-banner">` rode
  straight through as ordinary article content — confirmed live via a saved
  OnlyCFO newsletter rendering a full Brex sponsor block inline, and
  confirmed the identical content would have rendered the same way via
  Archive, not just Read Later. Fixed once, in the shared extraction layer:
  `linklib/extract.py`'s new `strip_promotional_chrome(soup)` decomposes any
  element whose class/id/`data-testid`/`data-test-id`/`data-qa` matches a
  curated marker list (sponsor/advertisement/native-ad/newsletter-signup/
  subscribe-widget/cookie-banner/cookie-consent/gdpr/onetrust/...) —
  deliberately multi-character, word-ish tokens, never a bare word like "ad"
  that would also nuke "advice"/"gadget" — checked against element
  attributes only, never text content. Called before either path's own
  tag-name junk stripping. No change needed to `/save-later` or Read
  Later's schema. See ARCHITECTURE.md's matching Reader-cleanliness section.
- **Reader "expand"/distraction-free mode, corrected (2026-08).** The
  original build only shrank the middle `.rr-list-pane` to a 220px "sliver"
  on expand, leaving `.rr-rail` (the left nav rail) fully visible on
  desktop — confirmed live as a real bug against Instapaper's own reference
  screenshots, not the intended design: Instapaper's expand hides BOTH the
  rail and the article list completely, leaving just the centered reading
  pane with the sticky action bar and a top-left collapse-back arrow. Fixed:
  `.rr-shell.rr-focus-mode` now hides `.rr-rail`/`.rr-list-pane` (and their
  resize handles) outright, on every viewport — the sliver mechanism
  (`.rr-sliver`, `rrUpdateSliver`, its scroll-driven time-remaining tracker)
  is removed entirely. The existing `#rr-reader-expand` button in the sticky
  `.rr-reader-header` (already swapping between expand/collapse icons)
  doubles as the collapse-back affordance, since there's no sliver left to
  click through. See ARCHITECTURE.md's matching bullet.
- **Read Later content caching + manual refresh (2026-08 follow-up).**
  `/save-later` was a bare metadata insert with no fetch at all — every open
  of an unread item re-fetched live, nothing ever cached. Per Brian's
  explicit scope decision, `read_later` gained `content`/`content_html`
  columns and `/save-later` now fetches at save time via the same
  `fetch_page`/`extract_reader_html` pair `ingest_url` uses (best-effort,
  never blocks the save on a fetch failure — same durability-audit
  precedent). `Library.add_read_later`'s content write is CASE-guarded
  (never overwrites a good cache with an empty one from a failed re-fetch).
  `_resolve_reader_content` gained a Read Later cache tier (below an Archive
  article's own cache, above the live-fetch fallback) and an `is_read_later`
  flag. A new session-gated (not token) `POST /read-later/refresh` is the
  manual per-item "Refresh" button in the reader toolbar — re-fetches on
  click only (no automatic staleness detection), non-destructive on failure.
  See ARCHITECTURE.md's matching section and `tests/test_read_later_caching.py`.
- **Reader expand-mode icon + typography polish (2026-09).** Two small bugs on
  the Instapaper-style expand toggle (`#rr-reader-expand`), found via live-site
  screenshots, no schema/route change. (1) **Icon geometry**: `RR_ICON_EXPAND`/
  `RR_ICON_COLLAPSE` originally sat on Feather's stock NE/SW diagonal (arrows
  toward the top-right/bottom-left corners) — mirrored horizontally onto the
  NW/SE diagonal instead (top-left/bottom-right), per Brian's direct
  confirmation, keeping each icon's own outward (collapsed state)/inward
  (focus-mode state) direction unchanged — only the axis rotated. First pass at
  this bug swapped which icon renders in which STATE instead of touching the
  geometry; reverted once Brian clarified the direction/state mapping was
  already correct and only the diagonal axis was wrong — worth remembering
  that "icon points the wrong way" bug reports can mean either axis or
  direction, and they're not interchangeable fixes.
  (2) **Typography — the merged Feed/Archive/Read Later reader (`/read`) now
  follows ordinary sitewide typography: Outfit heading, DM Sans body. Settled
  after two rounds of correction, both flagged explicitly rather than
  silently, same precedent as the homepage "🚧 building" sticker mix-up
  elsewhere in this doc.** `.rr-reader-title` was hardcoded to `'Source Serif
  4',Georgia,serif` with no letter-spacing — a leftover never updated to
  Outfit when the standalone `/read/{article_id}` reader's own `.reader-meta
  h1` (already Outfit) and this merged reader's heading treatments diverged —
  fixed to `var(--font-head)` + `-.02em` tracking, matching BRAND.md's "Page
  title (H1)" row, and unchanged across both rounds below. `.rr-reader-
  body-text` went through two states before landing: **round 1** left it on
  Source Serif 4, reasoning that BRAND.md §3 explicitly sanctioned serif for
  exactly this surface ("No serif anywhere except the reader" / "Reader body |
  Source Serif 4") — live-site-correct at the time (BRAND.md really did say
  that), but wrong on what the page should look like: Brian confirmed on
  seeing the "after" screenshot that the body should match the title, not stay
  serif. **Round 2** moved it to `var(--font-head)` (Outfit) alongside the
  title, on Brian's stated reasoning that the site's own published
  thought-leadership content proves Outfit is the standard for body text —
  checked directly against a real Original Content article's `.oc-body p`
  (the same template Growth Engine Ratio/Sail Don't Row/NetSuite MCP render
  through, confirmed via `getComputedStyle` and a live screenshot) and found
  that claim didn't hold: body copy there is DM Sans, Outfit reserved for the
  `<h1>`. Flagged before shipping a sitewide implication rather than
  assuming the precedent was accurate; Brian's call once shown the
  discrepancy was to revert the reader body to `var(--font-body)` (DM Sans)
  to match the rest of the site, rather than either keep it on Outfit as a
  one-off exception or change body copy to Outfit sitewide (the third option
  offered, which would have been a much larger change touching `--font-body`
  or every body-copy call site). **Net result: the merged reader is no longer
  a documented typography exception at all** — Outfit heading + DM Sans body
  is just the site's ordinary pairing, applied here like everywhere else.
  Source Serif 4 survives ONLY on the standalone `/read/{article_id}`
  single-article view (a separate, older template, `_READER_CSS`/
  `.reader-body`, untouched by any of this), which is genuinely a narrower
  scope than "the reader" as BRAND.md used to describe it — **BRAND.md §3 was
  updated in the same PR** to match this final state. (This holdout didn't
  last — see the very next bullet, a same-day follow-up that retired it too.) The now-unused `<link>`
  that loaded Source Serif 4 specifically for the merged reader page was also
  removed (nothing on that page references the font any more; both Outfit and
  DM Sans are already loaded sitewide via `_page()`'s own font link). If a
  future report says "the reader's font doesn't match the site," check which
  of the two reader templates it's actually about, and verify any claimed
  precedent (a specific page's actual computed style) before generalizing it
  into a brand-standards change — BRAND.md was stale at round 1 of this fix,
  and an unverified precedent claim was wrong at round 2, so neither "the doc
  says so" nor "our other content proves it" was reliable on its own here.
- **Source Serif 4 retired sitewide (2026-09 follow-up) — a real, explicit
  standing rule from Brian, stated directly rather than derived from BRAND.md
  or precedent: only Outfit or DM Sans, ever, for content/reading typography.
  No third font, no exceptions.** Prompted by the reader-typography saga
  immediately above, which left one holdout — the standalone `/read/{id}`
  single-article view (`_READER_CSS`/`.reader-body`) — still on Source Serif
  4 after the merged reader settled on DM Sans. Explicitly scoped before
  building: this rule covers content/reading fonts only, NOT the decorative
  Caveat (sticker badges)/Permanent Marker (nav+footer wordmark) layer, which
  Brian confirmed stays untouched — a separate design language, not a content
  font. Fixed: `_READER_CSS`'s `body{}` rule and its Google Fonts `@import`
  both moved from Source Serif 4 to DM Sans (`.reader-meta h1` was already
  Outfit, unchanged); the admin brand-showcase page's third type specimen
  (which existed solely to show off the serif) removed outright, since no
  font on the site renders in it any more; and — the part that makes this a
  real, mechanically-enforced retirement rather than just an unused
  declaration — `linklib/brand_check.py`'s `ALLOWED_FONTS` allowlist dropped
  "Source Serif 4" entirely, so `test_brand_standards.py` now fails a future
  reintroduction the same way it already fails Inter/Lora/Arial/etc., rather
  than silently permitting it back in. BRAND.md §3 rewritten to describe
  "two content families" as the standing rule, not three, with the full
  three-pass history (serif → Outfit → DM Sans, twice, once per reader
  template) kept as prose for context. No schema/route change.
- **MCP server, Phase 1 (2026-09) — a read-only remote MCP server at `/mcp`,
  mounted in-process (same app, same deploy, no second service), with its
  own user-bound token auth and three admin-gated schema-introspection
  tools.** The goal, stated up front: let Brian use CFO Navigator from
  Claude the way he already uses the admin web UI, starting with the
  smallest useful slice — schema introspection — rather than building
  every capability's MCP surface at once. Full write-up (mount mechanics,
  the two independent auth checks, the mcp.bmweis.com hosting carve-out) is
  in ARCHITECTURE.md's "MCP server — `/mcp` (Phase 1)" flow section; the
  points worth repeating here:
  - **A new `api_tokens` table, not a reuse of `LINKLIB_SAVE_TOKEN`.** The
    legacy flat token carries no identity — reusing it for MCP would have
    silently bypassed FP&A Buddy's and the matchmaker's per-user dollar
    caps the moment a later phase adds those tools, the exact
    quiet-degradation pattern this project's own standing rules ban. Every
    `api_tokens` row resolves, on every verify, to a real `users.id` and
    that user's CURRENT role (not one snapshotted at mint time); only a
    sha256 hash of the plaintext is ever stored. No admin UI yet —
    `scripts/mint_api_token.py` (mint/list/revoke, human-run via
    `railway ssh`) is the only way to manage tokens this phase.
  - **Fails closed, with two independent checks, not one.** A transport-
    level middleware 401s an unauthenticated `/mcp` request before any MCP
    protocol handling starts (the build brief's own requirement); each
    tool ALSO independently re-derives its caller from the live request it
    was handed and re-verifies the token itself, never trusting the
    middleware's decision. There is no default identity anywhere in this
    path — a missing/invalid/revoked token or a non-admin role calling an
    admin-gated tool is always refused, never defaulted to admin.
  - **`mcp.bmweis.com` is a second custom domain, deliberately DNS-only
    (unproxied) in Cloudflare** — the same reasoning that already sent the
    daily backup cron around Cloudflare's Bot Fight Mode applies to MCP
    traffic (automated, non-browser callers by definition). The existing
    canonical-host-redirect middleware got a dedicated carve-out: `/mcp`
    and `/health` serve directly on that host and the raw Railway origin
    (a DNS-outage fallback); every other path 301s to the apex rather than
    shadow-mirroring the whole site on the MCP subdomain.
  - **Mount path is exactly `/mcp`, not `/mcp/mcp` and not a redirect** —
    a real gotcha, not a style choice: FastMCP's default internal
    streamable-HTTP route is itself `/mcp`, so a naive `Mount("/mcp", ...)`
    doubles the segment, and the alternative (override the internal path
    to `/`) produces a `307` on every call instead. Fixed by mounting the
    sub-app at an empty prefix, registered as the very last route in
    `webapp/app.py`, so every literal route still matches first and only a
    genuinely unmatched request ever reaches FastMCP's own router.
  - **`scripts/mcp_server.py` (the existing stdio server wrapping
    `GET /api/search` for Claude Desktop/Code) is untouched** — its
    retirement, if it happens, is a later decision, not bundled into this
    phase.
  - **Deliberately out of scope this phase**: any Toolbox/Communities/
    Library/Feed/Buddy/matchmaker tool, any write capability, any change
    to cap logic or existing routes beyond the redirect carve-out, and an
    admin UI for tokens.
- **MCP server, production 421 fix (2026-09) — a bug the Phase 1 build's own
  local end-to-end testing structurally could not have caught.** The very
  first live authenticated call after `mcp.bmweis.com` went live returned a
  bare-text `421 Invalid Host header` — traced (by reading FastMCP's own
  constructor, not guessed) to `FastMCP.__init__` auto-enabling its
  DNS-rebinding-protection Host/Origin allowlist whenever `host` is left at
  its default `127.0.0.1`, restricted to `127.0.0.1`/`localhost`/`::1` —
  which is exactly, and only, what every local test connects to AND sends
  as its `Host` header, so the one allowlist that happened to work locally
  is the one that broke in production. Fixed by always passing an explicit
  `TransportSecuritySettings` (`webapp/mcp_server.py`'s `build_mcp()` gained
  `extra_allowed_hosts`/`extra_allowed_origins` params) — protection stays
  on, just with `mcp.bmweis.com` and the raw Railway origin added, derived
  from the SAME `_MCP_HOST`/`_LEGACY_HOSTS` constants the canonical-host-
  redirect carve-out already uses so the two host lists can't drift apart.
  Three new regression tests in `tests/test_mcp_server.py` prove this with
  a real server: a request that physically connects to `127.0.0.1` but
  carries `Host: mcp.bmweis.com` (or the Railway origin) now succeeds — the
  actual shape of a production request — while a genuinely unrecognized
  `Host` still gets rejected, confirming the allowlist is real and
  restrictive, not DNS-rebinding protection quietly disabled. **This
  session's sandbox has no network path to the live Railway origin at all**
  (its egress policy denies the host outright), so the "confirm with a real
  deployed curl" step this fix required could not be run from this session
  directly — flagged to Brian to run and confirm once this deploys, rather
  than silently skipped or claimed done without evidence. Full trace in
  ARCHITECTURE.md's "MCP server, production 421 fix" section and
  `webapp/mcp_server.py`'s `build_mcp` docstring. **2026-09 update: confirmed
  working in production** — three subsequent MCP phases (3, 4, 5) built and
  shipped successfully on top of a live `/mcp`, which wouldn't have been
  possible if this fix hadn't held.
- **MCP server, connector-vs-curl 401 mismatch (2026-09, resolved) — a
  report, past the 421 fix above: Claude's own connector got 401 from
  `_mcp_auth_gate` on the SAME token a `curl` call got 200 with** (Railway
  logs confirmed the token matched; no 421s, so the earlier fix held).
  Nothing in `verify_api_token` distinguishes *why* a token failed — it
  collapses "unknown hash"/"revoked"/"inactive user" into one `None` by
  design, so there was no way to tell from the 401 alone which branch was
  actually firing for the connector. Diagnosed with **temporary** logging
  added to `_mcp_auth_gate` (now removed — see the cleanup/hardening PR
  below), which traced it to real format variance in what was actually
  arriving: one connector attempt sent a bare token with no `"Bearer "`
  scheme at all, another sent `"Bearer"` + token with the separating space
  lost in transit (`header_len=49, has_space=False`) — the old gate
  required an exact `Bearer <token>` shape and 401'd both.
  Same PR also fixed a real, unrelated gap the same investigation
  surfaced: `/.well-known/oauth-*` was 301-redirecting to the apex on both
  `mcp.bmweis.com` and the raw Railway origin (the documented `/mcp`
  DNS-outage fallback — same failure mode, same fix, extended once
  flagged) — this server has no OAuth layer at all (see mcp_server.py's
  auth-model docstring), so an MCP client's RFC 8414/9728 discovery probe
  should get a clean same-origin 404 on either host, not a redirect that
  chases it into Cloudflare's Bot Fight Mode on bmweis.com. Scoped
  narrowly to that one path prefix, on those two `/mcp`-serving hosts only
  — `www.bmweis.com` (a `_LEGACY_HOSTS` entry that never serves `/mcp`)
  deliberately does not get this exemption.
- **MCP server, cleanup/hardening PR (2026-09) — the permanent fix for the
  mismatch above, plus three smaller post-launch items.** (1) The temporary
  diagnostic logging (`_mcp_auth_logger`, `_mcp_diagnose_token_miss`, and
  the WARNING calls in `_mcp_auth_gate`) is removed now that the mismatch
  is diagnosed and fixed. (2) In its place,
  `webapp.mcp_server.bearer_token_candidates(header)` is a small, shared,
  never-logging parser used by both `_mcp_auth_gate` and each tool's own
  `_caller_from_ctx` re-verification: it tries the trimmed header AS the
  token first (the bare-token case), and only on a miss strips a leading
  `bearer` scheme (case-insensitive, tolerant of the separating space going
  missing) and retries — a non-bearer scheme like `Basic ...` never matches
  that strip, so it's rejected the same as before, just via the token
  failing to verify rather than an explicit scheme check. **The
  credential-leak lesson the temporary logging surfaced is preserved as a
  standing constraint, not just history**: this helper returns only token
  candidates, never a parsed-out `scheme` — a header with no space at all
  puts the ENTIRE header (the credential itself) into whatever a naive
  `partition(" ")` would call `scheme`, so nothing in this codepath has a
  `scheme` value available to accidentally log in the first place. (3) Exact
  `/mcp` path matching was verified end to end (`_mcp_auth_gate`, the
  canonical-host-redirect carve-outs, `_McpOnlyMount`) — already
  `path == "/mcp" or path.startswith("/mcp/")` everywhere via the shared
  `_mcp_path` helper, not a loose `startswith("/mcp")`, confirmed by
  inventory rather than assumed; a scanner probe to `POST /mcp-builder` had
  been captured by the auth gate in prod logs before this was verified, now
  pinned by a regression test that such a path falls through to normal
  404/405 handling instead. (4) `list_tables` labels FTS5's/sqlite-vec's
  extension-internal shadow tables (`articles_fts_data`/`_idx`/`_docsize`/
  `_config`, `articles_vec_rowids`/`_chunks`/`_vector_chunks00`/`_info`)
  with `"shadow_of": "<virtual table name>"`, derived dynamically from
  whichever virtual table names are actually present rather than a
  hardcoded suffix list, so a caller doesn't misread their row counts as
  independent content. (5) `sample_rows` caps each string cell at
  `max_cell_chars` (default 500, `<=0` disables it) with a visible
  truncation marker — a production sample of 25 `articles` rows had come
  back at 523KB uncapped. See ARCHITECTURE.md's matching section for the
  full write-up.
- **MCP server, Phase 2 — retrieval (2026-09) — `get_rows` plus an
  `offset` on `sample_rows`, closing a real reachability gap the original
  three introspection tools left open.** `sample_rows`' two fixed windows
  (head: `ORDER BY rowid ASC LIMIT n`; tail: `ORDER BY rowid DESC LIMIT n`,
  reversed — each capped at 25, no offset, no cursor) stop meeting once a
  table passes 50 rows, leaving `total - 50` rows permanently unreachable
  through MCP by any parameter combination — silently, with no error.
  Confirmed live, not hypothetical: `settings` had 55 rows, and
  `htib_before_copy`/`htib_after_copy` (the `/how-this-is-built` page
  copy) sat in the 5-row dead middle — unreadable through MCP, which is
  what forced a recent copy change to be matched from a screenshot rather
  than source. `ai_surfaces`/`original_content`/`thought_leadership` were
  all under 50 rows at the time and so unaffected today, but only by row-
  count luck. Two additions, both admin-role-only, same tier as the three
  Phase 1 tools: **`sample_rows` gained `offset: int = 0`** — `offset=
  0,25,50,...` walks a table of any size in order with no gap and no
  overlap, negative values clamped to 0, omitting it a pure no-op (`OFFSET
  0` is identical to no clause) so every existing caller is unaffected;
  **new `get_rows(name, where_column, where_value, n, max_cell_chars)`**
  fetches by exact column match instead of position, so one specific row
  is reachable in a single call regardless of table size — `where_value`
  is always parameter-bound (never interpolated) and passed as text, with
  SQLite's own type affinity still matching it correctly against an
  INTEGER column; `name`/`where_column` are validated against the live
  schema exactly like the existing tools (`_validate_table`/
  `_validate_column`, both extracted so `sample_rows`/`get_rows`/
  `describe_table` can't drift into validating differently); a
  no-match query is a normal empty result, never an error. Both tools now
  share one `_apply_cell_truncation()` helper for the `max_cell_chars`/
  `truncated` behavior, rather than each implementing the truncation loop
  separately. **The 25-row cap itself is deliberately untouched** — the
  fix is reachability, not bigger payloads. **Deliberately out of scope
  this phase**: a discovery registry for non-schema content surfaces (the
  `ai_surfaces`/`original_content`/homepage/about copy `settings` keys) —
  Phase 0C's own investigation into that question found discovery is
  better solved by writing the surface inventory into project
  documentation than by building a tool for it, so this phase is retrieval
  only. **The route-render tool (Phase 3 of the *investigation* track, not
  to be confused with this same-numbered MCP *tool* Phase 3 below) is
  killed outright as of 2026-09, not parked** — #570 (`get_rows`) already
  solved the actual motivating problem (reading editable page copy at any
  table size), the residual need (seeing served markup/computed layout) is
  already covered by Brian pasting view-source or devtools output when he
  needs it, and the cost side (rendering arbitrary routes as an
  authenticated admin, with no completed GET-route side-effect audit and
  `/read/{article_id}` already known to fire a live external fetch on
  load) was real and never closed. The GET-route side-effect audit that
  would have gated this tool is dropped along with it — it had no other
  purpose. See ARCHITECTURE.md's matching section and `tests/test_mcp_server.py`'s
  Phase 2 section (the literal `settings`-at-55-rows paging case and the
  dead-middle-row `get_rows` lookup) for the full write-up and coverage.
- **MCP server, Phase 3 (2026-09) — six read-only Toolbox/Communities
  content tools (`search_software`, `get_software`, `search_communities`,
  `get_community`, `compare_software`, `compare_communities`), a new
  `webapp/mcp_toolbox.py` registered onto the same `/mcp` FastMCP instance
  the Phase 1 introspection tools live on.** Deliberately **not**
  admin-only like those three — any valid, active, unrevoked token may
  call these six (`webapp.mcp_server.require_caller`, same fail-closed
  resolution minus the role check), mirroring the fully-public web pages
  they replicate; the caller's role only changes what's visible *within* a
  result (a pending field's badge text — "under review" vs. "unverified,
  visible to visitors" — via `linklib.gates`, never whether the tool runs
  at all). `get_software`/`get_community` build their own gated dicts over the
  full profile-page field text; `compare_software`/`compare_communities` call
  `linklib.compare.build_software_compare`/`build_communities_compare`
  completely unmodified (confirmed neither takes a role parameter — badge
  text is applied afterward, per field) and reject rather than silently
  truncate a request outside the existing 4-tool/3-community cap. Compare
  Phase 2's cached AI summary is included cache-hit-only — a free
  `Library.get_compare_summary` lookup against the same cache key the web
  route computes; `generate_compare_summary` is never called from here, so
  an agentic conversation can't spend against the shared daily cost cap.
  See ARCHITECTURE.md's "MCP server — Toolbox & Communities content tools
  (Phase 3)" section for the full write-up and `tests/test_mcp_toolbox.py`
  for the gate-enforcement coverage. **PR 8 (2026-09)** added
  `search_benchmarking`/`search_books` (closing the Toolbox's last real
  MCP coverage gap — benchmarking resources and book recommendations,
  both reading the same `benchmarks` table via `section='benchmarking'`|
  `'books'`, no `compare_benchmarking` since neither has comparable fields
  or a Compare page to mirror) and renamed `search_tools`/`get_tool`/
  `compare_tools` to `search_software`/`get_software`/`compare_software`
  for consistency with the Communities naming — old names gone, not
  aliased. See ARCHITECTURE.md's Phase 3 PR 8 note for the full write-up.
- **MCP server, Phase 4 (2026-09) — Library (Archive) search
  (`search_archive`, `get_article`) + Feed browse/search (`browse_feed`,
  `search_feed`), a new `webapp/mcp_library.py`.** Wraps existing logic
  completely unmodified: `linklib.agent.retrieve()` (hybrid FTS5+vector,
  RRF-merged) and `Library.get_article`/`get_article_by_url` for Track A;
  `linklib.feed.get_feed_items()` and `linklib.agent.retrieve_feed()` for
  Track B. **Re-verified, not inherited, auth model**: `/read`/
  `/read/{id}`/`/api/read-article` all gate on admin specifically
  (`_is_authed`), confirming — rather than assuming — the "Library is
  admin-only" planning note; all four tools require the admin role via a
  new public `webapp.mcp_server.require_admin`. (`GET /api/search`, an
  older route wrapping the same `Library.search()`, is member-tier-gated —
  a likely-unintentional survivor of the Phase 1 restructure, left alone.)
  Feed has no DB-backed item history (30-min in-memory cache only,
  confirmed against the real `/read?view=feed` route) — hence two tools,
  not one: `browse_feed` (chronological, optional category filter) and
  `search_feed` (keyword-relevance, `retrieve_feed`'s existing
  overlap-count scoring). `search_archive` (renamed from `search_library`
  in PR 8, 2026-09, to pair with `search_feed`) returns compact hits (an
  excerpt, `is_own_content` included) with `get_article` as the full-text
  companion — the published-content ingestion PR's `is_own_content` flag
  needs no separate "own writing" tool, since it's just an `articles`
  column. See ARCHITECTURE.md's "MCP server — Library (Archive) search &
  Feed browse/search (Phase 4)" section for the full write-up (including
  the disclosed, uncapped query-embedding cost on a non-empty
  `search_archive` call) and `tests/test_mcp_library.py` for the
  auth-model regression coverage.
- **MCP field parity (2026-10) — `program_details`, `warm_intro_available`,
  and a registry that forces an MCP decision for every profile column.** The
  Phase 0 report found "Program details is missing from the MCP" was wrong:
  the key-facts band and the Additional benefits group were already served.
  The real gaps were CPE as one flat string with no review state,
  and no Warm Intro signal. `get_community`/`compare_communities` now return
  `program_details` (`label`, `value`, optional `note`; CPE also `state` and
  `badge`, gated by the whole-profile `needs_review` exactly like the page),
  `key_facts` stays as the flat alias, and `get_software` returns
  `warm_intro_available` (a boolean, never the vendor's email or name).
  `linklib.compare.MCP_PARITY` classifies every column on `tools`,
  `communities`, `community_profiles` and `tool_feature_links` as `mcp:`,
  `mcp-admin:`, `admin-only:`, `excluded:`, `retired` or `internal:`;
  `tests/test_mcp_field_parity.py` fails on a column with no entry, on an
  `mcp:` path the output lacks, and on any `admin-only`/`mcp-admin` value or
  key reaching a member token. **`excluded` is not `admin-only`**: logos,
  screenshots and their capture dates are public on the web and deliberately
  not served over MCP (Brian's decision, presentation assets); the guard
  does not treat them as a leak, and a separate test keeps them unserved
  until their registry line is changed on purpose. `tools.suite_note` is
  registered `admin-only: stored, not rendered, decision pending`. **Not in
  this PR, issue text ready to file (owner Brian):** (1) "Community profile
  Details card is hand-built; move into `compare.py`" — the public page
  assembles its Program details rows itself in `webapp/app.py` and shares only
  the labels with Compare and MCP; trigger: the next change to Program
  details. (2) "`suite_note` is stored and rendered nowhere: render or
  remove" — the NetSuite placeholder seeded by
  `scripts/seed_feature_taxonomy.py` has no reader. See ARCHITECTURE.md's
  "MCP field parity" paragraph in the Phase 3 section.
- **MCP server, Phase 5 (2026-09) — FP&A Buddy & Matchmaker proxy tools
  (`ask_fpa_buddy`, `ask_matchmaker(kind, ...)`), a new `webapp/mcp_qa.py`.**
  Neither `/ask` nor the two matchmaker routes' identity resolution
  (`_require_member`/`_current_user_id`) understands an MCP bearer token —
  an HTTP self-call would run as an unmetered, unaudited `user_id=None` —
  so these two tools call `answer_question()`/`linklib.matchmaker`'s
  `_answer()` **in-process**, passing the MCP-resolved `user_id` explicitly.
  Doing that required extracting the cap-check/history-rebuild/recording
  orchestration that used to live inline in the three HTTP routes into two
  new shared modules, `webapp/ask_orchestrator.py::run_ask` and
  `webapp/matchmaker_orchestrator.py::run_matchmaker` — `POST /ask` and both
  `.../find/chat` routes now call these themselves too, held to the same
  behavior-identical discipline as the radical-transparency gate-extraction
  PR: `tests/test_ask_conversations.py`/`tests/test_ask_feedback.py`/
  `tests/test_software_matchmaker.py`/`tests/test_communities_matchmaker.py`
  all pass **unmodified** against the refactored routes. A capped turn
  returns the orchestrators' `{"capped": true, ...}` dict as a normal
  (non-error) MCP result, matching `/ask`'s own HTTP-200-on-capped design.
  **Auth is a third tier** — `require_caller` (Phase 3's "any valid, active
  token, no role restriction"), not `require_admin` — since `/ask` itself
  only requires any signed-in member, and gating these tools to admin-only
  would preempt a possible future non-admin MCP tier. **One tool for both
  matchmaker kinds** (`kind: "tools"|"communities"`), not two, since
  `linklib.matchmaker._answer()` is already one shared function
  differentiated by an internal kind string. **A real, disclosed
  conversation-continuity asymmetry**: `ask_fpa_buddy` is keyed purely on
  `user_id` (seamless across web/MCP for the same user); `ask_matchmaker`
  additionally requires a session match, so an MCP caller gets a synthesized
  stable per-user session key (`f"mcp:user:{user_id}"`) — a conversation
  started via MCP resumes via MCP, but not from a web session, same
  pre-existing limitation the web's own cross-browser case already has. See
  ARCHITECTURE.md's "MCP server — FP&A Buddy & Matchmaker proxy tools
  (Phase 5)" section for the full write-up and `tests/test_mcp_qa.py` for
  cap/history/audit-trail coverage plus the unchanged-HTTP-route regression
  proof.
- **Compare Redesign, Phase 1 (2026-09) — both Compare pages rebuilt on a
  new `linklib/compare.py` serializer; the shared contract Compare Phase 2
  (AI summary generation) and MCP Phase 3 (compare tools) will also build
  on, not just this page's own HTML.** Fixed three real bugs Brian's
  review found: dead `[1]`/`[2]` citation markers (now the same
  `entity_citations` + `_citations_list_html` "Sources" chip list the
  profile pages already render — no new mechanism), flattened bulleted
  text in Software's compare cells (missing `white-space:pre-wrap`,
  matched to what profile pages already do — a real markdown-to-HTML
  renderer is deliberately NOT built here, it's scoped as its own
  immediate follow-up PR since it'd be a site-wide rendering change), and
  an orphaned section header (every section — Key facts, Description,
  AI / Agent involvement, Bottom line, Competitors/Similar communities,
  Communities' 4 themed groups — now shares one `.cc-section` band, not
  just Agent taxonomy). Added: a Key facts band with shared-vs-unique tag
  chips (solid seafoam = every compared entity has it, outline = only this
  one does), and a `-webkit-line-clamp` excerpt (~4 lines, approved over a
  fixed character count) on the full untruncated text (**superseded
  2026-10: Compare now shows full text, see the next bullets**). Communities'
  Compare collapsed its old flat 11-field list into the same 4 themed
  groups (`compare.COMMUNITY_PROFILE_GROUPS`) the profile page already
  uses — that constant, and `community_geo_line()`, moved out of
  `webapp/app.py` into `linklib/compare.py` so the profile page and
  Compare can't drift apart. Every section renders for every entity, even
  fully empty (mirroring the profile pages' "nothing ever disappears"
  standard), through `linklib/gates.py` exactly as before — this PR
  extends `gates.COMPARE_EMPTY_LABELS` with four new keys for sections
  Compare didn't previously render, but never touches the module's actual
  decision logic. See ARCHITECTURE.md's matching bullet for the full
  write-up.
- **Compare Redesign Phase 1, pre-merge follow-up (2026-09) — tags moved
  out of Key facts into the header row, and a real mobile fix.** Tags
  (shared-vs-unique chips, same visual treatment) now render directly
  under each entity's name in the header, not in the Key facts band — a
  category tag is an identity fact, not a "key fact" alongside
  Region/Access/Cost. Software's Key facts band had nothing left once tags
  moved out, so it's retired outright; Communities keeps its own,
  tag-free. **The mobile fix targeted the wrong element on the first
  pass, caught only by comparing a real before/after-scroll screenshot**:
  the original ask was "a sticky label column," but this table has no
  per-row label COLUMN — every row's field name lives in a full-width
  `.cc-section` band (the fix for the desktop orphaned-header bug), so
  making the blank leftmost `.cc-label` cell sticky pinned nothing. Fixed
  by sticking the band's own title text instead (`.cmp-sticky-label`, an
  inner `<span>` inside the wide band `<td>`) — see **Mobile
  swipeable-table pattern** below, the reusable version of this fix. A
  new swipe-hint affordance (two-headed-arrow icon + muted "Swipe to
  compare" text, deliberately not styled like this page's own bold navy
  "Full profile →" link) shows once per visitor, dismissed on first
  horizontal scroll via a plain `localStorage` flag — this codebase's
  existing convention for a client-only "seen it once" preference, not a
  new mechanism. See ARCHITECTURE.md's matching bullet for the full
  write-up.
- **Compare shows full field text, never clamped (2026-10) — replaces the
  Compare Redesign Phase 1 Step 0 decision.** Phase 1 clamped every Compare
  narrative cell to 4 lines with `-webkit-line-clamp` (`compare.EXCERPT_LINE_
  CLAMP`). Brian's standing rule is that a profile, a comparison and an MCP
  response never cut a field off, and a "Show more" control still hides text
  by default, so the answer is no collapse at all: the clamp, `.cmp-clamp`
  and `EXCERPT_LINE_CLAMP` are removed, and every character of every field
  renders on both Compare pages (software Description, AI / Agent
  involvement and Bottom line; community Bottom line and all the group
  fields). Measured on the live Abacum and Datarails text before the change,
  the clamp was a 87px box over 824-3,448px of content, hiding every `[n]`
  marker after the fourth line while all five Sources chips stayed, so
  markers and chips disagreed; they now agree by construction. The Compare
  Sources list is uncapped too (the profile page caps at 5): a sixth marker
  would otherwise have no chip. Nothing may hide text on these cells: no
  `overflow`, `max-height` or line-clamp on `.cmp-text` or the cells around
  it, and `tests/test_compare_full_text.py` measures `scrollHeight ==
  clientHeight` in real Chromium at 1280px and 390px with a 3,000-character
  field on both pages (skips where no Chromium exists, as CI does). The
  cost is tall rows: Datarails' agent text makes one row 984px at 1280px and
  2,767px at 390px. That was accepted, and no collapse was added to fix it.
  The only clamps allowed anywhere are the two directory cards
  (`/tools/software`, `/tools/communities`), each with a "Full profile" link.
- **Never cut off text, outside Compare (2026-10): finishes what the Compare
  full-text change started.** Standing rule: a profile, a comparison and an MCP
  response never cut a field off. Three fixes shipped together, from an audit
  that was an agent inventory with the main claims spot-checked (every line
  number was re-verified against source before changing anything).
  (1) **Profile Sources are uncapped.** Software Description, Agent taxonomy
  and the Community profile showed only the first 5 Sources chips
  (`_citations_list_html(..., cap=5)`), so a `[6]` marker in the text had no
  chip, the same bug Compare had. The three `cap=5` calls and the helper's
  `cap` parameter are gone (no other caller used it; the admin views and
  Compare never passed one). `tests/test_profile_sources_uncapped.py` is the
  fail-first coverage: six markers in the text, six chips, each chip's number
  beside its own title.
  (2) **Past answers render in full.** `_render_cited_answer` lost its
  `truncate` parameter: `/ask/history` (single cards and follow-up turns,
  was 500 characters) and the Buddy past-questions section (was 600) used to
  cut with a trailing "…" and no way to see the rest. No full-answer page
  exists to fall back on (`/ask/conversations*` are JSON for the Buddy page's
  JavaScript), so the rows themselves now carry the whole answer. Measured
  first, per the brief: 200 history rows at about 10,000 characters each was
  2.36 MB and about 0.2 s to render (over the 2 MB line), so `/ask/history`
  now paginates, 25 conversations per page, newest first, with "Newer" and
  "Older" links (`?page=N`, clamped). That is not truncation: every answer on a
  page is whole. Page one of the same fixture is 0.32 MB. `list_ask_questions`
  still reads the user's most recent 200 turns, unchanged.
  (3) **`stop_reason` is measured, not shown.** Nothing used to record when a
  model answer was cut off by `max_tokens`. `ask_questions` and
  `matchmaker_questions` each gained `stop_reason TEXT NOT NULL DEFAULT ''`
  (idempotent migration; existing rows read `''`, so counts only start after
  the deploy), written from the existing record calls, which the MCP
  `ask_fpa_buddy` and `ask_matchmaker` tools reach through the same
  orchestrators. Enrichment and generation calls have no table of their own
  and are run by hand in batches, so each logs one WARNING line per
  `max_tokens` stop (`linklib/stop_reason.py`, `stop_reason=max_tokens
  call_site=enrich.<function>`), with no column added to `enrichment_cost`.
  No `max_tokens` value changed (lowering one caused a truncation regression
  before), and there is no admin page, visible marker or retry. To count,
  paste over `railway ssh` after about a week:

  ```sql
  SELECT model, COUNT(*) FROM ask_questions WHERE stop_reason='max_tokens' GROUP BY model;
  SELECT model, COUNT(*) FROM matchmaker_questions WHERE stop_reason='max_tokens' GROUP BY model;
  ```

  Compare against `SELECT model, COUNT(*) FROM ask_questions WHERE stop_reason<>'' GROUP BY model;`
  for the rate. Enrichment counts come from searching the Railway logs for
  `stop_reason=max_tokens`. See `tests/test_stop_reason.py`.
  **Exempt from the rule, recorded here so a later audit does not re-open
  them:** feed item summaries cut at 300 characters at ingest (a third-party
  excerpt, not our text); Buddy "Recent conversations" labels (a click opens
  the full transcript); `search_archive` excerpts (admin-only, marked with
  "…") and citation titles capped at 250 characters (low impact). The only
  clamps allowed anywhere else are the two directory cards, each with its
  "Full profile" link.

- **Compare Redesign Phase 2 (2026-09) — a 1-3 sentence AI-generated
  overlap/contrast summary above both Compare tables, cached permanently
  and capped by a shared daily dollar budget.** Purely additive on top of
  Phase 1 — no existing cell-rendering function, and no `linklib/compare.py`/
  `linklib/gates.py` logic, is touched. Generation reuses the exact
  `CompareEntity`/`CompareField` data the page already built (no
  re-fetch), is voice-governed via `require_voice_setting` like every
  other `generate_*` call, and never recommends one entity over another —
  only describes the shape of a difference. Cached in `compare_summary_cache`,
  keyed by the compared entity set plus a content hash of what was
  actually summarized; the footnote's unverified-content disclosure is
  computed live at render time, decoupled from that cache key, so a
  verify-only action can't miss the cache but still shows an accurate
  disclosure. A cap hit renders a labeled note instead of failing the
  page; every other unavailability reason omits the block silently.
  Feedback is a public, no-token stored-submission mechanism
  (`compare_summary_feedback`) reviewed by hand at
  `/admin/compare-summary-feedback` — no automated action. See
  ARCHITECTURE.md's matching bullet for the full write-up.

**Mobile swipeable-table pattern** (established by the follow-up above,
reusable for any future wide table on a narrow viewport): when a table's
"row label" is a full-width band (`colspan` across every column) rather
than a narrow first column, `position:sticky` belongs on an inner
`<span>` wrapping the band's TEXT, not on the band `<td>` itself or on any
per-row label cell — the `<td>` already spans the whole row and doesn't
need to move; it's the text inside it that needs to stay in the viewport
while the row scrolls under it. Pair with a one-time swipe-hint affordance
(icon + muted text, `localStorage`-dismissed) styled deliberately unlike
any bare-arrow link convention already on the page, so a passive hint
never reads as something to tap.

- **Real Markdown/List Rendering for Narrative Fields (2026-09) — the
  follow-up PR flagged (and deliberately deferred) by Compare Redesign
  Phase 1's own "flattened markdown" finding above.** `webapp/
  markdown_render.py`'s `render_narrative_markdown()` is now the single
  shared mechanism for tool Description/Agent taxonomy/Bottom line and
  every community profile group field (including the community's own
  Bottom line) — real `<ul><li>`/`<strong>`/`<p>` in place of the old
  `_esc(text)` + `white-space:pre-wrap` approximation. It reuses
  `python-markdown` (already a dependency via `original_content`) but with
  a genuinely different, more restrictive config than
  `_render_original_content_markdown`'s: input is HTML-escaped first (these
  fields are AI-drafted, not Brian-authored-trusted the way
  `original_content.body_md` is), and the `Markdown` instance has every
  block/inline processor deregistered except paragraphs, lists, and bold/
  italic — no headers, blockquotes, links/images, code, or raw HTML, since
  no generation prompt this serves is ever asked to produce any of those.
  Lives in `webapp/`, not `linklib/`, on purpose — `linklib/gates.py`'s
  HTML-free boundary is enforced by import path specifically so nothing
  HTML-producing is reachable from `linklib` (a future MCP tool safety
  concern), and this module's whole job is producing HTML.
  **Compare's own cells are deliberately NOT rendered through
  this** — `_cmp_populated_field_html` renders plain `_esc()` text in full
  (no clamp since 2026-10); the real rendered version is one click away
  via "Full profile →". `linklib/compare.py` and `linklib/gates.py` are
  both untouched by this PR. See ARCHITECTURE.md's matching bullet for the
  full write-up and `tests/test_markdown_render.py`/`tests/
  test_narrative_field_markdown.py` for the coverage.

- **Tool edit page fixes (2026-09) — three independent small bugs, no shared root
  cause beyond "found in the same live-use session."**
  1. **Short summary textarea silently rejected typed input** for any tool whose
     `summary` already exceeded the field's `maxlength="400"` — a real, reproduced
     bug, not a guess. The one-time migration that introduced the `summary` column
     (`linklib/db.py`'s `UPDATE tools SET summary=description WHERE summary='' AND
     description!=''`) copied the full, uncapped `description` into any empty
     `summary`, with no length limit anywhere server-side; `description` has since
     grown into 8-12 sentence write-ups, so a tool never redrafted via "Generate
     summary" since can carry a legacy `summary` well past 400 chars. HTML
     `maxlength` blocks *appending* once a field's value is already at or over the
     cap — the cursor still blinks, nothing typed lands, and nothing in the markup
     said why. Fixed by rendering `maxlength="400"` only when the stored value
     already fits inside it; an admin can always edit/trim an over-length legacy
     value, and the guardrail returns once it's saved back under 400.
  2. **The public profile page's "Bottom line" callout had no edit-page field
     literally named "Bottom line"** — confirmed a direct 1:1, no-transformation
     mapping from `tools.competitive_differentiation` (the profile page renders
     `tool['competitive_differentiation']` verbatim under the header "Bottom
     line{badge}"). Pure label mismatch: the edit-page field was still labeled
     "Competitive differentiation." Renamed the edit-page label to "Bottom line" —
     no schema, route, or display-logic change. First instance of a class of
     internal/external name mismatch worth watching for elsewhere over time (not
     swept for here — only this one was in scope).
  3. **Homepage screenshot caption read "(not yet captured)" over a live image** —
     the literal string exists in exactly one place in the codebase,
     `_screenshot_slot_caption()` (`webapp/app.py`), used only by the public
     Software/Communities **profile** pages' screenshot card — not the actual
     `/tools/software/{slug}/edit` route, which already had correct wording
     ("Manually set—no capture date") for this same state. `_screenshot_slot_caption`
     shows this text whenever a screenshot URL is populated but `screenshot_captured_at`
     is empty — the deliberate, normal result of a hand-pasted URL
     (`Library.update_tool_screenshot_url` explicitly clears `captured_at` on a
     manual paste), not a rare or broken state. So every manually-set screenshot —
     completely normal — read as "not yet captured" on the profile page, right next
     to the live image and the "Edit" admin link, which is almost certainly what
     got called "the edit page" here. Fixed the wording to match the edit page's own
     phrase exactly, so both surfaces describe the state identically — same class of
     bug as the `seed_tools.py` description-sync incident (a caption/string not
     reflecting live state), just one navigation hop from where it was reported.
- **Tool Profile Layout: Sidebar Consolidation (2026-09) — the Software profile
  page now uses the same "reference sidebar" pattern the Community profile
  page already established, replacing the Aug 2026 UI pass's arrangement.**
  Brian's live review of the production Abacum page found two problems with
  that arrangement: Competitors (typically a short list) was paired with
  Key features (typically a long one) in a two-column band, leaving visible
  whitespace under the shorter column and stranding Description as a
  disconnected full-width block below both; and the category tag sat alone
  between the admin review pills and the Bottom line callout, orphaned with
  no clear grouping. Investigation (Step 0) confirmed the Community profile
  page already solves this shape — a wide main column carrying continuous
  narrative content, alongside a narrower sidebar carrying independently-
  scannable reference/lookup cards (Details, Categories, Similar
  communities) — so this PR names that the **"reference sidebar"
  pattern** and brings Software in line with it rather than inventing a
  second layout. Software's version: **main column** — Bottom line ->
  Description -> Agent taxonomy, one continuous flow (`main_col` in
  `tools_software_profile`); **sidebar** — screenshot -> Key features ->
  Competitors, each independently scannable (`sidebar_col`). The hero
  (logo/name/tags/subhead/review-status/actions) is now full-width above
  the two-column band — screenshot no longer sits beside it the way it
  used to (that was hero-paired-with-screenshot; it now opens the sidebar
  instead). Category tags moved to sit directly under the tool name, above
  the subhead — mirroring where Compare already places entity tags
  (`_cmp_tag_chips_html`, right under the linked name) — since a category
  is an identity fact about the tool, not action-row furniture. Software
  has no shared-vs-unique tag distinction the way Compare does (a solo
  profile page has nothing to compare against), so it keeps the existing
  solid-seafoam `.tp-cat-pill` treatment rather than adopting Compare's
  outline variant. The Bottom line callout's spacing rule changed
  alongside the move: it used to carry its own `margin-top/bottom:22px`
  (designed for its old spot between the category pills and the action
  row); now that it's the first card in `main_col`'s `tp-col-stack`, the
  stack's own `gap:22px` already produces even spacing against its
  neighbors, so a self-margin there would have double-spaced it — dropped
  in favor of the shared mechanism every other card in the column uses.
  Mobile (`@media(max-width:800px)`, `.tp-band` collapses to one column):
  DOM order is main column first, sidebar second — narrative content
  before reference content — which was confirmed as the right call in the
  Step 0 investigation and needed no JS reordering (unlike the homepage's
  own mobile-only DOM-order fix elsewhere in this doc, which needed one
  because its grid used explicit `grid-column`/`grid-row` placement;
  here plain DOM order was already correct). Content, gating, and
  admin-only elements are unchanged — this is a layout-only PR: no
  `linklib/gates.py` changes, no schema changes, no changes to Compare
  pages or the markdown renderer. Verified live at desktop (1400px) and
  mobile (390px) viewports, logged in and out, on both a fully-populated
  tool and a sparse/empty one — all four combinations screenshot-checked
  before merge.

- **Edit-page label alignment (2026-09) — nine internal edit-page labels renamed
  to match their public/external display labels, from a dedicated label-
  consistency sweep** (same precedent as "Competitive differentiation" ->
  "Bottom line" on the tool edit page, earlier in this doc). Label-string-only,
  no schema/logic/field-name changes: tool edit page's "Core competition" ->
  "Competitors" (matching the profile page's own `<h2>` — this specifically
  reverts Phase P's edit-page-only "Competitors" -> "Core competition" rename
  above, restoring the match to the public label it was originally meant to
  mirror; the stale help text claiming the profile page shows "Closest
  competitors" — a rename that never actually happened — was corrected to say
  "Competitors" too) and "Feature taxonomy" -> "Key features" (matching the
  profile page's Key features card — label only, the governed-feature-link
  mechanics underneath are untouched); the app-screenshot-source-URL field's
  "Product" label -> "App screenshot" (matching the profile page's screenshot
  card); and, on the Community profile edit page, "Anti-fit" -> "Who should
  skip it", "Level"/`seniority_band` -> "Who it targets", "Verdict" ->
  "Bottom line", "Cost vs. value verdict" -> "Cost vs. value", "Founded year"
  -> "Founded", and "CPE" -> "CPE eligible" — all six matching the exact
  labels `linklib/compare.py`'s `COMMUNITY_PROFILE_GROUPS`/the public profile
  page's Details card already use. Three admin-collected-but-never-publicly-
  rendered community fields found during the same sweep (Stage focus, Jobs
  program, Individual-or-Team) are a separate, deliberately out-of-scope
  question (why collect data that's never shown), not a label mismatch —
  untouched by this PR.
- **Admin Users page: table redesign + MCP user setup docs (2026-09) — `/admin/users`
  joins the standard admin-table convention, making it explicitly three-for-three
  with `/admin/tools/software` and `/admin/tools/communities`.** `/admin/users` was
  the last major admin list still on a bespoke one-card-per-user layout, with
  per-user cap editing/role/active/delete tucked behind a card-header "Manage"
  toggle. Rebuilt on the same shared machinery the other two tables already use —
  `_admin_column_picker_html`/`_admin_sort_filter_toolbar_html`/
  `_admin_row_data_attrs`, the `_ADMIN_BULK_EDIT_JS`/`_ADMIN_SORT_FILTER_JS` shared
  scripts, and the `.admin-table-responsive` mobile-card breakpoint — checkbox
  select, a column picker (Name/Email/Last login/FP&A Buddy cap/Matchmaker cap,
  all optional; Username/Actions always visible, same "always-visible name +
  actions" convention as the other two tables), sortable/filterable columns
  (Username/Last login/Created; Role/Status scalar filters; a live search box),
  and a "Delete selected" bulk action. This is a layout change, not a feature
  change — every existing per-user action (profile edit, Ask/Matchmaker cap
  override, password reset, role toggle, active toggle, single-row delete, the
  pending-password-reset notice) works exactly as it did before, just reached via
  a per-row "Manage" button instead of a card's own header button. Two deliberate
  departures from the other two tables' exact mechanics, both approved up front:
  (1) **no bulk "Edit selected"** — Software/Communities' bulk-edit assumes one
  categorical field to set across every selected row (categories, a checkbox
  flag); Users has no such field that's safe to bulk-set, since role/active both
  carry the last-active-admin lockout guard, which is inherently a per-row
  question, not a batch one — forcing a bulk version risked exactly the kind of
  silent partial-failure mode this codebase avoids everywhere else (see "No dead
  data"/the standing "never silently fail" principle above), so it's skipped
  entirely rather than built unsafely. (2) **the shared `_ADMIN_BULK_EDIT_JS`
  delete-confirm functions (`openDeleteSelectedPanel`/`renderDeleteSelectedPanel`/
  `submitBulkDelete`) were NOT reused for the new "Delete selected" action** —
  they hardcode the `/admin/tools/{tableKey}/...` URL prefix and a
  Software/Communities-specific response shape (`d.tools`, competitor-reference
  warnings), neither of which fits Users. Bespoke inline functions
  (`openUsersDeleteSelectedPanel`/`renderUsersDeleteSelectedPanel`/
  `submitUsersBulkDelete`, plus a generalized `toggleManage`) live in the route's
  own `<script>` block instead — deliberately not touching the shared constant
  Software/Communities still rely on. The generic pieces of that shared script
  (`updateBulkButton`, `selectAllRows`, the column-picker functions, the sort/
  filter functions) ARE reused as-is, since they're already table-agnostic
  (keyed entirely by `tableKey`). New `POST /admin/users/bulk-delete-check`/
  `/admin/users/bulk-delete` generalize the single-row Delete button's
  `_is_last_active_admin` guard to a batch: rather than silently dropping or
  silently allowing a selection that would zero out active admins, the preview
  reports every such row back as explicitly blocked (with the rest of the
  selection still deletable), and the commit route re-checks the guard fresh on
  every iteration against live state rather than trusting the preview's
  snapshot — deleting one selected admin can change whether the next one is the
  last active admin. **Manage panels render grouped below the table, not nested
  as a second `<tr>` under each row** — a deliberate choice to stay clear of the
  shared sort/filter script's row-reordering: `applySortFilter` physically
  `appendChild`s `tr[data-name]` rows to re-sort them, and a companion detail row
  (no `data-name` of its own) would either get silently left behind at its old
  position or need real changes to the shared, already-relied-upon sort/filter
  mechanism to keep it paired with its owner row. Clicking "Manage" scrolls the
  matching panel into view, so the panel-below-the-table layout doesn't cost
  the admin any context. Also new: a collapsible "How to set up a new MCP user"
  disclosure block, reusing the exact `<details>`/`<summary>`/`.disclosure-caret`
  markup `/admin/tools/communities`' own "How this works" block established —
  a `_MCP_USER_SETUP_HTML` constant (same "static reference content, not
  DB-backed" precedent as `_COMMUNITIES_REFERENCE_HTML`) walks through creating
  the account, raising the Ask/Matchmaker cap, minting a personal token via
  `scripts/mint_api_token.py` over `railway ssh`, and adding the connector in
  Claude (`https://mcp.bmweis.com/mcp`, header `authorization`, value
  `Bearer <token>`). Per Step 0's own review, the copy never hardcodes the
  default cap dollar amounts — both are admin-editable via this same page's
  "Save default" forms, so a literal `$5`/`$2` in the instructions would drift
  the moment either default changes; a regression test
  (`test_mcp_setup_copy_does_not_hardcode_default_cap_amounts`) pins this. No
  schema changes, no changes to cap logic or default cap amounts, and token
  minting stays exactly as railway-ssh-only as before — this PR only changes how
  the page is laid out and adds documentation for a flow that already existed.
  See ARCHITECTURE.md's "`/admin/users` joins this convention" bullet (under
  the Software admin column-picker/bulk-edit/bulk-delete section) for the
  full technical write-up, and `tests/test_admin_users_table.py` for the
  regression coverage, including the rendered `<script>` block's own
  `node --check` validation (this page's bulk-delete/manage-toggle script is
  inline in the route, not a module-level `*_JS` constant, so it isn't
  covered by `webapp.checks.script_syntax_problems()` — same standing
  caveat as the Reader's own inline script).

- **Encourage password change (2026-09) — a dismissible nudge, both via email
  and via a login-flow banner, not a hard block.** Investigated first, per the
  standing gate: `password_reset_requests` (the "pending password-reset
  requests" concept) turned out to be an unrelated, already-fully-built
  self-service mechanism (`/forgot-password` → `/reset-password`, tokenized,
  emailed) with no connection to account creation or admin-set passwords;
  outbound email infrastructure (`linklib/email_utils.py`, Gmail REST API,
  already used for welcome emails and self-service resets) already existed in
  full, so no new dependency/service decision was needed — confirmed rather
  than assumed, and reported before any build. Two decisions Brian made
  explicitly, both flagged rather than picked unilaterally: (1) a **dismissible
  reminder banner**, never a hard block that would gate every private route
  behind a forced change — a real, considered trade-off, not the default;
  (2) the admin's existing "Reset password" action
  (`POST /admin/users/{id}/password`) gets the same email nudge account
  creation already sent, closing the loop so "encourage via email" covers
  both account creation AND an existing account's admin-triggered reset, not
  just the former. Mechanism: `users.password_change_recommended`
  (`create_user`'s default = `True`; set back to `True` by an admin reset;
  cleared only when the account holder sets their own password — self-service
  `/reset-password`, or the new session-only `GET/POST /change-password` form)
  drives `_password_change_nudge_html`'s banner, wired into just the two pages
  a post-login redirect actually lands on (`homepage()`, `admin_page()`) —
  deliberately not threaded through `_page()`'s ~250 call sites, since a nudge
  only needs to appear once, on the very next page after login. Dismissal is
  client-only (`localStorage`, keyed per user id — same convention as the
  Compare page's swipe-hint), so nothing server-side tracks "seen it." The
  email itself is a new `send_admin_password_reset_email` (mirrors
  `send_welcome_email`'s shape: name/username/temp_password/login_url),
  admin-editable like every other outbound template via
  `_email_template_registry()`/`/admin/emails`. See ARCHITECTURE.md's "Auth:
  three tiers, one cookie" section and the `users` schema-table row for the
  full write-up, and `tests/test_password_change_recommended.py` for the
  regression coverage (flag defaults/set/clear across all four call sites,
  the admin-reset email, the in-session change-password form, and the
  banner's presence/absence including the break-glass admin login, which has
  no `users` row and so can never carry the flag).

- **Surface Hidden Community Profile Fields (2026-09) — Stage focus, Jobs
  program, and Individual or team join `linklib.compare.
  COMMUNITY_PROFILE_GROUPS`; a real hero/screenshot spacing bug fixed in
  the same PR.** Step 0 investigation found these three admin-editable
  Quick-facts fields (`stage_focus`/`jobs_program`/`team_or_individual`)
  had never rendered on the Community profile page or Compare — the last
  three Quick-facts fields with no public home, per `webapp/app.py`'s own
  comment above `_COMMUNITY_PROFILE_GROUPS` (every sibling field already
  had one: a Details-card row, folded into another field as texture, or a
  `COMMUNITY_PROFILE_GROUPS` entry). **A live query against production
  (this session had `/mcp` admin-tool access) found real, substantive
  content already stored for 36 of 40 communities** — not the near-empty
  state the edit-page's own `placeholder=` attribute text ("Placeholder,
  not yet researched or weighted," removed in this PR since it's
  misleading once the field renders publicly) might have suggested. So
  this shipped mostly as "surface content that already exists," not "build
  empty-state scaffolding for an unpopulated field," though the three-state
  standard (verified/pending/empty) still holds for the 4 communities with
  a real gap in one of the three. Placed by semantic fit, not to balance
  group sizes: **Stage focus** joins "Who it's for" (a company-stage
  targeting fact, a natural peer of the existing seniority-band "Who it
  targets" entry); **Jobs program** joins "What you get" (a member benefit,
  same category as Resources included); **Individual or team** joins "Cost
  & structure" (a membership-structure/purchasing fact, closer to Business
  model's "how this sustains itself" than to who it's personally for). No
  new gating logic — `_narrative_field`/`gates.field_state` handle all
  three exactly like every other group field, off the same whole-profile
  `needs_review` flag `build_communities_compare` already reads once per
  community.

  **Same PR fixed a real spacing bug this build surfaced** (flagged mid-turn:
  "too much spacing between the visit, compare, edit buttons and the
  bottom line box... look at a software profile for comparison"): the
  Community profile page's hero (name/tags/actions) and its screenshot
  card used to sit side by side in their own two-column `.tp-band`
  (`top_band`), so the Bottom line callout directly below it couldn't
  start until that whole grid row finished — gated behind the (usually
  much taller) screenshot column's height, not the hero column's actual,
  much shorter, content height. Software's own Tool Profile Layout: Sidebar
  Consolidation pass (above) had already solved the identical problem for
  the Software profile page — hero full-width above a single `.tp-band`,
  screenshot moved into the sidebar column — so this mirrors that exact
  pattern rather than inventing a new one: `hero_text` now renders
  full-width (no band, no screenshot beside it), and `screenshot_block`
  opens the sidebar column of the one remaining band (renamed
  `content_band`, from `lower_band`), alongside Details/Categories/Similar
  communities — the same reference-sidebar grouping this page already used
  for those three, just extended to the screenshot. The CSS gained
  `.tp-band:first-of-type{margin-top:20px;}`, matching Software's own
  override, since there's now only one `.tp-band` on the page. On mobile
  (`<=800px`, unchanged breakpoint), the sidebar now falls after all the
  main-column narrative content in DOM order rather than right after the
  hero — the same "main column first, sidebar second" mobile order
  Software's Sidebar Consolidation pass already established, not a new
  decision; verified with a real 390×844 Playwright session (no horizontal
  overflow, narrative-first stacking).

  **Standing rule this PR enforces, worth restating for future field
  additions**: every admin-editable field must render on at least the
  profile page (and Compare, where applicable) — no field is ever
  collected-but-never-shown. See `linklib/compare.py`'s own comment on
  `COMMUNITY_PROFILE_GROUPS` for the placement reasoning, ARCHITECTURE.md's
  matching bullet for the full technical write-up, and `tests/
  test_surface_hidden_community_fields.py` for the regression coverage
  (group placement, verified/pending/empty on both the profile page and
  Compare).

- **Community profile cleanup, PR 2a (2026-09) — one edit page, eleven retired
  fields, five groups of three, and a citation rule that follows the text.**
  `/tools/communities/{slug}/edit` now holds the listing AND the profile;
  `GET/POST /admin/tools/communities/{id}/profile` are gone (no redirect, same
  cutover convention as every admin route move). Two tables stay (`communities`,
  `community_profiles`); merging them was considered and rejected as a bigger
  data-model change than the page merge needed (Option A).
  - **Retired, frozen in the schema, never dropped**: `community_profiles.`
    `founded_year`, `event_style`, `seniority_band`, `platform_type`,
    `team_or_individual`, `primary_purpose`, `meeting_format`, `stage_focus`,
    and `communities.demographic`, `cost_note`, `notes`. Nothing renders,
    collects, generates or reads them. `linklib/community_profile.py` is the one
    list (`RETIRED_PROFILE_FIELDS`, `RETIRED_COMMUNITY_FIELDS`) plus the
    `PROFILE_LIMITS` and CPE vocabulary. `Library.upsert_community_profile` and
    `Library.update_community` treat a retired argument of `None` as "leave the
    stored value alone", so the merged page's save can never blank one; a test
    seeds every retired column, saves the page, and asserts nothing moved
    (`tests/test_community_profile_merged_edit.py`, shown failing first against a
    plain full replace). `communities.format`, `low_confidence` and the sponsorship
    columns stay.
  - **Five groups of three**, named once in `linklib/compare.py`
    (`GROUP_TARGET_AUDIENCE` and its four siblings) and read by the admin form,
    the public page, Compare and MCP: Target audience, Member experience,
    Economics, Key points, Additional benefits. Programming is the old
    `format_reality`; Trade-offs to weigh is the old `public_criticism`; Bottom
    line (`verdict_summary`) sits in Key points on the admin page, and stays the
    first section on Compare and MCP.
  - **Public page**: no subhead, category chips under the name, no Description or
    Categories card, Bottom line callout on top, Key points, then one Additional
    benefits card. The Details card's Format row is `communities.format` only.
    Founded and Cost detail are gone from Compare key facts and MCP.
  - **Directory card blurb is the Bottom line**, joined in by one query
    (`Library.list_communities_for_directory`); a community with no profile shows
    the muted empty blurb, never an error. `search_communities` matches name,
    Bottom line and Ideal member (was name, demographic, notes).
  - **`communities.notes` is no longer synced from the seed list**, so boot opens
    no seed-disagreement item for a frozen column. The matchmaker context lost
    its Who it's for, Cost detail, Notes and Founded lines and still carries
    Ideal member.
  - **Limits**: every live prose field has a soft target and a hard max in
    `PROFILE_LIMITS`; each max is above the longest value in production when it
    was set (a test pins that). Over the max is refused whole, naming the limit,
    and every limit is checked before any write. CPE is a dropdown (Not assessed,
    Yes, No, Unclear); a stored qualifier such as "Yes (NASBA sponsor)" survives a
    save while the leading word is unchanged, and legacy outliers are coerced to
    their leading word.
  - **Citation invariant (community profile only)**: the shared set is never
    cleared while at least one `[n]` marker remains in any profile field, and is
    cleared once none remain. The profile is grounded on one page, so every marker
    points at that page and editing one cited box can't orphan the others (the Vena
    shape). Fresh citations arriving with a save replace the whole set; markers with
    no stored and no fresh set change nothing. Replaces the tool-style "clear when
    the text changed" rule for this one entity (`_community_citation_action`);
    Agent taxonomy and Description keep the PR #640 rules. Shown failing first: on
    the pre-change route, editing one of two cited boxes cleared the set.
  - **Confidence**: a tracked field whose text you changed by hand saves as NULL
    ("Not yet assessed"), since the model never judged that text.
  - **Restore previous** (page-level, vanilla JS): one snapshot of the boxes, the
    hidden citations and the drafted-this-session state, taken right before a
    Generate overwrites them. Per-group Generate and per-group Restore are PR 2b,
    not built yet.
  See ARCHITECTURE.md's matching section.

- **Admin menu default-state + badge coverage (2026-09) — every admin-hub group
  now defaults to collapsed on load, reversing the 2026-08 "Inbox always open,
  any group with a nonzero badge also starts open" fix; plus a real Software
  badge undercount closed.** Brian's report ("Inbox, Toolbox, Software render
  expanded on load") traced to `admin_page()`'s `_group_html()`, which forced
  `open=True` for `gname=="Inbox"` or any group whose aggregate badge was
  nonzero — not a leftover/debug artifact, a deliberate, documented fix for a
  real incident (a pending LiveFlow/Liveflow name-duplicate pair and a Runway
  pair went unnoticed inside a collapsed, two-levels-deep Software sub-group,
  even though detection and badge counting were both correct — see the
  git-blame'd 2026-08 comment this reverts). Investigated and flagged to
  Brian before touching it, since honoring the literal ask would revert that
  incident fix; his explicit call — confirmed twice, after a session restart
  lost the first answer — reverses it anyway: **badges are the review-inbox
  signal on their own, and auto-expanding on top of that duplicated the same
  information as an intrusive default rather than a genuinely different
  safeguard.** The incident's own root cause is still guarded against by a
  different, pre-existing mechanism that didn't need to change: a group's
  badge only ever hides once its `<details>` is OPENED
  (`.admin-group[open] .group-badge{display:none;}`), so a collapsed-by-
  default group with something pending still shows its badge number, unhidden,
  right on the summary row — the exact spot the incident says to look. Single
  centralized fix (`_group_html`'s own `open=` parameter, now always `False`),
  not three per-section patches, since Inbox/Toolbox/Software all render
  through the same function. **Re-verified specifically for the nested case**
  the incident comment describes (Software's badge, two disclosure levels
  deep inside CFO Toolbox) now that every level defaults collapsed: a native
  `<details>` hides its ENTIRE body — including a nested `<details>` and that
  nested group's own badge span — the moment its parent is collapsed, so
  Software's own badge can't literally "show through" CFO Toolbox once
  CFO Toolbox itself is closed. What actually satisfies the incident's intent
  is that CFO Toolbox's own group-level badge already aggregates every href
  nested inside it (`toolbox_hrefs` in `admin_page()`, which folds in
  `software_hrefs`), so CFO Toolbox's own `<summary>` — visible regardless of
  its own open/closed state — already reflects Software's pending count
  without Software's `<details>` ever needing to be open. **Separately, a
  real, concrete badge gap**: the Software admin card counted only
  `count_pending_tools()` (the approval queue), while Communities' equivalent
  card already combines `count_pending_communities() +
  count_communities_needing_review()` — an asymmetry, not a deliberate scope
  choice; `count_tools_needing_review()` (the tools-side mirror of that same
  method, built for the "Tools whole-record profile signoff" feature) already
  existed and simply was never wired into `webapp.tasks.open_task_counts()`.
  Fixed by mirroring Communities exactly — `count_pending_tools() +
  count_tools_needing_review()`, summed, same as Communities' own two terms.
  **An earlier draft of this fix instead folded the three per-field
  `*_needs_verification` flags (Description/Agent taxonomy/Competitive
  differentiation) into one deduped `count_tools_needing_attention()` query,
  reasoning that they're rarely independent of `needs_review` (a fresh draft
  that sets one also auto-sets the whole-record flag in the same write, so
  summing separately would double-count) — reverted per Brian's explicit
  call**: those three flags have their own follow-on scope defined
  separately and are deliberately left out of this PR, not folded in under a
  different name. `count_tools_needing_review()` keeps its own other callers
  unaffected (the admin list's "Needs review" filter count, the
  review-status pill's "(n/3)" breakdown). FP&A Buddy feedback
  (`ask_feedback`) and the three per-field flags both stay deliberately out
  of the badge system for now — no reviewed-state column exists on
  `ask_feedback` at all (re-confirmed during this investigation), and the
  field flags have their own separately-scoped follow-on.
  See `tests/test_task_badges.py` for the regression coverage (every
  top-level and nested group collapsed on load even with a real pending
  badge; the badge itself still visible in that collapsed state; the nested-
  nested case specifically; the mirrored count and its deliberate exclusion
  of the per-field flags).

- **Admin menu badge coverage, follow-up (2026-09) — the per-field
  `*_needs_verification` flags this PR left out are folded in after all,
  superseding that deliberate exclusion.** The earlier bullet's `count_
  tools_needing_attention()` — combining the whole-record `needs_review`
  signal with any of the three per-field flags into one deduped, per-tool
  count — was built once, then reverted in favor of the plain `count_
  pending_tools() + count_tools_needing_review()` sum described above, per
  an explicit instruction at the time to defer the field flags to their own
  follow-on scope. That follow-on scope is this PR: `count_tools_needing_
  attention()` is resurrected verbatim (the original query needed no
  changes — a `SELECT COUNT(*) FROM tools WHERE ...` over one row per tool
  is already a per-tool dedup, no `DISTINCT` needed) and wired into
  `webapp.tasks.open_task_counts()` in place of `count_tools_needing_
  review()`. `count_tools_needing_review()` itself is untouched and keeps
  its own other callers (the admin list's "Needs review" filter count, the
  review-status pill's "(n/3)" breakdown) — only the Software card's badge
  wiring changed. FP&A Buddy feedback (`ask_feedback`) is the one signal
  still deliberately left out of the badge system — still no reviewed-state
  column on that table at all. See `tests/test_task_badges.py`'s
  `test_open_task_counts_dedupes_per_field_verification_flags` for the
  regression coverage, including the multi-field-on-one-tool dedup case.

- **Badge dedup safety, Software + Communities (2026-09) — both badges were
  a sum of two separate counts, and a single row satisfying both conditions
  at once got counted twice; both now read one dedup-safe count apiece
  instead.** For Software this was a real, live bug, not a hypothetical:
  `add_tool()` defaults `needs_review=1` for every brand-new tool regardless
  of caller (admin add-form, public `/tools/submit`, seed scripts — none
  passes 0), and `approve_tool()` only ever flips `approved`, never touches
  `needs_review` or the three per-field flags — so every tool sitting in the
  approval queue already also had `needs_review=1`, and the old `count_
  pending_tools() + count_tools_needing_attention()` sum double-counted it
  every time. `count_tools_needing_attention()` now also ORs in
  `approved=0` alongside its existing conditions (still one query over the
  `tools` table, one row per tool, no `DISTINCT` needed), and is the SOLE
  count behind the Software badge — `count_pending_tools()` is no longer
  summed alongside it (the method itself is untouched, just has no current
  caller). For Communities the overlap is possible but not automatic: the
  `needs_review` flag lives on `community_profiles`, a separate table with
  no row at all until a profile is actually drafted, and the public
  submission route (`add_community()` alone, no profile generation) never
  creates one — but `GET`/`POST /admin/tools/communities/{id}/profile`
  never checks the community's `approved` status either, so an admin
  drafting/saving a profile for a still-pending submission (reachable only
  by direct URL — no link from the Pending submissions table) would land in
  exactly this state. New `Library.count_communities_needing_attention()`
  (a `LEFT JOIN` against `community_profiles`, since most communities —
  pending ones especially — have no profile row and an `INNER JOIN` would
  silently drop them; `COUNT(DISTINCT c.id)` is defensive rather than
  strictly required, since `community_profiles.community_id` is a 1:1
  primary key with no fan-out risk) replaces the old `count_pending_
  communities() + count_communities_needing_review()` sum the same way.
  **No shared helper between the two** — investigated and deliberately
  passed on: tools' fix is a single-table OR, Communities' needs a join
  across two tables with different row-existence semantics, and a generic
  "table + condition list" abstraction can't express that difference
  cleanly without becoming its own small query builder for a two-call
  audience. Both entities' individual count methods
  (`count_pending_tools()`, `count_tools_needing_review()`, `count_pending_
  communities()`, `count_communities_needing_review()`) are all untouched
  and keep their own other callers — only the two badges' wiring in
  `webapp.tasks.open_task_counts()` changed, from a sum to a single call
  apiece. See `tests/test_task_badges.py`'s
  `test_open_task_counts_does_not_double_count_pending_and_needing_review_tool`/
  `..._community` for the direct regression coverage.

- **Admin grid reorder (2026-09) — Thought leadership and CFO Toolbox moved
  from the top of the right column to the left column, below Inbox; Brand,
  voice, and content and System shifted up to fill the vacated top-right
  slot.** Display-order only, confirmed before building: the two-column
  split (`admin_page()`'s `_LEFT_GROUPS` set, iterated in `_ADMIN_GROUPS`'
  own declared order) was already a simple set-membership check per group
  name, not a hardcoded per-column layout — extending `_LEFT_GROUPS` from
  `{"Inbox"}` to `{"Inbox", "Thought leadership", "CFO Toolbox"}` was the
  entire fix, since `_ADMIN_GROUPS`' own iteration order already places
  those three (then Brand/voice/content, then System) in exactly the
  desired top-to-bottom sequence within each column. The mobile single-
  column stack (both columns concatenate below the `1024px` breakpoint)
  needed no separate change either, for the same reason. See `tests/
  test_admin_nav_phase6.py`'s `test_admin_grid_reorder_renders_in_the_new_
  order` for the regression coverage.

- **FP&A Buddy feedback: reviewed toggle (2026-09, Phase 3) — `ask_feedback`
  gets the same manual "Mark reviewed" pattern Community gaps already has,
  closing the last deferred badge gap from the earlier admin-badge-coverage
  round.** Investigated first, per the standing gate: `community_gap_
  submissions.reviewed` is a plain `INTEGER NOT NULL DEFAULT 0` boolean
  (not a timestamp), flipped by a bespoke per-row toggle button/route
  (`toggle_community_gap_reviewed`, `POST /admin/inbox/community-gaps/{id}/
  toggle-reviewed`) — not a reusable macro/component, just markup local to
  that one page's `_card()` closure, confirmed by reading it rather than
  assumed. `community_gap_counts()["unreviewed"]` and the toggle read/write
  the exact same column, so the stat tile and the row action can never
  drift out of sync. `ask_feedback` was list-only (no detail view) with no
  reviewed concept of any kind — `ask_feedback_counts()` only ever grouped
  by `rating`. Built by mirroring Community gaps exactly, not inventing a
  parallel shape: a migration-added `ask_feedback.reviewed` column (same
  name/type/default — the schema comment explicitly says why this isn't a
  `viewed_at` timestamp instead), `toggle_ask_feedback_reviewed()` (same
  flip-in-place `UPDATE ... SET reviewed = 1 - reviewed`),
  `count_unreviewed_ask_feedback()` (same shape as `community_gap_counts()`'s
  own `unreviewed` bucket), and `list_ask_feedback()` gained an optional
  `reviewed` filter alongside its existing `rating` one (combined with AND,
  same convention `list_community_gap_submissions()` already uses). The
  toggle component itself is copy-pasted markup, not factored into a shared
  helper — Phase 0 found it was bespoke to begin with, and two call sites
  isn't a pattern worth abstracting yet; the "Reviewed"/"New" pill styling
  is reused verbatim (same hex-free `--seafoam-wash`/`--alert` tokens) so
  the two pages read as visually consistent regardless. `/admin/fpa-buddy/feedback`
  gained a second filter select (Reviewed: All/Unreviewed/Reviewed,
  combinable with the existing rating filter in one GET form) and a 4th
  stat tile ("Unreviewed", deliberately all-time not month-scoped — same
  choice Community gaps' own Unreviewed tile makes, since "how much is left
  to triage" isn't naturally a monthly figure); the stat-card grid switched
  from a hardcoded `repeat(3,1fr)` to `repeat(auto-fit,minmax(130px,1fr))`
  per the standing CSS-Grid-blowout lesson (Phase P) rather than hand-fixing
  the column count. `webapp.tasks.open_task_counts()` wires in
  `count_unreviewed_ask_feedback()` for `/admin/fpa-buddy/feedback` — no longer in
  the deferred bucket; the comment there was updated to say so. `ask_feedback_
  counts()` itself is untouched and keeps its own caller (the 3 per-rating
  "this month" stat tiles). See `tests/test_ask_feedback.py`'s new Phase-3
  section for the full regression coverage (toggle flip, filter, badge
  wiring, the route's admin-only gate, and the rendered page carrying the
  new badge/button/stat-tile markup).

- **Admin "Completeness" filter (2026-09) — one more scalar filter on both
  `/admin/tools/software` and `/admin/tools/communities`, for finding
  profiles missing content or a screenshot ahead of a manual review pass.
  Consume-only against `linklib/gates.py`: no new gating concept, no
  `gates.py` changes — it reuses the exact same field set and the same
  strip-then-check emptiness test `gates.field_state` already applies (per
  `linklib/compare.py`'s field list, the authoritative one, not a guess at
  an old memory of it): tools' Description/Agent taxonomy/Bottom line
  (`competitive_differentiation`)/Competitors; Communities' `verdict_summary`
  plus the 17 `COMMUNITY_PROFILE_GROUPS` fields/Similar communities. Plus a
  direct, non-gate `screenshot_url` presence check on both — deliberately
  **not** `app_screenshot_url` too, since the app/product screenshot is a
  genuinely optional curated extra most records never get (CFO Toolbox
  Phase E), so flagging its absence would make the filter useless.
  Renders as a single "Missing"/"Complete" dropdown (`_tool_completeness`/
  `_community_completeness` in `webapp/app.py`), the same `scalar_filters`
  convention every other AND-matched admin-table filter (Cost band, Access,
  ...) already uses — a precomputed `data-completeness` attribute per row,
  read by the existing shared `_ADMIN_SORT_FILTER_JS`, no new JS. Two new
  bulk-query `Library` methods avoid an N+1 per row, same precedent as
  `community_profile_quality_flags()`: `tool_competitor_counts()`/
  `community_competitor_counts()` (one grouped `COUNT(*)` over the join
  table) and `community_profile_has_empty_narrative_field()` (one bulk
  `SELECT` over `community_profiles`, mirroring `community_profile_quality_
  flags()`'s own shape) — a community with no `community_profiles` row at
  all is treated as incomplete too, since it has none of the tracked
  fields. See `tests/test_admin_completeness_filter.py`.
- **Sonnet 5 pricing correction (2026-09, issue #98) — the numeric rate row was
  already correct; only a stale comment claiming it would expire was fixed.**
  `linklib/pricing.py`'s `MODEL_PRICING["claude-sonnet-5"]` row (keyed off the
  literal model string `claude-sonnet-5`, the same id `linklib/models.py` and
  `linklib/agent.py`'s effort-tier map both use) already held the confirmed
  $2/$10/$2.50(5-min cache write)/$0.20(cache read) per-MTok rates — but its
  comment and the module docstring both said this was a temporary
  introductory window ending 2026-08-31, reverting to $3/$15 after. Per
  Anthropic's own pricing announcement
  (https://www.anthropic.com/news/claude-sonnet-5), that planned increase was
  cancelled — the introductory rate is now permanent — so both were corrected
  to say so rather than continue flagging a future edit that will never be
  needed. **1-hour cache TTL (2026-10, issue #622):** `MODEL_PRICING` rows now carry
  `cache_write_1h` (2x input; Sonnet 5 is $4.00/MTok) and `compute_cost` takes a
  separate `cache_creation_1h_tokens` bucket. Nothing requests the 1-hour TTL
  today (`linklib/matchmaker.py`'s `cache_control` sets no `ttl`, so it gets the
  5-minute default), so no existing cost or cap figure changes; this only means a
  future caller is billed correctly. The "Anthropic pricing" 90-day reminder is a
  manual attestation and needs no data change for this. New
  `tests/test_pricing.py` pins the confirmed rate values and a hand-checked
  `compute_cost` calculation so a future accidental edit is caught.
- **Test-hygiene guards (2026-10, issues #623, #639, #628).** `Library.write_opml`
  raises under pytest if asked to overwrite the git-tracked `preferred_sites.opml`
  (a test with a temp `LINKLIB_DB` and no `LINKLIB_SITES_OPML` used to regenerate
  it; in production the tracked path is the live path, so the guard is test-only
  and does not cover ad hoc scripts). `tests/test_exa_call_site_lists.py` fails
  when a module that calls `api.exa.ai` is missing from either Exa list on
  `/admin/system/ai`; the toggle card had omitted Feature Taxonomy vendor
  research. `tests/test_readme_references.py` fails when `README.md` names a repo
  path or route that does not exist. CI now pins `ubuntu-24.04` and uses the Node
  24 majors of `actions/checkout` (v5) and `actions/setup-python` (v6).
- **Pricing/model freshness check (2026-09, issue #98 follow-up) — two
  genuinely different pieces, one fully automatable, one that can't be.**
  **Piece 1, permanent and automated**: `tests/test_pricing.py::
  test_every_registry_model_has_a_pricing_row` asserts every model id in
  `linklib/models.py`'s curated `_REGISTRY` has a matching `MODEL_PRICING`
  row — closes the "new model registered, pricing forgotten" gap for good;
  no human ever needs to remember to check this again. As of this PR the two
  already matched exactly (confirmed by the Step 0 investigation), so the
  test passed immediately — its job is guarding against future drift, not
  fixing a current gap. **Piece 2, a dated manual reminder, not automation**
  — there's no pricing API to reconcile `MODEL_PRICING` against the way
  `linklib.models` reconciles the model registry against the live Models
  API, so the honest version of "check this periodically" is a human
  attestation, not a test. Reuses the same reviewed-toggle pattern already
  established for Community gaps (`toggle_community_gap_reviewed`) and
  FP&A Buddy feedback (`toggle_ask_feedback_reviewed`) — a plain dated
  `settings` value (`pricing_last_verified`, via the existing
  `get_setting`/`set_setting`, no new column) rather than a per-row boolean,
  since there's no per-row entity here, just one global "last verified"
  date. `/admin/checks` gained a "Pricing freshness" section below the
  automated pass/fail list — deliberately its own banner
  (`_pricing_freshness_banner`), not a row in `checks.run_all()`'s list,
  since this isn't a pass/fail check in that sense. Amber (never-reviewed,
  or older than `linklib.pricing.PRICING_REVIEW_STALE_DAYS` = 90, confirmed
  with Brian) / seafoam (fresh), with a "Mark reviewed" button
  (`POST /admin/checks/mark-pricing-reviewed`, admin-only) that stamps the
  current time — no auto-clear-on-view, same as the other two toggles.
  `linklib.pricing.pricing_review_is_stale()` is the pure, unit-tested
  staleness check (missing or unparseable value both read as stale, same
  as "never verified"). See `tests/test_pricing_freshness.py` for the
  end-to-end coverage (never-reviewed banner, mark-reviewed clearing it,
  going stale again past 90 days, staying fresh within it, auth on both
  the page and the action).
- **Exa pricing freshness — a third, parallel dated manual reminder,
  mirroring the Pricing freshness banner above exactly.** `linklib/pricing.py`'s
  `EXA_PRICING` table (checked 2026-07-26 against Exa's published rates) had
  no freshness reminder at all, unlike `MODEL_PRICING` and the model
  registry — found during the AI usage dashboard's Step 0 investigation.
  Same mechanism, same shape: a dated `settings` value
  (`exa_pricing_last_verified`), `linklib.pricing.exa_pricing_review_is_stale()`
  (pure, unit-tested, identical logic to `pricing_review_is_stale`), a
  third `/admin/checks` banner (`_exa_pricing_freshness_banner`) below the
  New-model-awareness one, and `POST /admin/checks/mark-exa-pricing-reviewed`
  — no auto-clear-on-view, admin-only, same amber/seafoam treatment. Same
  90-day window as Claude/OpenAI pricing (`EXA_PRICING_REVIEW_STALE_DAYS`),
  not the 30-day new-model-awareness window — this checks whether an
  existing rate is still accurate, the same category of question as
  Claude/OpenAI pricing, not "does something new exist to add." Not seeded
  from the table's own "checked 2026-07-26" comment — starts unreviewed,
  same as the other two banners never backfilled from their own pre-banner
  pricing-check comments either. See `tests/test_exa_pricing_freshness.py`
  for the end-to-end coverage (same shape as `tests/test_pricing_freshness.py`),
  and note `tests/test_models_freshness.py`'s own `_models_section` helper
  was widened to bound its split on both sides, since a third section now
  follows it on the page.
- **Public `/tools/software` directory card's Delete button — a broken URL,
  not a broken HTTP method (2026-09).** Reported symptom: clicking Delete on
  a tool's card on the public directory (admin-visible controls only, same
  `AUTHED`-gated block as Quick edit/Full edit) navigated to
  `/admin/tools/{id}/delete` and errored instead of deleting. The initial
  hypothesis — a plain GET `<a href>` hitting a POST-only route — was wrong
  and confirmed wrong, not just dropped: `renderTools()`'s Delete affordance
  was already a real `<form method="post">` with its own `confirm()` dialog
  and the correct tool id. The actual bug was a missing path segment: the
  form's `action` was hardcoded as `/admin/tools/' + t.id + '/delete'`,
  omitting `software/` — matching no route at all
  (`admin_tools_delete` is registered at
  `/admin/tools/software/{tool_id}/delete`), so the browser navigated
  straight into a plain 404 rather than completing the delete. `delete_tool()`'s
  cascade cleanup was never implicated — the request never reached the route
  handler at all. Fixed by correcting the action URL to the real route and
  adding an explicit `redirect_to` hidden input, matching the admin table's
  own already-working Delete form (`/admin/tools/software`, `_tool_row`)
  byte-for-byte rather than inventing a second mechanism — no auth check was
  touched. **`/tools/communities` was checked and found NOT to have the same
  bug, because it doesn't have the affordance at all**: `renderCommunities()`
  never gates on `AUTHED` and renders no edit/delete controls on its public
  cards, unlike `renderTools()` — confirmed by reading the function, not
  assumed clean because the report didn't mention it. See
  `tests/test_public_directory_delete_affordance.py` for the regression
  coverage, including a real reproduction of the old broken URL 404ing and a
  full create → delete-via-the-fixed-affordance → confirm-gone cycle.

- **"Remove content" retired (PR 4, 2026-09) — the enricher no longer judges
  audience fit at all; production had 0 flagged articles at retirement time.**
  `/admin/library/review-removals` (plus its `/check-link`, `/keep`, `/remove`
  sub-routes) showed articles the enrichment pipeline flagged as off-audience
  (`articles.in_scope=0` — podcasts, VC-career content, annual predictions) for
  a human to keep or remove. It was scaffolding for the initial bulk Feedly
  import; the archive is now curated one article at a time by hand, so an AI
  pre-filter has nothing left to do. **Investigated first, per the standing
  gate**: a full grep of every reader of `articles.in_scope` across `webapp/`,
  `linklib/`, and `scripts/` confirmed the flag was never read outside this one
  feature — not by the Reader, `Library.search()`/`vector_search()`,
  `linklib.agent.retrieve()`/`retrieve_feed()` (FP&A Buddy), or the matchmaker
  — so it was purely a "flagged for review" bookkeeping fact, never an
  exclusion filter on normal display/search/retrieval; no "default the filter
  to include everything" fifth change was needed, since no such filter existed
  outside the retired feature. `articles.in_scope`/`scope_reason` are **frozen,
  not dropped** (always `1`/`''` going forward — see the schema comment in
  `linklib/db.py`), same non-destructive-retirement precedent as
  `screenshot_is_product`/`field_reviews` elsewhere in this doc.
  `Library.list_flagged`/`flagged_count`/`keep_article` (the three methods that
  existed solely for this feature) are deleted outright. `linklib.enrich.enrich()`'s
  prompt instruction asking Claude to judge audience fit is removed entirely —
  not just discarded downstream — and the `Enrichment` dataclass's
  `in_scope`/`scope_reason` fields are dropped with it. **One real side effect
  this same grep surfaced, since made entirely moot by PR 3's own retirement of
  the Archive Queue (see the bullet immediately below — the two PRs shipped in
  the same window and touched the same shared `enrich()` call)**: at the time
  this PR was built, `enrich()` was shared by both the post-save enrichment
  pipeline and `linklib/queue.py`'s pre-queue candidate scoring, which used the
  identical `result.in_scope` signal to skip an off-audience candidate before it
  was ever proposed into the Archive Queue (a `skipped_scope` stat). With the
  audience-fit prompt gone, that skip logic would have silently no-op'd, so this
  PR removed it outright rather than leave it dead — but `linklib/queue.py`
  itself, the `skipped_scope` stat, and the Archive Queue it fed all no longer
  exist at all as of PR 3, so this whole side effect is now purely historical:
  there's no queue left for an off-audience candidate to reach or be filtered
  from either way. See ARCHITECTURE.md's "'Remove content' retirement,
  PR 4 (2026-09)" section for the full write-up.
- **Archive Queue retired outright (2026-09, PR 3) — a deliberate retirement
  of working code, not a bug fix; read this before ever considering rebuilding
  it.** A production query on 2026-09-09 found `library_queue` at 5,508
  rows, **every single one `status='dismissed'`, zero `pending`, and zero
  member submissions ever** — the AI-enriched proposal/review pipeline
  (`linklib/queue.py`'s RSS scan + one-time historical sitemap sweep,
  `linklib/suggest.py`'s Claude keep/skip advisory, `/admin/library/queue`'s
  review UI, the Historical Sweep panel merged into it in Phase 6 above) had
  been dormant since 2026-06-28. The archive itself keeps growing fine
  without it — 1-2 articles every few days via the bookmarklet, which was
  never gated by the queue to begin with. An AI-enriched proposal/review
  pipeline doesn't earn its keep at that volume, so it's gone, not paused:
  `linklib/queue.py`, `linklib/suggest.py`, and `scripts/backfill_queue.py`
  are deleted in full; every `GET`/`POST /admin/library/queue*` route, the
  Historical Sweep panel, `POST /admin/library/backfill/start`,
  `GET /admin/library/backfill/status`, and the `/admin/library/backfill`
  redirect stub are all gone with no replacement or redirect (nothing was
  bookmarked outside the admin nav itself, which was updated in the same
  PR); the Archive Queue card/quadrant is gone from `/admin/library`, and
  the `_LIBRARY_TOOLS` hub-nav entry with it. On the data layer,
  `Library.add_to_queue`/`list_queue`/`queue_count`/`dismiss_queue_item`/
  `remove_from_queue`/`update_queue_published`/`promote_queue_item` (plus
  the queue-only helpers `article_urls`/`queue_urls`/`last_saved_at`) are
  removed, and the `library_queue` badge entry in `webapp.tasks.
  open_task_counts()` is gone. **`library_queue`'s own table definition is
  FROZEN in `_SCHEMA`, not dropped** — same non-destructive-retirement
  precedent as `screenshot_is_product`/`field_reviews` elsewhere in this
  doc — kept as inert historical record with a comment marking the
  retirement, why, and pointing at PR 3; nothing reads or writes it any
  more. `linklib.authcheck` turned out to have a real, undocumented
  dependency on `queue.py`'s sitemap-discovery helpers
  (`discover_sitemaps`/`fetch_sitemap_entries`/`_looks_like_post`, used by
  `_recent_post_url()`'s fallback path when probing subscriber access) —
  found by grep, not anticipated by the retirement's own Phase 0 — so those
  functions (and their small dependencies: a User-Agent constant, a
  sitemap-XML parser, an HTTP-GET helper, a lastmod parser) were relocated
  directly into `authcheck.py` itself rather than left as a broken import;
  `authcheck` is now their only consumer, so `parse_sitemap_xml` was made
  private (`_parse_sitemap_xml`) in the move.

  **The `feeds.exclude_from_queue` fallout, retired alongside it**: the
  per-feed "Read only" checkbox on `/admin/reader/feeds`, its
  `POST /admin/reader/feeds/{feed_id}/read-only` route, and
  `Library.set_feed_excluded`/`excluded_feed_urls`/`has_feeds` are all gone
  — the checkbox's whole reason to exist was steering `scan_feed_into_queue`
  away from certain feeds, and that scanner no longer exists to steer.
  `exclude_from_queue` itself is FROZEN in the `feeds` table (same
  non-destructive precedent as the table above), and
  `_LEGACY_QUEUE_EXCLUDED_SECTIONS` — the old News-section fallback
  `excluded_feed_urls()` used on an unseeded DB — is deleted along with the
  method that read it; `seed_feeds_from_opml()` no longer passes
  `exclude_from_queue` at all when seeding a fresh feed row.
  **Verified explicitly, per this retirement's own "treat with extra
  care" instruction, since a mistake here would silently degrade FP&A
  Buddy's web search**: (1) `Library.write_opml()`/`opml_xml()` were read
  directly, line by line — neither ever referenced `exclude_from_queue` at
  all, so removing the column's write path changes nothing about what
  `preferred_sites.opml` contains. (2) `linklib/sources.py`'s
  `preferred_domains()` — the function FP&A Buddy's web-search allowlist
  actually calls — was read in full (36 lines): it parses the OPML XML for
  `<outline>` elements' `htmlUrl`/`xmlUrl` attributes only, with zero
  reference to `exclude_from_queue`, `library_queue`, or any queue concept
  whatsoever. The web-search allowlist is untouched by this retirement.

  **`/library/submit` (FP&A Buddy's "Suggest it for the archive →" citation
  link, shipped in #504, still points here) changed from a queue write to a
  plain email notification to Brian — never used as a queue write in
  practice (zero submissions, ever), so this is a mechanism swap with no
  user-visible behavior change for the one real caller.** No replacement
  table, no admin inbox page, no approve route, no badge — reusing
  `linklib/email_utils.py`'s existing `send_notification_email` (the same
  Gmail-REST infrastructure and `email_failures` tracking the tool/community
  submission notifications already use, editable at `/admin/emails` via a
  new `_INTERNAL_EMAIL_ROWS` entry, `notification_type="library_submission"`)
  instead. The `_is_member` gate, the honeypot, and every form field
  (`url` required, `why`/`name`/`email` all optional and length-capped) are
  unchanged; the on-page confirmation copy is unchanged too (pre-existing
  text, not newly authored). **Deliberately no confirmation email to the
  submitter** — only the notification to Brian, per explicit instruction.
  Since there's no queue/table to check any more, the route always shows the
  same confirmation regardless of what happens to the notification email
  (a failed send is logged to `email_failures`, never surfaced to the
  visitor) — there's nothing left to leak either way. See
  `tests/test_library_submit.py` (fully rewritten for this new behavior) for
  the coverage.

  **If a future session is tempted to rebuild queue-style proposal/review
  tooling for the archive: re-check the actual submission volume first.**
  The numbers that justified retiring this (5,508 rows, 100% dismissed, 0
  pending, 0 member submissions ever, 1-2 bookmarklet saves every few days)
  are the reason it's gone — don't restore it on the assumption it might be
  useful again without confirming the volume has actually changed.

- **Category-feature definition editor could silently truncate (2026-09).**
  `category_features.definition` stores up to 1,470 characters in production
  (ten rows over 500), but the Manage Features inline editor, its "Add a
  feature" form, and the review-queue approve card all capped the field at
  `maxlength="500"`. Measured in Chromium before fixing, the real failure
  mode is narrower than "any save truncates": an untouched long value
  submits intact and a hand-edit is blocked with a validation message, but
  **pasting** a revised definition is silently cut to 500 characters and the
  form still submits as valid. Separately, the fields were `<input
  type="text">`, which strips line breaks on submit (no production row has
  one today). Fixed: all three are `<textarea>`s with one shared limit,
  `Library.CATEGORY_FEATURE_TEXT_MAX` (10,000, ~7x the longest stored value,
  mirrored by `webapp.app._FEATURE_TEXT_MAX`), and
  `Library.add_category_feature`/`update_category_feature` now **refuse** an
  over-limit value with an error naming the limit and the actual length,
  never shortening it, so every write path (routes, the review-queue
  approve, scripts) gets the same guard. A second, related bug in the same
  pass: edit-then-approve on a new-feature proposal rebuilt the payload
  without its `definition`, so the proposed definition was dropped on
  approval; the approve card now shows it and the route carries it through.
  Second instance of this shape after the tool Short summary field; the
  `maxlength` sweep of every other admin input is in the PR, pending
  production length reads, with no mechanical check built yet (a separate
  decision). See `tests/test_category_feature_text_length.py`.

- **Character budget: a live count instead of `maxlength` (2026-09).** The
  category-feature definition fix above raised the cap to 10,000 but kept an
  HTML `maxlength`, so a paste over the limit was still cut silently, the
  same bug at a higher number. `maxlength` is gone from those fields; the
  server-side refusal is the only enforcement. `webapp.app._char_budget(limit,
  value, field_id)` is the shared helper for every capped admin text field
  (search for `_char_budget`): it returns the field's attributes plus a
  counter reading "Limited to 10,000 characters. 1,470 characters" that
  updates as you type or paste. Over the limit the count turns `--alert` red
  (never coral), says how far over it is, and the form's submit button(s)
  switch to "Over limit" and disable. `_page()` adds `_CHAR_BUDGET_JS` to any
  page containing a budgeted field. Counting uses
  `Library.text_budget_length`, which counts a CRLF line break once, since
  the browser submits CRLF but counts one character. Live on the Manage
  Features editor, its Add form, and the review-queue approve card. Other
  capped fields (PR 598's `maxlength` sweep, and PR 597's publishable vendor
  note) move to it in a later PR. Same PR: the empty-state placeholder
  standard is "{Field} not available.", not "not yet available", since "yet"
  implies Brian will write one, and often he won't. Changed in
  `gates.EMPTY_COPY` and the compare-cell "Not available." label so every
  surface moves together. See `tests/test_char_budget.py`.

- **Character budget gains a soft target, not just a hard limit (2026-09) —
  applied to every AI-drafted field a production length read (2026-09-23)
  found already exceeding its old cap.** The read found caps sitting below
  what's already stored — `tools.agent_taxonomy_note` (cap 1,200, longest
  3,540, 133 rows over), `community_profiles.stage_focus` (cap 300, longest
  440), `tools.summary` (cap 400, longest 453, previously guarded only by
  `admin_tools_edit`'s own conditional `maxlength` patch — see the Key
  architecture decisions bullet on that fix), plus `tools.description`/
  `tools.competitive_differentiation` (0 rows over, but with little
  headroom left). Nothing had been truncated — these were caps that would
  cut on the next paste, and PR 599 above only removed that risk for the
  category-feature definition field. `_char_budget(limit, value, field_id,
  target=None)` grew a third, optional tier: under `target`, the counter
  reads plainly; over `target` but under `limit`, it turns `--caution`
  amber and reads "N characters. Aim for &lt;target&gt;." — the save still
  works, it's an editorial nudge, not enforcement; over `limit`, the
  original red "refused" behavior is unchanged. Every field this predates
  (`category_features.definition`/`pointer_note`) keeps its old single-tier
  behavior, since `target` defaults to `None`. New `Library.
  _check_text_field_length(label, value, limit)` generalizes
  `_check_category_feature_text`/`_check_feature_link_public_note`'s
  identical shape to every field added here, wired into every write path
  the admin edit forms actually post to — `add_tool`/`update_tool`/
  `quick_update_tool` (description, summary), `update_tool_agent_taxonomy`,
  `update_tool_differentiation`, and `upsert_community_profile`
  (stage_focus/jobs_program/team_or_individual only — see below). Each new
  MAX clears its field's own real longest stored value with headroom, so
  nothing already saved becomes unsavable:

  | Field | Target | Max |
  |---|---|---|
  | `tools.description` | 2,500 | 3,500 |
  | `tools.summary` | 400 | 800 |
  | `tools.agent_taxonomy_note` | 2,500 | 4,000 |
  | `tools.competitive_differentiation` | 600 | 1,200 |
  | `community_profiles.stage_focus`/`jobs_program`/`team_or_individual` | 300 | 800 |
  | `tool_feature_links.public_note` | 500 | 1,000 (MAX unchanged — the column is new and empty on every row) |

  **`tools.summary`'s conditional-`maxlength` patch is retired outright** —
  the field now gets the same `_char_budget` treatment as every other
  field here, so a legacy summary already over the old 400-char cap no
  longer permanently blocks typing (the exact bug that patch existed to
  work around); it just renders amber past the new 400-char target, same
  as any other over-target-under-limit value. `_check_feature_link_public_note`
  was also switched from a bare `len()` to `Library.text_budget_length`, so
  its hard-limit check agrees with the live counter's own CRLF-counts-once
  rule — a real, small pre-existing mismatch this PR closed while adding
  the field's target.

  **Generator prompts tightened to draft under target, not just refuse over
  limit** — a nudge the generator ignores just paints every row amber.
  `_TOOL_DESC_PROMPT` and `_AGENT_TAXONOMY_PROMPT` both dropped their
  "Budget and depth are not a constraint here" opener (the likely actual
  driver of the drift) and gained an explicit ceiling converted from the
  character target at ~6.2 characters/word (Description: "stay under about
  400 words (roughly 2,200 characters)"; Agent taxonomy: "keep the whole
  summary under about 400 words (roughly 2,500 characters)," with an
  explicit note to favor a concise agent roster over a lengthy write-up
  per agent once there are more than a handful). **`max_tokens` itself is
  deliberately UNCHANGED for both** (3,000 and 2,000 respectively) —
  lowering it risked reintroducing the exact documented truncation
  incident this codebase already fixed once (see the citation-tag
  investigation and PR 260's `MIN_GENERATE_MAX_TOKENS` note above): Opus
  5's adaptive thinking shares the same budget as the visible response, and
  the current ceilings were raised specifically to give that headroom on a
  content-rich page. Only the prose's own stated length shrank; the safety
  margin against truncation did not. Differentiation needed no change —
  rule 4 ("one or two sentences") already keeps it well under its 600-char
  target (longest stored: 546), and its `max_tokens=1,200` already carries
  the same documented anti-truncation headroom.

  **`stage_focus`'s 440-character overflow root-caused to a real prompt
  gap, not a missing max_tokens**: `_COMMUNITY_PROFILE_PROMPT`'s rule 8
  already told the model six Quick facts fields (seniority_band,
  primary_purpose, platform_type, meeting_format, event_style, and —
  via its own separate rule 9 — cpe_eligible) to stay "a phrase, not a
  paragraph—deliberately brief," but never named stage_focus, jobs_program,
  or team_or_individual — the three fields this PR budgets. Fixed by
  adding all three to rule 8. The other six Quick facts fields keep their
  existing, unenforced `maxlength="300"` — flagged at ship time as unverified
  ("no production evidence they've ever needed it," since this session had
  no database access to confirm a number beyond what the brief supplied for
  the three fixed here), and since confirmed directly (2026-09, PR 600
  review, all 40 live `community_profiles` rows via the `/mcp` introspection
  tools): real longest values are `primary_purpose` 79, `cpe_eligible` 91,
  `platform_type` 90, `meeting_format` 116, `event_style` 100,
  `seniority_band` 128 — none within even half the 300 cap. Pinned as a
  static ceiling test (`tests/test_char_budget_targets.py`'s
  `test_other_quick_facts_fields_stay_well_under_their_unenforced_cap`) so
  a future regeneration pass that starts pushing these longer gets caught
  by a failing test rather than a silent surprise. `stage_focus`/`jobs_program`/
  `team_or_individual`'s own community-profile-edit-page `_short_field`
  helper grew a `budgeted: bool` flag rather than a hardcoded per-field
  branch, so a future field can opt in the same way.

  **No data repair, no production writes** — the 133 over-old-cap agent-
  taxonomy notes (and any other field already over its old cap) are
  untouched; shortening them is a separate editorial decision Brian
  hasn't made. Every Generate-populated field (`generateDescription`,
  `generateDifferentiation`, `generateCommunityProfile`'s per-field loop)
  now dispatches a synthetic `input` event after setting `.value`, since
  none of those AJAX call sites previously fired one and the char-budget
  counter only ever updates on a real `input` event — without this, the
  counter stayed stale (showing the field's PRE-generate count) until the
  admin's own next keystroke, which would have made the amber-vs-red
  distinction actively misleading for a freshly-drafted, already-over-
  target field. Agent taxonomy's own "Generate summary" button is a real
  form POST to `/admin/tools/software/{tool_id}/research/refresh` (a full
  page reload, not AJAX), so it needed no such fix — the counter is
  already correct on the fresh server render.

  **Two small riders, unrelated to the character budget itself but folded
  into the same PR per its own brief**: the four remaining "yet" empty-
  state strings (`gates.COMPARE_EMPTY_LABELS`'s "Not yet documented."/
  "Not yet curated." and `gates.EMPTY_COPY`'s two full sentences ending in
  "...hasn't been documented yet."/"...hasn't been researched yet.")
  dropped the "yet" — same reasoning the PR 599 bullet above already
  applied to the `{Field} not available.` family: "yet" promises a future
  fill-in that often never comes. And `tests/test_checks.py`'s
  `test_ai_provider_summary_rows_link_only_to_their_section_no_fix_links`,
  previously order-dependent — see that test's own updated comment for the
  root cause and fix.

  See `tests/test_char_budget_targets.py` and
  `tests/test_generator_length_guidance.py` for the full regression
  coverage.

See the **Authentication & security** section below for the full access-control model —
it supersedes the old "`/save` is token-gated" note.

- **Admin URL restructure, group A (2026-09) — nine admin routes moved into
  three grouped prefixes, no redirects.** The admin hub had accumulated
  routes at the flat top level with no shared naming convention across
  related pages — four "things waiting on Brian" pages scattered across
  the Inbox group with unrelated URL shapes, two thought-leadership admin
  pages that didn't share a prefix, and FP&A Buddy's two admin reporting
  pages sitting at generic `/admin/ask-*` names. Renamed, with every
  sub-route (POST targets, nested CRUD, reviewed toggles) moving with its
  parent and **no compatibility redirect for any of the nine** — this is
  admin-only surface with one or two users, and a redirect stub is exactly
  the kind of accumulating cruft this restructure exists to remove (same
  "nothing was bookmarked externally" reasoning the Library/Toolbox
  restructure phases used for their own route removals above).

  | Old | New |
  |---|---|
  | `/admin/contacts` (+ `/delete`) | `/admin/inbox/contact-submissions` |
  | `/admin/tools/software/leads` | `/admin/inbox/toolbox-intros` |
  | `/admin/community-gaps` (+ `/{id}/toggle-reviewed`) | `/admin/inbox/community-gaps` |
  | `/admin/email-failures` (+ `/{id}/dismiss`) | `/admin/inbox/email-failures` |
  | `/admin/thought-leadership` (+ `/new`, `/{id}/edit`, `/{id}/delete`) | `/admin/thought-leadership/third-party` |
  | `/admin/original-content` (+ `/new`, `/{id}/edit`, `/{id}/delete`) | `/admin/thought-leadership/original` |
  | `/admin/game-settings` (+ `/{rank}/edit`) | `/admin/thought-leadership/game-settings` |
  | `/admin/ask-report` (+ `/export.csv`) | `/admin/fpa-buddy/report` |
  | `/admin/ask-feedback` (+ `/{id}/toggle-reviewed`) | `/admin/fpa-buddy/feedback` |

  **The three group prefixes (`/admin/inbox`, `/admin/thought-leadership`,
  `/admin/fpa-buddy`) are not pages of their own** — no route was added at
  any of the three bare prefixes; the admin hub links directly to the leaf
  pages, same as every other group on the hub. **`/admin/fpa-buddy/*` (new,
  this PR) is unrelated to the pre-existing public `/tools/fpa-buddy/*`
  prefix** — the how-it-works explainer stays exactly where it was
  (`/tools/fpa-buddy/how-it-works`, public, unmoved) and is not nested under
  the new admin prefix. **Two card moves rode along with the URL changes**:
  Sail, Don't Row settings moved out of the CFO Toolbox hub-nav group into
  Thought leadership (its card belongs wherever its URL now lives, and its
  URL is now under `/admin/thought-leadership/*`), and the page at
  `/admin/thought-leadership/third-party` — previously titled "Thought
  leadership," the same name as the hub-nav GROUP that now contains it,
  reading as a page nested inside itself — was relabeled "Third-party
  content" on both its own `<h1>`/page title and its hub-nav card; the group
  itself keeps the name "Thought leadership." **Rider, same workstream**:
  `POST /tools/submit`'s notification email pointed at a route that never
  existed, `/admin/tools.` (note the trailing period — almost certainly a
  sentence-punctuation typo absorbed into the href), fixed in the same PR to
  point at the new `/admin/inbox/toolbox-intros` path rather than leave a
  known-dead link in place while the very page it should have pointed near
  was being renamed anyway. `webapp.hub_nav_orphans()`/`_hub_nav_all_hrefs()`
  needed no logic change — both are already derived directly from
  `_ADMIN_GROUPS`/`_LIBRARY_TOOLS`/`_FPA_BUDDY_TOOLS`/`_SOFTWARE_TOOLS`
  rather than a separately hand-maintained href list, so updating those
  tuples' hrefs was the whole fix; the detector stayed accurate with no
  separate edit.

- **Reader route moves, PR 6 (2026-09) — five more `/admin/library/*` routes
  into `/admin/reader/*`, plus the archive-backup card's own move to a
  different hub-nav group entirely.** Same shape as the Admin URL
  restructure, group A bullet above — every sub-route (POST targets, nested
  CRUD, start/status pairs, the manual-review and purge CSV trios) moves
  with its parent, **no compatibility redirect for any of the six**.

  | Old | New |
  |---|---|
  | `/admin/library/feeds` (+ add/edit/delete/section/subscription routes) | `/admin/reader/feeds` |
  | `/admin/library/backfill-content` (+ start/stop/status, the manual-review CSV trio, the purge CSV trio, accept/unaccept) | `/admin/reader/backfill-content` |
  | `/admin/library/enrich` (+ `/start`, `/status`) | `/admin/reader/enrich` |
  | `/admin/library/dedupe` (+ `/remove`, `/not-dupe`, `/remove-older`) | `/admin/reader/dedupe` |
  | `/admin/library/bulk-delete` (+ `/template.csv`, `/preview`, `/commit`) | `/admin/reader/bulk-delete` |
  | `/admin/library/backup` (+ `/upload-db`, `/download-db`) | `/admin/library-backup` |

  **`/admin/library-backup` deliberately keeps the word "library" instead of
  becoming `/admin/reader/backup`** — it snapshots `library.db` in its
  entirety (Toolbox, accounts, site operations, the game — not just
  Reader/archive content), so "library" is the accurate name and "reader"
  would misdescribe the page. This is a real, considered exception, not an
  inconsistency to "fix" in a later pass. `/admin/backup-now` (the separate
  token-authed POST trigger the daily Railway Cron Service and RUNBOOK.md's
  manual curl call) is a different route entirely, was never under
  `/admin/library/*`, and is untouched here.

  **Tag cleanup (`/admin/library/tags`) and Tagging style
  (`/admin/library/tag-style`) were deliberately NOT renamed in this PR** —
  they merge into a single `/admin/reader/tag-management` page in a later
  PR, so renaming them now would just be renamed again almost immediately.
  Both stay exactly where they are here. (**They've since merged — see the
  Tag management merge (PR 7) bullet below.**)

  **The archive-backup card moved hub-nav groups, not just its URL**: it
  leaves `_LIBRARY_TOOLS` (and `/admin/library`'s own page, which drops
  from four quadrants to three — see the Phase 6 admin-nav-restructure
  bullet's own now-superseded "four quadrants" description; that page is
  itself gone as of PR 9, its three quadrants folded into the Reader group
  on `/admin`) and joins the
  System group's card list on `/admin` instead — a whole-DB snapshot is
  accounts/health/plumbing, the same kind of thing as Users or Checks, not
  archive-specific. `webapp.hub_nav_orphans()`/`_hub_nav_all_hrefs()` needed
  no logic change for the same reason the group A PR's own didn't — both
  derive their href set from the live tuples, so moving the entry between
  `_LIBRARY_TOOLS` and `_ADMIN_GROUPS`' System list was the whole fix.

  **RUNBOOK.md rider**: its post-restore validation checklist still named
  `/admin/library/queue` — the Archive Queue's own admin page, retired
  outright in 2026-09 PR 3 (see the Archive Queue retirement bullet above)
  — as a page to spot-check after a restore, a stale reference the group A
  PR flagged as out of scope at the time. Fixed here since this PR was
  already touching RUNBOOK.md for the backup-page path anyway: the queue
  step is removed from the checklist with a note explaining why.

- **Tag management merge (PR 7, 2026-09) — Tag cleanup and Tagging style,
  left in place by the Reader route moves above, merge into one page,
  `/admin/reader/tag-management`.** No compatibility redirect — both old
  paths 404, signed in and signed out, sub-routes included. Two clearly
  headed sections, not blended and not collapsed into a disclosure: Tag
  cleanup is a data-cleanup tool (acts on tags articles already carry —
  merge, rename, delete, plus the AI-suggested-merges flow); Tagging style
  is configuration (shapes tags that don't exist yet — the objective and
  the learned guide that steer the enrichment prompt via `linklib.tagstyle`).
  Both are used regularly enough that a disclosure would add a click without
  reducing clutter. The page is titled "Tag cleanup and style" (shipped as
  "Tag cleanup &amp; style"; the ampersand was spelled out in PR 9's
  typographic sweep, below), not "Tag management" — the quadrant holding
  this card is already named "Tag management," and naming the card the same
  as its
  own containing group would repeat the exact self-nesting problem the
  "Third-party content" rename (see the Admin URL restructure bullet above)
  exists to avoid; the quadrant's name and description are unchanged. Every
  action from both original pages works unmodified from the merged page —
  nothing was dropped. Section copy (the intro text and bullet lists under
  each half) carries over from the two original pages verbatim — it
  predates the plain-language copy standard this page's own new intro
  pilots, and a planned site-wide copy pass is the right place to bring it
  in line, not a one-off touch here; that pass's starting inventory is in
  this PR's own description. **Rider, same workstream**: the Enrich archive
  hub-nav description was rewritten in the same PR — it used to undersell
  what the tool does ("Generate Claude summaries and tags..."), and it's
  grouped with the tagging tools specifically because the tags it drafts are
  the vocabulary Tag cleanup tidies and Tagging style teaches; the new copy
  says that plainly. See ARCHITECTURE.md's "Tag management merge" section
  for the full write-up and `tests/test_tag_management_merge.py` for the
  regression coverage.

- **The Reader box, and Library -> Reader (PR 9, 2026-09) — `/admin/library`
  stops being a page and becomes a collapsible group on `/admin`.** The
  standalone page is gone, 404 outright signed in and signed out, no
  redirect — same cutover convention every other admin route move in this
  sprint used. Its three quadrants (New content, Existing archive
  management, Tag management) are now a nested **Reader** group inside CFO
  Toolbox, alongside the Software sub-group and the Communities card, built
  by a new module-level `_reader_admin_quadrants(task_counts)` that returns
  them as pre-rendered HTML strings for `_group_html`'s existing
  accepts-a-string item list — the same mechanism FP&A Buddy and Software
  already nest through, so no new plumbing. **Two levels of nesting, all
  collapsed on load**: Reader -> quadrant -> cards, with the capture-method
  accordions inside New content making a fourth `<details>` level, every one
  of them closed (`test_every_disclosure_level_loads_collapsed` walks the
  whole chain in one assertion).
  **Three things the build brief named, and what actually happened to each**:
  (1) The **content-flow diagram** was to be shrunk to fit inside the
  collapsed box. It didn't exist — it was retired with the Archive Queue
  itself in PR 3 (with no queue there's no producer/consumer relationship to
  diagram), so there was nothing to shrink, relocate, or silently drop.
  Flagged rather than quietly skipped, and guarded by
  `test_flow_diagram_is_still_gone`. (2) The **bookmarklet/Share-Sheet
  accordions** (~4,200 characters, two capture pairs) stay expandable inside
  New content, the quadrant that covers bringing new material in.
  (3) **"Open Reader"** is a `.btn.btn-ghost` link in the Reader group's own
  description line — the first thing inside the box, above the quadrants.
  It was a header action beside the retired page's `<h1>`; with no page left
  to head, the group description is the equivalent position.
  **The page's two-column `.lib-cols` layout is deliberately not carried
  over** (nor its `.lib-q-*` order wrappers or mobile `align-items` reset):
  that layout existed to fill a full-width page, and inside one column of
  `/admin`'s own two-column grid there's no width left to split, so the
  quadrants stack in `_group_html`'s existing grid with no CSS of their own.
  `_group_html` gained one parameter, `count_label`, so the Reader group can
  show `len(_LIBRARY_TOOLS)` rather than the literal 3 items (quadrants,
  not tools) — every other caller is unaffected.
  **Library -> Reader is user-facing copy only, and the line is worth not
  blurring**: renamed are the `/tools` tile, the Reader's own nav label and
  rail back-link, the admin group heading, and seven sub-page back-links.
  Untouched are `linklib/`, `library.db`, the `Library` class,
  `_LIBRARY_TOOLS` (the tuple name), the frozen `library_queue` table, and
  `/admin/library-backup` — that last one keeps the word deliberately, since
  it snapshots `library.db` in its entirety, and PR 6 already documented it
  as an intentional exception. Do not "fix" it.
  **Two real bugs on the `/tools` Reader tile**, both confirmed in source
  before being changed rather than taken on faith from the brief: it pointed
  at `/admin/library` (the admin management page, not the reading surface it
  promised), so the one tile offering a reading stash opened a page of
  maintenance tools; and it didn't open in a new tab. Now `/read`,
  `target="_blank"` via `_toolbox_tile`'s new `new_tab` parameter. Confirmed
  it is genuinely admin-only (built conditionally in Python, absent from the
  HTML entirely otherwise), so a signed-out visitor sees nothing either way.
  Separately, `/read`'s own rail back-link pointed at `/admin/library` and
  read "&larr; Library"; it goes to `/tools` ("&larr; Toolbox") now.
  **A dependency the brief's file list didn't predict, found by the mandated
  grep sweep**: seven admin sub-pages carried a "&larr; Library" back-link
  pointing at `/admin/library` (the six Reader tools plus
  `/admin/library-backup`). Every one would have pointed at a 404; all now
  read "&larr; Admin" -> `/admin`. `_hub_nav_all_hrefs()` also stopped
  adding `/admin/library` to its set — there's no route left for a card to
  be an orphan of; the `_LIBRARY_TOOLS` hrefs it already contributed are
  unchanged. Detector and `/admin/system/page-index` both clean.
  See ARCHITECTURE.md's "The Reader box, and Library -> Reader" section for
  the full write-up and `tests/test_admin_reader_box.py` (renamed from
  `test_admin_library_layout.py`) for the coverage.

- **Typography lint: bare ampersands and spaced em dashes (PR 9, 2026-09) —
  extends the existing voice lint, deliberately not a parallel checker.**
  `linklib.voice_review.typography_findings(source)` enforces two of
  `voice_core`'s HARD MECHANICAL RULES that `mechanical_findings` couldn't:
  spell out "and" (except FP&A and friends), and never space an em dash.
  Wired into `webapp.checks.run_all()` as its own `/admin/checks` row and
  into `tests/test_voice_standards.py` for CI. Checked for an existing
  mechanism first, per the brief: no em-dash or ampersand lint existed, but
  `voice_mechanics._SPACED_EM_DASH` — the DB-write backstop's own regex —
  did, so the lint imports it rather than defining a second, driftable idea
  of what a spaced em dash is. **The two are the same rule on opposite sides
  of the same wall**: the backstop normalizes what Claude writes *into* the
  database; this catches what a human hand-types into `webapp/app.py`'s
  inline HTML, which never passes through `Library`'s write methods at all.
  **Scope is the whole design**: Python string literals only, docstrings
  excluded, embedded CSS/JS/HTML comments stripped per literal (the `:root`
  token table alone carries hundreds of spaced-em-dash spans in comments),
  and literals read as **source segments** rather than evaluated
  `ast.Constant` values — an f-string's value splits at each `{...}`, so a
  comment that interpolates something lands with its opener in one fragment
  and its closer in another, which a value-based scan flagged as copy for
  two real comments in this file. Database content is never scanned: a
  vendor or community name legitimately containing an ampersand (Bain &
  Company, Ernst & Young) is real data, and `site_copy` rows are Brian's own
  copy but edited at `/admin/copy` — those get reported, never rewritten.
  **The sweep it triggered was large and is reported rather than buried**:
  173 violations across `webapp/app.py`'s copy (120 spaced em dashes, 53
  bare ampersands), all fixed in this PR. The em-dash half was mechanical
  (collapse to unspaced); the ampersand half was per-phrase, with
  `AMPERSAND_ACRONYMS` and `AMPERSAND_NAMES` as the two small allowlists,
  both following the same "add a real one when it turns up" discipline
  `voice_core`'s own generalized ampersand carve-out already uses.
  **Tests are two-sided on purpose** — asserting only that the live source
  passes would be satisfied by a lint that never finds anything, so each
  rule also has a test proving it FAILS on a real violation of exactly the
  shape it catches, and PASSES on the near-miss beside it.

- **Brittle hardcoded-count tests, rewritten to assert on contents (PR 9,
  2026-09).** `tests/test_feed_cookie_flag.py`'s quadrant section and
  `tests/test_admin_library_layout.py` (renamed
  `tests/test_admin_reader_box.py`) both pinned literal tool counts —
  "3 tools", "2 tools", `count("<details") == 5`. They broke in PR 520,
  again in 523, again in 524, and would have broken again here: four PRs,
  four legitimate structural changes, zero real bugs caught. **A test that
  fails every time the structure it describes legitimately changes is a
  tax, not a safety net.** They assert on contents now — which tools are
  present, in which quadrant, derived from `_LIBRARY_TOOLS` itself so adding
  a tool needs no test edit — which is the fact actually worth protecting
  (a tool silently dropping off the admin surface, or landing under the
  wrong heading). One real bug in the rewrite is worth carrying forward:
  the first draft sliced a quadrant from its label to the next sibling
  label, which for the LAST quadrant swallowed every group rendered below
  it on `/admin` and "found" Archive backup inside Tag management. Fixed
  with a `<details>`-balancing slicer, which is what the shared
  `_disclosure_body` helper in both files does now.

- **Sitewide width pass (PR 12, 2026-09) — `.page-full` and `.page-admin`
  narrowed; `.page-grid` (1300px) confirmed as the width that already felt
  right and left untouched.** Brian's own read: nearly every admin page and
  the two main public landing pages felt too wide — a 760px `.tool-prose`
  column, or a hero/sidebar grid, floating inside a ~1900px shell reads as a
  skinny ribbon with a stranded back-link, not generous whitespace (the
  back-link itself was already fixed, separately, in PR 515 — this PR fixes
  the shell it was stranded inside). Three things were investigated and
  settled going in, not reopened by this PR: `.tool-prose` stays at 760px
  (already measures ~86-95 characters/line at 16px, in range for comfortable
  reading — widening it was the wrong lever); body font size stays at 16px
  (a 20px bump was considered as a way to make a wider column readable, and
  wasn't needed once the measure itself was confirmed fine); and the real
  cause was always the page shell, not the text column. Fixed by changing
  the tier token values, not by reassigning any page to a different tier —
  Brian's complaint covered essentially every page on a given tier, not a
  scattered subset, so one value change in one place was the right fix over
  dozens of per-page edits. `.page-full`: 1900px → 1440px. `.page-admin`:
  1500px → 1400px. `.page-grid` (1300px) is unchanged — it's the width
  Brian pointed to as already feeling right (the CFO Toolbox directory
  pages: `/tools`, `/tools/software`, `/tools/resources`, `/tools/communities`),
  so both other tiers moved toward it without merging into it.
  `.reader-layout` (the standalone `/read/{article_id}` single-article
  view's own bespoke max-width — a different route from the merged
  three-pane `/read` shell, which is a deliberate full-bleed exception this
  PR does not touch) deliberately tracks `.page-full`'s value even though
  that page is built from its own `_READER_TMPL`/`_READER_CSS` and never
  uses the `.page`/`.page-full` classes directly (see the
  `_PAGE_INDEX_CUSTOM_EXCEPTIONS` comment in `webapp/app.py`) — narrowed
  alongside it, from 1900px to 1440px, so the two stay in sync rather than
  silently drifting apart. `.page-form` (640px) and `.tool-prose` (760px)
  are both untouched — neither was part of Brian's complaint, and
  `/contact`/`/admin/tools/resources/new` (both `.page-form`) were
  explicitly out of scope. `/tools/fpa-buddy` (`.page-full` + a 1300px
  `.tool-inner`) is unaffected in practice — `.tool-inner` was already the
  narrower, binding constraint, so narrowing its outer `.page-full` shell
  changes nothing about how that page actually renders; it's genuinely
  different from its sibling directory pages (which use `.page-grid`
  directly, no inner wrapper) and stays that way pending its own
  redesign — reported, not changed, here. **Open question, reported rather
  than resolved**: `.page-full` (1440px) and `.page-grid` (1300px) are now
  only 140px apart — close enough that a future pass could reasonably
  collapse them into three tiers instead of four. Not done in this PR: a
  homepage hero/sidebar grid and a long-form article shell still read as
  wanting a little more room than a pure card grid, and collapsing a tier
  touches every page that uses it, which is a bigger, separately-scoped
  decision. **Part 2, same PR — admin table `min-width` sweep.** PR 11
  found and fixed three admin tables (Software categories, Community
  categories, Resources) with no `min-width` at all, so at narrow
  viewports the browser squished their columns into unreadably narrow
  text-wrapped slivers instead of triggering the `overflow-x:auto` scroll
  already wrapping them — the same bug shape reappears whenever a table has
  a free-text column (a description, a note, a URL) with nothing else in
  that column (nowrap content, an explicit per-cell `min-width`, a
  fixed-width input) to floor its min-content width. A full sweep of every
  `<table>` in `webapp/app.py` found this bug, in the same shape, on every
  remaining admin table that lacked a table-level `min-width` — roughly
  twenty more, across Contact submissions, Toolbox intros, Community gaps,
  Email failures, Compare-summary feedback, the name-duplicate check pages
  (both entity types), the Feature Review Queue's per-item link table, the
  Manage Features pivot table, Third-party content, Original content, the
  tool edit page's governed Feature Taxonomy checklist, the Reader content
  backfill's manual-review/accepted-as-final/recent-attempts tables, Tag
  cleanup, and the internal email-templates reference table on
  `/admin/emails` — plus the three `admin-table-responsive` column-picker
  tables (Software, Communities, Users) that PR 11's own table-shape fixes
  didn't touch. Fixed the same way as PR 11's own remediation: an explicit
  `min-width` on the `<table>` itself, sized to the table's own default-
  visible columns, plus an `overflow-x:auto` wrapper `<div>` on the four
  tables (Contact submissions' two tables, Email delivery failures, Tag
  cleanup) that had neither a wrapper nor a min-width at all. The
  three-button `.admin-table-actions-grid` Actions column and any per-cell
  `min-width`/`white-space:nowrap` already present on individual `<td>`s
  are untouched — those already floor their own column correctly; the fix
  only ever adds a table-level floor, never removes or narrows anything
  that was already protecting a column. `.cc-table`/`.tp-competitor-table`
  (public Compare/profile pages), `.ff-table`/`.fs-table` (the Reader feeds
  admin page, which already has its own dedicated 820px stacking
  breakpoint tuned around its own URL-wrapping content), and
  `.backup-log-table` (a small, already-narrow fixed-width table with its
  own 700px mobile-card breakpoint) were checked and are unaffected by this
  bug — each already has its own protection, so none needed a change.
  **A real regression this fix introduced into its own three
  `admin-table-responsive` tables, caught by the mobile-verification pass
  itself, not by inspection**: an inline `min-width` on a `<table>` element
  is not something a plain (non-`!important`) media-query rule can ever
  override, regardless of specificity — so the new desktop-only floor
  survived unchanged into each table's own existing `@media(max-width:700px)`
  card-stacking breakpoint, pinning the table box at its full desktop
  min-width even while every row inside it correctly stacked into a card.
  Confirmed live via `element.scrollWidth`/`clientWidth` at 390px before
  the fix (Software: 820/820, Communities: 880/880 — both pinned at their
  new desktop floor, well past the 390px viewport) and after (all three:
  scrollWidth equals clientWidth equals the container's real width, no
  overflow at all) — the on-screen card content looked identical in both
  states in a screenshot, since nothing visible actually wrapped or spilled
  off-screen; only the invisible box behind it was pointlessly wide,
  making the card swipeable-but-empty rather than genuinely full-width.
  Fixed with `min-width:0!important` added to each table's own existing
  `.admin-table-responsive{{display:block;width:100%;}}` mobile-card reset
  rule — the one place `!important` can legitimately beat an inline style.
  Confirmed at 900px (between the 700px breakpoint and each table's own
  min-width) that the desktop floor still works correctly post-fix: the
  table's own `scrollWidth`/`clientWidth` grow to fill the wider container
  (Software: 850/850, Communities: 880/880 pinned at floor, Users:
  1316/1317 — its many optional columns' own intrinsic content width
  already exceeds even a 900px viewport, correctly triggering the
  `overflow-x:auto` wrapper's scroll rather than squishing).

- **Width-tier collapse, four tiers to three (PR 13, 2026-09) — the "close
  enough to collapse" gap PR 12 flagged (`.page-full` 1440px and
  `.page-admin` 1400px, only 40px apart — a distinction no reader could
  perceive and no one could maintain deliberately) is now actually
  collapsed, not just noted.** `.page-full` and `.page-admin` both retire
  outright into one **Standard** tier, `.page-standard`, at the old
  `.page-grid`'s own 1300px — a genuine 3-into-1 merge of
  `.page-full`/`.page-grid`/`.page-admin`, with `.page-grid` itself also
  gone (not kept alive as an alias for the survivor). A new **Content**
  tier, `.page-content` (900px), splits off from `.page-full`'s old
  audience for the handful of pages that are pure long-form reading.
  **The one real question, answered before picking a value**: on an
  article page nearly everything already sits inside the 760px
  `.tool-prose` reading column, so the Content tier's value only affects
  whatever lives outside it. Enumerated per page before touching any CSS —
  About (everything is inside `.tool-prose`, nothing outside it at all);
  the three ported thought-leadership articles via
  `_original_content_article_body` (a back-link line, a tag label, `<h1>`,
  a byline — all short, all inside `.tool-prose` or immediately above it,
  nothing wide); `/tools/fpa-buddy/how-it-works` (a back-link/eyebrow/`<h1>`
  in `.tool-prose`, a diagram already pinned to its own `max-width:680px`
  wrapper — narrower than `.tool-prose` itself — and a data table already
  inside its own `overflow-x:auto` scroll container, so it never needed
  more room regardless of the outer shell); `/ask/history` (entirely inside
  `.tool-prose`). **Finding: essentially nothing on any of these pages
  needs more than the 760px reading column already provides** — 900px was
  picked as a little breathing room over that column, not because anything
  measurably needs it. **Recommendation, flagged rather than acted on**:
  given that finding, there's a real case for collapsing Content into
  Standard and shipping two tiers instead of three — the visual difference
  on every current Content page would likely be imperceptible. Built as
  three tiers per the brief anyway, so this is here for Brian to ask for
  the two-tier version in a follow-up if he agrees nothing is being lost by
  it. Every `.tool-inner`-wrapped "functional tool" page (FP&A Buddy chat,
  the GER calculator, Sail Don't Row + its leaderboard, both matchmaker
  chat pages) moved from `.page-full` onto `.page-standard` — a no-op in
  practice, since `.tool-inner`'s own 1300px cap already equalled
  Standard's value and was always the real binding constraint there, same
  as PR 12's own note that `/tools/fpa-buddy` was "unaffected in practice."
  Admin data tables (the old `.page-admin` audience) sit on the same 1300px
  Standard tier as every other admin page now, not a dedicated wider
  tier — they already carry their own `min-width` floors and
  `overflow-x:auto` horizontal scroll from PR 12 (see the "Part 2" bullet
  directly above), so narrowing their shell from 1400px to 1300px scrolls a
  wide table sooner, it doesn't squeeze its columns; re-verified directly
  against Users (the widest, with its column picker) at both the new
  1300px shell and a 390px mobile viewport — unchanged behavior at either
  width, since a table's own `min-width` and its `overflow-x:auto` wrapper
  govern its scroll independent of how wide the ancestor `.page-standard`
  shell is, as long as the shell doesn't hard-clip (it doesn't; it's a
  `max-width`, not `overflow:hidden`). `/read/{article_id}`'s
  `.reader-layout` — the one bespoke, non-`.page`-class exception PR 12's
  own width pass had to keep in sync by hand — moved onto `.page-standard`
  too, deliberately NOT the new Content tier: its two-column layout (a
  760px reading column plus a 220px sticky "On this page" TOC, joined by a
  40px gap) needs ~1020px of real headroom before any side padding, and
  Content's 900px would have forced the reading column to shrink below its
  own 760px floor via the flex layout's `min-width:0` — exactly the measure
  this page exists to protect. `.page-form` (640px) and `.tool-prose`
  (760px, 16px body) are both untouched, per the standing decision that
  settled them separately from this pass. `webapp.app._PAGE_TIER_RE`/
  `_PAGE_TIER_LABELS`/`_PAGE_INDEX_CUSTOM_EXCEPTIONS` (the machinery behind
  `/admin/system/page-index`) were updated to the new three-class set in
  the same PR — omitting this would have flagged every single page route
  on the site as untiered, since the regex that recognizes a tier class was
  hardcoded to the old four names. See BRAND.md §5 for the full tier table
  (now the authoritative, always-current version) and ARCHITECTURE.md's
  matching bullet for the technical write-up.

- **PR 15 (2026-09) — Users-page layout fixes, Bulk delete's non-standard
  layout corrected, prompt-text typography joins the mechanical lint, and
  four riders.** Two live-page review findings, both fixed: the "Add a
  member"/"Default usage caps" boxes on `/admin/users` weren't vertically
  aligned because "Add a member" had an `<h2>` above its form while the
  caps column had none at all (grid `align-items:start` means both columns
  start at the row top, but the left column's actual form was pushed down
  by its own heading) — fixed by giving the caps column a matching heading,
  not by nudging pixel values. "Delete selected" sat alone on its own row
  with nothing to pair with (Users has no bulk "Edit selected" sibling,
  unlike Software/Communities) — moved into the sort/filter toolbar's own
  row via a new `extra_html` parameter on `_admin_sort_filter_toolbar_html`
  (empty by default, so every other caller is unaffected), and switched
  from `disabled` to `hidden` so it disappears entirely rather than sitting
  grayed out — the existing bulk-delete confirm-panel/typed-guard mechanism
  is untouched. `/admin/reader/bulk-delete` wrapped its whole page body in
  one hand-picked `max-width:640px` card, unlike every sibling Reader tool
  (backfill-content, dedupe), which are full-width `.page-standard` with
  sections/cards sized to their own content — fixed to match; the
  preview-before-commit step (a separate route/page) was already correctly
  full-width and untouched.
  **Typography**: `linklib/enrich.py` (69 violations) and
  `linklib/feature_scan.py` (23) join `TYPOGRAPHY_SCANNED_FILES` — the
  PR 10 investigation that left prompt-assembly text out (reasoning it's
  text Claude reads, not HTML a person sees) is reversed for these two on
  the same grounds that got `VOICE_CORE_DEFAULT` held to this standard: the
  model reads and imitates prompt text, so a spaced em dash inside a prompt
  demonstrates the exact thing the prompt forbids. Mechanical fix only —
  spaced em dashes collapsed to unspaced, no rewording — applied only
  inside actual string-literal spans (via the same AST walk
  `typography_findings` itself uses), never touching `#`-comment prose.
  `"CFOs & VP Finance"` and `"Flux Analysis & Summaries"` are real terms,
  not lazy "and"s, so both join `AMPERSAND_NAMES` rather than being
  rewritten — the latter line-wraps mid-term inside a triple-quoted prompt
  string (`docs/FEATURE_TAXONOMY.md`'s §7 verbatim few-shot excerpt,
  `_UNIFY_TEST_EXCERPT`), which needed `scannable_copy`'s allowlist matcher
  to tolerate `\s+` between a term's words instead of a literal single
  space — a real, generally-useful checker fix, not a one-off workaround.
  That excerpt is the one place in `feature_scan.py` genuinely used as
  few-shot grounding the model is told to reason from; it carries no
  em-dash violation, only the now-allowlisted ampersand, so nothing about
  its demonstrated content changed. Every other em-dash fix in both files
  sits in ordinary instructional prompt prose or in roster-line formatting
  helpers (`_format_candidate_line`, `_format_queue_item_line` — data
  separators, not demonstrated output style), not inside a worked example.
  **Riders**: (1) Software's and Communities' approved-tables admin lists
  no longer carry their own individually-computed exception floors (820px/
  880px) — both now share the Xwide bucket (960px) and a 280px sticky Name
  column (Software widened from 220px), per Brian's explicit call that the
  two tables should be structurally identical, not merely similar; the
  first instance of a larger, separately-scoped column-width-standardization
  job, not that job itself. (2) The three PR-11 tables that shared a
  hand-picked 620px (Software categories, Community categories) or 640px
  (Resources) now reference `_TABLE_FLOOR_NARROW`/`_TABLE_FLOOR_MEDIUM` by
  column count like the other 22 tables. (3) `/admin/emails` 500'd
  unconditionally (not just on a fresh DB, contrary to this PR's own build
  brief — confirmed by reproducing it directly) because `NOTIFICATION_TYPE_
  LABELS` had no `library_submission` entry even though `_INTERNAL_EMAIL_
  ROWS` and the real `/library/submit` notification call site both use it
  (added in the Archive Queue retirement, PR 3) — fixed by adding the
  missing label; every other `notification_type` value already had one.
  (4) `linklib.brand_check._HEX_RE` mistook a PR/issue reference like
  `PR #529` for a 3-digit hex color — the third time this exact false
  positive has hit the repo (#465, #318, #529), each previously worked
  around by rewording the comment rather than fixing the checker. Fixed
  with a negative lookbehind excluding a `#\d{3}` match preceded by `PR `,
  `issue `, or `#` (case-insensitive), and the two previously-reworded
  comments ("PR 465", "PR 12 (PR 529)") were reverted back to their natural
  `PR #465`/`PR 12 (PR #529)` phrasing.

- **Public page polish + coral discipline (PR 16, 2026-09) — coral dropped from
  the icon-cycle array entirely, the MCP callout becomes the one deliberate
  coral moment, `/tools` splits into two columns, Reader moves out of the
  Toolbox tile grid, and Resources' card-height variance is fixed at the
  root.** `_CARD_ICON_STYLES` used to cycle seafoam/navy/coral by array
  index for every card-row/tile grid sitewide — so whichever card landed in
  the third slot spent the site's one rare accent by array position, not
  deliberate placement: Communities on `/tools` directly, and (worse) twice
  over on the homepage (the Toolbox panel's Communities mini-tile AND the
  Recent Highlights grid's Podcasts tile). Coral is dropped from that cycle
  outright — every caller now cycles seafoam/navy only (`% 2`, not `% 3`) —
  freeing coral for the placement `_mcp_callout_html()` always wanted but
  couldn't have (that function shipped navy specifically because coral was
  already spent elsewhere; it's coral-wash + navy text now, the same
  sanctioned pairing every other coral badge on this site already uses,
  still non-clickable, never a button). **One real, non-obvious side effect
  of the `%3 -> %2` change, caught by grep sweep before shipping**: FP&A
  Buddy's homepage mini-tile icon (`_ICON_HALF_CIRCLE`) has a hardcoded
  `#1F7A66` fill that the pre-existing cycle happened to pair with a
  seafoam badge ring purely by array-index coincidence (index 3, `3%3==0`);
  under the new 2-color cycle that same index lands on navy (`3%2==1`),
  which would have put a navy ring around a seafoam-toned fill — a real
  color clash, not just an unrequested cosmetic shift. Fixed with a small,
  explicit `_TOOLBOX_BADGE_INDEX`/`_toolbox_badge_index()` pin (FP&A Buddy's
  badge index is pinned to 0/seafoam regardless of its position, on both
  `/tools`' own grid and the homepage mini-tile row, so the same tile
  reads identically on both surfaces) rather than leaving it to accident a
  second time. **A second candidate side effect, investigated and
  deliberately left alone**: the Original Content flagship cards'
  `_OC_TAG_COLORS` cycle (a separate, three-color `coral-deep`/
  `seafoam-deep`/`navy-light` rotation for the homepage's/`/thought-leadership`'s
  featured-piece tag labels) also uses coral, and the Growth Engine Ratio
  card always lands on it by position — but that's a small coral-DEEP TEXT
  label, not a coral background fill, and doesn't compete for the same
  rare-accent budget the icon-cycle bug was about; verified this doesn't
  double up with the new coral MCP callout via the coral-moment check
  below (which only counts backgrounds), not merely assumed.
  - **`coral_moment_problems()`** (`webapp/app.py`, wired into
    `webapp/checks.py`'s `run_all()` as a live `/admin/checks` row and
    into CI via `tests/test_coral_discipline.py`) is a new, honestly-scoped
    best-effort check: it renders every real public (non-`/admin`, no
    path-param) GET/HTML route signed out and flags any page with more
    than one coral **background** declaration inside an actual rendered
    `style="..."` attribute — deliberately never a `<style>` block's own
    CSS rules (which can declare a class no element on that render
    actually carries — a matchmaker feedback button's `.sel-neg`, the
    Reader's `.rr-alert`, both real, already-sanctioned coral uses that
    only ever paint on user interaction or a real error state) and never a
    `<script>` block's JS template strings. `<head>` (the shared sitewide
    stylesheet, including the admin-only pending-count-badge exception)
    and each response's own `<header>`/`<footer>` are excluded as chrome,
    per the brief's explicit scope. Admin pages are out of scope entirely —
    BRAND.md already sanctions more than one coral element there
    (pending-count badges, review-status pills), so a blanket "at most
    one" rule doesn't apply to that surface. See BRAND.md §8's own bullet
    for the full, itemized list of what this can and can't catch — stated
    there rather than implied, per the brief's own ask. Verified the
    detector actually catches a regression, not just passes trivially: a
    test injects a second coral background into the MCP callout and
    confirms both `/` and `/tools` get flagged.
  - **`/tools` splits into two columns** (`.toolbox-layout`, 1fr/1fr) —
    left is heading/intro/bullets/MCP callout, right is the 2x2 tile grid,
    `position:sticky` so the cards stay visible while the left column's
    text scrolls. Before this the page stacked heading -> intro -> six
    bullets -> MCP callout -> 2x2 grid, pushing the tile grid a full
    screen below the fold on an ordinary viewport. Split chosen by
    rendering both a 40/60 and a 50/50 layout and reading the actual
    output, not by formula: at 40/60 the left column narrows to ~480px and
    the intro's longest bullet ("Which community is actually full of
    finance executives who've done the job, not just anyone who signed
    up?") wraps into several short, ragged lines; 50/50 (~600px each side
    at `.page-standard`'s 1300px) gives the bullets a full, comfortable
    line length and still leaves the tile grid ~280px per tile, legible
    side by side. Below 900px both columns stack (DOM order already puts
    text before cards, so no reordering needed) and sticky turns off.
  - **Reader pulled out of the Toolbox tile grid into its own standalone
    card** below it — it was an orphaned 5th tile crammed under a 2x2 grid
    whose other four ARE the Toolbox's real components (software,
    communities, benchmarks/books, FP&A Buddy); Reader is Brian's own
    private reading tool, not a sixth Toolbox component. The card's markup
    moved into a new shared `_reader_access_card_html()` (used by both
    `homepage()` and `tools_landing()`) so the two surfaces can't read
    differently — confirmed byte-for-byte identical between the two pages
    for a signed-in admin, and confirmed absent from the rendered HTML
    entirely (not just CSS-hidden) for a signed-out visitor on both pages.
    The old, less accurate `/tools`-only copy ("Your private reading
    stash—Feed, Archive, and Read Later, admin only") is gone; the
    homepage's own copy ("Shown here only when logged in as admin. Feed,
    Archive, and Read Later in one place.") is what ships on both now, per
    the build brief's explicit call that the homepage version names all
    three views and the old `/tools` version didn't. `_ICON_BOOK` (the
    glyph the old 5th tile used) is now genuinely dead — both cards use
    `_ICON_NEWSPAPER` via the shared helper — and is deleted rather than
    left behind unused.
  - **Resources' book-card height variance, root-caused rather than
    padded away.** Diagnosis (seeding the real `scripts/
    seed_book_recommendations.py` copy and the real `_DEFAULT_BENCHMARKS`
    copy side by side, then measuring real Playwright bounding boxes, not
    guessing): this was never a missing height constraint specific to
    Books, and never a different card component — Books and Benchmarking
    share one `.bench-card`/`.bench-desc` class. `auto-fill` CSS Grid's
    default `align-items:stretch` only equalizes cards WITHIN their own
    row; different rows are sized independently by their own tallest
    card, so a row of short names/descriptions renders visibly shorter
    than a row that happens to contain a long one. Benchmarking's more
    consistent, template-driven ("Best for X") copy length happened to
    keep its rows close enough in height to read as "already uniform" at
    its current row count and card mix — seeding it with enough rows to
    span two rows reproduced the identical row-to-row variance Books was
    showing, proving it's the same underlying mechanism on both sections,
    not a Books-specific bug. PR 16 fixed it with a clamp (2 and 3 lines) plus a
    `.bench-card` `min-height`. **Superseded 2026-10 (PR 660, Brian's call, "Option
    2"): the clamp and the min-height are both removed**, and there is no "Show
    more". Names and descriptions show in full; cards keep the
    `_CARD_WIDTH_RESOURCE_MIN` floor on the `auto-fill` grid; cards in a row
    stretch to equal height (grid default) and rows may differ. Reason: a clamp
    hides text. The only clamps left are the two directory cards, which keep
    "Full profile →". See `tests/test_resources_card_no_clamp.py`.
  - **The Resources intro-copy question was investigation-only, per the
    brief's own approval-gate rule** — a page-level paragraph introducing
    "what the resources are" (Brian's original ask) genuinely doesn't
    exist; what exists are two separate SECTION-level one-liners
    ("The benchmarking sources I actually use." / "A personal reading
    list—not benchmarking data.") with nothing framing the page as a
    whole before the Benchmarking section starts. No copy was written or
    shipped for this — any new copy is a stop-for-approval item, reported
    in the PR description as a draft, not committed. **Approved with edits
    the same PR** (before merge): "What's here: the benchmarking sources I
    rely on, and books that shaped how I do this job. Not exhaustive, just
    what's held up." — the draft's "actually" dropped (already used twice
    elsewhere on this page/its Toolbox card blurb) and its em dash swapped
    for a comma. Shipped between the H1 row and "Suggest a resource →",
    exactly as proposed.
  - **A real, previously-latent infinite-recursion bug found and fixed
    before merge, via `coral_moment_problems()` itself, not a code-review
    guess** — `webapp/checks.py`'s `run_all()` had one existing structural
    hazard nothing had ever triggered: `webapp/tasks.py`'s
    `_failing_checks_count()` (which badges the admin nav's own "Admin"
    link) calls that same `run_all()`, and `_page()` calls it on EVERY
    page render for a `role=="admin"` visitor. `_is_authed`/`_role` treat
    *every* visitor as admin when neither `LINKLIB_PASSWORD` nor
    `LINKLIB_SAVE_TOKEN` is configured — the documented "open, local-dev
    convenience" mode. Every check in `run_all()` before this PR was pure
    route/tuple introspection with no HTTP request involved, so this
    structural cycle was inert. `coral_moment_problems()` is the first
    check to actually render pages via `TestClient`, which made the cycle
    real: in open-auth mode, `coral_moment_problems()` renders `/` ->
    `_page()` (role admin) -> `_has_open_admin_tasks()` ->
    `_failing_checks_count()` -> `run_all()` -> `coral_moment_problems()`
    again, without end. Reproduced live to at least depth 3 before being
    killed — a genuine, not theoretical, infinite recursion, with runaway
    memory growth (confirmed in this session's own attempted local full-suite
    runs, which stalled and ballooned to multiple GB before being killed —
    `tests/test_checks.py` was the one file exercising this path, since it
    never set a password, unlike every other test file's `env` fixture).
    Fixed with a plain module-level `_CORAL_CHECK_IN_PROGRESS` guard around
    `coral_moment_problems()` — deliberately not thread-local, since
    Starlette's `TestClient` blocks the calling thread for the duration of
    each request, so there's no genuine concurrency to guard against here,
    only recursion within one logical call chain; a nested call returns
    `[]` immediately (see the guard's own comment for the full reasoning).
    `tests/test_checks.py` also picked up the same `env` fixture
    (password + secret key + fresh DB, reloaded per test) every other test
    file already uses, closing the specific gap that let this go
    undetected — the underlying open-auth-mode "every page render pays for
    a full `run_all()`" behavior is unchanged and out of scope for this
    PR, only the recursion is fixed. Verified both ways, not just that the
    fix works: reverted the guard locally and reproduced the exact hang
    again before restoring it, so the regression test
    (`tests/test_coral_discipline.py::test_coral_check_does_not_recurse_in_open_auth_mode`
    plus a sibling asserting `_has_open_admin_tasks()` itself returns
    promptly) is pinned against a confirmed-real failure mode, not a guess.

- **FP&A Buddy page redesign (PR 17, 2026-09) — get to the point, put the
  example beside the description instead of below it, fix what's under the
  Ask button, settle the page's width.** The build brief's own quoted
  "existing tagline" turned out not to exist anywhere in the codebase —
  checked directly, not assumed — so the top intro is genuinely new copy,
  drafted and approved by Brian in-session rather than lifted from an
  existing string: "Ask a real FP&A question and get an answer with its
  sources, not half a day of Googling. It pulls from a research archive I
  curate by hand, and it remembers the thread, so you can follow up."
  Replaces the old h1-then-paragraph intro and, more importantly, the
  bottom "What FP&A Buddy can do" bulleted box, which is retired outright —
  three of its five bullets restated content already on
  `/tools/fpa-buddy/how-it-works` (citations, hybrid retrieval, the
  human/AI pipeline), so keeping both was exactly the duplication the
  standing "less and simpler" rule exists to catch. Brian's own call on the
  two bullets that weren't duplicated elsewhere: "remembers the thread"
  (conversation memory) earns a clause in the new intro because it changes
  what question someone should even type first; "gets sharper" (feedback-
  driven improvement) doesn't, since it's a claim about the product's
  trajectory rather than something the reader can act on right now, and
  it's already visible in-product via the feedback buttons. The `#fpa-
  features` anchor and its "Scroll down" teaser link are gone with the box
  they pointed at; the teaser now reads "Curious how this works? Read the
  full breakdown →", linking straight to the how-it-works page.
  **Top section is a real two-column layout**, built with CSS Grid
  `grid-template-areas` rather than plain source order — the only way to
  give desktop and mobile genuinely different visual placements of the
  same DOM children with no JS and no `order` property. First draft put
  the illustrative example in a second, fixed column beside the intro
  alone; live review found ~350px of dead space under the shorter left
  column at desktop width, since three sentences can never balance a full
  mocked conversation. Restructured on Brian's direction: desktop now
  stacks description → usage line → Question box down the left column
  (named grid areas `intro`/`usage`/`question`) so the input sits above
  the fold and the gap fills with real form instead of empty space, while
  the illustrative example (`example`) spans that whole column's height on
  the right, scaled down via selectors scoped to `.fpa-intro-area-example`
  only — `.ask-example`/`.ask-q-bubble`/`.ask-answer` are shared with the
  real, live-rendered thread lower on the page, so the scale-down can't
  leak into it. Mobile collapses the same grid to a single `intro`/
  `example`/`usage`/`question` column — the exact order this page already
  used before the redesign, confirmed reading well and kept unchanged;
  `grid-template-areas` is what let that order survive the desktop
  restructure with zero DOM reshuffling, verified directly via
  `getBoundingClientRect()` y-ordering at 390px, not just eyeballed.
  **Round 2, same PR, per a second round of direct feedback**: the
  first draft's left column (intro/usage/question only) still left ~350px
  of dead space under it at desktop width next to the taller mocked
  example — three sentences and a Question box can't fill that height.
  Sources, Depth, and the Ask button moved INTO the same left column too
  (two more grid rows, `controls`/`action`, with a `.` placeholder keeping
  `example` confined to only the intro/usage/question rows above them) so
  the whole Ask form reads as one continuous shape instead of a form whose
  own controls span past the column's left edge. Sources (3 chips) and
  Depth (3 buttons) share `.ask-controls`' own 1fr/1fr split everywhere
  else on the site, but at this column's ~600px width that's only ~280px
  per side — fine for Depth's three short buttons, tight enough that
  Sources' longer labels wrap to two ragged lines; checked both ways with
  real screenshots before choosing stacked (Sources above Depth) as the
  cleaner read, via an unconditional override
  (`.fpa-intro-area-controls .ask-controls{grid-template-columns:1fr}`,
  not gated behind a media query, since this container is narrower than
  `.ask-controls`' own 640px breakpoint regardless of the real viewport).
  Same round also closed the ~30px bottom-edge mismatch between the
  example card and the Question box — free, not a magic-number height:
  `align-self:stretch` on both grid items plus `flex:1` on the actual
  visible cards inside them lets CSS Grid's own auto-sizing algorithm do
  the work (a multi-row spanning item taller than the rows it spans grows
  the LAST of those rows to fit, which is the same row the Question box
  lives in) — verified via real `getBoundingClientRect()` measurements at
  both 1280px and 1920px: **0.0px difference** between the two cards'
  bottom edges, not just "close." Mobile gained `controls`/`action` as two
  more rows in its own single-column `grid-template-areas`, in the exact
  position they already occupied — zero visual change there, confirmed via
  the same before/after y-ordering check as round 1.
  **Below the Ask button, order was Ask → Recent conversations (a JS-
  populated, initially-hidden list) with "Search past questions" stranded
  ABOVE the Question box** — not "Recent conversations above Past
  questions" as the build brief's own premise assumed; checked directly
  against source before touching anything. Moved to Ask → Past questions →
  Recent conversations, the requested order. **Back link changed from `/`
  ("← Home") to `/tools` ("← Toolbox")** — grep confirmed this was the only
  remaining "← Home" holdout on the whole site; every Toolbox directory/
  tool page, and `/read`'s own rail back-link, already used "← Toolbox".
  **`.tool-inner` removed from this page as a dead no-op**: `.tool-inner`
  and `.page-standard` have both been 1300px since the PR 13 tier collapse,
  so nesting one inside the other constrained nothing. Confirmed by
  checking every other `.tool-inner` call site before touching any of
  them (GER calculator, Sail Don't Row + leaderboard, both matchmaker chat
  pages) — all four still use it for a real reason (bounding a
  calculator/game canvas/chat panel to a card-grid width distinct from a
  possible future wider `.page-standard`), so none of them were swept into
  this PR; only FP&A Buddy's own wrapper, which had nothing left to do,
  came out. See ARCHITECTURE.md's "FP&A Buddy page redesign (PR 17,
  2026-09)" bullet and BRAND.md §5's `.tool-inner` passage for the
  technical write-up.
  **Round 3, same PR, per a third round of direct feedback**: round 2's fix
  traded one dead-space problem for another — confining Sources/Depth/Ask
  to the ~600px left column left ~450px empty to their right, under the
  wider example card. Full width was never the problem; the mismatched
  left edge round 2 originally fixed was. Reverted `controls`/`action` back
  to spanning both grid columns (`"controls controls"`/`"action action"`
  in `grid-template-areas`, not `"controls ."`/`"action ."`) — since
  `example`'s own row-span is set by which rows *it's* listed against, not
  by how wide the controls/action rows are, this doesn't reopen round 1's
  dead-space bug or disturb the bottom-edge alignment fix; both keep
  working. Column 1 still starts at the same x regardless of how many
  columns a row spans, so Sources/Depth/Ask still open flush with the
  Question box's left edge (0.00px diff at both 1280px and 1920px) while
  running full width. Round 2's forced single-column stacking override on
  Sources/Depth is removed — the shared `.ask-controls` 1fr/1fr split
  renders as-is, same as everywhere else it's used: at 1920px Sources fits
  one line and sits genuinely side by side with Depth; at 1280px Sources
  wraps to two lines while Depth doesn't, confirmed identical against
  `origin/main`'s pre-PR-17 markup for this exact component (byte-for-byte
  unchanged CSS) — a pre-existing asymmetry, not something this PR
  introduced or is scoped to fix. The ~60px Depth-to-Ask gap was a genuine
  round-2 artifact (stacked layout summed `.ask-controls`' 20px bottom
  margin + the grid's 16px row-gap + `.ask-action-row`'s 22px top margin);
  zeroing both components' margins (each has exactly one call site,
  confirmed by grep) leaves only the grid's 16px row-gap everywhere in this
  section — verified at 16.5px at 1920px, matching every other gap on the
  page. At 1280px the same gap still measures ~57.5px, but that's the
  Sources-wraps-to-two-lines asymmetry above showing through (the shared
  grid row's height is set by the taller column), not unresolved margin
  stacking — reported rather than chased further, since fixing it would
  mean reflowing Sources' chip labels or un-pairing Sources/Depth from a
  shared row, neither of which was asked for.
  **Round 4, same PR, per a fourth round of direct feedback**: Sources and
  Depth stop sitting side by side — they're the same kind of setting (a
  source-list choice, a depth choice), so a left/right split made the eye
  travel left, then right, then back left for Ask, instead of reading as one
  sequence. Fixed with a second override on the same selector round 3 used
  for the margin zero-out — `.fpa-intro-area-controls .ask-controls{grid-
  template-columns:1fr}` — rather than touching `.ask-controls`' own shared
  1fr/1fr rule (still exactly one live call site, confirmed by grep). This
  also closes the chip-wrapping problem round 3 left unresolved: at the
  page's full ~1300px width, all three Source chips — including "Web search
  (trusted sites)", deliberately not shortened, since the trusted-sites
  qualifier does real work — fit on one line; confirmed via
  `getBoundingClientRect()` on every chip, one distinct y-value at both
  1280px and 1920px. `.ask-controls`' own default 20px row-gap already
  matches "the same ~20px gap as the rest of the control spacing" once the
  column count drops to one — confirmed at exactly 20.0px between Sources
  and Depth at both widths; Depth-to-Ask stayed at the outer grid's own
  16.5px row-gap, a separate rule the ask didn't name. Left-edge alignment
  (0.00px) and full width were unaffected — both already established by
  round 3. Mobile (390px) confirmed unaffected via the same y-ordering
  check as every prior round.
  **Round 5, same PR — chip labels shortened, chips within each row made
  equal width.** Source labels: "My saved archive"/"Current RSS feed"/"Web
  search (trusted sites)" → "Saved archive"/"RSS feed"/"Trusted web" — the
  top intro already establishes these are Brian's own sources, so the
  chips drop "My"/"Current"; "Trusted web" deliberately keeps the
  trusted-sites qualifier rather than shortening to a bare "Web," since it
  does real work. No test asserted the old strings (confirmed by grep).
  Equal widths within each row (Sources' three match each other, Depth's
  three match each other, rows independent) via `.fpa-intro-area-controls
  .ask-tags{grid-template-columns:repeat(3,1fr)}` + `width:100%` on
  `.ask-tag` — `.ask-tags` has exactly two live call sites, both on this
  page, confirmed by grep, so this scopes the same way every other override
  on this page does rather than editing the shared flex-wrap rule. Verified
  equal at 1280px/1920px (405.3px/412px per chip, exact match) with no
  wrapping at either width. **Flagged per the explicit ask, not silently
  fixed**: at 390px, equal-width sizing wraps the two-word Source labels
  ("Saved archive," "Trusted web") to a second line — Depth's single-word
  labels stay single-line. Confirmed via screenshot it reads cleanly
  (uniform chip height, no overflow, `scrollWidth` still exactly 390) and
  the mobile stacking order is unaffected, but it's a real behavior change
  from the prior single-line flex-wrap layout, reported rather than
  assumed fine.
  **Round 6, same PR — chips go back to natural width (round 5's
  stretch-to-fill was a misread of the ask), the ~110px gap above the
  Question box is closed to 20px, and Ask-before-Past-questions is
  confirmed correct as-is.** Round 5's `grid-template-columns:repeat(3,1fr)`
  + `width:100%` stretched every chip to fill its row — not what was asked;
  the real ask was "equal width within a group, sized to that group's own
  widest label, left-aligned, not full-width." Reverted to `.ask-tags`' own
  default `flex-wrap` row (natural per-chip width with zero override) and
  added `fpaEqualizeChipWidths()`, a small page-load JS function
  (`getBoundingClientRect()` per chip, apply the group max as a fixed
  `width` to every chip in that `.ask-tags`) — there is no CSS-only way to
  size N flex/grid siblings to the widest one's *natural* content width
  without either stretching to fill the container (round 5's approach,
  reverted) or duplicating the widest label's text into every cell; a JS
  measurement also sidesteps a hardcoded pixel value potentially not
  matching a real browser's font metrics, since this sandbox can't load the
  sitewide Google Fonts (see the standing `capture_homepage()` limitation
  note above). Verified: Source chips 149.2px each (all three, sized to
  "Saved archive"), Depth chips 113.4px each (sized to "Standard"), at
  1280px, 1920px, and 390px alike — widths are font/text-driven, not
  viewport-driven, so one run on load is enough.
  **The gap fix took real debugging, not a CSS one-liner, and surfaced a
  genuine CSS Grid subtlety worth keeping**: the `example` card spans
  multiple rows (`intro`/`usage`/`question`) via `grid-template-areas`, and
  when its own content exceeds those rows' combined natural height, the
  leftover growth doesn't confine itself to the last-spanned row by
  default — every plain `auto` row it spans shares the excess. An initial
  fix attempt, `grid-template-rows:max-content max-content auto auto auto`
  (intended to cap `intro`/`usage` at their own content size and force all
  overflow onto `question`), measured **zero effect** live — confirmed
  independently in an isolated standalone test file
  (`grid_test.html`/`pw_gridtest.py`) reproducing the same structure, which
  showed the same failure (`186px 132px 150px`, `intro`/`usage` still
  inflated). Root cause: per the CSS Grid spec's own "distribute space
  beyond growth limits" fallback step, once every spanned track has hit its
  growth limit and space still remains, ALL of them — even ones capped at
  `max-content` — grow further to absorb the remainder; `max-content` only
  bounds the earlier "resolve intrinsic sizes" pass, not this later
  fallback. The fix that actually works, confirmed in the same isolated
  test before touching the real page: make `question`'s row `1fr`
  (`grid-template-rows:auto auto 1fr auto auto`) — a flexible track is
  sized in a separate, later distribution pass that exclusively absorbs
  leftover space, so `1fr` (not `max-content`) is the correct tool whenever
  one specific track, and only that track, needs to swallow a spanning
  item's overflow. This alone dropped the gap from ~93px to 32px — the
  remaining 32px being two genuine 16px structural row-gaps bracketing the
  `usage` row, which is completely empty for a fresh/admin session with no
  cap tracked. Closed the rest by treating that emptiness as a fact to act
  on, not a gap to paper over: `usage_html`'s own div/grid-area/row is now
  omitted entirely from both the HTML and every `grid-template-areas`/
  `grid-template-rows` string (desktop and mobile) whenever it's empty,
  computed via new `usage_div`/`_intro_areas_desktop`/`_intro_rows_desktop`/
  `_intro_areas_mobile` Python variables — and `.fpa-intro-layout`'s
  `row-gap` moved from 16px to 20px, deliberately reusing round 4's own
  already-established Sources→Depth spacing value (the ask's own "the same
  spacing used elsewhere in the form" pointed at that value, not an
  arbitrary pick) rather than inventing a new number. Landed the gap at
  exactly 20.0px at both 1280px and 1920px — bottom-edge alignment between
  the example card and the Question box stayed at 0.00px throughout, since
  the `1fr`/`align-self:stretch` mechanism from round 2 is untouched.
  **Ask-before-Past-questions needed no code change** — the DOM already
  rendered Ask first; verified by reading the render order and confirming
  live it matches on all three widths. Mobile (390px) re-verified after
  every change: visual order `intro → example → question → controls →
  action` (`usage` genuinely absent from the DOM, confirmed via a direct
  element-presence check, not just inferred from the CSS), `scrollWidth`
  exactly 390 (no overflow), and Source chip widths still 149.2px/equal —
  font-driven sizing, so mobile matches desktop exactly.

- **Growth Engine Ratio tier correction, sitewide (PR 18, 2026-09) — the F Suite's
  final whitepaper standardized the performance tiers; this PR found and fixed every
  place besides the article itself still carrying the old $0.70/$0.50 cutoffs.**
  New bands: Elite > $1.20 (unchanged), Strong $0.80–$1.20 (was $0.70–$1.20), Average
  $0.60–$0.80 (was "Typical" $0.50–$0.70), Below target < $0.60 (was < $0.50) — with
  matching break-even-year ranges (< 0.8 / 0.8–1.25 / 1.25–1.7 / > 1.7 years).
  **Verified first, not assumed stale**: the `articles` mirror for
  `https://bmweis.com/thought-leadership/growth-engine-ratio` (id 4760,
  `original_content.mirrored_article_id`) already carried the corrected tier table,
  break-even wording ("Years to Break Even = 1 ÷ Growth Engine Ratio"), and the
  "Contraction stretches it, and severe contraction means it never pays back" rewrite
  — `sync_original_content_article()` had already fired correctly when Brian edited
  the article through the admin UI, so no resync was needed. What was actually stale
  was the standalone `growth_engine_calculator()` route
  (`/thought-leadership/growth-engine-calculator`), a genuinely separate bespoke
  route the article's own admin edit can't touch: `gerTier()`'s threshold checks
  (`ratio >= 0.70`/`ratio >= 0.50` → `0.80`/`0.60`), the retired "Typical" label
  (now "Average"), the timeline chart's benchmark bands/y-axis seed domain
  (`band(1.20,0.70,...)`/`[0.5,0.7,1.0,1.2]` → `0.80`/`0.60` and `[0.6,0.8,1.0,1.2]`),
  its tier-label chart positions (recomputed to the new bands' midpoints), and one
  wrong metric name ("Efficiency Ratio" → "Growth Engine Ratio") inside the
  contribution-diagram SVG caption. Also fixed: the admin brand-showcase page's own
  GER swatch/table copy (`GER 'Typical' tier.` → `'Average'`, `$0.70–1.20` →
  `$0.80–1.20`) and two `linklib/brand_check.py` comments naming the retired tier.
  **Deliberately left untouched**: `scripts/migrate_growth_engine_ratio_content.py`'s
  own `BODY_MD` constant — a one-time migration script whose job already ran (its
  content was superseded by Brian's later direct admin edit, confirmed live), so its
  frozen old-tier copy is historical record, not live-rendering content; updating it
  would change nothing about what's on the site. `_TL_FEATURED_CARDS` (the dead,
  unimported rollback-reference tuple) has no tier numbers in its GER card copy, so
  it needed no change either. New tests
  (`tests/test_original_content_article.py`) pin the corrected copy and — via a
  Node subprocess that extracts and executes the real `gerTier()` function verbatim
  from the rendered page, not a Python reimplementation — the exact boundary ratios
  ($1.20/$0.80/$0.60/$0.59) and the break-even formula (`1 / ratio`) at its three
  named boundary years.
- **Icon fill contract check (PR 18, 2026-09) — closes the latent gap PR 16's own
  investigation flagged but didn't build a guard for.** A plain stroke-path `_ICON_*`
  constant inherits its color from whichever badge the position-based seafoam/navy
  cycle (`_CARD_ICON_STYLES`) assigns it, so cycling the array is safe by
  construction — but an icon with its own hardcoded `fill="#..."` (the only instance
  today, `_ICON_HALF_CIRCLE`) opts out of that, and was only ever safe by coincidence
  until PR 16 pinned it explicitly (`_TOOLBOX_BADGE_INDEX`). Nothing mechanically
  enforced that pin actually holding. `linklib.brand_check.icon_fill_contract_
  problems()` (folded into `findings()`, so it rides the same "Brand standards"
  check/test and `/admin/checks` row every other palette rule already does) now
  flags two things: an `_ICON_*` constant with a hardcoded fill that has no entry in
  a new `ICON_FILL_CONTRACTS` registry (icon name → required href + badge index), and
  a registered icon whose href doesn't actually resolve to its required index in
  `_TOOLBOX_BADGE_INDEX` — a stale/mistuned pin, not just a missing one. Verified
  against both failure shapes with a planted decoy (an unregistered fixed-fill icon,
  and a registered one repointed to the wrong index), each torn down immediately
  after asserting the flag — see `tests/test_brand_standards.py`'s three new tests.
  The one real instance today (`_ICON_HALF_CIRCLE` → `/tools/fpa-buddy` @ index 0)
  is registered and clean.
- **Admin table column widths, by field type (PR 19, 2026-09) — the natural
  next step after PR 14's floor buckets: a column is now sized by what it
  HOLDS, not by whatever a given page happened to pick.** Five constants
  (`_COL_WIDTH_NAME`=280, `_COL_WIDTH_EMAIL`=220, `_COL_WIDTH_DATE`=140,
  `_COL_WIDTH_STATUS`=110, `_COL_WIDTH_COUNT`=80 — 280 matches the
  Software/Communities sticky Name column PR 12/15 already established)
  are plain `width:` hints on ordinary (non `table-layout:fixed`) tables,
  applied wherever a column's own header literally names that field type;
  a free-text/URL/description/reason column stays unwidthed and absorbs
  the remaining space, same as before. Since these tables aren't
  fixed-layout, a hint is never a hard cap — real content wider than the
  hint still grows the column rather than getting clipped, so there's no
  truncation risk in applying one sitewide value everywhere. **Scoped to
  auto-layout ENTITY-LIST tables, not every `<table>` on the admin
  surface** — a first documentation pass over-counted by treating every
  table that doesn't use these constants as an "exception," when most were
  never in scope: `table-layout:fixed` tables use a different sizing
  mechanism entirely (Resources' percentage table, PR 11; `/admin/reader/feeds`'s
  `.ff-table`/`.fs-table`; `/admin/library-backup`'s `.backup-log-table`,
  each with its own tuned percentage or named-class pixel widths), and
  diagnostic/reference tables aren't entity lists at all (the three
  `.cc-table`-styled pages — Database, Page index, the FP&A Buddy
  explainer's tier table; the admin brand-showcase page's two illustrative
  component tables; the Reader content-backfill's three fetch-attempt
  worklists, with their own already-tuned 420px Article column and shared
  `_th`/`_th_nowrap` helpers; the Software name-duplicate check's rich-cell
  Tool A/B comparison tables). That leaves **two real exceptions**, both
  genuine entity-list tables this standard does cover: Overhead spend's two
  narrow flex-column summary tables (a tighter `min-width` than 280px
  allows), and the Users table's Username column (no clean field-type
  match, always visible — its Name/Email/Last login/Status columns do use
  the shared constants) — within the handful this standard's own PR
  anticipated. See BRAND.md §5 for the full scope statement. The
  Software/Communities approved-tables' own sticky Name column
  (`admin-sticky-col-2`) — the precedent 280px is drawn from — was also
  switched to read `min-width:{_COL_WIDTH_NAME}px` from the same constant,
  since it's the origin case, not an exception.
- **Layout fix batch 1, PR 28 (2026-09) — a real CSS scoping bug that
  silently hid a collapsed child group's badge, plus a mobile truncation
  bug on the Communities forms that hid real saved data on a phone.**
  Findings from `docs/LAYOUT_AUDIT_TRIAGE.md` (PR 544), fixed in order of
  how much information they cost.
  1. **`.admin-group[open] .group-badge{display:none;}` was a descendant
     selector, not a child selector** — it matched every `.group-badge`
     nested anywhere under an open `.admin-group`, not just that group's
     own summary. Since CFO Toolbox nests Software/Community/FP&A Buddy/
     Reader as sub-groups that stay individually collapsed, opening CFO
     Toolbox hid every one of those still-collapsed children's own badges
     too — the exact "a real pending item silently disappears" failure the
     whole collapsed-badge mechanism (see the 2026-09 "Admin menu
     default-state" bullet above) exists to prevent. Fixed by scoping to
     `> summary`: a child combinator that can only ever reach the group's
     own `<summary>`, never a nested sub-group's own (which lives in the
     sibling `<div>` after `<summary>`, not inside it). Verified the
     aggregation itself was never broken — every nested sub-group
     (`software_subgroup_html`, `communities_subgroup_html`, etc.) already
     computed its own `_group_badge` over its own `badge_hrefs`, so fixing
     the CSS didn't need a second fix to restore per-child counts.
  2. **`DOT_ONLY_HREFS` retired — the admin page always shows a real count
     now, even for an "all-or-none" source (Contact submissions, Toolbox
     intros).** Confirmed by inventory before changing anything: the
     mechanism was consumed in exactly two places, both admin-page-only
     (`webapp.app._badge_for_href`/`_group_badge`, used only by
     `admin_page()`'s card and group-aggregate rendering) — it never
     touched the top nav bar's own dot (`.task-dot`,
     `_has_open_admin_tasks()`), which was already a pure "is anything
     pending at all" boolean unrelated to DOT_ONLY_HREFS, so Brian's
     "nav bar: dot only, admin page: always show counts" split was already
     true for the nav bar and only needed fixing on the admin page.
     `_task_badge_dot()`/`.task-badge-dot` are deleted as genuinely dead
     code once nothing calls them. The dedup-safe counting rule (one
     `COUNT(DISTINCT id)` per entity, conditions OR'd, from the "Badge
     dedup safety" bullet above) is untouched — `_group_badge` still just
     sums whatever `webapp.tasks.open_task_counts()` already computed
     per href, it never re-derives a count of its own.
  3. **Mobile truncation on the Communities forms — real saved data,
     unreadable at 390px without clicking into the field.** Two hardcoded
     `grid-template-columns:1fr 1fr` grids (the Reach/Demographic pair in
     `_community_form_fields_parts`'s `details_html`, and the 10-field
     "Quick facts" block in `_community_profile_form_fields`) squeezed
     each column to ~170px at phone width — enough to truncate a real
     value like "VPs and directors in SaaS finance" to "VPs and
     directors." Fixed by switching both to
     `repeat(auto-fit,minmax(200px,1fr))`, the exact responsive pattern
     already used two sections lower in the same function (the Cost/
     Sponsor sub-columns) — not a new pattern. Both fragments are shared
     by all three call sites that render a community's Reach/Demographic
     or Quick facts fields: `/admin/tools/communities/new`, the public
     `/tools/communities/{slug}/edit` (both via
     `_community_form_fields_parts`), and
     `/admin/tools/communities/{community_id}/profile` (via
     `_community_profile_form_fields`) — fixing the two shared functions
     once fixed all three, confirmed by rendering each route directly
     rather than assumed from the shared-function relationship alone.
     Deliberately untouched: `stage_focus`/`jobs_program`/
     `team_or_individual`'s own collected-but-never-publicly-rendered
     status (see the "Surface Hidden Community Profile Fields" bullet
     above for two of these three; `team_or_individual`, `stage_focus`
     and the rest were later retired outright in PR 2a, see the community
     profile cleanup bullet) — this PR is layout only, not a content-scope
     change.
  4. **Three copy-length fixes, approved before shipping** (per the
     standing em-dash/new-copy review discipline): the third-party content
     admin form's (`/admin/thought-leadership/third-party/new` and its edit
     twin) Title/Date label/Display order placeholders were shortened from
     full explanatory sentences to bare examples (`e.g. "Cash Cycle Demo
     Day—Co-Chair"`, `e.g. Jun 2026`, `Auto-assigned`) — the fuller
     guidance already lives in the help paragraph directly below the
     fields, so the placeholder no longer has to double as documentation.
     The visually similar Original Content admin form (`_oc_form_fields`,
     a different page — `/admin/thought-leadership/original/*`) has a
     `Mon YYYY, e.g. Jun 2026—optional`/`Leave blank—auto-assigned` pair
     with the same shape but sits inside its own responsive
     `repeat(auto-fit,minmax(190px,1fr))` grid, not a fixed `1fr 1fr` —
     out of this PR's named scope, flagged rather than silently swept in.
     `/admin/system/ai`'s enrichment-model `<select>` used to render each
     option as `{label}—{the long "enrich" blurb}` (e.g. "Opus 5—Deepest
     summaries. The one to standardize the archi…"), which truncated
     mid-word inside the select's own `max-width:520px` — a `<select>`
     can't be widened past its container the way a paragraph can. Fixed by
     switching option text to the short qualifier every other picker on
     the site already uses (`{label}—{the "short" blurb}`, e.g. "Opus
     5—Best quality"), and moving the fuller per-model "enrich" description
     to a new `<p id="model-select-desc">` beside the dropdown, updated
     live via JS (`saveModel()` reads the selected `<option>`'s own
     `data-desc` attribute) as a different model is picked. No new
     sentences were written for this one — both texts already existed in
     `linklib.models._REGISTRY`, just re-arranged onto two surfaces
     instead of one.
- **Layout fix batch 2, PR 29 (2026-09) — the `auto-fill`/`auto-fit`
  failure class swept further, sparse category-checkbox grids stop
  stretching, and the Original Content form's look-alike placeholder pair
  (flagged and deliberately left out of scope by PR 28/#545) gets the same
  treatment.**
  1. **`/admin/system/page-index`'s stat-tile row** — a real `auto-fill`
     instance: `repeat(auto-fill,minmax(160px,1fr))` reserved 7 tracks for
     only 2 real tiles (Pages, Flagged), leaving ~837px blank. One-value
     fix, `auto-fill` -> `auto-fit`: confirmed live, the two tiles now
     stretch to fill the row (resolved columns collapse the 5 phantom
     tracks to `0px`) at 1280px, 1920px, and 390px alike, with zero
     horizontal overflow at any width.
  2. **`/admin/thought-leadership/game-settings`'s rank-detail field grid —
     already `auto-fit`, not `auto-fill` as `docs/LAYOUT_AUDIT_TRIAGE.md`'s
     own characterization had it (confirmed by `git blame`: unchanged since
     2026-08-29, predating both #544 and #545), so switching to `auto-fit`
     would have been a no-op.** The real mechanism: `auto-fit` only
     collapses a track that has ZERO items across the WHOLE grid, not one
     that's merely unfilled on a single row. With 7 base fields + 4
     shark-only fields (Mate/First Mate/Skipper) sharing one grid, the
     browser fits 8 columns (driven by row 1's fuller count), so row 2's 3
     shark fields still reserve the other 5 tracks — none of the 8 tracks
     are ever globally empty, since row 1 uses all of them. Fixed per the
     audit's own named alternative: shark fields render in their OWN
     `auto-fit` grid, appended after the base-fields grid rather than
     concatenated into the same `fields` string — each grid now only ever
     has to fit its own item count (7, or 4), so `auto-fit`'s collapse
     actually applies in both. Deckhand (no shark fields) renders exactly
     one grid, unchanged. Verified live at 1280px (base grid: 7×159.1px
     columns, 8th collapsed to 0; shark grid, on the 3 ranks that have one:
     4×287.5px columns, remaining 4 collapsed to 0 — no more shared
     8-column row), 1920px (same shape, wider columns), and 390px (both
     grids wrap to 2×142px columns, `body.scrollWidth` exactly 390 at every
     rank) — the ~689px blank second row is gone.
  3. **Category checkboxes on the tool/community add and edit forms**
     (`_tool_category_checkboxes`/`_community_category_checkboxes`, both
     called from three admin-form wrapper `<div>`s using
     `repeat(auto-fit,minmax(150px,1fr))`) **switched to a plain
     `display:flex;flex-wrap:wrap` row instead of an `auto-fill`/`1fr`
     grid-strategy fix** — this is a tag list, not a grid: with only a
     handful of categories, `auto-fit`'s `1fr` tracks stretched each
     checkbox to fill its row, measuring ~400-410px gaps between 3
     checkboxes at 1280px in the pre-fix version. The flex row lets each
     checkbox size to its own label's natural width and cluster left, the
     same as any other tag list on the site. Verified at both a sparse
     count (3 categories: natural ~105px widths, clustered flush left, no
     stretch) and a realistic one (18 categories: two full flex-wrapped
     rows, still natural widths, no overflow at 1280px) — the fix holds at
     both scales, not just the seeded-test case. The `1fr 1fr` grid on the
     PUBLIC `/tools/submit` category picker (a different, unrelated
     mechanism — a fixed always-2-column layout, not this sparse-`auto-fit`
     bug shape) is untouched, and so is the `grid-column:1/-1` fallback
     paragraph in each function's "no categories yet" branch — still
     functional there (harmless in the new flex context, still needed for
     the one surviving grid-based call site).
  4. **The Original Content admin form's placeholder pair — real overflow,
     not just a look-alike risk.** `/admin/thought-leadership/original/*`'s
     Date label/Display order fields (`Mon YYYY, e.g. Jun 2026—optional`/
     `Leave blank—auto-assigned`) sit on a 4-column
     `repeat(auto-fit,minmax(190px,1fr))` grid inside the form's own
     `max-width:900px` wrapper — 4 columns resolve to ~214.5px each.
     Measured (not assumed) before changing anything: at that width the
     Date label placeholder needed ~238px of text and Display order's
     needed ~190px, both past the ~186.5px of actual room inside the
     input's padding — genuine overflow, confirmed with a real headless
     rendering, not merely "could overflow" per PR 28/#545's own deferral
     note. Shortened to the exact same strings #545 used for the
     third-party form's equivalents — `e.g. Jun 2026` / `Auto-assigned` —
     since the help paragraph below the field already carries the fuller
     guidance (unlike the third-party form, this page has no such
     paragraph directly under these two fields specifically, but the
     shortened placeholder itself no longer needs one to avoid overflowing).
     Re-measured post-fix: both placeholders now need under 96px, clear of
     the ~186.5px available room.
  **Auto-fill sweep, full accounting (per this batch's own instruction to
  report every instance, not just the two named ones)** — 7 real
  `grid-template-columns:repeat(auto-fill,...)` declarations exist in
  `webapp/app.py`; the rest of the `auto-fill` grep hits are prose in
  comments, not CSS. Software's (`/tools/software`) and Communities'
  (`/tools/communities`) own directory-listing grids, and the Resources
  page's Benchmarking/Book-recommendation card grids, are genuine listing
  grids with real, usually-plentiful content (matching the shape the audit
  itself confirmed is fine for `/admin/open-source`'s card grid) — left
  unchanged, though the Resources grids weren't independently checked
  against a realistic sparse count (a handful of books/benchmarks) the way
  page-index/game-settings were, so a future pass finding a sparse-row
  version of this same bug there wouldn't be surprising. The brand-showcase
  page's color-swatch grids (`/admin/brand`) are a large, always-fully-
  populated fixed palette — no sparse-row risk. `/admin/open-source`'s own
  card grid is the audit's own confirmed-fine baseline case (fixed card
  width, no stretch). None of these were touched in this batch.

- **Layout fix batch 3, PR 30 (2026-09) — mobile wrap-grouping (three
  findings) and centering a narrower column inside a wider, otherwise-empty
  container (two findings).** Continues the same layout-audit-triage arc as
  batches 1 and 2 above.
  1. **Profile action row's stranded divider, both entity types.**
     `/tools/software/{slug}` and `/tools/communities/{slug}`'s Visit →
     Compare → [divider] → Edit action row (`.tp-hero-actions{display:flex;
     flex-wrap:wrap;gap:10px}`) had the divider (`.tp-admin-divider`, no
     `flex-basis` of its own) as a separate flex item from Edit — at 390px,
     Visit + Compare alone left too little room for the divider AND Edit as
     two independent items, so Edit wrapped alone while the divider was
     left stranded at the end of the first line with nothing beside it.
     Fixed by pairing, not hiding: the divider and Edit are now wrapped in
     one `<span style="display:inline-flex;...">` so they're a single flex
     item that always wraps as a unit — the divider can never land alone.
     Same fix, same markup shape, on both pages (they share identical
     `.tp-hero-actions`/`.tp-admin-divider`/`.tp-admin-btn` CSS).
  2. **FP&A Buddy feedback page, two defects at 390px.**
     `/admin/fpa-buddy/feedback`: (a) the filter form's "Reviewed:" label
     and its `<select>` rendered on different lines — each label/select
     pair is now wrapped in its own `<span>` so a pair can never split
     across a wrap point, only the whole form can wrap between pairs.
     (b) The "View in ask report →" / "Mark reviewed" action pair
     (previously `<a style="margin-left:auto">` immediately followed by
     the reviewed-action form) rendered differently depending on the
     preceding rating badge's text length — stacked/misaligned after a
     long badge ("Inaccurate"), side by side after a short one ("Helpful").
     Fixed by wrapping the pair in its own `flex-basis:100%` span
     (`justify-content:flex-end`), which forces it onto its own row
     unconditionally — it now wraps the same way regardless of what
     precedes it, verified against both a long-badge and a short-badge
     card.
  3. **Uncarded failure-reason pill row.**
     `/admin/reader/backfill-content`'s failure-reason pills (e.g. "1 Bot
     challenge") rendered as bare markup with no card border/background/
     heading, sandwiched between the bordered, headed Purge and Recent
     attempts cards. Wrapped in the same `background:var(--surface);
     border:1px solid var(--line);border-radius:14px` treatment with a
     "Failure reasons" heading, matching its neighbors. The adjacent
     domain-clustering banner directly below it was checked and did NOT
     need the same fix — it already had its own card treatment (an amber
     alert box, matching this page's other banner conventions) — only its
     top margin was harmonized (14px → 20px) so the two sit in the same
     visual rhythm now that the pill row above it is a full card too.
  4. **`/play` pregame setup, left-anchored inside a much wider container.**
     `#sdrIntro`/`.sdr-pregame` are capped at `max-width:720px` but had no
     centering of their own, so at 1920px (root ~1252px wide) they sat
     flush left with ~532px of dead space to their right. Added
     `margin:0 auto` to both. Both are hidden outright (`display:none`) the
     instant play starts, so this has zero effect on the in-game canvas,
     which fills `#sdrStage` separately — confirmed live: the game still
     starts and plays correctly after the change.
  5. **`/read/{article_id}` when the article has no `<h2>`s.**
     `.reader-layout{justify-content:space-between}` between `.reader-main`
     (760px) and `.reader-toc` (220px) assumed both columns are always
     present — but the page's own JS sets `.reader-toc{display:none}`
     inline whenever an article has no headings to build a TOC from,
     leaving `.reader-main` as the sole flex item, which `space-between`
     just left-anchors (measured ~496px of dead space at both 1280px and
     1920px). The brief described the JS as already toggling a "TOC-hidden
     class" — checked against source and that wasn't quite right: it set
     `toc.style.display='none'` directly, no class involved. Added one: the
     JS now also adds `.no-toc` to `.reader-layout` in that branch, and a
     new `.reader-layout.no-toc{justify-content:center}` /
     `.reader-layout.no-toc .reader-main{margin:0 auto}` rule (mirroring
     the existing `@media(max-width:1100px)` narrow-viewport rule exactly)
     centers the reading column. Verified on both a headingless article
     (now centered) and one with real `<h2>`s (unchanged, TOC still renders
     at its usual right-hand position).
  Every fix confirmed with real `getBoundingClientRect()` measurements at
  390px/1280px/1920px (or 1280px/1920px where a finding was desktop-only),
  not a screenshot glance — same standing discipline as every prior batch
  in this arc. `brand_check.findings()`, `coral_moment_problems()`, and
  `hub_nav_orphans()` all confirmed clean; the full suite (3,000 tests)
  passes unmodified — none of these five findings had any prior test
  coverage to update.
- **Layout fix batch 4, PR 31 (2026-09) — `/admin/fpa-buddy/report` joins
  `.admin-table-responsive`, closing the layout audit's fix phase.** The
  table already had `overflow-x:auto` + a hardcoded `min-width:760px` (from
  an earlier pass, not this arc), so the audit's "no responsive treatment
  at all" framing didn't fully hold — checked directly rather than taken on
  faith, per this batch's own grep-sweep instruction. What was real: the
  page never overflowed horizontally (`body.scrollWidth` always equalled
  the viewport), but *within* that scrollable 760px table, Date/Asker/
  Settings/Cost ate most of the width before Question ever got a share —
  measured live at 390px: Question column 103.9px, Settings 204.2px
  (matching the audit's own "~95px"/"~198px" almost exactly), and row
  heights of 38.4px/81.3px/273.8px depending purely on how unevenly that
  squeeze forced Question to wrap. Fixed with the same
  `.admin-table-responsive` card-stacking treatment Software/Communities/
  Users already use, not the scroll pattern — this is a Q&A report where
  each row (question/asker/when/cost/settings) is a self-contained record,
  not a table where reading across many rows at once matters, so stacking
  labeled fields (`data-label` + the shared `::before` pseudo, mirroring
  Software's simpler variant — no sticky column, no column picker, since
  this page has neither a bulk-select nor an optional-column concept to
  begin with) lets Question render at the card's full width instead of
  fighting four other columns for a narrow slice. Verified at 390px
  post-fix: `container.scrollWidth` now equals its width exactly (no
  horizontal scroll needed at all), and row heights are all in the
  ~290-335px range — no longer swinging between 38px and 274px purely from
  wrap unevenness. Floor dropped from the bespoke 760 to
  `_TABLE_FLOOR_MEDIUM` (640, the documented 4-5-column bucket — Date,
  Asker, Question, Settings, Cost) — safe, since a table's real content
  still grows past a smaller declared min-width when it needs to; the
  floor only guards the narrow strip just below where cards take over.
  Confirmed at 1280px/1920px the table renders unchanged (Question column
  actually measured marginally wider — 466.8px/486.8px vs the pre-fix
  466.3px/486.3px, a rounding-level difference from float-formatted cost
  text, not a real layout change) — desktop was never the problem and
  isn't touched by the fix. **One thing this batch's own measurement pass
  surfaced and deliberately left alone, since it's a pre-existing browser
  auto-table-layout quirk, not something this fix introduced or made
  worse**: at intermediate widths between the 700px card breakpoint and
  roughly 1000px, the Question column stays pinned at ~104px regardless of
  how much extra room the viewport actually has, then jumps sharply wider
  above ~1000px — reproduced identically against the pre-fix code at the
  same widths, so it's an inherent characteristic of this un-fixed-layout
  table's width-distribution algorithm (Date/Asker's own suggested `width`
  attributes apparently claim available space ahead of Question in that
  range), not a regression. Out of scope for a card-stacking fix — a real
  fix would mean `table-layout:fixed` with percentage widths, a bigger
  change than this batch's remit. **This closes the layout audit's fix
  phase** (`docs/LAYOUT_AUDIT_TRIAGE.md`, PRs 28-31) — no further findings
  from that audit remain unaddressed. `brand_check.findings()`,
  `coral_moment_problems()`, `hub_nav_orphans()`, and
  `/admin/system/page-index` all confirmed clean; the full suite
  (3,000 tests) passes unmodified — no test pinned the table's old markup.
- **PR 32 (2026-09) — Software/Communities admin tables at half-desktop
  width (900-1000px): three reported symptoms, two genuinely different root
  causes, and one of them a functionally broken control, not a layout
  polish item.** Investigated before touching anything, per the standing
  approval-gate discipline for a behavior change on pages Brian uses daily
  — the brief's own hypothesis (one shared cause, "auto table layout
  letting column widths depend on content") did not hold.
  1. **No scroll affordance at ~900-1000px** — real and isolated. Both
     tables reuse Compare's `#cmp-scroll-wrap`/sticky-column mechanism but
     deliberately never reused Compare's own swipe hint
     (`_CMP_SWIPE_HINT_HTML`/`_JS`), which is `@media(max-width:700px)`-only
     — tuned for Compare, whose table always scrolls at mobile widths.
     Below 700px these two admin tables card-stack instead
     (`.admin-table-responsive`), so a hint gated on that breakpoint would
     show at exactly the wrong width; nobody had built the width-independent
     version. Fixed with `_ADMIN_SCROLL_HINT_HTML`/`_ADMIN_SCROLL_HINT_JS` —
     same icon/copy/localStorage-dismiss convention as Compare's hint, but
     gated on actual overflow (`wrap.scrollWidth > wrap.clientWidth`,
     re-checked on resize) rather than a media query, so it shows whenever
     there's really more to see, at any width, and never shows once the
     table genuinely fits.
  2. **Inconsistent Delete-button width — NOT a CSS/table-layout issue at
     all, and a real functional bug, more serious than "narrower button."**
     Both tables wrapped their entire approved-rows `<table>` in
     `<form id="software-approved-form">`/`<form id="communities-approved-form">`
     (added for bulk-select, apparently) — grepped `webapp/`, `tests/`,
     `linklib/`, `docs/` and found **zero references to either id anywhere
     else**; every bulk-select/bulk-edit/bulk-delete function reads checked
     boxes via a plain class selector
     (`document.querySelectorAll('.software-row-cb:checked')`), completely
     independent of any wrapping `<form>`. The wrapping form was pure dead
     markup, and it made every row's own Delete/Mark-reviewed `<form>` a
     NESTED `<form>` — invalid HTML. Per the HTML5 parsing algorithm, a
     browser drops the first nested `<form>` open tag it encounters
     entirely (no element created), and that same form's own closing
     `</form>` tag then pops the OUTER form off the parser's stack instead
     — resetting parser state so every later nested form in the row/table
     parses normally (if still, confusingly, DOM-nested inside the outer
     form). Net effect, confirmed live by seeding test data and inspecting
     the parsed DOM directly (not assumed from the CSS): **exactly one row
     per table — whichever has the first Delete/Mark-reviewed form in
     server-render order, independent of any visible sort or column value
     — lost its `<form>` wrapper outright.** Its Delete button rendered
     narrower (the existing `.admin-table-actions-grid form{width:100px}`
     rule no longer matched a bare `<button>`), had no `onsubmit` confirm
     dialog, and **had no submit target at all — clicking it did nothing.**
     This is the finding worth stating plainly: it was never a cosmetic
     width inconsistency, it was a silently broken Delete control. Fixed by
     deleting the two vestigial outer `<form>` tags outright — `#cmp-scroll-wrap`
     now wraps `<table>` directly, matching how `/admin/users` already does
     this with no wrapping form at all. No CSS change was needed for the
     button width itself: once the invalid nesting is gone, every row's own
     form parses correctly and the existing 100px rule applies uniformly,
     for real. `tests/test_admin_table_form_nesting.py` guards this with a
     stack-depth scan of the raw source HTML (a real HTML5-recovery-aware
     parser would silently "fix" the defect before the test ever saw it,
     making it useless as a regression guard — see that file's own
     docstring) — confirmed, by reverting the fix and rerunning, that the
     test suite actually fails against the pre-fix code, not just passes
     trivially against the post-fix one.
  3. **The "ion" text fragment — investigated, root cause NOT confirmed,
     left open rather than closed.** Tested the sticky-column-occlusion
     angle directly (rows with wrapped multi-line content, real
     `getBoundingClientRect()` measurements of sticky cells vs. row height
     at several scroll positions) and found sticky cells always covered
     the full row height correctly in every synthetic reproduction — no
     bleed. Could not reproduce a stray "ion" fragment with seeded test
     data despite several attempts at plausible triggers (long/wrapping
     Format values, a row needing review, varying row order). The fix
     above (removing the invalid nested forms) may resolve this as a side
     effect — nested-form DOM corruption is exactly the kind of thing that
     can produce secondary rendering artifacts a synthetic reproduction
     might not hit the same way real production content does — but this is
     a hypothesis, not a confirmed finding. **Flagged as unresolved,
     pending Brian rechecking the live page after this PR deploys**, not
     silently assumed fixed.
  Neither of the brief's `table-layout:fixed`-with-percentages nor
  fixed-width-button candidates was needed: buttons were already
  fixed-width via CSS, and the real defect was invalid HTML silently
  discarding one row's form, not the browser's column-width algorithm —
  the actual fix (two deleted `<form>` tags plus one new overflow-gated
  hint) is smaller than any of the three candidates the brief proposed.
- **Card width standardization (PR 33, 2026-09) — "cards keep their width;
  containers distribute them," the same rule-based-constant precedent as
  the admin table floors/column widths, applied to every card-listing
  family sitewide.** Investigated-then-proposed, same shape as those two —
  a full inventory of every card family (grep sweep for every `repeat(auto-
  fi*...)` grid, plus every family the brief named by hand) measured live
  via Playwright at 1280px/1920px and at both a sparse and a realistic item
  count, before any code changed. Two named constants
  (`_CARD_WIDTH_DIRECTORY_MIN`=320, `_CARD_WIDTH_RESOURCE_MIN`=260) replace
  duplicated literals that had already drifted to matching values by
  coincidence: Software's and Communities' directory cards (`.tool-card`/
  `.comm-card`) measured byte-identical (401.3px/408px) before this PR,
  confirming they could share one constant at zero cost; Resources'
  Benchmarking and Books cards already share one literal CSS class
  (`.bench-card` — there is no separate `.book-card`). Both families were
  already `auto-fill`, already correct, no stretch either way — the fix
  here is purely "stop two call sites from drifting apart by accident,"
  not a behavior change.
  **Two real bugs found by the same inventory, both `auto-fit` with a
  fixed low item count — the exact shape that stretches a sparse row's
  populated cards to fill the leftover space instead of leaving it
  blank**: (1) the Original Content flagship cards (`.tl-featured`/
  `.tl-card`, shared verbatim by `/` and `/thought-leadership`) — always
  exactly 3 cards; on `/thought-leadership`'s full-width column (where 5
  tracks fit at the 220px floor), a lone populated card measured **1252px**
  wide, the full container — worse than either of us had on the list going
  in. (2) `/admin/system/page-index`'s two stat tiles (Pages, Flagged) —
  `auto-fit` was chosen in PR 29 to kill phantom tracks on a *different*
  page (game-settings' sparse field grid) and got applied here too; two
  numbers stretched to 611px each read as "two numbers with enormous gaps,"
  not a metrics strip, per Brian's own call overriding the initial
  recommendation to leave it alone as a possibly-intentional KPI-strip
  exception. Both switched `auto-fit`→`auto-fill`; verified live before and
  after (flagship: 1252px→235px for the same single-card case, matching
  the 3-card populated width exactly; page-index: 611px→167px, left-aligned
  at its natural floor).
  **A live correction caught mid-implementation, flagged rather than
  silently applied**: the original proposal claimed Software/Communities
  directory cards had `-webkit-line-clamp` but no matching `min-height`
  floor (the PR #533 Resources treatment) and needed the same fix — Brian
  approved it as part of the batch. Building it, a live Playwright
  measurement (a 6-category/long-description card vs. a 1-category/short-
  description card, same row and different rows) showed both rendering at
  an identical 320.86px — already solved, just via a different, earlier
  mechanism: `.tool-name`/`.comm-name` (clamp-2 + `min-height:44px`),
  `.tool-desc`/`.comm-demo` (clamp-3 + `min-height:63px`), `.comm-meta`
  (clamp-2 + `min-height:39px`), and `.tool-cats`/`.comm-cats`
  (`min-height:24px` on the tag row) — a per-element floor on every
  variable-length piece rather than one whole-card floor, confirmed by a
  comment on `.comm-name` itself stating it was built to match the
  Software directory's own earlier fix. Skipped rather than added
  redundantly — no bug was left to fix, and a second, unneeded floor is
  exactly the complexity this whole pass exists to avoid.
  **Deliberately out of scope, with reasons documented in BRAND.md §5 so a
  future pass doesn't "standardize" them for consistency's sake and
  reintroduce the complexity this rule is meant to prevent**: Toolbox
  landing tiles (`/tools`, always exactly 4, fixed `1fr`/`1fr 1fr` grid),
  Admin hub group cards (`.admin-cols`, always exactly 6 top-level groups,
  plus a single-column nested-item list with no `grid-template-columns` at
  all — these are `<details>` accordion sections, not a multi-card row
  grid), and the Homepage sidebar panel (a single fixed-360px card) +
  Recent highlights (a fixed 2-column grid of at most 4 thought-leadership
  types, which collapses entirely rather than stretching when empty). None
  of these has a sparse-vs-full-row case for `auto-fill`/`auto-fit` to
  matter for — their item counts always exactly match their grid shape.
  See BRAND.md §5's new "Card widths" section for the full write-up.

- **"How this is built" (`GET /how-this-is-built`, 2026-09) — the evidence
  behind `/about`'s "I was AI-native before AI-native was a thing" claim, a
  public page naming where AI does real work on the site and where a human
  still signs off.** Reachable three ways (the phrase itself in `/about`'s
  copy via a new `_link_phrase` helper, a fourth `/about` button, a
  homepage link under the hero subhead) — deliberately never the top nav.
  Lists four surfaces (FP&A Buddy, Exa's four call sites, profile/
  description generation, matchmakers and compare summaries) but links only
  the one that already has an explainer page
  (`/tools/fpa-buddy/how-it-works`) — the other three show a title,
  description, and "Explainer coming soon." rather than a placeholder link,
  per the standing rule that a visitor who clicks and finds nothing learns
  less than one who clicks and finds something real. No diagram: the four
  surfaces are independent mechanisms a list already represents, not one
  branching/parallel flow a picture would show better than prose — the one
  diagram that does earn its place (FP&A Buddy's retrieval-tier flowchart)
  already lives on that linked page. `docs/AI_SURFACES_BRIEF.md` is the
  underlying research brief (mechanism, cost tracking, rejected decisions
  for all four surfaces) the eventual per-surface explainer pages will
  draft from — raw material for Brian to write from, not shipped copy.
  See ARCHITECTURE.md's matching section for the full write-up.

- **"How this is built" gets Brian's own copy, and Manage feeds comes up one
  level (PR 35, 2026-09).** Two unrelated pieces in one PR.
  **The page copy** is replaced wholesale with Brian's own writing, shipped
  verbatim — already run against his `write-like-brian` voice rules, and it
  passes `typography_findings` with no allowlist entry needed (confirmed, not
  assumed). Two new sections: **"Why I built this"** (the origin story —
  Feedly's renewal notice as the hinge, deliberately long) and **"What else
  I've built with AI"**, plus a footnote on Fred Wilson's 2024 AVC.com to
  avc.xyz move and a skip link under the intro for a reader who came for the
  mechanism rather than the story. The four surface cards stay, with edited
  copy (Web search now says *four* jobs, not three).
  **The prose is stored as five module-level markdown constants**
  (`_HTIB_INTRO`, `_HTIB_WHY_I_BUILT_THIS`, `_HTIB_HOW_I_DECIDED`,
  `_HTIB_WHAT_ELSE`, `_HTIB_FOOTNOTE`) rendered through
  `_render_original_content_markdown` — **the admin-authored-and-trusted
  renderer, deliberately not `webapp/markdown_render.py`'s restricted one**,
  which escapes links by design because it serves AI-drafted fields. That
  distinction is the whole reason the choice matters here: this copy carries
  nine inline links crediting other people's blogs, and credit is the point
  of that section. Named plainly rather than `*_DEFAULT` — in this codebase
  that suffix means "fallback behind a live `get_setting` lookup," and
  nothing overrides these yet, so the suffix would misdescribe the code.
  **All eleven external URLs were confirmed to resolve to the right target**
  (the agent proxy blocks direct CONNECT, so this went through the Exa fetch
  tool instead) — and `avc.com`'s own last post, "I've Moved Onchain" dated
  May 2024, independently confirms the footnote's factual claim.
  **Part 3, unrelated: Manage feeds took four expansions to reach from
  `/admin`** (CFO Toolbox to Reader to New content to the card) and Brian
  couldn't find it. It's now a sibling of the three quadrants rather than
  inside one — two expansions, confirmed live — and "New content" is renamed
  "Add content" (a verb says what you do there). A fourth quadrant was the
  alternative and was rejected: a collapsible box holding exactly one card
  adds the click straight back without grouping anything. The resulting
  "three disclosure boxes plus one plain card" shape reads as irregular
  described in the abstract but isn't in practice — **CFO Toolbox, the group
  this box sits inside, already mixes plain cards (Resources, Compare summary
  feedback) with nested disclosure sub-groups**, so the Reader box now
  mirrors its own parent's established pattern. `_LIBRARY_TOOLS` is
  untouched, so the Reader group's aggregate badge, its "6 tools" count, and
  `hub_nav_orphans()` all needed no edit — every one of them derives from
  that tuple, not from the quadrant arrangement.
  **Part 2, approved and shipped in the same PR: the copy is admin-editable
  at `/admin/copy`, with no new renderer and no change to the restricted
  one.** The investigation that gated this corrected a standing assumption
  worth keeping: `original_content.body_md` **is** an existing precedent for
  admin-editable prose carrying arbitrary links (a DB column, edited at
  `/admin/thought-leadership/original`, rendered with links intact), so the
  brief's premise that "the markdown renderer excludes links, so something
  has to change" didn't hold — nothing had to change. That made Part 2 a
  pure **storage** change: the five constants become `_HTIB_*_DEFAULT`
  behind a live `lib.get_setting(...)` lookup (`_htib_copy()`), five
  `settings` rows, five `/admin/copy` sections, and **one** save route
  (`POST /admin/copy/how-this-is-built`) that takes the section name in its
  payload and validates it against `_HTIB_COPY_KEYS` — rather than five
  near-identical routes. `_HTIB_COPY_SECTIONS` is the single registry
  behind the keys, the admin sections, and the save route, the same
  precedent `_email_template_registry()` set. No schema change: `settings`
  is already `key`/`value`.
  **Two alternatives were considered and rejected.** Extending
  `webapp/markdown_render.py`'s restricted renderer to allow links would
  weaken link-escaping across every AI-drafted tool/community field on the
  site to serve one admin-authored page — the largest blast radius of the
  three options, for something a second renderer already provides. A new
  trusted-admin-copy renderer would be a near-duplicate of
  `_render_original_content_markdown`, which already *is* that renderer —
  two renderers sharing one trust model is exactly the drift risk this
  codebase keeps documenting.
  **The real asymmetry this introduces, named rather than left to be
  discovered**: these five fields inherit `body_md`'s trust model, so raw
  HTML passes through unescaped. Correct here (admin-only, `_is_authed`-
  gated, and the copy is nine-tenths credit links) but genuinely different
  from the About-page bio sitting beside them on the same page, which is
  plain text through `_about_copy_html`. Each section's own description on
  `/admin/copy` says so.
  **The four surface cards stay in code, deliberately.** Each is a 3-tuple
  (title, description, href-or-empty) where the href points at a real route
  and the empty string is load-bearing — it's what selects "Explainer coming
  soon." over a link. A textarea introduces two failure modes prose doesn't
  have: a typo'd href silently 404s, and there's no sensible text form of
  the coming-soon state that can't be got wrong. The reasoning is a comment
  above `_AI_SURFACES` so it doesn't get re-litigated.

- **Standing rule: every outbound link opens in a new tab (PR 35, 2026-09).**
  Any anchor whose destination is not on bmweis.com carries
  `target="_blank" rel="noopener"`; internal links (relative paths,
  anchors, absolute bmweis.com URLs) stay same-tab. BRAND.md §3.3 is the
  rule; `linklib.brand_check.outbound_link_problems()` enforces it, with
  its own `/admin/checks` row and `tests/test_outbound_links.py`.
  **The sweep found one offender, not five** — the Logo.dev footer
  attribution (`webapp/app.py`, the shared `_page()` footer). An earlier
  same-line `grep` had reported five; four of those were multi-line anchors
  that carry `target` on a later source line (e.g. `doc_link` on
  `/admin/checks`), which is exactly why the checker reads raw source
  spans rather than per-line or per-string-literal values — an anchor is
  routinely split across adjacent Python string literals, and a
  value-based scan would flag the half without the attribute.
  **Source scan, not a rendered-page scan**, unlike `coral_moment_problems()`:
  the failure mode is a hand-typed anchor in `webapp/app.py`, and scanning
  source also covers admin pages (which a signed-out rendered scan can't
  reach) at no render cost and with no re-entrancy hazard — the recursion
  trap PR 16 had to build a guard for. It deliberately cannot see links
  built in JavaScript, or links in stored DB content (an admin's saved
  override of the copy above, `original_content.body_md`, AI-drafted
  fields, user-submitted text); the function's own docstring and BRAND.md
  §8 both say so rather than overclaiming. **Markdown can't express the
  rule at all** (no `target` syntax), so outbound links in prose that
  renders through a markdown renderer must be written as raw `<a>` tags —
  which is why `/how-this-is-built`'s credit links are raw HTML. See
  ARCHITECTURE.md's matching bullets for the full technical write-up and
  `tests/test_how_this_is_built.py` for the coverage.

- **Overhead spend fixes (2026-09) — column-width rebalancing on the
  details table, the missing scroll hint, an em-dash frequency fix, and a
  real cross-engine regression in the Date/Amount grid that a first pass
  wrongly reported as "did not reproduce."** `/admin/overhead-spend/details`'s
  "All vendor charges" table had `_COL_WIDTH_NAME` (280px, calibrated for
  a full software/community name) on its Vendor column, starving the
  genuinely free-text Note column of room — a real note ("Claude Max
  monthly subscription (personal account)") wrapped to 5 lines at a
  scrolled mobile width, confirmed by direct measurement (row height
  126px → 83px → 46px across 390/960/1280px, Chromium). Fixed with a new
  `_COL_WIDTH_VENDOR` (160px) constant plus `white-space:nowrap` on
  Category (which was wrapping "AI Subscription" even though there was
  room, since it was unwidthed and competing with Note) and Amount; Note
  stays unwidthed per the standard's own free-text rule. The table's
  `min-width` also moved from a hand-picked `720px` literal to
  `_TABLE_FLOOR_WIDE` (800px, the correct 6-column bucket — Vendor, Date,
  Category, Note, Amount, Actions). The Actions column also gained a
  visible "Actions" header — every other column already had one. The
  table joins Software/Communities in carrying `_ADMIN_SCROLL_HINT_HTML`/
  `_ADMIN_SCROLL_HINT_JS` (overflow-gated, not a breakpoint), confirmed
  live to show at 390px and stay hidden at 1920px — Chromium only (see
  below). The "Toolbox usage" block's three consecutive paragraphs each
  carried their own unspaced em dash — passing the mechanical typography
  lint (unspaced) but violating the frequency half of the em-dash policy
  (sparingly). Reworded two of the three to a period; the typography lint
  remains clean (0 findings) since it was never about spacing here.
  **A first pass at the "Add a charge" form's Date/Amount overlap wrongly
  reported it as not reproducing, because it was only ever tested in
  Chromium at a narrow viewport — testing a different engine than the one
  Brian's report came from.** Brian's screenshot is Chrome on an iPhone 16
  Pro; every iOS browser (Apple's own platform requirement) runs on
  WebKit, not Chromium, regardless of which browser's UI wraps it — so a
  desktop-Chromium test at a phone-width viewport is not a test of the
  engine that actually rendered the bug. **This bug shipped once already
  and regressed, in the sense that it was never actually fixed for every
  engine, just verified against one.** Git archaeology (complicated by
  this repo's history containing several disconnected root/orphan
  commits with no parent — `40b5bd2` among them — which makes `git log -S`
  and `git blame` unreliable across those boundaries, since a commit with
  no parent shows its whole file as "added" regardless of what was already
  there; verification instead came from diffing file CONTENT directly
  against current `HEAD`, not trusting pickaxe search alone) found the
  original fix (2026-08-28, content-identical in every ancestor of `HEAD`
  since, byte-for-byte, right up to this PR) switched the grid from a
  rigid `1fr 1fr` to `repeat(auto-fit,minmax(140px,1fr))` to stop the
  whole PAGE from overflowing at phone widths — but never added
  `min-width:0` to the grid's own two item `<div>`s, and its own code
  comment explicitly cited "~160px in Chromium" as the native
  `<input type="date">` minimum that drove the 140px floor — Chromium-only
  evidence for a genuinely cross-engine problem. A grid item defaults to
  `min-width:auto`, so its track can't shrink below the item's own content
  minimum even inside a `minmax()` track; WebKit's native date-input
  content minimum is larger than Chromium's, so the same 140px floor that
  satisfies Chromium can still be narrower than WebKit's minimum —
  producing exactly the overlap Brian saw on a real iPhone even though a
  Chromium sweep at seven widths came back clean. Fixed by adding
  `min-width:0` to both grid-item `<div>`s — the standard, engine-
  independent remedy for this exact CSS Grid blowout failure class (see
  the Phase P entry above). **The sweep re-run under this corrected
  understanding found a second, real instance of the same gap that the
  first pass's sweep had wrongly cleared**: the overhead-details table's
  own inline edit form (Vendor+Date, Amount+Category, two rigid `1fr 1fr`
  grids with the `<input>` elements themselves as the grid items, no
  wrapping `<div>`) has the identical native-date/native-number-inside-a-
  grid shape. Fixed the same way — `min-width:0` directly on each input
  (since there's no wrapping div here) — and switched both grids from
  `1fr 1fr` to `repeat(auto-fit,minmax(120px,1fr))` for the same page-
  level-overflow protection the Add-a-charge grid already has. Every other
  admin form checked in the re-run (Users' "Add a member" form, the
  Resources add/edit form, the Third-party content admin form) still has
  no native date/time/number input inside any grid, so none of them are in
  scope for this defect. **This sandbox cannot install or run Playwright's
  WebKit browser** — `playwright install webkit` fails with a `403` policy
  denial against `playwright.download.prss.microsoft.com` and
  `cdn.playwright.dev`, confirmed via the agent network proxy's own status
  endpoint as a genuine organizational policy block, not a transient
  failure — so neither fix could be visually confirmed in the engine that
  actually needs it; the fix is justified by documented CSS Grid spec
  behavior (the same reasoning already established for this failure class
  elsewhere in this doc) rather than a live WebKit screenshot. In place of
  that screenshot, `tests/test_overhead_spend_grid_regression.py` adds a
  generic, rendered-HTML-level regression guard: it scans every
  `display:grid` region on both affected pages and asserts `min-width:0`
  is present wherever the region contains a native date/time/number
  input, in whichever of the two fix shapes (wrapping div, or directly on
  the input) applies — proven to actually fail against the pre-fix markup
  shape before being trusted, per this repo's own "a guard that can never
  fail is worse than none" standard. **The scroll hint's copy was also
  shortened** ("Scroll to see more columns" → "Scroll for more") as a
  defensive width-safety measure per Brian's report that it may be too
  wide on his device — Chromium measurement at 320/375/390/402/430px found
  the hint's text content comfortably fits its container at every width
  tested (no wrap), but this is Chromium-only and this sandbox's font
  rendering cannot be fully trusted to match Brian's real device (see the
  standing Google Fonts sandbox-networking caveat elsewhere in this doc),
  so the shorter copy ships as a safety margin rather than a confirmed
  fix. **Standing lesson, restated because it bears repeating**: every
  mobile-width measurement in this PR is Chromium-only unless stated
  otherwise, and Chromium results are not evidence for WebKit — Brian
  uses Chrome on desktop (Chromium) and Chrome on an iPhone 16 Pro
  (WebKit, per Apple's platform requirement), and any future mobile
  verification in this codebase should say which engine actually produced
  it. See `BRAND.md`'s "Admin table column widths" section for the
  `_COL_WIDTH_VENDOR` write-up.
- **Overhead spend fixes, follow-up (2026-09) — the `min-width:0` fix above
  did not work either; Date/Amount are stacked now, not fixed a third time.**
  A screenshot from the deployed `min-width:0` fix still showed the overlap
  on Brian's real iPhone 16 Pro — the second CSS Grid "correct-looking"
  attempt in a row that didn't survive WebKit (the first was `auto-fit`/
  `minmax`). Both tried to keep Date and Amount side by side and make the
  shared row absorb whatever minimum width WebKit's native
  `<input type="date">` actually demands — a number this sandbox still can't
  measure (Playwright's WebKit browser remains uninstallable here, a
  confirmed 403 policy denial, not a transient failure). **Stopped trying to
  make the two-column grid survive the engine and stacked the fields
  instead**: every other field in both affected forms (Vendor, Category,
  Note) was already full width; Date and Amount were the only side-by-side
  pair and the only thing that has ever broken here. A shared `.oh-grid-2`
  class (`1fr 1fr` above 640px, `1fr` at or below it via
  `@media(max-width:640px)`) replaces the auto-fit/minmax/min-width:0
  machinery on both the Add-a-charge form and the overhead-details inline
  edit form's Vendor+Date/Amount+Category pairs. **A single-column layout has
  no shared row for two fields' content-minimums to collide in — there is no
  CSS Grid failure mode left to get wrong, in any engine.** 640px is not a
  measured WebKit number; it's picked to be comfortably above anything a
  native date input is likely to demand, and matches this codebase's own
  `.page-form` width tier.
  **Also found the real root cause of the scroll hint's cramped spacing,
  which turned out to be a genuinely missing stylesheet, not just a tight
  gap value**: `.admin-scroll-hint` was defined inside `_CMP_SHARED_CSS`,
  which is injected on exactly two pages (the Software/Communities Compare
  pages) — but `_ADMIN_SCROLL_HINT_HTML`/`_JS` are reused on every wide admin
  table (Software, Communities, Overhead spend details), so the hint
  rendered with **zero** styling on all three: no gap, no color, no
  font-size, no margin, only the inline `style="display:none"`/JS-toggled
  `flex` controlling visibility. That's the actual mechanism behind "the
  icon sits flush against the text and the whole thing hugs the table's
  top-left corner" — there was no rule reaching the page at all. Moved
  `.admin-scroll-hint`/`.admin-scroll-hint svg` into the sitewide `_CSS`
  (injected on every page via `_page()`), so any future page reusing this
  component gets the styling for free instead of silently repeating the same
  gap, and widened `gap` (6px→10px) and the bottom margin (8px→14px) now
  that it actually applies.
  `tests/test_overhead_spend_grid_regression.py` was rewritten end to end —
  the old `min-width:0`-presence checker is replaced with a stacking checker
  that confirms each `.oh-grid-2` collapses to a genuine single track at or
  below 640px, proven to fail against BOTH of the two previous, now-
  abandoned fix shapes (`auto-fit`/`minmax` alone, and `min-width:0`) before
  being trusted — the same "a guard that can never fail is worse than none"
  discipline this repo already applies elsewhere. Still no live WebKit
  confirmation possible from this sandbox; the fix is justified by the
  simple, mechanical fact that two elements on separate grid rows cannot
  overlap horizontally regardless of either one's own content-minimum,
  which needs no browser to verify — only that the CSS actually collapses
  to one column at the right breakpoint, which the test does confirm.
- **Overhead spend fixes, round 3 (2026-09) — stacking above was necessary
  but not sufficient; the real bug was the `<input type="date">` element
  itself, not its grid or its row.** A real-device screenshot of the
  deployed stacking fix showed the Date input's own right edge extending
  past the card border — past every other field in the form (Vendor,
  Amount, Category, Note all sat correctly inside the card). The two-column
  overlap Rounds 2 and 3 chased was always the *symptom*: the input bled
  into Amount's track because the input itself refuses to shrink, not
  because the grid failed to give it room. Stacking removed the collision
  but left the oversized input exposed on its own row. Root cause (verified
  against the CSS spec, not just asserted): WebKit's native
  `<input type="date">` has an intrinsic content width driven by its
  internal day/month/year picker-segment UI, and a plain `width:100%`
  doesn't override that — `width` computes against the containing block,
  but nothing forces the *result* to be no wider than that if the control's
  intrinsic minimum is larger; `max-width`, unlike `width`, is a hard clamp
  that always wins regardless of intrinsic content (CSS2.1 §10.3.3). Fixed
  with `max-width:100%` plus `min-width:0` directly on the `<input>` itself
  (not just its wrapping grid-item `<div>`, which is what the Round 2
  `min-width:0` fix targeted and is exactly why it never reached this) —
  on all three real `type="date"` inputs sitewide, found via a full sweep:
  the Add-a-charge form's Date field, the overhead-details inline edit
  form's Date field (which also gained the same treatment on its Amount+
  Category pair and its Vendor field, for consistency within the same
  `.oh-grid-2` rows, and a `width:100%`/`box-sizing:border-box` pair it had
  never had at all), and the Feature Taxonomy admin table's `verified_as_of`
  date input (a fixed `width:130px` with no defensive properties — sits
  inside an already-horizontally-scrolling table, so any overflow there is
  absorbed by the table's own scroll rather than breaking out of a card the
  way the overhead-spend forms did, but the same containment fix was
  applied regardless, since a fixed pixel width can still lose to a larger
  intrinsic minimum in the same way `width:100%` did). **Deliberately not
  applied: `-webkit-appearance:none`/`appearance:none`**, which would strip
  the native picker chrome that's setting the intrinsic width in the first
  place — investigated per the explicit instruction to check usability
  first. Removing native styling from `type="date"`/`type="time"` inputs is
  documented across the frontend ecosystem as inconsistently supported and
  can produce a broken or invisible control in some WebKit versions
  (unlike `type="text"`/`type="number"`, where stripping native chrome is
  well-understood and safe) — a real usability risk this sandbox has no way
  to verify live (no WebKit access). Since the standard `max-width`/
  `min-width:0` remedy already resolves the containment problem without
  touching the native picker at all, there's no reason to take on that risk.
  **Stacking is kept** — even with the input's own box now constrained, one
  field per row is still the right mobile layout, and it removes the
  original two-column collision permanently regardless of any single
  field's own sizing quirks in any engine. `tests/
  test_overhead_spend_grid_regression.py` gained a new containment checker
  (`_input_cannot_exceed_container`, requiring `max-width:100%` +
  `min-width:0` + `box-sizing:border-box` together on a date input's own
  style) plus a sitewide sweep test that scans `webapp/app.py`'s source
  directly for every `type="date"` input (avoiding the need for a
  category-features admin fixture to reach the Feature Taxonomy table via
  a full page render) — both proven to fail against the pre-fix code before
  being trusted, same discipline as every prior round's own guard. Still no
  live WebKit confirmation possible from this sandbox (`playwright install
  webkit` remains a confirmed 403 policy denial); the fix is justified by
  CSS spec behavior (`max-width` always wins over intrinsic content,
  unconditionally, in every standards-compliant engine) rather than a live
  screenshot — the same category of justification Round 2's own stacking
  fix used, since neither round could get a real WebKit render.
- **Overhead spend fixes, round 4 (2026-09, PR 555 merged) — round 3's
  `max-width:100%`/`min-width:0`-on-the-input fix did not work either,
  confirmed live: a fresh post-deploy iPhone screenshot matched the
  pre-fix state exactly.** Per explicit instruction ("stop writing CSS and
  find out what's actually applied to that element"), the served
  `/admin/overhead-spend` HTML and every matching CSS rule were dumped and
  inspected directly rather than reasoned about from source — the rule was
  present, matching, and applying (confirmed: no inline style beat it, no
  fixed pixel width was present, `box-sizing:border-box` was on the input
  itself). That ruled out "the fix didn't land" and pointed one layer up,
  per a second hypothesis floated before touching `appearance:none`: at
  ≤640px `.oh-grid-2` collapses to a single `1fr` track, and a bare `1fr`
  track's implicit minimum is `auto` (min-content) — if the wrapper `<div>`
  holding the Date `<input>` (added to hold a `<label>` above the field)
  has a larger min-content than the card, the TRACK itself expands to fit
  it, and `max-width:100%` on the input then correctly clamps the input to
  100% of an already-oversized track. Per the explicit "verify by
  measurement, not reasoning" instruction: reproduced with a controlled,
  engine-independent isolated test (a `white-space:nowrap` element with a
  guaranteed-large intrinsic minimum standing in for WebKit's real
  date-input width, since this sandbox cannot measure that width directly)
  — a wrapper div with `min-width:auto` (unset) blew a 276px card's track
  out to 954px even with `min-width:0`/`max-width:100%` on the NESTED
  input one layer inside it, conclusively proving `min-width:0` on a
  descendant does not override its ANCESTOR grid item's own automatic
  minimum size. The overhead-details inline edit form was checked the same
  way and found structurally safe from this exact mechanism, because its
  `<input>`s are direct children of `.oh-grid-2` with no wrapper div — the
  input itself is the grid item there, so its own `min-width:0` (already
  present) correctly caps the track. **Fix, confirmed and applied to both
  `.oh-grid-2` definitions (Add-a-charge and overhead-details), at both
  the 2-column base rule and the 1-column stacked-breakpoint override**:
  `grid-template-columns:minmax(0,1fr)` instead of a bare `1fr` — this
  sets the TRACK's own minimum to `0` directly, so it can never expand
  past the available space regardless of what any current or future child
  declares, chosen over `min-width:0` on `.oh-grid-2`'s direct children
  specifically because a per-child fix is one careless future field
  (wrapped in yet another div, with nobody remembering this history) away
  from re-breaking — a track-level fix protects every child, forever, with
  one declaration. Round 4's input-level containment
  (`max-width:100%`/`min-width:0`/`box-sizing:border-box` on the `<input>`
  itself) is kept as a second, defense-in-depth layer, but it is not, on
  its own, sufficient — the track-level fix is what actually stops the
  bug. `tests/test_overhead_spend_grid_regression.py`'s regression guard
  was rewritten to match: the previous checker asserted input-level
  containment only, which — per this round's own finding — checks the
  wrong layer; a new `_grid_track_cannot_exceed_container` asserts every
  track in `grid-template-columns` declares an explicit `minmax(0,...)`
  zero minimum, at both the base rule and its stacking override, and is
  proven to correctly FAIL against the exact bare-`1fr` shape that shipped
  in this same round (and passed stacking) before being trusted. Fixing
  `_grid_track_count` itself was a prerequisite: its prior "any `minmax(`
  usage counts as 2 tracks, defensively" shortcut would have misjudged a
  1-track `minmax(0,1fr)` override as still 2 tracks, silently breaking the
  existing stacking check the moment the real fix shipped — replaced with
  a proper paren-depth-aware track splitter (`_split_top_level`) that
  counts `minmax(0,1fr) minmax(0,1fr)` as genuinely two tracks and a lone
  `minmax(0,1fr)` as genuinely one. **Sweep for the same shape, per
  explicit instruction to report rather than fix beyond this page without
  saying so**: the vulnerable shape (a bare `1fr`/`repeat(N,1fr)` track
  whose grid item is a wrapper `<div>` around exactly one native form
  control, rather than the control itself) also exists at `.ger-grid-2`/
  `.ger-grid-4` (Growth Engine Ratio calculator, `type="number"` inputs —
  lower risk than a date input's picker UI, but the same mechanism),
  `.qe-row` (Software directory Quick Edit panel, Vendor contact name/
  email text inputs — always 2-column, no stacking override at any width),
  the Resources admin add/edit form's Coverage/Pricing `<select>` pair
  (a `<select>`'s intrinsic minimum is its longest option's text, a
  real risk if an option label is long), the Third-party content admin
  form's Source/venue paired row (text inputs), and the Users "Add a
  member" form's own `1fr 1fr` grid (Username/Temp password/Name/Email/
  Role, including a `<select>`). None of these has shown a visible symptom
  yet — the bug is latent until a track's content is wide enough to blow
  it out, which is exactly why nothing else has been reported broken.
  `.ger-grid-3` is structurally different (inputs are direct grid children,
  no wrapper div — the same safe shape overhead-details already has) but
  still has no `min-width:0`/`max-width:100%` guard on the input itself,
  a related but lesser risk. Confirmed SAFE, different shape entirely:
  `.backup-actions` (two-column panels of text/buttons/a file-upload
  `<form>` in its own flex layout, never a single-native-control grid
  cell), `/tools/submit`'s and the tool-add-form's category-checkbox
  grids (checkboxes wrap freely with their label text, no fixed-width
  native control to blow anything out), and `.toolbox-layout`/
  `.toolbox-grid`/`.admin-cols`/`.home-grid`/`.fpa-intro-layout`/
  `.ask-controls` (page/section-level layout grids or chip/button rows,
  not single-form-control cells). **Not fixed in this PR** — reported per
  instruction, pending a decision on scope before touching anything beyond
  `.oh-grid-2`.
- **Overhead spend fixes, round 5 (2026-09, PR 557 merged) — the round-4
  track fix was real and correct, but it was not the cause of the overlap
  either; a follow-up real-device screenshot proved it via a control the
  earlier rounds hadn't looked at.** Brian's own diagnostic, confirmed
  correct: Amount sits in the exact same collapsed single-column track as
  Date (both children of the same `.oh-grid-2` at ≤640px), and Amount
  rendered at the CORRECT width, lined up with Vendor/Category/Note. If the
  track itself were oversized, Amount would be oversized too — it wasn't,
  which rules the track out categorically, no further measurement needed.
  Only the `<input>` itself was exceeding its own box by ~110px despite
  `max-width:100%` being present and correctly applied — which points at
  exactly the Round 3 hypothesis (WebKit does not honor `max-width` against
  a native date input's own intrinsic picker-chrome width) that was set
  aside at the time in favor of stacking, and correctly so at the time —
  stacking WAS a real, necessary fix for the two-column collision it
  targeted, it just wasn't sufficient for this deeper one underneath it.
  **Fix: `-webkit-appearance:none;appearance:none`**, added alongside the
  existing `width:100%;max-width:100%;min-width:0;box-sizing:border-box`
  on all three real `type="date"` inputs sitewide (Add-a-charge, the
  overhead-details inline edit form, and the Feature Taxonomy table's
  `verified_as_of` field). This strips the native picker-segment chrome —
  and the intrinsic width that chrome demands — outright, rather than
  trying to constrain a box the browser was never going to constrain no
  matter what sizing property was thrown at it. **On the usability
  question this session flagged in round 3 and was talked out of
  pursuing**: on iOS, tapping a date input opens the native picker
  regardless of `appearance:none` — that's platform tap behavior tied to
  the input's `type`, not something CSS `appearance` controls — so the
  realistic downside is the field's own chrome looking plainer, not losing
  the picker. **Every fix from rounds 3 through 5 is kept, none reverted**:
  stacking, track-level `minmax(0,1fr)` containment, and input-level
  `max-width`/`min-width`/`box-sizing` are all real, correct fixes for
  real defects they each targeted — they simply weren't the one causing
  this particular symptom. `appearance:none` is the layer that actually
  stops it. `tests/test_overhead_spend_grid_regression.py` gained a new
  `_input_has_no_native_chrome` checker (requiring both
  `-webkit-appearance:none` and the standard `appearance:none` — WebKit
  still needs the prefixed form in some versions, and neither alone is
  proven sufficient) alongside the existing `_input_cannot_exceed_container`
  checker (renamed in spirit, not in code, to "necessary but proven
  insufficient" — its docstring says so explicitly now), proven to
  correctly FAIL against the exact round-3/4 shape that shipped and still
  overflowed on a real device before being trusted. **Five rounds, five
  real fixes, only the last one was the actual cause of the reported
  symptom** — worth remembering as the reference case for why "the CSS
  looks correct" is never sufficient evidence on its own for a native
  form-control sizing bug in an engine this sandbox cannot run
  (`playwright install webkit` remains a confirmed 403 policy denial, not
  transient) — only a real device, and a control (a sibling field that
  didn't fail), can rule a hypothesis in or out with confidence. If a
  `type="date"` input somehow still overflows after this, the documented
  fallback is a real `overflow:hidden` wrapper around the input —
  containment rather than sizing, which cannot fail regardless of what the
  control wants — not yet needed, not yet built.
- **Overhead spend fixes, round 6 (2026-09, PR 559 merged) — `appearance:none`
  fixed the width, but stripped WebKit's own vertical padding around the
  picker segments along with the chrome, leaving the date input roughly
  half the height of its siblings.** Brian's own diagnostic again pointed
  at the exact right layer before any code was written: measure a sibling
  text input's rendered height first and target that number, rather than
  guessing at a fix. Confirmed in this sandbox's Chromium that the collapse
  itself doesn't reproduce here — every before-fix measurement on all
  three real `type="date"` inputs showed Date already at or above its
  sibling's rendered height (e.g. 45.09px vs. Vendor/Amount's 43.09px on
  the Add-a-charge form, 37.4375px matching Vendor exactly on the
  overhead-details form, 29px vs. Note's 27px on the Feature Taxonomy
  table) — consistent with the standing pattern in this investigation:
  Chromium has never reproduced a single one of the real symptoms Brian's
  iPhone 16 Pro (WebKit) has shown across all six rounds. **Fix: an
  explicit `min-height` on all three inputs, sized to that context's own
  sibling text input's measured Chromium height** — 43px (Add-a-charge,
  matching Vendor/Amount), 37px (overhead-details, matching Vendor in the
  Vendor+Date row — Amount/Category's own row measures 2px shorter, no
  `font-weight:500`, so Date's floor targets its real row-mate, not the
  other row), 27px (Feature Taxonomy, matching Note in the same row). This
  is a floor, not a resize: verified live before/after that it changes
  nothing in this sandbox (Date was already taller everywhere), and it can
  only ever raise a collapsed box up to the sibling height, never push a
  correctly-sized one down — the mechanism this investigation needed from
  the start of round 6, since `min-height` composes safely with whatever
  `appearance:none` does to an engine's internal height calculation without
  having to know what that calculation actually produces. `tests/
  test_overhead_spend_grid_regression.py` gained `_declared_min_height`
  (extracts the numeric px value, proven to fail against the exact round-5
  shape with `appearance:none` present but no `min-height` yet) — wired
  into both page-render tests (asserting the exact 43px/37px value on that
  page's own Date input) and the sitewide source-sweep test (asserting all
  three sitewide values, sorted, since the regex-based sweep can't
  distinguish the three inputs by name alone). It also gained a genuinely
  new kind of check for this file — two tests that launch a real Chromium
  browser (`_launch_chromium`, `pytest.skip`ping cleanly wherever a browser
  isn't resolvable, per this repo's own testing-convention precedent for
  Playwright-adjacent coverage that can't assume a browser exists in every
  environment) and compare the Date input's real rendered height against
  its sibling's, within a small pixel tolerance — real coverage in any
  environment with a working Chromium, a no-op (never a CI failure)
  everywhere else, since `.github/workflows/qa.yml` installs only
  `requirements-dev.txt`, no browser binary. **Still no live WebKit
  confirmation possible from this sandbox** (`playwright install webkit`
  remains a confirmed 403 policy denial, not transient) — the fix is
  justified by the mechanical fact that `min-height` is a hard floor in
  every standards-compliant engine, the same category of justification
  every prior round in this investigation has had to fall back on for
  exactly this reason. Six rounds, six real, kept fixes — stacking,
  track-level containment, input-level `max-width`/`min-width`, native-
  chrome stripping, and now a height floor — each fixing a real defect
  along the way, only the accumulation of all six actually closing the bug
  end to end on the one engine that ever showed it.

- **`/admin/copy` split into three pages, one per public page it edits
  (2026-09).** The single "Site copy" page had grown to eight sections
  (Homepage headline/subhead, Homepage bio box, About bio, and the five
  How this is built prose fields) with no way to tell, from the one hub
  card, which public page a given field actually changed. Split into
  `/admin/copy/homepage` (hero headline/subhead + bio-box lead/body),
  `/admin/copy/about` (the About bio), and `/admin/copy/how-this-is-built`
  (the five HTIB sections) — three cards under Brand, voice, and content
  now, replacing the one "Site copy" card. **No settings-key change, no
  save-route change, no rendering change** — `_htib_copy()`,
  `_about_copy_html()`, and the homepage/about lookups are all untouched;
  this is a pure admin-UI reorganization. **`/admin/copy/how-this-is-built`
  was already a POST save route before this split** (added when the HTIB
  copy became admin-editable) — GET and POST on the same path are two
  separate FastAPI routes, same precedent as `/admin/tools/resources/new`
  or `/tools/software/{slug}/edit`, so adding a GET page here isn't a
  rename or a collision: the new page's Save buttons keep posting to the
  exact same path they always did. **`/admin/copy` itself is not an index
  page — it 404s, with no compatibility redirect**, matching the standing
  pattern every other admin group prefix on this site already follows
  (`/admin/thought-leadership`, `/admin/system`, `/admin/tools`,
  `/admin/inbox` are all bare prefixes with no route of their own; the
  hub-nav cards are the real entry point, not the prefix) — building a
  landing page here would be the one inconsistent exception. Each new
  page moved from the old page's `.page-standard` (1300px, sized for eight
  sections at once) to `.page-form` (640px) — the same tier every other
  single-purpose admin edit form on this site uses (`/contact`,
  `/admin/tools/resources/{id}/edit`), and now a better fit once each page
  holds one or two sections instead of eight. Every new page's card
  description states plainly whether its fields are plain text (Homepage,
  About) or accept raw HTML for links (How this is built) — the one real
  capability asymmetry `_HTIB_COPY_SECTIONS`' own comment already
  documented, now visible from the hub without opening the page.

- **Explainers collection, Phase 1 (2026-09) — the four `/how-this-is-built`
  surface cards move from the hardcoded `_AI_SURFACES` tuple into a managed
  DB collection, `ai_surfaces`.** Same "add a piece, set a slug, write the
  body, flip it live" shape `original_content` already has: `Library.
  list_ai_surfaces`/`get_ai_surface`/`get_ai_surface_by_slug`/
  `add_ai_surface`/`update_ai_surface`/`delete_ai_surface`, admin CRUD at
  `/admin/ai-surfaces` (modeled directly on `/admin/thought-leadership/
  original`'s own form/list shape), and a public `GET /how-this-is-built/
  {slug}` article route with the identical live/draft/404 contract
  `GET /thought-leadership/{slug}` already has. **The FP&A Buddy case named
  in the build brief — an explainer whose own page lives elsewhere** — is
  handled with a new `external_href` column: when set on a `status='live'`
  row, the card links straight there instead of to this table's own
  `/how-this-is-built/<slug>` page, and `body_md` is left `NULL` since
  there's nothing here to render; FP&A Buddy is seeded exactly this way
  (`external_href="/tools/fpa-buddy/how-it-works"`). A Draft row's card is
  always unlinked ("Explainer coming soon."), regardless of what `body_md`/
  `external_href` hold — flipping to Live is the one action that changes
  what a visitor sees. `scripts/migrate_ai_surfaces.py` (not yet run
  against production, not yet archived — same human-review-before-a-
  production-write precedent as `scripts/archive/migrate_original_
  content.py`) seeds the four existing cards from `_AI_SURFACES`, which
  stays in the repo, unimported by any live route, purely as the
  migration's seed data and a rollback reference (same precedent as
  `_TL_FEATURED_CARDS`). Same PR fixed a real comment-staleness bug on
  `/admin/thought-leadership/original`'s own footer, which named "the 3
  flagship pieces" by title as having no body — a static claim that would
  go wrong the moment a fourth body-less piece existed; it's now a live
  count derived from the current table (`no_body_count`), not a hardcoded
  sentence.
- **Explainers collection, Phase 2 (2026-09) — About's body joins the
  trusted markdown renderer, the five How this is built prose fields
  consolidate to two, and both pages gain a Preview action.** `/about`'s
  bio previously rendered through a retired `_about_copy_html`/
  `_link_phrase` pair (plain text, with one special case: wrap the phrase
  "AI-native before AI-native was a thing" in a link to
  `/how-this-is-built`). Both are gone — the About route now calls
  `_render_original_content_markdown(about_copy)` directly, same as How
  this is built, and the link is a plain raw `<a>` baked straight into
  `_ABOUT_COPY_DEFAULT` — verified byte-identical to the old rendered
  output for the default copy before shipping. `_link_phrase` had exactly
  one caller, confirmed by grep before retiring it. **The five
  `_HTIB_*_DEFAULT` prose constants stay** (Brian's actual copy,
  unduplicated) but the admin-editable settings surface drops from five
  keys to two — `htib_before_copy` (intro + "Why I built this") and
  `htib_after_copy` ("How I decided..." + "What else I've built..." + the
  footnote) — split exactly where the four surface cards interrupt the
  page. **A genuinely single field per side isn't possible without moving
  the cards or introducing a placeholder token, and this uses one anyway,
  disclosed as exactly that rather than presented as a clean design**: the
  intro's own font-size/line-height/color and the footnote's own
  border-top/small-print treatment are each a template-level style around
  one sub-section of a combined field, not the whole thing, so both
  combined defaults are built by joining the original five constants with
  a lightweight `_HTIB_SPLIT_MARKER = "\n\n<!--split-->\n\n"`, and
  `_htib_split(raw, n)` splits a saved field back into exactly `n` parts
  (padding with `""` rather than raising if an edit removes a marker) —
  reproducing the pre-consolidation five-field rendering byte-for-byte
  while genuinely halving the settings-key/textarea count. Each field's
  admin description explains the marker and warns not to remove it. **Both
  `/admin/copy/about` and `/admin/copy/how-this-is-built` gained a shared
  Preview action** — `POST /admin/copy/preview` renders arbitrary posted
  text through `_render_original_content_markdown` with no save, and one
  shared JS toggle (`_ADMIN_COPY_PREVIEW_JS`) injects the result into a
  bordered box under the textarea — built specifically because raw HTML
  without a preview is exactly how an unclosed `<div>` reaches production
  silently, and both pages are now raw-HTML-tolerant. See
  ARCHITECTURE.md's "How this is built" section for the full write-up.

- **Explainers collection follow-up (2026-09) — the surface-cards grid fix,
  the three `/admin/copy/*` pages' width tier, redundant helper text, and a
  new Content sub-group on `/admin`.** Five small, independently-scoped
  fixes shipped together.
  1. **The four `/how-this-is-built` surface cards rendered outside
     `.tool-prose`** — the same shape PR #515 fixed for the back-arrow. The
     page had split into three separate `.tool-prose` divs with the cards
     grid as an unwrapped sibling between them, so it rendered at the full
     `.page-standard` width, left-shifted from the 760px reading column
     above and below it. Fixed by merging the page back into ONE
     `.tool-prose` wrapper spanning the whole page, with the cards grid as
     a plain nested child — its width now comes from being a block-level
     descendant of the 760px ancestor, the same mechanism every other
     element on the page already relies on. `grid-template-columns:1fr` is
     now explicit (not left to `display:grid`'s bare implicit-single-column
     stretch) and each card's own `<div>` carries
     `width:100%;box-sizing:border-box` directly — two independent,
     redundant mechanisms pinning every card to the identical width, since
     this is a single-column stack (never more than one card per row), it
     doesn't need one of BRAND.md §5's `_CARD_WIDTH_*_MIN` constants
     (`auto-fill` vs. `auto-fit` is moot with exactly one column) — matching
     `.tool-prose` is both necessary and sufficient. Verified live via
     Playwright at 1280px and 390px: cards grid now measures x/width
     identical to `.tool-prose` at both widths, and every card measures
     identical to its siblings.
  2. **The three `/admin/copy/*` pages (Homepage, About, How this is
     built) were on `.page-form` (640px)**, which centers the whole page
     and narrows every field — inconsistent with every other admin edit
     page built since (`_ai_surface_form_page`, `_oc_form_page`), both of
     which use `.page-standard` (1300px) with the back-link/`<h1>` at the
     page's own left edge and only the fields themselves capped, at 900px,
     centered via their own `max-width:900px;margin:0 auto;` wrapper.
     Aligned all three copy pages to that same shape — confirmed live:
     back-link/h1 render at x=24 (matching `/admin/ai-surfaces/new`'s own
     x=24 exactly) at both 1280px and 390px, and each page's field wrapper
     measures x=190/width=900 at 1280px (x=24/width=342 at 390px, same as
     the form on the reference pages at that width).
  3. **`/admin/copy/how-this-is-built` explained the raw-HTML/
     `target="_blank"` rule three times** before a reader reached a
     textarea — the page intro, and again inside each of the two section
     descriptions. Cut to one: the page intro is now the sole place that
     states it (matching About's own single-intro-sentence pattern); each
     section's own description is now split-marker guidance only, nothing
     else — which, since there's nothing left to fold it into, already
     reads as its own clear line rather than a clause inside an HTML-rules
     paragraph.
  4. **A new "Content" sub-group nests inside "Brand, voice, and content"**
     — Homepage, About, How this is built, and AI surfaces, the same
     `_group_html(..., nested=True)` mechanism CFO Toolbox already uses
     for its own Software/Community/FP&A Buddy/Reader sub-groups. A new
     `_CONTENT_TOOLS` constant (mirroring `_SOFTWARE_TOOLS`/
     `_COMMUNITIES_TOOLS`) holds the four content-page tuples, spliced into
     the group at render time in `admin_page()`; `_ADMIN_GROUPS`' own
     static "Brand, voice, and content" entry now lists only the three
     voice/design standards cards that stay direct children (Verbal
     identity, Email templates, Brand standards) — those are standards,
     not content pages, so they don't nest. **`_hub_nav_all_hrefs()` was
     updated in the same PR**, per the standing warning its own docstring
     already carries: every constant nested as its own sub-group at
     render time has to be added there by hand, since the generic
     per-`_ADMIN_GROUPS` sweep can no longer see items that moved out of
     that static tuple — forgetting this step is exactly what made the
     LiveFlow/Runway incident (see the "Admin menu default-state" bullet
     above) possible in the first place. `hub_nav_orphans()` confirmed
     clean both before and after, via `tests/test_admin_content_subgroup.py`.
  5. **The "Preview isn't clickable" report was investigated and could not
     be reproduced.** Live-tested via a real headless-browser session
     (not just `TestClient`-rendered HTML, per the standing testing
     standard) on all three pages that carry a Preview button (About, and
     both How this is built sections): the button carries no `disabled`
     attribute, `getComputedStyle` reports `opacity:1`/
     `pointer-events:auto`/`cursor:pointer` at both 1280px and 390px, and
     clicking it — on first click, against the untouched default textarea
     content, with no prior `change` event — correctly toggles the preview
     box open and populates it with real rendered HTML every time. It is
     not gated behind a `change` event and not disabled by design; the
     wiring (`onclick="previewCopy(...)"`, `_ADMIN_COPY_PREVIEW_JS`) is
     correct as written. **WebKit could not be tested from this
     sandbox** — `playwright install webkit` is a confirmed 403 policy
     denial (same standing limitation this doc records elsewhere for
     other fixes), so a WebKit-specific defect can't be ruled out from
     here. Flagged as unresolved rather than silently closed: worth
     re-checking directly in the browser/device the original report came
     from — a stale cached JS bundle (hard refresh) or a WebKit-specific
     quirk are both more likely than a code defect, given the button is
     provably not disabled and provably wired to a function that exists
     and runs correctly on click.

- **Current Feed (`GET /current-feed`, 2026-09) — a public mixtape-tracklist
  page listing the writers/publications Brian actually reads, derived live
  from the `feeds` table.** Side A ("Timeless Classics") / Side B ("The New
  Generation") — renamed from the original "Old School"/"New School" in a
  2026-09 cassette-treatment follow-up, see below — are two per-feed
  columns — `feeds.show_on_current_feed`/
  `feeds.current_feed_side` — not section-name matching, per a mid-flight
  redesign: the original section-based design (Blogs=Side A,
  Substacks=Side B, News/Market Insights excluded) left one real gap, a
  feed in a section that's neither a known side nor a known exclusion
  (production already has an empty "Tools" section) had no clean home. A
  per-feed flag has no such edge case — any feed, in any section, simply
  isn't shown until deliberately marked. `Library.seed_current_feed_sides()`
  (settings-flagged, same discipline as `seed_paywall_cookie_flags`) seeded
  existing rows once from their section at the time; **new feeds default to
  hidden** (Brian's own call — deliberate over unreviewed). Admin control is
  one auto-submitting dropdown per row on `/admin/reader/feeds` itself
  (Hidden/Timeless Classics/The New Generation —
  `_CURRENT_FEED_SELECT_CHOICES`, shared with the add/edit form) plus the
  same control there. **The hidden-feed
  footnote is public, not admin-only** — since a hidden feed is still in
  FP&A Buddy's web-search allowlist, silently hiding it from the tracklist
  would misrepresent the tool: `_current_feed_hidden_footnote()` names every
  hidden feed grouped by section, derived from the data at render time
  (never a hardcoded list), with a plain "excluded from the tracklist
  format, not from search" disclosure. Every track links to `feeds.html_url`
  (the writer's own site, populated at add-time from the feed's own
  `<link>`/alternate — already existed, already populated on every
  production row, no new column needed), never `xml_url`. Track titles use
  Caveat (`var(--font-sticker)`, 700 weight, 16px) — reversed from an
  initial Permanent-Marker-wordmark pass, since that face is built for a
  word or two, not seventeen names of varying length. **No coral anywhere
  on this page** — the Side A/Side B divider was considered and explicitly
  rejected as a coral moment (structure isn't something a reader acts on).
  Reachable from `/how-this-is-built`'s origin story, a small link on
  `/tools/fpa-buddy` near the Sources chips, and the `web-search`
  `ai_surfaces` explainer's own body (closed via
  `scripts/add_current_feed_link_to_web_search_explainer.py`, a one-off,
  human-run production content fix — see ARCHITECTURE.md's matching
  section for the full write-up). Also doubles as the FP&A Buddy web-search
  allowlist, since `linklib.sources.preferred_domains` builds from the same
  OPML `feeds`/`feed_sections` generate.
- **Current Feed display order (2026-09 follow-up) — a third per-feed
  column, `feeds.current_feed_order`, since a mixtape's running order is
  part of the point.** `current_feed()` sorts within each side by
  `(current_feed_order, id)` — `id` is the stable tie-breaker (permanent,
  already unique), so two feeds sharing a number render in a fixed
  sequence rather than an unspecified SQLite order, and the page can't
  reshuffle between requests. The admin control is a second per-row
  number `<input>` (`.ff-order`), cross-associated to the same `<form>`
  as the existing side `<select>` via `form=""` (the same cross-cell
  trick the Sections table's rename form already uses), so either
  control's `onchange` submits both fields together. **Left visible and
  editable even when a feed is Hidden** — deliberate, not an oversight:
  hiding it would need JS keyed off the side dropdown for no real
  benefit, and leaving it visible lets a position be set ahead of turning
  a feed on rather than blocking or losing that value. Seeded once via
  `Library.seed_current_feed_order()` (settings-flagged, same
  non-emptiness-check discipline as every other one-time feed seed —
  `0` is also a real "goes first" value) from the render order shown
  feeds already had before the column existed, so shipping it didn't
  move anything; Brian reorders from there.
- **Current Feed order, arrows-not-typing follow-up (2026-09) — the number
  `<input>` from the bullet above is retired; two problems it had, both
  fixed structurally rather than validated around.** The number field's
  `onchange="this.form.submit()"` fired on every keystroke change, not
  once per edit — typing "12" submitted "1" first, a real intermediate
  value reaching the database before the second digit was ever typed.
  Worse, nothing stopped two feeds from ending up at the same order value
  (Brian hit this directly: setting one feed's order to 1 left it tied
  with another feed already at 1, saved silently, no warning). Both are
  gone now, not mitigated: the Order column is two buttons (&uarr;/&darr;,
  `_cf_order_arrows_html`), each posting to a new
  `POST /admin/reader/feeds/{id}/order-move` (`direction=up|down`,
  `admin_feeds_move_order`) — there is no text field to type an
  intermediate value into. Every move renumbers the **whole side** to a
  dense `0..N-1` sequence, not just the two rows being swapped (find the
  feed's index in its side, sorted by the same `(current_feed_order, id)`
  tie-break `current_feed()` itself uses; swap with the neighbor in that
  direction if one exists; reassign `0..N-1` across the resulting list) —
  so the very first move on a side with a pre-existing duplicate or gap
  self-heals it, and a fresh duplicate can never be created going forward.
  An arrow is rendered `disabled` (a plain non-form `<button>`, so clicking
  it can't even post a no-op) for a Hidden feed (order is inert until
  shown) or when the feed already sits at that end of its side. The typed
  field is gone from the full add/edit forms too, not just the inline
  table row — a brand-new feed, or a feed newly joining a side from Hidden
  or the other side, is appended to the end via a new
  `_next_current_feed_order(lib, side, exclude_feed_id=None)` helper
  (`count of what's already shown on that side` — always correct because
  every side stays densely numbered); a feed keeping the same side on an
  edit-form save keeps its existing order untouched, since reordering
  within a side is the arrows' job now, not a field a save can silently
  reset.
- **Feeds admin Cookie column, wide enough for status plus age (2026-09) —
  the same per-row health indicator introduced in "Subscriber-cookie
  health moved into the feed table" above still wrapped onto two lines in
  production** (OnlyCFO, Cautious Optimism, Mostly Metrics), because the
  `<th>`/`.ff-cookie` width was `_COL_WIDTH_STATUS` (110px) — right for a
  bare status badge, but this column's real content is a dot, a state
  word ("working"/"inconclusive"/"expired"), and a relative age
  ("· just now"/"· 45d ago") all on one line. Measured in real Chromium
  with DM Sans loaded: "inconclusive · 45d ago" needs ~148px of content
  width, and "· 999d ago" (the longest `_relative_age()` can ever
  produce) pushes that to ~157px — plus 24px of cell padding, a worst
  case around 181px, comfortably past the old 110px column. A new
  `_COL_WIDTH_STATUS_AGE = 190` constant (documented alongside the other
  `_COL_WIDTH_*` field-type widths) replaces `_COL_WIDTH_STATUS` for this
  one column, leaving a small margin over the measured worst case rather
  than sitting flush against it. **The deeper root cause wasn't Cookie's
  width alone — `.ff-table` had no `min-width` at all**, so with
  `table-layout:fixed`, every column (Cookie included) got proportionally
  squeezed to fit whatever container width the page happened to render
  at, at any desktop width; there was no floor and no scroll, only a
  full-stacked-cards fallback below 820px. Fixed the same way every other
  wide (8+-column) admin table on this site already is: `.ff-table` now
  carries a real `min-width:{_TABLE_FLOOR_XWIDE}px` (the 8+-column
  bucket — Name, URL, Section, Cookie, Subscriber, Current Feed, Order,
  Actions is exactly 8), wrapped in the same shared `#cmp-scroll-wrap`/
  `_ADMIN_SCROLL_HINT_HTML`/`_ADMIN_SCROLL_HINT_JS` overflow-x:auto +
  "Scroll for more" hint every other wide admin table already uses,
  rather than inventing a page-specific mechanism. The existing
  `@media(max-width:820px)` stacked-card breakpoint resets the min-width
  back to `0` in the same rule (a class rule, not an inline style, so a
  later same-specificity media-query rule wins with no `!important`
  needed — confirmed, not assumed, since PR 12 needed `!important` for
  the inline-style version of this exact problem elsewhere). Verified
  live end to end (real Chromium, real Google Fonts, the actual rendered
  `/admin/reader/feeds` response, not a simplified repro): the worst-case
  content ("inconclusive · 999d ago") renders on one line at every
  desktop width from the 820px breakpoint up to 1920px, and at the
  table's 960px floor every other column still renders its full intended
  share (Name/URL/Current Feed ~131-140px, Section ~96px, Subscriber
  ~79px, Order ~61px, Actions ~132px) rather than being squeezed further.
  See `tests/test_feed_cookie_flag.py`'s
  `test_cookie_column_is_wide_enough_for_status_plus_age`/
  `test_feeds_table_has_a_min_width_and_scrolls_instead_of_squeezing` for
  the regression coverage — both confirmed to fail against the pre-fix
  code before being trusted.
- **`/current-feed` copy revision + a "Last mixed" cassette-label stamp
  (2026-09).** Three changes, all Brian-directed. (1) The `<h1>` moved
  from "Current Feed" to "Current feed" — the one holdout of BRAND.md
  §3.2's sentence-case standard on this page. (2) The intro copy and the
  hidden-feed footnote were both replaced/restructured: the footnote's
  old single run-on sentence became a lead sentence ("Not on the tape:
  While I wish I could read everything, time is finite—so the Buddy also
  pulls from a few other trusted sites for more timely news and data.")
  followed by a real `<ul>` — the underlying per-section `{section}:
  {names}` groups `_current_feed_hidden_footnote()` derives are
  byte-for-byte the same data as before, only the framing/markup around
  them changed. A new closing line ("Have something you think I should
  add to the list? Send me the demo track and you might see it show up
  on a future update.") links "Send me the demo track" to `/contact` —
  a bare internal link, not `/contact?context=...`, since nothing asked
  for the resource-suggestion prefill convention here. (3) A new
  **"Last mixed [date]" stamp**, styled as a small white write-on
  cassette label (`.cf-stamp`/`.cf-stamp-row`), sits in the lower-right
  of `.cf-tape-card` — a `justify-content:flex-end` row placed after
  `.cf-sides` inside the card, not an absolutely-positioned overlay, so
  it can never overlap the tracklists regardless of how many tracks each
  side has. **Investigated and confirmed before building, per explicit
  instruction not to substitute a different timestamp if this didn't
  exist**: `feeds.created_at` is a real column, present since the
  table's original `CREATE TABLE` (not a later `ALTER TABLE` migration),
  set to `_now()` by `add_feed()` on every insert (including
  `seed_feeds_from_opml()`'s own calls) and never touched by
  `update_feed()` — so `MAX(feeds.created_at)` genuinely answers "when
  was a feed last ADDED," not "when was anything about the feed table
  last touched," exactly the distinction Brian's own instruction called
  for. `_current_feed_stamp_date(feeds)` computes this directly from the
  `feeds` list `current_feed()` already fetches (no new query, no new
  `Library` method) and returns `""` on no feeds or an unparseable
  `created_at` — the stamp renders only when that's non-empty, real data
  or nothing, never a substitute. Verified live (real Chromium, real
  Google Fonts) at desktop and 390px mobile: the stamp sits inside the
  card with no overlap at either width. See `tests/test_current_feed.py`'s
  new copy-revision/stamp section for the regression coverage, including
  a direct proof that editing an existing feed's name never moves the
  stamp forward (only adding a genuinely new feed can).
- **Cookie domain registry, live-derived (2026-09) — see the entry above
  in this same section for the full incident and fix** (`_COOKIE_DOMAINS`
  hardcoded tuple → `linklib.extract._opml_feed_domains()`, parsed live
  from `preferred_sites.opml`). Filed here too since it was diagnosed and
  fixed in the same pass as the two feeds-admin items above it.
- **Subscriber-cookie health moved into the feed table (2026-09) — the
  always-on per-domain summary panel above the table is retired; status
  and last-checked now render in each feed's own Cookie cell.** The old
  panel (`_cookie_status_panel`, one `<div class="ck-row">` per domain)
  sat visually apart from the rows it described, and the table's own
  Cookie column only ever said "configured" — true the moment a variable
  exists, regardless of whether fetching actually works. That gap is
  exactly what let Cautious Optimism's mismatch go unnoticed: its row read
  identically to a genuinely healthy cookie. Fixed by moving
  `authcheck.get_auth_status()`'s persisted per-domain record
  (`{"ok": bool|None, "checked_at", "detail"}`) into a new
  `_cf_cookie_cell_html(domain, configured, status, feed_name)`, called
  once per row from the same loop that already renders Section/Cookie/
  Subscriber/Current Feed/Order — a colored dot (green/red/amber, the same
  `_COOKIE_STATE_STYLES` triple as before) plus a relative "3h ago" via
  the existing `_relative_age()`, or "configured, not yet checked" for a
  domain with a variable set but no probe result yet. `_auth_cookie_controls()`
  now returns a 3-tuple (`button_html, refresh_steps_html, status`) instead
  of `(button_html, panel_html)` — the domain-summary half of its return
  value is gone; the coral "Subscriber cookie expired" block with the
  step-by-step refresh instructions is unchanged and still renders when
  any domain is stale, since a per-row dot can't carry "here's exactly
  which env var to update" without cluttering every row. **"Re-check
  subscriber access" stays the only trigger** — nothing about how or when
  the check runs changed, only where its result is displayed. The now-dead
  `.ck-panel`/`.ck-row`/`.ck-dot`/`.ck-dom`/`.ck-state`/`.ck-detail`/
  `.ck-age` CSS is removed along with `_cookie_status_panel()` itself.
  **One design point from the retired panel's own comment worth noting as
  reversed, not silently dropped**: it argued a per-row light would
  "misrepresent the relationship" whenever two feeds shared a domain,
  since cookies are keyed by domain and the table by feed. In practice two
  feeds sharing a domain would just show the identical, correct status on
  both rows — not a misrepresentation, since it *is* true of both — so
  this objection didn't hold up once actually building the per-row
  version.
- **Feeds-admin follow-up, three real issues from live use (2026-09) — a
  rank indicator (not a functional bug), a shared cookie-state helper, and
  a page-copy trim.** All three surfaced the same day the arrows/registry/
  per-row-health PR above shipped.
  1. **Arrow sizing.** `_cf_order_arrows_html`'s outer `<span>` was a plain
     `display:inline-flex` with no `align-items` set — the default,
     `stretch`, sizes each flex child to the container's full cross-axis
     height, and a bare `disabled` `<button>` and a `<form>`-wrapped
     `<button>` don't report the same natural height to that stretch
     calculation, so a disabled arrow rendered visibly smaller than an
     enabled one. Fixed with `align-items:center` on the outer span (which
     now also holds the new rank text below), with the two arrow buttons
     nested in their own inner flex span.
  2. **"Reordering doesn't work" — investigated and confirmed NOT a
     functional bug.** Brian's USV/Fred Wilson report (an up arrow that
     moved once then wouldn't move again, despite the row not looking like
     it was at the top) was checked directly against production data via
     the `/mcp` introspection tools: USV genuinely held `current_feed_order=0`,
     a real, valid, unique rank-1 value, inside a clean dense `0..10`
     sequence with no duplicates or gaps — the disabled-boundary check and
     the whole-side renumber (both from the arrows-not-typing PR above)
     were both already correct. The actual cause is structural, not a bug:
     `Library.list_feeds()`'s own ordering (by section, then feed id — see
     that PR's own docstring) has zero relationship to a feed's Current
     Feed rank, so the admin table's row position has never meant "this is
     first/last within its side." A correctly-disabled up arrow on a row
     sitting visibly lower in the table reads as broken when nothing next
     to it says otherwise. Fixed by rendering the feed's real rank ("1 of
     11") beside the arrows — `admin_feeds()`'s `_cf_boundary` map grew
     from `(is_first, is_last)` to `(is_first, is_last, rank_1indexed,
     total)`, computed once per side from the same sort the renumber logic
     already uses, so the number shown is guaranteed to match what the
     arrows themselves are keyed off. No change to the disabled condition,
     the move route, or the renumber — this is a display-only fix for a
     display-only confusion.
  3. **Cookie column: cramped width and disagreement with the edit page —
     fixed at the root with one shared computation.** `_cf_cookie_cell_html`
     and `_feed_cookie_readout` (the list cell and the add/edit form's own
     readout) each independently derived "is this cookie configured/
     working" from a slightly different question — the edit form said
     "Cookie configured for this domain" in green for any domain with a
     `LINKLIB_COOKIE_<DOMAIN>` variable set, regardless of what the list
     page's own health probe had found for it, which is exactly how
     Cautious Optimism's row could read "expired" on one page and green on
     the other for the identical feed. New `_cookie_health_state(domain,
     configured, status)` is the single function both now call — returns a
     dict (`state`/`color`/`label`/`age`/`detail`) covering the same four
     states either surface needs (not configured / configured-but-
     unchecked / working / expired-or-unknown) — so the two pages can no
     longer independently disagree, by construction rather than by
     convention. Threading the edit form's readout onto the same live
     `authcheck.get_auth_status()` data the list page already reads
     required passing `auth_status` through `_feed_cookie_readout` ->
     `_feed_form_fields` -> `_feed_form_page`, fetched fresh at all three
     call sites that render the edit/reject form (`admin_feeds_edit`, and
     the `_reject()` closures inside both `admin_feeds_new_submit` and
     `admin_feeds_edit_submit`) — the add form (no existing feed, no
     `xml_url` yet) has nothing to compute against and passes `None`.
     Width fixed by switching the Cookie column from a hand-picked `9%`
     to the existing shared `_COL_WIDTH_STATUS` (110px) constant, plus
     shortening the visible unchecked-state text from "configured, not yet
     checked" (which wrapped to three lines in that width) to "Not yet
     checked" — the fuller phrase survives only in the `title`/aria-label
     for anyone who hovers or uses a screen reader.
  4. **Too much explanatory text above the table.** The 2-sentence intro
     paragraph plus 5-bullet mechanics list (Sources rail, Cookie,
     Subscriber, Current Feed, Order) that used to sit between the auth
     panel and the table is cut to one sentence ("The RSS subscriptions
     behind the Reader's Feed view and FP&A Buddy's web-search
     allowlist."); the same five explanations move to a new "Column
     reference" section right after the table, alongside the pre-existing
     "Finding the right cookie in DevTools" instructions they already sat
     near — reference material a returning admin skips past, not
     onboarding copy re-taught on every visit. The Order bullet also
     gained a line explicitly naming that the table's own row order
     doesn't reflect a feed's Current Feed rank, tying the copy fix back
     to fix #2 above rather than leaving that a silent design fact.
  See `tests/test_current_feed.py` (rank-matches-current-feed-order,
  consistent arrow sizing), `tests/test_cookie_status_panel.py`
  (shortened text, named-constant width, list/edit agreement), and
  `tests/test_feed_cookie_flag.py` (column-reference relocation, the
  updated Section-column width) for the regression coverage.
- **Feeds table: click-to-sort headers, and the "N of M" rank readout is
  retired (2026-09) — the sort now solves the problem the readout was a
  workaround for.** Brian's own framing of the follow-up request: the
  table sorts by section then feed id by default, so it never shows the
  order being edited, and a correctly-disabled up arrow on a row that's
  genuinely first reads as broken because nothing in the table's default
  view says so. A rank number next to the arrows patched the symptom;
  making the column itself sortable fixes the cause. **Client-side, not
  server-side** — the table is ~21 rows, small enough that a full
  client-side sort/reorder is instant and needs no query-param plumbing,
  and (the deciding factor) an Order-arrow click is still a plain
  `POST`-then-redirect full-page reload, not an AJAX call, so a
  server-side sort would need its own `?sort=`/`?dir=` query params
  threaded through every arrow's redirect target anyway — no simpler than
  the client-side version, and client-side sorting reorders `<tr>` DOM
  nodes directly rather than re-rendering server output, which keeps
  every row's live `<form>`/`<select>` state (the Section dropdown, the
  Subscriber checkbox, the Current Feed select) intact across a sort with
  zero extra code. Six of the eight columns are sortable — Name, Section,
  Cookie, Subscriber, Current Feed, Order — the same six the build brief
  named; URL and Actions have nothing worth sorting by and stay plain.
  Each sortable `<th>` carries `data-sort="<field>"`, is keyboard-
  operable (`tabindex="0"`, `role="button"`, Enter/Space via `onkeydown`),
  and shows a ▲/▼ `.ff-sort-ind` span for the active column. Click once to
  sort ascending, again to reverse; clicking a different column moves the
  indicator there and clears the old one. **Default sort is genuinely
  untouched** — clicking nothing leaves every row exactly where
  `Library.list_feeds()`'s own section-then-id order put it, matching the
  explicit requirement that nothing moves for anyone not deliberately
  sorting; the new `_FEEDS_SORT_JS` only reorders `<tr>`s once a sort
  state exists in `localStorage` (key `ffSort`), never on a fresh page
  load with no stored state.

  **Order's sort key is a zero-padded composite string** — `"{side_rank}-
  {order:04d}"`, `side_rank` = 0 for `old_school`, 1 for `new_school`, 2
  for Hidden — computed once per row alongside the other five plain
  `data-*` sort attributes (`data-name`, `data-section`, `data-cookie`,
  `data-subscriber`, `data-current-feed`), all lowercased so the shared
  string-comparison sort function needs no per-field special-casing
  except Order's composite. Grouping by side FIRST, then by position
  within it, is deliberate: that's the exact sequence the up/down arrows
  move a feed through, so sorting by Order is the one view where a row's
  position in the table actually equals its rank — the direct answer the
  rank readout used to compute and print separately.

  **The sort persists across a page reload via `localStorage`, not a URL
  param** — necessary specifically because the Order arrows themselves
  cause a full-page reload (a `POST` to `/admin/reader/feeds/{id}/
  order-move`, redirecting back to the plain `GET /admin/reader/feeds`
  with no query string). Without persistence, sorting by Order, clicking
  an arrow, and landing back on the default section/id view would
  silently undo the one sort state that actually answers "did this
  work" — reproducing the exact confusion this whole follow-up exists to
  fix. Verified live (real headless-browser session, not just rendered
  HTML): sorting Name ascending/descending toggles the ▲/▼ indicator and
  reorders rows correctly; sorting Order groups a same-side feed set by
  position (0, 1, 2, ...) ahead of a different-side feed regardless of
  table/insertion order; a page reload after sorting by Order keeps that
  sort and its indicator; switching to a different column's sort clears
  the old column's indicator.

  **The "N of M" rank readout is dropped, not kept as a belt-and-
  suspenders redundancy** — `_cf_order_arrows_html`'s boundary tuple
  reverts from `(is_first, is_last, rank, total)` back to plain
  `(is_first, is_last)`, and the cell markup collapses from two nested
  spans (one wrapping both the arrows and the rank text) to one. Once
  sorting by Order shows a feed's real rank directly, in the row order
  itself, a static number recomputed and printed beside two already-
  narrow buttons was noise competing for the same small cell, not a
  second safety net worth keeping "just in case" — the explicit call this
  follow-up asked to make. The Column reference bullet for Order was
  rewritten to describe the sort instead of the retired number.
  See `tests/test_current_feed.py`'s
  `test_order_column_is_sortable_by_side_then_position` (the composite
  sort key, and confirming the old rank text is genuinely gone) and
  `test_order_column_headers_are_clickable_and_carry_a_sort_indicator`
  for the regression coverage.

- **`/current-feed` cassette J-card visual treatment (2026-09) — an
  investigate-and-propose build with a real approval gate, plus a side
  rename: "Old School"/"New School" become "Timeless Classics"/"The New
  Generation" everywhere.** Brian's ask was the literal 1990s cassette
  J-card, not a stylistic nod — the paper card, the ruled tracklist form,
  a boxed side letter, tilt, shadow. Investigated first: BRAND.md §4's
  graffiti/street-art accent layer is a closed, four-item vocabulary with
  no room for a paper-card panel or a plastic case, so this needed a new,
  page-scoped BRAND.md exception before any CSS shipped — added as a
  fifth, explicitly-scoped item, not a general license (see BRAND.md §4).
  Two full mockups (card-only, and card-plus-plastic-case) were built and
  screenshotted at 1280px/390px before writing real code, per the standing
  "mock it and show, don't just describe it" instruction for this kind of
  visual proposal. **The case didn't ship** — it read as a convincing
  stylized case, never photorealistic (a flat gradient sheen and a
  border-radius aren't real depth cues), and Brian's call was that its
  cost wasn't worth it against the plainer card alone, which already reads
  as "cassette" without anything that looks like it's trying. Only
  `.cf-tape-card` (off-white panel, 1px border, warm-black shadow, a
  slight `-0.6deg` tilt) shipped.
  **Two direct-feedback iterations shaped the final boxed-letter
  treatment.** The first mockup paired the box with a small preprinted
  "DATE/TIME · NOISE REDUCTION" label; cut on direct instruction, since a
  standalone "Side A" eyebrow, the boxed letter, and the side's own name
  were three labels doing one job. The tradeoff was flagged before
  cutting, not silently dropped: on a real J-card, that preprinted form
  text is what makes the handwriting read as filled INTO a form, so
  losing it makes the box read as a plain label rather than a form field
  — accepted as the simpler version to try first, confirmed against a
  real screenshot with real fonts loaded. The box itself stayed — "the
  strongest cassette cue on the page," per direct instruction — merged
  onto one line directly beside the side's name, with the standalone
  eyebrow removed and `aria-hidden="true"` on the box (the heading already
  names the side). Ruled lines are dotted and darkened/thickened from an
  earlier, fainter pass specifically so they read as part of a printed
  form rather than a plain content divider that happens to be dotted.
  **Side names became mixtape themes, not category labels, in the same
  pass** — "Old School"/"New School" (the original 2026-09 launch copy)
  are now "Timeless Classics"/"The New Generation", in exactly the two
  places the old names appeared: `_CURRENT_FEED_SIDE_LABELS`' heading on
  `/current-feed` itself and `_CURRENT_FEED_SELECT_CHOICES`' admin
  dropdown label on `/admin/reader/feeds` — one vocabulary, so an admin
  picking a side sees the same name a visitor reads. **Display copy
  only** — the stored `current_feed_side` values (`old_school`/
  `new_school`) are untouched; a display rename doesn't need a migration.
  Checked for crowding before shipping: the admin `<select>` sits in an
  `11%`-width table column and a closed `<select>`'s box doesn't reflow on
  a longer selected-option string (the browser truncates the closed-state
  text, never the box), and the `/current-feed` page line fits its
  half-width grid column at 1280px+ with no measured crowding either — no
  shortening needed on either surface. No coral anywhere on the page,
  still — the paper panel and its shadow are achromatic, matching the
  black-ink-on-white-card reference photo, and Brian's explicit call was
  that the page doesn't need a coral moment to work. At `max-width:430px`
  the tilt, shadow, and rounded corners all flatten to a plain bordered
  rectangle — no case (there never was one), no tilt, no shadow drama on a
  phone; the existing 800px `.cf-sides` stacking breakpoint needed no
  change. **Verified against real Google Fonts before sign-off, not this
  sandbox's fallback-font default** — this sandbox's headless Chromium
  normally can't reach `fonts.googleapis.com` at all (a documented,
  standing limitation elsewhere in this doc), but a direct `curl` check
  found the outbound TLS proxy actually does reach it; Chromium was only
  refusing the connection because it doesn't trust the proxy's own CA the
  way system `curl` does. Launched with `--ignore-certificate-errors` for
  this one verification render (never something to carry into production
  code or a committed test) and confirmed via `document.fonts` — not just
  the CSS declaration, which reports what was asked for regardless of
  whether it loaded — that Caveat 700 genuinely reached `status:'loaded'`
  before the screenshot was taken; a fallback-font screenshot would have
  completely hidden the handwriting-versus-print contrast the whole
  review was about. See ARCHITECTURE.md's "Current Feed" section for the
  full write-up and `tests/test_current_feed.py` for the regression
  coverage.

- **Coral-guard correction (2026-09) — the module-level bool from the PR 16
  recursion-fix bullet above WAS a real cross-thread race after all, and
  its first proposed fix (`threading.local()`) was built, measured, and
  rejected before merge; the guard is `contextvars.ContextVar` now.** A
  later review pushed back on PR 16's own "deliberately not thread-local,
  no genuine parallelism" framing: `webapp.tasks._failing_checks_count()`
  holds `_checks_cache_lock` only long enough to check/update the cache
  dict — it releases the lock BEFORE calling `_checks.run_all()`, so two
  admin-role page renders landing close together against a stale (120s
  TTL) cache genuinely call `run_all()` -> `coral_moment_problems()`
  concurrently, on two different threadpool threads (FastAPI dispatches
  sync `def` route handlers via `run_in_threadpool`). This needs no
  exotic trigger — two ordinary overlapping page loads by the one admin
  user, e.g. two tabs, around a cache-expiry boundary. A shared module
  bool is wrong for this: thread A mid-loop (flag already `True`) makes
  thread B's own, unrelated top-level call silently short-circuit to
  `[]`, so B's `run_all()` reports "no coral problems" as fact even if a
  real one exists — a false negative on an advisory check.
  `threading.local()` looked like the fix, and was built and measured
  before being trusted, per the standing discipline for this kind of
  concurrency change — and found WRONG: the re-entrant case this guard
  exists for is not confined to one OS thread. `coral_moment_problems()`
  calls `TestClient(app).get(path)` in a loop, and each of those calls
  dispatches through `run_in_threadpool` again, onto a THREADPOOL WORKER
  THREAD that may be a different OS thread than the one already running
  this function. Measured directly: a single signed-out GET "/" in
  open-auth (admin) mode recurses through this exact chain (the same
  chain PR 16's own recursion fix describes), and with `threading.local()`
  each nested call landed on a genuinely different OS thread (confirmed
  via `threading.get_ident()` at 5+ levels deep before the probe was
  killed) — so each one saw its own thread-local `in_progress=False` and
  started a brand-new, real, unbounded pass over every route, turning the
  old bool's bounded ~18s recursion into indefinite threadpool growth (a
  real deadlock risk in production, since anyio's worker pool has a
  capacity limit). The module bool "worked" for this specific case only
  by accident, by being visible to every thread regardless of which one
  set it. `contextvars.ContextVar` is the primitive that has both
  properties this guard actually needs: Starlette's `run_in_threadpool`
  explicitly `copy_context()`s the calling context into each dispatched
  worker thread, so a `.set(True)` made before a nested
  `client.get(...)` call is correctly visible inside that nested call —
  restoring the ~18s bounded-recursion baseline with no threadpool
  blowup (verified) — while a genuinely unrelated top-level call (a raw
  `threading.Thread`, or a separate incoming request's own asyncio Task
  spawned from the server's own never-`.set()` top-level context) starts
  from an unmutated ancestor context and never inherits another call's
  in-progress flag (verified with a real two-thread harness: the second
  thread's own result is a real, non-suppressed check, not a leaked
  `[]`). See `webapp/app.py`'s own comment above `_CORAL_CHECK_CONTEXT`
  for the full mechanism — **do not revert this to a plain module-level
  bool or to `threading.local()` "for simplicity"; both were tried, both
  were measured, both are wrong.**
  `tests/test_coral_discipline.py::
  test_concurrent_calls_do_not_leak_in_progress_state_across_threads` (a
  deliberately synchronized two-thread harness, not a hopeful timing
  test) fails deterministically against the old module bool
  (`AssertionError: thread B's result was suppressed by thread A's
  unrelated in-progress state...: {'problems': []}`) and passes against
  the `contextvars` fix;
  `test_same_request_recursion_stays_bounded_across_threadpool_workers`
  fails (times out past 90s, still recursing) against a
  `threading.local()` reconstruction and passes (~28s for the whole
  9-test file) against the `contextvars` fix — both reproduced live in
  this session before either fix was trusted, per the standing "prove a
  regression test actually fails against the old code, don't just show
  it passing" discipline. `webapp.tasks._failing_checks_count()`'s own
  lock still only guards the cache dict, not the `run_all()` call — two
  concurrent cache-miss threads now both correctly compute a real,
  non-suppressed result (fixed by this change), but they still
  redundantly redo that expensive computation rather than one waiting on
  the other's in-flight result. Flagged as a real, separate performance
  question — assessed, not fixed here, out of explicit scope.
- **`_failing_checks_count()` redundant-work fix (2026-09) — the "assessed,
  not fixed" question the coral-guard bullet above left open, closed with
  the same investigate-before-you-build discipline.** The obvious fix
  (double-checked locking: acquire a lock, re-check the cache under it,
  compute only if still stale) is unsafe: `_failing_checks_count()` is
  genuinely re-entered through its own call chain (`run_all()` ->
  `coral_moment_problems()` renders every public route, and in open-auth
  mode every one of those renders is `role="admin"`, so `_page()` calls
  `_has_open_admin_tasks()` -> this function again) — on a *different* OS
  thread than the one running the outer call, since `run_in_threadpool`
  dispatches each nested render onto its own worker (the exact mechanism
  the coral fix's own comment documents). Confirmed live with real
  instrumentation on a signed-out `GET /` in open-auth mode, per the
  brief's own "prove it, don't read the call graph" requirement: `run_all()`
  executed twice, at depth 2, on two distinct thread ids — matching the
  earlier bullet's own measurement exactly. A `threading.Lock` held across
  `run_all()` would deadlock permanently in this case (the outer thread
  holds the lock while blocked waiting for the render; the nested call, on
  a different thread, blocks trying to acquire that same lock — neither
  ever proceeds). `threading.RLock` does not fix it either — it only waives
  re-entry for the *same* thread, and the nested call is provably on a
  different one. **Fixed with a non-blocking sentinel instead**
  (`_checks_computing`, `webapp/tasks.py`): any caller — genuinely
  concurrent (two admin browser tabs hitting a stale cache) or the
  recursive same-chain case above — that finds a computation already in
  flight just returns whatever's cached (or `0` cold-start) rather than
  trying to compute or waiting for the in-flight one to finish. Nothing
  ever blocks on another thread's progress, so this can't deadlock either
  way — and it does better than merely being safe: the recursive case now
  skips the redundant `run_all()` entirely (confirmed: the same real
  open-auth `GET /` that showed depth-2/2x `run_all()` before the fix now
  shows depth-1/1x after it), and two genuinely independent concurrent
  cache misses now compute `run_all()` exactly once instead of twice
  (confirmed with a deliberately synchronized two-thread harness, same
  discipline as the coral PR's own concurrent-threads test — proven to
  fail against the pre-fix code with `run_all()` computed twice before
  passing against the fix). See ARCHITECTURE.md's matching section for the
  full write-up and `tests/test_task_badges.py`'s re-entrancy/concurrent-
  miss section for the regression coverage, including a real signed-out
  `GET /` in open-auth mode run in a background thread with a hard join
  timeout — the test that matters most, since a naive lock-based "fix"
  would have hung it permanently rather than merely run it slowly.

- **Social share cards — Open Graph / Twitter Card metadata (2026-09).**
  Every page previously shared as a bare link: no description, no image
  — a grep sweep confirmed zero `og:`/`twitter:` tags anywhere. Shipped in
  two planned phases; **Phase 2 (automated image generation) is killed
  outright, not deferred** — recorded here with the reasoning, not just
  the outcome, so a future session doesn't quietly rebuild it.
  **Phase 1 (shipped)**: `_page()` (`webapp/app.py`, the one shared
  `<head>`-assembly function behind 181 route call sites) gained three
  optional keyword params — `request` (for an accurate absolute `og:url`;
  threaded through only the handful of genuinely public/shareable pages —
  `/`, `/about`, `/thought-leadership`, and the two catch-all article
  routes — everything else, mostly admin, falls back to the bare
  `PUBLIC_BASE` root rather than erroring), `og_description`, and
  `og_image_slug` — and now emits `og:title`/`og:description`/`og:image`/
  `og:url`/`og:type`/`og:site_name`, the four `twitter:*` tags, and
  `<meta name="description">` on every page, with a hardcoded
  `_OG_DEFAULT_DESCRIPTION` fallback when a route passes nothing. `og:title`
  reuses `_short_title(title)` — the same suffix-stripped value already
  computed for the real `<title>` tag, just without the "BMW CFO · "
  prefix. Committed 1200×630 PNGs live in `webapp/static/og/`, slug-keyed
  (`{slug}.png`) with one `default.png` fallback for everything else;
  `_og_image_slugs()` is a module-level, computed-once filename-set cache
  (`_OG_IMAGE_SLUGS`, same "doesn't reset between tests in the same
  process" caveat as `webapp.tasks`' own `_checks_cache` — tests that
  add/remove files under `_OG_DIR` must reset it explicitly) rather than
  an `os.path.exists()` per render. **A real Phase 0 finding that changed
  the actual route shape**: `/static/{filename}` is a single-path-segment
  route with no `:path` converter, so `/static/og/{slug}.png` as originally
  proposed can't route at all — a nested URL simply never matches. Fixed
  with a dedicated sibling route, `GET /static/og/{filename}` → a new
  `_OG_DIR`, mirroring the established precedent every other "committed
  images in a subdirectory" need on this site already uses
  (`tools_software_screenshot`, `tools_software_logo`, etc.) — same
  basename-only traversal guard as `/static/{filename}`.
  **Escaping — two different treatments for what looks like the same
  field, confirmed live via production data before writing any code**:
  `original_content.teaser` is stored PRE-ENCODED (real HTML entities
  already in the string — `"R&amp;D"`, `"&mdash;"`, confirmed against the
  live growth-engine-ratio row) — the normal `_esc()` would double-encode
  it (`R&amp;amp;D`, rendering as literal `&amp;D` to a scraper), so it's
  interpolated through a new, narrowly-scoped `_esc_attr_quote_only()`
  instead — escapes only a literal `"` (the one character that can break
  out of the `content="..."` attribute), deliberately leaves `&` alone,
  matching `_oc_card_tuple`'s own established raw-interpolation precedent
  for this exact field. `ai_surfaces.teaser`, by contrast, is stored as
  genuinely plain text (confirmed the same way) and goes through the
  ordinary `_esc()`. Both regression tests are proven to actually fail
  against the wrong escaping choice, not just pass trivially against the
  right one. `/admin/brand` gained a "Social share cards" section listing
  every committed card for download (`default.png` first, then every
  `{slug}.png` sorted) via a plain `<a href download>` link — the same
  general "download a static asset" pattern every other admin download
  affordance on this site already uses (CSV template downloads, the DB
  snapshot download) — not the Avatar section's own upload/remove pattern,
  which the original build brief assumed existed for the headshot and
  doesn't; there's no upload path for these, they're committed directly to
  the repo.
  **Phase 2 killed, not deferred — the reasoning, so it stays killed**:
  Pillow is a dependency this codebase has deliberately avoided at least
  three separate times already — the App screenshot upload uses
  client-side Cropper.js specifically to avoid server-side image
  processing; `scripts/audit_tool_logo_dimensions.py` hand-parses
  PNG/JPEG/GIF/WEBP/ICO headers with an explicit "NO NEW DEPENDENCY"
  comment rather than reach for Pillow just to read image dimensions;
  upload validation in several places uses a magic-bytes check, "not
  Pillow," by name, more than once. Pillow itself was never a real
  dependency to begin with — confirmed absent from `requirements.txt` and
  from every `import`, listed in `/admin/open-source`'s showcase (and
  `webapp/checks.py`'s `OSS_EXTRAS`) purely as a one-off historical build
  tool that drew the two favicon files, once, years before this feature.
  Building a generator would also mean committing font files for the first
  time ever (Outfit/DM Sans/Caveat/Permanent Marker are all Google-Fonts-
  CDN-only sitewide — a PNG renderer can't reach a CDN font) — a real,
  separate cost with no other beneficiary. Against all of that: original
  pieces publish at roughly one a month. A generator's fixed build-and-
  maintain cost never pays back at that cadence — a ten-minute pass in a
  design tool wins on cost every time. No font files were committed; that
  decision died with Phase 2. If a future session is tempted to rebuild
  this: re-check both premises (is Pillow now a real dependency for some
  other reason? has the publishing cadence materially increased?) before
  assuming either has changed.
- **Social share cards, review-round corrections (2026-09, same day) — two
  real gaps found by explicit review questions, both fixed before merge.**
  (1) **Escaping — evaluated and replaced.** The original two-helper design
  (`_esc_attr_quote_only()` for `original_content.teaser`'s pre-encoded
  convention, plain `_esc()` for `ai_surfaces.teaser`'s/`homepage_teaser`'s
  plain-text convention) was replaced with one shared rule,
  `_esc_attr_normalize()` — `_esc(html.unescape(s))` — per an explicit
  request to check that single-rule alternative before shipping two
  per-field treatments. Tested against real production data and
  constructed edge cases (a pre-encoded `&amp;`, a plain `&`, a literal
  `"`, a pathological already-double-encoded string) before trusting it: it
  produces correct output for both storage conventions, and is genuinely
  safer than the two-helper version it replaced, not just simpler — the
  retired `_esc_attr_quote_only()` trusted a pre-encoded field's `&`
  completely, so a single un-pre-encoded ampersand slipping into
  `original_content.teaser` (a plausible admin typo, not a contrived case)
  would have shipped as a literal, unescaped `&` in the rendered
  attribute — invalid markup. Decode-then-re-encode can't have that
  failure mode: every `&` in the output is a real, correctly-escaped
  entity exactly once, regardless of how the source string was typed —
  confirmed with a real inconsistent-input test case
  (`"Ben & Jerry's &amp; Associates"`) that fails against the old helper
  and passes against the new one. All three `og_description` call sites
  (homepage, `ai_surface_article`, `original_content_article`) now go
  through this one function.
  (2) **og:url threading — a real, shipped gap, found by asking for the
  full list instead of accepting a spot check.** The original build
  threaded `request=request` through only 5 hand-picked "the pages that
  matter" routes (per Phase 0's own stated recommendation to avoid
  touching all 181 `_page()` call sites) — but that recommendation was
  read too narrowly: it left 44 other genuinely public (non-`/admin`)
  `_page()` calls across 39 routes silently falling back to the bare
  `PUBLIC_BASE` root for `og:url`, **including every individual tool and
  community profile page** (`/tools/software/{slug}`,
  `/tools/communities/{slug}`), the three directory pages, both compare
  pages, `/contact`, `/current-feed`, `/tools/fpa-buddy`, and more — any
  two of these pages would have reported an identical `og:url`,
  indistinguishable from the homepage to a scraper. Fixed by threading
  `request=request` through all 44 (every route with `request` in its own
  signature, minus `/admin/*`) — confirmed live afterward that an
  individual tool profile page now reports its own real URL, not the
  homepage's. **Made permanent, not just fixed once**: `og_url_
  threading_problems()` (`webapp/app.py`) is a mechanical drift detector
  in the same spirit as `hub_nav_orphans()` — live `app.routes`
  introspection plus `inspect.getsource()` per route (the same established
  technique `_page_index_tier_for` already uses, not a hand-rolled
  whole-file line scan, which the function's own first draft tried and hit
  two real false-positive classes with: matching `_page(` as a substring
  of a route-function name ending in "`_page`" like `login_page(`, and
  matching a `#` comment that merely *mentions* `_page()`; both are now
  guarded against and unit-tested directly). Wired into
  `webapp.checks.run_all()` as "og:url threading," the same way
  `hub_nav_orphans()` is wired in as "Hub-nav orphans" — confirmed it
  actually catches a planted regression (a temporarily-reverted
  `/tools` route) before trusting it clean, per this codebase's own
  standing "prove the detector isn't trivially passing" discipline.

- **Slow first page load (raised 2026-09-15, resolved 2026-09-17): confirmed as
  `webapp.checks.run_all()`, reached via `webapp.tasks._failing_checks_count()`
  from `_page()` on every admin-role render. Its result caches for 120
  seconds, so only the first render after the cache expires pays the cost; a
  warm load inside the TTL is 1-2s and tells you nothing.**

  Measured on the identical URL with 3+ minutes idle before each load:
  signed in 7-8s, signed out 2s. Auth state was the only variable.

  Ruled out along the way: Railway Serverless / container sleep — the toggle is
  OFF, confirmed in the dashboard 2026-09-17. Worth remembering that
  first-slow/second-fast was the first hypothesis for this pattern and did not
  survive the dashboard check; on this service that measurement alone does not
  prove cold start.

  The mechanism: `run_all()` renders every public route through `TestClient` so
  `coral_moment_problems()` can count coral backgrounds, and it now carries ten
  checks. #573 halved the cost on the re-entrant path via the
  `_checks_computing` sentinel but never touched per-pass cost. The existing
  parked item's own trigger, "if suite runtime or admin page loads become a
  problem," has fired.

  Not yet scoped: what to do about it. Every admin page load Brian makes after
  a two-minute gap pays ~6 seconds. Options not yet evaluated — a longer TTL, a
  background refresh, making the route-rendering check opt-in rather than part
  of the badge path, or precomputing at deploy.

  **Resolved (2026-09, test-suite-runtime PR) — the trigger fired again, this
  time as suite runtime rather than a page load: `run_all()` was found to
  cost ~110 uncached calls across just 14 test files, ~31.5 minutes (45.7%)
  of a ~69-minute single-threaded suite.** Real, itemized per-check timing
  (not the estimate this parked item shipped with — that guess turned out
  wrong in an important way, see below):

  | check | cost | cacheable (pure function of on-disk source)? |
  |---|---|---|
  | `table_override_problems` (+ `table_standard_problems`) | **6.3s** | yes |
  | `_pyflakes_problems` | 2.2s | yes |
  | `coral_moment_problems` | 1.9s | no — renders live pages |
  | Typography (`VOICE_SCANNED_FILES`) | 1.8s | yes |
  | `_resolve_checked_voice_core` | 1.1s | no — reads the live `voice_core` DB setting |
  | `script_syntax_problems` | 0.7s | yes |
  | Voice standards (`VOICE_SCANNED_FILES`) | 0.4s | yes |
  | everything else (14 checks) | 0.7s combined | mixed, all individually trivial |
  | **total** | **~15.1-15.5s** | **~11.4s cacheable** |

  The single biggest line item was never itemized before this pass:
  `table_override_problems(src)` runs `_CSS_RULE_RE.finditer()` — a regex
  meant to find CSS `!important` overrides — over `_app_src()`, which is the
  **entire 2.17-million-character `webapp/app.py` source file**, not just its
  CSS. Timed in isolation: 6.25-6.41s, 6,137 matches, almost all of it spent
  in the regex scan itself (the downstream filtering that actually finds a
  real `!important` override is 0.0025s for all 6,137 matches). A Python
  source file this size, full of dict literals, f-strings, and embedded
  JS/HTML, gives a "text, then `{`, then text, then `}`" pattern an enormous
  number of places to match. This was genuinely missed before — an earlier
  read of the function assumed "pure regex over a 26KB CSS block = cheap"
  without ever timing it standalone; it's actually a regex over 2.17MB of
  Python source, and only `table_standard_problems` (the other half of the
  same check) touches the small CSS block.

  Five checks — table format, pyflakes, script syntax, and both
  `VOICE_SCANNED_FILES` scans — are pure functions of on-disk source code
  (confirmed per each function's own body/docstring: `_pyflakes_problems`
  walks a static `("linklib", "webapp", "scripts")` tuple via `rglob`;
  `script_syntax_problems` reads `_JS`-suffixed module attributes plus
  `_app_src()`; `typography_findings`/`mechanical_findings` take only the
  passed-in source string — `typography_findings`'s own docstring: *"source
  is Python source text... not rendered HTML and not database content"*).
  Source on disk can't change within a running process, so
  `webapp.tasks.cached_static_check()`/`reset_static_check_cache()` now
  cache all five — first call in a process pays the full ~15s, every call
  after that drops to **~2.7-2.85s**, measured, including across
  `importlib.reload(webapp.app)` + `importlib.reload(webapp.checks)`
  together (the exact pattern `tests/test_checks.py`'s own `env` fixture
  uses on every single test, which is why that one file alone was 27.1% of
  the suite's total runtime). Deliberately placed adjacent to the
  pre-existing `_checks_cache` in `webapp/tasks.py`, not inside
  `webapp/checks.py` itself (which gets reloaded, wiping an in-module
  cache) — with an explicit comment contrasting the two: `_checks_cache`
  holds DB/request-dependent state and must reset every test (the #573
  lesson); the new cache holds source-only state and must *never* reset
  per test, since doing so would defeat the entire win for the one file
  that needs it most. `coral_moment_problems` (live page renders) and
  `_resolve_checked_voice_core` (reads the live `voice_core` setting,
  which Brian edits at `/admin/voice`) stay uncached on purpose — caching
  either would mean an admin's own live edit silently stops showing up on
  `/admin/checks`, the exact "a check that finds something and doesn't
  surface it" failure this project treats as worse than no check at all.

  **One real gap this caching change surfaced, closed in the same PR**:
  `tests/test_checks.py::test_admin_checks_summary_banner_is_red_on_a_real_failure`
  monkeypatches `linklib.voice_review.mechanical_findings` directly to
  force a synthetic finding — the cache would otherwise silently keep
  serving a stale, clean, already-cached result over it. Searched the
  whole suite for every such monkeypatch of the five now-cached functions
  and found exactly this one; fixed with `reset_static_check_cache()`
  around the plant (forces the monkeypatch to take effect) and again in
  `finally` (stops the synthetic finding from leaking into later tests as
  a false positive) — see `tests/test_static_check_cache.py` for the same
  plant/reset/prove-fresh pattern covered generically. **Standing rule for
  any future test**: a test that monkeypatches anything reachable from one
  of the five cached checks — not just the five function names
  themselves, but a helper or file read one of them calls into — must call
  `reset_static_check_cache()` around the patch, or the cache can silently
  serve a stale result over it. A test asserting the *absence* of a
  finding won't fail either way, so this can't be caught by "the suite is
  green" alone.

  **A second, unrelated real bug found while wiring the cache through, not
  by this cache itself**: the "One table format" check's `detail` field
  was reading a stale `tf` local variable left over from the Typography
  check's own block immediately above it in `run_all()` — so it had always
  rendered Typography's findings instead of its own. Harmless in practice
  (both were empty on real source), but a real bug, fixed alongside the
  cache wiring (renamed to `tbf`).

  **Also fixed in the same PR**: the confirmed-real escaped daemon thread
  in `tests/test_task_badges.py::test_start_background_checks_refresher_force_true_still_starts`
  — it started a genuine, never-joined `while True: run_all(); sleep(120)`
  thread that outlived the test for the rest of the single-threaded suite,
  proven via a `threading.enumerate()`-based before/after script against
  the pre-fix code (real output, not reasoning: the thread was still alive
  well after the test's own cleanup ran). `_checks_refresher_loop` now
  takes a `threading.Event`, checked between iterations via
  `stop_event.wait(timeout=...)` in place of a bare `time.sleep()`; a new
  `stop_background_checks_refresher()` lets a test that deliberately starts
  the real thread (`force=True`) stop and join it deterministically.
  Production behavior is unchanged — nothing calls `.set()` anywhere in app
  code, only the one test that needs it. Worth remembering for next time:
  a first attempt at this fix used a 5-second default join timeout,
  reasoned rather than measured, and failed the same regression proof —
  the loop only checks the stop event *between* iterations, and a first
  `run_all()` pass takes ~15-16s, so `.set()` mid-computation has no
  observable effect until that pass finishes. Raised to 20s once measured
  against the real thread, not guessed at a second time.

  **A citation correction, not a repo finding**: an earlier round of this
  investigation was pointed at a specific figure ("~10.6s as of 2026-09-23,
  PR #596") that turned out to live in Brian's Claude project instructions,
  not in this repo — confirmed absent from every historical revision of
  this file (`CLAUDE.md`) after unshallowing the clone (124 -> 1,623
  commits) and grepping full history for both "10.6s" and "#596"; zero
  hits either way. Worth noting as its own observation, unrelated to the
  citation mistake: PR merge commits `#593`, `#594`, `#597`, `#599` all
  exist in this history, but `#595`/`#596`/`#598` leave no traceable merge
  commit — likely a mix of merge-commit and squash/rebase merges rather
  than anything having actually gone missing, since the functionality
  those PR numbers would correspond to is demonstrably live either way.
  Not investigated further; the git-log-only lesson here is that a
  PR-number citation in this project's own documentation can't always be
  verified from commit history alone.

- **`/admin/checks`' top summary, two-table rework (2026-09) — Brian's
  direct review of PR #593's preview replaced the single stacked list of 8
  rows with two side-by-side cards, "Site checks" (5 rows) and "AI
  providers" (3 rows), sharing one identical column layout.** Every row is
  a named-field dict — `{"check", "href", "status", "details"}` — rather
  than the prior `_checks_summary_row_html(label, href, status_text,
  dot_color, fix_links)`'s positional args, and `_ai_row_status` (the
  AI-provider rows' own status computation) returns that same
  `{"status", "details"}` shape instead of an unnamed `(status_text,
  dot_color)` tuple. `_SUMMARY_ROW_COLUMNS = ("check", "status",
  "details")` (`webapp/app.py`) is the single source of truth for both
  the column order and the `<thead>` header text — `_checks_summary_
  thead_html()` sentence-cases the tuple's own entries into header labels
  and `_checks_summary_cell_html()` builds every `<td>` from the same
  tuple, so a header can't drift from what its column actually renders:
  there's one place to edit for a column to change, not two. `href` is
  deliberately excluded from the column list — it's link metadata for the
  Check cell's own `<a>`, not a rendered column of its own. Status is a
  colored dot ONLY, each with a real `title`/`aria-label` word so color is
  never the only signal; Details is the status text only, left-aligned.
  The AI-providers rows' GitHub-source and vendor-page links came OUT of
  the summary entirely — Details there shows plain text only ("Never
  reviewed"); those two links stay exactly where they already were, in
  each provider's own `<h3>` detail section further down the page. Both
  tables share an identical `<colgroup>` (a fixed Check width, a fixed
  Status width, Details flexible) so the dot column lines up across the
  two cards regardless of either one's own row-count or text length, and
  the last row of each `<tbody>` has its `border-bottom` stripped by
  string substitution (no `:last-child` selector available inline) —
  fixing a real stray divider that used to sit just above the card's own
  bottom edge, most visible under Exa pricing. Below 760px the two cards
  stack (Site checks first, AI providers second) via a single
  `flex-direction:column` override in a page-scoped `<style>` block, DOM
  order alone doing the ordering. Database copy's and Review queue's own
  destination (`/admin/voice/review-queue`, not their local `/admin/
  checks` section) is unchanged from the prior rework — this pass's "no
  outbound links" rule is scoped to the three AI-provider rows' GitHub/
  vendor fix-links specifically, not to same-site destinations.
  **Two same-PR design corrections, from Brian's own review of a live
  screenshot rather than the initial spec:** (1) the Check column's
  `<th>` header text ("Check") is dropped to a blank visible label — every
  row already names a check ("Live checks," "Anthropic pricing," ...), so
  the header was redundant and was wrapping on some viewports; the `<th>`
  still carries `aria-label="Check"` so the column stays identified for
  assistive tech, and Status/Details keep their visible headers unchanged.
  (2) The dot colors moved off the semantic `var(--good)`/`var(--caution)`/
  `var(--alert)` tokens onto the sanctioned true-stoplight hex trio BRAND.md
  §6 carves out for exactly this case ("glanceable health indicators" —
  test results, uptime/sync status) — `--good` is navy, the site's own
  dominant color, so a passing check read as ordinary text rather than a
  status signal at a glance. Now `#15803D` green / `#CA8A04` amber /
  `#b91c1c` red (`_SUMMARY_STATUS_META`), the exact same trio the
  cookie-status panel already uses (`_COOKIE_STATE_STYLES`) — red reuses
  the existing destructive-action red rather than adding a second one;
  "unknown" stays `var(--muted)`, unaffected, since it's outside the
  stoplight (no signal either way, not a pass/warn/fail state). All three
  hexes were already registered in `linklib/brand_check.py`'s `AUX_COLORS`
  allowlist under that same exception, so no new palette entry was needed.
  See `tests/test_checks.py`'s two-table-summary section for the
  regression coverage (row membership per table, header-text-matches-
  column-tuple including the blank Check label's `aria-label`, left-aligned
  Details, non-empty `aria-label` on every dot, the sanctioned-stoplight-
  only dot-color check, zero outbound links anywhere in the summary,
  identical `<colgroup>`s, the stray-divider fix, the 760px stacking
  breakpoint).

  **Round 3 (same PR, same day) — the blank Check header from Round 2 was
  the actual cause of a real visual complaint, and the fix turned out to
  be structural, not cosmetic.** Brian flagged "the headers aren't
  aligning" against a live screenshot; several rounds of pixel-level
  verification (raw HTML byte inspection for hidden characters, a real
  headless-Chromium render with the actual site fonts loaded, a direct
  pixel scan of the divider line's y-coordinate across the full width of
  both cards) all came back clean — no wrap, no hidden character, no
  genuine CSS misalignment anywhere. The header row itself was pixel-
  perfect. What Brian was circling in a hand-annotated screenshot was
  real, just not a rendering bug: with Check's visible label blank, the
  STATUS/DETAILS header row started well to the right of "SITE CHECKS"/
  "AI PROVIDERS" above it (the Check column still needed its 150px for the
  data rows below, it just had nothing to show in the header cell), and
  that blank strip read as two things failing to line up even though every
  column was correctly positioned over its own data one row down. Brian's
  own fix proposal, modeled on `/tools/fpa-buddy`'s "Sources"/"Depth"
  labels (a plain eyebrow label sitting above a control group, no card
  around the two together): move "Site checks"/"AI providers" out of the
  table entirely, as a bare label above a bare table — and while doing
  that, restore Check's visible header, since it no longer has anywhere
  disconnected to sit. He separately flagged that the header style itself
  (small muted 11px all-caps, invented for this page) had drifted from the
  site's real admin-table convention — the soft `var(--accent-light)`
  header band with plain 13px sentence-case text every other admin table
  (Software, Communities, ...) already uses — and asked for that back too.
  All three landed together: `_checks_summary_table_html` no longer wraps
  a heading + table in a bordered/padded "card" (`.checks-summary-card` is
  gone); the heading is now a plain eyebrow label (`font-size:11.5px;
  font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:10px;` — matching `.ask-section-label`'s
  own values, the "Sources"/"Depth" precedent, not a new invented style)
  directly above the table; `_checks_summary_thead_html` dropped the
  aria-label-only trick and the muted/uppercase treatment for the real
  `background:var(--accent-light)` band with visible, sentence-case
  `<th>` text for all three columns (Check included); `_checks_summary_
  row_tr`'s last-row border-strip hack is gone too, since there's no card
  edge left for a trailing divider to look stray against — every row,
  header included, now matches ordinary admin-table styling with no
  special-casing. `.checks-summary-grid`'s gap widened from 16px to 32px
  (24px stacked on mobile) now that a card border isn't doing any of the
  visual separation between the two columns any more. Verified with a real
  local render (logged-in session, real fonts, both 1280px desktop and a
  390px mobile viewport) before shipping, not just reasoned about — see
  `tests/test_checks.py`'s `test_summary_tables_have_three_columns_with_
  headers_matching_the_named_row_fields` (rewritten for the visible Check
  header + `accent-light` band) and `test_summary_every_row_including_
  the_last_has_the_ordinary_divider` (replacing the retired stray-border
  test) for the regression coverage.

- **Admin edit-form width-tier fix + scroll hints on six more wide tables
  (2026-09) — `.page-form` had drifted onto three admin edit forms it was
  never meant for; fixed, plus the shared scroll-affordance mechanism
  extended to six tables that never got it.** Investigated first, per the
  standing gate: `_feed_form_page` (the shared add/edit page for
  `/admin/reader/feeds/new`/`{id}/edit`) and the inline Resources
  (`/admin/tools/resources/new`/`{id}/edit`) and Third-party content
  (`/admin/thought-leadership/third-party/new`/`{id}/edit`) forms were all
  still on `.page-form` (640px, centered whole-page) — the same tier this
  file's own PR-12 bullet already confirmed was a deliberate, considered
  exception for Resources at the time, but never revisited once every
  other admin edit form (Software, Communities, AI surfaces, Original
  content) had already converted to the `.page-standard` reference shape
  (`_ai_surface_form_page`/`_oc_form_page`: back-link + `<h1>` at the
  page's own left edge, `<form>` capped at `max-width:900px;margin:0
  auto`). All three converted to that exact shape — Resources' and
  Third-party's inline forms also gained a back-link, which neither had
  before (both other reference forms have one). The CSS comment on
  `.page-form` itself was corrected — it used to say "forms — contact,
  admin edit forms," which is exactly the wrong signal that let this drift
  happen; it now states plainly that `.page-form` is for genuinely public,
  single-purpose forms only, never an admin edit form, and points at the
  two reference functions. **Six admin tables joined the `_ADMIN_SCROLL_HINT_
  HTML`/`_ADMIN_SCROLL_HINT_JS`/`#cmp-scroll-wrap` mechanism `/admin/reader/
  feeds` already had (PR 32, PR 33)**: Users, Toolbox intros, Third-party
  content, Original content, and — on pages with more than one `<table>` —
  the PRIMARY wide table only, matching the exact precedent Software/
  Communities already set (their own secondary "Pending submissions" table
  is deliberately left plain `overflow-x:auto`, no hint, per this file's own
  "genuinely empty" reasoning elsewhere): Contact submissions' main
  submissions table (its "Deletion history" audit table stays plain), and
  Reader content backfill's "Needs manual review" table (its "Accepted as
  final" and "Recent attempts" tables stay plain — the most-actionable
  table on a multi-table page is the one that earns the hint, not every
  table on the page). No table `min-width` floor or column width changed —
  scoped strictly to the shell width tier and the scroll affordance, per
  the task's own instruction. See BRAND.md §5's width-tier table for the
  corrected `.page-form` row.

- **Software edit page — four admin fixes (2026-09).** Four independent
  live-use gaps on `/tools/software/{slug}/edit`, fixed in one PR.
  1. **Logo controls split into two rows** (`_logo_admin_section`, shared
     with the Community edit page) — URL input + "Fetch from URL" +
     "Pull from Logo.dev" (renamed from "Revert and re-fetch from
     Logo.dev") on row one, file chooser + "Upload" on row two, preview to
     the left of both. Every input on both rows carries `min-width:0` (a
     flex item's default `min-width:auto` resists shrinking below its
     intrinsic content — the same file-input-intrinsic-width lesson the
     Overhead Spend investigation documented for date inputs, applied here
     to the same failure class) so neither row can overlap or clip at a
     narrower viewport (verified live at 1280/960/390px).
  2. **"Generate app screenshot" no longer requires a prior Save**
     (`admin_tools_app_screenshot_recapture`/
     `admin_communities_app_screenshot_recapture`, both `_app_screenshot_
     admin_section` call sites). Root cause: the route read
     `app_screenshot_source_url` back from the DB, and the hidden
     recapture `<form>` it submits carried no fields at all — a URL just
     typed but never Saved was invisible to the request. Fixed
     client-side: `submitAppScreenshotRecapture` reads the visible input's
     live value, copies it into a new hidden field on the recapture form,
     and refuses an empty value with a visible inline error (never a
     disabled-button tooltip — invisible to a screen reader and
     unreachable on touch) before `confirmDiscardsUnsavedEdits`/
     `startGenAnim` run. The route itself now reads that posted field and
     persists it via `update_tool_app_screenshot_source`/
     `update_community_app_screenshot_source` in the same request,
     regardless of whether the capture succeeds — a failed capture never
     loses the typed URL. Still a synchronous full-page-reload form submit,
     same shape as the homepage "Generate" button — which has the
     identical "discards other unsaved edits" trade-off, mitigated only by
     the pre-existing `confirm()` warning either way; not redesigned here.
  3. **"Mark verified" appears the moment a Generate call returns a
     draft**, for Description/Short summary and Competitive
     differentiation — both are stateless AJAX calls
     (`generateDescription`/`generateDifferentiation`) that never touch
     the database, so the server-rendered badge/button (driven by
     `description_needs_verification`/
     `competitive_differentiation_needs_verification`) couldn't reflect an
     unsaved draft; only a full Save-then-reload round trip made it show.
     **Agent taxonomy already worked in one click** — its "Generate
     summary" is a real synchronous form submit
     (`research-refresh-form` → `/research/refresh` →
     `_run_tool_research`) that persists `agent_taxonomy_needs_
     verification` to the DB directly, before redirecting back to the
     edit page, so the claim that it also needed a Save round trip didn't
     hold; a regression test pins this as a control. Fixed for the two
     stateless fields with a new "Save and mark verified" action
     (`showSaveAndMarkVerified`/`saveAndMarkVerified`, injected right into
     the description/differentiation verify-widget host `<span>`s) that
     submits the real edit form with the field name added to a new hidden
     `confirm_verified_fields` input — `admin_tools_edit_submit` forces
     that field's `*_needs_verification` to 0 in the SAME request that
     saves its (freshly drafted) text and writes a real
     `narrative_review_log` row, so the save and the verification always
     happen together against whatever text is actually in the textarea
     that submit — never a stale, previously-saved value. A hand-edit
     right after Generate (Description only — the one field with an
     existing `onEdit` citation-guard listener) retracts the injected
     badge/button via `hideSaveAndMarkVerified`, since the field is no
     longer AI-drafted-this-session and an ordinary Save clears
     `needs_verification` on its own. The two-tier length guard
     (`_check_text_field_length`) is untouched — `confirm_verified_fields`
     only changes which flag value gets written, never which write method
     runs, so an over-max draft is still refused whole regardless of
     whether it's also being confirmed.
  4. **A new "Upload homepage screenshot…" control** (Software edit page
     only — Communities' own homepage-screenshot section is hand-rolled,
     not shared markup, so it's untouched), for a case like Payhawk's
     (a Generate-captured homepage screenshot that caught a cookie
     banner). Reuses the App screenshot slot's exact Cropper.js flow
     rather than building a second one: `_APP_SCREENSHOT_CROP_JS`
     generalized to a `slot` parameter (`'app'`/`'home'`) — `slot='app'`
     reproduces the app slot's own existing `app-screenshot-*` element ids
     verbatim (zero markup change for that slot), `slot='home'` gives the
     new homepage upload its own non-colliding `home-screenshot-*` ids for
     free, and both crop to the identical fixed size
     (`.tp-shot-frame`'s 4:3 ratio — the same frame both slots render
     inside on the public profile). New
     `POST /admin/tools/software/{tool_id}/screenshot/upload` calls
     `update_tool_screenshot_url` — the SAME method the hand-typed
     Screenshot URL field already uses — so an uploaded image reads
     "Manually set—no capture date," exactly like a pasted URL, never a
     stamped "Captured {date}" the way Recapture's own
     `set_tool_screenshot_capture` claims. `screenshot_is_product` is left
     untouched, same as the existing hand-pasted-URL path — it's a frozen,
     non-behavioral historical marker (see its own schema comment), not
     something an upload needs to set.

  See `tests/test_homepage_screenshot_upload.py`,
  `tests/test_save_and_mark_verified.py`, and the extended
  `tests/test_app_screenshot.py` for the regression coverage — every new
  test proven to fail against the pre-fix code before being trusted.

- **JS-rendered vendor pages, grounding fetch defect (2026-09) — the four
  AI vendor-research functions decided a page fetch "succeeded" via a bare
  `not bool(page.content.strip())`, so a client-rendered homepage that
  loaded fine but left the page shell nearly empty read as a real,
  substantive fetch, producing a low-confidence draft that looked like a
  genuine finding rather than a failed one.** Found via Lumera's real
  homepage — its actual content only ever loads client-side, so
  `extract.fetch_page()` returned a few dozen words of shell markup, non-
  empty enough to pass the old check but nowhere near a real page. Phase 1
  (investigation) and Phase 2a (`scripts/audit_thin_fetch_grounding.py`,
  PR #633 — a read-only diagnostic, no fix) confirmed the actual production
  blast radius: 7 records with a real, specific issue (1 confirmed
  thin-fetch defect — Paylocity; 1 confirmed instance of a separate
  `[1]`-pseudo-citation-marker bug, tracked as its own follow-up issue —
  Vena; 4 "stale flag" false positives the audit can't tell apart from an
  already-fixed record, see below) out of a much larger NULL backlog
  (115/118 tool rows with no `low_confidence` signal at all — a second
  follow-up issue tracks re-fetching and comparing that backlog).
  **Phase 2b is the fix**, applied identically at all four call sites
  (`generate_tool_description`, `generate_tool_agent_taxonomy`'s
  `_fetch_taxonomy_grounding`, `generate_community_profile`,
  `generate_community_listing`) via one new shared helper,
  `linklib.enrich._fetch_grounding_page(url, exa_enabled)`: the direct
  fetch is gated through the SAME `extract.assess_extraction_quality()`
  bar the Reader's own content backfill already holds a fetch to (a real
  60-word floor, plus paywall/bot-challenge detection — see the "Reader
  content-structure backfill" section above for where that gate came
  from) instead of a bare truthiness check. When the direct fetch loaded
  but failed that gate, or — 2026-09 fetch-error follow-up, below — it
  failed outright with a fetch_error that itself looks like active
  blocking (`extract.is_likely_bot_block_error`: HTTP 403/429/503, or a
  timeout), a single Exa fallback is tried, `linklib.medium_platform.fetch_content_by_url` — the
  existing Reader-backfill fetch tier, reused rather than a new Playwright
  path (a Phase 1 routing note originally pointed at Playwright; reversed
  once Exa's own rendering was confirmed to already solve the same
  problem for JS-heavy pages, with no new headless-browser dependency to
  maintain) — re-gated through the identical quality check. A successful
  Exa recovery still drafts, flagged `low_confidence=True` (a genuinely
  grounded second-choice route, not "no content at all"). **When BOTH
  tiers fail the gate, the function raises `enrich.GroundingUnavailable
  (reason, url)` and nothing is drafted or saved** — non-negotiable, per
  this site's own radical-transparency standard: a pending/unverified
  field's content still RENDERS to every visitor with a badge, never
  hidden, so a hedge drafted from zero real page content would be live,
  wrong, public copy about a real vendor, not a safely quarantined draft.
  The three AJAX Generate routes and `_run_tool_research` all catch this
  exception and surface an honest, specific coral error via a new shared
  `webapp.app._grounding_unavailable_error` helper, naming the real reason
  (fetched/fetched via Exa/loaded but unreadable/blocked/unreachable) and
  the URL — never a generic "couldn't complete the research pass." The old
  boolean "Source page fetch: Succeeded/Failed" status line
  (`_low_confidence_indicator_html`) is retired in favor of an honest,
  specific one — "Direct" / "Via Exa fallback" / "Not recorded" — since
  "Succeeded" was exactly the misleading word for a fetch that recovered
  content via a second-choice route, and "Failed" no longer describes what
  `low_confidence=1` means at all (that state is now unreachable — see the
  refusal above). Exa's cost is recorded as a second `enrichment_cost` row
  (`model="exa-fetch"`), kept separate from the Claude call's own
  per-token rate rather than misattributed to it. `exa_enabled` (the site's
  existing Exa kill switch, `/admin/system/ai`) is honored throughout —
  threaded as a plain `bool` parameter through every affected function and
  its caller, matching `voice_core`'s own established "no `Library` handle
  inside `enrich.py`" convention exactly; when off (or `EXA_API_KEY` is
  unset), the fallback tier is a safe no-op and a thin direct fetch goes
  straight to refusal. **`linklib.feature_scan.research_vendor_domain`
  (the canonical Feature Taxonomy vendor-research scan) was investigated
  and confirmed to need no fix** — it was already Exa-`/search`-based from
  the start and handles a JS-rendered page correctly; it's a structurally
  separate mechanism from the four `enrich.py` functions this defect lived
  in, not a fifth call site sharing the same bug.

  **The "stale flag" problem — investigated against real production data,
  not decided in the abstract.** Once a record like Lumera is fixed by
  hand (Brian re-generating its draft, or editing it directly), the same
  audit script would re-flag it forever, since nothing about
  `needs_verification`/`low_confidence`/`ai_confident` changes just because
  the underlying page later became fetchable. Two candidate fixes were
  proposed up front — (a) update `low_confidence` on a later successful
  regeneration/hand-edit, or (b) exclude from the audit any field whose
  last save postdates its last generation. **Both were tested against real
  Lumera (fixed) vs. Paylocity (confirmed still broken) tool rows via the
  site's own read-only `/mcp` introspection tools, and both failed**:
  `needs_verification`, `low_confidence`, and `ai_confident` are all
  identical (`0`/`0`/`0`) between the two records — neither candidate signal
  can tell "already fixed" apart from "still broken," so building either
  one would have produced a mechanism that looks like it works but doesn't
  actually resolve the false positive it was built for. Built instead: a
  new `thin_fetch_audit_dismissals` table (entity_type/entity_id/
  field_name/dismissed_at/note, composite PK), the same explicit
  human-confirms-and-persists pattern `voice_review_queue`/
  `voice_approved_terms` already establish for "a flagged finding is
  reviewed and marked fine" — no attempt to auto-derive "already fixed"
  from data that provably can't support that inference.
  `scripts/audit_thin_fetch_grounding.py` gained `--dismiss`/`--undismiss`/
  `--list-dismissals` CLI modes (mirroring `Library.
  dismiss_thin_fetch_audit_finding`/`undismiss_thin_fetch_audit_finding`/
  `list_thin_fetch_audit_dismissals`), and the default audit excludes any
  currently-dismissed (entity, field) pair from its report. **No bulk
  regeneration** — only 7 records are actually affected by real issues
  (not dozens), and Brian hand-fixes each one himself via the existing
  admin edit page, the same review path every other AI-drafted field
  already goes through; this fix's job is stopping the defect from
  recurring, not correcting the historical 7.

  **Fetch-error follow-up (2026-09, same day) — Exa never fired on a
  fetch error at all, which was the wrong line to draw.** Phase 2b's own
  `_fetch_grounding_page` tried Exa only when the direct request LOADED
  but failed the quality gate; a request that failed outright
  (`page.fetch_error` set) always refused immediately, no Exa attempt.
  That left the actual case `linklib.medium_platform`'s whole Exa tier was
  built for unreachable: a Cloudflare-style WAF commonly returns a
  non-2xx status (raising before `assess_extraction_quality` is ever
  reached), not a 200-with-thin-shell — so a WAF-blocked vendor page
  refused to draft at all, the one failure shape the fallback exists to
  recover from. Fixed by classifying the `fetch_error` string itself
  (`linklib.extract.is_likely_bot_block_error`) rather than treating every
  fetch error the same: HTTP 403 (Forbidden — the standard explicit
  anti-bot signature), 429 (Too Many Requests — rate-limiting/anti-bot),
  503 (Service Unavailable — Cloudflare's own default status for its
  browser-check challenge), and a timeout (something DID respond, just
  not cleanly — closer in kind to a slow bot-check handshake than to a
  dead URL) now get the Exa attempt; a bare 404, a DNS failure, a refused
  connection, an SSL error, or any other 5xx still refuse immediately, on
  the reasoning that these are high-confidence "the URL is wrong or
  nothing is there" signals where a second crawler has no real chance of
  finding anything the first one didn't — spending an Exa call there is
  waste, not a second chance. A refused connection specifically has
  already had its one legitimate second chance by the time this check
  runs: `extract.fetch_page`'s own bare-domain `www.` retry (2026-08,
  see the Key architecture decisions bullet above) already re-attempts a
  `ConnectionError` once before giving up, so a `fetch_error` shaped like
  "connection error: ..." reaching `_fetch_grounding_page` has already
  failed twice, not once. The two Exa-attempt paths (thin-content,
  block-shaped fetch-error) now share one `_try_exa_grounding_fallback`
  tail rather than duplicating the attempt-and-classify logic. The
  refusal banner (`_grounding_unavailable_error`) already distinguished
  "loaded but unreadable" (`reason` in `paywall`/`bot-challenge`/
  `too-thin`) from "never loaded at all" (`reason` is the raw
  `page.fetch_error` string, e.g. `"HTTP 403"`/`"timeout"`) — no separate
  change was needed there; the two vocabularies were already distinct.
  See `tests/test_fetch_grounding_error_fallback.py` — the pure
  classifier's boundary cases, `_fetch_grounding_page`'s branching with
  explicit call-tracking proving Exa is genuinely never invoked on a
  404/DNS/connection-error/other-5xx (not just that the end result
  happens to match what "never called" would look like), and one
  end-to-end test per direction through `generate_tool_description`.

  **`fetch_content_by_url`'s current home in `linklib/medium_platform.py`
  will read oddly now that a non-Medium caller (Tool/Community research)
  uses it** — flagged as a known naming/organization debt, not fixed here
  per explicit instruction: renaming or moving it is deferred to its own
  follow-up issue rather than folded into this fix, since a rename touches
  every existing Medium-platform caller too and isn't itself part of the
  grounding-defect fix.

  See `tests/test_description_citations.py`, `tests/
  test_agent_taxonomy_enrichment.py`, `tests/test_community_profile_citations.py`,
  and `tests/test_community_listing_autofill.py` for the regression
  coverage — each of the four call sites has both a "loaded-but-thin,
  recovered via Exa" test and a "both direct fetch and Exa fail, raises
  `GroundingUnavailable`" test, with the direct fetch and the Exa call both
  mocked (never touching the network), each one confirmed to fail against
  the pre-fix `enrich.py` before being trusted.

- **Save no longer wipes citations Generate just wrote (#634, 2026-09).**
  Vena and Paylocity showed `[1]`/`[2]` markers with no Sources list: both
  `entity_citations` rows were `'[]'` with `model=""` (the signature of
  `clear_entity_citations`, milliseconds apart). Cause: every admin edit
  save cleared citations unless fresh ones rode along in the hidden
  `ai-drafted-citations` field, which is empty on every page load. Agent
  taxonomy was the loudest case, since its Generate persists in a separate
  request and the next Save cleared it unconditionally. One rule now covers
  Agent taxonomy (`Library.update_tool_agent_taxonomy`, compare inside the
  same transaction as the UPDATE), tool Description and the community
  profile (route-level): fresh validated citations write; else clear only
  if the text changed; else leave the rows alone. "Changed" is
  `voice_mechanics.norm_for_compare` (None = "", CRLF = LF, whitespace-only
  edits ignored, spaced-em-dash normalization applied to both sides). Missing
  row and `'[]'` row both read as "no sources" via `get_entity_citations`;
  the changed-no-fresh branch keeps the unconditional clear. The community
  compare is derived from `_COMMUNITY_PROFILE_FIELD_IDS`, never a hardcoded
  list. `scripts/regen_ai_drafted_fields.py` is untouched on purpose: it
  always rewrites the text, so its set-or-clear is already correct. No data
  repair shipped; existing `'[]'` rows stay until those fields are
  regenerated.

- **Community and software edit page polish, PR 2a.1 (2026-09).** Standing rule
  from Brian: a name in the edit view must never differ from the visitor-facing
  name. Exempt only admin-only controls with no public rendering (Verification
  status, Profile draft, Save buttons, Logo and Screenshots controls, "Community
  details"). The words live in `linklib/compare.py` constants (Program details,
  Reach, Cost band, Sponsorship, Access, CPE eligible, Format, Featured, Formal
  advisor) read by the edit page, public Details card, Compare band and MCP
  key facts; `tests/test_edit_polish_2a1.py` is the drift test. The review pill
  now reads "Under review" (was "Needs review") to match the visitor label; this
  shared component also changed the Software page and admin lists.
  CPE eligible has four states in this order: Not assessed, Yes, No, Unclear.
  Not assessed is the default and what an empty stored value reads as on load;
  the next save writes "Not assessed" (no migration, nothing changes until a
  profile is saved). It stays one stored string, `Word (note)`, with a note
  target 40 and max 60 and no note on Not assessed (no column, no split
  script). Generation (the Generate button and the two scripts) decides CPE
  from the single page it already fetches (direct crawl, Exa `/contents` as
  the fallback; there is no Exa search step in profile generation) and can
  only write Yes, No or Unclear (`generated_cpe`), never Not assessed; a
  failed grounding raises and leaves the stored value untouched. The two bulk
  scripts write CPE only while the stored value is empty or Not assessed
  (`cpe_for_generation_run`) and otherwise keep it and say so; "Generate full
  profile" persists nothing, it only fills the boxes for review, and fills the CPE control only while it reads Not assessed. A POST that omits `cpe_eligible` keeps the stored answer. `upsert_community_profile` is a full replace, so every caller must pass `cpe_eligible`. Profile prose limits are 600/800 (Bottom
  line 250/400, Resources included and Jobs program 300/600); 23 profiles over
  800 are a hand-trim to-do, listed on `/admin/checks` as a warning, not a merge
  gate. An over-max save re-renders the page from the submitted values (status
  400, nothing written). `scripts/regen_ai_drafted_fields.py` and
  `scripts/enrich_community_profiles.py` skip and report a community whose draft
  is over a limit instead of aborting. Note: the voice review queue Edit and the
  bulk ampersand replace write through `apply_voice_review_write`, which bypasses
  these limits and can lengthen text past a max.

- **Community profile edit page: collapsible field groups (2026-10).** The five
  groups are native `<details>`/`<summary>` (`.cp-group`), all collapsed on every
  load, including a profile with a stored over-limit field. The one exception is
  a refused save (`open_over_max`, passed only when `refusal` is set), which opens
  just the groups holding an over-max field. The summary row is the group header:
  caret (`.disclosure-caret`), title (the public name from
  `compare.community_admin_groups()`), an over-limit flag (amber "N over target",
  red "N over limit", kept live by `_CHAR_BUDGET_JS` through the opt-in
  `data-char-group` hook; offending fields get `.cp-field-over`), then an empty
  `.cp-group-actions` slot reserved for the per-group Generate button queued as
  2b (it can sit in the slot with no layout change; a button inside a summary does
  not toggle the group). A required field inside a closed group opens it and takes
  focus (`_CP_GROUPS_JS`, an `invalid` listener on the form's capture phase),
  since the browser otherwise blocks the save with no visible cause. The
  "Over limit" buttons now show `cursor:not-allowed` (`.btn`, declared later,
  used to win; the shared rule now carries `!important`, which also fixes the
  other budgeted pages) and an opt-in visible reason sits beside them
  (`data-char-reason-for`). CPE eligible: fixed-width dropdown
  (`_CPE_SELECT_WIDTH_PX`), the note takes the rest (`_CPE_NOTE_MIN_PX`), and the
  two stack when the cell is too narrow. Placeholder shortened to "Note, e.g.
  NASBA sponsor" so it fits beside the dropdown in the 1280px cell. Saving,
  limits, refusal and status buttons are unchanged. Software vendor parity is 2a.2.
  See `tests/test_cp_collapsible_groups.py`.

- **PR 2a.1 follow-up (2026-10).** (1) Both public directories read one
  `_DIRECTORY_PAGE_SIZE` (12); Communities had its own literal of 10, so page 1
  showed 10 cards and left a ragged last row. (2) `/admin/checks` "Profile fields
  over their limit" now covers software vendors (Description, Short summary,
  Agent taxonomy, Bottom line, limits read from the `Library.TOOL_*_MAX`
  constants the edit form uses) as well as communities, adds a Type column,
  sorts over-the-limit items (blocking) before over-target, and says plainly
  that over-limit text stays visible but can't be saved until trimmed. The
  summary tables' Check column is 260px (150px under the 760px stacking
  breakpoint, where a wider column would leave Details almost no room).
  (3) **Compare tables put each field name once in a first column**
  (`_CMP_LABEL_COL_WIDTH`, 176px; 116px and sticky under 700px) with only
  values in each entity's column (`_cmp_row_html`). Bottom line is the first
  body row on both pages, seafoam like the profile callout, with no rules above
  or below. Communities keep navy bands only on Program details and the five
  themed groups (one row per field under each); software has one field per
  section, so it has labelled rows and no bands. Similar communities and
  Competitors are labelled rows. The two-tier empty handling (group
  placeholder vs "No details available.") is unchanged. Real bug found on the
  way: `.site-main .table-frame>table{overflow:visible!important}` lost on
  specificity to the generic table rule's `overflow:hidden!important`, so
  sticky cells never moved on Compare; `table.cc-table.cc-table` now wins.
  The Compare intro says "under review", matching the visitor label.

- **Layout hardening batch (2026-10).** FP&A Buddy report's Asker column moved off
  `_COL_WIDTH_NAME` (it left Question at 164px at 900px; now 284px). Six admin grids
  (`.tool-form-cols`, `.qe-row`, `.users-top-grid`, Users add-member, Resources
  Coverage/Pricing, Third-party Source/venue) use `minmax(0,...)` tracks; the claim that
  they blow out was latent in Chromium (text, select and date inputs are compressible) and
  only provable by injecting a wide cell, which `tests/test_grid_track_zero_minimum.py`
  does. Resources sparse-count grids were already correct (`auto-fill`); only tests were
  added. **`.site-main .table-frame>table` never applied** (specificity below the generic
  table rule), so every framed table clipped and double-bordered, and the sticky Name
  column on `/admin/tools/software` and `/admin/tools/communities` did not stick; fixed
  by repeating the `:not()` chain. Compare's own `.cc-table.cc-table` patch is untouched
  and now redundant. New `/admin/checks` row "Reading column holds its tables and grids"
  (`brand_check.reading_column_problems`, source scan, cached): verified to flag the grid
  and table on the pre-fix `/tools/fpa-buddy/how-it-works`. See BRAND.md section 8 and
  ARCHITECTURE.md's "Layout hardening batch".

## Authentication & security

The site is one app with a **public face** and a **private back office**. Auth is a
single shared secret with a session-cookie login on top — no user accounts, no DB
tables, no third-party dependency.

- **One secret, two front doors.** `LINKLIB_PASSWORD` is the login password; if unset it
  **falls back to `LINKLIB_SAVE_TOKEN`**, so by default the same string unlocks both the
  login screen and the token API. If *neither* is set, the private routes are open
  (local-dev convenience).
- **Login = signed session cookie.** `POST /login` checks the password and sets an
  HMAC-signed, HttpOnly, SameSite=Lax cookie (`cfo_session`, 30-day TTL). Signing uses
  `LINKLIB_SECRET_KEY`, falling back to the password. Implemented with the stdlib
  (`hmac`/`hashlib`) — deliberately no `itsdangerous`/SessionMiddleware dependency.
  **If `LINKLIB_SECRET_KEY` is unset, an app restart invalidates all sessions** (you just
  log in again — harmless). Set it on the host to keep sessions sticky across deploys.
- **Route protection:**
  - Public (no auth): `/`, `/thought-leadership`, `/thought-leadership/growth-engine-calculator`
    (Original Content Phase 4c — the standalone interactive calculator; still a real, literal
    bespoke route, not part of the `original_content` system), `/contact`,
    `/privacy`, `/login`, `/logout`, `/static/*`, `/health`, `/tools/fpa-buddy/how-it-works`
    (moved off `/admin/*` in the FP&A Buddy explainer follow-up round — see the Key
    architecture decisions bullet above). (The old flat `/growth-engine-ratio`,
    `/finops-ai-hackathon`, `/netsuite-mcp` URLs still 301-redirect to the nested paths below —
    all three now served by the `{slug}` catch-all, none a literal route any more, see Original
    Content Phase 4a/4b/4c.)
    `/thought-leadership/{slug}` (Original Content Phase 2 — see Key architecture decisions
    above) is public **only for a `status='live'` piece with a real `body_md`**; it's not a
    blanket-public route nor a `/login`-redirecting one — a `status='draft'` row and an
    unknown slug both 404 for a signed-out visitor, and a draft 200s only for an active admin
    session, at its own canonical URL, per `_is_authed`. `/thought-leadership/netsuite-mcp`,
    `/thought-leadership/ai-hackathon-playbook`, and `/thought-leadership/growth-engine-ratio`
    are all now served this way — their hand-built Python route functions were retired in
    Phases 4a, 4b, and 4c respectively, so `_OC_RESERVED_SLUGS` is now empty (the standalone
    `/thought-leadership/growth-engine-calculator` route above is deliberately NOT added to
    it — it was never part of the `original_content` system, so there's no slug collision to
    guard against).
  - Private HTML pages → **redirect to `/login`** when signed out: `/tools/fpa-buddy`,
    `/admin/inbox/contact-submissions`, `/change-password` (all member-gated — see the Encourage
    password change note in Key architecture decisions above), and
    `/read`, `/read/{article_id}` (**admin-only**, Phase 1 access level, merged into
    the single Reader in Phase 5 — see the Library access-level note and the Phase 5
    Reader-merge note in Key architecture decisions above). (The old flat
    `/archive`, `/feed` URLs, and the pre-merge `/library/archive`/`/library/feed`
    routes they used to redirect to, are all gone outright as of Phase 5 — no
    compatibility redirect, nothing was bookmarked. `/library/ask` and
    `/library/past-questions` were retired outright in Phase 2 — see the
    Library/Toolbox Phase 2 note in Key architecture decisions above — along
    with the old flat `/questions` redirect stub that pointed at the latter;
    `GET /ask`'s redirect stub is gone the same way, but the bare `/ask` path
    now 405s rather than 404s since `POST /ask` still lives there.) The
    `/library` hub route was removed outright in Phase 1 — no redirect.
  - Private API → **401** when unauthenticated, but also accept a valid token (cookie OR
    `X-Save-Token`): `/ask`, `/post`, `/feed/save`,
    `/library/{article_id}/tags` (the Reader's inline tag editor, Phase 5c —
    the route predates it but had no callers until then). Of these, only
    `/api/search` (see below) also accepts a `?token=` query param as a fallback
    (`token: str | None = None` on the route itself) — the others check
    only the `X-Save-Token` header (2026-09 correction: this line previously
    claimed `?token=` worked for all of them, verified false in code for
    `/ask` specifically during the MCP cleanup/hardening PR's Phase 0).
  - **`/api/search` is now admin-only (`_require_api`), matching `/read`'s real
    access tier (2026-09 fix)** — previously gated at member-tier
    (`_require_member`, any signed-in user), a likely-unintentional survivor
    of the Phase 1 restructure that moved the Reader itself to admin-only
    without revisiting this API route (flagged but deliberately left alone by
    the MCP-server Phase 4 investigation — see ARCHITECTURE.md's matching
    note, now corrected). A signed-in non-admin member could search/read
    Library content, including articles behind Brian's own paid subscriptions
    (OnlyCFO, Mostly Metrics), directly through this route even though `/read`
    itself already blocked them — closed. Still accepts a valid admin cookie
    OR the save token (`X-Save-Token` header or `?token=` query param), same
    as before — only the cookie-tier requirement changed, from any member to
    admin specifically.
  - **Session-cookie-only** (401 when unauthenticated, no token fallback at all — these are
    reached only from inside the already-authenticated `/read` UI, never cross-origin):
    `/api/read-article` and `/read-later/refresh` (2026-08 follow-up — the per-item Read
    Later "Refresh" action deliberately doesn't accept `X-Save-Token`/`?token=`, unlike
    `/save-later`, since it's a button in the signed-in Reader, not the bookmarklet).
    `/change-password` (2026-09) is the same tier for the same reason — a self-service
    password change only ever makes sense from inside an already-authenticated session,
    never cross-origin, so it never needed a token fallback either.
  - `/save` is **token-only** (`X-Save-Token` header or `?token=`) because the bookmarklet
    calls it cross-origin, where the login cookie can't be sent. It also carries a
    dedicated, `/save`-only CORS middleware (`_save_cors` in `webapp/app.py`, 2026-08
    wrap-up sprint item 2) so that cross-origin call actually works — confirmed broken
    in production before this shipped (a real third-party origin got `TypeError: Failed
    to fetch`, the classic CORS-rejection signature); see ARCHITECTURE.md's
    "Three middlewares wrap everything" bullet for the full write-up. A permissive
    `Access-Control-Allow-Origin: *` is safe here specifically because `/save` already
    requires a valid token to do anything — same trust model as any bearer-token API,
    and it grants no cookie-authenticated access. No other route gets a CORS header.
  - `/save-later` is the same token-only, no-login mechanism as `/save` — same
    `_check_token`, same CORS treatment (the `_save_cors` middleware now matches
    either path) — but writes into the per-user `read_later` list instead of the
    shared Archive, via a second bookmarklet/Shortcut pair (`GET
    /read-later-bookmarklet`, admin-gated like `/bookmarklet`). Since a token-only
    request has no session, and Read Later is `user_id`-scoped, the write is
    attributed to `Library.default_admin_user_id()` (the earliest admin account) —
    see ARCHITECTURE.md's matching bullet for the full write-up.
  - **`/mcp` (Phase 1) is its own mechanism, separate from every route above —
    not session cookies, not `LINKLIB_SAVE_TOKEN`.** A `Authorization:
    Bearer <token>` header, verified against the `api_tokens` table
    (`Library.verify_api_token` — sha256 hash comparison, resolves to a
    real `users.id` + that user's CURRENT role). No cookie fallback, no
    `X-Save-Token`/`?token=` fallback — this is deliberate: the whole
    point of a dedicated token table is that an MCP caller is never
    anonymous or unmetered the way the legacy flat token is (see the Key
    architecture decisions bullet above). Two independent checks apply on
    every call — a transport-level 401 before any MCP protocol handling,
    and a per-tool re-verification that never trusts the first check — and
    the three introspection tools this phase ships additionally require
    `role == "admin"`, refused with a clean MCP-level tool error otherwise
    (never a crash, never a silent downgrade to a lesser view). Minting/
    revoking is `scripts/mint_api_token.py`, human-run via `railway ssh` —
    no admin UI yet. Full mount/auth mechanics are in ARCHITECTURE.md's
    "MCP server — `/mcp` (Phase 1)" flow section.
- **No secret in rendered HTML.** Internal links no longer carry `?token=`; the cookie
  authorizes navigation. Token comparison is constant-time (`hmac.compare_digest`).
- **⚠️ Bookmarklet caveat (by design).** The `/bookmarklet` snippet embeds
  `LINKLIB_SAVE_TOKEN` in plaintext JS — it must, because it runs on third-party pages
  cross-origin where the cookie is unavailable. The `/bookmarklet` *page* is login-gated
  so only Brian can retrieve it, **but the snippet itself is a secret.** Don't paste it
  publicly, and **if you rotate `LINKLIB_SAVE_TOKEN`, re-grab the bookmarklet** (the old
  one stops working) — the page itself always renders whatever `LINKLIB_SAVE_TOKEN` the
  running process currently has (a live module-level global, re-populated from the env
  var at each process start), so a stale copy means a stale COPY in someone's bookmarks
  bar, not a stale server. **2026-08 wrap-up sprint item 2:** the snippet shipped with an
  unbalanced closing brace (`body:JSON.stringify({url:u,tags:t})}}` closed the fetch
  options object twice before `.then` ever ran) — a syntax error, so every copy was a
  silent no-op that never threw anywhere visible; fixed, plus a `.catch` on the fetch
  chain so a network/CORS failure now alerts visibly instead of doing nothing.
  Same caveat applies to `/read-later-bookmarklet` — it embeds the same
  `LINKLIB_SAVE_TOKEN`, is login-gated the same way, and needs re-grabbing on the
  same token rotation.
- `/static/{filename}` resolves through `os.path.basename` to block path traversal.

## `library.db` is intentionally not in the repo

It's Brian's personal reading history. It lives beside the code
locally (on a Railway volume in production) and should never be committed —
`.gitignore` covers it (`library.db` + `*.db`). Off-site daily backups (bumped from
weekly, 2026-08 — Railway-native volume snapshots turned out unavailable on the
current plan, so this is the only recovery path) go to Google Drive when the
`GOOGLE_OAUTH_*` vars are set (see `.env.example`), with `linklib/backup.py`'s
`prune_old_backups()` keeping the most recent 14 daily snapshots plus one per week
for 8 further weeks, deleting the rest, so the folder doesn't grow without limit.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for enrichment, Q&A, and post drafting |
| `OPENAI_API_KEY` | — | Required for embed-on-save, `embed_backfill`, and the vector half of hybrid retrieval. Absent → FTS5-only, no error. |
| `EXA_API_KEY` | — | Exa search API key for FP&A Buddy's preferred web retrieval mechanism (`linklib/agent.py`'s `retrieve_exa`). Absent, or the `exa_enabled` setting toggled off at `/admin/system/ai` (merged there from the retired standalone `/admin/exa-settings`, PR 10) → Claude's native `web_search_20250305` tool handles the web tier instead (Phase 7 kill switch); web search itself is never disabled, only which engine runs. No error either way. |
| `LOGODEV_API_KEY` | — | Logo.dev image-endpoint token, required for `scripts/backfill_logos.py --apply` (CFO Toolbox logo backfill, Phase D) and for the admin edit page's "Pull from Logo.dev" live re-fetch action (2026-08 follow-up; renamed from "Revert & re-fetch from Logo.dev" in the software edit page's four-fix pass below) — both go through `linklib/logodev.py`. The active logo source since 2026-09, replacing Brandfetch (see the Key architecture decisions bullet above). Absent → the batch script errors out on `--apply`; the button still reverts a manual override to automatic but reports it couldn't re-fetch live. |
| `BRANDFETCH_API_KEY` | — | Brandfetch **Brand API** Bearer token. **Dormant since 2026-09** — Brandfetch's one-time 100-credit free tier is permanently exhausted, so `linklib/brandfetch.py` is no longer called by either the batch script or the admin re-fetch button; kept only so Brandfetch can be restored (swap the import back) if credits are ever renewed. A different product/credential from `BRANDFETCH_CLIENT_ID` below — do not confuse them. |
| `BRANDFETCH_CLIENT_ID` | — | Public client ID for Brandfetch's free CDN Logo API (`cdn.brandfetch.io`). Kept for reference/potential future browser-embed use, but **not** used by the logo backfill — that product is browser-embed-only and blocks programmatic access (see the Key architecture decisions bullet above). |
| `LINKLIB_EMBED_MODEL` | `text-embedding-3-small` | OpenAI embedding model for `linklib/embeddings.py` |
| `LINKLIB_DB` | `library.db` | Path to the SQLite database |
| `LINKLIB_SAVE_TOKEN` | (none) | Token for `POST /save` + bookmarklet; also the default login password. Set when hosted. |
| `LINKLIB_PASSWORD` | = `LINKLIB_SAVE_TOKEN` | Login password for the private section. Set to decouple the login password from the save token. |
| `LINKLIB_SECRET_KEY` | = password | HMAC key for signing session cookies. Set on the host so logins survive restarts/deploys. |
| `LINKLIB_ENRICH_MODEL` | `claude-opus-5` | Claude model for enrichment. This table entry previously read `claude-haiku-4-5-20251001`, which never matched the actual code default — the code has always defaulted to Opus for depth (see `linklib/enrich.py`'s module docstring). Separately, the code's own literal fallback was briefly changed to `claude-opus-4-8` on a mistaken belief that `claude-opus-5` wasn't a valid current model id — corrected back: `claude-opus-5` is real, current, and Anthropic's own top recommendation for complex/enterprise work (confirmed against Anthropic's docs, `platform.claude.com/docs/en/about-claude/models/overview` — also linked from `/admin/system/ai`), so it's the curated registry's "Best quality" entry (`linklib/models.py`), not `claude-opus-4-8`. As of the model-selection settings feature below, this env var is only the fallback used when no DB-stored selection exists — see "AI model selection" in Key architecture decisions. |
| `LINKLIB_CHAT_MODEL` | `claude-sonnet-4-6` | Claude model for Q&A and post drafting |
| `LINKLIB_PUBLIC_BASE` | `http://localhost:8000` | Base URL embedded in the bookmarklet |
| `LINKLIB_SITES_OPML` | `preferred_sites.opml` | OPML path — web-search allowlist AND `/read`'s Feed-view source list |
| `GOOGLE_OAUTH_CLIENT_ID` | — | Google Cloud OAuth client ID. Required (with the two below) for `linklib/backup.py`'s daily off-site Drive backup and `linklib/email_utils.py`'s outbound contact-form email — one client, both scopes. Absent → both features are a safe no-op, no error. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | — | Google Cloud OAuth client secret, paired with the above. |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | — | OAuth refresh token (`drive.file` + `gmail.send` scopes), paired with the above. Mint once with both scopes — see `.env.example` for the exact steps. |
| `GOOGLE_DRIVE_FOLDER_ID` | (self-managed) | Explicit override for the Drive folder id snapshots upload into. **Normally left unset** — the app creates its own folder ("CFO Navigator — Library Backups", in My Drive root) on the first successful backup and remembers its id in the `settings` table, since the `drive.file` OAuth scope can't see a folder made by hand in the Drive UI (Phase O — see the Key architecture decisions bullet above for the full 404 story). Only set this if a folder has been explicitly granted to the app some other way (e.g. a Drive Picker consent flow) and you want backups to target it instead. |

## One-off admin fixes against the database

Every `scripts/*.py` CLI now resolves its `--db` path via `linklib.db.resolve_db_path`:
explicit `--db` wins, then `LINKLIB_DB`, and with neither it exits loudly instead of
silently falling back to a relative `library.db` in whatever the current directory
happens to be. It also refuses to run against a path that doesn't already exist (sqlite3
otherwise creates an empty file there with no error) and prints the resolved absolute
path so it's visible in the run's output. This exists because a July 2026 admin fix
(Corpay's vendor category) was reported done but never showed up in production — the
most likely cause was exactly this silent-relative-path failure mode, with no error and
nothing to catch it after the fact. (A couple of scripts — `import_archive.py`,
`seed_tools.py`, `seed_communities.py` — are meant to run against a brand-new DB the
first time, so they pass `allow_missing=True` and skip the existence check.)

That guards the path. It doesn't guard the write itself. **For any single-record admin
fix — an `UPDATE`/edit against one specific row, not a bulk migration — immediately
`SELECT` the row back after the write and assert the change actually applied** (e.g.
`assert cursor.rowcount == 1` right after the `UPDATE`, then print the row you just
read back). This is standard practice for this class of change: cheap for a one-off, and
it's the exact check that would have caught the Corpay failure the moment it happened
instead of a later session discovering production was unchanged. This is guidance for
scripts you write for a specific fix, not something to build into `linklib` itself.

**Every one-off production data fix must leave a trace, whatever mechanism performs
it.** A committed script (per the above) is preferred, but going straight at production
via `railway ssh`/shell for a genuinely one-off correction is also fine — that's not the
problem. The problem is a fix that leaves **zero record anywhere**: not git, not a
script, not a chat note findable later. At minimum, say in the session what was
changed and why, so it's in that project's chat history — even a fix that feels too
small or too obvious to bother writing down.

**Lesson learned (2026-08):** a Lumos category investigation spanning several sessions
of code archaeology eventually concluded the most likely explanation was a correct,
deliberate manual production fix — probably several tools' categories corrected in one
`railway ssh` sitting — that happened to leave no trace anywhere: not in git, not in any
script, not in any chat transcript a keyword search could find, and not in an audit log
(the audit log didn't even exist yet at the time). The fix itself was almost certainly
fine. The entire cost was in not being able to tell that quickly — an untraceable
correct fix and a silent data-corruption bug look identical from the outside. Writing
down "changed X's category from A to B, here's why" at the time is nearly free;
reconstructing it after the fact from timestamps and git history is not.

**Archive a one-time script as soon as its run is confirmed.** After a one-time
data-fix script (`--apply` run, or any script whose whole job is a single
correction) is confirmed to have run successfully — verified via its own
write-then-read-back output, per the rule above — `git mv` it into
`scripts/archive/` in that same session, not as a later cleanup pass. If the
outcome is unambiguous (the printed before/after diff and read-back match what
was intended), archive it immediately. If there's real doubt about whether the
run actually did what was intended, ask first rather than archiving a script
that might need a second pass. This mirrors the Documentation section's rule 5
for a *recurring* script's registry entry once its job is done — this rule
covers the more common case of a single preview/apply/verify script finishing
its one job.

**No dead data.** Anything in the database with no live code path reading or writing
it gets deleted — a table, a column, a whole row set. "Live code path" means the
running app or an actively-run script actually touches it today, not "touched it once
during a since-finished migration" and not "might be read by some future phase." The
legacy `tool_features` table (retired outright in the Feature Taxonomy Phase 1b PR 2 —
see the Key architecture decisions bullet above) is the reference case: once every
route, admin section, and public rendering path that read or wrote it was removed in
one PR, the table itself became dead weight with nothing left to justify keeping it,
so a dedicated one-off script (`scripts/drop_legacy_tool_features.py`) drops it —
human-run, never wired into a boot hook or deploy step. **Soft-retired rows are NOT
dead data** — a `retired_at`-flagged `category_features` row, a `tool_audit_log`
snapshot of a deleted tool, `field_reviews`' frozen history after the fields it tracked
moved to a different mechanism (see that CLAUDE.md bullet) — these are all still read
by something (an admin page's history view, an audit trail, a "why does this exist"
investigation) or are the deliberate historical record the retirement itself was
designed to preserve; the rule is about code paths, not about whether a row is still
"active." **Destructive deletions are always human-executed, with a same-day backup and
a typed confirmation** — same standing discipline as the Article purge flow (CLAUDE.md's
Key architecture decisions bullet above) and this rule's own `tool_features` reference
case: preview/confirm, never silent, never automatic.

## Script-block syntax validation

All shared inline `<script>` blocks in `webapp/app.py` (every module-level constant
named `*_JS` — `_ADMIN_BULK_EDIT_JS`, `_GENERATE_DESC_JS`, `_SDR_JS`, and so on —
plus any `<script>{A}{B}</script>` that concatenates two of them into one tag) are
syntax-checked against Node (`webapp.checks.script_syntax_problems`,
`tests/test_admin_js_syntax.py`). Runs in CI on every PR (GitHub-hosted runners ship
Node by default) and live on `/admin/checks` wherever Node happens to be on `PATH` in
dev — skipped, not failed, when it isn't, since the production Docker image
(`python:3.11-slim`) has no reason to add Node just for this.

**Lesson learned (2026-08):** a fix validated a shared script block by regexing
`webapp/app.py`'s raw source text and reported success — but every `*_JS` constant is
a plain (non-f-string) Python string, so Python resolves its own escape sequences
(`\'`, `\\`, etc.) between the source on disk and the value actually embedded in the
page. Regexing the source checks a different string than the one the browser
receives; the "fix" flipped `\\'` to `\'` (correct-looking in the source, broken once
Python resolved it) and shipped a regression through the exact check meant to catch
one. **Always validate what the browser actually receives — the resolved Python
string or the live rendered response — never an approximation reconstructed from
source text.** The failure mode compounds the stakes: a syntax error anywhere in a
shared `<script>` tag aborts parsing of the *entire* tag, so no function in it gets
defined — not just the one nearest the typo — which is how one truncated apostrophe
silently took out search, select-all, and bulk edit/delete together, on two separate
admin pages, with no exception thrown anywhere a person would see it.

## Hub-nav orphan detector

`/admin`'s hub-nav cards (`_ADMIN_GROUPS`, `_LIBRARY_TOOLS`, `_FPA_BUDDY_TOOLS`,
`_SOFTWARE_TOOLS` in `webapp/app.py`) are hand-maintained tuples — the same failure
shape as every other hand-maintained list this project has had to guard against
mechanically. Found 2026-09: Communities' category CRUD
(`/admin/tools/communities/categories`) had a real, working route with no hub-nav
card at all, while its Software parallel (`/admin/tools/software/categories`) did —
caught by manual sampling during an admin-sprawl review, not by anything mechanical.

`webapp.app.hub_nav_orphans()` closes this the same way `/admin/system/page-index`
closes the width-tier drift problem: it reuses that same live `app.routes`
introspection technique (a sibling, not a parallel implementation) to enumerate
every real `/admin/*` GET/HTML route, flattens the hub-nav tuples into their own
href set, and diffs the two. Three route classes are excluded before diffing
because they're never expected to carry their own card — `/admin` itself, any
route with a path parameter (a per-record detail/edit page reached from its list
page's rows), and a `.../new` creation-form route whose own parent path is already
carded (a general, mechanical rule — a future `.../new` route needs no edit here) —
plus two small, individually-documented exceptions
(`/admin/overhead-spend/details`, `/admin/tools/software/name-duplicates`) mirroring
`_PAGE_INDEX_CUSTOM_EXCEPTIONS`'s own precedent. Wired into `webapp.checks.run_all()`
as "Hub-nav orphans" — a real automated pass/fail entry in `/admin/checks`' existing
list, not a fourth dated manual-attestation banner (unlike the Pricing/Exa-pricing/
New-model-awareness reminders above, this is mechanically computable with no
external source or human judgment call). See `tests/test_hub_nav_orphans.py` for
the coverage, including a reproduction of the exact 2026-09 gap proving the detector
actually catches it, not just passes trivially.

**PR 11 (2026-09) follow-up — Communities nests inside CFO Toolbox the same
way Software does, and System splits into two groups.** A new
`_COMMUNITIES_TOOLS` tuple (Communities, Community categories) pulls those
two cards out of `_TOOLBOX_TOOLS`'s flat item list into their own nested
"Community" sub-group, mirroring `_SOFTWARE_TOOLS`'s existing shape exactly
— same `_group_html(..., nested=True)` mechanism, same aggregate-badge
pattern via `badge_hrefs`. `_hub_nav_all_hrefs()` gained a line folding in
`_COMMUNITIES_TOOLS`'s hrefs, same as it already did for `_SOFTWARE_TOOLS`.
System (nine flat cards, no internal grouping) splits into two top-level
siblings — **Configuration** (Users, AI configuration, Open source) and
**Health and maintenance** (Checks, Overhead spend, Database, Page index,
Scripts, Archive backup) — spelled "and" rather than "&" so the new group
name passes `linklib.voice_review.typography_findings()`'s bare-ampersand
check without extending its allowlist. Two plain top-level groups, not a
nested sub-group: System never had a nesting mechanism, and building one
for a nine-card list would be more machinery than the job needs. Both new
groups render in the right column, after Brand/voice/content, same spot
System used to occupy. See `tests/test_hub_nav_orphans.py` for the
regression coverage (updated to patch `_COMMUNITIES_TOOLS` directly, since
the category-CRUD card it exercises moved out of `_TOOLBOX_TOOLS`).

## Checks-page follow-ups (2026-09)

- **The Database-backed copy count honors review-queue decisions.** A
  finding covered by a row-level "Allow once" exception or an "Always
  allow" term is not a violation. It isn't hidden either: `/admin/checks`
  states the decisions on their own line ("0 violations. 2 allowed once, 6
  always allowed."), and the summary row and the section body are built
  from the same sentence so they can't disagree. Removing the decision
  brings the finding back on the next pass. A red status nobody can clear
  by deciding trains the reader to ignore the page, so the two surfaces
  may differ in what they show but never contradict each other.
- **Every table on the site uses one format.** Light-blue header row,
  white rows, a line between rows, rounded 12px frame in `--table-border`
  (`--navy-light`, Brian's pick). Tables with subheading rows (Compare's
  section bands) use the secondary format: navy-light band, white text,
  white label column. It's one `!important` block in `_CSS` scoped to
  `main.site-main`, so don't style a new table inline; it inherits the
  format. Sticky tables put the frame on their wrapper (`.table-frame`).
  Excluded: the profile Competitors logo list and Reader article HTML.
  BRAND.md's table rule and `tests/test_table_format.py` are the reference.
  Guarded live by the "One table format" row on `/admin/checks`: a new
  exclusion goes in `brand_check.TABLE_SCOPE_EXCLUSIONS` (a design call for
  Brian), and a supporting `!important` table rule goes in
  `TABLE_OVERRIDE_ALLOWLIST` with its reason. Anything else fails.
- **`webapp/checks.py` is voice-linted** (joined `VOICE_SCANNED_FILES`):
  its check descriptions render on `/admin/checks`, so they follow the same
  rules as app.py copy. Keep them to one plain sentence.
- **Mechanical guard: production script examples use an absolute `--db`.**
  `webapp.checks.db_path_example_problems()`, a `/admin/checks` row and CI
  test (`tests/test_db_path_examples.py`). It scans the script registry,
  every `scripts/*.py` docstring, and CLAUDE.md/README.md/RUNBOOK.md. The
  allowlist (`DB_PATH_ALLOWLIST`) is explicit, one reason per entry; add to
  it only for a genuinely local-only example. So don't write a relative
  `--db` path in any of those files, prose included, unless it's allowlisted.
- **`/admin/checks` layout:** the CI-quota control is a switch near the
  top; Live checks loads collapsed, grouped by theme (`_LIVE_CHECK_THEMES`:
  voice and copy, brand and design, site structure and content, code and
  repo health), in a two-column grid (`_LIVE_CHECKS_COLUMNS = 2`, Brian's pick over
  three). Every card's status sits in the same
  top-right spot. A new `run_all()` check needs a theme line, or
  `tests/test_checks.py` fails. Below Live checks, Database copy, Disk
  space, Badge refresh and the three AI-provider reminders are one
  details block (`_checks_detail_row`): explanation in the left two
  thirds, result in the right third (dot, status word, the summary's own
  details text, and any action such as Mark reviewed), stacking on phones.
  The status vocabulary and details text come from the same row dicts the
  summary tables use, so the two can't disagree.
- **Voice review queue:** Detail shows findings and fixes in context,
  editing happens in the Detail cell on the full stored value with a
  stale-value check, every resolution records its outcome, and the
  history section loads collapsed with every row.

## Freshness-Banner & Reviewed-Toggle Consolidation

An admin-sprawl review (2026-09-08) found two duplication patterns on `/admin/checks`
and nearby admin pages — investigated as one question (are these the same underlying
mechanism, or two that happen to rhyme?) before building anything. **Answer: two
genuinely different patterns, each with its own real, byte-for-byte duplication worth
extracting on its own** — not one shared "toggle core" with banner presentation on
top. They differ on the two axes that actually drive rendering: cardinality (one
global value per page vs. one row among many) and reversibility/staleness (a
time-windowed amber/seafoam banner vs. a plain reviewed-or-not pill with no color
staleness at all). Forcing them into one component would mean a banner pretending to
be a row or vice versa — more parameters than duplication saved.

- **`_reviewed_freshness_banner(is_stale, message_html, mark_url)`** — the shared
  wrapper (colors, layout, the "Mark reviewed" button) `_pricing_freshness_banner`/
  `_models_freshness_banner`/`_exa_pricing_freshness_banner` now all delegate to. Each
  of the three stays its own function, since the real staleness predicate (imported
  from a different module per banner) and the message wording genuinely differ — only
  the mechanical wrapper was actually duplicated.
- **`_reviewed_toggle_html(is_reviewed, toggle_url, *, one_way=False, reviewed_at="",
  form_style="")`** — the shared badge+action pair for community-gaps and ask-feedback
  (two-way: a boolean that flips back and forth, rendered as a seafoam/alert pill) and,
  in `one_way=True` mode, compare-summary-feedback (a timestamp set once and never
  un-set — no pill, since there's nothing to toggle back to; plain muted "Reviewed
  {date}" text once set). Returns `(badge_html, action_html)` so each caller places the
  two pieces exactly where its own row layout already puts them.
- **`backfill-content`'s accept/unaccept pair is deliberately NOT folded in.** It only
  rhymes at the vocabulary level — underneath it's a `content_refetch_log` status enum
  (not a reviewed boolean/timestamp), rendered as a row that physically moves between
  two different table sections with a confirm dialog and a separate Undo action, not an
  inline pill flip. Forcing it through `_reviewed_toggle_html` would have been the
  wrong abstraction, so it stays its own thing.
- **The stale "two call sites, not a growing pattern" comment is corrected** (it was
  wrong the moment a third caller — compare-summary-feedback — existed, and would have
  gone stale again the moment a fourth did): the corrected version says "see all
  callers of `_reviewed_toggle_html`" rather than naming a count, specifically so it
  can't go stale the same way again.
- **A real, unrelated correctness bug found during this pass, fixed in the same PR**:
  `MODEL_PRICING` is Claude-only — OpenAI's embedding rate is tracked separately in
  `EMBEDDING_PRICING`, which has no freshness-reminder banner of its own — but the
  pricing-freshness banner's copy, its docstrings, and `PRICING_REVIEW_STALE_DAYS`'s
  own comment all said "Anthropic's (and OpenAI's) published rates," asserting coverage
  the table doesn't actually have. Every instance was corrected to name only Anthropic;
  `test_pricing_banner_no_longer_claims_openai_coverage` pins it.
- **`/admin/checks`' three near-duplicate "this is a manual, dated reminder, not
  automatable" paragraphs are tightened to one shared line** (stated once, above all
  three sections) plus a single one-line question per section naming only what that
  section actually checks — no more re-explaining the same "not automatable" framing
  three times with only the subject swapped.

See `tests/test_reviewed_helpers.py` for direct coverage of both shared helpers; every
pre-existing test for the three banners and the three toggle call sites
(`tests/test_pricing_freshness.py`, `tests/test_models_freshness.py`,
`tests/test_exa_pricing_freshness.py`, `tests/test_ask_feedback.py`,
`tests/test_community_profiles.py`, `tests/test_compare_summary.py`) passes
unmodified — proof the consolidation is behavior-identical.

## Running locally

```bash
pip install -r requirements.txt
pip install anthropic          # for Q&A, enrichment, post drafting
export ANTHROPIC_API_KEY=...

# Import Feedly archive (one time)
python -m scripts.import_archive --zip feedly-archive.zip --db library.db

# Backfill enrichment
python -m scripts.enrich_backfill --db library.db

# Backfill embeddings for semantic search (optional — needs OPENAI_API_KEY)
python -m scripts.embed_backfill --db library.db

# Web app
uvicorn webapp.app:app --reload    # http://localhost:8000

# MCP server (optional — lets Claude Desktop/Code search the library)
pip install mcp
python -m scripts.mcp_server
```

The commands above cover the core scripts every fresh checkout needs. For everything
else in `scripts/` — the recurring backfills, seeders, and diagnostics run on their own
cadence (`backfill_logos.py`, `capture_tool_screenshots.py`, `seed_tools.py`, and so on)
— see **`/admin/system/scripts`** (Phase N), the live, hand-maintained reference for
purpose, cadence, required env vars, and exact invocation. One-time migrations and
closed-investigation reports that already did their job live in `scripts/archive/`
instead, off that page — kept for git history, not meant to run again.

## What's built vs. what's next

**Built and working:**
- Feedly archive import (`archive.py`, `scripts/import_archive.py`)
- Going-forward capture: CLI (`add_link.py`) and web (`/save`)
- FTS5 search and web UI
- Enrichment backfill
- FP&A Q&A (hybrid library retrieval — FTS5 + vector search via `sqlite-vec`, RRF-merged
  — + web search); `scripts/embed_backfill.py` backfills the vector side for the
  existing corpus
- Bookmarklet
- Public site: bio homepage (`/`), thought leadership (`/thought-leadership`),
  Growth Engine Ratio article (`/thought-leadership/growth-engine-ratio`) and its standalone
  calculator (`/thought-leadership/growth-engine-calculator`, Original Content Phase 4c),
  contact (`/contact`)
- Password login for the private section (`/login` + signed session cookie)
- Merged Reader (`/read`, Phase 5): a three-pane Feed/Saved/Read Later view with category
  and per-source filtering, an AJAX-loaded article pane, save-to-library, and a
  standalone single-article view at `/read/{article_id}`
- Hosting/deployment on Railway (see Deployment below)
- bmweis.com custom domain pointed at Railway (July 2026)
- MCP server (`scripts/mcp_server.py`) wrapping `/api/search` for Claude Desktop/Code
- Remote MCP server (Phase 1, `/mcp`, mounted in-process) with user-bound API-token auth
  and three admin-gated schema-introspection tools (`list_tables`, `describe_table`,
  `sample_rows`) — see the Key architecture decisions bullet above
- Daily off-site Drive backup (bumped from weekly, 2026-08), scheduled via a
  Railway Cron Service (Phase O — originally a GitHub Action, migrated 2026-08;
  see Key architecture decisions above), with retention pruning
  (`linklib.backup.prune_old_backups`), a persistent `backup_log` audit trail, and a
  status banner + history table on `/admin/library-backup`

**Not yet built (from the migration plan):**
- iOS Share Sheet shortcut

Note: the migration plan document (`MIGRATION_AND_BUILD_PLAN.md`) was never
committed to the repo — this item is tracked here as the only record of it.

## Running the test suite — single-threaded is the verification standard, `-n auto` is for iteration only

**The single-threaded run (`python3 -m pytest -q`, never bare `pytest`) is the
number that goes in a PR body and the only one that gates a merge.**
`pytest-xdist`/`-n auto` is safe to use freely while iterating locally (it's
what CI itself runs — see `.github/workflows/qa.yml`'s own "CI cost
optimization round 2" comment, added 2026-08-22 with Brian's explicit
sign-off), but never as the reported number for a PR.

**Why single-threaded specifically, not "because it's slower and therefore
more careful":** this repo has an open, real hazard around module-level
globals leaking between tests within one process (see #573's
`_checks_cache`/`_checks_computing` race, and the escaped-daemon-thread bug
the 2026-09 test-suite-runtime PR found and fixed in
`test_start_background_checks_refresher_force_true_still_starts`). Which
tests share a process — and therefore which tests can leak state into which
others — is exactly what changes between a single-threaded run and an
`-n auto` run (xdist splits the suite across N worker *processes*, each
running a different subset of tests in a different order than plain
collection order). A suite that's green under `-n auto` and red
single-threaded, or the reverse, is a live possibility here, not a
theoretical one — it's already happened once (#573) and was found again
while investigating suite runtime itself. The single-threaded run is the
standard specifically because it's the one deterministic grouping: the same
tests, in the same order, in the same process, every time, so a pass/fail
result actually means the same thing from one run to the next.

**Setup-phase cost, measured (2026-09 test-suite-runtime investigation):**
across a full single-threaded run, the `setup` phase (pytest's per-test
fixture cost) totaled **997.9s / 24.1% of the suite** (n=2615, avg 0.382s) —
almost entirely the `importlib.reload(webapp.app)` + fresh temp-DB pattern
every `env` fixture uses. **This is deliberate, not creep**: zero test files
in this suite use a module- or session-scoped fixture (`grep -rn
"@pytest.fixture(scope=" tests/*.py` → 0 hits) — every test gets a
completely isolated app/DB, on purpose. Worth a look if suite runtime
becomes a problem again, but secondary to whatever's dominating the `call`
phase at the time (see the next section for how big that split can get).

**Suite-wide test isolation lives in `tests/conftest.py` (2026-10).** An autouse
fixture resets the module globals that survive a test's own
`importlib.reload(webapp.app)` (`webapp.tasks`' check cache and in-flight
sentinel, `linklib.feed`/`linklib.models` caches, `preferred_domains`'
`lru_cache`), never `_static_check_cache` (process-lifetime by design). A second
autouse fixture stubs `coral_moment_problems()` (re-applied after every reload,
since ~2s per call and it runs inside every `run_all()`); a test that needs the
real scan uses `@pytest.mark.real_coral` or the `no_password_env` fixture.
Measured on four admin-heavy files: 363s without the conftest, 249s with it.
A fixture that depends on open auth must clear `LINKLIB_PASSWORD`,
`LINKLIB_SAVE_TOKEN` and `LINKLIB_SECRET_KEY` itself rather than assume the
ambient environment. A test helper that calls `sync_playwright().start()` must
`stop()` it on every exit path, including a failed launch, or the leaked asyncio
loop breaks every later Playwright test in the process.

**Background a long run with the environment's native background mechanism,
never a manual `nohup ... & disown`.** A long-running verification command
(the full suite is the standing example, but this applies to any command
expected to outlive the current turn) has to survive across tool calls and
notification turns — the native mechanism (this harness's own
`run_in_background` parameter, or the equivalent in whatever environment is
running the command) is built and tracked for exactly that; `nohup`/`disown`
bypasses that tracking entirely. Found the hard way (2026-09, PR #604's
rebase-and-reverify cycle): three consecutive full-suite runs launched via
manual `nohup ... & disown` were silently reaped between check-ins — no
error, no traceback, just a process that stopped existing and a log that
stopped growing, indistinguishable from a genuinely slow run until an
explicit liveness check (`ps aux | grep ...`) came back empty. **A reaped
process and a slow one look identical from outside, with zero output
either way** — that's what makes this dangerous rather than merely
annoying: it can turn an unverified/incomplete run into an apparent
"verification" if the incompleteness isn't checked for and disclosed.
Switching to the native mechanism fixed it immediately, on the very next
attempt. Two standing rules follow from this: **use the native background
mechanism for anything that has to survive across turns**, and **confirm a
long-running background job's liveness rather than assuming it** — a stale
log timestamp plus an empty `ps` result means dead, not slow, and a PR body
or chat report should say so plainly rather than projecting an ETA from
partial progress. **A verification claim isn't communicable until it
carries a number and a SHA** — a chat line saying "running now, will report
when done," or a PR-body placeholder saying the same, reads to a human
skimming it as a completed result even when it explicitly isn't; state
"in progress, no result yet" plainly enough that it can't be mistaken for
one (PR #604's rebase-and-reverify cycle nearly got a merge approved on
exactly this misreading, even though the placeholder itself never claimed
completion).

**A performance claim measured in this sandbox needs a genuinely solo,
controlled run — an uncontrolled figure will materially overstate an
improvement, not just wobble around the true number.** #606's merged PR
body and this file both originally claimed a 26.5-minute saving (4143.49s
pre-#606 baseline down to a 2550.73s/0:42:30 "after" figure) from caching
five source-only checks. **That 42:30 figure does not hold under a
controlled measurement and should be treated as corrected, not merely
disputed.** Two genuinely solo runs against the identical post-#606 code
path, both confirmed uncontaminated from the tool-call sequence (nothing
else executing alongside either one for its full duration, not merely
assumed) — 3271.72s (0:54:31) and 3258.94s (0:54:18), 13 seconds apart —
land close together and both well above 42:30, about 11-12 minutes
(≈27-28%) slower than the claim. **What isn't known: whether #606's own
original 42:30 measurement was itself contaminated.** This PR's own
attempts did hit exactly that failure mode once (a `pyflakes`/`grep`
overlap that contaminated a since-discarded confirmation attempt, caught
and the run redone rather than reported) — real, useful evidence that this
class of contamination is easy to introduce by accident in this sandbox —
but that specific incident is not evidence about how #606's figure was
produced; its original conditions are unknown, not confirmed contaminated.
The caching itself is real and still worth having — 4143.49s → ~3265s
(averaging the two clean runs) is a genuine ~14.6-minute (≈21%) improvement
over the pre-#606 baseline — it's just materially smaller than what got
written down. A third, less-controlled figure from a different branch in
this same workstream (3645s/1:00:45) also sits closer to the clean
~54-minute range than to 42:30. Whether #606's own merged PR body should
carry a correcting comment is Brian's call, not something to post
unilaterally — flagged for him in the PR #604 chat.

## Testing standard for UI-facing changes

**Live-verify with a real headless-browser session (Playwright — pre-installed in
this environment; see the top-level agent instructions for the executable path),
not just code review or a `TestClient`-rendered HTML string, for any change that
touches interactive behavior** — a click/tap handler, a CSS layout switch, JS
state, anything a person actually clicks or taps. A rendered-HTML assertion
confirms the markup exists; it does not confirm the click handler actually fires,
that nothing else is capturing the tap, or that the updated content ends up
somewhere the user can see it. This standard was set from a real miss: the
Reader's mobile "clicking an article does nothing" bug (see the Reader
follow-up-pass bullet above) was never caught because verification up to that
point only ever ran at desktop widths — the click handler was fine the whole
time; the root cause (the reader pane updating far off-screen with nothing to
scroll to it) was a mobile-layout-only symptom a desktop-only browser session
could never have surfaced.

**Test at real mobile viewport widths, not just desktop, whenever a change
touches anything that has a mobile layout.** Use Playwright's device emulation
(a real viewport size, `has_touch`/`is_mobile`, and `.tap()` rather than only
`.click()`) so touch-event and CSS-breakpoint differences actually have a chance
to surface, the same way the desktop-only gap above let a real bug through
undetected. Cover both portrait and landscape where a layout has orientation-
specific behavior (see the Reader's `orientation:portrait` breakpoint) — a
width-only check can't tell a cramped phone-portrait viewport from a
phone-landscape one at a similar or even narrower width.

This applies to every UI-facing feature going forward, not just the Reader
batch that prompted it.

**Known limitation: `capture_homepage()` screenshot testing inside a sandboxed
Code session may fail with a Chromium-not-found error, even though production
is unaffected.** Cause: `requirements.txt` doesn't pin an exact Playwright
version. A sandbox's pre-baked Chromium browser cache (built once, when that
sandbox image was created) can drift out of sync with whatever Playwright
version pip resolves at session start — Playwright's own `.executable_path`
property is computed purely from the installed package's internal revision
registry, not from what's actually on disk, so it can report a path that
doesn't exist in a given sandbox. **Do not "fix" this by adding an explicit
`executable_path=` argument to the Chromium launch call in
`capture_homepage()`.** This was investigated during the 2026-08 QA
regression pass and deliberately not shipped — it doesn't reliably fix
sandbox testability (the mismatch can recur any time pip resolves a newer
Playwright version than a given sandbox's pre-baked cache), and it's a no-op
in production, where Railway's Dockerfile always installs the browser and
the package together at build time, so they can never drift apart there. If
this needs to actually be fixed for testability, the correct angle is
environment-level, not a code change to `capture_homepage()`: pin an exact
Playwright version in `requirements.txt` matching a known-good sandbox
browser cache, or have the session re-run `playwright install chromium`
fresh before testing.

**A passing pytest count can under-report what actually ran: duplicate test
names silently shadow each other.** Python rebinds the name, so only the last
definition in a file executes and the earlier one vanishes with no warning from
pytest. Six tests in #341 never ran for exactly this reason, having reused names
from an existing section of the same file; only `pyflakes` flagged it
("redefinition of unused..."), and the count went 39 -> 45 once renamed. pyflakes
is already part of the standard local CI substitute, so the discipline is simply
to read its redefinition warnings as real findings rather than lint noise, and to
sanity-check that a suite's test count moved the way an added file should have
moved it.

**Verify computed/rendered values, not a screenshot glance — CSS specificity
fails silently and desktop-first.** #341's mobile alignment fix was correct in
the stylesheet and still wrong in the browser, because an inline
`justify-content:center` on the same element outranked it; inline styles beat
any selector short of `!important`. It looked fine at desktop width, where the
intended and actual values happened to agree. Caught only by measuring the
element's real offsets at each viewport. When a layout fix targets a specific
breakpoint, assert the computed value (`getComputedStyle`, a bounding rect, a
`Range` line-box count) at that breakpoint; "the CSS says so" and "it looks
right in the screenshot I took at 1280px" are both weaker evidence than they
appear.

## Contributing — pull requests

**All changes ship via pull request. Never push or merge directly to `main`.**
Work on a feature branch, push it, and open a PR into `main`; let the QA workflow
(`tests` + `secret-scan`) run, then merge the PR. This keeps every change reviewable
and traceable, and the **Checks** admin page (`/admin/checks`) mirrors what the PR
must pass. **Mechanically enforced** by a GitHub ruleset (`main-protection`) on
`main`, created 2026-09-26: requires a PR before merging (0 required approvals —
solo repo), requires the `tests` and `secret-scan` status checks to pass, requires
the PR branch to be up to date with `main` before merge, blocks force pushes, and
restricts branch deletion. The ruleset carries "Repository admin — Always allow" on
its bypass list — a deliberate escape hatch so a GitHub Actions quota outage can't
lock Brian out when required checks can never go green, not an oversight. It guards
against an accidental direct push, not a gate on the sole committer. Issue #631
tracks removing that bypass once the repo is public and Actions minutes are
unrestricted; that's Brian's call, on his own timeline, once CI has run green for a
week. (Before this ruleset existed, "requires a PR and passing checks" was
convention only, not a real gate — worth knowing if a future investigation finds a
commit that looks like it skipped review; anything from before 2026-09-26 could
have.)

**Open the PR as soon as there's a reviewable chunk — don't wait until a multi-phase
task is fully done.** For work that's naturally sequenced into phases (e.g. a phased
build with sign-off between phases), open a PR per phase as it's completed, not one
PR at the very end. Small, incremental PRs are easier to review and catch problems
before they compound across phases.

**Brian merges PRs himself — don't call the merge tool unless he explicitly asks
for it in that moment.** Open the PR, make sure checks are green, then hand him the
link.

**When a prompt or instruction says to post a plan and wait for approval, that is a
hard stop — do not proceed to code until approval is given in the session.**

**GitHub Issues track deferred work.** The working agreement:

- Claude Code creates and edits Issues (backlog grooming, adding context,
  linking related work), but **never closes them directly**. An Issue is closed
  either by a merged PR that references it (`Closes #N` in the PR body) or by
  Brian manually — those are the only two paths.
- **Every PR that addresses an Issue must reference it** in the PR body —
  `Closes #N` when the PR fully resolves it, a plain `#N` mention when it's
  partial progress.

## Documentation

`docs/BUILD_PLAN.md` is the canonical build plan for the current Admin Tooling +
Profile Pages + Feature Normalization & Compare initiative — every phase (1
through 9), what's decided, and the investigation-first gates that require
Brian's explicit approval before schema/migration work. It exists specifically
so a session doesn't lose the plan when it was only ever pasted into chat —
read it directly at the start of any session picking up this work, rather than
asking to have it re-pasted. Verify phase status against actual code/PR
history before assuming the doc is current, the same way you'd verify any
other memory of repo state.

`ARCHITECTURE.md` (repo root) is the living technical overview — schema, request
flows, design decisions, and their Mermaid diagrams. `RUNBOOK.md` (repo root)
holds the operational procedures — DB restore from a Drive snapshot, save-token
rotation, Railway-outage triage — and should be updated in the same PR whenever
a change alters one of those procedures. Several standing rules keep the
docs honest, **in the same PR as the change** (never a follow-up):

1. **Any PR that changes the database schema** (a table or column in
   `linklib/db.py`, including the migration list), **adds/removes/renames a
   route, or alters a flow documented in ARCHITECTURE.md** must update
   `ARCHITECTURE.md` in that PR — including any affected Mermaid diagram (the
   deployment map, the ER diagram, the `/ask` sequence diagram), not just the
   prose. All diagrams stay as fenced `mermaid` code blocks so they render on
   GitHub and remain editable; no image files.
2. **Any PR that adds or removes a dependency** (`requirements.txt` /
   `requirements-dev.txt`) must update the open-source attributions page in
   that PR: the `_OPEN_SOURCE` list in `webapp/app.py`, rendered at
   `/admin/open-source` — add new components with their license, homepage, and
   a one-line "what we use it for" blurb in the right group; remove entries for
   dependencies no longer used. CI already fails on a bare mismatch
   (`tests/test_open_source.py` keeps the showcase in sync with
   `requirements*.txt`), but the test can't write the blurb or verify the
   license — that part is on the PR.
3. **`BRAND.md`** is the other doc this rule covers. §7 (the CSS token table)
   is generated, not hand-edited — it's copied verbatim from the live `:root`
   block in `webapp/app.py`'s `_CSS` by `scripts/generate_brand_docs.py`; run
   `python -m scripts.generate_brand_docs` and commit the diff after touching
   that block (CI, `tests/test_brand_docs_sync.py`, fails a stale table). §9
   (voice) is prose about `/admin/voice`'s DB-backed settings, not a duplicated
   copy — no generator, but **any PR that adds/renames/removes an
   `/admin/voice*` route or changes what's editable there must update §9 by
   hand** in the same PR, the same way ARCHITECTURE.md gets updated for other
   route changes. **§5's width-tier table and any other BRAND.md passage that
   names a specific route or UI section is hand-written, not generated, and
   drifts the same way ARCHITECTURE.md would if this rule didn't cover it —
   so it's covered by the same rule 1 trigger**: any PR that adds, removes, or
   renames a route, or renames a UI section/page BRAND.md names by title
   (a width-tier table entry, a component-catalog example, a named-product
   exception in §3.2), must `grep BRAND.md` for the old name/path and update
   or remove the reference in that same PR — same "in the same PR, never a
   follow-up" discipline as every other rule here. (A 2026-09 brand/voice-doc
   audit found BRAND.md's width-tier table still naming three routes retired
   across earlier phases — `/library`, `/library/archive`, `/library/feed` —
   plus a stale admin-page section-heading example; this is the standing fix
   for that class of drift, not a one-time cleanup.)
4. **The Communities feature reference** — a collapsible "How this works"
   block at the top of `/admin/tools/communities` (`_COMMUNITIES_REFERENCE_HTML`
   in `webapp/app.py`) documents every user-facing prompt/CTA/copy block
   across the Communities feature, in plain language, for Brian's own recall.
   It's static reference content, not a DB-backed editable field, same
   reasoning as why BRAND.md §9 stays hand-edited prose. **Any PR that
   changes Communities-feature copy** (new CTA wording, new form states)
   **must update this block in the same PR** — the same discipline as rules
   1-3 above, so a forgotten prompt doesn't become the next thing this rule
   set has to fix retroactively. (Copy audit batch 6, 2026-09, narrowed this
   block's scope to copy only — the tracking-mechanism/schema half it used
   to also carry was a near-duplicate of ARCHITECTURE.md's own Communities
   section, so that half was cut in favor of a pointer there; a
   tracking-mechanics change is a flow change, already covered by rule 1's
   "alters a flow documented in ARCHITECTURE.md," not this rule.)
5. **The scripts registry** — `/admin/system/scripts` (`_SCRIPT_REGISTRY` in
   `webapp/app.py`, Phase N) is a static, hand-maintained inventory of every
   still-relevant script in `scripts/`: purpose, cadence, required env vars, and
   exact invocation. **Any PR that adds a new script to `scripts/`, or changes
   what an existing recurring script does** (its purpose, required env vars, or
   invocation) **must update this registry in the same PR** — same discipline as
   rules 1-4 above. **Any PR that makes a recurring script's job "done"** (a
   one-time migration completes, a diagnostic's question gets answered for
   good) **should `git mv` it into `scripts/archive/` and remove its entry from
   the registry, in that same PR** — not as a follow-up cleanup. Scripts already
   in `scripts/archive/` are deliberately excluded from the registry; they're
   kept only for git history, never meant to run again in the ordinary course.

## Voice enforcement

**`BANNED_WORDS`/`FILLER_PHRASES`/`PERFORMATIVE` (`linklib/voice_review.py`) stay in
source, permanently — decided and closed (2026-09 voice-enforcement PR, do not
re-litigate).** An earlier round of this same investigation considered moving them into
the DB alongside `voice_core` (so `/admin/voice` would be the single source of truth for
every voice rule, mechanical and holistic). The investigation that followed is why the
answer is no: CI has no authenticated route to the live production database that isn't
wildly disproportionate to the need (the only existing path, `/mcp`'s `get_rows`, has no
per-table denylist — an admin-role token stored as a GitHub Actions secret would be read
access to `users`/`api_tokens`/`password_reset_requests`/`contacts`, held by a
third-party CI system, to read four settings rows), and there is no DB-to-source
generation pattern in this repo to fall back on either (`scripts/generate_brand_docs.py`
is the closest analog, but its source — CSS in `webapp/app.py` — lives in the same git
commit as what it generates; a DB-sourced version would be asymmetric, regeneratable
only by someone with production access, never by CI). A committed mirror can drift from
a live settings row with nothing in the running Railway container able to close that gap
automatically — which is exactly the contradiction the DB-as-source-of-truth idea was
supposed to prevent. Keeping one copy, in source, means there's nothing to diverge.
`/admin/voice` mirrors all three lists **read-only**, in a "Mechanical rules" card marked
"Source-managed" — changing them is a code change, not an admin edit — so the full voice
picture (editable prose rubric above, mechanical enforcement below) still lives on one
page, just not one editable surface.

**Mechanical scan scope widened to match typography's own file list, and the two checks
now share one implementation.** Both rules used to have different footprints:
`typography_findings` (bare ampersands, spaced em dashes) already swept
`TYPOGRAPHY_SCANNED_FILES` — `webapp/app.py`, `linklib/enrich.py`, `linklib/feature_scan.py`
(PR 15) — and failed CI on a hit; the mechanical rules only ever swept `webapp/app.py`,
via a separate, hand-rolled regex scanner in `tests/test_voice_standards.py` (`_hits()`)
that never actually called `mechanical_findings()` — meaning the test, the live
`/admin/checks` dashboard, and any future caller could in principle disagree about what
counts as a violation. Renamed `TYPOGRAPHY_SCANNED_FILES` → `VOICE_SCANNED_FILES` (same
tuple, both rules read it now) and retired `_hits()` — the sweep test now calls
`mechanical_findings()` directly, closing that drift risk and widening coverage from 1
file to 3 in the same move. `webapp.checks.run_all()`'s "Voice standards" row does the
same widened sweep for the live dashboard.

**A real false positive from the widened scope, fixed with a general mask, not a
line-number exclusion.** `linklib/enrich.py`'s own rule text — "No marketing language:
no 'powerful,' 'seamless,' 'game-changing,' 'best-in-class,' ..." — cites `BANNED_WORDS`
members as examples of what NOT to write; a naive file-list widening would have flagged
the rule for stating itself. `linklib.agent.VOICE_CORE_DEFAULT`'s own "- Avoid: ...
delve, robust, seamless, ..." line has the identical shape, confirming this needed a
general fix rather than excluding one known line. `voice_review._mask_rubric_enumerations`
blanks a rubric's own "words to avoid" enumeration (the marker phrase through the next
sentence-ending period, curated via `_RUBRIC_ENUMERATION_RE` — "No marketing language:"
and "- Avoid:" today, same "add a real one when it turns up" discipline as
`AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS`) before `mechanical_findings` scans anything — a
real violation elsewhere in the same string, even the same sentence before the marker,
still gets caught. Note the mask doesn't cover every shape `agent.py`'s own rubric uses
("No performative openers or closers (...)", "No filler (...)" are a different marker
shape) — harmless today since `agent.py` isn't in `VOICE_SCANNED_FILES`, but a future
sweep that adds it needs new markers, not just a file-list edit.

**Semantic contradiction, checked separately from the mechanical mirror above: does
`voice_core`'s own prose promise a rejection the machine doesn't actually enforce?**
`voice_review.voice_core_gap_problems(voice_core_text)` extracts every 2+-word quoted
phrase from the rubric and confirms each is covered by re-running it through
`mechanical_findings` itself (never a second, independent containment check, so this can
never disagree with what real copy scanning does). One direction only — a
`BANNED_WORDS`/`FILLER_PHRASES`/`PERFORMATIVE` entry the prose never mentions is fine,
the lists are allowed to be more specific than the rubric. The 2+-word floor is
evidence-based, not arbitrary: a blind scan of every double-quoted span in the real
`VOICE_CORE_DEFAULT` flags `"&"`, `"and"`, and `"to"` too — single-word/character asides
quoted for an unrelated reason (the ampersand-spelling rule, an arrow-notation
replacement suggestion), not "avoid this phrase" examples; every real
`FILLER_PHRASES`/`PERFORMATIVE` example in that same text is a genuine 2+-word phrase, so
the floor removes exactly the three false positives and none of the real signal. Running
this against the real rubric before shipping found one genuine gap: "there are many
factors to consider" (voice_core's own generic-hedging example) wasn't in
`FILLER_PHRASES` — fixed by adding the phrase to the list (a code change extending
enforcement to match an existing promise, not a rewrite of Brian's prose). Wired into
`run_all()` as a CI-safe row against `VOICE_CORE_DEFAULT` (the code constant) — an admin
edit to the *live* `voice_core` setting that names a new example isn't caught by this
row, only a drift in the code default is; same CI-has-no-DB-route boundary as everything
else in this section.

**Database content is scanned too — but only live, on `/admin/checks`, never in CI.**
A growing share of real user-facing copy lives in the database, not source: `settings`
overrides (`homepage_*_copy`, `about_page_copy`, `htib_before_copy`/`htib_after_copy`),
`original_content`, `ai_surfaces`, `thought_leadership`, `tools` (description/summary/
agent_taxonomy_note/competitive_differentiation/suite_note), `communities`
(demographic/cost_note/notes/local_markets), all 23 `community_profiles` narrative
fields, `category_features.name`, `benchmarks`, and `tool_categories`/
`community_categories.description` — the last two confirmed rendering publicly as `title=`
tooltips on the `/tools/software`/`/tools/communities` category filter pills (a
correction to an earlier, incomplete scan of the same question — both genuinely do
render, not just one). `linklib/voice_db_scan.py`'s `scan_db_copy(lib)` runs both
`mechanical_findings` and a new plain-text sibling, `typography_findings_plain`
(factored out of `typography_findings` — same masking/allowlist logic, no `ast.parse`,
since a DB value is already the whole literal, not something to extract one from), over
every enumerated column. Surfaced as a "Database-backed copy" section on `/admin/checks`
(a live count + a capped violation list, colored banner, no reviewed-toggle — unlike the
three dated pricing/model-freshness reminders on the same page, this is a fact computed
fresh on every load, not a human attestation) — it counts and reports, it never
rewrites; any real fix is an ordinary editorial change through whichever admin page owns
the record, reviewed by Brian before it ships, same as any other copy edit.

Two real, found-before-shipping design points, not incidental: (1) **`tools.name`/
`communities.name`/`benchmarks.name` are exempt from the typography half of the scan**
(mechanical checks still apply) — these are third-party entity names, and
`typography_findings_plain("Bain & Company")` genuinely flags it as a bare ampersand,
reproducing the exact false-positive risk BRAND.md's own ampersand rule already
documents for source-code scanning. `category_features.name`/`tool_categories.name`/
`community_categories.name` are Brian's own curated vocabulary, not third-party names,
so they stay in typography scope. (2) **`category_features.definition`/`pointer_note`
are excluded from the scanner entirely, not just from typography** — confirmed neither
renders on any public page: `list_tool_feature_links_with_details()` (the read path both
the public "Key features" card and the Software Matchmaker's context use) doesn't even
`SELECT` those two columns, and the only place either renders is two `_is_authed`-gated
admin surfaces. This is a real, separate, logged-not-fixed finding: real per-feature
reference text Brian writes and re-reads that nothing public — not the profile card, not
Compare, not the Matchmaker — ever shows. Worth its own decision later (render it, or
stop collecting it as if it were user-facing); out of scope here.

**A related root-cause fix, found while building the scanner's own tests, not assumed:**
`add_category_feature`/`update_category_feature` (`linklib/db.py`) never ran their
`definition`/`pointer_note` fields through `voice_mechanics.normalize_voice_mechanics`
(`_voice_fix`) — the same spaced-em-dash write-time backstop nearly every other
narrative-field write path in this file already uses (`tools.description`,
`communities.demographic`, all 23 `community_profiles` fields, ...). This is almost
certainly why the confirmed live production violation this whole investigation started
from — `category_features` id 8's `definition`, "...patterns that don't look right —
not necessarily a balance change" — exists at all. Fixed for future writes only (both
methods now call `_voice_fix()` on `definition`/`pointer_note`, matching the established
pattern exactly); the already-stored live value is untouched — it's exactly the kind of
finding the DB scanner above is for, surfaced there, fixed through the admin UI like any
other copy edit, never auto-corrected.

**Follow-up, same PR — the `_voice_fix()` omission wasn't unique to `category_features`,
and the `/admin/checks` summary banner was separately broken the whole time.** A review
pass on this PR pushed on two things the first round under-covered. (1) `admin_checks()`'s
green/red summary banner compared `r["where"]` against the literal `"In-app"` — a value
`run_all()` has never produced (every live row's `where` is `"Live + CI"`); the banner
had been permanently blank regardless of pass/fail state, on a page this very PR was
about to make a load-bearing surfacing mechanism for a new class of finding. Fixed to
compare against `"Live + CI"`; verified live, both directions, via `TestClient` (green
on a clean DB, red once a real `run_all()` check — `mechanical_findings`, monkeypatched —
is forced to fail). (2) The `category_features` fix turned out to be one instance of a
broader pattern, not an isolated miss: auditing every `Library` write method against the
same (table, column) pairs `voice_db_scan._SCAN_TABLES`/`_SCAN_SETTINGS_KEYS` already
treat as real copy found seven more write paths skipping `_voice_fix()` —
`add_tool_category`/`rename_tool_category`, `add_community_category`/
`rename_community_category`, `add_benchmark`/`update_benchmark`/`update_benchmark_content`,
`add_thought_leadership`/`update_thought_leadership`, `add_original_content`/
`update_original_content`, `add_ai_surface`/`update_ai_surface`, and — the single
broadest gap — `Library.set_setting()` itself, the one choke point every `/admin/copy/*`
route (homepage headline/subhead/teaser/expanded, about page, how-this-is-built) writes
through directly with no wrapping of its own. All eight fixed the same way, in the same
PR. `normalize_voice_mechanics` is confirmed safe applied unconditionally inside
`set_setting()` — it's a no-op on any value without a spaced em dash, which every
non-prose settings key (caps, flags, model ids, JSON blobs, tokens) always is; a
dedicated test (`test_set_setting_is_a_no_op_on_non_prose_values`) round-trips a cap, a
flag, a model id, and a JSON blob byte-for-byte to confirm it. `insert_mirrored_article`/
`update_mirrored_article` needed no separate fix — `sync_original_content_article()` reads
the already-normalized `original_content` row back via `get_original_content()`, so
fixing the two source methods is the real root-cause fix there too. Deliberately left
alone: `update_game_rank_settings`'s `label`/`difficulty_label` (short rank labels, not
prose) and `rename_tag` (short keyword labels) — same reasoning as the entity-name
typography exemption above, just for mechanical scope instead. `scripts/
fix_spaced_em_dashes.py` (the existing one-off cleanup for content written before the
backstop existed) is extended with the same seven tables plus a settings pass, so the
already-confirmed live violation — `category_features` id 8's `definition` — and any
sibling violations in these newly-covered columns can be cleaned up the same human-run
way (preview by default, `--apply` to write, write-then-read-back verified per row) —
reproduced against a faithful copy of that exact row before shipping and confirmed to
produce the correct before/after text; running `--apply` against the real database is,
per this repo's own standing rule, Brian's to do via `railway ssh`, not something this
session can do itself (no production DB write path is available here — the `/mcp` tools
this session can reach are read-only by design, see the MCP server bullet elsewhere in
this doc).

**Structural-enforcement assessment (asked, not built): can the `_voice_fix()` backstop
be made impossible to skip, rather than opt-in by convention?** Two real options, not
one obvious answer. (a) **Intercept at the SQL layer** — wrap `Library`'s own
`conn.execute`/parameter-binding so every string parameter on every INSERT/UPDATE is
run through `normalize_voice_mechanics` unconditionally, the same reasoning that makes
the `set_setting()` fix above safe. Genuinely un-skippable — no method-level call to
forget, ever, for any future write path — but the blast radius is the entire `Library`
class, not just the ~15 prose-capable methods: every URL, id, date, JSON blob, and token
column in the schema would flow through the same regex on every write, and the SQL text
itself would need to be inspected (or every write funneled through a second choke-point
method) to know which query is a write vs. a read. Low functional risk (the regex only
ever touches a spaced em dash, which should never legitimately appear in a non-prose
column), but a large, all-at-once change to verify, and a real precedent for "quiet
magic happening underneath every write" that a future `Library` method's own author
might not expect. (b) **A mechanical CI drift-detector**, the same shape as
`hub_nav_orphans()`/`icon_fill_contract_problems()`/`og_url_threading_problems()`
elsewhere in this codebase: an AST/regex-based test that finds every `linklib/db.py`
method issuing an `INSERT INTO`/`UPDATE` against a table+column pair already listed in
`voice_db_scan._SCAN_TABLES` and asserts `_voice_fix(` appears somewhere in that
method's body (with an explicit, named allowlist for the few real exceptions — rank
labels, tag names, the two mirror-sync methods that read already-normalized data).
Lower risk, smaller diff, and reuses a pattern this codebase already trusts and tests —
but it's a CI-time guard, not a runtime guarantee: a new write method still *can* ship
without the backstop, it just fails loudly the moment this test runs rather than
shipping silently. Given this repo's own established preference (every other "keep two
things from drifting apart" problem here is solved with option (b)'s shape, never
option (a)'s), (b) is the one worth building if this is worth closing further — roughly
half a day including tests, reusing the exact AST-walk technique `hub_nav_orphans()`
already uses. Not built in this PR; flagged for a decision, not assumed.

See `linklib/voice_review.py`, `linklib/voice_db_scan.py`, `webapp/checks.py`'s
`VOICE_SCANNED_FILES`, `scripts/fix_spaced_em_dashes.py`, `tests/test_voice_standards.py`,
`tests/test_voice_db_scan.py`, `tests/test_checks.py`, and `tests/
test_voice_fix_write_path_audit.py` for the full implementation and regression coverage.

- **Voice review queue (2026-09) — violations get a review queue, not
  silent correction; asynchronous, never a save-time blocking gate.** Two
  real gaps closed first, since they were blockers for everything else:
  (1) the DB scanner and `_voice_fix` (the write-time spaced-em-dash
  backstop) disagreed about `category_features.definition`/`pointer_note`
  — the scanner never looked at those two columns at all (deemed
  admin-only, not "user-facing copy"), so it could never report the exact
  correction `_voice_fix` was already applying at write time (this is what
  the confirmed production case, `category_features` id 8's `definition`,
  actually was). Fixed by adding both columns to `voice_db_scan._SCAN_TABLES`
  (typography-checked; `name` stays typography-scanned, unchanged) — the
  two now agree.
  (2) Part 2's ampersand allowlist gained `G&A`/`L&D` in
  `AMPERSAND_ACRONYMS`, and the name-column typography exemption
  (previously only `tools.name`/`communities.name`/`benchmarks.name`) was
  extended to `thought_leadership.title` — a real curated title (an
  externally-hosted event/piece name) can legitimately carry an ampersand
  the same way a third-party entity name can, no conflicting test blocks
  it. `category_features.name` was initially left out of that same
  exemption on the same PR's own reasoning: with 22 real confirmed
  production findings on this one column ("Sales & Marketing"-shaped
  feature names), the original call was that this is Brian's own curated
  feature vocabulary, not a third-party name, so it should stay in
  typography scope and a legitimate ampersand should resolve via the
  shared `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS` allowlists instead — a
  pre-existing, committed test (originally
  `test_category_features_name_ampersand_is_still_scanned`) encoded that
  decision. **Reversed, on purpose, by Brian himself in the same PR's
  follow-up round**: he reviewed the reasoning and chose to own each
  category name's ampersand usage directly rather than route 22 findings
  through the review queue to reach the same answer he'd give by hand —
  `category_features.name` now joins the same typography-exempt set as
  `tools.name`/`communities.name`/`benchmarks.name`/
  `thought_leadership.title`, and the test (renamed
  `test_category_features_name_ampersand_is_no_longer_scanned`) now
  asserts the new behavior, with its own docstring stating the reversal
  and why — so a future session finds evidence of the decision, not a
  mystery to re-litigate. He explicitly accepts the trade: mechanical
  rules (banned words/filler/performative) still apply to this column, but
  the typography rule no longer runs on it at all, so a genuinely-lazy
  "X & Y" typed in place of "X and Y" won't be flagged there either — same
  accepted risk as every other name-column exemption above.
  A new `voice_review_queue` table (`linklib/db.py`) records every finding
  — an `_voice_fix` correction already applied at save time
  (`status='auto_corrected'`, before/after text logged) or a scanner
  finding with nothing to auto-fix (`status='open'`) — for an explicit
  human review pass at `/admin/voice/review-queue` (grouped by rule, one
  table per group), rather than silently applying a correction or
  reporting only an aggregate count. Deliberately asynchronous, not a
  blocking gate — a save-time modal would be correct but unusable.
  `Library._vf(table, row_id, column, value)` is the instrumented
  replacement for a bare `_voice_fix(value)` call — normalizes identically,
  and additionally logs an `auto_corrected` row when the text actually
  changed. **Wired into `set_setting()` (every `/admin/copy/*` field, the
  single broadest write path) and every `tools`/`category_features` write
  method with a known row id** — `update_tool`/`update_tool_content`/
  `quick_update_tool` (description/summary), `update_tool_differentiation`,
  `set_tool_suite_note`, `update_tool_agent_taxonomy`/
  `set_tool_agent_taxonomy_draft`, `add_category_feature`/
  `update_category_feature` (the exact confirmed-bug write path).
  **Explicitly NOT instrumented in this PR's own first round — a disclosed
  scope cut, not a silent gap**: the `communities`/`community_profiles`/
  `benchmarks`/`thought_leadership`/`original_content`/`ai_surfaces`
  UPDATE/INSERT methods still called bare `_voice_fix()` with no queue
  logging — named explicitly in `tests/
  test_voice_fix_coverage_ci_guard.py`'s allowlist as a follow-up, not
  silently left uncovered. `original_content` was instrumented in this
  same PR's own follow-up round (Brian's own published thought
  leadership — the single highest-value table for this whole feature);
  the other five were instrumented in a separate follow-up PR — see the
  bullet directly below. A backfill script,
  `scripts/backfill_voice_review_queue.py` (preview/`--apply`, same
  convention as `scripts/fix_spaced_em_dashes.py`), populates the queue's
  `open` rows retroactively from `voice_db_scan.scan_db_copy_report()` —
  the queue launches populated, not empty, since a scanner finding is a
  live fact about content already in the database, unlike the
  `auto_corrected` log, which only starts from the moment the per-write
  logging shipped.
  **Two exception mechanisms, kept visibly distinct**: the global,
  source-side `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS` allowlists in
  `linklib/voice_review.py` (a code change, reviewed like any other PR)
  vs. `Library.is_voice_exception`/the "Accept as exception" queue action
  (a database-backed, row-scoped exception — this ONE record+column+rule,
  never a global rule; `resolve_voice_review_item(..., "accept_exception")`)
  — accepting one never reads as changing the other.
  **A CI-safe structural drift-detector** (`tests/
  test_voice_fix_coverage_ci_guard.py`, option (b) from the assessment
  above) parses `linklib/db.py` and asserts every `Library` method that
  writes a scanned prose column also calls `_voice_fix`/`self._vf`
  somewhere in its body, with a small, named allowlist (rank/tag labels,
  the two mirror-sync methods, and this PR's own disclosed scope cut) — a
  CI-time guard, not a runtime guarantee; a new write path that skips the
  backstop fails this test the next time it runs, it isn't structurally
  prevented from ever shipping (SQL-layer interception was assessed again
  and rejected for the same reasons as before).
  **`category_features.definition`/`pointer_note` remain a real, open
  question, reported rather than resolved**: they now render nowhere
  public (confirmed again in this pass) but ARE now scanned/queued for
  correction-tracking purposes — whether they should ever be surfaced
  publicly (making this dual-purpose) or dropped from user-facing-copy
  framing entirely is still Brian's call, not decided here.
  **Deliberately out of scope this PR, per its own priority-order cut**:
  holistic (Claude-judged) review against a database record on demand
  (extending `/admin/voice`'s existing text-paste tester to accept a
  table/column/id instead), the cheap deterministic invisible-unicode-
  character mechanical check (both instrumentation and this check shipped
  in the follow-up PR directly below), and a per-record "N open voice
  findings" indicator on the tool/community edit forms — all real,
  deferred to a follow-up, not silently dropped. See `tests/
  test_voice_review_queue.py` and `tests/test_voice_fix_coverage_ci_guard.py`
  for the regression coverage.
- **Voice review queue — seed-sync overwrite fix, edit-safety fix,
  bidirectional sync, and the Approve-term/Allow-here split (2026-09).**
  Four coupled fixes, shipped together, on top of the queue mechanism
  described above.
  1. **Part 1 (seed-sync overwrite fix)** — `_seed_toolbox()` used to
     silently `UPDATE` `tools.name`/`communities.name`/`communities.notes`/
     `benchmarks.name`/`benchmarks.description` back to whatever the static
     seed source said on every process boot, with no logging anywhere — an
     admin's own hand-edit was reverted on the very next deploy with no
     trace. `Library.add_seed_disagreement_item(table, row_id, column,
     stored_value, seed_value, source="startup-sync")` replaces the
     overwrite: it queues an `open`, `seed-disagreement`-rule row instead
     of touching the live value, deduplicated against both an existing
     exception and an existing open row for the exact same location (so
     repeated boots proposing the identical divergence never queue a
     duplicate — the exact infinite-loop shape the incident exposed).
     `tools.name`'s sync is routed through this same method now, not a
     raw, unlogged `UPDATE tools SET name=?` — the identical fix already
     applied to `communities`/`benchmarks`. Two resolution actions exist
     only for this rule: `use_seed` (writes the seed's proposed text back
     via `apply_voice_review_write`, then resolves) and `keep_mine` (writes
     nothing back, marks the location a permanent exception so the
     divergence can never reopen).
  2. **Part 4 (URGENT — edit-safety)** — the `open`-row edit textarea used
     to be pre-filled from the queue row's own `excerpt`, a mid-text
     SNIPPET capped at 200 characters, so saving it back unchanged would
     truncate real, live, published copy down to a 200-char fragment.
     Fixed with `Library.get_voice_review_current_value(table, row_id,
     column)` — fetches the FULL, CURRENT live value (validated against
     the same `_SCAN_TABLES` enumeration `apply_voice_review_write` uses)
     and pre-fills the textarea with that instead; the excerpt now only
     ever renders as a small "Flagged text:" hint above the field. See
     `tests/test_voice_review_queue.py::
     test_review_queue_edit_prefill_uses_full_value_not_truncated_excerpt`.
  3. **Addition 1 (bidirectional sync)** — `Library.
     reconcile_voice_review_queue()`, run periodically from the background
     checks refresher, closes two gaps a single scan or write-time hook
     can't: a judgment-needed violation (bare ampersand, banned word)
     entering the DB via an ordinary write sat invisible in the live
     `/admin/checks` count but never reached the queue without a manual
     backfill run; and fixing a violation directly on a record's own admin
     edit page (bypassing the queue entirely) left its `open` row open
     forever, since nothing closed it. One pass adds an `open` row for
     every live finding not already queued, and resolves every currently-
     open row (among the rules the scan can produce — deliberately
     excluding `seed-disagreement` and `holistic`) that the scan no longer
     reproduces, with a `"Resolved outside the queue — no longer found on
     the last scan."` note. Returns `{"added": n, "closed": n}`.
  4. **Addition 2 — a new `voice_approved_terms` table splits "Mark as
     exception" into two visually and functionally distinct actions, per
     the coordinator's own explicit amendment.** "Allow here"
     (`resolve_voice_review_item(..., "accept_exception")`, unchanged from
     the original per-row exception) stays scoped to exactly one record+
     column+rule, rendered as a plain muted dashed-border button, available
     on every `open` row regardless of rule. "Approve term"
     (`Library.approve_voice_term(term, rule="bare-ampersand")` /
     `voice_approved_terms` — `id`/`term`/`rule`/`created_at`, unique per
     `(rule, term)` case-insensitively) is a GLOBAL, PERMANENT allowlist
     entry — shown ONLY on a `bare-ampersand` finding (a banned-word/
     filler/performative phrase stays permanently banned, with only
     per-row "Allow here" exceptions, Brian's own explicit policy) —
     styled as a solid navy button, pre-filled with the row's own flagged
     excerpt as an editable starting guess. Approving a term is idempotent
     and immediately sweeps every currently-`open` row of the same rule
     whose text contains the term, resolving each with a `resolution_note`.
     The live DB scanner masks an approved term out of future bare-
     ampersand scans via `linklib.voice_review.mask_approved_ampersand_terms`,
     called from `voice_db_scan.scan_db_copy_report` with the full
     approved-terms list (`list_approved_voice_terms`) read once per scan
     — **not** `Library.is_approved_voice_term`, a separate, smaller
     per-value helper kept for a possible future call site but with no
     live caller today (corrected 2026-09; an earlier version of this
     bullet named the wrong method). **Never** read by the CI-only
     source-code scan (`linklib.voice_review.typography_findings`), which
     has no database to read this table from; the source-side
     `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS` allowlists remain the only
     mechanism for a source-code ampersand, kept visibly distinct in the
     UI so approving one is never confused with editing the other. One
     nuance worth knowing: `mask_approved_ampersand_terms`'s masking is
     layered ON TOP OF the source-level `AMPERSAND_NAMES`/
     `AMPERSAND_ACRONYMS` allowlist inside `typography_findings_plain`
     (both are checked before a bare-ampersand finding is reported), so a
     term added to the source allowlist for CI purposes also silently
     suppresses that phrase in the live DB scan — the two lists aren't
     fully independent in effect, even though they serve different scans.
     `Library.remove_approved_voice_term(term_id)` makes the term
     flaggable again on the next scan pass only — it never retroactively
     reopens already-resolved rows. New routes: `POST /admin/voice/
     review-queue/{item_id}/approve-term` and, on `/admin/voice` itself,
     `POST /admin/voice/approved-terms/add`/
     `POST /admin/voice/approved-terms/{term_id}/remove` — a standalone
     management section listing every currently-approved term per rule.
     See `tests/test_voice_approve_term.py` for the regression coverage
     (per-rule action-set gating, the bulk-resolve sweep on approval,
     removal re-enabling future flagging, both admin routes) and
     ARCHITECTURE.md's "Voice review queue: the base mechanism, and
     Addition 2's `voice_approved_terms` split" section for the full
     write-up.
- **Voice review queue, independent-verification follow-up (2026-09) —
  a second session verified the PR above requirement-by-requirement
  against the actual code (not the PR's own summary), found four real UI
  gaps on `/admin/voice/review-queue` and three real content gaps, and
  fixed all seven in one pass.**
  1. **Column widths were never actually consistent across rule
     groups** — each rule renders its own separate `<table>` with no
     shared width constants, so the browser auto-sized each independently
     based on that group's own content; measured live before the fix,
     Actions alone ranged 221-651px across groups. Fixed with
     `table-layout:fixed` plus shared `_VOICE_COL_WIDTH_*` constants
     (Field reuses `_COL_WIDTH_NAME`, Source reuses `_COL_WIDTH_STATUS`,
     Actions gets its own 320px) — every group's table now renders
     byte-identical column proportions, confirmed via live
     `getBoundingClientRect()` measurement, not just a screenshot glance.
  2. **The `open`-row edit textarea rendered always-visible, not behind a
     button reveal.** Collapsed behind a new "Edit" button
     (`voiceToggleEditField`, plain `style.display` toggle by item id — no
     new CSS classes) — the default row shows Edit/Allow here (plus the
     Approve-term mini-form for bare-ampersand rows, kept always-visible
     since it's a small compact field, not the bulky textarea C4 was
     actually about); clicking Edit reveals the full-width textarea +
     Save edit/Cancel underneath, Cancel collapsing it back without
     submitting anything. Confirmed live: clicking Edit on one row
     expands only that row, leaves sibling rows collapsed.
  3. **Row actions weren't right-aligned or reliably one-line** — every
     row type's actions now render inside a `justify-content:flex-end`
     flex row by default, a deliberate, page-scoped departure from
     `.admin-table-actions-grid`'s own sitewide `justify-content:start`
     convention (confirmed by inspection before diverging — this page's
     content genuinely calls for the opposite alignment, it isn't a
     mistake replicated from elsewhere).
  4. **Mobile had no scroll affordance** — at 390px, every group's table
     had real horizontal overflow (`scrollWidth` up to 905px vs. a 340px
     `clientWidth`) with the entire Actions column scrolled off-screen and
     zero visual cue. A page-scoped `_VOICE_SCROLL_HINT_ITEM_HTML`/
     `_VOICE_SCROLL_HINT_JS` reuses the sitewide `.admin-scroll-hint` CSS
     class (shared look) but operates on N wrap/hint pairs by DOM
     adjacency rather than the shared single-id `_ADMIN_SCROLL_HINT_JS`
     mechanism, since this page can render more than one wide table.
  5. **No script tagged the legacy `open` rows with `source IS NULL` as
     `'script'`.** `scripts/backfill_voice_review_queue_source.py` gained
     a second phase, reasoned independently of its original two-row
     target: `Library.add_voice_review_item` is the only method that has
     ever created an `open` row (the new `add_seed_disagreement_item` is
     the only other creator, and it's brand-new in this same PR and
     always sets a real `source`), and `add_voice_review_item`'s `source`
     parameter has always defaulted to `'script'` — so any `open` row
     still showing `source IS NULL` must predate the column's own
     migration and must have come from `scripts/backfill_voice_review_queue.py`.
     Deliberately does NOT touch an `auto_corrected` row with
     `source IS NULL` outside the original two hand-identified rows — that
     row's origin is genuinely ambiguous pre-migration, so guessing would
     violate this same file's own "never a blanket sweep on an ambiguous
     case" rule.
  6. **The write-time invisible-character strip (Part 5 above) never
     retroactively fixed already-stored data** — including the exact
     production row (`category_features` id 104's trailing zero-width
     space) the whole feature was framed around. New
     `scripts/fix_invisible_characters.py` is the retroactive counterpart
     to `scripts/fix_spaced_em_dashes.py` for this half of
     `normalize_voice_mechanics` — same structure, same `_TARGETS`/
     `_SETTINGS_TARGETS` table (imported from the sibling script, not
     duplicated), same preview/`--apply`/write-then-read-back discipline,
     only strips the three auto-strip-safe characters (never the
     flag-only ones). Not yet run against production — reserved for
     Brian via `railway ssh`, same as every other one-off fix script here.
  7. **A doc/docstring inaccuracy**: `Library.is_approved_voice_term`'s
     docstring and this file's own Addition 2 writeup both claimed the
     live DB scanner reads approved terms through that method — it
     doesn't (confirmed: zero live callers). The actual masking path is
     `voice_db_scan.scan_db_copy_report` → `linklib.voice_review.
     mask_approved_ampersand_terms`, given the full list from
     `list_approved_voice_terms` once per scan. `is_approved_voice_term`
     is kept as a small, tested, currently-uncalled primitive for a
     possible future use, not deleted — both docstrings corrected to
     describe the real mechanism instead. Also documented in the same
     pass: `mask_approved_ampersand_terms` checks the source-side
     `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS` allowlist too, not just the DB
     table, so a term added to the source allowlist for CI purposes also
     silently suppresses that phrase in the live DB scan — the two lists
     aren't fully independent in effect, even though they govern
     different scans.

  Also widened `tests/test_script_syspath_fix.py` from 3 scripts to all
  ~13 scripts touched across #588/#590 (the other 10 already had their own
  sys.path shim — this closes the gap between "manually verified working"
  and "has a committed regression test"), and added a regression test
  proving `approve_voice_term` actually stops the live scanner from
  reflagging a term, not just resolving already-queued rows. See
  `tests/test_backfill_voice_review_queue_source.py`,
  `tests/test_fix_invisible_characters.py`, and the updated
  `tests/test_voice_approve_term.py`/`tests/test_script_syspath_fix.py`
  for the full regression coverage.
- **Voice review queue, issue #592 follow-ups (2026-09) — the four items
  logged from #590's own merge review: the advisor boolean's silent
  revert, the background refresher wandering between test databases, a
  mislabeled `source` value, and a bulk ampersand-replacement action.**
  1. **`tools.advisor`/`communities.advisor` had the identical
     silent-revert shape** as name/notes/description before #590 fixed
     those — a raw, unlogged `UPDATE` in `_seed_toolbox()` re-syncing to
     the seed list's value on every boot, regardless of a deliberate admin
     edit. Confirmed bidirectional first (it already re-synced True->False
     as readily as False->True, contrary to an earlier code comment
     claiming otherwise) — no reason found to treat one direction
     differently, so both now queue through the identical
     seed-disagreement mechanism as the text fields, via a new
     `Library._SEED_BOOLEAN_COLUMNS` allowlist (`tools.advisor`,
     `communities.advisor`) that `apply_voice_review_write`/
     `get_voice_review_current_value` both recognize alongside their
     existing `_SCAN_TABLES`-driven text-column handling — a boolean is
     stored/read as the literal string `"True"`/`"False"`, never mixed
     into the prose-only `_SCAN_TABLES` enumeration (which also drives the
     voice-typography scanner, and a boolean isn't prose). "Use seed
     version"/"Keep mine" both work unmodified for these rows, same as any
     other seed-disagreement item.
  2. **The background checks refresher (`webapp/tasks.py`) re-read
     `LINKLIB_DB` on every single iteration**, a latent "wander between
     test databases" risk once a leftover thread from one test kept
     looping in the background against a since-monkeypatched-and-deleted
     DB path — a real risk, not hypothetical, since the one test file that
     triggers the FastAPI startup event
     (`tests/test_seed_toolbox_startup.py`, via `with TestClient`) started
     this thread on every one of its tests. Fixed two ways: the DB path is
     now resolved exactly once, in `start_background_checks_refresher()`,
     and threaded through to `_checks_refresher_loop`/
     `_reconcile_voice_review_queue_once` as a plain argument instead of
     each iteration re-reading the environment; and
     `start_background_checks_refresher()` is now a no-op under the test
     suite by DEFAULT (detected via `"PYTEST_CURRENT_TEST" in os.environ`
     — pytest's own standard, reliable signal that a test is currently
     running, with no pytest import needed), closing the wandering risk at
     its source rather than only narrowing the window. A test that
     deliberately wants the real thread can still get it via
     `force=True`. Production is unaffected either way — `LINKLIB_DB`
     never changes after boot there, and it isn't running under pytest.
     **Checked explicitly, not assumed (a same-PR follow-up review):**
     `PYTEST_CURRENT_TEST` is only set while a test is actively running,
     never during collection/import — could the refresher start before
     then? Every `with TestClient(...)` in this suite (the only thing
     that can trigger the startup event and reach this function at all)
     lives inside a `def test_*` function body, and there's no
     conftest.py providing a session/module fixture that could construct
     one earlier — so under the current suite, no. As a cheap, strictly
     broader backup guard against a future test file collecting a
     `TestClient` outside any test function, the check also covers
     `"pytest" in sys.modules` (true for the whole pytest process
     lifetime, not just while a test is running) — safe in production
     since `pytest` is `requirements-dev.txt`-only, never installed in
     the Docker image.
  3. **`reconcile_voice_review_queue()` tagged its own inserted rows
     `source="script"`**, indistinguishable from a genuinely one-off,
     human-run backfill script (`scripts/backfill_voice_review_queue.py`,
     which shares the exact same `add_voice_review_item` insertion path
     via its own `source` default) — but a periodic BACKGROUND pass isn't
     a script. Fixed by passing `source="scan"` explicitly at that one
     call site; `add_voice_review_item`'s own default stays `"script"`
     for the actual one-off script, unaffected. **The taxonomy is six
     concepts across two different columns, worth stating precisely
     rather than folding into one list**: `voice_review_queue.source`
     recognizes five values — `admin-edit`, `startup-sync`, `script`,
     `submission`, and now `scan` — describing which MECHANISM wrote a
     given row. `rule='seed-disagreement'` (set by
     `add_seed_disagreement_item`) is a genuinely different dimension —
     the RULE that matched, not a `source` value — and a
     seed-disagreement row's own `source` is always `'startup-sync'`, the
     mechanism that found the divergence; the two are easy to conflate
     since both are string tags on the same table, so every taxonomy
     comment in `linklib/db.py`/`webapp/app.py` now says this explicitly.
     **Relabeling already-existing rows was investigated and found not
     reliably possible, not skipped**: every `open`-status row tagged
     `source='script'` before this fix could only have come from either
     the one-off backfill script's own run or this reconciliation pass
     (both share the identical insertion path, both wrote the identical
     `'script'` value, and no other column on this table records which
     literal code path produced a row) — there's no reliable per-row
     signal to split them by after the fact, so none were reclassified;
     this is stated here rather than guessed past.
  4. **A group-level "Replace ampersands with and" bulk action on the
     Ampersands group** (`/admin/voice/review-queue`), for cases like the ~20
     `communities.demographic` bare-ampersand findings the live DB scan
     surfaced (see the "Database content is scanned too" section above) —
     almost certainly all legitimate mechanical fixes, not judgment calls.
     `linklib.voice_review.replace_spaced_ampersands(text, approved_terms)`
     is the pure function underneath it: replaces every SPACED raw
     ampersand (`" & "`) or spaced HTML-escaped ampersand (`" &amp; "`)
     with `" and "` — deliberately never a bare `"&"`->`"and"`
     substitution, which would turn `" &amp; "` into the stray
     `" andamp; "` instead. An UNSPACED ampersand (`"S&M"`, `"AT&T"`)
     never matches the pattern at all, so it's always left for a manual
     decision — replacing those would produce `"SandM"`. Protects any
     spaced ampersand that's part of an already-approved bare-ampersand
     term (`Library.list_approved_voice_terms`) by reusing
     `mask_approved_ampersand_terms`'s own length-preserving underscore
     mask rather than a second implementation — a candidate match is
     protected exactly when its own span in the masked text is entirely
     underscores, i.e. it fell inside an approved term's own occurrence;
     verified with the exact scenario named in the spec, a field
     containing both "Bain & Company" (approved) and "finance &
     operations" (not) — only the second moves. `Library.
     preview_ampersand_replacement(item_id)`/`apply_ampersand_replacement
     (item_id)` are the review-queue-aware wrappers: preview reads the
     FULL CURRENT live value (never the queue row's own excerpt, same
     Part-4 discipline as every other write-back path here) and computes
     what would change with nothing written; apply re-derives the
     replacement fresh against the current value (never trusting an
     earlier preview call — the same TOCTOU discipline
     `/admin/reader/bulk-delete`'s own preview-then-commit flow already
     uses) and only writes+resolves a row that actually has something
     eligible to change, via `apply_voice_review_write` (so the write is
     logged exactly like any other resolution) — a no-op selection (only
     unspaced/approved-term ampersands) is left untouched and open, never
     silently resolved. Two new routes,
     `POST /admin/voice/review-queue/bulk-replace-ampersand/preview` and
     `.../apply`, follow the same preview-page-then-confirm-form shape as
     `/admin/reader/bulk-delete`'s own CSV-driven preview — nothing is
     written until the admin reviews a before/after diff per row (via the
     same `_voice_char_diff_html` helper the `auto_corrected` rows already
     use) and clicks confirm; a "Left as-is" section lists every selected
     row that wouldn't change, and why, rather than silently dropping it
     from the result. The button (`_voice_review_group_bulk_actions_html`)
     renders only on the `bare-ampersand` group, alongside the existing
     Accept/Allow-here buttons, not in place of them. See
     `linklib/voice_review.py`'s `replace_spaced_ampersands` docstring,
     `tests/test_voice_review_queue.py`'s "Issue #592 item 4" section
     (the pure-function unit tests plus the Library-level preview/apply
     tests, including the mixed-approved-term and escaped-`&amp;`-in-
     `body_md` edge cases), and `tests/test_voice_bulk_replace_ampersand.py`
     for the two routes' own coverage (diff rendering, no-write-on-preview,
     empty-selection redirect, TOCTOU-safe apply, auth).
  5. **Same-PR UI review round, from live screenshots of the queue page** —
     folded into this PR per explicit instruction, not shipped as a
     follow-up. Row actions (`_voice_review_row_html`) now render inside
     one `flex-wrap:wrap` single-line row by default (wrapping to a
     second line only when the viewport forces it), left-aligned
     (`justify-content:flex-start`, reversing the earlier page-scoped
     `flex-end` departure to match `.admin-table-actions-grid`'s own
     sitewide left-aligned convention elsewhere, e.g.
     `/admin/tools/software` — Brian's own framing is that this is the
     alignment to try now, not a settled final call). Every row-action
     button, filled or outlined, shares one `btn_style` string
     (`font-size:12px;padding:5px 10px;`) and the shared `.btn`/
     `.btn-ghost` classes — the filled "Approve term" button no longer
     overrides `.btn`'s own `border:1px solid var(--navy)` with
     `border:none`, which is what broke its height parity with the
     outlined buttons beside it. Approve term moved out of the Actions
     cell entirely: it's now a plain third button in the default action
     row (Edit / Allow here / Approve term), and clicking it reveals a
     dedicated full-width panel — a second `<tr id="voice-approve-term-
     {id}">` with a `colspan="5"` cell spanning the whole table, shown/
     hidden via `voiceToggleApproveTerm` (mirroring `voiceToggleEditField`'s
     existing collapsed/expanded pattern) — holding a full-width text
     input prefilled with the detected term, the caption "Trim to the
     exact term first. Once approved, it's fine everywhere, permanently."
     (no em dash, no all-caps, replacing the old em-dash/all-caps
     wording), then Approve/Cancel; nothing is approved until that second
     click. Field and Source columns were compressed to fixed narrow
     widths (`_VOICE_COL_WIDTH_FIELD = 150`, `_VOICE_COL_WIDTH_SOURCE =
     100`, both down from reusing `_COL_WIDTH_NAME`/`_COL_WIDTH_STATUS`)
     with `nowrap`+ellipsis truncation and a `title` tooltip carrying the
     full value (`_voice_field_cell_html`) — the width freed up flows
     straight to Detail, which has no declared width of its own under
     `table-layout:fixed`; Actions was narrowed to `_VOICE_COL_WIDTH_
     ACTIONS = 280`, sized to the widest real case (an open bare-ampersand
     row's three buttons on one line). Two of the bulk-replace-ampersand
     feature's own rendered strings (the group bulk bar's button label,
     the preview page's H1/explanatory prose) had to be reworded away
     from a literal `&amp;`/`&amp;amp;` — "Replace ampersands with and,"
     never a literal ampersand character — since `webapp/app.py` is itself
     one of `linklib.voice_review`'s `VOICE_SCANNED_FILES`, and
     `_BARE_AMPERSAND`'s `&amp;` alternative matches unconditionally
     regardless of spacing; a page whose whole purpose is describing the
     ampersand character has to do it in words, not by rendering the
     character itself, or it fails its own lint (a real regression this
     round caught and fixed, surfaced by `tests/test_checks.py`'s live
     `run_all()` check, not by Brian's screenshots). New tests in `tests/
     test_voice_bulk_replace_ampersand.py` assert the Approve-term input
     is absent from the page except inside its own (initially hidden)
     panel row, and that every row-action button (scoped past the page's
     own nav-toggle hamburger, a legitimate non-row-action exception)
     carries the shared `.btn` class.
- **Voice review queue, issue #592 PR — pre-merge review fixes (2026-09) —
  `scripts/fix_invisible_characters.py --apply` now logs every change to
  `voice_review_queue`, the same way the write-time backstop does; and a
  standing note that its sibling script has an identical, still-open
  gap.** A pre-merge review of the #592-followups PR pushed back on the
  script: it did raw single-column `UPDATE`s with no queue trace at all,
  which is the exact "nothing changes quietly" violation this whole
  feature exists to close, just via a script instead of a live write
  path. Fixed by switching the script from a bare `sqlite3.connect(db_path)`
  to a real `Library(db_path)`, and calling `Library.log_voice_correction`
  — the same method `Library._vf` calls — immediately after each verified
  `--apply` write, with `source="script"` and the rule derived from the
  pre-fix text via `voice_mechanics.correction_rule_for` (never
  hardcoded, since a future caller of `strip_safe_invisible_chars` isn't
  guaranteed to be invisible-character-only forever). The settings branch
  follows `Library.set_setting`'s own convention exactly (`row_id=None`,
  the settings key stored as the "column"). **`scripts/
  fix_spaced_em_dashes.py` — the sibling script this one was built to
  mirror, and the other of the two scripts this PR's own post-merge
  instructions ask Brian to run — has the identical gap and was
  deliberately NOT fixed here**, since it wasn't part of the request that
  prompted this fix and touching it would have silently widened the PR;
  flagged explicitly so it isn't mistaken for already covered. Also
  confirmed, by actually running each script against a path that doesn't
  exist (not just reading the code): both scripts already exit nonzero
  with a clear "Database file not found" error and create no file,
  because both already call `resolve_db_path(..., allow_missing=False)`
  — the guard this repo built specifically after the July 2026 Corpay
  silent-write-to-nowhere incident (see "One-off admin fixes against the
  database" below) already covers this case for every script that uses
  it, this pair included. A regression test for that exact behavior was
  added to both scripts' test files anyway, since nothing had exercised
  it directly before. See `tests/test_fix_invisible_characters.py`'s
  `test_apply_logs_every_change_to_voice_review_queue`/
  `test_preview_never_writes_to_voice_review_queue`/
  `test_missing_db_exits_nonzero_and_never_creates_a_file`, and `tests/
  test_fix_spaced_em_dashes.py`'s own `test_missing_db_exits_nonzero_and_
  never_creates_a_file`, for the regression coverage.
- **`scripts/fix_spaced_em_dashes.py` closes the sibling gap the bullet
  above deliberately left open (2026-09).** Same fix, same shape: switched
  from a bare `sqlite3.connect(db_path)` to a real `Library(db_path)`, and
  every verified `--apply` write (both the per-table columns and the
  `settings` key/value branch) now also calls `Library.log_voice_correction`
  — `source="script"`, rule derived via `voice_mechanics.correction_rule_for`
  rather than hardcoded, matching `fix_invisible_characters.py`'s own
  pattern exactly, including the settings-row convention (`row_id=None`,
  the key stored as the "column"). Preview mode still writes nothing,
  including to the queue. See `tests/test_fix_spaced_em_dashes.py`'s
  `test_apply_logs_every_change_to_voice_review_queue`/
  `test_preview_never_writes_to_voice_review_queue` for the regression
  coverage — both scripts now leave the identical kind of trace a live
  save through `Library._vf` would have.
- **Voice review queue, remaining-tables follow-up (2026-09) — the five
  tables disclosed and named as a scope cut in the bullet above
  (`communities`, `community_profiles`, `benchmarks`, `thought_leadership`,
  `ai_surfaces`) are now instrumented the same way, plus the deferred
  invisible-unicode-character mechanical check.** Same pattern as before,
  mechanically repeated: `add_ai_surface`/`update_ai_surface`,
  `add_benchmark`/`update_benchmark`/`update_benchmark_content`,
  `add_thought_leadership`/`update_thought_leadership`,
  `add_community`/`update_community`/`update_community_content`, and
  `upsert_community_profile`/`update_community_profile_research_fields`
  all now call `self._vf(...)` (row id known — `update_community_profile_
  research_fields`'s dynamic `fields` dict just needed `self._vf(table,
  community_id, k, v)` inside its existing per-field loop, no structural
  change) or the after-insert `log_voice_correction(...)` pattern
  `add_original_content`/`add_category_feature` already established (row
  id not known until `cur.lastrowid` — every `add_*` method above). No
  table in this batch needed anything beyond that established shape —
  every write method already had a real, known-or-derivable row id to log
  against by the time `_voice_fix`/`self._vf` runs, so there was no case
  here of the "what would it take" structural gap the original PR brief
  flagged as a possibility. `tests/test_voice_fix_coverage_ci_guard.py`'s
  allowlist drops all five tables' add/update methods; what remains
  (`update_game_rank_settings`/`rename_tag` — not prose;
  `insert_mirrored_article`/`update_mirrored_article` — read
  already-normalized data back, never write fresh text; `__init__` — the
  schema-migration DDL list; `_migrate_community_local_markets` — a
  one-time backfill, not a live write path; `delete_tool_category`/
  `delete_community_category` — confirmed false positives, they only ever
  touch `categories_json` via an unrelated `SELECT` lookup line) is every
  disclosed exception from the original PR, unchanged — none of it is new.
  `tool_categories`/`community_categories`' own `add_*`/`rename_*` methods
  were never named in either PR's scope (they were never on the five-table
  list) and, at the time this bullet was first written, already called
  bare `_voice_fix()`, so the CI guard passed for them with no allowlist
  entry needed either way — closing that gap (adding real `self._vf`/
  queue-logging to those two tables too) was flagged as a genuine,
  still-open follow-up, not done here.

  **Coordinator review of this same PR caught the real problem that
  reasoning was resting on, closed in the same PR rather than a third
  one**: a bare `_voice_fix()` call was never actually equivalent to
  `self._vf`/`log_voice_correction` — it normalizes the text but never
  logs anything to `voice_review_queue`, so `add_tool_category`/
  `rename_tool_category`/`add_community_category`/`rename_community_category`
  were silently applying un-logged corrections the entire time, and the
  guard's own "already passes" framing above was true only because the
  guard's own check was too loose to catch it. Fixed two ways, together:
  (1) `tests/test_voice_fix_coverage_ci_guard.py`'s check now requires
  `self._vf(`/`self.log_voice_correction(` specifically — a bare
  `_voice_fix()` call is no longer sufficient, closing the loophole for
  good, not just for these four methods; (2) all four category methods
  were wired to `self._vf(...)`/the after-insert `log_voice_correction(...)`
  pattern, the same shape as every other method in this PR. **The
  tightened check also caught one more real, pre-existing gap**: `add_tool`
  itself — from the ORIGINAL voice-review-queue PR, not this one — called
  bare `_voice_fix()` on `description`/`summary` with no queue logging,
  never caught because the guard accepted a bare call as sufficient at the
  time. Fixed the same way (post-insert `log_voice_correction`, row id
  known via `cur.lastrowid`). None of `add_tool_category`/
  `rename_tool_category`/`add_community_category`/`rename_community_category`/
  `add_tool` remain in the allowlist — they were never legitimate
  exceptions, they were unwired write paths, and the allowlist is
  reserved for the former, never the latter.
  **Invisible/zero-width Unicode characters** — the mechanical check
  deferred from the original PR, described there as cheap and
  deterministic, motivated by a real production row
  (`category_features` id 104's own `definition`, ending in a zero-width
  space, U+200B) that every existing rule (banned words, filler,
  performative, bare ampersands, spaced em dashes) is structurally blind
  to, since none of them look for a character with no visible glyph at
  all. Added directly to `linklib.voice_review.mechanical_findings` (a
  small, curated `INVISIBLE_CHARS` dict — zero-width space/non-joiner/
  joiner, the zero-width no-break space/BOM, word joiner, the
  left-to-right/right-to-left marks, soft hyphen — same "add a real one
  when it turns up" discipline as `AMPERSAND_NAMES`/`AMPERSAND_ACRONYMS`),
  so it fires everywhere `mechanical_findings` already runs with zero new
  wiring: every `VOICE_SCANNED_FILES` source file via
  `webapp.checks`/`tests/test_voice_standards.py`, and every `_SCAN_TABLES`
  column via `voice_db_scan._scan_value` (which already calls
  `mechanical_findings` unconditionally for every scanned column, typography
  exemptions aside). Deliberately checked against the RAW, unmasked text —
  `_mask_rubric_enumerations`/lowercasing only ever matter for the
  banned-word rubric enumeration and don't remove or alter a zero-width
  character elsewhere in the string, so there's nothing to lose by
  checking the original. **Detection only, no auto-fix, no backfill** —
  `normalize_voice_mechanics` (the write-time backstop) is unchanged and
  still only fixes a spaced em dash, so an already-stored invisible
  character (including the real production row this item is built
  around) surfaces as a live `open`-status finding the next time the
  scanner/checks run, exactly like any other pre-existing scanner finding
  — it is not swept into `voice_review_queue` by any backfill step this PR
  runs, since the scanner itself already covers all `_SCAN_TABLES`
  (including the five newly-instrumented ones) and `scripts/
  backfill_voice_review_queue.py` already exists for that purpose,
  unmodified, if a future pass wants to run it again. **Not verified
  against the real production count** — this session has no production
  database access (the standing limitation noted throughout this doc); the
  count above is confirmed only against seeded test fixtures
  (`tests/test_voice_db_scan.py`), reproducing the id-104 shape directly.
  Getting a real production count is `scripts/backfill_voice_review_queue.py`'s
  own preview-mode job (or a live `/admin/voice/review-queue` load), run by
  someone with DB access — not fabricated here.
- **Voice review queue — a source/trigger taxonomy, and the seed-sync
  infinite-loop's fix (2026-09).** Investigation into a live production
  incident — `_seed_toolbox()`'s per-boot re-sync of `communities.notes`
  against `scripts/seed_communities.py`'s raw (pre-fix) spaced-em-dash
  source text, repeating a correction against an already-`_voice_fix`'d DB
  value on every single deploy — found the deeper problem: nothing on
  `voice_review_queue` recorded WHICH mechanism produced a given row, so an
  `auto_corrected` row from a genuine one-time admin typo and one from a
  silently-repeating startup-sync bug were indistinguishable in the review
  UI. Fixed with a new `voice_review_queue.source TEXT` column (a plain
  idempotent migration, no backfill for pre-existing rows — they read
  `source=NULL`, rendered as "unknown," never a blank cell) taking one of
  four values (extended to five in 2026-09, issue #592 item 3, which added
  `'scan'` for `reconcile_voice_review_queue()`'s own periodic background
  pass — see that bullet below for the full write-up): `'admin-edit'` (a
  human editing through an `/admin/*`, or an admin-gated public-looking,
  submit route), `'startup-sync'` (`_seed_toolbox()`'s own per-boot
  re-sync — the exact mechanism behind the incident), `'script'` (a
  one-off backfill/fix/migration script), or `'submission'` (a public,
  member-gated submission route, where the origin is known but is neither
  an admin edit nor a script/sync run).
  `Library._vf`/`log_voice_correction` both grew an optional `source`
  parameter threaded straight into the INSERT; all 28 `Library` write
  methods that already call either grew a matching `source: str | None =
  None` param (including `set_setting()`, since every `/admin/copy/*` field
  goes through it), and every real call site across `webapp/app.py` and
  every script that writes a voice-scanned column now supplies the correct
  value for its own calling context — `_seed_toolbox()`'s two re-sync calls
  (`update_community_content`, `update_benchmark_content`) and its
  one-time-per-empty-table category seeding pass `source="startup-sync"`;
  every `/admin/*` submit route passes `source="admin-edit"`; every
  one-off/backfill script (`scripts/regen_ai_drafted_fields.py`, `scripts/
  enrich_agent_taxonomy.py`, `scripts/enrich_community_profiles.py`,
  `scripts/seed_tools.py`, `scripts/seed_communities.py`, `scripts/
  seed_feature_taxonomy.py`, and the various one-off content-fix scripts)
  passes `source="script"`. **The two public, member-gated (not admin)
  submission routes — `POST /tools/submit`, `POST /tools/communities/submit`
  — pass `source="submission"`**, the fourth taxonomy value: an earlier
  draft of this feature left them unwired (`source=None`), reasoning
  neither is an admin-edit or a script/sync context and that a 4th value
  was unneeded scope — reversed on review, since `source=None` renders as
  "unknown" in the review-queue UI, indistinguishable from a genuine gap in
  coverage, when the provenance here is actually known and is exactly the
  content Brian most wants to review closely. `/admin/voice/review-queue`
  shows the source as a small muted badge per row (both the open/
  auto-corrected groups and the resolved/exceptions table share the same
  `_voice_review_row_html` renderer, so both get it for free).
  `scripts/backfill_voice_review_queue_source.py` (preview/`--apply`,
  write-then-read-back verified) is the one-off backfill for the two
  specific pre-existing rows this investigation started from — matched by
  `(table_name, column_name, an excerpt substring)`, never a blanket
  "every row with `source IS NULL`" sweep, since a genuinely different row
  logged before this column existed could have `source=NULL` for an
  unrelated, legitimate reason; not run against production as part of this
  PR, reserved for Brian via `railway ssh`. **A new, column-aware CI test**
  (`tests/test_seed_source_voice_scan.py`) closes the structural blind spot
  that let the original incident go undetected for as long as it did: the
  live `/admin/checks` DB scanner (`linklib/voice_db_scan.py`) only ever
  sees already-normalized text, since every write path runs `_voice_fix`
  before storing, so a violation sitting in a raw SEED SOURCE FILE (synced
  into the DB on every boot) was invisible to it forever. The new test
  reuses `voice_db_scan`'s own `_scan_value`/`_SCAN_TABLES` column-aware
  machinery directly against every seed source found in a full search
  (`scripts/seed_communities.py`'s `COMMUNITIES`/`CATEGORIES`, `scripts/
  seed_tools.py`'s `TOOLS`, `webapp/app.py`'s `_DEFAULT_BENCHMARKS`/
  `_DEFAULT_TOOL_CATEGORIES`, `scripts/seed_book_recommendations.py`'s
  `BOOKS`, and `scripts/seed_data/seed_category_features.csv`) —
  deliberately NOT folded into `linklib.voice_review.VOICE_SCANNED_FILES`,
  which treats a whole file as undifferentiated text with no per-column
  exemption and would flag a legitimate third-party-name ampersand (e.g.
  "Finance & Accounting for Bioscience") as a violation. **The scan found
  real, pre-existing violations well beyond the two rows this PR fixes** —
  19 in `COMMUNITIES` (mostly `&` in `demographic`), 19 in `TOOLS` (spaced
  em dashes in `description`), 1 in `BOOKS`, 4 in the category-features
  CSV — none in scope for this PR to fix (a mass unreviewed content rewrite
  is exactly what CLAUDE.md's "no copy rewritten without Brian seeing
  before and after" rule exists to prevent), so the test is a
  **baseline-regression guard**: each source's current count is a named
  constant, the test only fails on a future INCREASE, and every violation
  is printed on every run so the outstanding list stays visible for a
  future, human-reviewed cleanup pass. `scripts/seed_data/
  seed_review_queue.csv`/`seed_tool_feature_links.csv` and the two
  feature-framework JSON files feed tables (`feature_review_queue`,
  `tool_feature_links`) that aren't in `voice_db_scan._SCAN_TABLES` at all,
  so they're out of scope for this scan with no corresponding config to map
  a column-aware check onto — reported, not silently force-included or
  dropped. See ARCHITECTURE.md's matching section for the full write-up.
- **Voice review queue, PR #595 review round (2026-09) — the base
  durability guard/mirror-drift-detection PR wasn't done when it claimed
  it was.** Brian's review of the PR found two real gaps beyond what its
  own description claimed: (1) queue writes to `original_content.body_md`
  never fired `sync_original_content_article()` — the drift check on
  `/admin/checks` could eventually flag a stale mirror, but detection
  alone meant a queue Edit/Revert/bulk-ampersand-replace quietly created
  the exact drift it would then ask Brian to fix by hand; confirmed live
  via a real production finding (`original_content.body_md` id 36, open
  in the queue with no corresponding sync). Fixed by calling the sync
  directly from the three write paths (`webapp.app._resolve_voice_item_
  action`, `admin_voice_review_bulk_replace_ampersand_apply`), the same
  layer the two admin save routes already call it from — see the
  Published-Content Ingestion entry above and ARCHITECTURE.md's matching
  follow-up for the full write-up. (2) Part C items 1-4 (column widths,
  page layout, button style, labels) were reported as "already shipped in
  prior PRs" — literally impossible for item 2's lede copy, which was
  written for this PR — and re-verifying each against the brief found
  three more real gaps beyond what had actually shipped: the table's
  `min-width` floor was still the old bespoke 760px even though Field/
  Actions had both grown to 280px, squeezing Detail exactly the way the
  fix was supposed to prevent (fixed with a documented `_TABLE_FLOOR_
  XWIDE` exception, `_VOICE_TABLE_FLOOR`); the lede paragraph had no
  `max-width`, so it spanned the full `page-standard` container instead
  of matching the sitewide descriptive-lede pattern (e.g. `/admin/
  ai-surfaces`'s own lede) at `max-width:900px`; and three buttons
  ("Keep mine," and the Cancel button inside both the Allow-everywhere
  and Edit reveal panels) still carried `color:var(--muted)`, plus "Save
  edit" was still outlined instead of filled, contrary to the brief's own
  "the only filled buttons are Approve, Save edit." The Allow-everywhere
  panel's caption also still had placeholder text instead of Brian's
  exact specified wording — fixed verbatim, with "Dun & Bradstreet" added
  to `linklib.voice_review.AMPERSAND_NAMES` so the new example clears the
  typography lint. Every fix has a regression test in `tests/
  test_voice_bulk_bar_gating.py`, each confirmed to fail against the
  pre-fix code before being trusted — the bulk-bar (rule, status) gating
  itself (item 4 of the review) was re-verified and found already
  correct, with new tests pinning the exact button set per group type.
- **`_seed_toolbox()`'s sync loops have no manual-override guard — the
  exact class of bug the incident above surfaced, generalized (2026-09
  investigation, report-only, not fixed here).** Reading `_seed_toolbox()`
  directly (see its own docstring and body in `webapp/app.py`) confirms
  three sync loops, none guarded by anything like `logo_manual_override`:
  (1) `tools.name` — synced via a raw `UPDATE tools SET name=? WHERE id=?`
  (not routed through `Library`, so never logged to `voice_review_queue`
  either) whenever the live name differs from `scripts/seed_tools.py`'s
  `TOOLS` entry for that URL; `tools.description` was deliberately retired
  from this sync entirely after the 2026-08 incident (81/148 tools'
  AI-drafted descriptions silently reverted) and is NOT vulnerable. (2)
  `communities.name`/`communities.notes` — synced via
  `update_community_content()` whenever either differs from `scripts/
  seed_communities.py`'s `COMMUNITIES` entry for that URL (this is the
  exact mechanism the whole investigation started from). (3)
  `benchmarks.name`/`benchmarks.description` — synced via
  `update_benchmark_content()` whenever either differs from `webapp/app.py`'s
  `_DEFAULT_BENCHMARKS` entry for that URL (`scripts/
  seed_book_recommendations.py`'s `BOOKS` entries are NOT re-synced by
  `_seed_toolbox()` at all — only `_DEFAULT_BENCHMARKS` is). **The
  practical consequence, stated plainly**: for a tool/community/benchmark
  whose URL is present in the relevant seed-source file, `name` (all
  three tables), `communities.notes`, and `benchmarks.description` are
  effectively READ-ONLY from the admin UI's perspective — an admin's own
  hand-edit to any of these fields, made through `/admin/tools/software/edit`,
  `/tools/communities/{slug}/edit`, or `/admin/tools/resources/{id}/edit`,
  is silently reverted back to the seed-source text on the very next
  deploy/restart, with no warning anywhere in the UI that this will
  happen. `advisor` (tools and communities) is a narrower, one-directional
  variant of the same shape — only ever bumped `True` to match the seed
  list, never demoted, so a manual "turn advisor off" edit against a
  seed-listed True entry reverts too, just not in a full round trip.
  **No guard exists anywhere in `_seed_toolbox()` analogous to
  `tools.logo_manual_override`/`logo_override_stale`** (confirmed by
  reading the full function body — no check of any `*_manual_override`-
  style flag before any of the three sync loops' overwrite calls). The two
  category-vocabulary loops (`tool_categories`, `community_categories`) are
  NOT vulnerable to this — both are gated on "only seed when the whole
  table is empty," so they run exactly once, ever, on a fresh DB, and never
  re-touch a category's name/description again regardless of what the seed
  list says. **Compared against CLAUDE.md's own documented "intended"
  behavior** (the "CFO Toolbox startup sync" bullet above): the mechanism
  itself is described accurately there — `_seed_toolbox()` re-syncing
  `name`/`description`/`advisor` on every restart is stated as deliberate,
  explicitly framed around Brian occasionally renaming a seed-listed tool
  by editing `scripts/seed_tools.py` directly rather than through the admin
  UI. What that bullet does NOT say, and what this investigation surfaces
  as the real gap, is that the reverse path — an admin hand-editing one of
  these fields through the admin UI for a URL that's ALSO in the seed
  source — is silently unsafe, with no warning callout anywhere a person
  editing that form would see it. This is a documentation/UX gap, not a
  code contradiction: the doc isn't wrong about what the code does, it just
  never states the corollary risk. Not fixed as part of this investigation,
  per its own report-only scope — flagged for a future decision (a warning
  banner on the affected admin edit forms, or a `logo_manual_override`-style
  guard extended to these fields, are both real options, neither built
  here).

## Voice — em dash policy

The voice guide (`BRAND.md` §9, rubric text in `linklib.agent.VOICE_CORE_DEFAULT`)
is enforced two ways: mechanical (deterministic banned words/filler/performative
phrases, `tests/test_voice_standards.py`) and holistic (Claude judges tone on
demand, `/admin/voice`'s "Check content against your voice"). Em dash usage
doesn't fit either bucket cleanly — a scan of the live site found 63 lines of
Brian's own existing copy using spaced em dashes inconsistent with the rubric's
literal "no surrounding spaces" line, so a mechanical rule would false-positive
constantly, and what actually reads as off-voice (an em dash bolting a long
mechanism clause onto an already-complete sentence, especially with a nested
parenthetical) is a cadence judgment call, not a string match.

**So: whenever you write new site or admin copy that uses an em dash, flag it
to Brian explicitly before it ships**, rather than deciding yourself that it's
fine. Quote the full sentence the em dash appears in, plus the sentence(s)
immediately before and after it, so he has enough context to sign off without
having to go dig up the surrounding copy himself. He'll tell you to keep it or
give you an editorial rewrite. This applies to draft copy you're presenting
for review same as anything you're about to commit.

**Clarified rule (from the #134 em-dash cleanup round-trip):** a spaced em
dash ( — with spaces) is never allowed. An unspaced em dash is fine, and so is
a matched pair of unspaced em dashes bracketing a short aside or appositive
(e.g. "two acquisitions—Ansible to Red Hat, Tidelift to Sonar—both LOI to
close in under 45 days") when that's the most natural construction—don't
mechanically force it into parentheses or a colon just to get the dash count
down. What's actually off-voice is peppering em-dash asides into nearly every
sentence of a piece, or using one to bolt a long trailing clause onto an
already-complete sentence. Still flag it rather than deciding unilaterally;
Brian may prefer a comma or colon rewrite for a specific sentence even when
the em-dash form would otherwise be fine.

## Deployment

- **Host:** Railway, building from the `Dockerfile` (`python:3.11-slim`, runs
  `uvicorn webapp.app:app`). `Procfile` and `railway.toml` are also present;
  `railway.toml` sets the healthcheck to `/health`.
- **Deploy flow:** Railway auto-deploys from the **`main`** branch. Feature work happens on
  a branch and ships by **merging a PR** into `main` (see *Contributing* above).
  **If it isn't on `main`, it isn't live** — a common gotcha (e.g. a change that looks done
  but "doesn't show up" is usually still on a feature branch / unmerged PR).
- **Domain:** bmweis.com (and www) point at the Railway service; the
  `*.up.railway.app` hostname still serves too. `LINKLIB_PUBLIC_BASE` should be
  `https://bmweis.com` in Railway's env — it's the base URL baked into the
  bookmarklet, so **re-grab the bookmarklet from `/bookmarklet` after changing it**.
- Set `LINKLIB_SAVE_TOKEN` (and ideally `LINKLIB_SECRET_KEY`) in Railway's env so the
  private section is protected and logins persist across deploys. `library.db` is not in
  the repo, so a hosted instance starts empty unless the DB is provisioned/persisted
  separately.

## Models in use

- Enrichment (article summaries and every AI-drafted directory field — Description,
  Agent taxonomy, Competitive differentiation, Community profile fields, and so on):
  `claude-opus-5` by default, quality over cost — see "AI model selection" below for
  how this is now chosen and where it's overridable.
- Q&A and post drafting: `claude-sonnet-4-6` (better synthesis quality)
- Embeddings (hybrid retrieval, `linklib/embeddings.py`): OpenAI `text-embedding-3-small`

All three are overridable via environment variables; enrichment is also overridable
live from `/admin/system/ai` without a redeploy — see "AI model selection" below.
(This setting's own page moved there from a standalone `/admin/system/model`
in the admin AI-page consolidation, PR 10, 2026-09 — see that bullet below.)

**The model pickers are dynamic** (`linklib/models.py`): a single curated registry
feeds every picker (re-enrich, backfill), and `models_for` reconciles it
with the live Anthropic Models API — retired models drop off the lists on their own,
and newly released models surface on the chat pickers automatically. So there's no
longer a manual "check the current model IDs" step before a new model can be used:
add a row to `_REGISTRY` to give it a curated label/blurb, or just let the live list
surface it. The enrichment pickers stay curated (no auto-surfacing) so a whole-archive
re-enrich can't be pointed at an unexpectedly pricey new model by accident. When the
API/key is unavailable, every picker falls back to the static registry.

**FP&A Buddy (`/tools/fpa-buddy`) has no visible model picker.** The UI exposes only a
Quick/Standard/Deep effort choice; each tier maps internally to a model, an
archive/web-search count, and a token budget (`EFFORT_SETTINGS` in
`linklib/agent.py`). The model is an implementation detail, not a user-facing choice.

**AI model selection (2026-08) — enrichment's model is a live, DB-backed
setting, not just `LINKLIB_ENRICH_MODEL`.** Same reasoning and same page
pattern as `/admin/exa-settings`' Phase 7 kill switch: an env var needs a
redeploy to take effect, a DB-stored setting doesn't. `/admin/system/model`
(`Library.get_enrich_model`/`set_enrich_model`, a `settings` table row —
no new column) lets Brian pick any model from the same curated-registry-
reconciled-with-the-live-Models-API list every other picker already uses
(`linklib.models.models_for`), with a "Test connection" action mirroring
Exa's own (`linklib.enrich.test_model_connection`, one real minimal Claude
call, with a link to Anthropic's live model docs
(`platform.claude.com/docs/en/about-claude/models/overview`) right on the
page for whenever the options need re-checking against what Anthropic
currently ships). **Immediate correctness fix that motivated this, done
independently of the feature itself — and its own correction, in the same
PR:** the code's hardcoded fallback was actually `"claude-opus-5"` all
along, matching `linklib/enrich.py`'s own module docstring and every git
revision's stated intent ("Defaults to Opus for depth… quality matters more
than the per-article cost"). A first pass this build mistakenly "fixed" it
to `"claude-opus-4-8"` on the assumption `claude-opus-5` wasn't a valid
current model id — it is: confirmed directly against Anthropic's docs,
`claude-opus-5` is real, current, and Anthropic's own top recommendation for
complex/enterprise work, with a newer knowledge cutoff than Opus 4.8's.
Reverted back to `"claude-opus-5"`, the curated registry's actual "Best
quality"/"Deepest summaries" entry — flagging the reversal explicitly here
rather than silently, same precedent as the homepage "🚧 building" sticker
mix-up elsewhere in this doc. This table's env-var row above was also
independently stale (said Haiku, code always said Opus) and is corrected to
match. Every `linklib.enrich` generation call site invoked
from the live app (`webapp/app.py` — tool Description, Agent taxonomy,
Competitive differentiation, competitor-match judging for both Software and
Communities, Community profile fields, the Community basic-listing auto-fill,
and article-save enrichment in `linklib/pipeline.py`) now resolves
`lib.get_enrich_model()` explicitly at call time rather than relying on each
function's own `model: str = DEFAULT_MODEL` parameter default — a plain
Python default is baked in at import time, so a live DB-backed setting has
to be passed in explicitly on every call for the "no redeploy" promise to
actually hold. **Not touched**: the re-enrich/backfill admin pickers
(`linklib/models.py`'s own per-run model choice — a different, already-live
mechanism for a whole-archive pass) and every `scripts/*.py` CLI tool's own
`--model` flag/default — both already let the operator choose per-run, so
routing them through this new global default would just be a second,
redundant selection layer. **Defaults to the deepest/highest-quality curated
model** (`Library._DEFAULT_ENRICH_MODEL`, `"claude-opus-5"`) until an admin
picks something else — quality over cost for this use case, same reasoning
`linklib.models._REGISTRY`'s own "Best quality" blurb already states.
`/admin/overhead-spend`'s "Toolbox usage" section now names the active
enrichment model next to its existing AI-cost estimate, since model choice
directly affects that number.

**Admin AI-page consolidation (PR 10, 2026-09) — the standalone
`/admin/system/model` page above, the standalone `/admin/exa-settings` page
(the Exa kill switch — see the retrieval-mechanism bullet earlier in this
doc), and the standalone `/admin/system/ai-usage` page (see the AI usage/
config dashboard bullet below) all merge into one page, `/admin/system/ai`.**
All three old URLs 404 now — no redirect, no compatibility stub, same
"nothing was bookmarked externally" precedent every other admin
URL-restructure in this codebase has used — and the three hub-nav cards
they used to have (one under FP&A Buddy, two under System) collapse into
one, in System. The new page is split into two clearly separated sections:
**Configuration** (the same Enrichment-model dropdown and Exa toggle,
unchanged logic, now posting to `/admin/system/ai/model/*` and
`/admin/system/ai/exa/*`) and **Usage index** (the original read-only
`/admin/system/ai-usage` content, unchanged in substance). The retired
usage-only page's own "no `<form>` on this page" test is inverted, not
deleted, since editable config genuinely lives here now — see
`webapp.app.ai_config_editable_outside_ai_page()` (wired into
`/admin/checks` as "AI config consolidated") and its own disclosed
limitation (it can only catch a route reusing one of the three retired URL
shapes, not a brand-new differently-named mutation route). See
ARCHITECTURE.md's matching "AI configuration and usage" section and
`tests/test_admin_ai_settings.py` for the full write-up and regression
coverage — the old `tests/test_admin_exa_settings.py`/
`test_model_selection.py`/`test_ai_usage_dashboard.py` are folded into it.

### Adding a new Claude model — every touchpoint

**Investigated (issue #98, Piece 1, 2026-09) after #506 shipped a CI test guarding
registry-vs-pricing drift — the question was whether "add a new model" is genuinely
a one-string change once that test exists. It isn't.** `linklib/models.py`'s own
docstring used to claim its `_REGISTRY` is what "every model picker (Q&A, LinkedIn
posts, re-enrich, backfill)" renders from — both false as of this investigation:
LinkedIn post drafting was removed outright in #95, and Q&A (FP&A Buddy) never reads
the registry at all. The docstring is corrected; this section is the real list.

There are (at least) five separate places a model id can need adding or updating,
not connected to each other and not connected to `linklib/models.py`'s registry
except the first one:

1. **`linklib/models.py`'s `_REGISTRY`** — feeds exactly three admin surfaces:
   `/admin/system/ai` (the live enrichment-model setting, since the admin
   AI-page consolidation, PR 10 — see the Key architecture decisions bullet
   above), the re-enrich
   picker, and the backfill picker. Adding a row here is sufficient — and only
   sufficient — for those three. `models_for(allow_new=True)` also auto-surfaces
   a genuinely new model on the two chat-oriented pickers without a code change,
   but the enrichment pickers stay curated on purpose (no auto-surfacing, so a
   whole-archive re-enrich pass can't land on an unexpectedly pricey model by
   accident).
2. **`linklib/agent.py`'s `EFFORT_SETTINGS`** — FP&A Buddy's actual Quick/
   Standard/Deep -> model mapping, a completely separate hardcoded dict with no
   link to the registry at all. This is the real "model selector" for Buddy,
   even though there's no dropdown — Buddy's UI only ever shows the three effort
   tiers (`linklib.agent.EFFORT_SETTINGS`; see "FP&A Buddy has no visible model
   picker" above). Putting a model in the registry does **not** make it reachable
   by a Buddy question; only editing this dict does. Confirmed live: `EFFORT_SETTINGS`'s
   "deep" tier already runs `claude-opus-4-8`, a model that was never in
   `_REGISTRY` at all — proof the two have always been independent, not a new gap.
3. **`linklib/agent.py`'s `COST_ESTIMATES`** — the rough pre-call cost estimate
   shown on `/tools/fpa-buddy`, keyed by canonical model id then effort tier.
   `COST_ESTIMATES.get(model, {}).get(tier)` degrades silently to `None` (a blank
   cost estimate in the UI) for a tier pointed at a model missing here — no error,
   no crash, just quietly wrong/missing UI.
4. **`linklib/pricing.py`'s `MODEL_PRICING`** — the real per-call USD cost table
   real spend is recorded against (drives per-user dollar caps). An unlisted model
   silently falls back to Sonnet 4.6 rates rather than $0 or an error (see that
   module's own docstring) — a live-cost-accuracy bug, not cosmetic. #506's CI
   test (`test_every_registry_model_has_a_pricing_row`) only ever checked this
   against the *registry*; a follow-up test
   (`test_every_effort_tier_model_has_a_pricing_row`/
   `test_every_effort_tier_model_has_a_cost_estimate_row`, `tests/test_pricing.py`)
   now also checks it against `EFFORT_SETTINGS`, since a tier's model can (and
   does, per point 2) live outside the registry entirely.
5. **Assorted standalone `DEFAULT_MODEL` fallbacks** —
   `linklib/agent.py`, `linklib/matchmaker.py`,
   `linklib/dedupe.py`, `linklib/enrich.py`, and
   `linklib/embeddings.py` each declare their own `os.environ.get("LINKLIB_..._MODEL",
   "<hardcoded literal>")` default. None of these read the registry, and
   `enrich.py`/`embeddings.py` each pick their own deliberately
   different literal for a deliberately different reason (see the
   model-config-consolidation bullet below) — each is its own literal to
   update if that default itself (not a picker option) should change.
   (`linklib/queue.py`'s own `QUEUE_ENRICH_MODEL`, and `linklib/suggest.py`'s
   own fallback, were retired along with the Archive Queue itself — 2026-09,
   PR 3 — so this list is now five modules, not seven.)
   `linklib/agent.py`'s `MODEL_ALIASES` (friendly short names like
   `"opus"`/`"sonnet5"` -> canonical id) is a sixth, smaller list in the same
   category — it's used internally by `agent.py` itself (`REWRITE_MODEL`,
   `ask()`'s own `model=` resolution), not only by `scripts/ask.py`'s
   `--model` flag as this doc once assumed; stays in `agent.py`, not moved.

MCP tools (`ask_fpa_buddy`, `ask_matchmaker`) were checked too and don't add a
seventh touchpoint — neither exposes a model parameter; both proxy straight into
the same `EFFORT_SETTINGS`/matchmaker `DEFAULT_MODEL` machinery in-process (Phase 5),
so point 2 already covers them.

**Bottom line: not a one-string change.** A new model that should power FP&A Buddy
needs edits to (at least) `EFFORT_SETTINGS`, `COST_ESTIMATES`, and `MODEL_PRICING` —
three separate dicts in two files, none of which the registry reconciles for you.
A new model that should only be selectable for enrichment/re-enrich/backfill needs
only the registry (plus a `MODEL_PRICING` row, now enforced by CI either way).
**No further automation was built for this** beyond the CI-test extension above
(explicitly scoped and approved before building, issue #98 Piece 1) — unifying
`EFFORT_SETTINGS`/`COST_ESTIMATES` into the registry-reconciliation mechanism
`models_for` already has would be a real, riskier redesign (Buddy's tiers are
deliberately curated/manual, the same reasoning the enrichment pickers already use
for staying non-auto-surfacing) and was left as a possible future decision, not
something to do silently as part of this reminder.

**New-model-awareness reminder (issue #98, Piece 2)** — same reviewed-toggle
pattern as the Pricing-freshness reminder directly above (a dated `settings` value,
a banner on `/admin/checks` separate from the pass/fail list, an admin-only "Mark
reviewed" action, no auto-clear-on-view), but answering a different question and
kept as a fully separate, independent reminder: not "has an existing model's price
gone stale" but "does Anthropic have current models this app doesn't know about at
all." There's no API for that either (`models.list()`, per `linklib/models.py`'s
own `_live_models`, only ever returns models already deployed/visible to this
account — a consequence of a model having been added somewhere already, never a
way to discover a brand-new release), so this stays a human attestation too.
`linklib.models.MODELS_REVIEW_STALE_DAYS` = **30 days** (vs. pricing's 90) —
confirmed with Brian: new models ship roughly every 30-60 days, meaningfully more
often than an existing model's price changes, so the review window is tighter.
Settings key `models_last_reviewed`; `POST /admin/checks/mark-models-reviewed`
records it. The banner links out to both Anthropic's live model-overview docs and
back to this section (so "what do I actually need to touch" doesn't need
re-deriving each time it goes stale).

**Model-config consolidation (2026-09, follow-up to issue #98's touchpoint
investigation above) — the "default chat model" literal, deduplicated where it
was genuinely duplicated; left alone everywhere it wasn't.** Point 5 above
flagged five separately-hardcoded `"claude-sonnet-4-6"`/`os.environ.get("LINKLIB_CHAT_MODEL",
...)` fallbacks (`linklib/agent.py`, `linklib/matchmaker.py`, `linklib/suggest.py`,
`linklib/dedupe.py`, plus `linklib/queue.py`'s differently-valued
`QUEUE_ENRICH_MODEL`) as drift risk, same shape as the pricing/registry gap
#506 closed — a real default duplicated in multiple places with nothing
keeping them in sync. Investigated per-site before touching anything, rather
than assuming uniformity:
- **`agent.py`, `matchmaker.py`, `suggest.py` were genuinely identical** — same
  env var (`LINKLIB_CHAT_MODEL`), same literal, no stated reason to differ.
  Consolidated into one new `linklib.models.DEFAULT_CHAT_MODEL =
  "claude-sonnet-4-6"` constant, which all three now pass as the fallback
  argument to their own unchanged `os.environ.get("LINKLIB_CHAT_MODEL", ...)`
  call — behavior-identical (confirmed live: with/without `LINKLIB_CHAT_MODEL`
  set, each module's resolved default is byte-identical before and after).
- **`dedupe.py` shares the same *ultimate* default but has a real, deliberate
  extra override layer** (`LINKLIB_DEDUPE_MODEL`, checked before
  `LINKLIB_CHAT_MODEL`) — not drift, an intentional per-feature knob. Kept
  exactly as its own two-level `os.environ.get(...)` chain; only its
  innermost hardcoded literal now points at the shared
  `DEFAULT_CHAT_MODEL` constant instead of re-typing the string.
- **`queue.py`'s `QUEUE_ENRICH_MODEL` (`"claude-opus-4-8"`) is untouched, as
  scoped** — a deliberately different, more capable model for a deliberately
  different task class (background enrichment depth, same "quality over
  cost" reasoning as `enrich.py`'s own `DEFAULT_MODEL`), not a copy of the
  chat default that happened to diverge. Forcing it onto the shared constant
  would be false consistency, not a fix. (`linklib/queue.py` itself no
  longer exists — the whole module, `QUEUE_ENRICH_MODEL` included, was
  retired along with the Archive Queue — 2026-09, PR 3 — so this bullet is
  now purely historical: at the time this decision was made, leaving it
  untouched was the right call, and the module simply isn't there to touch
  any more.)
- **`linklib/agent.py`'s `MODEL_ALIASES` stays in `agent.py`** — the task's
  original premise (flagged as one of five things to re-verify, not assumed)
  that it's "used only by a CLI script" turned out to be wrong: it's used
  internally by `agent.py` itself (`REWRITE_MODEL = MODEL_ALIASES["haiku"]`,
  and `ask()`'s own `model=` resolution at
  `MODEL_ALIASES.get(model, model)`) — `scripts/ask.py` never imports it at
  all, it just passes a raw string through to `agent.ask()`, which resolves
  the alias internally. Moving a lookup table this tightly coupled to
  `agent.py`'s own resolution logic would be pure churn with no drift-risk
  benefit, so it was left in place.

`linklib/models.py` has zero `linklib`-internal imports of its own (a leaf
module), so importing `DEFAULT_CHAT_MODEL` from it into `agent.py`/
`matchmaker.py`/`suggest.py`/`dedupe.py` carries no circular-import risk —
confirmed by actually importing all five touched modules together, not just
reasoned about. `queue.py` and every other model-default site in point 5
above (`enrich.py`, `embeddings.py`) are unmodified. This is a
where-the-default-is-defined refactor only — no change to what model is
actually used anywhere, no changes to `EFFORT_SETTINGS`/`COST_ESTIMATES`/the
pricing or registry CI tests (already addressed by the new-model-awareness
PR above).

**`/admin/system/ai`'s Usage index section (originally the standalone
`/admin/system/ai-usage` page, merged in PR 10, 2026-09) is a read-only
index over everything in this section** — which model/mechanism powers
each Claude/Exa/OpenAI surface, live vs. code-only, and a link to wherever
it's actually changed (the Configuration section directly above it on the
same page, or `/admin/checks`), plus a compact status glance on the three
freshness reminders above. No dollar totals — those stay at
`/admin/overhead-spend`, which this page links to.
See ARCHITECTURE.md's "AI usage/config dashboard" section for the full
write-up.

## Billing note

Three separate billing relationships:
- **Building the site** → Claude Pro subscription (flat monthly, covers Claude Code)
- **Running the app (Claude)** → Anthropic API key, pay-per-use (`ANTHROPIC_API_KEY`)
- **Running the app (embeddings)** → OpenAI API key, pay-per-use (`OPENAI_API_KEY`) —
  embed-on-save/backfill cost is overhead; the `text-embedding-3-small` rate is a
  fraction of a cent per thousand articles

Keep the app's API keys out of Claude Code building sessions (don't set them in the
terminal where you run `claude`), or Code will bill the API key instead of the
subscription.
