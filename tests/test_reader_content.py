"""Reader content-and-behavior fixes (post-launch pass on the Phase 5 merge):

- paywalled Feed items open in-app instead of hijacking to an external tab
- article extraction preserves paragraph structure (and, for a live fetch,
  real HTML: images/links, not flattened plain text)
- thousands-separator formatting on the Reader's item counts

See ARCHITECTURE.md's "Reader fixes/follow-ups" note (Phase 5 section) and
CLAUDE.md's matching "Reader follow-up pass" bullet for the full write-up.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


# ---------------------------------------------------------------------------
# 1. Paywalled Feed items open in-app (no more external <a target="_blank">
#    hijacking the click before rrOpen()/the reader pane's "Original ->"
#    link ever gets a chance to run).
# ---------------------------------------------------------------------------

def test_paywalled_feed_row_opens_in_app(env, monkeypatch):
    fake_items = [{
        "url": "https://stratechery.com/2026/some-piece/",
        "title": "A Paywalled Piece",
        "source": "Stratechery",
        "category": "Blogs",
        "published_at": "2026-08-14T00:00:00Z",
        "summary": "Behind a paywall.",
        "paywalled": True,
    }]

    def _fake_get_feed_items(opml_path, category="", max_total=120):
        return fake_items, ["Blogs"]

    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)

    c = _admin_client(env)
    r = c.get("/read?view=feed")
    assert r.status_code == 200
    html = r.text

    assert "Paywalled" in html, "paywall badge should still render"
    # The row must NOT be wrapped in a real link — that's exactly the bug:
    # a click on the row used to navigate straight to the external site.
    assert 'target="_blank" rel="noopener" style="text-decoration:none' not in html, \
        "paywalled row is still hijacking clicks to an external tab"
    assert "rr-row-disabled" not in html, "dead paywall-disabled class should be gone"
    # It must have the same click handler every other row gets.
    assert 'onclick="rrOpen(this)"' in html


# ---------------------------------------------------------------------------
# 2. Extraction preserves paragraph structure, and (for a live fetch) real
#    HTML — images, links — rather than one flattened blob.
# ---------------------------------------------------------------------------

def test_extract_content_bs4_fallback_preserves_paragraphs():
    from linklib.extract import _extract_content

    html = """<html><body>
    <article>
      <p>First paragraph.</p>
      <p>Second paragraph.</p>
      <p>Third paragraph.</p>
    </article>
    </body></html>"""
    text = _extract_content(html)
    # The old bug: soup.get_text(" ", strip=True) flattened everything into
    # one line with no \n\n markers, so a downstream .split("\n\n") saw a
    # single "paragraph" no matter how many <p> tags existed.
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    assert len(paragraphs) == 3, f"expected 3 paragraphs, got {len(paragraphs)}: {text!r}"
    assert paragraphs == ["First paragraph.", "Second paragraph.", "Third paragraph."]


def test_extract_reader_html_preserves_structure_and_sanitizes():
    from linklib.extract import extract_reader_html

    html = """<html><head><title>Should not appear</title></head>
    <body>
    <nav>Nav junk</nav>
    <article>
      <h1>Title</h1>
      <p>Paragraph with a <a href="/relative">link</a>.</p>
      <div class="ad"><script>evil()</script></div>
      <img src="/img/pic.jpg" alt="desc">
      <p></p>
    </article>
    <footer>Footer junk</footer>
    </body></html>"""
    out = extract_reader_html(html, "https://example.com/articles/foo")

    assert "Should not appear" not in out
    assert "Nav junk" not in out
    assert "Footer junk" not in out
    assert "evil()" not in out
    assert "<script" not in out
    assert 'href="https://example.com/relative"' in out
    assert 'target="_blank"' in out
    assert 'src="https://example.com/img/pic.jpg"' in out
    assert "<h1>Title</h1>" in out
    # The empty <p></p> should have been dropped, not rendered as dead space.
    assert "<p></p>" not in out


def test_resolve_reader_content_uses_structured_html_for_live_fetch(env, monkeypatch):
    import linklib.extract as extract_mod

    class FakePage:
        title = "Live-Fetched Title"
        content = "Flat plain text fallback."
        raw_html = "<html><body><article><p>Para one.</p><img src='/x.png'><p>Para two.</p></article></body></html>"

    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: FakePage())

    data = env._resolve_reader_content(url="https://example.com/unsaved-piece")
    assert data is not None
    assert data["has_content"] is True
    assert "<img" in data["body_html"], "should use the structured extraction, not the flat fallback"
    assert "Para one." in data["body_html"]
    assert "Para two." in data["body_html"]
    assert "Flat plain text fallback." not in data["body_html"]


def test_resolve_reader_content_falls_back_to_plain_text_when_structure_fails(env, monkeypatch):
    import linklib.extract as extract_mod

    class FakePage:
        title = "Some Title"
        content = "First paragraph.\n\nSecond paragraph."
        raw_html = ""  # nothing to structure-extract from

    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: FakePage())

    data = env._resolve_reader_content(url="https://example.com/unsaved-piece-2")
    assert data is not None
    assert data["body_html"] == "<p>First paragraph.</p><p>Second paragraph.</p>"


# ---------------------------------------------------------------------------
# 3. Thousands separators on the Reader's counts (inherited from the
#    pre-merge Archive page; an audit found no other sitewide gaps).
# ---------------------------------------------------------------------------

def test_reader_counts_use_thousands_separators(env):
    lib = env._lib()
    now = "2026-08-14T00:00:00"
    rows = [
        (f"https://example.com/bulk/{i}", f"Bulk article {i}", now, now)
        for i in range(1050)
    ]
    lib.conn.executemany(
        "INSERT INTO articles (url, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    lib.conn.commit()
    lib.close()

    c = _admin_client(env)
    r = c.get("/read?view=saved")
    assert r.status_code == 200
    html = r.text
    assert "1,050" in html, "saved count should be comma-formatted"
    assert "1050 saved" not in html
