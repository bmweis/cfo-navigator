"""Checks-page follow-ups, Part A (2026-09): the Database-backed copy check
honors decisions made in /admin/voice/review-queue.

Live evidence that motivated this: /admin/checks reported two "seamless"
buzzword violations that had both been decided with Allow once in the
queue — a red status nobody could clear by making a decision. A finding
covered by a row-level exception or an "Always allow" term is not a
violation; it's stated on its own decisions line instead, and removing the
decision puts it back on the next pass. The summary row and the section
body are built from the same sentence, so they can't disagree.
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.voice_db_scan import scan_db_copy, scan_db_copy_report


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    yield lib
    lib.close()
    if os.path.exists(db):
        os.remove(db)


def _tool_with_buzzword(lib):
    tid = lib.add_tool("Acme", "A seamless close for finance teams.", "https://acme.example", [])
    return tid


def _allow_once(lib, table, row_id, column, rule):
    item_id = lib.add_voice_review_item(table, row_id, column, rule, "seamless")
    assert item_id
    assert lib.resolve_voice_review_item(item_id, "accept_exception")
    return item_id


def test_row_exception_is_not_a_violation_and_removing_it_brings_it_back(lib):
    tid = _tool_with_buzzword(lib)
    report = scan_db_copy_report(lib)
    assert [v.rule for v in report.violations if v.table == "tools"] == ["buzzword"]
    assert report.allowed_once == ()

    item_id = _allow_once(lib, "tools", tid, "description", "buzzword")
    report = scan_db_copy_report(lib)
    assert not [v for v in report.violations if v.table == "tools"]
    assert [(v.table, v.column, v.rule) for v in report.allowed_once] == [("tools", "description", "buzzword")]
    assert scan_db_copy(lib) == []

    # Removing the decision: the finding counts again on the next pass.
    lib.conn.execute("DELETE FROM voice_review_queue WHERE id=?", (item_id,))
    lib.conn.commit()
    report = scan_db_copy_report(lib)
    assert [v.rule for v in report.violations if v.table == "tools"] == ["buzzword"]
    assert report.allowed_once == ()


def test_row_exception_is_scoped_to_its_own_record(lib):
    tid = _tool_with_buzzword(lib)
    other = lib.add_tool("Other", "Also seamless.", "https://other.example", [])
    _allow_once(lib, "tools", tid, "description", "buzzword")
    report = scan_db_copy_report(lib)
    assert [(v.table, str(v.row_id)) for v in report.violations] == [("tools", str(other))]
    assert len(report.allowed_once) == 1


def test_settings_row_exception_is_honored(lib):
    lib.set_setting("homepage_teaser_copy", "A seamless product.")
    assert scan_db_copy_report(lib).violations
    _allow_once(lib, "settings", None, "homepage_teaser_copy", "buzzword")
    report = scan_db_copy_report(lib)
    assert report.violations == ()
    assert len(report.allowed_once) == 1


def test_approved_term_is_not_a_violation_and_is_counted(lib):
    lib.set_setting("homepage_teaser_copy", "Advice from Smith & Jones veterans.")
    report = scan_db_copy_report(lib)
    assert [v.rule for v in report.violations] == ["bare-ampersand"]
    assert report.always_allowed_count == 0

    term_id = lib.approve_voice_term("Smith & Jones")
    report = scan_db_copy_report(lib)
    assert report.violations == ()
    assert report.always_allowed_count == 1

    lib.remove_approved_voice_term(term_id)
    report = scan_db_copy_report(lib)
    assert [v.rule for v in report.violations] == ["bare-ampersand"]
    assert report.always_allowed_count == 0


def test_reconciler_does_not_reopen_an_allowed_once_finding(lib):
    tid = _tool_with_buzzword(lib)
    _allow_once(lib, "tools", tid, "description", "buzzword")
    assert lib.reconcile_voice_review_queue() == {"added": 0, "closed": 0}
    assert lib.list_voice_review_queue(status="open") == []


# --- /admin/checks rendering --------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    lib = Library(db)
    yield c, lib
    lib.close()
    if os.path.exists(db):
        os.remove(db)


def _summary_details(html: str) -> str:
    m = re.search(r'>Database copy</a></td><td[^>]*>.*?</td><td[^>]*>(.*?)</td>', html, re.S)
    assert m, "Database copy summary row not found"
    return m.group(1)


def _section(html: str) -> str:
    start = html.index('id="db-copy-scan"')
    return html[start:html.index('id="disk-space"', start)]


def test_summary_and_section_agree_with_decisions(client):
    c, lib = client
    tid = lib.add_tool("Acme", "A seamless close.", "https://acme.example", [])
    lib.add_tool("Beta", "Another seamless thing.", "https://beta.example", [])
    # add_tool copies description into summary, so each tool carries the
    # finding in two columns.
    _allow_once(lib, "tools", tid, "description", "buzzword")
    _allow_once(lib, "tools", tid, "summary", "buzzword")
    lib.set_setting("homepage_teaser_copy", "Advice from Smith & Jones veterans.")
    lib.approve_voice_term("Smith & Jones")

    html = c.get("/admin/checks").text
    expected = "2 violations. 2 allowed once, 1 always allowed."
    assert _summary_details(html) == expected
    section = _section(html)
    assert expected in section
    assert '>2 allowed once</a>' in section and '>1 always allowed</a>' in section
    assert 'href="/admin/voice/review-queue#voice-resolved"' in section
    assert 'href="/admin/voice#approved-terms"' in section


def test_all_decided_reads_clean(client):
    c, lib = client
    tid = lib.add_tool("Acme", "A seamless close.", "https://acme.example", [])
    _allow_once(lib, "tools", tid, "description", "buzzword")
    _allow_once(lib, "tools", tid, "summary", "buzzword")
    html = c.get("/admin/checks").text
    assert _summary_details(html) == "0 violations. 2 allowed once, 0 always allowed."
    section = _section(html)
    assert "By table:" not in section  # no violation list
    status = section[section.index('class="chk-status"'):]
    assert "0 violations. 2 allowed once, 0 always allowed." in status
    assert ">OK</strong>" in status


def test_decisions_line_reads_correctly_at_zero(client):
    c, _lib = client
    html = c.get("/admin/checks").text
    assert _summary_details(html) == "0 violations. 0 allowed once, 0 always allowed."
    assert "Decisions: " in _section(html)
    assert ">0 allowed once</a>" in _section(html)
    assert ">0 always allowed</a>" in _section(html)
