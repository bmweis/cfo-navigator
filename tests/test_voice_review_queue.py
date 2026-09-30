"""Voice review queue (2026-09) — the scanner/auto-corrector blocker fix,
the ampersand-exception additions, and the queue mechanism itself."""
import os
import tempfile

import pytest

from linklib.db import Library
from linklib.voice_review import AMPERSAND_ACRONYMS
from linklib.voice_db_scan import scan_db_copy, scan_db_copy_report


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


# --- Blocker: scanner now detects what _voice_fix corrects -------------------

def test_category_features_definition_is_now_scanned_and_matches_the_corrector():
    """The confirmed production case (category_features id 8's `definition`)
    — before this fix, the DB scanner never looked at `definition`/
    `pointer_note` at all, so it could never report what `_voice_fix`
    (already wired into add/update_category_feature) was silently
    correcting at write time. This proves the two now agree: the raw text
    `_voice_fix` would change is exactly what the scanner flags."""
    path = tempfile.mktemp(suffix=".db")
    lib = Library(path)
    try:
        cid = lib.add_tool_category("Test Category")
        fid = lib.add_category_feature(cid, "Test Feature")
        # add_category_feature already runs _voice_fix at write time, so the
        # STORED value is pre-corrected — insert the raw spaced-em-dash text
        # directly to simulate a row written before the backstop existed
        # (or any of the other still-uncovered write paths).
        lib.conn.execute(
            "UPDATE category_features SET definition=? WHERE id=?",
            ("...patterns that don't look right — not necessarily a balance change", fid),
        )
        lib.conn.commit()
        report = scan_db_copy_report(lib)
        hits = [v for v in report.violations
                if v.table == "category_features" and v.column == "definition" and v.row_id == fid]
        assert hits, "scanner did not catch a spaced-em-dash in category_features.definition"
        assert hits[0].rule == "spaced-em-dash"
    finally:
        lib.close()
        try:
            os.remove(path)
        except OSError:
            pass


def test_category_features_name_still_scanned_but_typography_exempt(lib):
    cid = lib.add_tool_category("Test Category 2")
    lib.add_category_feature(cid, "Sales & Marketing", "", "")
    violations = scan_db_copy(lib)
    # Mechanical rules still apply to name; typography (bare-ampersand) does
    # not, since "Sales & Marketing" is an already-allowlisted real term —
    # confirming no bare-ampersand finding fires for it.
    amp_hits = [v for v in violations if v.table == "category_features" and v.column == "name"
                and v.rule == "bare-ampersand"]
    assert not amp_hits


def test_thought_leadership_title_is_typography_exempt(lib):
    lib.add_thought_leadership("writing", "Cash Flow Show & Friends", "https://example.com",
                                "Venue", "Jun 2026", "2026-06", "", 0, None)
    violations = scan_db_copy(lib)
    amp_hits = [v for v in violations if v.table == "thought_leadership" and v.column == "title"
                and v.rule == "bare-ampersand"]
    assert not amp_hits


# --- Part 2a: ampersand allowlist additions -----------------------------------

def test_ga_and_ld_are_allowlisted_acronyms():
    assert "G&A" in AMPERSAND_ACRONYMS
    assert "L&D" in AMPERSAND_ACRONYMS


# --- Part 1: the review queue mechanism ---------------------------------------

def test_vf_logs_an_auto_corrected_row_when_text_changes(lib):
    fixed = lib._vf("tools", 42, "description", "a — b")
    assert fixed == "a—b"
    items = lib.list_voice_review_queue()
    assert len(items) == 1
    assert items[0]["status"] == "auto_corrected"
    assert items[0]["table_name"] == "tools"
    assert items[0]["row_id"] == "42"
    assert items[0]["before_text"] == "a — b"
    assert items[0]["after_text"] == "a—b"


def test_vf_logs_nothing_when_text_is_already_clean(lib):
    fixed = lib._vf("tools", 42, "description", "a—b, already fine")
    assert fixed == "a—b, already fine"
    assert lib.list_voice_review_queue() == []


def test_set_setting_logs_a_correction(lib):
    lib.set_setting("homepage_headline_copy", "Strategic partner — not just a scorekeeper")
    items = lib.list_voice_review_queue()
    assert len(items) == 1
    assert items[0]["table_name"] == "settings"
    assert items[0]["row_id"] is None
    assert items[0]["column_name"] == "homepage_headline_copy"


def test_update_tool_logs_a_correction(lib):
    tid = lib.add_tool("Test Tool", "A tool.", "https://example.com", [], approved=1)
    lib.update_tool(tid, "Test Tool", "A tool that does X — and Y too", "https://example.com", [])
    items = lib.list_voice_review_queue()
    assert any(i["table_name"] == "tools" and i["column_name"] == "description" for i in items)


def test_add_tool_logs_a_correction(lib):
    """No known row id exists until the INSERT returns one, same as
    add_category_feature/add_original_content — logged AFTER insert, using
    the real id. This was a real pre-existing gap: add_tool called bare
    _voice_fix() with no queue logging, never caught until the guard was
    tightened to require self._vf()/log_voice_correction() specifically."""
    tid = lib.add_tool(
        "Test Tool", "A tool that does X — and Y too", "https://example.com", [],
        summary="Short and snappy — a bit too snappy",
    )
    items = lib.list_voice_review_queue()
    hits = {i["column_name"]: i for i in items if i["table_name"] == "tools" and i["row_id"] == str(tid)}
    assert "description" in hits and hits["description"]["after_text"] == "A tool that does X—and Y too"
    assert "summary" in hits and hits["summary"]["after_text"] == "Short and snappy—a bit too snappy"


