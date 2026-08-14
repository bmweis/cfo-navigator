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
  queue.py         # fills the Archive Queue from RSS (ongoing) + sitemaps (backfill)
  suggest.py       # Claude-predicted keep/skip for queue candidates (advisory only)
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
  archive/            # (Phase N) one-time migrations and closed-investigation reports
                      #   whose job is done — kept for history via `git mv`, never run
                      #   again in the ordinary course

# The full recurring/actively-useful script inventory (purpose, cadence, required env vars,
# exact invocation) lives at /admin/system/scripts, not here — that admin page is the live
# reference; this tree only sketches scripts/'s shape. See "Documentation" below for the
# standing rule that keeps that page in sync with scripts/.

webapp/
  app.py           # FastAPI, ~110 routes, all HTML/CSS/JS inline: public site
                   #   (/, /thought-leadership [+ /thought-leadership/growth-engine-ratio,
                   #   /thought-leadership/ai-hackathon-playbook, /thought-leadership/netsuite-mcp],
                   #   /tools, /contact, /play) + private tools
                   #   (/tools/fpa-buddy, /save, /api/search, /bookmarklet — plus
                   #   /library/archive, /library/feed, and /read, all admin-only)
                   #   + auth (/login, /logout) + the /admin back office (~40 pages)
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
- **Embedding costs are split by who pays for them.** Embed-on-save/backfill cost is
  Brian's overhead (`article_embeddings.cost_usd`) and never touches a user's Ask
  budget. Embedding the retrieval QUESTION at ask-time is a user-cap cost — it folds
  into `ask_questions.cost_usd` the same way the follow-up query-rewrite's cost already
  does (`embed_cost_usd` breaks out its share). A general ledger covering overhead
  spend more broadly (enrichment included) is deferred — see issue #105.
- **`preferred_sites.opml` is dual-purpose.** It's both the web-search allowlist and the
  `/library/feed` reader's subscription list. Use direct RSS/Atom URLs — Feedly proxy URLs
  (`feedly.com/web/...`) are skipped because they require auth. Paywalled sources are
  tagged in `feed.py` (`PAYWALLED_DOMAINS`) and shown with a badge; the in-app reader is
  disabled for them.
