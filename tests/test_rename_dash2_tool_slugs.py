"""scripts/rename_dash2_tool_slugs.py — the one-off fix for seven
`tools.slug` values carrying a spurious `-2` suffix (dealhub-2,
liveflow-2, puzzle-2, zenskar-2, m3ter-2, digits-2, tropic-2).

Covers: preview makes no writes, --apply renames and write-then-read-back
verifies, a missing/already-renamed old slug is skipped (not an error), a
real collision with the target bare slug aborts that pair without writing
anything for it, and re-running after a successful apply is a clean no-op
(nothing left to rename)."""
import os
import subprocess
import sys
import tempfile

import pytest

from linklib.db import Library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def _run(db_path: str, *, apply: bool = False):
    args = [sys.executable, "-m", "scripts.rename_dash2_tool_slugs", "--db", db_path]
    if apply:
        args.append("--apply")
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)


def _seed(db_path: str) -> dict:
    """A tool whose slug is exactly 'tropic-2' — how it got that way (a
    creation-time collision, later resolved for the base slug) doesn't
    matter to the script, which only ever looks at the current slug
    column; setting it directly here keeps the fixture simple and robust
    rather than trying to replay add_tool()'s exact collision algorithm."""
    lib = Library(db_path)
    tool_id = lib.add_tool("Tropic", "d", "https://tropic.example",
                            ["Procurement/Spend"], approved=1)
    lib.conn.execute("UPDATE tools SET slug='tropic-2' WHERE id=?", (tool_id,))
    lib.conn.commit()
    lib.close()
    return {"id": tool_id, "slug": "tropic-2"}


def test_preview_makes_no_changes(db_path):
    seeded = _seed(db_path)
    result = _run(db_path, apply=False)
    assert result.returncode == 0, result.stderr
    assert "tropic-2" in result.stdout
    assert "PREVIEW ONLY" in result.stdout
    lib = Library(db_path)
    row = lib.conn.execute("SELECT slug FROM tools WHERE id=?", (seeded["id"],)).fetchone()
    lib.close()
    assert row["slug"] == "tropic-2"


def test_apply_renames_and_verifies(db_path):
    seeded = _seed(db_path)
    result = _run(db_path, apply=True)
    assert result.returncode == 0, result.stderr
    assert "confirmed: " in result.stdout
    assert "Verified" in result.stdout
    lib = Library(db_path)
    row = lib.conn.execute("SELECT slug FROM tools WHERE id=?", (seeded["id"],)).fetchone()
    lib.close()
    assert row["slug"] == "tropic"


def test_apply_only_touches_slug_nothing_else(db_path):
    seeded = _seed(db_path)
    lib = Library(db_path)
    before = lib.get_tool(seeded["id"])
    lib.close()

    result = _run(db_path, apply=True)
    assert result.returncode == 0, result.stderr

    lib = Library(db_path)
    after = lib.get_tool(seeded["id"])
    lib.close()
    for key in before:
        if key in ("slug", "updated_at"):
            continue
        assert after[key] == before[key], f"{key} changed unexpectedly"


def test_missing_old_slug_is_skipped_not_an_error(db_path):
    """No row seeded at all — every RENAMES pair is a clean no-op skip,
    not a failure."""
    lib = Library(db_path)
    lib.close()
    result = _run(db_path, apply=True)
    assert result.returncode == 0, result.stderr
    assert "no row currently has this slug" in result.stdout
    assert "Nothing to do." in result.stdout


def test_real_collision_aborts_that_pair_without_writing(db_path):
    """If the target bare slug is already taken by a DIFFERENT row at
    run time, the script must refuse to touch that pair — never a raw
    UNIQUE-constraint crash, never a silent overwrite."""
    seeded = _seed(db_path)
    lib = Library(db_path)
    # A different row now legitimately holds the bare "tropic" slug.
    other_id = lib.add_tool("Some Other Tropic-Named Thing", "d",
                             "https://another-host-marker/x", [], approved=1)
    lib.conn.execute("UPDATE tools SET slug='tropic' WHERE id=?", (other_id,))
    lib.conn.commit()
    lib.close()

    result = _run(db_path, apply=True)
    assert result.returncode == 0, result.stderr
    assert "STOP:" in result.stdout
    assert "would collide with tool" in result.stdout

    lib = Library(db_path)
    still = lib.conn.execute("SELECT slug FROM tools WHERE id=?", (seeded["id"],)).fetchone()
    other_row = lib.conn.execute("SELECT slug FROM tools WHERE id=?", (other_id,)).fetchone()
    lib.close()
    assert still["slug"] == "tropic-2"       # untouched
    assert other_row["slug"] == "tropic"      # untouched


def test_rerun_after_apply_is_a_clean_noop(db_path):
    _seed(db_path)
    first = _run(db_path, apply=True)
    assert first.returncode == 0, first.stderr

    second = _run(db_path, apply=True)
    assert second.returncode == 0, second.stderr
    assert "Nothing to do." in second.stdout


def test_missing_db_exits_nonzero_and_never_creates_a_file():
    missing_path = tempfile.mktemp(suffix=".db")
    assert not os.path.exists(missing_path)
    result = _run(missing_path, apply=False)
    assert result.returncode != 0
    assert not os.path.exists(missing_path)
