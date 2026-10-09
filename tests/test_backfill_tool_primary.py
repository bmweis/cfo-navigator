"""scripts/backfill_tool_primary.py (issue #624): preview writes nothing, --apply
fills only single-category vendors, multi-category vendors are never touched and
are printed as the worksheet, and a second run changes nothing."""
import os
import sqlite3

import pytest

from linklib.db import Library
from scripts import backfill_tool_primary as bf


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "scratch.db")
    # Never let a test or ad hoc run regenerate the tracked preferred_sites.opml.
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    lib = Library(path)
    lib.add_tool("Solo", "d", "https://solo.example", ["ERP"], approved=1)
    lib.add_tool("Duo", "d", "https://duo.example", ["ERP", "FP&A"], approved=1)
    lib.add_tool("None", "d", "https://none.example", [], approved=1)
    lib.add_tool("Done", "d", "https://done.example", ["ERP", "FP&A"], approved=1, primary_category="FP&A")
    lib.add_tool("Pending Solo", "d", "https://ps.example", ["Revenue"], approved=0)
    lib.close()
    return path


def _state(path):
    lib = Library(path)
    try:
        return {t["name"]: (t["primary_category"], t["updated_at"], tuple(t["categories"]))
                for t in lib.list_tools(approved_only=False)}
    finally:
        lib.close()


def test_plan_counts_zero_one_many():
    tools = [{"categories": [], "primary_category": ""}, {"categories": ["a"], "primary_category": ""},
             {"categories": ["a", "b"], "primary_category": ""}, {"categories": ["a", "b"], "primary_category": "a"}]
    p = bf.plan(tools)
    assert (len(p["zero"]), len(p["one"]), len(p["many"]), len(p["has_primary"])) == (1, 1, 1, 1)


def test_preview_writes_nothing_and_prints_the_worksheet(db, capsys):
    before = _state(db)
    assert bf.main(["--db", db]) == 0
    assert _state(db) == before
    out = capsys.readouterr().out
    assert "Preview only" in out
    assert "2+ categories (by hand):     1" in out and "1 category (will be filled): 2" in out
    assert "Duo" in out and "[ERP | FP&A]" in out and "WORKSHEET" in out


def test_apply_sets_only_single_category_vendors(db):
    before = _state(db)
    assert bf.main(["--db", db, "--apply"]) == 0
    after = _state(db)
    assert after["Solo"][0] == "ERP" and after["Pending Solo"][0] == "Revenue"
    assert after["Duo"][0] == "" and after["None"][0] == ""      # multi and zero: untouched
    assert after["Done"][0] == "FP&A"                            # existing primary: untouched
    for name in before:
        assert after[name][2] == before[name][2]                 # category sets unchanged
        assert after[name][1] == before[name][1]                 # updated_at not bumped


def test_apply_is_idempotent(db, capsys):
    bf.main(["--db", db, "--apply"])
    first = _state(db)
    capsys.readouterr()
    assert bf.main(["--db", db, "--apply"]) == 0
    assert _state(db) == first
    assert "Wrote 0 of 0." in capsys.readouterr().out


def test_missing_db_exits_without_creating_a_file(tmp_path):
    missing = str(tmp_path / "nope.db")
    with pytest.raises(SystemExit):
        bf.main(["--db", missing])
    assert not os.path.exists(missing)


def test_script_is_in_the_registry():
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    assert any(row[0] == "backfill_tool_primary.py" for row in appmod._SCRIPT_REGISTRY)
