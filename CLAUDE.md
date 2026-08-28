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
                   #   (/, /thought-leadership [+ /thought-leadership/growth-engine-calculator,
                   #   the one remaining literal bespoke /thought-leadership/* route — see
                   #   Original Content Phase 4c],
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
  but nothing on `/admin/library/feeds` showed that a feed depended on one, or which env
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
  Surfaced as a "Flagged at save" tile on `/admin/library/backfill-content`,
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
  `POST /admin/library/backfill-content/{id}/accept` and `.../unaccept` are
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
  "Pre-backup integrity check" banner on `/admin/library/backup` — coral on
  failure (not amber; a blocked backup isn't a routine/expected state),
  seafoam on ok — sits above the existing backup-status banner rather than
  merging into it, since "the backup succeeded" and "the DB is structurally
  sound" are two different facts. See ARCHITECTURE.md's `integrity_check_log`
  table row and `backup_now()`'s docstring for the full write-up.
- **Durability audit item 3 — a durable start/finish record for the three
  `_JOB_STATE`-backed background jobs, so a redeploy or crash doesn't erase
  whether re-enrich, Historical sweep, or the Reader content backfill last
  succeeded, failed, or ever ran.** `_JOB_STATE` (`webapp/app.py`) is an
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
  schema) and a new admin section, `/admin/thought-leadership` — add/edit/
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
  CRUD at `/admin/original-content`, plus an "Original Content" box beside
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

- **Original Content, Phase 3 — admin CRUD at `/admin/original-content`.**
  Read `/admin/thought-leadership`'s existing list/add/edit/delete routes
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
  `/admin/original-content` — `count_label` is `len(items)`, so the badge
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
  - **Investigated, not deleted — the 3 Writing-column `thought_leadership`
    rows duplicating the flagship pieces.** This session has no access to
    the live `library.db` (same Railway-volume-only limitation as above),
    so the exact row `id` values weren't confirmed directly — Brian can
    find them at `/admin/thought-leadership?type=writing` by matching the
    three titles/URLs in the task description. A full codebase search found
    no sitemap generator, RSS/Atom feed, or other producer for the site
    itself (the only "sitemap" code in this repo is the unrelated Archive
    Queue's historical-backfill sitemap *crawler*, over an entirely
    different table). No test asserts a specific count or title against the
    *production* `thought_leadership` table tied to these 3 rows —
    `test_migration_script_moves_32_of_33_entries`'s `writing: 6` count
    (mentioned above) is a fresh-temp-DB migration test, unrelated to
    production row counts. The only two live readers beyond
    `/admin/thought-leadership`'s list/edit views are `/thought-leadership`'s
    own Writing column (`list_thought_leadership(type="writing")`) and the
    homepage's "Recent highlights" grid
    (`get_thought_leadership_representative("writing")`) — deleting the 3
    rows is safe for both: the Writing column just shows one fewer
    (duplicate) entry, and if one of the 3 happens to currently be the
    `writing` representative, deletion falls back to the most-recent
    remaining entry per that function's existing fallback rule (not a bug,
    just a different pick). **Confirmed safe for Brian to delete via
    `/admin/thought-leadership` himself — no code change needed for this
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

