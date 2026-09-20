"""CI drift-detector for the `_voice_fix`/`_vf` write-path backstop — option
(b) from CLAUDE.md's "Structural-enforcement assessment" (item 3c of the
2026-09 voice-review-queue PR). This is a CI-TIME GUARD, not a runtime
guarantee: a new `Library` method that writes to a scanned (table, column)
pair without calling `_voice_fix`/`self._vf` somewhere in its body will
fail THIS test the next time it runs — it does not stop the write from
happening in production if this test is skipped or not run. The honest,
disclosed alternative (SQL-layer interception, guaranteeing coverage for
every write regardless of whether a test ever runs) was assessed and
rejected in the same PR for the reasons CLAUDE.md's own write-up gives —
broad blast radius, no existing precedent in this codebase for that shape
of "magic happens on every write" mechanism.

Method: parse `linklib/db.py` with `ast`, find every `Library` method whose
body's source text contains a raw SQL string referencing `UPDATE <table>`
or `INSERT INTO <table>` for a table name in `voice_db_scan._SCAN_TABLES`,
and assert the method's own source also calls `_voice_fix(` or `self._vf(`
somewhere — a coarse but real check: it can't confirm every individual
scanned COLUMN within that method is instrumented, only that the method
touches the backstop somewhere. A hand-curated allowlist covers genuine,
reasoned exceptions (rank/tag labels — not prose; the two original-content
mirror-sync methods, which read already-normalized data back rather than
writing new text) plus write paths this PR's own scope explicitly left
uninstrumented (see CLAUDE.md's voice-review-queue write-up for the exact
list) — every allowlist entry is named, not a blanket skip.
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
    # Deliberately out of scope for THIS PR's write-path instrumentation
    # pass (see CLAUDE.md's voice-review-queue write-up for the disclosed
    # scope cut) — flagged here by name, not silently allowed forever.
    # `original_content` was instrumented in this same PR's follow-up round
    # (Brian's own published thought leadership — the single highest-value
    # table for this whole feature) and is deliberately NOT in this list
    # any more; see add_original_content/update_original_content in
    # linklib/db.py for the actual _vf/log_voice_correction wiring.
    "add_ai_surface", "update_ai_surface",
    "add_benchmark", "update_benchmark", "update_benchmark_content",
    "add_thought_leadership", "update_thought_leadership",
    "add_community", "update_community", "update_community_content",
    "upsert_community_profile", "update_community_profile_research_fields",
    "add_community_category", "rename_community_category",
    "add_tool_category", "rename_tool_category",
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
        if "_voice_fix(" not in segment and "self._vf(" not in segment:
            offenders.append((node.name, table))
    assert not offenders, (
        "Library method(s) write to a scanned (voice-copy) table with no "
        "_voice_fix/self._vf call anywhere in the method body: "
        f"{offenders}. Add the backstop, or add the method name to this "
        "test's _ALLOWLIST with a stated reason."
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
        assert "_voice_fix(" not in segment and "self._vf(" not in segment
