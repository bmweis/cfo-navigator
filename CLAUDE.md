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
                   #   the merged Reader, /read and /read/{article_id}, admin-only)
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
  subscription list behind `/read`'s Feed quick view. Use direct RSS/Atom URLs — Feedly
  proxy URLs (`feedly.com/web/...`) are skipped because they require auth. Paywalled
  sources are tagged in `feed.py` (`PAYWALLED_DOMAINS`) and shown with a badge; the
  in-app reader is disabled for them.
- **Feed management (`/admin/library/feeds`) — the OPML file is now GENERATED from
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
  instead of defaulting to "nothing is excluded." **A feed's `xml_url` is stored and
  regenerated verbatim** (only `.strip()` for surrounding whitespace) because a paid
  subscription's feed URL can carry a per-subscriber token, and a normalized token is a
  silently dead feed; the per-row section dropdown and read-only checkbox are backed by
  deliberately narrow one-column update methods (`move_feed_to_section`,
  `set_feed_excluded`) so neither can rewrite a URL in passing. **Sections are pure
  grouping** — one flat feed table on the page, with a separate "Manage sections" area
  for add/rename/remove and no per-section settings at all. Adding a feed validates it
  server-side first
  (`feed.probe_feed()`), rejecting Feedly proxy links by name since those save cleanly
  and then produce nothing forever; editing re-probes only when the URL actually
  changed, so a rename doesn't fail because the source is down that day. **Known
  asymmetry, stated in the page copy rather than fixed:** the Reader's Sources rail is
  built from fetched items, not from the subscription list, so a quiet or unreachable
  feed shows on the admin page and not in the rail.
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
  `/admin/library/backfill-content`: a new `articles.content_html` column
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
- **Thought Leadership — click-to-expand descriptions.** `description` has
  been editable via `/admin/thought-leadership` since Phase 1, with 32+
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
- **Phase 6 — Layout Width Fixes, Admin Nav Restructure, Library Admin
  Cleanup.** Three coupled pieces in one PR (Library's management entry
  point moves as part of the nav restructure, so splitting wasn't clean).
  `/about`'s bio column had a real centering bug (bare inline
  `style="max-width:760px;"` instead of `.tool-prose`) — fixed. A live
  pixel-measurement check found `/tools` and `/admin`, also flagged as
  possibly affected, were already correctly centered — no fix needed there,
  confirmed rather than assumed. `/admin`'s right column now mirrors the
  public nav's order (Thought Leadership, then an expandable CFO Toolbox —
  Software/Toolbox categories/Benchmarking resources/Communities/Sail Don't
  Row settings plus a nested FP&A Buddy sub-group and a Library link, then
  "Brand, voice, and content", then System unchanged). `/admin/library`
  dropped "Open Reader" from its tool list (now reachable via a dedicated
  callout at the top of the page, and via Admin's CFO Toolbox &rarr;
  Library) and merged Historical Sweep into Archive Queue as a collapsible
  panel — `/admin/library/backfill` now 301s to `/admin/library/queue`; the
  underlying `POST .../backfill/start` and `GET .../backfill/status` routes
  are unchanged. The remaining 7 Library tools are grouped into three
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
    `X-Save-Token`/`?token=`): `/ask`, `/post`, `/feed/save`, `/api/search`,
    `/library/{article_id}/tags` (the Reader's inline tag editor, Phase 5c —
    the route predates it but had no callers until then).
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
| `LINKLIB_SITES_OPML` | `preferred_sites.opml` | OPML path — web-search allowlist AND `/read`'s Feed-view source list |
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
- Merged Reader (`/read`, Phase 5): a three-pane Feed/Saved/Read Later view with category
  and per-source filtering, an AJAX-loaded article pane, save-to-library, and a
  standalone single-article view at `/read/{article_id}`
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
