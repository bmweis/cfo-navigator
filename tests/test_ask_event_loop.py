"""A slow FP&A Buddy turn must not stall the rest of the site (issue #738, A1).

POST /ask is an `async def` route. Before the fix it called the blocking
run_ask() (retrieval, Exa, the Claude call, SQLite) directly, so the whole
event loop waited on one answer and every other request on the process,
/health included, queued behind it. The fix runs that work in the threadpool.

Both requests go through one TestClient (one event loop), from two threads:
a slow /ask, then /health shortly after it starts."""
import importlib
import os
import tempfile
import threading
import time

import pytest

from linklib.db import Library

SLOW_SECONDS = 1.5


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.create_user("asker", "supersecret", role="user")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)

    def slow_run_ask(*args, **kwargs):
        time.sleep(SLOW_SECONDS)
        return {"answer": "ok"}

    monkeypatch.setattr(appmod, "run_ask", slow_run_ask)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        assert c.post("/login", data={"username": "asker", "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        yield c
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def test_slow_ask_does_not_stall_health(client):
    ask_result = {}

    def do_ask():
        ask_result["r"] = client.post("/ask", json={"question": "How long is a typical close?"})

    t = threading.Thread(target=do_ask)
    t.start()
    time.sleep(0.3)                      # let /ask get into its slow part
    start = time.monotonic()
    health = client.get("/health")
    health_elapsed = time.monotonic() - start
    t.join(timeout=10)

    assert health.status_code == 200
    assert ask_result["r"].status_code == 200
    assert ask_result["r"].json() == {"answer": "ok"}
    # Blocked, /health would wait out the rest of the slow turn (about 1.2s).
    assert health_elapsed < 0.6, f"/health waited {health_elapsed:.2f}s behind a slow /ask"
