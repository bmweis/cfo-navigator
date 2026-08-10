"""scripts/audit_screenshot_is_product_regression.py (2026-08 incident
follow-up): compares a pre-bug snapshot against the current DB to find
exactly which rows had their screenshot_is_product flag silently cleared —
see that script's docstring for the full incident. Read-only against both
files; never writes.
"""
import shutil
import sys

sys.path.insert(0, "/home/user/cfo-navigator")

from linklib.db import Library


def _capsys_lines(capsys):
    return capsys.readouterr().out


def test_detects_clobbered_row(tmp_path, monkeypatch, capsys):
    import scripts.audit_screenshot_is_product_regression as audit

    before = str(tmp_path / "before.db")
    lib = Library(before)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product.png", 1)
    lib.close()

    after = str(tmp_path / "after.db")
    shutil.copy(before, after)
    lib = Library(after)
    lib.conn.execute("UPDATE tools SET screenshot_is_product=0 WHERE id=?", (a,))
    lib.conn.commit()
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--before-db", before, "--after-db", after])
    rc = audit.main()
    assert rc == 0
    out = _capsys_lines(capsys)
    assert "CLOBBERED" in out
    assert "Runway" in out
    assert "1 row(s)" in out


def test_no_false_positive_when_flag_survives(tmp_path, monkeypatch, capsys):
    import scripts.audit_screenshot_is_product_regression as audit

    before = str(tmp_path / "before.db")
    lib = Library(before)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product.png", 1)
    lib.close()

    after = str(tmp_path / "after.db")
    shutil.copy(before, after)  # flag untouched

    monkeypatch.setattr(sys, "argv", ["prog", "--before-db", before, "--after-db", after])
    rc = audit.main()
    assert rc == 0
    out = _capsys_lines(capsys)
    assert "No clobbered rows found" in out


def test_no_false_positive_when_already_migrated(tmp_path, monkeypatch, capsys):
    """A row already moved into app_screenshot_url (e.g. the Phase E manual
    migration script was run, or someone re-curated by hand) is not a
    clobber even though screenshot_is_product now reads 0."""
    import scripts.audit_screenshot_is_product_regression as audit

    before = str(tmp_path / "before.db")
    lib = Library(before)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product.png", 1)
    lib.close()

    after = str(tmp_path / "after.db")
    shutil.copy(before, after)
    lib = Library(after)
    lib.migrate_app_screenshot_from_product_flag()
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--before-db", before, "--after-db", after])
    rc = audit.main()
    assert rc == 0
    out = _capsys_lines(capsys)
    assert "No clobbered rows found" in out


def test_missing_row_reported_separately_not_as_clobbered(tmp_path, monkeypatch, capsys):
    import scripts.audit_screenshot_is_product_regression as audit

    before = str(tmp_path / "before.db")
    lib = Library(before)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product.png", 1)
    lib.close()

    after = str(tmp_path / "after.db")
    shutil.copy(before, after)
    lib = Library(after)
    lib.conn.execute("DELETE FROM tools WHERE id=?", (a,))
    lib.conn.commit()
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--before-db", before, "--after-db", after])
    rc = audit.main()
    assert rc == 0
    out = _capsys_lines(capsys)
    assert "No clobbered rows found" in out
    assert "no longer exist in the current DB" in out


def test_missing_db_file_errors_cleanly(tmp_path, monkeypatch, capsys):
    import scripts.audit_screenshot_is_product_regression as audit
    monkeypatch.setattr(sys, "argv", [
        "prog", "--before-db", str(tmp_path / "nope.db"), "--after-db", str(tmp_path / "nope2.db"),
    ])
    rc = audit.main()
    assert rc == 1
