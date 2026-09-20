"""DB-backed copy scanner — the database half of voice/typography enforcement.

``linklib/voice_review.py``'s two mechanical checks (banned words/filler/
performative, bare ampersands/spaced em dashes) only ever scan Python
source — deliberately, since CI has no route to a live database (see
CLAUDE.md's "Voice enforcement" notes and ``linklib.brand_check.
outbound_link_problems``'s own docstring, which states the identical
boundary for outbound links). But a growing share of real user-facing copy
lives in the database instead: ``settings`` overrides, ``original_content``,
``ai_surfaces``, ``thought_leadership``, ``tools``, ``communities``,
``community_profiles``, ``category_features.name``, ``benchmarks``,
``tool_categories``/``community_categories.description``.

This module is the reporting half of closing that gap — **live-only,
never CI** (it needs a real ``Library``/database connection, which a
GitHub Actions run structurally can't have — see the Q1 investigation in
this PR's own history for why that stays true). It counts violations; it
never rewrites anything. Any actual copy fix is a separate, human-reviewed
change, per CLAUDE.md's standing "no copy rewritten without Brian seeing
before and after" rule.

``category_features.definition``/``pointer_note`` ARE scanned here as of
the 2026-09 voice-review-queue PR, even though they don't render on any
public page (confirmed: the SQL read path both the public "Key features"
card and the Software Matchmaker use never even SELECTs those two
columns) — this was the exact gap behind the "scanner and auto-corrector
disagree" finding: `Library.add_category_feature`/`update_category_feature`
already run both columns through `normalize_voice_mechanics` (the
spaced-em-dash write-time backstop) before storing, so a violation there
is a real thing the corrector fixes and the review queue needs to be able
to log/backfill, whether or not the text is public-facing. Whether they
*should* ever be rendered publicly (so this scan doubles as user-facing-
copy coverage too, not just correction-tracking) is a separate, open
question — see CLAUDE.md's voice-enforcement section, item 3d.
"""
from __future__ import annotations

from dataclasses import dataclass

from .voice_review import mechanical_findings, typography_findings_plain

# (table, id_column, [text columns], [typography-exempt columns]) — every
# column confirmed, in this PR's own investigation, to render on a real
# public page. `settings` is handled separately below (key/value, not a
# text-column table).
#
# The fourth element is a real, found-before-shipping exception, not
# boilerplate: `tools.name`/`communities.name`/`benchmarks.name` are
# THIRD-PARTY entity names — a real vendor/community/resource name can
# legitimately contain an ampersand ("Bain & Company") or, in principle,
# an em dash. That's exactly the false-positive risk BRAND.md's own
# ampersand rule already documents for source-code scanning ("a vendor or
# community name legitimately containing an ampersand is real data, not a
# copy violation") — confirmed to reproduce here too before shipping
# (`typography_findings_plain("Bain & Company")` flags it). So typography
# rules skip these three name columns; mechanical rules (banned words/
# filler/performative) still apply everywhere, since a literal company
# name being "Robust Software Inc." is a risk with no real precedent.
# `category_features.name`/`tool_categories.name`/`community_categories.name`
# are Brian's OWN curated vocabulary (feature/category labels, not
# third-party names), so mechanical rules still apply like any other copy
# he writes — and "FP&A" as a category name is already handled by the
# shared AMPERSAND_ACRONYMS allowlist `typography_findings_plain` reuses.
#
# Voice-review-queue PR (2026-09): `thought_leadership.title` (2 confirmed
# production ampersand findings, title-shaped, e.g. an externally-hosted
# event/piece name) is added to the typography-exempt set below, same
# reasoning as the tools/communities/benchmarks name columns: a real
# curated title can legitimately carry an ampersand or (in principle) a
# dash the same way a third-party entity name can. Mechanical rules
# (banned words/filler/performative) are UNCHANGED — they still apply.
#
# `category_features.name` is DELIBERATELY NOT added to this exempt set,
# even though real production findings there (22, e.g. "Sales & Marketing"-
# shaped feature names) motivated the same question — see the existing,
# committed `test_category_features_name_ampersand_is_still_scanned`: this
# column is Brian's own curated feature vocabulary, not a third-party name,
# so the deliberate design (from the PR that first scanned this table) is
# to keep it in typography scope and resolve a legitimate ampersand via the
# shared AMPERSAND_NAMES/AMPERSAND_ACRONYMS allowlists instead — the same
# mechanism that already covers "Sales & Marketing" and now G&A/L&D.
# Exempting the whole column here would silently let a real "X and Y" typed
# lazily as "X & Y" through unflagged, which the allowlist approach doesn't.
_SCAN_TABLES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("original_content", "id", ("title", "teaser", "tag_label", "link_label", "body_md"), ()),
    ("ai_surfaces", "id", ("title", "teaser", "body_md"), ()),
    ("thought_leadership", "id", ("title", "venue", "date_label", "description"), ("title",)),
    ("tools", "id", ("name", "description", "summary", "agent_taxonomy_note",
                      "competitive_differentiation", "suite_note"), ("name",)),
    ("communities", "id", ("name", "demographic", "cost_note", "notes", "local_markets"), ("name",)),
    ("community_profiles", "community_id", (
        "ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
        "sponsor_relationship_note", "application_friction", "cost_value_verdict",
        "notable_members", "public_criticism", "verdict_summary", "business_model",
        "primary_purpose", "cpe_eligible", "platform_type", "meeting_format",
        "event_style", "seniority_band", "resources_included", "stage_focus",
        "jobs_program", "team_or_individual",
    ), ()),
    # name is the public feature label on the "Key features" card;
    # definition/pointer_note are admin-only (see module docstring) but ARE
    # scanned, since `_voice_fix` already runs against them at write time.
    # name stays in typography scope (no exempt column) — see the comment
    # above _SCAN_TABLES for why this differs from tools/communities/
    # benchmarks/thought_leadership's own name/title exemptions.
    ("category_features", "id", ("name", "definition", "pointer_note"), ()),
    ("benchmarks", "id", ("name", "description"), ("name",)),
    ("tool_categories", "id", ("name", "description"), ()),
    ("community_categories", "id", ("name", "description"), ()),
)