# --- tool_categories/community_categories: instrumented in this same PR's
# follow-up round (coordinator review) — the CI guard used to accept a bare
# _voice_fix() call as sufficient, which is exactly what all four of these
# methods had, with no queue logging at all. -----------------------------

def test_add_tool_category_logs_a_correction(lib):
    cid = lib.add_tool_category("Anomaly Detection", "Flags patterns — the odd ones")
    items = lib.list_voice_review_queue()
    hits = [i for i in items if i["table_name"] == "tool_categories"
            and i["row_id"] == str(cid) and i["column_name"] == "description"]
    assert hits and hits[0]["after_text"] == "Flags patterns—the odd ones"


def test_rename_tool_category_logs_a_correction(lib):
    cid = lib.add_tool_category("Anomaly Detection")
    lib.rename_tool_category(cid, "Anomaly Detection", "Flags patterns — the odd ones")
    items = lib.list_voice_review_queue()
    hits = [i for i in items if i["table_name"] == "tool_categories"
            and i["row_id"] == str(cid) and i["column_name"] == "description"]
    assert hits and hits[0]["after_text"] == "Flags patterns—the odd ones"


def test_add_community_category_logs_a_correction(lib):
    cid = lib.add_community_category("Peer Groups", "Small cohorts — usually 8-10 people")
    items = lib.list_voice_review_queue()
    hits = [i for i in items if i["table_name"] == "community_categories"
            and i["row_id"] == str(cid) and i["column_name"] == "description"]
    assert hits and hits[0]["after_text"] == "Small cohorts—usually 8-10 people"


def test_rename_community_category_logs_a_correction(lib):
    cid = lib.add_community_category("Peer Groups")
    lib.rename_community_category(cid, "Peer Groups", "Small cohorts — usually 8-10 people")
    items = lib.list_voice_review_queue()
    hits = [i for i in items if i["table_name"] == "community_categories"
            and i["row_id"] == str(cid) and i["column_name"] == "description"]
    assert hits and hits[0]["after_text"] == "Small cohorts—usually 8-10 people"


# --- original_content: instrumented in this PR's own follow-up round,
# since it holds Brian's own published thought leadership --------------------

def test_add_original_content_logs_a_correction(lib):
    """No known row id exists until the INSERT returns one, same as
    add_category_feature — logged AFTER insert, using the real id."""
    item_id = lib.add_original_content(
        "test-slug", "A Piece — With A Correction", "A teaser — also with one",
    )
    items = lib.list_voice_review_queue()
    hits = {i["column_name"]: i for i in items if i["table_name"] == "original_content"
            and i["row_id"] == str(item_id)}
    assert "title" in hits and hits["title"]["after_text"] == "A Piece—With A Correction"
    assert "teaser" in hits and hits["teaser"]["after_text"] == "A teaser—also with one"


def test_update_original_content_logs_a_correction(lib):
    item_id = lib.add_original_content("test-slug-2", "Clean Title", "Clean teaser")
    lib.update_original_content(
        item_id, "test-slug-2", "Clean Title", "Clean teaser", "", "", None,
        "draft", False, "", "", 0,
    )
    # No correction yet — resave with clean text logs nothing new.
    assert lib.list_voice_review_queue() == []
    lib.update_original_content(
        item_id, "test-slug-2", "Clean Title", "Clean teaser", "", "",
        "Some body text — with a spaced dash.", "draft", False, "", "", 0,
    )
    items = lib.list_voice_review_queue()
    hits = [i for i in items if i["table_name"] == "original_content"
            and i["row_id"] == str(item_id) and i["column_name"] == "body_md"]
    assert hits and hits[0]["after_text"] == "Some body text—with a spaced dash."


def test_add_voice_review_item_and_exception_flow(lib):
    item_id = lib.add_voice_review_item("communities", 5, "local_markets", "bare-ampersand", "Bain & Company")
    assert item_id
    assert lib.count_open_voice_review_items() == 1
    # Accept as exception marks it permanently — future adds for the exact
    # same (table, row_id, column, rule) are skipped.
    assert lib.resolve_voice_review_item(item_id, "accept_exception")
    assert lib.is_voice_exception("communities", 5, "local_markets", "bare-ampersand")
    second = lib.add_voice_review_item("communities", 5, "local_markets", "bare-ampersand", "Bain & Company")
    assert second == 0
    assert lib.count_open_voice_review_items() == 0


def test_accept_exception_writes_an_allowed_once_resolution_note(lib):
    """PR #595 follow-up: "Allow once" (accept_exception) used to leave
    resolution_note blank — the note now says plainly what happened, in
    the current button's own words, not the old "Allow here" wording."""
    item_id = lib.add_voice_review_item("communities", 5, "local_markets", "bare-ampersand", "Bain & Company")
    lib.resolve_voice_review_item(item_id, "accept_exception")
    item = lib.get_voice_review_item(item_id)
    assert item["resolution_note"] == "Allowed once."


def test_exception_is_row_scoped_not_global(lib):
    """Accepting an exception for one record must not suppress the identical
    finding on a DIFFERENT record — this is a per-record exception, never a
    global rule change (that's what the source-side AMPERSAND_NAMES/
    AMPERSAND_ACRONYMS allowlists are for)."""
    item_id = lib.add_voice_review_item("communities", 5, "local_markets", "bare-ampersand", "Bain & Company")
    lib.resolve_voice_review_item(item_id, "accept_exception")
    other = lib.add_voice_review_item("communities", 6, "local_markets", "bare-ampersand", "Ernst & Young")
    assert other != 0
    assert lib.count_open_voice_review_items() == 1


