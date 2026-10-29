"""Rendering the /ask response of a split benchmark turn (production turn 20 shape):
figures citations with an empty or null url, a notice, stop_reason max_tokens.
Invented text. Serves the real page through route handlers; skips without Chromium."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_buddy_phone_fixes import ORIGIN, Site, buddy_html, phone  # noqa: E402,F401

Q = "What does a company at 25 million in ARR spend on each function?"


def _payload(url):
    cites = [
        {"n": 1, "title": "Figures: Example Benchmarks 2099 (2099)", "url": url, "type": "library"},
        {"n": 2, "title": "Figures: Example Benchmarks 2098 (2098)", "url": url, "type": "library", "article_id": 5},
        {"n": 3, "title": "Example digest", "url": "https://example.test/d", "type": "library"},
    ]
    return {
        "answer": "| Metric | Value |\n|---|---|\n| S&M | 41% [1] |\n\nPer FTE rose [2][3]. It ends mid sen",
        "citations": cites, "sources": [], "feed_sources": [], "web_sources": [],
        "followups_left": 4, "conversation_id": "20", "turn_id": 20,
        "usage": {"spent": 1.5, "cap": 5.0}, "is_private": False,
        "notice": "This answer was cut off at the length limit. Ask a narrower question, or ask a follow-up to continue.",
    }


def _run(pg, body):
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route(re.compile(r"^http://buddy\.test/ask$"),
             lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body)))
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer", timeout=8000)
    pg.wait_for_timeout(500)
    return pg.inner_text("#ask-thread .ask-answer"), errors


def test_empty_url_figures_citations_render(phone):
    pg, _ = phone
    text, errors = _run(pg, _payload(""))
    assert "Something went wrong" not in text, errors
    assert "cut off at the length limit" in text
    assert "no link on file" in text


def test_null_url_figures_citations_render(phone):
    pg, _ = phone
    text, errors = _run(pg, _payload(None))
    assert "Something went wrong" not in text, errors
    assert "no link on file" in text


def test_marker_for_a_source_with_no_link_is_plain_text(phone):
    pg, _ = phone
    pg.route(re.compile(r"^http://buddy\.test/ask$"),
             lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(_payload(""))))
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer sup.cite", timeout=8000)
    # [1] and [2] have no link; [3] does. Before: all three were anchors, two with href="".
    anchors = pg.eval_on_selector_all("#ask-thread .ask-answer sup.cite a", "els => els.map(e => e.getAttribute('href'))")
    assert anchors == ["https://example.test/d"]


def test_network_failure_logs_stage_and_offers_recovery_note(phone):
    pg, _ = phone
    beacons = []
    pg.route(re.compile(r"^http://buddy\.test/ask/client-error$"),
             lambda r: (beacons.append(r.request.post_data), r.fulfill(status=204)))
    pg.route(re.compile(r"^http://buddy\.test/ask$"), lambda r: r.abort())
    # The page now polls /ask/result/<id> before giving up; here the server has no such turn.
    pg.add_init_script("window.__askRecover={pollMs:60,graceMs:300,maxMs:3000}")
    pg.route(re.compile(r"^http://buddy\.test/ask/result/.*"), lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"status":"unknown"}'))
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer span[style*='alert']", timeout=8000)
    pg.wait_for_timeout(500)
    text = pg.inner_text("#ask-thread .ask-answer")
    assert "Something went wrong. Try again." in text
    assert "Check Recent conversations" in text
    assert beacons and '"stage":"fetch"' in beacons[0]
