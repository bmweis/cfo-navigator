"""scripts/archive/migrate_software_tags.py — the one-off (re-runnable) migration
that collapses the old ad hoc Software category vocabulary down to the fixed
15-tag taxonomy. Covers the CATEGORY_MAP entries added after a real
production run surfaced unmapped stragglers (Tax Compliance, CLM, Legal AI,
Process Optimization), plus the end-to-end apply/dry-run/idempotency
behavior.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from scripts.archive import migrate_software_tags as script


@pytest.fixture
def db():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.mark.parametrize("old_name,new_name", [
    ("Tax Compliance", "Tax Management"),
    ("CLM", "Legal and Contracting"),
    ("Legal AI", "Legal and Contracting"),
    ("Process Optimization", "Accounting"),
])
def test_category_map_entry(old_name, new_name):
    assert script.CATEGORY_MAP[old_name] == new_name
    assert new_name in script.NEW_NAMES


def test_apply_remaps_stragglers_and_rebuilds_pill_table(db, monkeypatch, capsys):
    lib = Library(db)
    a = lib.add_tool("Ironclad", "CLM tool", "https://ironclad.com", ["CLM"], approved=1)
    b = lib.add_tool("Numeric", "Tax tool", "https://numeric.io", ["Tax Compliance"], approved=1)
    c = lib.add_tool("Multi", "Multiple old cats", "https://multi.example",
                      ["Legal AI", "Process Optimization"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["migrate_software_tags", "--db", db])
    script.main()
    out = capsys.readouterr().out
    assert "no mapping" not in out.lower()   # nothing left unmapped

    lib = Library(db)
    assert lib.get_tool(a)["categories"] == ["Legal and Contracting"]
    assert lib.get_tool(b)["categories"] == ["Tax Management"]
    assert lib.get_tool(c)["categories"] == ["Accounting", "Legal and Contracting"]

    pill_names = {row["name"] for row in lib.list_tool_categories()}
    assert pill_names == script.NEW_NAMES
    lib.close()


def test_dry_run_writes_nothing(db, monkeypatch, capsys):
    lib = Library(db)
    tool_id = lib.add_tool("Ironclad", "CLM tool", "https://ironclad.com", ["CLM"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["migrate_software_tags", "--db", db, "--dry-run"])
    script.main()
    out = capsys.readouterr().out
    assert "no changes written" in out.lower()

    lib = Library(db)
    assert lib.get_tool(tool_id)["categories"] == ["CLM"]   # untouched
    lib.close()


def test_rerunning_is_idempotent(db, monkeypatch, capsys):
    lib = Library(db)
    tool_id = lib.add_tool("Ironclad", "CLM tool", "https://ironclad.com", ["CLM"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["migrate_software_tags", "--db", db])
    script.main()
    capsys.readouterr()
    script.main()   # second run
    out = capsys.readouterr().out
    assert "0 would change tags" in out or "0 tool(s)" in out

    lib = Library(db)
    assert lib.get_tool(tool_id)["categories"] == ["Legal and Contracting"]
    lib.close()


def test_still_flags_genuinely_unmapped_category(db, monkeypatch, capsys):
    lib = Library(db)
    lib.add_tool("Mystery Co", "Unknown category", "https://mystery.example",
                 ["Some Brand New Category"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["migrate_software_tags", "--db", db, "--dry-run"])
    script.main()
    out = capsys.readouterr().out
    assert "Some Brand New Category" in out
    assert "no mapping" in out.lower()