- **Original Content admin — width fix + Preview link.** `/admin/original-content/{id}/edit`
  (and `/admin/original-content/new`) rendered in `.page-form` (640px) — the
  same tier used for one-column public-facing forms like `/contact` — even
  though every other admin data-management page (`/admin/tools/software`,
  `/admin/original-content`'s own list view) uses the wider `.page-admin`
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
  Software/Toolbox categories/Resources/Communities/Sail Don't
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
    calls it cross-origin, where the login cookie can't be sent. It also carries a
    dedicated, `/save`-only CORS middleware (`_save_cors` in `webapp/app.py`, 2026-08
    wrap-up sprint item 2) so that cross-origin call actually works — confirmed broken
    in production before this shipped (a real third-party origin got `TypeError: Failed
    to fetch`, the classic CORS-rejection signature); see ARCHITECTURE.md's
    "Three middlewares wrap everything" bullet for the full write-up. A permissive
    `Access-Control-Allow-Origin: *` is safe here specifically because `/save` already
    requires a valid token to do anything — same trust model as any bearer-token API,
    and it grants no cookie-authenticated access. No other route gets a CORS header.
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
- `/static/{filename}` resolves through `os.path.basename` to block path traversal.

## `library.db` is intentionally not in the repo

It's Brian's personal reading history (~1,500+ articles). It lives beside the code
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
| `EXA_API_KEY` | — | Exa search API key for FP&A Buddy's preferred web retrieval mechanism (`linklib/agent.py`'s `retrieve_exa`). Absent, or the `exa_enabled` setting toggled off at `/admin/exa-settings` → Claude's native `web_search_20250305` tool handles the web tier instead (Phase 7 kill switch); web search itself is never disabled, only which engine runs. No error either way. |
| `BRANDFETCH_API_KEY` | — | Brandfetch **Brand API** Bearer token, required only for `scripts/backfill_logos.py --apply` (CFO Toolbox logo backfill, Phase D). A different product/credential from `BRANDFETCH_CLIENT_ID` below — do not confuse them. |
| `BRANDFETCH_CLIENT_ID` | — | Public client ID for Brandfetch's free CDN Logo API (`cdn.brandfetch.io`). Kept for reference/potential future browser-embed use, but **not** used by the logo backfill — that product is browser-embed-only and blocks programmatic access (see the Key architecture decisions bullet above). |
| `LINKLIB_EMBED_MODEL` | `text-embedding-3-small` | OpenAI embedding model for `linklib/embeddings.py` |
| `LINKLIB_DB` | `library.db` | Path to the SQLite database |
| `LINKLIB_SAVE_TOKEN` | (none) | Token for `POST /save` + bookmarklet; also the default login password. Set when hosted. |
| `LINKLIB_PASSWORD` | = `LINKLIB_SAVE_TOKEN` | Login password for the private section. Set to decouple the login password from the save token. |
| `LINKLIB_SECRET_KEY` | = password | HMAC key for signing session cookies. Set on the host so logins survive restarts/deploys. |
| `LINKLIB_ENRICH_MODEL` | `claude-opus-5` | Claude model for enrichment. This table entry previously read `claude-haiku-4-5-20251001`, which never matched the actual code default — the code has always defaulted to Opus for depth (see `linklib/enrich.py`'s module docstring). Separately, the code's own literal fallback was briefly changed to `claude-opus-4-8` on a mistaken belief that `claude-opus-5` wasn't a valid current model id — corrected back: `claude-opus-5` is real, current, and Anthropic's own top recommendation for complex/enterprise work (confirmed against Anthropic's docs, `platform.claude.com/docs/en/about-claude/models/overview` — also linked from `/admin/system/model`), so it's the curated registry's "Best quality" entry (`linklib/models.py`), not `claude-opus-4-8`. As of the model-selection settings feature below, this env var is only the fallback used when no DB-stored selection exists — see "AI model selection" in Key architecture decisions. |
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
- Daily off-site Drive backup (bumped from weekly, 2026-08), scheduled via GitHub
  Action (Phase O — see Key architecture decisions above), with retention pruning
  (`linklib.backup.prune_old_backups`), a persistent `backup_log` audit trail, and a
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
must pass. **Mechanically enforced** (2026-08) by a GitHub ruleset (`main-protection`)
on `main`: requires a PR before merging (0 required approvals — solo repo), requires
the `tests` and `secret-scan` status checks to pass, requires the PR branch to be up
to date with `main` before merge, blocks force pushes, restricts branch deletion, and
has an empty bypass list — direct commits to `main` are blocked outright, not just
discouraged by convention. (Before this, "requires a PR and passing checks" was
convention only, not a real gate — worth knowing if a future investigation finds a
commit that looks like it skipped review; anything from before this date could have.)

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

- Enrichment (article summaries and every AI-drafted directory field — Description,
  Agent taxonomy, Competitive differentiation, Community profile fields, and so on):
  `claude-opus-5` by default, quality over cost — see "AI model selection" below for
  how this is now chosen and where it's overridable.
- Q&A and post drafting: `claude-sonnet-4-6` (better synthesis quality)
- Embeddings (hybrid retrieval, `linklib/embeddings.py`): OpenAI `text-embedding-3-small`

All three are overridable via environment variables; enrichment is also overridable
live from `/admin/system/model` without a redeploy — see "AI model selection" below.

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
