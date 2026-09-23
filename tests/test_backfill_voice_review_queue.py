"""scripts/backfill_voice_review_queue.py — the 2026-09 double-insert fix.

Reproduces the exact production incident: a scan re-run must not re-propose
a finding that's already sitting in the queue as an OPEN row, and the
preview/apply reporting must count "already queued" separately from
"already accepted" rather than conflating the two (or, as originally
shipped, only ever checking the latter).
"""
import os
import sys
import tempfile

import pytest

from linklib.db import Library

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import backfill_voice_review_queue as mod  # noqa: E402


@pytest.fixture
def lib():
    path = tempfile.mktemp(suffix=".db")
    lib = Library(path)
    yield lib
    lib.close()
    try:
        os.remove(path)
    except OSError:
        pass


def _seed_spaced_em_dash_finding(lib):
    """A real category_features.definition row carrying a spaced em dash —
    the confirmed production shape (see test_voice_review_queue.py's own
    `test_category_features_definition_is_now_scanned_and_matches_the_corrector`).
    Written directly to bypass `_voice_fix`, simulating pre-backstop content."""
    cid = lib.add_tool_category("Test Category")
    fid = lib.add_category_feature(cid, "Test Feature")
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE id=?",
        ("...patterns that don't look right — not necessarily a balance change", fid),
    )
    lib.conn.commit()
    return fid


# --- The exact bug: a re-scan against unchanged content must not double-insert ---

def test_skip_reason_is_queued_not_accepted_for_an_existing_open_row(lib):
    fid = _seed_spaced_em_dash_finding(lib)
    report = mod.scan_db_copy_report(lib)
    hits = [v for v in report.violations
            if v.table == "category_features" and v.row_id == fid and v.rule == "spaced-em-dash"]
    assert len(hits) == 1
    v = hits[0]

    # Nothing queued yet — this is a genuinely new finding.
    assert mod.skip_reason(lib, v) is None

    # Seed the queue with an open row for this exact finding, as the first
    # backfill run would have — a repeated scan (a new rule shipping, a
    # second run, whatever the trigger) reports this same finding again.
    lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    report2 = mod.scan_db_copy_report(lib)
    hits2 = [w for w in report2.violations
             if w.table == "category_features" and w.row_id == fid and w.rule == "spaced-em-dash"]
    assert len(hits2) == 1
    v2 = hits2[0]

    # This is the exact bug: it must be recognized as already-queued, NOT
    # reported as new (the original bug) and NOT conflated with the
    # separate "already accepted" exception bucket.
    assert mod.skip_reason(lib, v2) == "queued"


def test_apply_does_not_double_insert_an_already_queued_finding(lib, capsys):
    fid = _seed_spaced_em_dash_finding(lib)
    report = mod.scan_db_copy_report(lib)
    v = [x for x in report.violations
         if x.table == "category_features" and x.row_id == fid][0]
    lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    before_count = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    assert before_count == 1

    # Simulate the apply loop exactly as scripts.backfill_voice_review_queue.main()
    # runs it, against the SAME db this test already has an open connection to.
    report2 = mod.scan_db_copy_report(lib)
    inserted = skipped_queued = skipped_accepted = 0
    for finding in report2.violations:
        reason = mod.skip_reason(lib, finding)
        if reason == "queued":
            skipped_queued += 1
            continue
        if reason == "accepted":
            skipped_accepted += 1
            continue
        new_id = lib.add_voice_review_item(finding.table, finding.row_id, finding.column,
                                            finding.rule, finding.excerpt)
        if new_id:
            inserted += 1

    after_count = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    assert after_count == before_count == 1, "the already-queued finding was duplicated"
    assert inserted == 0
    assert skipped_queued == 1
    assert skipped_accepted == 0


def test_a_genuinely_new_finding_is_still_inserted_alongside_an_existing_queue(lib):
    """A repeated scan that turns up one already-queued finding plus one
    genuinely new one (the real production shape — 32 already-queued, 1
    new from a freshly-added rule) must still insert the new one and skip
    only the old one, not the reverse."""
    fid = _seed_spaced_em_dash_finding(lib)
    report = mod.scan_db_copy_report(lib)
    v = [x for x in report.violations
         if x.table == "category_features" and x.row_id == fid][0]
    lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)

    # A second, genuinely new violation at a different location.
    cid2 = lib.add_tool_category("Another Category")
    fid2 = lib.add_category_feature(cid2, "Another Feature")
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE id=?",
        ("a fresh finding — never seen before", fid2),
    )
    lib.conn.commit()

    report2 = mod.scan_db_copy_report(lib)
    inserted = skipped_queued = skipped_accepted = 0
    for finding in report2.violations:
        reason = mod.skip_reason(lib, finding)
        if reason == "queued":
            skipped_queued += 1
            continue
        if reason == "accepted":
            skipped_accepted += 1
            continue
        new_id = lib.add_voice_review_item(finding.table, finding.row_id, finding.column,
                                            finding.rule, finding.excerpt)
        if new_id:
            inserted += 1

    assert inserted == 1
    assert skipped_queued == 1


def test_skip_reason_distinguishes_accepted_exception_from_open_row(lib):
    """The two skip reasons are genuinely different states and must never be
    conflated — an accepted exception is a permanent, deliberate override;
    an open row is an ordinary pending review."""
    fid = _seed_spaced_em_dash_finding(lib)
    report = mod.scan_db_copy_report(lib)
    v = [x for x in report.violations
         if x.table == "category_features" and x.row_id == fid][0]

    item_id = lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    lib.resolve_voice_review_item(item_id, "accept_exception")

    # Checks-page follow-ups (2026-09): an Allow once finding is no longer
    # a violation; the scan reports it under allowed_once instead.
    report2 = mod.scan_db_copy_report(lib)
    assert not [x for x in report2.violations
                if x.table == "category_features" and x.row_id == fid]
    v2 = [x for x in report2.allowed_once
          if x.table == "category_features" and x.row_id == fid][0]
    assert mod.skip_reason(lib, v2) == "accepted"


def test_preview_reports_the_two_skip_reasons_separately(lib, capsys):
    fid = _seed_spaced_em_dash_finding(lib)
    report = mod.scan_db_copy_report(lib)
    v = [x for x in report.violations
         if x.table == "category_features" and x.row_id == fid][0]
    item_id = lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    lib.resolve_voice_review_item(item_id, "accept_exception")

    # Directly exercise main()'s CLI entry point against this same DB (a
    # second sqlite connection to the same file — safe for a sequential,
    # single-threaded test).
    argv = ["backfill_voice_review_queue.py", "--db", lib.path]
    old_argv = sys.argv
    sys.argv = argv
    try:
        mod.main()
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert "skipping 0 already queued, 1 already accepted" in out