# settings.key values that hold real UI copy — see CLAUDE.md's Part 1
# finding for the full table. Each falls back to a source-resident
# `*_DEFAULT`/`_HTIB_*_DEFAULT` constant when empty; those code defaults
# are already covered by `VOICE_SCANNED_FILES` (webapp/app.py's own string
# literals) — only a live, saved override is unique content this scanner
# needs to reach.
_SCAN_SETTINGS_KEYS = (
    "homepage_headline_copy", "homepage_subhead_copy", "homepage_teaser_copy",
    "homepage_expanded_copy", "about_page_copy", "htib_before_copy", "htib_after_copy",
)


@dataclass(frozen=True)
class DbCopyViolation:
    table: str
    row_id: object
    column: str
    rule: str
    excerpt: str

    def __str__(self) -> str:
        rid = f"id={self.row_id}" if self.row_id is not None else "(setting)"
        return f"{self.table}.{self.column} {rid} — {self.rule}: {self.excerpt}"


def _scan_value(table: str, row_id: object, column: str, value: str, *,
                 check_typography: bool = True) -> list[DbCopyViolation]:
    if not value:
        return []
    out = [DbCopyViolation(table, row_id, column, rule, phrase)
           for rule, phrase in mechanical_findings(value)]
    if check_typography:
        out += [DbCopyViolation(table, row_id, column, rule, excerpt)
                for rule, excerpt in typography_findings_plain(value)]
    return out


@dataclass(frozen=True)
class DbScanReport:
    """What the scan actually DID, not just what it found — the reason this
    exists at all. A clean scan (0 violations, every configured table
    successfully queried) and a scan that silently skipped a table (also 0
    violations, by construction, since a skipped table is never checked)
    used to render identically on /admin/checks: nothing. Same failure
    class as the summary-banner bug fixed alongside this PR, one layer
    down — a report has to say it ran before "0 findings" means anything.

    `tables_checked`/`tables_skipped` account for every table in
    `_SCAN_TABLES`, so `len(tables_checked) + len(tables_skipped) ==
    len(_SCAN_TABLES)` always holds. `columns_checked`/`columns_configured`
    are (table, column) pairs, not scanned rows — the same shape Brian's
    own "Scanned N columns across M tables" phrasing describes. Settings
    keys are counted separately (`settings_checked`, always all of
    `_SCAN_SETTINGS_KEYS` — `get_setting` never raises) since they're a
    key/value table, not a row-per-record one."""
    violations: tuple[DbCopyViolation, ...]
    tables_checked: tuple[str, ...]
    tables_skipped: tuple[tuple[str, str], ...]  # (table_name, error_str)
    columns_checked: int
    columns_configured: int
    settings_checked: int
    rows_checked: int


def scan_db_copy_report(lib) -> DbScanReport:
    """The real scan — computes both the violations list and the execution
    stats in one pass, so the two can never disagree about what was
    actually checked. `scan_db_copy()` below is a thin backward-compatible
    wrapper over this for every caller that only wants the flat list."""
    violations: list[DbCopyViolation] = []

    for key in _SCAN_SETTINGS_KEYS:
        violations.extend(_scan_value("settings", None, key, lib.get_setting(key)))

    tables_checked: list[str] = []
    tables_skipped: list[tuple[str, str]] = []
    columns_checked = 0
    rows_checked = 0

    for table, id_col, columns, typography_exempt in _SCAN_TABLES:
        cols_sql = ", ".join([id_col, *columns])
        try:
            rows = lib.conn.execute(f"SELECT {cols_sql} FROM {table}").fetchall()
        except Exception as exc:
            # A table/column that doesn't exist yet on an older DB (a
            # migration not yet run) is a no-op here, not a crash — this
            # scanner is a live report, never something that should take
            # /admin/checks down. But it's not a silent no-op any more:
            # recorded here so the report can say so, rather than letting
            # a skipped table look identical to a clean one.
            tables_skipped.append((table, str(exc)))
            continue
        tables_checked.append(table)
        columns_checked += len(columns)
        rows_checked += len(rows)
        for row in rows:
            d = dict(row)
            row_id = d[id_col]
            for col in columns:
                violations.extend(_scan_value(table, row_id, col, d.get(col) or "",
                                               check_typography=col not in typography_exempt))

    columns_configured = sum(len(cols) for _, _, cols, _ in _SCAN_TABLES)

    return DbScanReport(
        violations=tuple(violations),
        tables_checked=tuple(tables_checked),
        tables_skipped=tuple(tables_skipped),
        columns_checked=columns_checked,
        columns_configured=columns_configured,
        settings_checked=len(_SCAN_SETTINGS_KEYS),
        rows_checked=rows_checked,
    )


def scan_db_copy(lib) -> list[DbCopyViolation]:
    """Every mechanical/typography violation across the DB-backed copy
    columns enumerated above, as a flat list. Read-only — one query per
    table, no writes, no auto-fix. `lib` is a live `linklib.db.Library`.

    Back-compat wrapper: every existing caller (and every test written
    against it) wants just the violations. Use `scan_db_copy_report()`
    directly for the execution stats — which tables/columns were actually
    scanned, not just what was found."""
    return list(scan_db_copy_report(lib).violations)
