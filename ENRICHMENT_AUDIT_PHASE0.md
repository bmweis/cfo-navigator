# Digital Library — Enrichment Audit via Exa, Phase 0 (Investigation Only)

No code changes, no Exa API calls, no schema changes were made for this phase. This
report is the write-up requested in the task description.

## Important caveat up front: no live/local database access

`library.db` is intentionally not in git (`CLAUDE.md`, "`library.db` is intentionally
not in the repo") — it lives beside the code locally and on a Railway volume in
production. This investigation environment has neither copy. That means:

- I could not run a query against real rows, so **section 1's "pull a representative
  sample" ask could not be executed as literally specified.** Everything below about
  schema and coverage comes from reading the code (`linklib/db.py`, `linklib/enrich.py`,
  `linklib/pipeline.py`, `scripts/enrich_backfill.py`) rather than from live data.
- The row count is the one already documented in `ARCHITECTURE.md`: **~1,500+ articles**
  (not a live count — I could not re-verify it).
- If you want the actual sample-row breakdown, the fastest path is running a short
  read-only query yourself (locally, or via a Railway shell) — see "If you want the
  real sample data" at the end of section 1. I did not write a script for this since
  the task scoped this phase to investigation only and I have no DB to test one against.

## 1. Enrichment schema and coverage

**Fields the Opus enrichment pass populates**, from `linklib/enrich.py::enrich()` and
`linklib/db.py`'s `articles` table:

| Column | What it holds |
|---|---|
| `summary` | Claude-generated, 4-7 sentences, "written to be retrieved and reasoned from" — the resale-safe, member-facing asset and the material FP&A Buddy's retrieval reasons from |
| `tags_json` / `tags_text` | 3-7 topic tags, preferring existing vocabulary (`known_tags()`) over inventing new ones |
| `enriched` | 1 once a summary/tags have been applied, 0 otherwise |
| `enrich_model` | which model produced it (e.g. `claude-opus-5`) |
| `enrich_rules` | the `ENRICH_RULES_VERSION` string in force at enrichment time — currently `"v4"` in the prompt (`linklib/enrich.py`). This is the field designed to let you re-run older rows once you bump the prompt/rules. |
| `in_scope` / `scope_reason` | audience-scope flag + one-line rationale — `in_scope=0` marks something the enricher judged off-audience (e.g. "how to get a job in VC"), for the Phase 3 review queue (`list_flagged`/`keep_article` in `linklib/db.py`) |

Enrichment is triggered from two places: `linklib.pipeline.ingest_url` (going-forward
saves, via `/save` or `add_link.py`) and `scripts/enrich_backfill.py` (catch-up pass
over `Library.unenriched()` rows). Cost per call is logged to `enrichment_cost`
(`article_id`, `model`, `input_tokens`, `output_tokens`, `cost_usd`).

**Is there a `needs_verification`-style marker on `articles`? No.** That pattern
exists elsewhere in the schema — `tool_features.needs_verification`, the
`NEEDS_VERIFICATION` sentinel used in `generate_community_listing`,
`community_profiles.needs_review` — but none of it was ever extended to `articles`.
The closest thing `articles` has is `in_scope`/`scope_reason`, and that's answering a
different question (is this piece even relevant to the audience) rather than "is this
field's *value* thin, stale, or low-confidence." If you build an audit, this is the gap
a new flag would fill — see the "Phase 1" section below.

**On `enrich_rules` version history:** the prompt in the current code is versioned
`"v4"`, and `git log -p` on `linklib/enrich.py` across this repo's history shows only
`"v4"` ever committed — meaning any earlier version bumps (v1-v3) predate this repo's
tracked history (likely from the original Feedly-import era). I can't tell you from
here what fraction of the ~1,500 rows carry `"v4"` vs. something older/blank without a
live query.

**Patterns I'd expect but can't confirm without real data:**
- Rows saved via the original Feedly import (`scripts/import_archive.py`) that predate
  `enrich_backfill` running would show `enriched=0` until the backfill catches them —
  worth checking whether the backfill has actually been run to completion, or whether
  a chunk of the archive is still sitting unenriched.
- `content` (full fetched text) is best-effort (`linklib/extract.py` — "dead links,
  paywalls, and bot-blocks are expected for older saves, so failures are swallowed").
  An empty `content` on an older row means enrichment ran off `title` alone
  (`enrich(title, text=content or title, ...)` in `pipeline.py`), which plausibly
  produces thinner summaries for older/paywalled saves than for recent ones with full
  text. This is a real, code-confirmed mechanism for "older saves worse than recent
  ones" — I just can't quantify how many rows it actually affects.
- Similarly, `summary` for pre-Claude-enrichment rows might still hold the raw Feedly
  snippet (per `CLAUDE.md`: `summary` = "Feedly snippet or Claude-generated") until
  overwritten by `enrich_backfill` — another plausible "thin metadata" bucket for
  older saves.

**If you want the real sample data:** the read-only equivalent of `sqlite3 library.db
"SELECT id, url, source, saved_at, enriched, enrich_model, enrich_rules, in_scope,
length(summary), length(content) FROM articles ORDER BY RANDOM() LIMIT 20;"` run
against the actual `library.db` (locally or via Railway) would give you the exact
"which fields are populated vs. null/thin, and does it correlate with save date/source"
breakdown this section asks for. I'm intentionally not writing a script for this in
Phase 0 (investigation only, per the task scope) — happy to build one in Phase 1.

## 2. What "audit via Exa" could concretely mean

### Metadata verification (per-row)
Exa's Contents endpoint could re-fetch each saved URL's live page and compare its
current title/text against the stored `title`/`summary`, flagging drift (a
rewritten headline, an updated stat, a since-corrected article) or a dead
fetch. This is feasible in shape — same idea as `linklib/enrich.py`'s existing
`extract.fetch_page` re-fetch used by `generate_tool_description`, just swapping the
fetch source. It would need a second pass (Claude comparing old vs. new content) to
actually judge "drifted" vs. "unchanged," so the real cost is Contents-fetch cost
*plus* a Claude call per row, similar in shape to the original enrichment cost.

### Gap-finding (topic-shaped, not per-row)
A different query shape: run Exa Search against `preferred_sites.opml`'s domain
allowlist for topics that match the archive's tag distribution, and diff results
against saved URLs to surface things you likely would have saved but didn't. This is
a small number of broad queries (e.g. one per major tag/topic bucket), not one call
per archived row — much cheaper and a genuinely separate sub-feature from metadata
verification, as the task anticipated. Flagging it here as distinct, per instructions
— not something to fold into the per-row verification pass.

### Dead link / staleness detection
**A simpler method already exists and doesn't need Exa at all.** `linklib/extract.py`'s
`fetch_page` (plain `requests` + BeautifulSoup, already a dependency) can detect a
dead/redirected/blocked URL directly — a 404/410, a redirect to a generic homepage, or
an empty extraction, the same signals `linklib/authcheck.py` already uses to detect a
lapsed paywall cookie for a *different* purpose. Since I have no live archive to
sample, I can't report an actual percentage of likely-dead links from a spot check
today, but the mechanism to check is already in the codebase — this doesn't need to
wait on an Exa decision, and shouldn't be priced against Exa's per-page rate at all.

## 3. Cost estimate

Using the pricing already gathered for the separate Exa retrieval-tier evaluation
(Search $7/1,000 queries, Contents $1/1,000 pages) against the ~1,500-article
archive size documented in `ARCHITECTURE.md`:

| Approach | Shape | Rough one-time cost |
|---|---|---|
| Metadata verification | ~1,500 Contents fetches (one per article) | ~$1.50 in Exa fetch cost, **plus** a Claude compare call per row — no live `enrichment_cost` data to benchmark from here, but structurally the same order of magnitude as the original enrichment pass (that pass processes "thousands of articles" per `CLAUDE.md`'s model notes) |
| Gap-finding | Tens of Search calls (one per topic/tag bucket, not per article) | Well under $1 in Exa Search cost (e.g. 100 queries × $0.007 ≈ $0.70) — cost is dominated by whatever Claude filtering/dedup pass you'd add on top, not by Exa itself |
| Dead-link check | No Exa needed — plain HTTP fetches, ~1,500 requests | Effectively free (no third-party API cost) |

This is a **separate one-time or periodic admin cost**, not part of the ~$5/user/month
FP&A Buddy cap (`EFFORT_SETTINGS` in `linklib/agent.py`) — it shouldn't be summed into
that budget or tracked in `ask_questions`. If built, it would want its own ledger entry
following the existing `enrichment_cost`/`article_embeddings` overhead-ledger pattern
(both explicitly "never summed into `ask_questions`, never counts toward a user's Ask
cap" — see the comments above those tables in `linklib/db.py`), not a new mechanism.

## If Brian wants to proceed — what a Phase 1 build would need to touch

- **`linklib/db.py`** — a schema migration (additive `ALTER TABLE`, per this
  codebase's migration convention) if you want a `needs_verification`-style flag or a
  `last_verified_at`/`verification_status` column on `articles`, plus a new
  overhead-ledger table (mirroring `article_embeddings`/`enrichment_cost`) if
  metadata-verification cost should be tracked durably rather than just logged.
  **Any schema change here requires an `ARCHITECTURE.md` update in the same PR**
  (the ER diagram + the `articles` table row), per the standing doc rule in `CLAUDE.md`.
- **A new module**, likely `linklib/audit.py`, mirroring `linklib/authcheck.py`'s shape
  (probe → persist a status record → expose via `get_*_status`) for whichever of the
  three sub-features gets built first — metadata verification, gap-finding, and dead-
  link detection are different enough in query shape that they probably don't belong
  in one function.
- **A CLI entry point** under `scripts/`, e.g. `scripts/audit_enrichment.py`, following
  `scripts/embed_backfill.py`/`scripts/enrich_backfill.py`'s batched, resumable,
  one-off-pass pattern.
- **An admin surface** to review flagged rows — either a new `/admin/library-audit`
  page or an extension of the existing off-audience review flow
  (`list_flagged`/`keep_article` in `linklib/db.py`, already backing a review queue in
  the admin UI) if the shape turns out to be similar enough to reuse.
- **`EXA_API_KEY` wiring is already done** (Phase 2 of the separate FP&A Buddy Exa
  retrieval work, `linklib/agent.py`'s `retrieve_exa`) — an audit build would reuse the
  same key/dependency, no new third-party dependency to discuss.
- Per the standing rule, any new/changed route also needs `ARCHITECTURE.md` updated in
  the same PR, not a follow-up.

No recommendation on which of the three sub-features (metadata verification,
gap-finding, dead-link detection) to build first is made here, per the task's scope —
that's your call once you've seen the real sample-row breakdown.
