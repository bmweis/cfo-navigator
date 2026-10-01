"""Footer "Source on GitHub" link (go-public chain, step 5).

A quiet trust signal for the public footer: the code is open (AGPL-3.0).
There is exactly one `<footer>` in the app (`_page()`), so every page that
goes through `_page()` carries it, admin pages included.
"""
import importlib
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_URL = "https://github.com/bmweis/cfo-navigator"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "feeds.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    yield appmod, TestClient(appmod.app)


def _footer(html: str) -> str:
    m = re.search(r"<footer class=\"site-footer\">.*?</footer>", html, re.S)
    assert m, "expected the shared site footer"
    return m.group(0)


@pytest.mark.parametrize("path", ["/", "/about", "/contact", "/tools"])
def test_public_footer_links_to_the_source_repo(env, path):
    _, client = env
    footer = _footer(client.get(path).text)
    anchor = re.search(r'<a [^>]*href="%s"[^>]*>Source on GitHub</a>' % re.escape(REPO_URL), footer)
    assert anchor, "footer must carry the Source on GitHub link"
    assert 'target="_blank"' in anchor.group(0)
    assert 'rel="noopener"' in anchor.group(0)


def test_repo_url_is_one_hardcoded_constant(env):
    appmod, _ = env
    assert appmod.SOURCE_REPO_URL == REPO_URL


def test_source_link_adds_no_inline_styling(env):
    """Quiet by design: it inherits the footer link style, no color of its own."""
    _, client = env
    footer = _footer(client.get("/").text)
    anchor = re.search(r'<a [^>]*href="%s"[^>]*>' % re.escape(REPO_URL), footer).group(0)
    assert "style=" not in anchor and "class=" not in anchor
