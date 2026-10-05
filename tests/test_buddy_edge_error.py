"""A 5xx from the edge (Cloudflare, Railway) arrives as an HTML page, not our JSON.

`doAsk` must still show the standard failure line and put the question back in
the box. Serves the real page over http through route handlers (no server, no
model); skips where no Chromium exists."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_buddy_phone_fixes import ORIGIN, Site, buddy_html, phone  # noqa: E402,F401

from webapp.ask_orchestrator import FAILED_TURN_MESSAGE

Q = "How do peers size FP&A teams?"


def _ask(pg, handler):
    pg.route(re.compile(r"^http://buddy\.test/ask$"), handler)
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer span[style*='alert']", timeout=8000)
    return pg.inner_text("#ask-thread .ask-answer"), pg.input_value("#ask-q")


def test_html_502_shows_failure_line_and_restores_question(phone):
    pg, _ = phone
    text, box = _ask(pg, lambda r: r.fulfill(
        status=502, content_type="text/html",
        body="<html><body><h1>502 Bad Gateway</h1>cloudflare</body></html>"))
    assert FAILED_TURN_MESSAGE in text
    assert "Bad Gateway" not in text
    assert box == Q


def test_network_error_restores_question(phone):
    pg, _ = phone
    text, box = _ask(pg, lambda r: r.abort())
    assert "Something went wrong. Try again." in text
    assert box == Q


def test_json_failure_still_restores_question(phone):
    pg, _ = phone
    text, box = _ask(pg, lambda r: r.fulfill(
        status=502, content_type="application/json",
        body='{"detail":"%s","failed":true,"usage":null}' % FAILED_TURN_MESSAGE))
    assert FAILED_TURN_MESSAGE in text
    assert box == Q