def test_resolve_revert_and_edit_write_back_via_apply(lib):
    tid = lib.add_tool("Test Tool 2", "A tool.", "https://example.com", [], approved=1)
    lib.update_tool_content(tid, "Test Tool 2", "A tool that does X — and Y too")
    item = lib.list_voice_review_queue()[0]
    assert lib.apply_voice_review_write("tools", item["row_id"], "description", item["before_text"])
    tool = lib.get_tool(int(item["row_id"]))
    assert tool["description"] == item["before_text"]


def test_apply_voice_review_write_rejects_unknown_table_column(lib):
    assert not lib.apply_voice_review_write("not_a_real_table", 1, "x", "y")
    assert not lib.apply_voice_review_write("tools", 1, "not_a_real_column", "y")


def test_backfill_script_queues_open_items_from_the_scan(lib):
    from scripts.backfill_voice_review_queue import main
    assert callable(main), "backfill script must at least be importable"
    cid = lib.add_tool_category("Test Category 3")
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE category_id=? AND id=(SELECT id FROM category_features WHERE category_id=?)",
        ("bad — dash", cid, cid),
    )
    fid = lib.add_category_feature(cid, "Feature X", "", "")
    lib.conn.execute("UPDATE category_features SET definition=? WHERE id=?", ("bad — dash", fid))
    lib.conn.commit()
    report = scan_db_copy_report(lib)
    before = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    for v in report.violations:
        lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    after = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    assert after > before


# --- Part 4 (URGENT) — edit-safety: full column value, never the excerpt ----

def _reproduce_pre_fix_prefill(lib, item):
    """Reproduces the exact pre-fix bug: pre-filling the edit textarea from
    the row's own `excerpt` (a mid-text SNIPPET, capped at 200 chars — see
    `log_voice_correction`'s `after[:200]` truncation) instead of the full,
    current column value. Used only to prove the regression test below
    would have failed against the pre-fix code."""
    return item["excerpt"]


def test_review_queue_edit_prefill_uses_full_value_not_truncated_excerpt(lib):
    """The URGENT Part 4 data-corruption bug: a review-queue row's `excerpt`
    is a mid-text SNIPPET (capped at 200 chars), never the full column
    value. Pre-filling the edit textarea with the excerpt and then saving
    it back unchanged would TRUNCATE real, live, published copy down to a
    200-char fragment. This seeds a mid-text-snippet excerpt scenario
    directly: a description whose flagged excerpt is short but whose real
    stored value is much longer, then submits the "full value, unchanged"
    round trip through the real submit path and asserts the full column
    value survives byte-identical."""
    # Uses a bare-ampersand (an OPEN finding, not an auto-corrected em-dash
    # write) so "edit" is a valid action for this row's status — the
    # durability guard (Part A item 1/2) restricts "edit" to `status='open'`
    # rows, and `update_tool_content` with a spaced em dash would instead
    # auto-correct at write time and land the row in `auto_corrected`, where
    # "edit" is no longer a valid action at all.
    tid = lib.add_tool("Test Tool Long", "placeholder", "https://example-long.com", [], approved=1)
    long_value = ("This is a long description. " * 20) + "A tool that does X & Y too."
    lib.update_tool_content(tid, "Test Tool Long", long_value)
    item_id = lib.add_voice_review_item("tools", tid, "description", "bare-ampersand", "X & Y too")
    item = lib.get_voice_review_item(item_id)
    assert item["status"] == "open"
    assert item["table_name"] == "tools" and item["column_name"] == "description"
    # The excerpt is genuinely a short snippet, not the full stored value —
    # confirms the seeded scenario actually exercises the truncation risk.
    assert len(item["excerpt"]) < len(long_value)

    # Fetch the FULL current value the way the fixed edit-form prefill does
    # (Library.get_voice_review_current_value) — this is what the textarea
    # is now pre-filled with, per the Part 4 fix.
    current = lib.get_voice_review_current_value(item["table_name"], item["row_id"], item["column_name"])
    assert current is not None
    normalized_long_value = long_value
    assert current == normalized_long_value

    # Submit the FULL value back unchanged, through the real submit path
    # (_resolve_voice_item_action's "edit" branch), exactly as the fixed
    # edit-form's textarea would.
    from webapp.app import _resolve_voice_item_action
    ok = _resolve_voice_item_action(lib, item, "edit", current)
    assert ok

    tool = lib.get_tool(tid)
    assert tool["description"] == normalized_long_value, (
        "full column value was not preserved byte-identical after a "
        "no-op edit resolve — this is the exact Part 4 data-corruption bug"
    )

    # Confirm this test WOULD have failed against the pre-fix behavior:
    # pre-filling from the row's own excerpt (a truncated snippet) and
    # saving that back would NOT match the original full value.
    pre_fix_prefill = _reproduce_pre_fix_prefill(lib, item)
    assert pre_fix_prefill != normalized_long_value, (
        "sanity check failed: the pre-fix excerpt-based prefill must differ "
        "from the full value for this regression test to be meaningful"
    )


# --- Part 1: the 5 named regression tests --------------------------------

