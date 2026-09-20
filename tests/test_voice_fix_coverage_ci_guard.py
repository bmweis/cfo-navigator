"""CI drift-detector for the `_voice_fix`/`_vf` write-path backstop — option
(b) from CLAUDE.md's "Structural-enforcement assessment" (item 3c of the
2026-09 voice-review-queue PR). This is a CI-TIME GUARD, not a runtime
guarantee: a new `Library` method that writes to a scanned (table, column)
pair without calling `self._vf`/`self.log_voice_correction` somewhere in
its body will fail THIS test the next time it runs — it does not stop the
write from happening in production if this test is skipped or not run.
The honest, disclosed alternative (SQL-layer interception, guaranteeing
coverage for every write regardless of whether a test ever runs) was
assessed and rejected in the same PR for the reasons CLAUDE.md's own
write-up gives — broad blast radius, no existing precedent in this
codebase for that shape of "magic happens on every write" mechanism.

Method: parse `linklib/db.py` with `ast`, find every `Library` method whose
body's source text contains a raw SQL string referencing `UPDATE <table>`
or `INSERT INTO <table>` for a table name in `voice_db_scan._SCAN_TABLES`,
and assert the method's own source also calls the QUEUE-LOGGING backstop
(`self._vf(` or `self.log_voice_correction(`) somewhere — a coarse but
real check: it can't confirm every individual scanned COLUMN within that
method is instrumented, only that the method touches the backstop
somewhere. **Tightened (2026-09, coordinator review of the
remaining-tables PR)**: this used to accept a bare `_voice_fix(` call as
equivalent to `self._vf(`/`self.log_voice_correction(` — which is wrong,
since a bare call normalizes the text but never logs anything to
`voice_review_queue`. That gap let `add_tool_category`/
`rename_tool_category`/`add_community_category`/`rename_community_category`
(and, as it turned out, `add_tool` itself — a pre-existing gap from the
ORIGINAL voice-review-queue PR) silently apply un-logged corrections while
still passing this guard. See `test_every_write_to_a_scanned_table_calls_
the_voice_fix_backstop`'s own docstring and `_ALLOWLIST`'s comments for
the full incident. A hand-curated allowlist covers genuine, reasoned
exceptions (rank/tag labels — not prose; the two original-content
mirror-sync methods, which read already-normalized data back rather than
writing new text, and so have nothing to log) plus write paths a PR's own
scope has explicitly, disclosedly left uninstrumented — every allowlist
entry is named, not a blanket skip.
"""
from __future__ import annotations

import ast
import pathlib
import re

from linklib.voice_db_scan import _SCAN_TABLES

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DB_PY = _ROOT / "linklib" / "db.py"

# table -> its own scanned prose columns (id column excluded — this is about
# whether a WRITE touches a prose column specifically, not just the table).
_SCANNED_COLUMNS: dict[str, tuple[str, ...]] = {
    t: cols for t, _id, cols, _exempt in _SCAN_TABLES
}

# Real, named exceptions — never a blanket skip.
_ALLOWLIST = {
    # Rank/short labels, not prose (same reasoning as the entity-name
    # typography exemption in voice_db_scan.py).
    "update_game_rank_settings",
    "rename_tag",
    # Original-content mirror-sync: reads Library.get_original_content()'s
    # already-normalized value back rather than writing fresh text — see
    # CLAUDE.md's Published-Content Ingestion note.
    "insert_mirrored_article",
    "update_mirrored_article",
    # `ai_surfaces`, `benchmarks`, `thought_leadership`, `communities`, and
    # `community_profiles` were instrumented in the follow-up PR to the
    # voice-review-queue PR — `add_ai_surface`/`update_ai_surface`,
    # `add_benchmark`/`update_benchmark`/`update_benchmark_content`,
    # `add_thought_leadership`/`update_thought_leadership`,
    # `add_community`/`update_community`/`update_community_content`, and
    # `upsert_community_profile`/`update_community_profile_research_fields`
    # are all deliberately NOT in this list any more; see linklib/db.py for
    # the actual _vf/log_voice_correction wiring on each.
    #
    # `tool_categories`/`community_categories` were flagged (not
    # instrumented) in that same follow-up PR, on the reasoning that a bare
    # `_voice_fix(...)` call already satisfied this guard, so there was
    # nothing forcing the fix — coordinator review of that PR caught the
    # real problem this reasoning was resting on: a bare `_voice_fix()`
    # call normalizes the text but never reaches `voice_review_queue`, so
    # `add_tool_category`/`rename_tool_category`/`add_community_category`/
    # `rename_community_category` were silently applying un-logged
    # corrections the whole time. Fixed by (1) tightening the check below
    # to require `self._vf(`/`self.log_voice_correction(` specifically —
    # a bare `_voice_fix()` call is no longer sufficient — and (2) wiring
    # all four methods the same way as every other add/update method in
    # this file. The same tightened check also caught `add_tool` itself
    # (a pre-existing gap in the ORIGINAL voice-review-queue PR — it
    # called bare `_voice_fix()` on `description`/`summary` with no
    # queue logging, never caught because the guard accepted a bare call
    # as sufficient at the time), fixed the same way. None of the four
    # category methods or `add_tool` are in this allowlist any more.
    # Confirmed false positives from the coarse whole-method-body scan
    # (read directly, not assumed): __init__ is the schema-migration list
    # (ALTER TABLE DDL mentioning column names, never a content write);
    # _migrate_community_local_markets is a one-time backfill migration, not
    # a live write path; delete_tool_category/delete_community_category
    # only ever touch categories_json (via a SELECT ... WHERE categories_json
    # LIKE — the match came from an unrelated "SELECT name FROM
    # tool_categories" lookup line, not a write to a scanned prose column).
    "__init__", "_migrate_community_local_markets",
    "delete_tool_category", "delete_community_category",
}


