"""Column-aware voice/typography scan over every static Python/CSV "seed
source" in this codebase — a static list/dict/CSV that some startup-sync
hook or one-time script syncs into a live `Library` table.

This closes the exact gap the seed-sync infinite-loop investigation (2026-09,
see CLAUDE.md's "Voice review queue" and the `_seed_toolbox()`/
`scripts/seed_communities.py` write-up) found: the live DB scanner
(`linklib.voice_db_scan.scan_db_copy`, `/admin/checks`) only ever sees
already-normalized `_voice_fix`-corrected text, since every write path runs
that backstop before storing — so a violation sitting in a SEED SOURCE FILE
(raw, pre-write text, re-synced against the DB on every boot by
`_seed_toolbox()`/a one-time script) is invisible to it forever, even though
it's the actual root cause of a live, repeating correction.

**Deliberately reuses `linklib.voice_db_scan`'s own column-aware machinery
(`_scan_value`/`_SCAN_TABLES`), not a second, independent notion of what's
scanned or exempt.** A seed source's field is checked against exactly the
same (table, column) rules the live scanner already applies to that table's
real rows — a `name` field is typography-exempt wherever the real column is
(third-party entity names/curated labels can legitimately carry an
ampersand), while `notes`/`description`/`definition`-shaped fields are not.

**Deliberately NOT added to `linklib.voice_review.VOICE_SCANNED_FILES`** —
that scanner treats an entire file as undifferentiated Python source text,
with no per-field/per-column awareness at all. Scanning these seed files
that way would incorrectly flag every legitimate ampersand in a company/
community/category name (e.g. "Finance & Accounting for Bioscience") as a
bare-ampersand violation, exactly the false-positive risk `voice_db_scan.py`'s
own `_SCAN_TABLES` comment documents and exists to avoid.

Seed sources found and their disposition:

  SCANNED (this file):
  - scripts/seed_communities.py: COMMUNITIES (table=communities) and
    CATEGORIES (table=community_categories)
  - scripts/seed_tools.py: TOOLS (table=tools)
  - webapp/app.py: _DEFAULT_BENCHMARKS (table=benchmarks) and
    _DEFAULT_TOOL_CATEGORIES/_DEFAULT_CATEGORY_DESCRIPTIONS (table=tool_categories)
  - scripts/seed_book_recommendations.py: BOOKS (table=benchmarks)
  - scripts/seed_data/seed_category_features.csv (table=category_features)

  OUT OF SCOPE, reported rather than silently force-included or dropped —
  none of these feeds a table that's in `voice_db_scan._SCAN_TABLES`, so
  there's no corresponding scan config to map a column-aware check onto:
  - scripts/seed_data/seed_review_queue.csv (feature_review_queue — a
    pending-approval proposal queue, not live content)
  - scripts/seed_data/seed_tool_feature_links.csv (tool_feature_links — a
    link table with no free-text prose column of its own)
  - scripts/seed_data/headcount_planning_feature_framework.json and
    neobanking_feature_framework.json (also feature_review_queue proposals,
    via scripts/remap_queue_to_framework.py)
"""
from __future__ import annotations

import csv
import importlib
import os
import sys

from linklib.voice_db_scan import _SCAN_TABLES, _scan_value

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# {table_name: (columns, typography_exempt_columns)} — the exact live
# scanner config, indexed for lookup by this file's own per-source checks.
_TABLE_CONFIG = {t: (cols, exempt) for t, _id, cols, exempt in _SCAN_TABLES}


def _seed_communities_module():
    sys.path.insert(0, _ROOT)
    return importlib.import_module("scripts.seed_communities")


def _seed_tools_module():
    sys.path.insert(0, _ROOT)
    return importlib.import_module("scripts.seed_tools")


def _seed_book_recommendations_module():
    sys.path.insert(0, _ROOT)
    return importlib.import_module("scripts.seed_book_recommendations")