def test_seed_divergence_no_overwrite(lib):
    """Part 1 — a live stored value that diverges from a seed source no
    longer gets silently overwritten; it's queued as a 'seed-disagreement'
    review item instead, via `add_seed_disagreement_item`, and the live
    value is left untouched until an admin explicitly resolves it."""
    cid = lib.add_community("Test Community", "https://example-comm.com", "", "", [], local_markets="Original notes")
    community = lib.get_community(cid)
    assert community["local_markets"] == "Original notes"

    seed_value = "Seed-proposed notes text"
    new_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", community["local_markets"], seed_value)
    assert new_id

    # The live value must be completely untouched — no overwrite happened.
    community_after = lib.get_community(cid)
    assert community_after["local_markets"] == "Original notes"

    item = lib.get_voice_review_item(new_id)
    assert item["rule"] == "seed-disagreement"
    assert item["status"] == "open"
    assert item["before_text"] == "Original notes"
    assert item["after_text"] == seed_value


def test_dedupe_across_repeated_boots(lib):
    """Part 1 — the exact seed-sync infinite-loop bug this mechanism
    replaces: calling `add_seed_disagreement_item` again for the identical
    (table, row_id, column) — simulating a second app boot / re-sync pass
    against the same divergence — must NOT queue a duplicate open row."""
    cid = lib.add_community("Test Community 2", "https://example-comm2.com", "", "", [], local_markets="Stored A")
    first_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", "Stored A", "Seed B")
    assert first_id
    assert lib.count_open_voice_review_items() == 1

    # A second "boot" proposing the SAME divergence must be a no-op.
    second_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", "Stored A", "Seed B")
    assert second_id == 0
    assert lib.count_open_voice_review_items() == 1

    # Even a THIRD boot, still the same divergence, stays deduped.
    third_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", "Stored A", "Seed B")
    assert third_id == 0
    assert lib.count_open_voice_review_items() == 1


def test_use_seed_applies_and_resolves(lib):
    """Part 1 — the 'use_seed' resolution action applies the seed's own
    proposed text back to the live row and resolves the queue item."""
    cid = lib.add_community("Test Community 3", "https://example-comm3.com", "", "", [], local_markets="Stored value")
    seed_value = "Seed's proposed value"
    item_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", "Stored value", seed_value)

    item = lib.get_voice_review_item(item_id)
    from webapp.app import _resolve_voice_item_action
    ok = _resolve_voice_item_action(lib, item, "use_seed")
    assert ok

    community = lib.get_community(cid)
    assert community["local_markets"] == seed_value

    resolved_item = lib.get_voice_review_item(item_id)
    assert resolved_item["status"] == "resolved"


def test_keep_mine_preserves_and_prevents_reopen(lib):
    """Part 1 — the 'keep_mine' resolution action leaves the currently
    stored value untouched (writes NOTHING back) and marks this exact
    location a permanent exception, so the identical divergence can never
    reopen on a future sync/boot."""
    cid = lib.add_community("Test Community 4", "https://example-comm4.com", "", "", [], local_markets="My own notes")
    item_id = lib.add_seed_disagreement_item("communities", cid, "local_markets", "My own notes", "Seed's version")

    item = lib.get_voice_review_item(item_id)
    from webapp.app import _resolve_voice_item_action
    ok = _resolve_voice_item_action(lib, item, "keep_mine")
    assert ok

    # The stored value must be completely untouched.
    community = lib.get_community(cid)
    assert community["local_markets"] == "My own notes"

    resolved_item = lib.get_voice_review_item(item_id)
    assert resolved_item["status"] == "exception"
    assert lib.is_voice_exception("communities", cid, "local_markets", "seed-disagreement")

    # A future "boot" proposing the same divergence must never reopen it.
    reopened = lib.add_seed_disagreement_item("communities", cid, "local_markets", "My own notes", "Seed's version")
    assert reopened == 0
    assert lib.count_open_voice_review_items() == 0


def test_tools_name_sync_via_library_method(lib):
    """Part 1 — `tools.name`'s seed-sync divergence is now routed through
    `Library.add_seed_disagreement_item` (a real Library method, logged to
    voice_review_queue with source='startup-sync'), never a raw, unlogged
    `UPDATE tools SET name=?` — mirroring the exact fix already applied to
    communities.name/notes and benchmarks.name/description."""
    tid = lib.add_tool("Old Name", "A tool.", "https://example-tool.com", [], approved=1)
    tool = lib.get_tool(tid)
    assert tool["name"] == "Old Name"

    seed_name = "New Seed Name"
    new_id = lib.add_seed_disagreement_item("tools", tid, "name", tool["name"], seed_name, source="startup-sync")
    assert new_id

    # The live name must be untouched — no raw SQL overwrite happened.
    tool_after = lib.get_tool(tid)
    assert tool_after["name"] == "Old Name"

    item = lib.get_voice_review_item(new_id)
    assert item["table_name"] == "tools"
    assert item["column_name"] == "name"
    assert item["rule"] == "seed-disagreement"
    assert item["source"] == "startup-sync"
    assert item["before_text"] == "Old Name"
    assert item["after_text"] == seed_name


# --- Addition 1: the 3 bidirectional-sync regression tests -----------------

def test_reconciliation_tags_new_rows_as_scan_not_script(lib):
    """Issue #592 item 3 — reconcile_voice_review_queue() must tag the rows
    it inserts as source='scan', not 'script'. 'script' is reserved for a
    one-off, human-run backfill/fix script (e.g.
    scripts/backfill_voice_review_queue.py, which shares this exact
    insertion path via add_voice_review_item's own 'script' default); this
    is a periodic background pass, a genuinely different mechanism, and the
    two were indistinguishable under the old shared label."""
    lib.add_community("Amp Scan Label Co", "https://amp-scan-label.com",
                       "", "", [], local_markets="Widgets & Gadgets clients")
    result = lib.reconcile_voice_review_queue()
    assert result["added"] >= 1
    open_items = lib.list_voice_review_queue(status="open")
    hits = [i for i in open_items
            if i["table_name"] == "communities" and i["column_name"] == "local_markets"]
    assert hits
    assert hits[0]["source"] == "scan"
    assert hits[0]["source"] != "script"


