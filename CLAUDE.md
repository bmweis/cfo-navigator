# CFO Navigator — Claude Code context

## What this is

A personal finance research tool for Brian Weisberg (brian.weisberg@gmail.com).
Three capabilities, all backed by a single SQLite database (`library.db`):

1. **CFO Library** — searchable archive of saved articles (imported from Feedly or
   captured going forward). SQLite + FTS5 full-text search is the spine.
2. **CFO Navigator** — FP&A Q&A chatbot. Retrieval-augmented: pulls the most relevant
   saved articles + fresh web results from trusted sites, then synthesizes a cited answer
   via Claude.
3. **Social** — drafts LinkedIn posts in Brian's voice from any saved article or topic.

The longer-term plan (see `MIGRATION_AND_BUILD_PLAN.md`) is to grow this into a public
site at bmweis.com, with a public-facing bio/thought-leadership section and a
login-gated private section for the library, chatbot, and feed.

## Project layout

```
linklib/           # core library (the only thing that matters long-term)
  db.py            # SQLite + FTS5 schema, Library class, Article dataclass — the spine
  archive.py       # parses Feedly "Download your data" bookmark HTML export
  extract.py       # best-effort full-text fetch (trafilatura preferred, BS4 fallback)
  enrich.py        # Claude API: generates summary + auto-tags for each article
  pipeline.py      # shared ingest used by CLI and web app
  agent.py         # FP&A Q&A: library retrieval + web search, cited answer
  social.py        # LinkedIn post generator in Brian's voice (self-contained)
  sources.py       # parses preferred_sites.opml → domain allowlist for web search
  feed.py          # RSS/Atom reader over the OPML list: concurrent fetch, 30-min cache

scripts/           # CLI entry points
  import_archive.py   # one-time Feedly archive import
  add_link.py         # save a single URL
  enrich_backfill.py  # backfill Claude summaries/tags over imported rows
  ask.py              # FP&A Q&A from the terminal
  post.py             # draft a LinkedIn post from the terminal

webapp/
  app.py           # FastAPI: public site (/, /thought-leadership, /growth-engine-ratio,
                   #   /contact) + private tools (/library, /feed, /read, /ask, /post,
                   #   /save, /api/search, /bookmarklet) + auth (/login, /logout)
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
- **Web search is domain-restricted.** `agent.py` passes `preferred_sites.opml` domains
  as `allowed_domains` to the `web_search_20250305` tool, so the chatbot only cites
  sources Brian already trusts.
- **`preferred_sites.opml` is dual-purpose.** It's both the web-search allowlist and the
  `/feed` reader's subscription list. Use direct RSS/Atom URLs — Feedly proxy URLs
  (`feedly.com/web/...`) are skipped because they require auth. Paywalled sources are
  tagged in `feed.py` (`PAYWALLED_DOMAINS`) and shown with a badge; the in-app reader is
  disabled for them.
- **The `/feed` reader caches per-feed for 30 minutes** (`feed.py`, in-memory). Cached
  item dicts are shallow-copied before mutation — never mutate a cached entry in place.
  Editing the OPML won't show up live until the cache expires or the app restarts.

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
  - Public (no auth): `/`, `/thought-leadership`, `/growth-engine-ratio`, `/contact`,
    `/login`, `/logout`, `/static/*`, `/health`.
  - Private HTML pages → **redirect to `/login`** when signed out: `/library`, `/feed`,
    `/read`, `/admin/contacts`.
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
locally and should never be committed. A `.gitignore` entry is needed — see the gap
list below.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for enrichment, Q&A, and post drafting |
| `LINKLIB_DB` | `library.db` | Path to the SQLite database |
| `LINKLIB_SAVE_TOKEN` | (none) | Token for `POST /save` + bookmarklet; also the default login password. Set when hosted. |
| `LINKLIB_PASSWORD` | = `LINKLIB_SAVE_TOKEN` | Login password for the private section. Set to decouple the login password from the save token. |
| `LINKLIB_SECRET_KEY` | = password | HMAC key for signing session cookies. Set on the host so logins survive restarts/deploys. |
| `LINKLIB_ENRICH_MODEL` | `claude-haiku-4-5-20251001` | Claude model for enrichment |
| `LINKLIB_CHAT_MODEL` | `claude-sonnet-4-6` | Claude model for Q&A and post drafting |
| `LINKLIB_PUBLIC_BASE` | `http://localhost:8000` | Base URL embedded in the bookmarklet |
| `LINKLIB_SITES_OPML` | `preferred_sites.opml` | OPML path — web-search allowlist AND `/feed` source list |

## Running locally

```bash
pip install -r requirements.txt
pip install anthropic          # for Q&A, enrichment, post drafting
export ANTHROPIC_API_KEY=...

# Import Feedly archive (one time)
python -m scripts.import_archive --zip feedly-archive.zip --db library.db

# Backfill enrichment
python -m scripts.enrich_backfill --db library.db

# Web app
uvicorn webapp.app:app --reload    # http://localhost:8000
```

## What's built vs. what's next

**Built and working:**
- Feedly archive import (`archive.py`, `scripts/import_archive.py`)
- Going-forward capture: CLI (`add_link.py`) and web (`/save`)
- FTS5 search and web UI
- Enrichment backfill
- FP&A Q&A (library + web search)
- LinkedIn post drafting
- Bookmarklet
- Public site: bio homepage (`/`), thought leadership (`/thought-leadership`),
  Growth Engine Ratio page + calculator (`/growth-engine-ratio`), contact (`/contact`)
- Password login for the private section (`/login` + signed session cookie)
- CFO Feed RSS reader (`/feed`) with category tabs, per-source filter, save-to-library
- Article reader, Instapaper-style (`/read`)
- Hosting/deployment on Railway (see Deployment below)

**Not yet built (from the migration plan):**
- iOS Share Sheet shortcut
- MCP server wrapper over `/api/search` for Claude chat access
  (note: `/api/search` already accepts a `token` for programmatic auth)
- bmweis.com custom domain pointed at Railway

## Deployment

- **Host:** Railway, building from the `Dockerfile` (`python:3.11-slim`, runs
  `uvicorn webapp.app:app`). `Procfile` and `railway.toml` are also present;
  `railway.toml` sets the healthcheck to `/health`.
- **Deploy flow:** Railway auto-deploys from the **`main`** branch. Feature work happens on
  a branch, then merges to `main` to ship. **If it isn't on `main`, it isn't live** — a
  common gotcha (e.g. a change that looks done but "doesn't show up" is usually still on a
  feature branch).
- Set `LINKLIB_SAVE_TOKEN` (and ideally `LINKLIB_SECRET_KEY`) in Railway's env so the
  private section is protected and logins persist across deploys. `library.db` is not in
  the repo, so a hosted instance starts empty unless the DB is provisioned/persisted
  separately.

## Models in use

- Enrichment: `claude-haiku-4-5-20251001` (cheap, processes thousands of articles)
- Q&A and post drafting: `claude-sonnet-4-6` (better synthesis quality)

Both are overridable via environment variables. Check current model IDs at
https://docs.anthropic.com/en/docs/about-claude/models before changing defaults.

## Billing note

Two separate billing relationships:
- **Building the site** → Claude Pro subscription (flat monthly, covers Claude Code)
- **Running the app** → Anthropic API key, pay-per-use (`ANTHROPIC_API_KEY`)

Keep the app's API key out of Claude Code building sessions (don't set it in the
terminal where you run `claude`), or Code will bill the API key instead of the
subscription.
