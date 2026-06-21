# CFO Navigator

_Find your way back to anything — a self-owned, searchable library of saved articles, built to replace Feedly._
SQLite + full-text search is the spine; everything else reads/writes through it.

## The two jobs

1. **Import** — a one-time pull of your entire Feedly Pro archive
2. **Capture** — save new links going forward (CLI now; phone/desktop once hosted)

Both go through the same pipeline, so a link saved from your phone and one
imported from Feedly land identically.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your tokens
```

### Job 1 — import your Feedly archive

**Recommended: the data-export route (no API token needed).** In Feedly,
Preferences → Privacy & Personal Data → "Download your data". Then:

```bash
python -m scripts.import_archive --zip feedly-archive.zip --db library.db
```

The export is Netscape bookmark HTML, one file per board; board names become
two-level tags (`Finance` + `KPIs`). Add `--enrich` for Claude summaries/tags
during import, or `--include-read` to also pull read history (noisy, off by
default). Idempotent — re-running merges and unions tags rather than duplicating.

### Job 2 — save links going forward

CLI:
```bash
python -m scripts.add_link "https://example.com/post" --tags saas,metrics --note "good CAC framing"
```

Web app (search UI + capture endpoint):
```bash
uvicorn webapp.app:app --reload     # http://localhost:8000
```

---

## Getting your archive

In Feedly: Preferences → Privacy & Personal Data → "Download your data". You'll
get a zip; point `import_archive` at it. No API token needed.

---

## Enrichment (optional but recommended)

This is what makes the library *better* than Feedly: a Claude pass that writes
a clean summary and consistent auto-tags on a schema you control.

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...
python -m scripts.enrich_backfill --db library.db
```

Defaults to Haiku (cheapest capable model) since you'll process a few thousand
articles. Override with `LINKLIB_ENRICH_MODEL`.

---

## Ask your library (FP&A Q&A)

Ask finance questions and get answers grounded in your saved articles **plus fresh
articles from your trusted sites**, with cited sources. Uses the same API key as enrichment.

```bash
python -m scripts.ask "how should I frame CAC payback for usage-based pricing?"
```

Or use the **Ask box** at the top of the web UI. It retrieves your most relevant saved
articles, and (via web search restricted to the sites in `preferred_sites.opml`) pulls in
recent articles you haven't saved, then answers from both and links every citation. Edit
`preferred_sites.opml` to curate which sites it's allowed to pull from. Defaults to Sonnet;
set `LINKLIB_CHAT_MODEL`. Web search bills per use—disable in code with `use_web=False` if desired.

## Draft LinkedIn posts (in your voice)

Turn any saved article into a LinkedIn post written in your voice—self-contained, no
Claude.ai or skill system required.

```bash
python -m scripts.post --id 42                       # from a saved article
python -m scripts.post --topic "why CAC payback misleads" --mode self_promo
```

Or click **Draft LinkedIn post** on any card in the web UI, then Copy. Modes:
`original`, `self_promo`, `amplification`.

---

## Project layout

```
linklib/
  db.py         storage + FTS5 search + idempotent upsert (the spine)
  archive.py    parser for the Feedly data-export (bookmark HTML)
  extract.py    optional live full-text fetch/clean
  enrich.py     Claude summary + auto-tags (taxonomy-aware)
  agent.py      FP&A Q&A: saved library + fresh web from trusted sites, cited
  social.py     LinkedIn post generator in your voice (standalone)
  sources.py    preferred-site domains parsed from preferred_sites.opml
  pipeline.py   shared ingest used by CLI + web app
scripts/
  import_archive.py   Job 1 — import the data-export archive
  add_link.py         Job 2 — save a link (CLI)
  enrich_backfill.py  add Claude summaries/tags over imported rows  <- run this next
  ask.py              FP&A Q&A from the command line
  post.py             draft a LinkedIn post from the command line
webapp/
  app.py        search UI + Ask box + Draft-post + capture + JSON API
```

---

## What's tested and what isn't

- **Verified:** archive parsing, dedupe/tag-merge across boards, FTS ranking,
  enrichment plumbing, Q&A retrieval, and all web endpoints (Ask, Draft, search,
  capture). The 1,526-article import ran clean.
- **Needs your key to see live:** enrichment quality, Q&A answers, and post
  drafts all call the Claude API, so their real output appears once you run them
  with `ANTHROPIC_API_KEY` set. If a Feedly field ever parses oddly,
  `archive.py` is the single place to adjust.

---

## Next decisions (not built yet)

1. **Hosting** — to save from phone + iPad + desktop, the web app needs to be
   reachable. Recommended low-ops default: Cloudflare Workers + D1, or
   Vercel + Turso (both SQLite under the hood). Or a small always-on box.
2. **Phone/iPad capture** — an iOS Share Sheet shortcut that POSTs to `/save`.
3. **Desktop capture** — the bookmarklet at `/bookmarklet`.
4. **Claude access** — a thin MCP server over `/api/search` so you can query
   the library from any Claude chat.
```