def test_backfill_script_still_tags_rows_as_script(lib):
    """The sibling half of the fix above: the actual one-off backfill
    script's own default ('script') must be untouched by the 'scan' fix —
    add_voice_review_item's default parameter still applies whenever a
    caller doesn't explicitly pass source, exactly like
    scripts/backfill_voice_review_queue.py's own call."""
    item_id = lib.add_voice_review_item(
        "communities", 999, "local_markets", "bare-ampersand", "Widgets & Gadgets",
    )
    assert item_id
    item = lib.get_voice_review_item(item_id)
    assert item["source"] == "script"


def test_finding_appears_without_backfill(lib):
    """Addition 1 — a bare-ampersand/banned-word-style violation entering
    the DB via an ordinary write no longer needs a manual backfill-script
    run to reach the queue; `reconcile_voice_review_queue()` (the periodic
    background sync) picks it up on its own."""
    lib.add_community("Amp Test Co", "https://amp-test.com", "", "", [], local_markets="Widgets & Gadgets clients")
    result = lib.reconcile_voice_review_queue()
    assert result["added"] >= 1
    open_items = lib.list_voice_review_queue(status="open")
    hits = [i for i in open_items if i["table_name"] == "communities" and i["column_name"] == "local_markets"]
    assert hits, "reconcile_voice_review_queue did not add a finding for a live ampersand violation with no backfill run"


def test_edit_page_fix_auto_closes_queue_row(lib):
    """Addition 1 — fixing a violation directly on the record's own admin
    edit page (bypassing the queue's own resolve actions entirely) makes it
    disappear from the live scan, and the next reconciliation pass closes
    the now-stale open queue row instead of leaving it open forever."""
    cid = lib.add_community("Amp Test Co 2", "https://amp-test2.com", "", "", [], local_markets="Widgets & Gadgets clients")
    lib.reconcile_voice_review_queue()
    open_before = [i for i in lib.list_voice_review_queue(status="open")
                   if i["table_name"] == "communities" and i["row_id"] == str(cid)]
    assert open_before, "expected an open row to exist before the direct edit"

    # Fix it directly on the record's own admin edit page — NOT via the
    # queue's own resolve route. update_community (the real admin edit-form
    # submit path) writes demographic; update_community_content deliberately
    # never touches demographic at all (admin-owned, see its own docstring).
    lib.update_community(cid, "Amp Test Co 2", "https://amp-test2.com",
                          None, "", [], local_markets="Widgets and Gadgets clients")

    result = lib.reconcile_voice_review_queue()
    assert result["closed"] >= 1

    stale_item = lib.get_voice_review_item(open_before[0]["id"])
    assert stale_item["status"] == "resolved"
    assert "outside the queue" in (stale_item.get("resolution_note") or "")


def test_scan_count_equals_queue_open_count(lib):
    """Addition 1 — after reconciliation, the live DB scan's count of
    reconcilable violations and the queue's own open-row count for those
    same scannable rules must agree (the exact /admin/checks-vs-review-queue
    disagreement this whole mechanism was built to close)."""
    from linklib.voice_db_scan import scan_db_copy

    lib.add_community("Amp Test Co 3", "https://amp-test3.com", "", "", [], local_markets="Widgets & Gadgets only")
    lib.add_community("Amp Test Co 4", "https://amp-test4.com", "", "", [], local_markets="Doodads & Sprockets only")
    lib.reconcile_voice_review_queue()

    live = scan_db_copy(lib)
    scanned_rules = {"buzzword", "filler", "performative", "invisible-character",
                      "bare-ampersand", "spaced-em-dash"}
    live_count = len([v for v in live if v.rule in scanned_rules])

    open_items = [i for i in lib.list_voice_review_queue(status="open") if i["rule"] in scanned_rules]
    assert len(open_items) == live_count


# --- Issue #592 item 4: bulk "Replace & with and" ---------------------------

def test_replace_spaced_ampersands_unit():
    """Direct unit coverage for linklib.voice_review.replace_spaced_ampersands
    — the pure function underneath the bulk action, independent of the
    review queue."""
    from linklib.voice_review import replace_spaced_ampersands

    # A plain spaced ampersand is replaced.
    text, changed = replace_spaced_ampersands("Finance & Operations leaders", [])
    assert changed is True
    assert text == "Finance and Operations leaders"

    # An unspaced ampersand is left alone entirely — never even flagged as
    # a candidate, per the "S&M" -> "SandM" caution in the spec.
    text, changed = replace_spaced_ampersands("A firm doing S&M consulting", [])
    assert changed is False
    assert text == "A firm doing S&M consulting"

    # An HTML-escaped, spaced ampersand becomes "and", not a stray "amp;".
    text, changed = replace_spaced_ampersands("Widgets &amp; Gadgets only", [])
    assert changed is True
    assert text == "Widgets and Gadgets only"
    assert "amp;" not in text

    # An approved term's own ampersand is protected; a different, unapproved
    # ampersand elsewhere in the same text is still replaced.
    text, changed = replace_spaced_ampersands(
        "Bain & Company advises on finance & operations for clients.",
        ["Bain & Company"],
    )
    assert changed is True
    assert text == "Bain & Company advises on finance and operations for clients."


