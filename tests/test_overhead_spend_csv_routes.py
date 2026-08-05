"""Route-level test for the /admin/overhead-spend CSV upload flow: preview
never writes to the DB, confirm inserts exactly the previewed rows, and a
mixed valid/invalid file reports skips without failing the whole import.

See tests/test_overhead_csv.py for the parsing logic itself, and
tests/test_manual_overhead.py for the underlying Library validation.
"""
import io
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c._db = db
    yield c
    if os.path.exists(db):
        os.remove(db)


def _entries(db):
    from linklib.db import Library
    lib = Library(db)
    try:
        return lib.list_manual_overhead()
    finally:
        lib.close()


def _upload(client, text):
    return client.post(
        "/admin/overhead-spend/csv/preview",
        files={"file": ("charges.csv", io.BytesIO(text.encode()), "text/csv")},
        follow_redirects=False,
    )


def test_preview_does_not_write_to_db(client):
    r = _upload(client, "vendor,date,amount\nRailway,2026-07-01,5.00\n")
    assert r.status_code == 200
    assert "Railway" in r.text
    assert _entries(client._db) == []


def test_confirm_inserts_previewed_rows(client):
    r = _upload(client, "vendor,date,amount,category,note\nRailway,2026-07-01,5.00,Infrastructure,Hobby\n")
    assert "Confirm" in r.text

    # Simulate the browser resubmitting the preview page's hidden fields.
    r2 = client.post(
        "/admin/overhead-spend/csv/commit",
        data={
            "vendor": ["Railway"],
            "date": ["2026-07-01"],
            "amount": ["5.0"],
            "category": ["Infrastructure"],
            "note": ["Hobby"],
        },
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "msg=" in r2.headers["location"]
    rows = _entries(client._db)
    assert len(rows) == 1
    assert rows[0]["vendor"] == "Railway"
    assert rows[0]["amount"] == pytest.approx(5.00)


def test_malformed_row_reported_in_preview_not_silently_dropped(client):
    r = _upload(client, "vendor,date,amount\nRailway,2026-07-01,5.00\n,2026-07-02,10.00\n")
    assert r.status_code == 200
    assert "Ready to import (1)" in r.text
    assert "Skipped rows (1)" in r.text
    assert "Vendor is required" in r.text


def test_missing_required_column_redirects_with_error(client):
    r = client.post(
        "/admin/overhead-spend/csv/preview",
        files={"file": ("charges.csv", io.BytesIO(b"vendor,amount\nRailway,5.00\n"), "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert _entries(client._db) == []


def test_template_download_has_header_and_example_row(client):
    r = client.get("/admin/overhead-spend/csv/template")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "overhead-spend-template.csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "vendor,date,amount,category,note"
    assert len(lines) == 2  # header + one example row
    # The template itself must parse cleanly through the same parser used on upload.
    from linklib.overhead_csv import parse_overhead_csv
    valid, skipped = parse_overhead_csv(r.content)
    assert skipped == []
    assert len(valid) == 1


def test_commit_revalidates_and_reports_failures_without_aborting_batch(client):
    # A hand-crafted commit (bypassing preview) with one bad row mixed in —
    # the route must not trust the hidden fields blindly.
    r = client.post(
        "/admin/overhead-spend/csv/commit",
        data={
            "vendor": ["Railway", ""],
            "date": ["2026-07-01", "2026-07-02"],
            "amount": ["5.0", "10.0"],
            "category": ["", ""],
            "note": ["", ""],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Imported%201" in r.headers["location"]
    rows = _entries(client._db)
    assert len(rows) == 1
    assert rows[0]["vendor"] == "Railway"