def _scan_records(table: str, records: list[dict], id_key: str = "name") -> list:
    """Scans a list of dict-shaped seed records against `table`'s own
    _SCAN_TABLES columns/exemptions. Returns every DbCopyViolation found."""
    columns, exempt = _TABLE_CONFIG[table]
    violations = []
    for rec in records:
        row_id = rec.get(id_key, "?")
        for col in columns:
            value = rec.get(col)
            if not value:
                continue
            violations.extend(
                _scan_value(table, row_id, col, value, check_typography=col not in exempt)
            )
    return violations


def _report(violations: list, source: str) -> str:
    lines = [f"{source}: {len(violations)} violation(s) found —"]
    for v in violations[:25]:
        lines.append(f"  {v}")
    return "\n".join(lines)


# --- Real seed-source scans -------------------------------------------------
#
# These are BASELINE-REGRESSION guards, not zero-tolerance gates: a full
# search (this file's own module docstring) found real, pre-existing
# mechanical-rule/typography violations already sitting in these seed
# sources — most of them a lazy "&" in a `tools.description`/
# `communities.demographic` field (e.g. "CFOs & senior finance leaders"),
# unrelated to the two spaced-em-dash `communities.notes` fixes this same
# investigation made (scripts/seed_communities.py's Proformative/Bioscience
# rows — see CLAUDE.md's "Voice review queue" write-up). Hard-failing CI on
# the full pre-existing count would either block this PR on an unrelated,
# much larger content cleanup (43 findings across 3 files, none part of the
# two-fix scope this PR was asked to make) or force a mass, unreviewed
# rewrite of real seed content with no human sign-off — exactly what
# CLAUDE.md's "no copy rewritten without Brian seeing before and after"
# rule exists to prevent. Each baseline below is the exact count found
# by this test's own scan at the time it was written (verified against the
# real files, not guessed); a future PR that ADDS a new violation to one of
# these sources fails this test the moment it does, and a future cleanup
# pass (mirroring `scripts/fix_spaced_em_dashes.py`'s own preview/apply/
# write-then-read-back convention) can lower a baseline once real content
# is fixed. Every violation is printed on failure regardless of whether the
# count changed, so a CI run always shows exactly what's outstanding.
_BASELINE = {
    "scripts/seed_communities.py COMMUNITIES": 19,
    "scripts/seed_communities.py CATEGORIES": 0,
    "scripts/seed_tools.py TOOLS": 19,
    "webapp/app.py _DEFAULT_BENCHMARKS": 0,
    "webapp/app.py _DEFAULT_TOOL_CATEGORIES": 0,
    "scripts/seed_book_recommendations.py BOOKS": 1,
    "scripts/seed_data/seed_category_features.csv": 4,
}


def _assert_within_baseline(violations: list, source: str) -> None:
    baseline = _BASELINE[source]
    report = _report(violations, source)
    print(report)
    assert len(violations) <= baseline, (
        f"{source} has {len(violations)} violation(s), exceeding its recorded "
        f"baseline of {baseline} — a new violation was introduced. See the "
        f"printed report above for exactly which one(s).\n{report}"
    )


def test_seed_communities_communities_list_within_baseline():
    mod = _seed_communities_module()
    violations = _scan_records("communities", mod.COMMUNITIES)
    _assert_within_baseline(violations, "scripts/seed_communities.py COMMUNITIES")


def test_seed_communities_categories_list_within_baseline():
    mod = _seed_communities_module()
    records = [{"name": name, "description": desc} for name, desc in mod.CATEGORIES]
    violations = _scan_records("community_categories", records)
    _assert_within_baseline(violations, "scripts/seed_communities.py CATEGORIES")


def test_seed_tools_list_within_baseline():
    mod = _seed_tools_module()
    violations = _scan_records("tools", mod.TOOLS)
    _assert_within_baseline(violations, "scripts/seed_tools.py TOOLS")


