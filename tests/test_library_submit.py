"""Public library-submission flow (webapp /library/submit).

Submissions land UN-ENRICHED in the Library Queue (no server fetch, no Claude),
deduped by URL, with a honeypot to drop bots.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c._db = db
    yield c
    if os.path.exists(db):
        os.remove(db)


def _queue(db):
    from linklib.db import Library
    lib = Library(db)
    try:
        return lib.list_queue(status="pending")
    finally:
        lib.close()


def test_submission_lands_unenriched_in_queue(client):
    r = client.post("/library/submit",
                    data={"url": "https://ex.com/great-post", "why": "Best NRR breakdown I've read",
                          "name": "Jane", "email": "jane@x.com"},
                    follow_redirects=False)
    assert r.status_code == 303 and "submitted=1" in r.headers["location"]
    q = _queue(client._db)
    assert len(q) == 1
    row = q[0]
    assert row["url"] == "https://ex.com/great-post"
    assert row["enriched"] == 0                      # option (c): no enrichment yet
    assert row["origin"] == "submission:Jane"
    assert "Jane" in row["summary"] and "NRR" in row["summary"]


def test_honeypot_drops_silently(client):
    r = client.post("/library/submit",
                    data={"url": "https://ex.com/spam", "website": "http://bot.example"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _queue(client._db) == []                  # nothing stored


def test_bad_url_rejected(client):
    r = client.post("/library/submit", data={"url": "not-a-url"}, follow_redirects=False)
    assert r.status_code == 400


def test_duplicate_url_confirms_without_leaking(client):
    client.post("/library/submit", data={"url": "https://ex.com/x"}, follow_redirects=False)
    # Second submission of the same URL still confirms (no info leak), no dup row.
    r = client.post("/library/submit", data={"url": "https://ex.com/x"}, follow_redirects=False)
    assert r.status_code == 303 and "submitted=1" in r.headers["location"]
    assert len(_queue(client._db)) == 1


def test_submit_page_renders(client):
    assert "Suggest a piece for the archive" in client.get("/library/submit").text
    assert "suggestion received" in client.get("/library/submit?submitted=1").text.lower()