def test_preview_ampersand_replacement_shows_before_after_without_writing(lib):
    cid = lib.add_community("Amp Preview Co", "https://amp-preview.com",
                             "", "", [], local_markets="Finance & Operations leaders")
    item_id = lib.add_voice_review_item(
        "communities", cid, "local_markets", "bare-ampersand", "Finance & Operations leaders",
    )
    preview = lib.preview_ampersand_replacement(item_id)
    assert preview is not None
    assert preview["changed"] is True
    assert preview["before"] == "Finance & Operations leaders"
    assert preview["after"] == "Finance and Operations leaders"

    # Nothing was written by preview alone.
    row = lib.get_community(cid)
    assert row["local_markets"] == "Finance & Operations leaders"
    item = lib.get_voice_review_item(item_id)
    assert item["status"] == "open"


def test_apply_ampersand_replacement_writes_and_resolves(lib):
    cid = lib.add_community("Amp Apply Co", "https://amp-apply.com",
                             "", "", [], local_markets="Finance & Operations leaders")
    item_id = lib.add_voice_review_item(
        "communities", cid, "local_markets", "bare-ampersand", "Finance & Operations leaders",
    )
    ok = lib.apply_ampersand_replacement(item_id)
    assert ok is True

    row = lib.get_community(cid)
    assert row["local_markets"] == "Finance and Operations leaders"

    item = lib.get_voice_review_item(item_id)
    assert item["status"] == "resolved"
    assert item["resolution_note"]
    assert "Replace" in item["resolution_note"]


def test_apply_ampersand_replacement_leaves_unspaced_field_untouched_and_open(lib):
    cid = lib.add_community("Amp Unspaced Co", "https://amp-unspaced.com",
                             "", "", [], local_markets="A firm doing S&M consulting")
    item_id = lib.add_voice_review_item(
        "communities", cid, "local_markets", "bare-ampersand", "A firm doing S&M consulting",
    )
    preview = lib.preview_ampersand_replacement(item_id)
    assert preview["changed"] is False

    ok = lib.apply_ampersand_replacement(item_id)
    assert ok is False

    row = lib.get_community(cid)
    assert row["local_markets"] == "A firm doing S&M consulting", "unspaced text must never be written to"

    item = lib.get_voice_review_item(item_id)
    assert item["status"] == "open", "a no-op selection must leave the row open for a manual decision"


def test_apply_ampersand_replacement_protects_approved_term_in_same_field(lib):
    """The exact scenario named in the spec: a field mixing an approved
    term with an ordinary prose ampersand — only the unapproved one moves."""
    lib.approve_voice_term("Bain & Company", rule="bare-ampersand")
    cid = lib.add_community(
        "Amp Mixed Co", "https://amp-mixed.com",
        "", "", [],
    local_markets="Bain & Company advises on finance & operations for clients.")
    item_id = lib.add_voice_review_item(
        "communities", cid, "local_markets", "bare-ampersand",
        "Bain & Company advises on finance & operations for clients.",
    )
    ok = lib.apply_ampersand_replacement(item_id)
    assert ok is True

    row = lib.get_community(cid)
    assert row["local_markets"] == "Bain & Company advises on finance and operations for clients."


def test_apply_ampersand_replacement_handles_escaped_amp_in_body_md(lib):
    """body_md (original_content) can carry HTML-escaped ampersands —
    confirms the write-back produces real "and", not a stray "amp;"."""
    oc_id = lib.add_original_content(
        slug="amp-test-piece", title="Amp Test Piece", teaser="A teaser.",
        tag_label="Guide", link_label="Read the guide",
        body_md="This covers widgets &amp; gadgets in depth.",
    )
    item_id = lib.add_voice_review_item(
        "original_content", oc_id, "body_md", "bare-ampersand",
        "This covers widgets &amp; gadgets in depth.",
    )
    ok = lib.apply_ampersand_replacement(item_id)
    assert ok is True

    row = lib.get_original_content(oc_id)
    assert row["body_md"] == "This covers widgets and gadgets in depth."
    assert "amp;" not in row["body_md"]


def test_apply_ampersand_replacement_returns_false_for_unknown_item(lib):
    assert lib.apply_ampersand_replacement(999999) is False
    assert lib.preview_ampersand_replacement(999999) is None


# --- Part A item 1/2 — the "reopen loop" durability guard -------------------
#
# Before this fix, `resolve_voice_review_item` only checked that `action` was
# one of the six known action strings — it never checked the item's own
# `status`, so running e.g. "accept" (an `auto_corrected`-only action) against
# a still-`open` row silently wrote nothing back and returned True anyway.
# That's the exact "reopen loop": the queue read as resolved for one 120s
# reconciler pass, then `reconcile_voice_review_queue()` found the identical
# live violation still there and re-queued it as a brand-new row. Every test
# below is proven to fail against the pre-fix code (no `status` check at all,
# every action accepted unconditionally for any item) before being trusted.

