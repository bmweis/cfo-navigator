"""A failed Matchmaker turn is never answer text (#703), same rule as the
Buddy (#700): the asker gets a plain message, the raw error goes to the log,
and nothing is recorded. These tests use the real `_answer` with a raising
client, so they fail against the old code, which returned "(Answer call
failed: <raw error>)" as the answer and saved it as a turn."""
import logging
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from linklib.db import Library

RAW = "upstream 529 overloaded"
PLAIN = "Couldn't answer that just now. Try again in a moment."


def _boom_client():
    raise RuntimeError(RAW)


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr("linklib.matchmaker._get_client", _boom_client)
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.close()
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _rows(db):
    lib = Library(db)
    try:
        return lib.conn.execute("SELECT COUNT(*) FROM matchmaker_questions").fetchone()[0]
    finally:
        lib.close()


@pytest.mark.parametrize("path", ["/tools/software/find/chat", "/tools/communities/find/chat"])
def test_web_failed_turn_is_a_plain_502_and_not_recorded(env, path, caplog):
    appmod, db = env
    c = TestClient(appmod.app)
    with caplog.at_level(logging.ERROR, logger="linklib.matchmaker"):
        r = c.post(path, json={"question": "close automation software"})
    assert r.status_code == 502
    assert r.json() == {"detail": PLAIN, "failed": True}
    for leak in (RAW, "upstream", "Answer call failed", "Traceback"):
        assert leak not in r.text
    assert RAW in caplog.text  # the raw error is in the server log
    assert _rows(db) == 0


def test_missing_key_is_a_failure_not_answer_text(env, monkeypatch):
    appmod, db = env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = TestClient(appmod.app).post("/tools/software/find/chat", json={"question": "q"})
    assert r.status_code == 502
    assert "ANTHROPIC_API_KEY" not in r.text
    assert _rows(db) == 0


def test_voice_prompt_unset_is_a_failure_not_answer_text(env):
    appmod, db = env
    lib = Library(db)
    lib.set_setting("voice_matchmaker", "")
    lib.close()
    r = TestClient(appmod.app).post("/tools/communities/find/chat", json={"question": "q"})
    assert r.status_code == 502
    assert "Voice prompt" not in r.text and "voice_matchmaker" not in r.text
    assert _rows(db) == 0
