"""Regression coverage for the Reader cleanliness investigation (2026-08):
sponsor/ad blocks and cookie-consent banners embedded mid-article (not just
page-level nav/header/footer chrome) were surviving into both stored Archive
content and live Reader views — confirmed live via a saved OnlyCFO newsletter
rendering a full Brex sponsor block inline. Root cause: neither
`_extract_content`'s BS4 fallback nor `extract_reader_html` had any
class/id-based detection — only tag-name-based junk stripping
(nav/header/footer/script/...), which a plain `<div class="sponsor-block">`
never matches. `strip_promotional_chrome` closes that gap for both paths.
"""
from linklib.extract import _extract_content, extract_reader_html, strip_promotional_chrome
from bs4 import BeautifulSoup

_ARTICLE_HTML = """
<html><body><article>
<p>Real intro paragraph about SPACs and IPOs.</p>
<div class="sponsor-block sponsor-block--wide">
  <p>This newsletter is brought to you by Brex. Brex is the all-in-one
  finance platform for scaling businesses.</p>
</div>
<p>Real second paragraph continuing the analysis.</p>
<div id="cookie-consent-banner">
  <p>We use cookies to improve your experience. Accept all cookies?</p>
</div>
<section class="newsletter-signup-cta">
  <p>Subscribe to get more posts like this in your inbox.</p>
</section>
<p>Real closing paragraph with the actual conclusion.</p>
</article></body></html>
"""


def test_extract_content_strips_sponsor_block():
    text = _extract_content(_ARTICLE_HTML)
    assert "Brex" not in text
    assert "Real intro paragraph" in text
    assert "Real second paragraph" in text
    assert "Real closing paragraph" in text


def test_extract_content_strips_cookie_banner_and_newsletter_cta():
    text = _extract_content(_ARTICLE_HTML)
    assert "cookies to improve" not in text
    assert "Subscribe to get more posts" not in text


def test_extract_reader_html_strips_sponsor_block():
    html = extract_reader_html(_ARTICLE_HTML, "https://example.com/post")
    assert "Brex" not in html
    assert "Real intro paragraph" in html
    assert "Real closing paragraph" in html


def test_extract_reader_html_strips_cookie_banner_and_newsletter_cta():
    html = extract_reader_html(_ARTICLE_HTML, "https://example.com/post")
    assert "cookies to improve" not in html
    assert "Subscribe to get more posts" not in html


def test_real_article_text_is_never_touched():
    """A real paragraph that happens to mention 'sponsor' as a plain word
    (not a class/id) must survive — only element class/id/data-testid is
    checked, never text content."""
    html = (
        "<html><body><article>"
        "<p>The company signed a new sponsor deal with a major airline this quarter.</p>"
        "</article></body></html>"
    )
    text = _extract_content(html)
    assert "sponsor deal" in text
    reader_html = extract_reader_html(html, "https://example.com/post")
    assert "sponsor deal" in reader_html


def test_strip_promotional_chrome_ignores_common_words_containing_ad():
    """'ad' alone must never match — would nuke 'advice', 'gadget', etc."""
    html = (
        '<div class="advice-column"><p>Some financial advice here.</p></div>'
        '<div class="gadget-review"><p>A gadget review.</p></div>'
    )
    soup = BeautifulSoup(html, "html.parser")
    strip_promotional_chrome(soup)
    assert "Some financial advice here." in str(soup)
    assert "A gadget review." in str(soup)


def test_strip_promotional_chrome_matches_mixed_case_and_separators():
    html = (
        '<div class="Sponsor-Block--wide"><p>Ad copy.</p></div>'
        '<div id="cookie_banner_2"><p>Cookie copy.</p></div>'
    )
    soup = BeautifulSoup(html, "html.parser")
    strip_promotional_chrome(soup)
    assert "Ad copy." not in str(soup)
    assert "Cookie copy." not in str(soup)
