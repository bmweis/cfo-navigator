"""FP&A Buddy page-text trim (rows 1, 2, 4, 5 and 6 of the page-text review).

The caption under the illustrative example and the "seamlessly ... Claude/MCP"
line are gone; the teaser link reads "How it works"; a failed ask shows a plain
line instead of the raw exception; the limit link says "Start a new
conversation". The HTML-level tests run everywhere; the browser test skips where
no Chromium exists (CI).
"""
import re

import pytest

from tests import test_buddy_phone_fixes as T

ORIGIN, _launch, Site = T.ORIGIN, T._launch, T.Site
buddy_html = T.buddy_html   # a signed-in non-admin reader


def test_example_caption_is_gone_but_the_example_and_its_label_stay(buddy_html):
    html, _ = buddy_html
    assert "A mocked example" not in html and "no real question history" not in html
    assert "ask-example-caption" not in html
    assert "Illustrative example" in html and 'class="ask-example"' in html


def test_the_seamlessly_line_is_gone(buddy_html):
    html, _ = buddy_html
    assert "seamlessly" not in html and "Claude/MCP" not in html


def test_teaser_link_reads_how_it_works(buddy_html):
    html, _ = buddy_html
    assert re.search(r'<a href="/tools/fpa-buddy/how-it-works">How it works &rarr;</a>', html)
    assert "Curious how this works" not in html and "Read the full breakdown" not in html


def test_failed_ask_does_not_show_the_raw_exception_to_the_reader(buddy_html):
    html, _ = buddy_html
    assert "Something went wrong. Try again." in html
    assert "Something went wrong: " not in html
    assert "escapeHtml(String(e))" not in html           # the exception text never reaches the page
    assert "console.error('FP&A Buddy ask failed', e)" in html   # it stays in the console


def test_limit_link_says_start_a_new_conversation(buddy_html):
    html, _ = buddy_html
    assert "Start a new conversation</a>" in html
    assert "Start a new question" not in html


def test_browser_failed_ask_shows_a_plain_line_and_logs_the_raw_error(buddy_html):
    html, _ = buddy_html
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        site = Site(html)
        logged = []
        pg.on("console", lambda m: logged.append(m.text) if m.type == "error" else None)

        def handle(route):
            if route.request.url.endswith("/ask") and route.request.method == "POST":
                return route.abort()
            return site.handle(route)

        pg.route(re.compile(r"^http://buddy\.test/.*"), handle)
        pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
        pg.goto(ORIGIN + "/tools/fpa-buddy")
        pg.wait_for_selector("#ask-q")
        pg.fill("#ask-q", "A question that will fail")
        pg.click("#ask-btn")
        pg.wait_for_selector("#ask-thread .ask-answer span")
        text = pg.inner_text("#ask-thread .ask-answer").strip()
        assert text == "Something went wrong. Try again."
        assert "Failed to fetch" not in pg.inner_text("body") and "TypeError" not in pg.inner_text("body")
        assert any("FP&A Buddy ask failed" in m for m in logged)
    finally:
        browser.close()
        pw.stop()
