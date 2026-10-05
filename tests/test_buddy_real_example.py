"""The real-example snapshot on /tools/fpa-buddy, the source labels, and the taller box.

The example is a hardcoded snapshot of a real production conversation
(webapp/buddy_example.py), shown verbatim. These tests pin the text so an edit
shows up as a failing test, not a quiet change.
"""
import hashlib
import re

import pytest

from tests import test_buddy_phone_fixes as T
from webapp import buddy_example as ex

ORIGIN, _launch, Site = T.ORIGIN, T._launch, T.Site
buddy_html = T.buddy_html

# sha256 of the question and answer exactly as pasted on 2026-10-04.
QUESTION_SHA = "fc002429c68aa6950c644644fedd55ce117c54349e9f85334f54328b981169ef"
ANSWER_SHA = "91166367317aa5d3d2f2b376a007d2d5ed5ef845eb4066086246eadc340a3ed7"


def test_snapshot_text_is_unchanged():
    q = ex.QUESTION
    a = ex.ANSWER
    assert q.startswith("What is a framework other software and SaaS businesses use")
    assert q.endswith("how those frameworks have changed with AI.")
    assert len(a) == 4112
    assert a.startswith("The two most useful frameworks in the library here are a benchmark-and-ROI model")
    assert a.rstrip().endswith("which of these frameworks deserves the most weight.")
    assert hashlib.sha256(q.encode()).hexdigest() == QUESTION_SHA
    assert hashlib.sha256(a.encode()).hexdigest() == ANSWER_SHA


def test_snapshot_has_four_library_citations_with_urls():
    assert [c["n"] for c in ex.CITATIONS] == [1, 2, 3, 4]
    assert all(c["type"] == "library" for c in ex.CITATIONS)
    assert [c["url"] for c in ex.CITATIONS] == [
        "https://a16z.com/how-to-think-of-rd-spend/",
        "https://fsuite.co/blog/growth-engine-ratio",
        "https://www.tomtunguz.com/ai-implementation-guide/",
        "https://saastr.com/the-redpoint-ventures-playbook-how-top-vcs-are-really-investing-in-ai-applications-and-what-it-means-for-your-saas-strategy",
    ]


def test_page_shows_the_real_example(buddy_html):
    html, _ = buddy_html
    start = html.index('class="ask-example"')
    end = html.index('class="fpa-intro-area-question"')
    block = html[start:end]
    assert "A real question and answer, shown as asked." in block
    assert "Depth: Standard" in block and "Sources: Curated archive, Current feed" in block
    assert "Open web" not in block                      # the original ran with Open web off
    settings = re.search(r'ask-example-settings">([^<]*)<', block).group(1)
    assert "$" not in settings                           # no cost shown on the example (dollar amounts are admin-only)
    assert "What is a framework other software and SaaS businesses use to measure the effectiveness of R&amp;D investments?" in block
    assert "The two most useful frameworks in the library here" in block
    # all four citations link out in a new tab
    for c in ex.CITATIONS:
        assert re.search(r'<a href="%s" target="_blank" rel="noopener"' % re.escape(c["url"]), block)
    # the answer renders as blocks through the real renderer (rule, bold heading, [n] markers)
    assert "<hr" in block and "<strong>General R&amp;D effectiveness" in block


def test_expand_control_is_wired_and_text_stays_in_the_dom(buddy_html):
    html, _ = buddy_html
    assert 'id="ask-ex-toggle"' in html and 'aria-controls="ask-ex-answer"' in html
    assert 'aria-expanded="false"' in html and re.search(r'id="ask-ex-toggle"[^>]*\shidden', html)
    # the clip is CSS gated on a class only the script adds, so no script means full text
    assert ".ask-example.ask-ex-js:not(.open) .ask-answer{max-height:300px" in html
    # the last sentence of the answer is in the served HTML, not behind the button
    assert "which of these frameworks deserves the most weight." in html


def test_source_labels_renamed(buddy_html):
    html, _ = buddy_html
    for new in ("Curated archive", "Current feed", "Open web"):
        assert f">{new}<" in html or f"{new}</button>" in html or new in html
    for old in ("Saved archive", ">RSS feed<", "Trusted web"):
        assert old not in html


def test_question_box_is_five_rows(buddy_html):
    html, _ = buddy_html
    assert re.search(r'<textarea id="ask-q" rows="5"', html)
    # the follow-up textarea is a different rule and does not share it
    assert ".fu textarea{" in html


def test_expand_toggle_in_a_browser(buddy_html):
    html, _ = buddy_html
    pw = _launch()
    if pw is None:
        pytest.skip("no Chromium")
    p, b = pw
    try:
        pg = b.new_page(viewport={"width": 390, "height": 900})
        pg.set_content(html)
        h0 = pg.evaluate("document.getElementById('ask-ex-answer').getBoundingClientRect().height")
        assert 295 <= h0 <= 305
        pg.click("#ask-ex-toggle")
        assert pg.get_attribute("#ask-ex-toggle", "aria-expanded") == "true"
        assert pg.inner_text("#ask-ex-toggle") == "Show less"
        h1 = pg.evaluate("document.getElementById('ask-ex-answer').getBoundingClientRect().height")
        assert h1 > 1500
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        b.close(); p.stop()