def test_default_benchmarks_within_baseline():
    from webapp import app as webapp_app
    violations = _scan_records("benchmarks", webapp_app._DEFAULT_BENCHMARKS)
    _assert_within_baseline(violations, "webapp/app.py _DEFAULT_BENCHMARKS")


def test_default_tool_categories_within_baseline():
    from webapp import app as webapp_app
    records = [
        {"name": name, "description": webapp_app._DEFAULT_CATEGORY_DESCRIPTIONS.get(name, "")}
        for name in webapp_app._DEFAULT_TOOL_CATEGORIES
    ]
    violations = _scan_records("tool_categories", records)
    _assert_within_baseline(violations, "webapp/app.py _DEFAULT_TOOL_CATEGORIES")


def test_seed_book_recommendations_within_baseline():
    mod = _seed_book_recommendations_module()
    violations = _scan_records("benchmarks", mod.BOOKS)
    _assert_within_baseline(violations, "scripts/seed_book_recommendations.py BOOKS")


def test_seed_category_features_csv_within_baseline():
    csv_path = os.path.join(_ROOT, "scripts", "seed_data", "seed_category_features.csv")
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [
            {"name": r["feature_name"], "definition": r.get("definition", ""),
             "pointer_note": r.get("pointer_note", "")}
            for r in csv.DictReader(f)
            if any((v or "").strip() for v in r.values())
        ]
    violations = _scan_records("category_features", rows)
    _assert_within_baseline(violations, "scripts/seed_data/seed_category_features.csv")


# --- Column-awareness demonstration -----------------------------------------

def test_name_field_ampersand_is_not_flagged_but_notes_field_would_be():
    """The exact column-aware behavior this test file exists to prove: the
    SAME text ("X & Y") is exempt on a typography-exempt column (name) and
    flagged on a non-exempt one (notes) — a blind whole-file scan can't tell
    the difference, but this scan, keyed off the real _SCAN_TABLES config,
    does."""
    text = "Finance & Accounting for Bioscience"

    # name is typography-exempt for `communities` — real production-shaped
    # text, confirmed against scripts/seed_communities.py's own Bioscience
    # community entry (a genuine live ampersand in a community name).
    mod = _seed_communities_module()
    bioscience = next(
        c for c in mod.COMMUNITIES if "Bioscience" in c["name"]
    )
    assert "&" in bioscience["name"]
    _columns, _exempt = _TABLE_CONFIG["communities"]
    assert "name" in _exempt, "communities.name is expected to be typography-exempt"
    name_violations = _scan_value(
        "communities", bioscience["name"], "name", bioscience["name"],
        check_typography="name" not in _exempt,
    )
    assert not name_violations, (
        f"communities.name should be typography-exempt, but got: {name_violations}"
    )

    # notes is NOT typography-exempt for `communities` — the identical
    # ampersand-bearing text, in a field the live scanner does not exempt,
    # must be flagged.
    notes_violations = _scan_value("communities", "synthetic", "notes", text, check_typography=True)
    assert notes_violations, (
        "communities.notes is not typography-exempt and should have flagged "
        "the bare ampersand, but nothing was found"
    )
    assert any("&" in v.excerpt or "ampersand" in v.rule.lower() for v in notes_violations)


def test_the_scan_actually_fires_on_a_real_violation():
    """Proves this isn't a check that can never fail — a hand-built seed
    record with a spaced em dash in a non-exempt column must be caught,
    the same shape as the real Proformative/Bioscience communities.py bug
    this whole investigation started from."""
    records = [{"name": "Example Community", "local_markets": "Boston — with a spaced em dash."}]
    violations = _scan_records("communities", records)
    assert violations, "expected the spaced em dash in `local_markets` to be flagged"
    assert any(v.column == "local_markets" for v in violations)


def test_the_scan_does_not_flag_a_clean_record():
    records = [{"name": "Clean Co & Associates", "local_markets": "Boston—with a correct unspaced em dash."}]
    violations = _scan_records("communities", records)
    assert not violations
