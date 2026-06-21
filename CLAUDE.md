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

scripts/           # CLI entry points
  import_archive.py   # one-time Feedly archive import
  add_link.py         # save a single URL
  enrich_backfill.py  # backfill Claude summaries/tags over imported rows
  ask.py              # FP&A Q&A from the terminal
  post.py             # draft a LinkedIn post from the terminal

webapp/
  app.py           # FastAPI: search UI, Ask box, Draft-post, /save, /api/search, /bookmarklet

preferred_sites.opml  # Feedly subscriptions — used as allowed_domains for web search
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
- **The `/save` endpoint is token-gated** via `LINKLIB_SAVE_TOKEN`. Leave it unset for
  local-only use; set it when the app is public-facing.

## `library.db` is intentionally not in the repo

It's Brian's personal reading history (~1,500+ articles). It lives beside the code
locally and should never be committed. A `.gitignore` entry is needed — see the gap
list below.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for enrichment, Q&A, and post drafting |
| `LINKLIB_DB` | `library.db` | Path to the SQLite database |
| `LINKLIB_SAVE_TOKEN` | (none) | Auth token for `POST /save` (set when hosted) |
| `LINKLIB_ENRICH_MODEL` | `claude-haiku-4-5-20251001` | Claude model for enrichment |
| `LINKLIB_CHAT_MODEL` | `claude-sonnet-4-6` | Claude model for Q&A and post drafting |
| `LINKLIB_PUBLIC_BASE` | `http://localhost:8000` | Base URL embedded in the bookmarklet |
| `LINKLIB_SITES_OPML` | `preferred_sites.opml` | Path to OPML for web-search domain list |

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

**Not yet built (from the migration plan):**
- Public site: bio homepage, thought leadership page, contact
- Authentication/login for the private section when hosted
- CFO Feed (RSS reader pulling Feedly feeds + Substacks, with "save to library" action)
- iOS Share Sheet shortcut
- Hosting/deployment (Vercel, Railway, or similar)
- MCP server wrapper over `/api/search` for Claude chat access

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