def test_accept_and_revert_are_rejected_against_an_open_row(lib):
    """`accept`/`revert` only make sense for an `auto_corrected` row (undo or
    keep an already-applied automatic correction) — running either against a
    still-`open` finding must be a no-op, not a silent false-positive
    resolve."""
    tid = lib.add_tool("Durability Tool", "A tool.", "https://durability.example.com", [], approved=1)
    item_id = lib.add_voice_review_item("tools", tid, "description", "bare-ampersand", "Acme & Co", source="script")
    item = lib.get_voice_review_item(item_id)
    assert item["status"] == "open"

    assert lib.resolve_voice_review_item(item_id, "accept") is False
    assert lib.get_voice_review_item(item_id)["status"] == "open"

    assert lib.resolve_voice_review_item(item_id, "revert") is False
    assert lib.get_voice_review_item(item_id)["status"] == "open"


def test_accept_exception_and_edit_are_rejected_against_an_auto_corrected_row(lib):
    """`accept_exception`/`edit` only make sense for a still-`open` finding —
    running either against an already-`auto_corrected` row (whose text was
    already fixed at save time) must be rejected, not silently accepted."""
    tid = lib.add_tool("Durability Tool 2", "A tool.", "https://durability2.example.com", [], approved=1)
    lib.update_tool_content(tid, "Durability Tool 2", "A tool that does X — and Y too")
    item = lib.list_voice_review_queue(status="auto_corrected")[0]
    assert item["status"] == "auto_corrected"

    assert lib.resolve_voice_review_item(item["id"], "accept_exception") is False
    assert lib.get_voice_review_item(item["id"])["status"] == "auto_corrected"

    assert lib.resolve_voice_review_item(item["id"], "edit") is False
    assert lib.get_voice_review_item(item["id"])["status"] == "auto_corrected"


def test_accept_exception_and_edit_succeed_against_an_open_row(lib):
    """The matching-status actions still work — the guard only rejects a
    mismatch, it doesn't break the legitimate path."""
    tid = lib.add_tool("Durability Tool 3", "A tool.", "https://durability3.example.com", [], approved=1)
    item_id = lib.add_voice_review_item("tools", tid, "description", "bare-ampersand", "Acme & Co", source="script")
    assert lib.resolve_voice_review_item(item_id, "accept_exception") is True
    assert lib.get_voice_review_item(item_id)["status"] == "exception"


def test_accept_and_revert_succeed_against_an_auto_corrected_row(lib):
    tid = lib.add_tool("Durability Tool 4", "A tool.", "https://durability4.example.com", [], approved=1)
    lib.update_tool_content(tid, "Durability Tool 4", "A tool that does X — and Y too")
    item = lib.list_voice_review_queue(status="auto_corrected")[0]
    assert lib.resolve_voice_review_item(item["id"], "accept") is True
    assert lib.get_voice_review_item(item["id"])["status"] == "resolved"


def test_use_seed_and_keep_mine_are_rejected_against_a_non_seed_disagreement_item(lib):
    """`use_seed`/`keep_mine` only ever make sense for `rule='seed-disagreement'`
    rows — running either against an ordinary bare-ampersand/spaced-em-dash
    finding must be rejected."""
    tid = lib.add_tool("Durability Tool 5", "A tool.", "https://durability5.example.com", [], approved=1)
    item_id = lib.add_voice_review_item("tools", tid, "description", "bare-ampersand", "Acme & Co", source="script")
    assert lib.resolve_voice_review_item(item_id, "use_seed") is False
    assert lib.resolve_voice_review_item(item_id, "keep_mine") is False
    assert lib.get_voice_review_item(item_id)["status"] == "open"


def test_use_seed_and_keep_mine_succeed_against_a_seed_disagreement_item(lib):
    tid = lib.add_tool("Durability Tool 6", "A tool.", "https://durability6.example.com", [], approved=1)
    item_id = lib.add_seed_disagreement_item("tools", tid, "name", "Live Name", "Seed Name")
    assert lib.resolve_voice_review_item(item_id, "use_seed") is True
    assert lib.get_voice_review_item(item_id)["status"] == "resolved"

    item_id2 = lib.add_seed_disagreement_item("tools", tid, "name", "Live Name 2", "Seed Name 2")
    assert lib.resolve_voice_review_item(item_id2, "keep_mine") is True
    assert lib.get_voice_review_item(item_id2)["status"] == "exception"


# --- Part A item 4 — Allow-everywhere prefill/validation/verification -------

def test_guess_ampersand_terms_extracts_a_real_term_not_the_raw_excerpt():
    """The production bad-write shape this closes: prefilling from the raw
    excerpt let an untrimmed multi-sentence blob be submitted as a "term".
    `guess_ampersand_terms` must instead extract just the capitalized phrase
    immediately around the ampersand."""
    from linklib.voice_review import guess_ampersand_terms
    text = "NetSuite, and Intacct[1] BILL Spend & Expense pairs budgeting software with a"
    guesses = guess_ampersand_terms(text)
    assert any("Spend & Expense" in g for g in guesses)
    # The guess must never be the entire untrimmed field text.
    assert text not in guesses


def test_guess_ampersand_terms_handles_multiple_ampersands_as_separate_candidates():
    from linklib.voice_review import guess_ampersand_terms
    text = "Bain & Company, Dun & Bradstreet, and the G&A team"
    guesses = guess_ampersand_terms(text)
    assert "Bain & Company" in guesses
    assert "Dun & Bradstreet" in guesses
    # An unspaced, unanchored ampersand with no adjacent capitalized words on
    # either side (G&A, lowercase "team" after) must never surface as a
    # bare "&" guess with nothing around it.
    assert "&" not in guesses


def test_guess_ampersand_terms_masks_already_approved_terms(lib):
    from linklib.voice_review import guess_ampersand_terms
    lib.approve_voice_term("Bain & Company")
    text = "Bain & Company and Dun & Bradstreet are both cited here"
    approved = [row["term"] for row in lib.list_approved_voice_terms()]
    guesses = guess_ampersand_terms(text, approved_terms=approved)
    assert "Bain & Company" not in guesses
    assert "Dun & Bradstreet" in guesses


