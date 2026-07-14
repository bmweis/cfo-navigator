# CFO Navigator

_Brian Weisberg's personal site + FP&A research toolkit, live on Railway._

One FastAPI app with a public face (bio, thought leadership, Growth Engine
Ratio, CFO Toolbox, contact) and a login-gated back office (article archive,
feed reader, FP&A Buddy, and a ~40-page admin hub). SQLite + FTS5 full-text
search is the spine; everything reads and writes through it.

## What's in the box

**Public site**
- `/` — bio homepage
- `/thought-leadership` — talks, podcasts, events, writing, and the three
  showcase pages below
- `/thought-leadership/growth-engine-ratio` — the GER essay + interactive
  calculator (and the song)
- `/tools` — CFO Toolbox landing page, linking to:
  - `/tools/software` — curated vendor directory with categories, reader
    submissions, and Warm Intro requests
  - `/tools/benchmarks` — benchmarking resources
  - `/tools/communities` — CFO/finance communities directory (placeholder)
- `/thought-leadership/ai-hackathon-playbook`, `/thought-leadership/netsuite-mcp` — guides
- `/contact` — contact form (submissions stored, and emailed once Google
  OAuth is configured — see `.env.example`)
- (`/growth-engine-ratio`, `/finops-ai-hackathon`, `/netsuite-mcp` 301-redirect
  to the nested paths above)

**Private (login or token)**
- `/library` — the CFO Library hub, grouping the pages below into "Reading
  Room" (Archive, Feed) and "FP&A Buddy" (the Buddy itself, Past Questions)
- `/library/archive` — 1,500+ saved articles, imported from Feedly and
  captured going forward, searchable via FTS5
- `/library/feed` — RSS/Atom reader over `preferred_sites.opml`, with category
  tabs and save-to-library
- `/read` — Instapaper-style article reader
- `/library/ask` — **FP&A Buddy**: retrieval-augmented Q&A over the library
  plus domain-restricted web search, with cited answers and per-user cost caps
- `/library/past-questions` — Past Questions: browse questions other members
  have already asked FP&A Buddy
- (`/archive`, `/feed`, `/ask`, `/questions` 301-redirect to the nested paths
  above)
- `/admin` — the back office: archive queue and enrichment, dedupe, tag
  cleanup, CFO Toolbox management, users, brand/voice standards, checks,
  backups, and more

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env        # every knob is documented in this file
uvicorn webapp.app:app --reload     # http://localhost:8000
```

Set `ANTHROPIC_API_KEY` to enable enrichment and FP&A Buddy. With no
`LINKLIB_SAVE_TOKEN`/`LINKLIB_PASSWORD` set, the private section is open —
local-dev convenience.

## The library pipeline

**One-time import from Feedly** (Preferences → Privacy & Personal Data →
"Download your data"):

```bash
python -m scripts.import_archive --zip feedly-archive.zip --db library.db
```

Board names become tags; re-running merges rather than duplicates (URL is
the natural key).

**Ongoing capture:** the `/save` endpoint + bookmarklet (grab it at
`/bookmarklet` when logged in), the CLI (`python -m scripts.add_link <url>`),
reader submissions at `/library/submit`, and the admin Archive Queue, which
scans your feeds and sitemaps for candidates to approve.

**Enrichment** — Claude writes a clean summary + consistent tags for every
article; this is the material FP&A Buddy reads from:

```bash
python -m scripts.enrich_backfill --db library.db
```

Defaults to Haiku (cheap at ~1,500-article scale); override with
`LINKLIB_ENRICH_MODEL`. To judge whether a pricier model is worth it before
a full re-enrich, compare models side by side on one article:

```bash
python -m scripts.enrich_compare --url https://example.com/some-article
```

## FP&A Buddy (Q&A)

Web UI at `/library/ask`, CLI at `python -m scripts.ask "your question"`. Answers
are grounded in your saved articles **plus** fresh web results restricted to
the domains in `preferred_sites.opml`, with every citation linked. Model
pickers are dynamic (`linklib/models.py`) — new Claude models surface
automatically; enrichment models stay curated.

## Other CLI tools

```
scripts/voice_review.py   check any text against the writing-voice standards
scripts/backfill_queue.py one-time sitemap sweep to queue historical articles
scripts/mcp_server.py     stdio MCP server wrapping /api/search — lets Claude
                          Desktop/Code search the library (pip install mcp)
```

## Project layout

```
linklib/    core library — db.py (SQLite+FTS5 spine), agent.py (FP&A Buddy),
            enrich/extract/pipeline (ingest), feed.py (RSS), dedupe.py,
            queue.py, suggest.py, tagstyle.py, models.py, pricing.py,
            backup.py (Drive backup), email_utils.py (Gmail API email),
            passwords.py, authcheck.py, brand_check.py, voice_review.py,
            sources.py, archive.py
scripts/    CLI entry points (see above)
webapp/     app.py (all routes + inline HTML/CSS/JS), checks.py (/admin/checks),
            tasks.py (admin badges), thought_leadership_data.py, static/
tests/      pytest suite — runs in CI alongside a secret scan
```

`library.db` is deliberately **not** in the repo (personal reading history);
`CLAUDE.md` holds the full architecture notes, auth model, and contributor
conventions; `BRAND.md` holds the visual/voice standards, enforced by CI and
`/admin/checks`.

## Hosting

Railway, building from the `Dockerfile`, auto-deploying from `main` — **if it
isn't on `main`, it isn't live.** All changes ship via PR (branch protection
requires passing `tests` + `secret-scan` checks). The database lives on a
Railway volume; off-site weekly backups go to Google Drive when the
`GOOGLE_OAUTH_*` vars are set (same OAuth client powers outbound email —
setup steps in `.env.example`).
