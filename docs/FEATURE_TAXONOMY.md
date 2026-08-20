# CFO Toolbox — Feature Taxonomy Rules v1

Status: draft for Brian's approval. Once approved, this document is the single source of truth for
(a) the Claude Code schema/build prompt and (b) the system prompt for the AI feature-scan tool.
Derived from the nine-vendor normalization pilot (Trios: Rillet/Campfire/NetSuite,
Runway/Abacum/Aleph, FloQast/Numeric.io/Ledge), August 2026.

---

## 1. The model

**Category → Feature list → Tool-feature link.**

- Every tool belongs to a category (ERP/Accounting, FP&A Planning, Close Management, etc.).
- Each category owns a curated, flat list of ~10-15 features. This list is the controlled
  vocabulary — tools do not bring their own features; they are mapped against the category's list.
- A tool-feature link records that vendor's implementation: availability, AI flag, verified date,
  optional note.
- **No Feature Families.** No grouping layer. Like features sit together via curated sort order
  within the category — a display concern, not a data model layer.
- New features enter a category's list only through the review gate (§8). This is what makes
  consistency self-enforcing: a new tool cannot reintroduce vocabulary drift because it has no
  channel to.

## 2. Principle 1 — Consistent vocabulary

Same capability = same words, everywhere.

- If a capability exists in more than one category (Multi-Entity Consolidation in both ERP and
  FP&A; Anomaly Detection in ERP and Close), it appears as a **separate row per category with the
  identical name**. Duplicate-with-naming-discipline, not a shared global pool.
- Before adding any feature, check every category's list for the same capability under any name.
  If found, reuse that exact name.

## 3. Principle 2 — Outcomes, not solutions

A feature names the job done from the buyer's perspective, never the vendor's implementation or
branding.

Naming rules:
- No vendor product names, brand names, or trademarked terms (not "Ember," not "AutoRec," not
  "Ambient Intelligence").
- No "AI-powered" / "AI-driven" / "agentic" in feature names. AI-ness is a link-level flag (§6),
  not part of the name.
- Prefer noun phrases describing the capability or "Automated X" describing the job:
  *Automated Journal Entry Creation*, *Multi-Entity Consolidation*, *Bank Reconciliation
  Auto-Matching*, *Post-Close Folder Lockdown*.
- Where vendors use divergent jargon for the same outcome, define the feature by the outcome and
  verify against the outcome, not the term. Worked example: **Multi-Book Accounting** = "parallel
  GAAP, tax, and IFRS ledgers natively on the same underlying data" — verifiable even when a
  vendor never says "multi-book."

## 4. Principle 3 — Curated, not comprehensive

~10-15 features per category. This is a selection discipline, not a completeness target.

Keep:
- **Differentiators** — vendors in the category split on it. This is what drives a buy decision.
- **Table stakes worth confirming** — everyone credible has it; buyers want the baseline verified.
- **Standouts** — genuinely unique capabilities, including novel ones (e.g., agentic/MCP access).

Cut:
- Marketing filler and micro-features nobody decides on.
- Anything that is neither clearly common nor clearly differentiating.

UI framing: feature lists are presented as **"Key features"** — never implying completeness.

## 5. Product features only

A feature is a product capability. Excluded entirely from the feature system:
- Vendor services (implementation speed, white-glove onboarding, included support, support staffing).
- Commercial terms (pricing model, seat structure, published pricing, partner/SI networks).

Test: *if it's humans doing work, or contract terms, it's not a feature.* These may live elsewhere
on a tool profile later, but never in the feature list.

**Suite membership: notation with one exception.** When a tool is part of a vendor's broader
offering, the test is whether the adjacent capability is covered by the CFO Toolbox itself:

- **Covered by another Toolbox category** (e.g., bill pay, procurement, billing): list it as a
  feature row in the current category, with a pointer note — the buyer may want to compare the
  embedded option against standalone products in that category.
- **Beyond the office of the CFO** (e.g., CRM, HRIS): goes to a standard, reusable suite notation
  on the tool profile — generic text noting the vendor offers a suite of operational solutions for
  teams beyond the CFO's office. Same notation text serves NetSuite, Workday, and any future
  qualifiers.

Pointer notes are free text for now, not a schema relationship.

## 6. Link-level designations

Stored on the tool-feature link — never on the feature itself (the same feature is AI-driven at one
vendor and rules-based at another).

**Availability** — exactly one of:
- `native` — included out of the box in the standard offering.
- `add_on` — costs more: paid module, upgrade, or higher-tier gating. (Tier-gated features are
  add-ons; the buyer question is "included in what I'd buy, or costs more?")
- *(absent)* — not available. No row, or an explicit not-available state; never a third label.

**AI-enabled** — boolean. TRUE when the capability is delivered via an intelligent/learning/agentic
approach rather than deterministic rules. This is a buyer-relevant signal (smart vs. rules-based)
and deliberately coarse.

**verified_as_of** — date, required on every link. Three of four pilot grid errors came from
features shipped within the prior 12 months; claims decay fast.

