"""Item 3b audit (2026-09 voice-enforcement PR follow-up) — every other
`Library` write path found to skip the `_voice_fix()`/`normalize_voice_mechanics`
spaced-em-dash backstop, the same omission `add_category_feature`/
`update_category_feature` had (see tests/test_voice_db_scan.py).

Audited every INSERT/UPDATE-issuing method in linklib/db.py against the
columns `linklib/voice_db_scan.py`'s own `_SCAN_TABLES`/`_SCAN_SETTINGS_KEYS`
already treat as real user-facing copy. Real gaps found and fixed here:
tool_categories.description, community_categories.description,
benchmarks.name/description, thought_leadership.title/venue/description,
original_content.title/teaser/body_md, ai_surfaces.title/teaser/body_md,
and every settings-backed copy field (homepage_*, about_page_copy,
htib_*_copy) via a single fix at `set_setting()` itself, since all four
/admin/copy/* routes call it directly with no wrapping of their own.

Not fixed, and not a gap: `game_rank_settings.label`/`difficulty_label`
(short rank labels, not prose — same reasoning as tag names), `rename_tag`
(short keyword labels), `insert_mirrored_article`/`update_mirrored_article`
(read the already-normalized `original_content` row back via
`sync_original_content_article`, so fixing the two source methods above is
the real root-cause fix — no separate call needed), and every entity-name
column already covered by CLAUDE.md's typography-exemption note (tools/
communities/benchmarks.name get mechanical checks, not typography, but
_voice_fix only ever touches em-dash spacing, never ampersands, so applying
it to benchmarks.name here is harmless and consistent).
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    yield lib
    lib.close()
    if os.path.exists(db):
        os.remove(db)


def _clean(text):
    return "—" in text and " — " not in text


def test_tool_category_add_and_rename_normalize(lib):
    cid = lib.add_tool_category("Finance", "Flags items — not a balance change.")
    row = lib.get_tool_category(cid) if hasattr(lib, "get_tool_category") else None
    if row is None:
        row = dict(lib.conn.execute("SELECT * FROM tool_categories WHERE id=?", (cid,)).fetchone())
    assert _clean(row["description"])
    lib.rename_tool_category(cid, "Finance II", "Renamed — still spaced.")
    row2 = dict(lib.conn.execute("SELECT * FROM tool_categories WHERE id=?", (cid,)).fetchone())
    assert _clean(row2["description"])


def test_community_category_add_and_rename_normalize(lib):
    cid = lib.add_community_category("Ops", "Handles ops — not necessarily finance.")
    row = dict(lib.conn.execute("SELECT * FROM community_categories WHERE id=?", (cid,)).fetchone())
    assert _clean(row["description"])
    lib.rename_community_category(cid, "Ops II", "Renamed — still spaced.")
    row2 = dict(lib.conn.execute("SELECT * FROM community_categories WHERE id=?", (cid,)).fetchone())
    assert _clean(row2["description"])


def test_benchmark_add_and_update_normalize(lib):
    bid = lib.add_benchmark("Acme Benchmarks", "https://example.com", "A resource — for finance teams.")
    row = dict(lib.conn.execute("SELECT * FROM benchmarks WHERE id=?", (bid,)).fetchone())
    assert _clean(row["description"])
    lib.update_benchmark(bid, "Acme Benchmarks", "https://example.com",
                          "Updated — description.", "Private", "free")
    row2 = dict(lib.conn.execute("SELECT * FROM benchmarks WHERE id=?", (bid,)).fetchone())
    assert _clean(row2["description"])
    lib.update_benchmark_content(bid, "Acme Benchmarks", "Synced — description.")
    row3 = dict(lib.conn.execute("SELECT * FROM benchmarks WHERE id=?", (bid,)).fetchone())
    assert _clean(row3["description"])


def test_thought_leadership_add_and_update_normalize(lib):
    tid = lib.add_thought_leadership("writing", "A Piece — about finance", description="Seamless — read.")
    row = dict(lib.conn.execute("SELECT * FROM thought_leadership WHERE id=?", (tid,)).fetchone())
    assert _clean(row["title"])
    assert _clean(row["description"])
    lib.update_thought_leadership(tid, "writing", "Updated — title", "https://example.com", "Venue — name",
                                   "Jun 2026", "2026-06", "Updated — description.", False, 0)
    row2 = dict(lib.conn.execute("SELECT * FROM thought_leadership WHERE id=?", (tid,)).fetchone())
    assert _clean(row2["title"])
    assert _clean(row2["venue"])
    assert _clean(row2["description"])


def test_original_content_add_and_update_normalize(lib):
    oid = lib.add_original_content("test-slug", "A Title — with a dash", teaser="A teaser — text.",
                                    body_md="Body — text.")
    row = dict(lib.conn.execute("SELECT * FROM original_content WHERE id=?", (oid,)).fetchone())
    assert _clean(row["title"])
    assert _clean(row["teaser"])
    assert _clean(row["body_md"])
    lib.update_original_content(oid, "test-slug", "Updated — title", "Updated — teaser", "Guide", "Read",
                                 "Updated — body.", "live", False, "", "", 0)
    row2 = dict(lib.conn.execute("SELECT * FROM original_content WHERE id=?", (oid,)).fetchone())
    assert _clean(row2["title"])
    assert _clean(row2["teaser"])
    assert _clean(row2["body_md"])


def test_ai_surface_add_and_update_normalize(lib):
    aid = lib.add_ai_surface("ai-slug", "A Title — with a dash", teaser="A teaser — text.",
                              body_md="Body — text.")
    row = dict(lib.conn.execute("SELECT * FROM ai_surfaces WHERE id=?", (aid,)).fetchone())
    assert _clean(row["title"])
    assert _clean(row["teaser"])
    assert _clean(row["body_md"])
    lib.update_ai_surface(aid, "ai-slug", "Updated — title", "Updated — teaser", "Updated — body.",
                           "", "live", 0)
    row2 = dict(lib.conn.execute("SELECT * FROM ai_surfaces WHERE id=?", (aid,)).fetchone())
    assert _clean(row2["title"])
    assert _clean(row2["teaser"])
    assert _clean(row2["body_md"])


def test_set_setting_normalizes_prose_values(lib):
    lib.set_setting("homepage_teaser_copy", "A teaser — with a spaced dash.")
    assert _clean(lib.get_setting("homepage_teaser_copy"))
    lib.set_setting("about_page_copy", "A bio — with a spaced dash.")
    assert _clean(lib.get_setting("about_page_copy"))


def test_set_setting_is_a_no_op_on_non_prose_values(lib):
    """The fix must not corrupt a numeric/flag/token/JSON settings value —
    normalize_voice_mechanics only ever matches a spaced em dash, so any
    value without one round-trips byte-for-byte."""
    lib.set_setting("ask_default_cap_usd", "5.00")
    assert lib.get_setting("ask_default_cap_usd") == "5.00"
    lib.set_setting("exa_enabled", "1")
    assert lib.get_setting("exa_enabled") == "1"
    lib.set_setting("enrich_model", "claude-opus-5")
    assert lib.get_setting("enrich_model") == "claude-opus-5"
    import json
    blob = json.dumps({"a": 1, "b": [1, 2, 3]})
    lib.set_setting("avatar_json", blob)
    assert lib.get_setting("avatar_json") == blob
