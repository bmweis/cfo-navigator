"""The new-tool form shows the Sources list right after Generate (Chromium).

The Generate route sends back citations in a hidden field, and used to show
no Sources list until the tool was saved. Skips where no Chromium exists."""
import os
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from linklib.db import Library

CITES = [{"n": 1, "title": "cfo.ai", "url": "https://cfo.ai/", "type": "web"}]


def _chromium():
    pw = pytest.importorskip("playwright.sync_api")
    for path in ("/opt/pw-browsers/chromium", None):
        try:
            p = pw.sync_playwright().start()
            b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
            return p, b
        except Exception:
            try:
                p.stop()
            except Exception:
                pass
    pytest.skip("no Chromium available")


@pytest.fixture
def server(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    lib.close()
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from linklib import enrich
    draft = SimpleNamespace(description="Does X [1].", summary="Short [1].", low_confidence=False,
                            confident=True, citations=CITES, model="claude-opus-5-5",
                            input_tokens=1, output_tokens=1, cost_usd=0.0, exa_cost_usd=0.0)
    monkeypatch.setattr(enrich, "generate_tool_description", lambda *a, **k: draft)
    import uvicorn
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def test_sources_list_appears_after_generate_and_clears_on_hand_edit(server):
    p, b = _chromium()
    try:
        pg = b.new_page()
        pg.goto(f"{server}/login")
        pg.fill("input[name=username]", "boss")
        pg.fill("input[name=password]", "supersecret")
        pg.click("button[type=submit]")
        pg.goto(f"{server}/admin/tools/software/new")
        pg.fill("#tool-name", "cfo.ai")
        pg.fill("#tool-url", "https://cfo.ai/")
        assert pg.locator("#description-sources").inner_text().strip() == ""
        pg.click("text=Generate summary")
        pg.wait_for_function("document.getElementById('tool-gen-status').textContent.includes('Drafted')")
        box = pg.locator("#description-sources")
        assert "sources" in box.inner_text().lower() and "cfo.ai" in box.inner_text()
        # The hidden field Save reads is unchanged: same citations, same shape.
        assert "https://cfo.ai/" in pg.input_value("#ai-drafted-citations")
        # A hand-edit clears the citations Save would send, so the list goes too.
        pg.fill("#tool-desc", "Hand written.")
        assert pg.input_value("#ai-drafted-citations") == ""
        assert pg.locator("#description-sources").inner_text().strip() == ""
    finally:
        b.close()
        p.stop()
