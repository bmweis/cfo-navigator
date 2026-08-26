"""Regression coverage for linklib.extract._extract_title's og:title/
twitter:title fallback (2026-08 bookmarklet title-extraction bug).

Reproduces the real failure mode: a site (e.g. Medium) serves real body
content and social meta tags in the initial HTML, but leaves <title> empty
until client-side JS hydrates document.title — a plain server-side fetch
never sees that update. Before the fix, _extract_title returned "" here,
and linklib.pipeline.ingest_url fell back to storing the raw URL as the
article's title even though a real title was available via og:title.
"""
from linklib.extract import _extract_title


def test_title_tag_used_when_present():
    html = "<html><head><title>Real Title</title></head><body></body></html>"
    assert _extract_title(html) == "Real Title"


def test_falls_back_to_og_title_when_title_tag_empty():
    html = (
        "<html><head><title></title>"
        '<meta property="og:title" content="What Founders Really Want From VCs">'
        "</head><body><p>Real article body text.</p></body></html>"
    )
    assert _extract_title(html) == "What Founders Really Want From VCs"


def test_falls_back_to_og_title_when_title_tag_missing():
    html = (
        "<html><head>"
        '<meta property="og:title" content="A Real Headline">'
        "</head><body><p>Body.</p></body></html>"
    )
    assert _extract_title(html) == "A Real Headline"


def test_falls_back_to_twitter_title_when_og_title_missing():
    html = (
        "<html><head>"
        '<meta name="twitter:title" content="Twitter Fallback Title">'
        "</head><body><p>Body.</p></body></html>"
    )
    assert _extract_title(html) == "Twitter Fallback Title"


def test_title_tag_wins_over_og_title_when_both_present():
    html = (
        "<html><head><title>Title Tag Wins</title>"
        '<meta property="og:title" content="OG Title Loses">'
        "</head><body></body></html>"
    )
    assert _extract_title(html) == "Title Tag Wins"


def test_returns_empty_when_nothing_found():
    html = "<html><head></head><body><p>No title anywhere.</p></body></html>"
    assert _extract_title(html) == ""


def test_whitespace_only_title_tag_falls_back():
    html = (
        "<html><head><title>   </title>"
        '<meta property="og:title" content="Real Title From Meta">'
        "</head><body></body></html>"
    )
    assert _extract_title(html) == "Real Title From Meta"


def test_malformed_html_does_not_raise():
    assert _extract_title("<html><title>Unclosed") in ("Unclosed", "")
