# CFO Navigator

CFO Navigator is Brian Weisberg's personal FP&A research tool and public site,
live at [bmweis.com](https://bmweis.com). If you're a finance leader looking
for the vendor directory, the community reviews, or FP&A Buddy (a Q&A tool
grounded in a curated archive of finance writing plus live web search), start
at the site itself. This repo is the code behind it.

If you're here as a developer: one FastAPI app, one SQLite database with
FTS5 full-text search as the spine, no separate backend or frontend build.
Public pages, a login-gated back office, and a remote MCP server all live in
the same process.

## What it does

- **CFO Toolbox** (`/tools`)—a curated directory of FP&A software vendors
  and finance communities, each with a full profile page and a side-by-side
  Compare view. Vendor profiles add an AI-assisted agent-taxonomy summary.
  Vendor and community submissions come in from the public; Brian reviews
  and approves them.
- **FP&A Buddy** (`/tools/fpa-buddy`)—retrieval-augmented Q&A. It answers
  from Brian's saved article archive plus web search restricted to a curated
  list of trusted finance sites, cites every claim, and remembers the thread
  so you can follow up. Member-gated; every answer carries a per-user
  monthly dollar cap.
- **The Reader** (`/read`, admin-only)—an Instapaper-style reader over
  Brian's saved archive, RSS feed, and read-later list, all in one
  three-pane view.
- **Thought leadership** (`/thought-leadership`)—talks, podcasts, press,
  writing, and a handful of long-form original pieces with their own pages.
- **The public site**—a bio homepage, a contact form, and
  [`/how-this-is-built`](https://bmweis.com/how-this-is-built), which
  explains where AI actually does work on this site and where a human still
  signs off.

## Site map

**Public**
- `/`—bio homepage
- `/about`—the fuller bio
- `/thought-leadership`—talks, podcasts, press, writing, and the featured
  long-form pieces
- `/thought-leadership/{slug}`—an individual piece (Growth Engine Ratio,
  the AI hackathon playbook, connecting Claude to NetSuite, and any new one
  added through the admin) plus a standalone
  `/thought-leadership/growth-engine-calculator`
- `/tools`—the CFO Toolbox landing page, linking to `/tools/software`,
  `/tools/resources` (benchmarking + book recommendations), and
  `/tools/communities`, each with its own directory, profile pages, and a
  Compare view
- `/current-feed`—the writers and publications Brian actually subscribes
  to, as a mixtape tracklist
- `/how-this-is-built` and `/how-this-is-built/{slug}`—where AI does real
  work on this site, and where it doesn't
- `/tools/fpa-buddy/how-it-works`—the FP&A Buddy explainer
- `/play` and `/play/leaderboard`—a small arcade game
- `/contact`, `/privacy`

**Login-gated**
- `/tools/fpa-buddy` and `/ask/history`—FP&A Buddy and your own question
  history (member)
- `/read` and `/read/{article_id}`—the merged Feed/Archive/Read Later
  reader (admin)
- `/admin`—the back office: article enrichment, dedupe, tag cleanup, the
  Toolbox admin, user management, brand/voice standards, `/admin/checks`,
  backups, and more, grouped by area

**API**
- `/mcp`—a remote MCP server (bearer-token auth), covering read access to
  the Toolbox directory and Compare, the article archive and feed, FP&A
  Buddy and the Toolbox matchmakers, and read-only database introspection.
  Separately, `scripts/mcp_server.py` is a small stdio MCP server that wraps
  `GET /api/search` for Claude Desktop/Code.
- `/save` and `/save-later`—the bookmarklet's save endpoints (token-only,
  since the bookmarklet runs cross-origin on someone else's page)

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env        # every knob is documented in this file
uvicorn webapp.app:app --reload     # http://localhost:8000
```

Set `ANTHROPIC_API_KEY` to enable enrichment and FP&A Buddy. With no
`LINKLIB_SAVE_TOKEN`/`LINKLIB_PASSWORD` set, the private section is
open—local-dev convenience.

## The library pipeline

**One-time import from Feedly** (Preferences → Privacy & Personal Data →
"Download your data"):

```bash
python -m scripts.import_archive --zip feedly-archive.zip --db library.db
```

Board names become tags; re-running merges rather than duplicates (URL is
the natural key).

**Ongoing capture:** the `/save` endpoint plus a bookmarklet (grab it at
`/bookmarklet` when logged in) or an iOS Share Sheet shortcut, and the CLI
(`python -m scripts.add_link <url>`).

**Enrichment.** Claude writes a clean summary and consistent tags for every
article. This is the material FP&A Buddy reads from:

```bash
python -m scripts.enrich_backfill --db library.db
```

Defaults to `claude-opus-5`, chosen for summary quality over per-article
cost; override with `LINKLIB_ENRICH_MODEL`, or change it live from
`/admin/system/ai` without a redeploy. To judge whether a different model is
worth it before a full re-enrich, compare models side by side on one
article:

```bash
python -m scripts.enrich_compare --url https://example.com/some-article
```

## FP&A Buddy (Q&A)

Web UI at `/tools/fpa-buddy`, CLI at `python -m scripts.ask "your question"`.
Answers are grounded in the saved article archive plus fresh web results
restricted to the domains in `preferred_sites.opml`, with every citation
linked. FP&A Buddy takes a Quick, Standard or Deep effort choice, which maps
to a model and token budget internally. Model pickers used elsewhere
(re-enrich, backfill) are dynamic (`linklib/models.py`), reconciled against
the live Anthropic Models API so a new Claude model surfaces on its own; the
enrichment picker stays curated so a whole-archive re-enrich can't land on
an unexpectedly pricey model by accident.

## MCP server

Two separate MCP surfaces, for two different jobs:

- **`scripts/mcp_server.py`**—a stdio server (`pip install mcp`) wrapping
  `GET /api/search`, for a local Claude Desktop or Claude Code session to
  search the archive.
- **`/mcp`**—a remote MCP server mounted in the same FastAPI process,
  authenticated per-user with a bearer token (`scripts/mint_api_token.py`
  mints one). It's a real tool surface, not a search wrapper: database
  introspection (`list_tables`, `describe_table`, `sample_rows`,
  `get_rows`), the Toolbox directory and Compare (`search_software`,
  `get_software`, `compare_software`, plus the `communities` and
  `resources`/`books` equivalents), the article archive and RSS feed
  (`search_archive`, `get_article`, `browse_feed`, `search_feed`), and two
  proxy tools onto FP&A Buddy and the Toolbox matchmakers
  (`ask_fpa_buddy`, `ask_matchmaker`) that respect the same per-user dollar
  caps the web UI does. Three of the introspection tools are admin-only;
  the rest follow whatever access level the equivalent web page has.

## Other CLI tools

The full, current inventory—purpose, cadence, required env vars, exact
invocation—lives at `/admin/system/scripts`, the live reference this repo
keeps in sync with `scripts/`. A couple of highlights:

```
scripts/voice_review.py    check any text against the writing-voice standards
scripts/mcp_server.py      stdio MCP server wrapping /api/search (see above)
```

## Project layout

```
linklib/    core library—db.py (SQLite+FTS5 spine), agent.py (FP&A Buddy),
            matchmaker.py (Toolbox matchmakers), compare.py/gates.py
            (Compare + review-state gating), enrich/extract/pipeline
            (ingest), feed.py (RSS), dedupe.py, tagstyle.py, models.py,
            pricing.py, backup.py (Drive backup), email_utils.py (Gmail API
            email), passwords.py, authcheck.py, brand_check.py,
            voice_review.py/voice_db_scan.py/voice_mechanics.py (voice
            enforcement), sources.py, archive.py, and more
scripts/    CLI entry points (see above); scripts/archive/ holds retired
            one-time migrations, kept for history
webapp/     app.py (nearly every route, HTML/CSS/JS inline), mcp_server.py/
            mcp_toolbox.py/mcp_library.py/mcp_qa.py (the /mcp server, see
            above), checks.py (/admin/checks), tasks.py (admin badges),
            static/
tests/      pytest suite, runs in CI alongside a secret scan
```

`library.db` is deliberately **not** in the repo (personal reading history);
`CLAUDE.md` holds the full architecture notes, auth model, and contributor
conventions; `BRAND.md` holds the visual/voice standards, enforced by CI and
`/admin/checks`.

## Hosting

Railway, building from the `Dockerfile`, auto-deploying from `main`. If it
isn't on `main`, it isn't live. Changes ship via PR: a branch ruleset on
`main` requires a PR, requires the `tests` and `secret-scan` checks, and
blocks force-pushes and branch deletion. The database lives on a Railway
volume; daily off-site backups go to Google Drive when the `GOOGLE_OAUTH_*`
vars are set (the same OAuth client also powers outbound email—setup steps
in `.env.example`).

## License

[AGPL-3.0](LICENSE). You're free to use, study, modify, and redistribute this
code, including commercially. The one real obligation: if you run a modified
version of this app as a network service, you have to offer that modified
source to the people using it, not just to whoever you hand a copy to
directly. That's the "affero" part, and it's the reason this license was
picked over a plain GPL for a web app.