def _method_defs(source: str):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Library":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield item


def _touches_scanned_table(src_segment: str) -> str | None:
    """A table name from `_SCANNED_COLUMNS`, only when the segment both
    writes to that table (UPDATE/INSERT INTO) AND references at least one
    of that table's own scanned PROSE columns as a SQL token — not just
    "mentions the table name somewhere" (a first draft of this check found
    ~30 false positives this way: methods that write only non-prose columns
    like `approved`/`logo_path`/`needs_review`/`mirrored_article_id` to a
    scanned table, which this backstop was never meant to guard)."""
    up = src_segment.upper()
    for table, columns in _SCANNED_COLUMNS.items():
        if f"UPDATE {table.upper()}" not in up and f"INSERT INTO {table.upper()}" not in up:
            continue
        for col in columns:
            if re.search(r"\b" + re.escape(col) + r"\b", src_segment):
                return table
    return None


def test_every_write_to_a_scanned_table_calls_the_voice_fix_backstop():
    """Tightened (2026-09, coordinator review of the remaining-tables PR):
    this used to accept a bare `_voice_fix(` call as sufficient — which
    normalizes the text but never logs anything to `voice_review_queue`.
    That's exactly the gap `add_tool_category`/`rename_tool_category`/
    `add_community_category`/`rename_community_category` were caught in:
    all four called bare `_voice_fix()` (so the OLD version of this check
    passed) while silently applying corrections that never reached the
    review queue. The guard now requires the QUEUE-LOGGING call
    specifically — `self._vf(` (the inline wrapper, used when the row id is
    known up front) or `self.log_voice_correction(` (the after-INSERT
    pattern, used when the row id isn't known until `cur.lastrowid`) — a
    bare `_voice_fix(` with neither present is no longer enough. A write
    path that genuinely can't log anything (it never writes fresh text —
    see `insert_mirrored_article`/`update_mirrored_article` below) belongs
    in `_ALLOWLIST` with its reason stated, not silently passed by a call
    the guard used to (wrongly) treat as equivalent."""
    source = _DB_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    offenders = []
    for node in _method_defs(source):
        if node.name in _ALLOWLIST:
            continue
        segment = "\n".join(lines[node.lineno - 1:node.end_lineno])
        table = _touches_scanned_table(segment)
        if not table:
            continue
        if "self._vf(" not in segment and "self.log_voice_correction(" not in segment:
            offenders.append((node.name, table))
    assert not offenders, (
        "Library method(s) write to a scanned (voice-copy) table with no "
        "queue-logging call (self._vf/self.log_voice_correction) anywhere "
        "in the method body — a bare _voice_fix() call alone normalizes "
        "the text but never reaches the review queue: "
        f"{offenders}. Add self._vf()/log_voice_correction(), or add the "
        "method name to this test's _ALLOWLIST with a stated reason."
    )


def test_the_guard_actually_fires_on_a_real_offender():
    """Proves this isn't a check that can never fail — a hand-built fake
    method touching a scanned table with no _voice_fix call must be caught."""
    fake_source = '''
class Library:
    def totally_unguarded_write(self, tools_id, text):
        self.conn.execute("UPDATE tools SET description=? WHERE id=?", (text, tools_id))
'''
    tree = ast.parse(fake_source)
    for node in tree.body[0].body:
        segment = ast.get_source_segment(fake_source, node)
        table = _touches_scanned_table(segment)
        assert table == "tools"
        assert "self._vf(" not in segment and "self.log_voice_correction(" not in segment


def test_the_tightened_guard_fires_on_a_bare_voice_fix_call_with_no_queue_logging():
    """The exact real-world shape the guard used to (wrongly) pass, before
    the 2026-09 tightening: `add_tool_category`/`rename_tool_category`/
    `add_community_category`/`rename_community_category` all called bare
    `_voice_fix()` — which mechanically normalizes the text — with no
    `self._vf()`/`log_voice_correction()` call anywhere, so the correction
    never reached `voice_review_queue`. The OLD predicate
    (`"_voice_fix(" not in segment and "self._vf(" not in segment`) would
    have passed this fake method silently, since a bare `_voice_fix(` call
    IS present. The tightened predicate must fail it."""
    fake_source = '''
class Library:
    def silently_normalizes_but_never_logs(self, category_id, description):
        self.conn.execute(
            "UPDATE tool_categories SET description=? WHERE id=?",
            (_voice_fix(description), category_id),
        )
'''
    tree = ast.parse(fake_source)
    for node in tree.body[0].body:
        segment = ast.get_source_segment(fake_source, node)
        table = _touches_scanned_table(segment)
        assert table == "tool_categories"
        assert "_voice_fix(" in segment   # the old, now-insufficient signal is present
        assert "self._vf(" not in segment and "self.log_voice_correction(" not in segment
