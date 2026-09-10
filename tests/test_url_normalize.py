"""URL canonicalization for dedup (linklib.db.normalize_url + its use in
upsert).

A source can expose the same article under two URL variants (http/https,
www, trailing slash, ?utm=). These pin that such variants collapse to one
library row.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library, normalize_url


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.mark.parametrize("raw, expected", [
    ("https://ex.com/post", "https://ex.com/post"),
    ("http://ex.com/post", "https://ex.com/post"),              # http -> https
    ("https://www.ex.com/post", "https://ex.com/post"),         # drop www
    ("https://ex.com/post/", "https://ex.com/post"),            # trailing slash
    ("https://ex.com/post#section", "https://ex.com/post"),     # drop fragment
    ("https://ex.com/post?utm_source=twitter&utm_medium=social", "https://ex.com/post"),
    ("https://ex.com/post?id=5&utm_campaign=x", "https://ex.com/post?id=5"),  # keep real params
    ("https://EX.com:443/post", "https://ex.com/post"),         # default port + case
    ("https://ex.com/", "https://ex.com"),                      # bare root slash stripped
    ("https://ex.com", "https://ex.com"),                       # bare root, no slash
])
def test_normalize_variants(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_blank_and_unparseable():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""
    assert normalize_url("not a url") == "not a url"


def test_normalize_root_domain_slash_variants_converge():
    """Regression for a bug where the trailing-slash strip was gated behind
    `len(path) > 1`: urlsplit gives path == "/" for a root URL with a
    trailing slash and "" without one, so that guard (len("/") == 1) never
    fired and root-domain slash variants never converged — e.g.
    https://vendor.com vs https://vendor.com/ silently bypassed the PR #197
    duplicate-URL check. Deeper paths (/foo/ -> /foo) were never affected."""
    assert normalize_url("https://trovata.io") == normalize_url("https://trovata.io/")
    assert normalize_url("https://www.abacum.io") == normalize_url("https://www.abacum.io/")


def test_upsert_merges_url_variants(lib):
    """The same article via http+www+slash+utm collapses to one row, tags unioned."""
    id1 = lib.upsert(Article(url="http://www.ex.com/m-and-a/", tags=["m&a"]))
    id2 = lib.upsert(Article(url="https://ex.com/m-and-a?utm_source=rss", tags=["deals"]))
    assert id1 == id2                       # same canonical row
    rows = lib.search("")
    assert len(rows) == 1
    assert set(rows[0]["tags"]) == {"deals", "m&a"}
    assert rows[0]["url"] == "https://ex.com/m-and-a"
