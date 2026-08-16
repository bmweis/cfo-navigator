"""Library.search() / FTS5 query safety — Reader QA pass fix.

Found live during the Reader Build arc's full QA pass: Library.search()
passed the raw, unescaped user query straight into `articles_fts MATCH ?`.
SQLite FTS5 has its own query mini-language, and plain English collides with
it constantly — a hyphen ("self-serve"), an apostrophe ("brian's"), an
unmatched quote, or a bareword that happens to be an FTS5 operator keyword
(AND/OR/NOT) all threw sqlite3.OperationalError, uncaught, turning an
ordinary search into an HTTP 500 instead of "no results." This hit both
call sites in the app: the Reader's own Saved search box (webapp/app.py's
reader_shell) and /api/search (also used by scripts/mcp_server.py for
Claude Desktop/Code search).

Fixed by trying the query exactly as given first, then falling back to the
whole thing wrapped as a single FTS5 phrase (quoted, with any literal double
quotes doubled) if that raises, then finally degrading to an empty result
list if even that somehow fails — never a 500.

**Trying the raw query first, not quote-wrapping unconditionally, matters
for a reason discovered while building this fix, before it shipped**:
Library.search() already has a second caller besides the two raw-user-text
ones above — linklib.agent.retrieve() (FP&A Buddy's library retrieval)
pre-sanitizes its own query via `_safe_fts_query()`, which tokenizes a
question into deliberately well-formed FTS5 syntax like
`"self" OR "serve" OR "churn"`. An earlier version of this fix
unconditionally wrapped every query in one more layer of phrase-quoting —
which turned that already-valid OR-query into a single literal string
search for the doubly-quoted text verbatim, matching nothing. That would
have silently broken FP&A Buddy's library retrieval for every question,
with no test failure to catch it (no existing test exercised
`Library.search()` with `_safe_fts_query()`'s actual output shape). Caught
by reasoning through every existing caller of the method being changed,
not just the two that motivated the fix — see
`test_safe_fts_query_output_is_not_double_wrapped` below for the regression
test.

Covers:
- Every crash-inducing query from the QA report now returns a plain list,
  never raises.
- A genuinely findable term (with or without special characters) still
  returns real results — the fix doesn't just swallow all queries into
  silence.
- FP&A Buddy's `_safe_fts_query()`-shaped OR queries still retrieve real
  matches — the regression caught before shipping.
- The empty-query "recent articles" path is untouched.
- The Reader's Saved search route (`GET /read?view=saved&q=...`) no longer
  500s for any of these — the actual live symptom from the QA report.
- /api/search doesn't 500 either (the MCP server's call path).
- The specific round trip the QA pass found blocked: a hyphenated tag added
  via the Reader is immediately findable through /api/search.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib.agent import _safe_fts_query

# Every query shape confirmed to crash Library.search() before the fix,
# reproduced directly against a bare FTS5 table during the QA
# investigation (independent of this app's schema) — hyphens, an
# apostrophe, an unmatched quote, and FTS5's own reserved operator words.
CRASHING_QUERIES = [
    "qa-renamed-tag",
    "hello-world",
    "self-serve",
    "e-commerce",
    "well-being",
    "brian's",
    '"unterminated',
    "AND",
    "OR",
    "NOT",
    "2026-08-16",
    "title: foo",
]


def _seed(lib, url="https://example.com/piece", title="Piece", content="Original plain text content.",
          tags=None):
    art = Article(url=url, title=title, content=content, tags=tags or [],
                  saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


@pytest.mark.parametrize("q", CRASHING_QUERIES)
def test_search_never_raises_on_previously_crashing_queries(lib, q):
    _seed(lib)
    # Must not raise — the whole point of the fix.
    result = lib.search(q, limit=10)
    assert isinstance(result, list)


def test_search_still_finds_real_matches_with_hyphenated_term(lib):
    _seed(lib, url="https://example.com/self-serve-post", title="A Self-Serve Onboarding Guide",
          content="This piece covers self-serve onboarding in detail.")
    results = lib.search("self-serve", limit=10)
    assert any(r["url"] == "https://example.com/self-serve-post" for r in results)


def test_search_still_finds_real_matches_with_apostrophe(lib):
    _seed(lib, url="https://example.com/brians-notes", title="Brian's Notes",
          content="Some notes belonging to Brian.")
    results = lib.search("brian's", limit=10)
    assert any(r["url"] == "https://example.com/brians-notes" for r in results)


def test_search_still_finds_real_matches_with_plain_word(lib):
    _seed(lib, url="https://example.com/plain", title="Plain", content="An ordinary sentence about finance.")
    results = lib.search("finance", limit=10)
    assert any(r["url"] == "https://example.com/plain" for r in results)


def test_search_finds_hyphenated_tag(lib):
    """The specific case the QA pass's admin round-trip check was blocked
    on: a hyphenated tag must be findable via search once added."""
    aid = _seed(lib, url="https://example.com/tagged", title="Tagged Article",
                content="Body text.", tags=["qa-renamed-tag"])
    results = lib.search("qa-renamed-tag", limit=10)
    assert any(r["id"] == aid for r in results)


def test_search_empty_query_returns_recent_articles_unaffected(lib):
    a1 = _seed(lib, url="https://example.com/one", title="One")
    results = lib.search("", limit=10)
    assert any(r["id"] == a1 for r in results)


def test_search_quoted_query_still_works(lib):
    """A user who already quotes their own query manually shouldn't have
    the quotes doubled into something broken."""
    _seed(lib, url="https://example.com/x", title="Exact Phrase Match Test",
          content="An exact phrase match test right here.")
    results = lib.search('"exact phrase match"', limit=10)
    assert isinstance(results, list)  # must not raise; matching semantics aren't the point here


def test_safe_fts_query_output_is_not_double_wrapped(lib):
    """Regression test for the exact bug caught before this fix shipped:
    linklib.agent.retrieve()'s _safe_fts_query() already produces
    deliberately valid, well-formed FTS5 syntax (an OR-joined, individually
    quoted token list) — Library.search() must try that AS GIVEN, not wrap
    it in a second outer phrase-quote layer, or FP&A Buddy's library
    retrieval goes dark on every question with no error to surface it."""
    aid = _seed(lib, url="https://example.com/churn-deep-dive", title="Self-Serve Churn Deep Dive",
                content="An analysis of self-serve churn rate trends this quarter.")
    q = _safe_fts_query("What is our self-serve churn rate?")
    assert q == '"self" OR "serve" OR "churn" OR "rate"'  # sanity-check the shape being tested
    results = lib.search(q, limit=10)
    assert any(r["id"] == aid for r in results), \
        "FP&A Buddy's OR-query retrieval must still find real matches, not just avoid crashing"


# ---------------------------------------------------------------------------
# Live route-level checks — the Reader's Saved search box and /api/search
# ---------------------------------------------------------------------------

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


@pytest.mark.parametrize("q", ["self-serve", "brian's", "AND", "2026-08-16"])
def test_reader_saved_search_no_longer_500s(env, q):
    lib = env._lib()
    _seed(lib, url="https://example.com/route-test", title="Route Test Article")
    lib.close()

    c = _admin_client(env)
    r = c.get("/read", params={"view": "saved", "q": q})
    assert r.status_code == 200
    assert "rr-list-pane" in r.text


@pytest.mark.parametrize("q", ["self-serve", "brian's", "AND"])
def test_api_search_no_longer_500s(env, q):
    lib = env._lib()
    _seed(lib, url="https://example.com/api-route-test", title="API Route Test")
    lib.close()

    c = _admin_client(env)
    r = c.get("/api/search", params={"q": q})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("results"), list)


def test_reader_added_hyphenated_tag_findable_via_api_search(env):
    """End-to-end regression for the exact round trip the QA pass's admin
    check was blocked on: add a hyphenated tag through the same DB method
    the Reader's inline tag editor uses, then confirm /api/search finds it
    without a 500."""
    lib = env._lib()
    aid = _seed(lib, url="https://example.com/roundtrip", title="Round Trip Article")
    lib.update_tags(aid, ["qa-roundtrip-tag"])
    lib.close()

    c = _admin_client(env)
    r = c.get("/api/search", params={"q": "qa-roundtrip-tag"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert any(row["id"] == aid for row in results)