- **The `/library/feed` reader caches per-feed for 30 minutes** (`feed.py`, in-memory). Cached
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
  only way this was caught was checking `/admin/library/backup`'s banner and history table
  directly against what actually landed, not trusting an exit code. Two more gaps closed
  in the same phase: (1) every backup attempt, success or failure, now writes a row to the
  new `backup_log` table (`Library.record_backup_attempt`/`list_backup_log`) from inside
  `backup.py` itself, rather than only `print()`ing to stdout where nothing in the app
  could see it; `/admin/library/backup` reads that table for a status banner (green/amber/
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
  `/admin/library/backup` shows a live link to whichever folder is currently in use. The
  old hand-made "Library Backup" folder is abandoned, not deleted or referenced anywhere.
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
- **Thought Leadership Admin CRUD, Phase 1 — the four `/thought-leadership`
  columns (Writing, Speaking & Events, Podcasts, Press) are now admin-managed,
  not hardcoded.** Phase 0 investigation found all four columns reading from
  one shared Python module, `webapp/thought_leadership_data.py` (33 hand-
  written `TLItem` dataclass instances across four module-level lists, no DB
  table, no admin surface — edited directly by hand each time). Phase 1
  replaces that with a `thought_leadership` table (see `linklib/db.py`'s
  table comment and `ARCHITECTURE.md`'s Thought Leadership section for the
  schema) and a new admin section, `/admin/thought-leadership` — add/edit/
  delete across all four types from one filterable list, same CRUD pattern
  as `/admin/tools/benchmarks`. `scripts/archive/migrate_thought_leadership.py`
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
  public page, but it isn't editable via `/admin/thought-leadership`.
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
  icon in the `/admin/thought-leadership` list row whenever a non-blank
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

See the **Authentication & security** section below for the full access-control model —
it supersedes the old "`/save` is token-gated" note.

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
  - Public (no auth): `/`, `/thought-leadership`, `/thought-leadership/growth-engine-ratio`,
    `/thought-leadership/ai-hackathon-playbook`, `/thought-leadership/netsuite-mcp`, `/contact`,
    `/privacy`, `/login`, `/logout`, `/static/*`, `/health`. (The old flat `/growth-engine-ratio`,
    `/finops-ai-hackathon`, `/netsuite-mcp` URLs 301-redirect to the nested paths above.)
  - Private HTML pages → **redirect to `/login`** when signed out: `/tools/fpa-buddy`,
    `/admin/contacts` (member-gated), and
    `/library/archive`, `/library/feed`, `/read` (**admin-only**, Phase 1 — see the
    Library access-level note in Key architecture decisions above). (The old flat
    `/archive`, `/feed` URLs 301-redirect to their nested equivalents above,
    unconditionally — the redirect itself carries no gated content, so it fires
    even signed-out; where it lands is what's gated. `/library/ask` and
    `/library/past-questions` were retired outright in Phase 2 — see the
    Library/Toolbox Phase 2 note in Key architecture decisions above — along
    with the old flat `/questions` redirect stub that pointed at the latter;
    `GET /ask`'s redirect stub is gone the same way, but the bare `/ask` path
    now 405s rather than 404s since `POST /ask` still lives there.) The
    `/library` hub route was removed outright in Phase 1 — no redirect.
  - Private API → **401** when unauthenticated, but also accept a valid token (cookie OR
    `X-Save-Token`/`?token=`): `/ask`, `/post`, `/feed/save`, `/api/search`.
  - `/save` is **token-only** (`X-Save-Token` header or `?token=`) because the bookmarklet
    calls it cross-origin, where the login cookie can't be sent.
- **No secret in rendered HTML.** Internal links no longer carry `?token=`; the cookie
  authorizes navigation. Token comparison is constant-time (`hmac.compare_digest`).
- **⚠️ Bookmarklet caveat (by design).** The `/bookmarklet` snippet embeds
  `LINKLIB_SAVE_TOKEN` in plaintext JS — it must, because it runs on third-party pages
  cross-origin where the cookie is unavailable. The `/bookmarklet` *page* is login-gated
  so only Brian can retrieve it, **but the snippet itself is a secret.** Don't paste it
  publicly, and **if you rotate `LINKLIB_SAVE_TOKEN`, re-grab the bookmarklet** (the old
  one stops working).
- `/static/{filename}` resolves through `os.path.basename` to block path traversal.

## `library.db` is intentionally not in the repo

It's Brian's personal reading history (~1,500+ articles). It lives beside the code
locally (on a Railway volume in production) and should never be committed —
`.gitignore` covers it (`library.db` + `*.db`). Off-site weekly backups go to
Google Drive when the `GOOGLE_OAUTH_*` vars are set (see `.env.example`).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for enrichment, Q&A, and post drafting |
| `OPENAI_API_KEY` | — | Required for embed-on-save, `embed_backfill`, and the vector half of hybrid retrieval. Absent → FTS5-only, no error. |
| `EXA_API_KEY` | — | Exa search API key for FP&A Buddy's preferred web retrieval mechanism (`linklib/agent.py`'s `retrieve_exa`). Absent, or the `exa_enabled` setting toggled off at `/admin/exa-settings` → Claude's native `web_search_20250305` tool handles the web tier instead (Phase 7 kill switch); web search itself is never disabled, only which engine runs. No error either way. |
| `BRANDFETCH_API_KEY` | — | Brandfetch **Brand API** Bearer token, required only for `scripts/backfill_logos.py --apply` (CFO Toolbox logo backfill, Phase D). A different product/credential from `BRANDFETCH_CLIENT_ID` below — do not confuse them. |
| `BRANDFETCH_CLIENT_ID` | — | Public client ID for Brandfetch's free CDN Logo API (`cdn.brandfetch.io`). Kept for reference/potential future browser-embed use, but **not** used by the logo backfill — that product is browser-embed-only and blocks programmatic access (see the Key architecture decisions bullet above). |
| `LINKLIB_EMBED_MODEL` | `text-embedding-3-small` | OpenAI embedding model for `linklib/embeddings.py` |
| `LINKLIB_DB` | `library.db` | Path to the SQLite database |
| `LINKLIB_SAVE_TOKEN` | (none) | Token for `POST /save` + bookmarklet; also the default login password. Set when hosted. |
| `LINKLIB_PASSWORD` | = `LINKLIB_SAVE_TOKEN` | Login password for the private section. Set to decouple the login password from the save token. |
| `LINKLIB_SECRET_KEY` | = password | HMAC key for signing session cookies. Set on the host so logins survive restarts/deploys. |
| `LINKLIB_ENRICH_MODEL` | `claude-haiku-4-5-20251001` | Claude model for enrichment |
| `LINKLIB_CHAT_MODEL` | `claude-sonnet-4-6` | Claude model for Q&A and post drafting |
| `LINKLIB_PUBLIC_BASE` | `http://localhost:8000` | Base URL embedded in the bookmarklet |
| `LINKLIB_SITES_OPML` | `preferred_sites.opml` | OPML path — web-search allowlist AND `/library/feed` source list |
| `GOOGLE_OAUTH_CLIENT_ID` | — | Google Cloud OAuth client ID. Required (with the two below) for `linklib/backup.py`'s weekly off-site Drive backup and `linklib/email_utils.py`'s outbound contact-form email — one client, both scopes. Absent → both features are a safe no-op, no error. |
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
  Growth Engine Ratio page + calculator (`/thought-leadership/growth-engine-ratio`), contact (`/contact`)
