# CFO Navigator — Full Build Plan: Admin Tooling + Profile Pages + Feature Normalization & Compare

> **This is the canonical build plan for this initiative.** It was previously only ever pasted into chat, which meant a session that lost its context also lost the plan. Any session picking up this work should read this file directly rather than relying on it being re-pasted. Referenced from `CLAUDE.md`. Update phase status here (or check PR/git history) as work lands — don't let this drift into a stale snapshot.

**Supersedes all prior prompts on this thread**, including the previous combined version. Phase 0 has run and reported back — its findings are now baked directly into the relevant phases below rather than left as open investigation items.

**Design references — attach both to the session:**
- `software-profile-mockup-v11-final.html` — literal visual/structural spec for Phase 3/3b.
- `compare-view-mockup-v4.html` — Compare view built in Phase 8.5. Screenshots drop below 640px.

---

## Status (as of 2026-08-01)

Pulled from actual code/PR state, not from this doc's original phase-by-phase
guesses — see the "Plan vs. code" notes below for where the two disagreed.

| Phase | Status | Notes |
|---|---|---|
| 0 — Findings | MERGED | Investigation-only; findings folded into the phases below. |
| 1 — Bulk edit polish / admin table columns | MERGED | [#244](https://github.com/bmweis/cfo-navigator/pull/244). |
| 2 — Domain-slug URL restructure | MERGED | [#245](https://github.com/bmweis/cfo-navigator/pull/245). Public + edit routes both on `/tools/{type}/{slug}[/edit]`. |
| 3 — Software profile page | MERGED | [#246](https://github.com/bmweis/cfo-navigator/pull/246) (redesign) + [#247](https://github.com/bmweis/cfo-navigator/pull/247) (Bottom Line callout addendum). |
| 3b — Communities profile page | MERGED | [#248](https://github.com/bmweis/cfo-navigator/pull/248). Full build (screenshot capture extended to Communities, Details card, edit form) — the 3b.0 preview gate was cleared before this merged, not just the preview round. |
| 4 — Edit-page button reorg | MERGED | [#251](https://github.com/bmweis/cfo-navigator/pull/251), bundled with Phases 6 and 7 — see note below. |
| 5 — Competitors / Similar-entities auto-suggestion | MERGED — **ahead of plan sequencing** | Landed in [#249](https://github.com/bmweis/cfo-navigator/pull/249), before Phase 4/6/7, bundled into the AI-first-pass principle work instead of as its own phase. Verified complete in a dedicated investigation session (2026-08-01) — see the Phase 5 section below for what actually exists. |
| 6 — Features section collapsible (Software edit page) | MERGED | [#251](https://github.com/bmweis/cfo-navigator/pull/251), bundled with Phases 4 and 7. |
| 7 — Warm intro reorder (Software edit page) | MERGED | [#251](https://github.com/bmweis/cfo-navigator/pull/251), bundled with Phases 4 and 6. |
| 8 — Feature normalization + Compare | NOT STARTED | Hard gate at 8.0 — investigation + cleanup-mapping proposal needs Brian's explicit approval before 8.1/8.2. |
| 9 — Per-item memory | NOT STARTED | Hard dependency on Phases 5 and 8 both being fully merged; Phase 5 is done, Phase 8 is not, so this stays blocked. |

**Note on #251 (Phases 4/6/7 bundled into one PR):** this is a deliberate
exception to the "one PR per phase minimum" process rule below, not a missed
rule — Phase 6 (collapsible Features) and Phase 7 (Warm Intro moving to below
Features) are structurally entangled on the same edit-page real estate as
Phase 4's button moves, so splitting them into three PRs would have meant
each one repeatedly touching code the other two also touch.

**AI-first-pass, human-approval-gate standing principle:** all 5 items are
done (Bottom Line generate button, Competitors/Similar-communities full
draft, review-status tracking via `ai_drafted_fields`/`_record_ai_drafted_reviews`,
Communities narrative-field generate buttons, and the 7 previously
hand-entry-only Communities fields folded into the generate flow) — ahead of
where the phase-by-phase list below implies, since this was pursued as a
cross-cutting principle rather than sequenced phase-by-phase.

---

## Standing project context

- Repo: bmweis/cfo-navigator on GitHub. `main` is the only branch — feature work happens on short-lived branches off `main`, merged via PR.
- Hosted on Railway, custom domain bmweis.com (canonical apex). Auto-deploys from `main`.
- Python FastAPI, all HTML/CSS inline in Python strings (no Jinja, no React). Vanilla JS only where needed.
- SQLite (library.db) with FTS5, on a Railway volume at /data/library.db. `linklib/db.py` is the schema/Library class. `webapp/app.py` holds routes and inline HTML.
- Design system: background #F5F4EF, navy #002975, seafoam #A3E5D4, coral #E8704F/#B14A30 (rare accent, one per screen). Outfit headings, DM Sans body. Full rules in BRAND.md.
- Any PR that changes schema, adds/removes/renames a route, or alters a documented flow must update ARCHITECTURE.md (and CLAUDE.md where relevant) in the same PR.
- No new dependencies without discussing first.

## Scope note

Still multiple sessions, not one. Each numbered phase (and each 8.x sub-phase) is its own session minimum.

## Standing principle: AI-first-pass, human-approval-gate on every profile field

Every profile field, both Software and Communities, should have an AI "Generate" option that drafts a first pass. Nothing publishes without Brian reviewing/editing and approving it — the AI draft fills the edit form field, it never writes directly to the live page. This supersedes two earlier design decisions:

- The Bottom Line/differentiation text was previously hand-written-only, explicitly "not auto-drafted," to protect the site's neutral-curator positioning. **Now gets a Generate button too** — the review-before-publish gate is what protects neutrality, not the absence of an AI draft.
- Competitors was previously AI-ranked suggestions Brian picks from, explicitly because the ranking was "too noisy to trust unreviewed." **Now becomes a full AI-drafted list**, same review-before-publish gate — AI does the full first draft, not just candidate ranking.

Applies to Communities' narrative fields too, regardless of what currently exists there — build Generate capability for all of them, don't just verify and report back.

---

## Phase 0 — Findings (complete)

- **Bulk edit already exists**, via a shared picker-based pattern (`_admin_bulk_panel_html()`): select rows → "Bulk Edit" → pick a field + value → confirm → JSON POST. Not inline table-cell editing. Already covers Software (categories, advisor, promoted/Featured, warm_intro_enabled) and Communities (cost_band, sponsorship_type, access, format, reach, categories, featured, advisor).
- **Edit routing today:** numeric-ID based. Software: `/admin/tools/{tool_id}/edit`. Communities: `/admin/tools/communities/{community_id}/edit`. **Superseded entirely by Phase 2** — no interim fix needed on the old routes.
- **Public profile pages already live**, not stubs: `/tools/software/{slug}` and `/tools/communities/{slug}`. Phase 3/3b is a redesign of real, populated pages.
- **"Refresh AI research"** confirmed: one Claude call (`generate_tool_features`, `linklib/enrich.py`) produces both agent taxonomy and features in one structured-JSON response. **Software-only.**
- **Screenshot recapture:** Playwright-based (`linklib/screenshots.py`), synchronous, **Software-only today** — Phase 3b builds the Communities equivalent from scratch (see below).
- **Competitors:** manually curated. A tag/category-overlap function ranks admin-dropdown suggestions but is explicitly commented as "too noisy to trust unreviewed" — Phase 5 builds real suggestion logic as planned. **Software-only, no Communities equivalent.**
- **Schema field-naming bug:** Software's admin table "Description" column header currently renders `summary` (the short field), not `description` (the long field). Fix while touching this table in Phase 1 — don't let the new "Short description" column duplicate a mislabeled existing one.
- **Communities has no separate short-summary field** — a single `notes` field does both short and long description duty. Use `notes` (truncated) as the Short description reference column source; no schema change needed now, just be aware Software and Communities aren't symmetric here.
- **Collapsible pattern:** native `<details>/<summary>`, no JS, no custom classes — trivial to replicate for Phase 6.
- **Communities confirmed:** no warm-intro/vendor-contact fields, no competitors, no screenshot fields, no features table. **Also found:** untracked columns (`demographic`, `cost_note`, `sponsor_name`, `metros_json`, `local_markets`) and a separate `community_profiles` overlay table (~38 columns) not accounted for in any prior plan. See Phase 3b.0 below — this needs its own investigation before the Details card is designed, there's too much unknown content to build against blind.
- **Domain-slug collisions:** checked against seed scripts (library.db isn't in this checkout) — a lower bound, not definitive. Zero same-type collisions found. 3 cross-type collisions (airbase, datarails, rillet) are **moot** — Software and Communities use separate URL prefixes and will never share a namespace (confirmed). **Re-verify against live production data when Phase 2 actually executes**, don't trust the seed-script result as final.

---

## Phase 1 — Bulk edit polish (smaller than originally scoped)

Existing bulk-edit pattern is adopted as-is, not rebuilt. This phase is now just:

1. Add **URL** (clickable, new tab) and **Short description** as read-only reference columns to both `/admin/tools/software` and `/admin/tools/communities` tables. Software pulls from `summary`; Communities pulls from `notes` (truncated).
2. Fix the mislabeled "Description" column on the Software admin table — it currently shows `summary` under a "Description" header. Relabel correctly or consolidate with the new Short description column so there's no confusing duplicate.
3. Visual fixes on both tables: fix Name-column tag wrapping, widen the Format column, rearrange row Actions (Edit / Profile / Mark reviewed / Delete) into a 2x2 grid.

---

## Phase 2 — URL restructure: domain-derived slugs

Replace numeric-ID edit URLs with slug-based ones. Public profile prefix confirmed: `/tools/software/{slug}` and `/tools/communities/{slug}`.

**Algorithm:**
1. Take the hostname from the stored URL (e.g. `https://www.abacum.io` → `abacum.io`).
2. Strip a leading `www.` if present.
3. Take the first label before the first remaining dot as the slug (e.g. `abacum.io` → `abacum`).
4. Profile page: `/tools/software/{slug}` or `/tools/communities/{slug}`.
5. **Edit page: same URL + `/edit`** — public prefix, e.g. `/tools/software/{slug}/edit`. Confirmed, not admin-namespaced.

**Collision handling:** if two entries of the *same type* reduce to the same slug, fall back to the full domain (dots replaced with hyphens) for the conflicting entries only. Cross-type collisions are not a concern — separate namespaces. **Re-verify against live production `library.db` before executing** — Phase 0's check was against seed scripts only.

- No redirect needed from old ID-based URLs — hard cutover.
- Update ARCHITECTURE.md for the route changes in this PR.

---

## Phase 3 — Software profile page (build against `software-profile-mockup-v11-final.html`)

This is a **redesign of an existing, live, populated page**, not new-from-scratch.

- **Top band**, two columns (2/3 + 1/3): left is hero text (name + short description + action row), right is the screenshot card.
  - Screenshot card carries the **Featured sticker** on its top-right corner (rotated, overlapping, matches the homepage bio-box sticker treatment in BRAND.md) — conditional on the Featured flag.
  - Vendor name gets a small superscript asterisk when the Advisor flag is set, linking conceptually to the page-bottom footnote.
  - Action row, all in one line: **Visit** (primary) → **Warm intro** (ghost, conditional on the warm-intro flag + vendor contact) → **Compare** (ghost, UI only — see note below) → thin divider → **Edit** (admin-only, muted style).
- **Lower band**, two columns (2/3 + 1/3):
  - Left: Description card (long description only — the "Bottom Line" callout below replaces the previous italic "how this differs" sub-paragraph), then Agent taxonomy card below it.
  - **Bottom Line callout** (new, small follow-up to the already-merged Phase 3 page): seafoam-background callout block, same treatment as Communities' — "Best for X... trade-off is Y" framing. Replaces the buried italic sub-paragraph inside the old Description card. **Gets an AI "Generate" button** (per the new standing principle below) — drafts the text into the edit form for Brian to review/edit before publish. No longer hand-written-only by design.
  - Right: **Features card** — two-column table (Feature | AI), plain checkmark in the AI column only when AI-enabled, blank otherwise. Verification tag inline next to the feature name where applicable. "N of M need verification" moves to the **bottom** of the card, right-aligned, admin-only. Then Categories card (bulleted list), then Competitors card (chip row).
- **Page-bottom footnote:** plain text, small/muted — "Brian is a formal advisor to \[Vendor\]. Advisor relationships are always disclosed and never affect ranking or inclusion." Conditional on the Advisor flag.
- Admin sees the Edit button in the action row (not a separate corner element).

**Compare button note:** ship as a UI-only stub now; real behavior built in Phase 8.5.

---

## Phase 3b — Communities profile page

### Phase 3b.0 — Investigate, then produce a rendered preview before building for real

The `community_profiles` overlay table (~38 columns) and untracked columns (`demographic`, `cost_note`, `sponsor_name`, `metros_json`, `local_markets`) found in Phase 0 aren't accounted for in this plan yet. Communities is the one page in this whole build where the field set is genuinely uncertain — Software's design was already stress-tested against real data through many rounds of mockup iteration; Communities hasn't been. Don't build the full page against assumptions here.

1. Inventory `community_profiles`: full column list + a sample of populated data.
2. For each untracked column and each `community_profiles` column, **grep the codebase** for where it currently renders (templates, routes) — mechanical check, not a memory question. Report which are actually live in the UI today versus vestigial.
3. Propose a field-to-UI mapping. **The filter is reader value, not data existence** — a column being populated doesn't mean it belongs on the page. Ask "does this actually help someone evaluate whether this community is right for them" before including it. Flag anything borderline rather than including it by default.
4. **Build a rendered preview** (not just a written table) of 1-2 real communities using the proposed mapping against the Phase 3 visual system. This is the deliverable Brian reacts to — a live page, since layout and hierarchy decisions are hard to evaluate from a text proposal alone.

**Hard gate: do not proceed to the full Phase 3b build until Brian has reviewed the rendered preview and approved it, in whole or with corrections.** Expect at least one round of feedback, same as Phase 8.0's mapping gate.

### Build (once the 3b.0 preview is approved)

Apply the Phase 3 visual system, adapted:

- Communities have Cost band, Access, Sponsorship type, Format, Reach — plus whatever Phase 3b.0 surfaces from `community_profiles`. Add a "Details" card (right column) for these as label/value rows — final field list depends on 3b.0.
- **No warm-intro button** — confirmed, Communities has no equivalent field. Don't build it.
- **No Competitors/"Similar communities" card in this phase** — deferred to Phase 5, which now covers both Software and Communities together (schema, admin curation UI, and suggestion logic built once, applied to both types). Not omitted permanently, just sequenced after Phase 3b so this phase isn't held up reopening schema that's already been reviewed and approved.
- **No Agent taxonomy** — Software-specific framing, omit entirely.
- **Screenshot capture gets built from scratch** — Communities has no Playwright capture today. Extend the existing Software mechanism (`linklib/screenshots.py`) to Communities entries, plus the admin UI (Screenshot URL field + "Recapture from homepage" button) on the Communities edit form.
- Everything else (Featured sticker, Advisor asterisk + footnote, admin-only Edit, Description card, Compare button as UI-only) carries over unchanged.

---

## Phase 4 — Edit-page button reorg

**Button naming rule: all labels max 2-3 words.**

**Software (confirmed applicable):**
1. Rename the description-generation button to **"Generate descriptions"**.
2. Add a **"Generate"** button next to "How this differs from the competition" — none exists today; reuse existing AI-generation infrastructure.
3. Move the Agent-taxonomy-generating trigger (currently "Refresh AI research") up next to **Agent taxonomy**, relabel to signal it does both (e.g. **"Generate AI Data"**). Keep its combined behavior.
4. Move the screenshot-puller button up next to **Screenshot URL**.
5. Remove old button locations after moving.
6. Remove "(optional — ...)" parenthetical labels on non-required fields; keep useful instruction text, drop just the word "optional."

**Communities:** items 3 and 4 don't apply — no Agent taxonomy/Features generation, and screenshot capture is new (Phase 3b builds its button placement fresh, not a move). Before applying item 1 (rename to "Generate descriptions"), **confirm Communities' edit form actually has an equivalent generate button** — not verified in Phase 0. Item 6 (remove "optional" labels) applies wherever it's found on either form.

---

## Phase 5 — Competitors / Similar-entities auto-suggestion (Software AND Communities) — COMPLETE

**Shipped for both types, verified against code in a dedicated investigation
session (2026-08-01) — no build work remains.** This landed in
[PR #249](https://github.com/bmweis/cfo-navigator/pull/249) ("AI-first-pass
Competitors/Similar-communities upgrade"), ahead of where this doc originally
sequenced it (after Phase 4/6/7) — it shipped bundled into the AI-first-pass
principle work instead of as a standalone phase. The bullets below described
the target scope at planning time; what's left is a record of what actually
exists, confirmed by code inspection, not a to-do list.

- **Schema**: `community_competitors` (`linklib/db.py`) — a real relational
  join table, structural mirror of `tool_competitors` (normalized pair with
  the smaller id first, same `UNIQUE(community_id, competitor_id)`
  constraint, same OR-both-sides lookup). Full CRUD exists:
  `add_community_competitor`, `remove_community_competitor`,
  `list_community_competitors`, `suggest_community_competitors` (tag-overlap
  shortlist) — one-for-one mirrors of the Software functions of the same
  name. `delete_community` cascades cleanup into this table. Documented in
  `ARCHITECTURE.md` (schema table + ER diagram).
- **Suggestion logic drafts a full list, not just ranking**: `POST
  /admin/tools/communities/{community_id}/competitors/generate-matches`
  (`webapp/app.py`) runs `suggest_community_competitors`'s tag-overlap
  shortlist through the shared `linklib.enrich.generate_competitor_matches()`
  judgment function — the same one Software's equivalent route uses, since
  the underlying task (pick genuine matches from a pre-filtered shortlist) is
  identical for both entity types. Returns JSON only; nothing is written
  here.
- **Review-before-publish gate, same as every other AI-drafted field**: the
  frontend `generateCommunityCompetitorMatches()` JS pre-checks the returned
  candidate ids in the suggestion checkbox list. Only when a human submits
  "+ Add selected" (`POST
  /admin/tools/communities/{community_id}/competitors/add-selected`) does
  `add_community_competitor` actually write a row — AI drafts, Brian
  approves, exactly like every other Generate button in this build.
- **Admin edit-form picker**, matching Software's Competitors card
  structurally: a "Similar communities" section on
  `/tools/communities/{slug}/edit` with the current list (per-row "Remove"),
  a "Suggested — shares a tag" checkbox list with the "Generate summary"
  button, "+ Add selected", and a manual "+ Add a similar community by name"
  dropdown.
- **Public profile card**: `/tools/communities/{slug}` renders a "Similar
  communities" card using the same `tp-chip-row` treatment as Software's
  Competitors card, linking to each curated similar community. Admin-only
  empty-state nudge ("No similar communities curated yet.") when nothing's
  curated, same pattern used elsewhere on the profile pages.

---

## Phase 6 — Features section: collapsible (Software admin edit page only)

- Collapsible, matching the native `<details>/<summary>` pattern already in use elsewhere.
- Collapsed by default. Badge showing feature count and count needing verification.
- **Software-only** — Communities has no Features section on its edit page (until Phase 8 changes that).
- Distinct from the public profile page's Features card (Phase 3/3b), which is not collapsible.

---

## Phase 7 — Reorder: Warm intro below Features (Software admin edit page only)

- Confirmed Software-only — Communities has no warm-intro field.

---

## Phase 8 — Feature Taxonomy + Compare

**Superseded 2026-08 — this phase no longer describes what got built.** The
Feature Family model below (Phase 8.1-8.5, as originally planned) was
replaced before any of it was built: docs/FEATURE_TAXONOMY.md is now the
canonical rules document, and the real model is **Category → curated
feature list (~10-15 per category) → tool-feature link**, with designations
(availability, AI-enabled, verified date, note) living on the LINK, never on
the feature. **There are no Feature Families** — no grouping layer above the
per-category flat list; like features sit together via curated `sort_order`
within a category, a display concern, not a schema layer. See CLAUDE.md's
"Feature Taxonomy" notes (Phase 0/1 investigation and build) for the full
history, including why the pilot's three category names didn't map onto the
existing 15-tag `tool_categories` vocabulary and how that got resolved
(reuse ERP/FP&A, add Close Management as a genuine new pill).

### What's actually built (Phase 1, this repo)

- `category_features` / `tool_feature_links` / `feature_review_queue`
  (schema in `linklib/db.py`, admin CRUD in `webapp/app.py`) — see
  ARCHITECTURE.md's Feature Taxonomy section for the full table shapes and
  admin routes.
- Seeded from a nine-vendor pilot (Rillet/Campfire/NetSuite → ERP,
  Runway/Abacum/Aleph → FP&A, FloQast/Numeric/Ledge → Close Management) via
  the idempotent `scripts/seed_feature_taxonomy.py`, reading
  `scripts/seed_data/*.csv`.
- Admin-only: Manage Features (per category, `/admin/tools/software/features`),
  Manage Tool Features (a checklist on each tool's own edit page), and the
  Feature Review Queue (`/admin/tools/software/feature-review-queue`) — approve,
  edit-then-approve, or deny any proposal (admin/scan/public source) before
  it reaches the live tables (rules doc §9).
- `tools.suite_note` — free-text suite-membership notation (rules doc §5's
  "beyond the office of the CFO" case), independent of the feature tables.

### Still not built — later phases, unchanged in spirit from the original plan

- **Public profile-page rendering** of the governed model (still reads the
  legacy free-text `tool_features` for every tool today — the read-time
  branch on `category_has_features()` is written and ready, but nothing
  calls it from the public Features card yet). Needs real brand/visual
  spec work, not just a data-source swap.
- **The recurring AI scan tool** (rules doc §10 — origination mode for a
  brand-new category, freshness mode for re-checking an existing one).
  `feature_review_queue.source='scan'` and the CSV-seeded pilot proposals
  already exercise the queue's scan path end to end; the actual recurring
  scan job that populates it going forward doesn't exist yet.
- **The public feedback/suggestion UI** (rules doc §9's "public suggestion
  channel requirements" — `feature_review_queue.submitter_name`/
  `submitter_email` are already nullable columns waiting for this). The
  investigation into reusable contact-form infrastructure (rate limiting,
  honeypot, spam keyword filter) is done; no UI or route exists yet.
- **A real Compare view for the governed model.** The existing
  `/tools/compare` route still reads the legacy `tool_features` union-of-
  names approach; rebuilding it against category_features/
  tool_feature_links (grouped by category rather than a Feature Family
  header, per the model above) is unscheduled.

---

## Phase 9 — Per-item memory, generalized via similarity

Builds on Phases 5 and 8 — memory is corrections *about* competitors/similar-entities and normalized features, so both need to exist first. Sequence after both.

### Decided

- **Silent application, not a surfaced flag.** When memory exists for a field, it feeds directly into that field's next AI draft as context. The draft itself is smarter — there's no separate "heads up, you corrected this before" interstitial. Review-before-publish (the AI-first-pass standing principle) is still the approval gate; memory just makes what gets reviewed better.
- **Two retrieval modes, one table.** Direct lookup when regenerating the *same* entity (pull everything — volume per item will be small). Similarity-based lookup when drafting a *brand-new* entity with no memory of its own — reuse the existing embeddings infrastructure (same mechanism as Phase 8's feature-candidate suggestions and FP&A Buddy retrieval), capped at the top 3-5 most similar entries' notes. No new retrieval infrastructure, just a new consumer of what exists.
- **Capture is cheap and automatic; reasoning is optional and manual.** Every time Brian edits or overwrites an AI draft, capture the before/after diff automatically — no LLM call needed for this part. Add an optional manual note field so Brian can write the "why" when it matters (e.g. "not a competitor — different market segment"), rather than forcing every correction to carry inferred reasoning.
- **Memory is visible and editable, admin-only, never public.** A "Memory" section on each entity's edit page (Software and Communities both) shows accumulated notes for that item, with the ability to delete or fix a bad one. Same visibility gate as Edit/verification-count/every other admin-only element in this build — **never rendered anywhere a public visitor can see it**, and not exposed via any public API or page.
- **Categories map to existing schema, not a new ontology.** Memory is keyed to the same structures already being built — Agent taxonomy, Features/Feature Families (Phase 8), Competitors/Similar-entities (Phase 5), and Communities-specific fields like `seniority_band`. This isn't a parallel "learnings" system, it's a correction-history layer on structures that already exist.

### Phase 9.0 — Investigate, report back before coding

1. Confirm Phase 5 and Phase 8 are both fully merged before starting — hard dependency, don't begin schema work otherwise.
2. Confirm the existing embeddings infrastructure's exact retrieval pattern (used in Phase 8 and FP&A Buddy) for reuse here.
3. Propose the memory table shape (entity type, entity id, category/field, diff snapshot, optional manual note, timestamp) — report back before building.

### Phase 9.1 — Schema

- `entity_memory` table (or similar): entity_type (`software` | `community`), entity_id, category (maps to the field/structure it corrects — e.g. `competitors`, `agent_taxonomy`, `seniority_band`), diff_before, diff_after, manual_note (nullable), created_at.
- Update ARCHITECTURE.md.

### Phase 9.2 — Capture

- Hook into the existing save flow for every AI-generatable field (per the AI-first-pass principle) — on save, if the field was AI-drafted and then edited, write a memory row automatically.
- Add the manual-note UI on the edit page's Memory section.

### Phase 9.3 — Retrieval + generation integration

- Direct retrieval: when regenerating a field for an entity that already has memory, inject its own memory notes into the generation prompt.
- Similarity retrieval: when generating for an entity with no memory of its own, pull top 3-5 similar entities' notes (same category) via embeddings similarity, inject as context.
- Admin-only Memory section UI: view accumulated notes per entity, delete individual entries.

---

## Process reminders

- Phase 0 is done — its findings are baked into the phases above.
- Phase 3b.0 (community_profiles investigation → rendered preview) gates the rest of Phase 3b, same pattern as Phase 8.0's mapping gate — needs Brian's explicit approval of the live preview, not just the written proposal.
- Phase 2 before Phase 3/3b. Phase 3 before Phase 3b. Phase 8 after Phase 3/3b are live.
- Phase 8.0's cleanup mapping proposal needs Brian's explicit approval before 8.1/8.2 — hard gate, not a formality.
- One PR per phase minimum; several phases need their own full session.
- Confirm CI is green before merging.
- Flag any new em dashes in copy (full sentence context) before shipping.