**note** — optional free text for nuance the designations can't carry (e.g., "identifies variances;
commentary manual").

## 7. The don't-collapse / unify test

When two vendors' capabilities look similar, decide at the level of **what job actually gets done**:

- **Same job, different depth or mechanism → one feature.** The AI flag and link note carry the
  difference. Worked example: *Automated Flux Analysis & Summaries* is one feature across ERP,
  FP&A, and Close; FloQast links TRUE with AI=No (variance fields, manual commentary), Numeric and
  Ledge link TRUE with AI=Yes (drafted narrative).
- **Different job → different features**, even when the vendor language rhymes. Worked example:
  *Real-time Spreadsheet Sync* (links to your existing spreadsheets — FloQast) vs. *Automated
  Working Paper Generation* (generates live-formula workpapers — Ledge). Both are "spreadsheet
  integration"; they are different features.
- Never match on shared buzzwords ("AI," "variance," "real-time"). Match on the outcome.

## 8. Verification and sourcing

Every claim is a claim until verified. Sourcing hierarchy, strongest first:

1. Vendor changelogs and API documentation (the multi-book find: release notes state capabilities
   plainly, without marketing framing)
2. Help centers / support documentation
3. Vendor product pages
4. Vendor press releases
5. Independent reviews and analyst writeups
6. Vendor-authored competitor comparisons — lowest; a vendor grading itself against rivals

Rules:
- Unverified stays unverified. A plausible claim that can't be sourced gets a `?`/pending state,
  not a TRUE (worked example: Campfire multi-book).
- Record `verified_as_of` and, where practical, the source tier.

## 9. The review gate

No proposed change reaches the live feature tables without Brian's approval — regardless of who
or what proposed it. The review queue is multi-source; every entry carries a `source`:

- `admin` — Brian's own edits (may fast-path, but still logged through the queue)
- `scan` — the recurring AI scan workflow (§10)
- `public` — vendor/user suggestions submitted from the live site

The gate — not withholding AI assistance — is what protects the directory's neutral-curator
positioning. AI and the public draft; Brian decides.

**Public suggestion channel requirements** (schema now, UI in a later phase):
- Visitors can, per tool-feature: question/flag a claim, suggest a designation change
  (TRUE↔FALSE, native↔add-on, AI flag), or propose a new key feature for the category.
- Before submitting an addition or change, the UI surfaces a plain-language summary of the
  guidelines (§3-§5: outcome-oriented, no marketing jargon, curated not comprehensive) and
  requires the submitter to articulate why their suggestion adheres and is worth a slot. The
  articulation is part of the queue entry Brian reviews.
- Submissions require submitter name (first and last) and email.
- Flag/question submissions are lighter-weight: no articulation required to say "I don't think
  this is right — please review" (name and email still required).
- Suggestions never write to live tables. Build-phase considerations: rate limiting and basic
  abuse protection; guideline-summary dialog copy is Brian's to draft; Phase 0 of the build
  investigates existing site contact flows for reusable form/validation infrastructure before
  building new.

## 10. Scan tool behavior (system-prompt requirements)

The scan operates in two modes, both under these rules:

**Origination mode** (backfilling a category with no feature table yet): given the category, its
tool roster, and this rules document, research each tool against the §8 sourcing hierarchy and
propose the full curated feature table — ~10-15 features per §4, named per §3, with proposed
tool-feature links carrying designations per §6. The entire proposal lands in the review queue;
nothing is created until approved. This is how every category beyond the three pilot categories
gets built.

**Freshness mode** (recurring scan of an existing category):

1. Input: the category's current feature list (the controlled vocabulary) and its tool roster.
2. For each tool, check primary sources per the §8 hierarchy — changelogs first — for capabilities
   shipped or changed since each link's `verified_as_of`.
3. Map findings against the existing feature list first. Only when a capability genuinely matches
   no existing feature (per the §7 test) may the scan propose a new feature, named per §3.
4. Output: a review queue of proposed changes — new links, designation changes (e.g., add-on →
   native), new-feature proposals — each with source URL, source tier, and date. Never a direct
   write to live tables.
5. The scan never removes features or links; retirement is a human decision.

## 11. Open items (not yet rules)

- ~~Suite granularity~~ **RESOLVED 8/19** via §5 suite rule v2 (in-Toolbox overlap → feature +
  pointer; beyond the CFO's office → generic notation). One dependency: confirm whether AP/
  procurement and billing categories exist in the Toolbox, to place NetSuite Procurement and
  SuiteBilling.
- ~~Sync depth~~ **RESOLVED 8/19**: transaction-level vs. trial-balance are different features —
  the former is the deeper capability. *Granular Transaction-Level ERP Mirroring* stands.
- Anomaly Detection consolidation, pending Brian's confirm: recommendation is 3 rows → 2 — keep
  *Anomaly Detection* (cross-category: transaction-level error/pattern watching; FloQast's split
  checkmarks prove it's distinct), merge *Out-of-Balance Notifications* into *Continuous
  Reconciliation Monitoring* (identical checkmark pattern; notification is the monitoring's
  alerting surface). **Last items blocking table freeze.**