- Password login for the private section (`/login` + signed session cookie)
- CFO Feed RSS reader (`/library/feed`) with category tabs, per-source filter, save-to-library
- Article reader, Instapaper-style (`/read`)
- Hosting/deployment on Railway (see Deployment below)
- bmweis.com custom domain pointed at Railway (July 2026)
- MCP server (`scripts/mcp_server.py`) wrapping `/api/search` for Claude Desktop/Code
- Weekly off-site Drive backup, scheduled via GitHub Action (Phase O — see Key
  architecture decisions above), with a persistent `backup_log` audit trail and a
  status banner + history table on `/admin/library/backup`

**Not yet built (from the migration plan):**
- iOS Share Sheet shortcut

Note: the migration plan document (`MIGRATION_AND_BUILD_PLAN.md`) was never
committed to the repo — this item is tracked here as the only record of it.

## Contributing — pull requests

**All changes ship via pull request. Never push or merge directly to `main`.**
Work on a feature branch, push it, and open a PR into `main`; let the QA workflow
(`tests` + `secret-scan`) run, then merge the PR. This keeps every change reviewable
and traceable, and the **Checks** admin page (`/admin/checks`) mirrors what the PR
must pass. (Enforced server-side by a branch-protection rule on `main` that requires
a PR and passing checks — the convention here so tooling/agents follow it regardless.)

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
   route changes.
4. **The Communities feature reference** — a collapsible "How this works"
   block at the top of `/admin/tools/communities` (`_COMMUNITIES_REFERENCE_HTML`
   in `webapp/app.py`) documents every user-facing prompt/CTA/copy block
   across the Communities feature plus how the `cfo_visitor` anonymous
   tracking mechanism works. It's static reference content, not a DB-backed
   editable field, same reasoning as why BRAND.md §9 stays hand-edited prose.
   **Any PR that changes Communities-feature copy** (new CTA wording, new
   form states) **or tracking mechanics** (new cookie fields, new tables,
   retention changes) **must update this block in the same PR** — this is
   the same discipline as rules 1-3 above, so a forgotten prompt or a
   drifted tracking description doesn't become the next thing this rule set
   has to fix retroactively.
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

## Voice — em dash policy

The voice guide (`BRAND.md` §9, rubric text in `linklib.agent.VOICE_CORE_DEFAULT`)
is enforced two ways: mechanical (deterministic banned words/filler/performative
phrases, `tests/test_voice_standards.py`) and holistic (Claude judges tone on
demand, `/admin/brand`'s "Check content against your voice"). Em dash usage
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

- Enrichment: `claude-haiku-4-5-20251001` (cheap, processes thousands of articles)
- Q&A and post drafting: `claude-sonnet-4-6` (better synthesis quality)
- Embeddings (hybrid retrieval, `linklib/embeddings.py`): OpenAI `text-embedding-3-small`

All three are overridable via environment variables.

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