def test_validate_ampersand_term_rejects_untrimmed_excerpt():
    from linklib.voice_review import validate_ampersand_term
    text = "NetSuite, and Intacct[1] BILL Spend & Expense pairs budgeting software with a"
    ok, err = validate_ampersand_term(text, text)
    assert ok is False
    assert err


def test_validate_ampersand_term_rejects_missing_ampersand():
    from linklib.voice_review import validate_ampersand_term
    ok, err = validate_ampersand_term("Just some words", "Just some words appear here")
    assert ok is False


def test_validate_ampersand_term_rejects_term_not_verbatim_in_field():
    from linklib.voice_review import validate_ampersand_term
    ok, err = validate_ampersand_term("Foo & Bar", "This field mentions nothing like that")
    assert ok is False


def test_validate_ampersand_term_rejects_partial_word_boundary():
    from linklib.voice_review import validate_ampersand_term
    # "ain & Company" is a substring of "Bain & Company" but starts mid-word.
    ok, err = validate_ampersand_term("ain & Company", "We work with Bain & Company often")
    assert ok is False


def test_validate_ampersand_term_accepts_a_clean_trimmed_term():
    from linklib.voice_review import validate_ampersand_term
    ok, err = validate_ampersand_term("Spend & Expense", "BILL Spend & Expense pairs budgeting")
    assert ok is True
    assert err == ""


def test_approve_voice_term_clears_the_masked_finding_and_sibling_rows(lib):
    """Approving a term must (a) mask it out of future scans, and (b)
    immediately resolve every currently-open row of the same rule whose text
    contains it — proving the term actually took effect, not just that a row
    was inserted into voice_approved_terms."""
    tid1 = lib.add_tool("Sibling Tool A", "A tool.", "https://siblinga.example.com", [], approved=1)
    tid2 = lib.add_tool("Sibling Tool B", "A tool.", "https://siblingb.example.com", [], approved=1)
    item1 = lib.add_voice_review_item("tools", tid1, "description", "bare-ampersand", "Works with Bain & Company", source="script")
    item2 = lib.add_voice_review_item("tools", tid2, "description", "bare-ampersand", "Also cites Bain & Company here", source="script")

    lib.approve_voice_term("Bain & Company")

    assert lib.get_voice_review_item(item1)["status"] == "resolved"
    assert lib.get_voice_review_item(item2)["status"] == "resolved"

    # And a fresh scan of raw text containing the same term no longer flags it.
    from linklib.voice_review import mask_approved_ampersand_terms
    approved = [row["term"] for row in lib.list_approved_voice_terms()]
    masked = mask_approved_ampersand_terms("We partner with Bain & Company on this.", approved)
    assert "Bain & Company" not in masked


def test_remove_approved_voice_term_reopens_the_finding_on_the_next_reconcile_pass(lib):
    """Part A item 5 — "Remove reopens the masked findings." Confirmed as
    "on the next reconcile pass," not immediately: `remove_approved_voice_term`
    itself never touches `voice_review_queue` (its own docstring says so —
    it "does not retroactively reopen queue rows already resolved"), but the
    live scan no longer masks the term once it's removed, and
    `reconcile_voice_review_queue()` (the background reconciler) adds a
    fresh `open` row for any live finding not already queued. Together, the
    two satisfy the brief's "Remove reopens the masked findings" —
    mechanically, through the reconciler, not through Remove itself."""
    # A term with no source-level allowlist entry (unlike "Bain & Company",
    # which is in AMPERSAND_NAMES for an unrelated reason — that would mask
    # it at the source scanner level regardless of this DB-backed mechanism).
    tid = lib.add_tool("Reopen Tool", "Works with Widgetco & Partners", "https://reopen.example.com",
                        [], approved=1)
    term_id = lib.approve_voice_term("Widgetco & Partners")

    # A fresh scan finds nothing (masked) and reconcile adds no open row.
    result1 = lib.reconcile_voice_review_queue()
    open_rows = lib.list_voice_review_queue(status="open")
    matching = [i for i in open_rows if i["table_name"] == "tools" and str(i["row_id"]) == str(tid)]
    assert not matching, "masked term must not produce an open row while approved"

    lib.remove_approved_voice_term(term_id)
    result2 = lib.reconcile_voice_review_queue()
    assert result2["added"] >= 1
    open_rows2 = lib.list_voice_review_queue(status="open")
    matching2 = [i for i in open_rows2 if i["table_name"] == "tools" and str(i["row_id"]) == str(tid)
                 and i["rule"] == "bare-ampersand"]
    assert matching2, "removing the approved term must let the finding reopen on the next reconcile pass"


def test_remove_approved_voice_term_lets_the_term_be_flagged_again(lib):
    term_id = lib.approve_voice_term("Foo & Bar")
    from linklib.voice_review import mask_approved_ampersand_terms
    approved = [row["term"] for row in lib.list_approved_voice_terms()]
    masked = mask_approved_ampersand_terms("Foo & Bar shows up here.", approved)
    assert "Foo & Bar" not in masked

    lib.remove_approved_voice_term(term_id)
    approved2 = [row["term"] for row in lib.list_approved_voice_terms()]
    masked2 = mask_approved_ampersand_terms("Foo & Bar shows up here.", approved2)
    assert "Foo & Bar" in masked2
