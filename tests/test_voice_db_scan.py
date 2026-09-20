"""linklib/voice_db_scan.py — the database half of voice/typography enforcement
(2026-09 voice-enforcement PR, Part 2).

Live-only (real Library needed) — this is never a CI test of "does the
production database have violations" (it structurally can't be, see the
module's own docstring), only of the scanner mechanism itself: given a
seeded temp DB, does it find what's really there, table by table, and does
it correctly leave `category_features.definition`/`pointer_note` alone
(neither renders publicly — see CLAUDE.md's Part 1 finding).
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.voice_db_scan import _SCAN_SETTINGS_KEYS, _SCAN_TABLES, scan_db_copy, scan_db_copy_report


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    yield lib
    lib.close()
    if os.path.exists(db):
        os.remove(db)


def test_fresh_db_has_no_violations(lib):
    assert scan_db_copy(lib) == []


def test_settings_override_is_scanned(lib):
    lib.set_setting("homepage_teaser_copy", "A robust, seamless product.")
    violations = scan_db_copy(lib)
    tables = {v.table for v in violations}
    assert "settings" in tables
    assert any(v.column == "homepage_teaser_copy" and v.rule == "buzzword" for v in violations)


def test_settings_key_not_overridden_is_not_scanned():
    """An empty settings value (never saved) falls back to a source-resident
    *_DEFAULT constant — already covered by VOICE_SCANNED_FILES, so an empty
    override contributes nothing here."""
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    try:
        assert scan_db_copy(lib) == []
    finally:
        lib.close()
        os.remove(db)


def test_tools_description_is_scanned(lib):
    lib.add_tool("Acme Corp", "A robust game-changer for finance teams.", "https://acme.example", [])
    violations = scan_db_copy(lib)
    assert any(v.table == "tools" and v.column == "description" for v in violations)
    rules = {v.rule for v in violations}
    assert "buzzword" in rules


def test_tools_em_dash_is_scanned(lib):
    """`add_tool`/`update_tool` already run every write through
    `normalize_voice_mechanics` (the 2026-08 spaced-em-dash backstop), so a
    spaced em dash written through the normal API is fixed before it's ever
    stored — this writes directly via SQL to simulate content that arrived
    some other way (pre-backstop data, or a column the backstop doesn't
    cover, like category_features.definition — the real production case
    this scanner exists for) and confirm the SCANNER still catches it
    regardless of how it got into the database."""
    tid = lib.add_tool("Acme Corp", "clean description", "https://acme.example", [])
    lib.conn.execute("UPDATE tools SET description=? WHERE id=?",
                      ("Handles anomalies — not necessarily a balance change.", tid))
    lib.conn.commit()
    violations = scan_db_copy(lib)
    assert any(v.rule == "spaced-em-dash" for v in violations)


def test_category_features_name_is_scanned_but_definition_is_not(lib):
    """The real production example this PR was built around: a spaced em
    dash in category_features.definition. name (public) is scanned;
    definition/pointer_note (never rendered publicly, confirmed in Part 1
    of this PR's investigation) are deliberately not."""
    cat_id = lib.add_tool_category("Finance")
    lib.add_category_feature(
        cat_id, "Anomaly detection",
        "Identifies unusual or erroneous items and patterns that don't look right — not necessarily a balance change.",
    )
    violations = scan_db_copy(lib)
    assert violations == []   # the violation lives only in `definition`, which isn't scanned

    # Now put the same violation in `name` (the public field) instead.
    lib.add_category_feature(cat_id, "A robust — feature", "harmless definition text")
    violations = scan_db_copy(lib)
    assert len(violations) >= 1
    assert all(v.column == "name" for v in violations if v.table == "category_features")


def test_benchmarks_and_categories_are_scanned(lib):
    lib.add_benchmark("Robust Benchmarks Inc.", "https://example.com", "A seamless resource.")
    lib.add_tool_category("Robust Category", "A seamless tooltip.")
    lib.add_community_category("Another Category", "Also seamless.")
    violations = scan_db_copy(lib)
    tables = {v.table for v in violations}
    assert {"benchmarks", "tool_categories", "community_categories"} <= tables


def test_original_content_and_ai_surfaces_are_scanned(lib):
    lib.add_original_content("test-slug", "A Robust Title", teaser="Seamless teaser.", body_md="Body — text.")
    lib.add_ai_surface("ai-slug", "Title", teaser="A robust teaser.")
    violations = scan_db_copy(lib)
    tables = {v.table for v in violations}
    assert "original_content" in tables
    assert "ai_surfaces" in tables


def test_thought_leadership_is_scanned(lib):
    lib.add_thought_leadership("writing", "A Robust Piece", description="Seamless read.")
    violations = scan_db_copy(lib)
    assert any(v.table == "thought_leadership" for v in violations)


def test_communities_and_profile_are_scanned(lib):
    cid = lib.add_community("Acme Community", "https://community.example", "Finance leaders",
                             "Free", [], cost_note="A seamless membership.")
    lib.upsert_community_profile(cid, ideal_member="A robust fit.", verdict_summary="Seamless overall.")
    violations = scan_db_copy(lib)
    tables = {v.table for v in violations}
    assert "communities" in tables
    assert "community_profiles" in tables


def test_violation_str_is_human_readable(lib):
    lib.set_setting("homepage_teaser_copy", "A robust product.")
    v = scan_db_copy(lib)[0]
    s = str(v)
    assert "settings.homepage_teaser_copy" in s
    assert "buzzword" in s
    assert "robust" in s


# --- add_category_feature/update_category_feature now run definition/ -----
# --- pointer_note through the existing spaced-em-dash backstop -------------
# Found while writing this file's own tests: unlike nearly every other
# narrative-field write path in linklib/db.py, these two never called
# _voice_fix() — almost certainly why the confirmed live production
# violation (category_features id 8's definition) exists in the first
# place. Fixed as part of this PR since it's a small, direct, in-scope
# extension of an already-established backstop, not a new mechanism — see
# linklib/db.py's add_category_feature/update_category_feature.

def test_add_category_feature_normalizes_spaced_em_dash(lib):
    cat_id = lib.add_tool_category("Finance")
    fid = lib.add_category_feature(cat_id, "Anomaly detection",
                                    "Flags items — not necessarily a balance change.")
    row = lib.get_category_feature(fid)
    assert "—" in row["definition"]
    assert " — " not in row["definition"]


def test_update_category_feature_normalizes_spaced_em_dash(lib):
    cat_id = lib.add_tool_category("Finance")
    fid = lib.add_category_feature(cat_id, "Anomaly detection", "clean")
    lib.update_category_feature(fid, "Anomaly detection", "Flags items — not necessarily a balance change.",
                                 "A pointer — note.", 10)
    row = lib.get_category_feature(fid)
    assert " — " not in row["definition"]
    assert " — " not in row["pointer_note"]


def test_missing_table_does_not_crash(lib, monkeypatch):
    """A table/column not yet migrated on an older DB is a no-op, not a
    crash — this scanner renders live on every /admin/checks load and must
    never take that page down."""
    import linklib.voice_db_scan as scan_mod
    monkeypatch.setattr(scan_mod, "_SCAN_TABLES", (("nonexistent_table", "id", ("name",), ()),))
    assert scan_db_copy(lib) == []


# --- Execution reporting (2026-09 follow-up) --------------------------------
# The whole point: a scan that skipped a table and a scan with nothing to
# find must not look identical. scan_db_copy() alone (the flat violations
# list) can't tell the two apart — scan_db_copy_report() has to.

def test_report_states_execution_on_a_clean_scan(lib):
    """A genuinely clean scan (every configured table queried successfully,
    zero violations) reports that it actually ran every table/column/
    settings key — not just that nothing was found."""
    report = scan_db_copy_report(lib)
    assert report.violations == ()
    assert report.tables_skipped == ()
    assert set(report.tables_checked) == {t for t, *_ in _SCAN_TABLES}
    assert report.columns_checked == sum(len(cols) for _, _, cols, _ in _SCAN_TABLES)
    assert report.settings_checked == len(_SCAN_SETTINGS_KEYS)


def test_report_surfaces_a_skipped_table_distinctly_from_a_clean_one(lib, monkeypatch):
    """The exact case the summary-banner-bug-one-layer-down fix is for: a
    query that raises must show up as a named, explained skip — not
    silently vanish into a report that reads identically to a clean pass."""
    import linklib.voice_db_scan as scan_mod
    monkeypatch.setattr(
        scan_mod, "_SCAN_TABLES",
        (("nonexistent_table", "id", ("name",), ()),) + tuple(_SCAN_TABLES[1:]),
    )
    report = scan_db_copy_report(lib)
    assert report.violations == ()
    assert len(report.tables_skipped) == 1
    skipped_table, error = report.tables_skipped[0]
    assert skipped_table == "nonexistent_table"
    assert error  # a real error string, not blank
    # every OTHER configured table still got checked — one bad table
    # doesn't take the rest of the scan down with it.
    assert set(report.tables_checked) == {t for t, *_ in _SCAN_TABLES[1:]}


def test_scan_db_copy_thin_wrapper_matches_the_report(lib):
    """scan_db_copy() (the back-compat flat list every existing caller/test
    uses) must always equal report.violations — the two can never
    disagree about what was found, only about how much is reported."""
    assert scan_db_copy(lib) == list(scan_db_copy_report(lib).violations)


# --- Entity names are exempt from typography, not mechanical rules --------
# Found before shipping, not assumed: a real vendor/community/resource name
# can legitimately contain an ampersand ("Bain & Company") — the exact
# false-positive risk BRAND.md's own ampersand rule already documents for
# source scanning. Confirmed it reproduces for DB content too, then fixed
# via the per-table typography-exempt column set.

def test_tool_name_with_real_ampersand_is_not_flagged_for_typography(lib):
    lib.add_tool("Bain & Company", "A consulting firm.", "https://bain.example", [])
    violations = scan_db_copy(lib)
    assert not any(v.table == "tools" and v.column == "name" for v in violations)


def test_community_name_with_real_ampersand_is_not_flagged_for_typography(lib):
    lib.add_community("Founders & Friends", "https://community.example", "Finance leaders", "Free", [])
    violations = scan_db_copy(lib)
    assert not any(v.table == "communities" and v.column == "name" for v in violations)


def test_benchmark_name_with_real_ampersand_is_not_flagged_for_typography(lib):
    lib.add_benchmark("Ernst & Young Benchmarks", "https://example.com", "A resource.")
    violations = scan_db_copy(lib)
    assert not any(v.table == "benchmarks" and v.column == "name" for v in violations)


def test_tool_name_still_gets_mechanical_checks(lib):
    """The typography exemption for entity names doesn't extend to
    mechanical checks — a literal buzzword in a name field is still rare
    enough to be worth catching, not a real vendor-naming pattern."""
    lib.add_tool("Totally Robust Inc.", "A tool.", "https://robust.example", [])
    violations = scan_db_copy(lib)
    assert any(v.table == "tools" and v.column == "name" and v.rule == "buzzword" for v in violations)


def test_category_features_name_ampersand_is_no_longer_scanned(lib):
    """REVERSAL (2026-09, Brian's explicit decision, voice-review-queue PR
    follow-up): this test used to assert the opposite — that
    category_features.name stayed in typography scope because it's Brian's
    own curated vocabulary, not a third-party name. With 22 real production
    ampersand findings on this one column, Brian reviewed that reasoning
    and reversed it on purpose: he's choosing to own each category name's
    ampersand usage directly rather than route every one of them through
    the review queue to reach the same answer by hand. He explicitly
    accepts the trade — the rule stops running on this column entirely, so
    a genuinely-should-say-"and" name won't be flagged either. See the
    comment above `_SCAN_TABLES` in linklib/voice_db_scan.py for the full
    reversal write-up."""
    cat_id = lib.add_tool_category("Finance")
    lib.add_category_feature(cat_id, "Users & auth", "a definition")
    violations = scan_db_copy(lib)
    assert not any(v.table == "category_features" and v.column == "name"
                   and v.rule == "bare-ampersand" for v in violations)


# --- Invisible/zero-width Unicode characters (2026-09 follow-up) -----------
# The exact real-production shape this whole item exists for:
# category_features.definition (id 104 in production) ends with a zero-width
# space that nothing before this pass could flag — the write-time backstop
# (linklib.voice_mechanics.normalize_voice_mechanics) only ever fixes a
# spaced em dash, so a zero-width character survives storage unchanged; this
# is a read-time/scan-time finding, not something the backstop silently
# corrects before it ever reaches the scanner.
def test_category_features_definition_invisible_character_is_scanned(lib):
    cat_id = lib.add_tool_category("Finance")
    lib.add_category_feature(
        cat_id, "Anomaly detection",
        "Identifies unusual or erroneous items and patterns that don't look right"
        "​",
    )
    violations = scan_db_copy(lib)
    hits = [v for v in violations if v.table == "category_features" and v.column == "definition"]
    assert len(hits) == 1
    assert hits[0].rule == "invisible-character"
    assert "U+200B" in hits[0].excerpt


def test_invisible_character_is_scanned_on_a_newly_instrumented_table(lib):
    """Confirms the invisible-character rule reaches the five tables this
    PR instrumented too — added to `mechanical_findings` itself, so every
    caller of `_scan_value` (every `_SCAN_TABLES` entry, unconditionally)
    gets it automatically, with no per-table wiring needed."""
    lib.add_community(
        "Finance Leaders", "https://community.example", "VPs and directors​",
        "Free", [],
    )
    violations = scan_db_copy(lib)
    hits = [v for v in violations if v.table == "communities" and v.column == "demographic"]
    assert len(hits) == 1
    assert hits[0].rule == "invisible-character"


def test_clean_copy_across_all_scan_tables_has_no_invisible_character_findings(lib):
    """A normal, clean save through one of the newly-instrumented write
    paths introduces no false positive."""
    lib.add_benchmark("Clean Resource", "https://example.com", "A perfectly ordinary description.")
    violations = scan_db_copy(lib)
    assert not any(v.rule == "invisible-character" for v in violations)
