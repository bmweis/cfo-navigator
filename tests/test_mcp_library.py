"""Tests for MCP Phase 4: search_archive / get_article (Track A, the
Archive) and browse_feed / search_feed (Track B, the live RSS Feed).

Same real-running-server pattern tests/test_mcp_toolbox.py establishes (a
TestClient's ASGI shortcut doesn't run a real event loop the way FastMCP's
session manager needs) — a real uvicorn server, a real streamable-HTTP
client, real tokens minted against a temp DB.

The most important coverage here is the re-verified auth model: all four
tools require the admin role specifically (matching what /read actually
enforces today), NOT merely any signed-in user — a plain 'user'-role token
must be refused on every one of them.
"""
import asyncio
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


def _mint(lib: Library, username: str, role: str, active: int = 1):
    lib.conn.execute(
        "INSERT INTO users (username, role, active, created_at) VALUES (?, ?, ?, '')",
        (username, role, active),
    )
    lib.conn.commit()
    user = lib.get_user(username)
    _, token = lib.create_api_token(user["id"], label="test")
    return token


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)

    lib = Library(db)
    admin_token = _mint(lib, "admin_user", "admin")
    member_token = _mint(lib, "plain_user", "user")
    lib.close()

    import uvicorn
    port = _free_port()
    config = uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start in time")

    class Bundle:
        base_url = f"http://127.0.0.1:{port}"
        admin = admin_token
        member = member_token
        db_path = db

    try:
        yield Bundle
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if os.path.exists(db):
            os.remove(db)


def _call_tool(base_url: str, token: str, tool_name: str, args: dict | None = None):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def go():
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(f"{base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, args or {})

    return asyncio.run(go())


def _list_result(result):
    assert not result.isError, result.content[0].text if result.content else result
    return result.structuredContent["result"]


def _dict_result(result):
    assert not result.isError, result.content[0].text if result.content else result
    return json.loads(result.content[0].text)


def _error_text(result):
    assert result.isError
    return result.content[0].text if result.content else ""


def _seed_article(lib: Library, title: str, **overrides) -> dict:
    from linklib.db import Article
    url = overrides.pop("url", f"https://example.com/{title.lower().replace(' ', '-')}")
    content = overrides.pop("content", f"{title} — full article body text about FP&A topics.")
    article = Article(
        url=url, title=title, author=overrides.pop("author", ""),
        source=overrides.pop("source", "Example Source"),
        summary=overrides.pop("summary", f"{title} summary."),
        content=content, tags=overrides.pop("tags", ["FP&A"]),
    )
    article_id = lib.upsert(article)
    if overrides:
        sets = ", ".join(f"{k}=?" for k in overrides)
        lib.conn.execute(f"UPDATE articles SET {sets} WHERE id=?", (*overrides.values(), article_id))
        lib.conn.commit()
    return lib.get_article(article_id)


# ---------------------------------------------------------------------------
# search_archive
# ---------------------------------------------------------------------------

def test_search_archive_matches_query(live_server):
    lib = Library(live_server.db_path)
    _seed_article(lib, "Runway Modeling Guide", content="A deep guide to runway modeling and cash forecasting.")
    _seed_article(lib, "Hiring Playbook", content="A playbook about hiring finance leaders.")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_archive",
                                      {"query": "runway"}))
    titles = [r["title"] for r in result]
    assert "Runway Modeling Guide" in titles
    assert "Hiring Playbook" not in titles


def test_search_archive_empty_query_returns_recent(live_server):
    lib = Library(live_server.db_path)
    _seed_article(lib, "Older Article")
    _seed_article(lib, "Newer Article")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_archive", {}))
    assert len(result) >= 2
    for hit in result:
        assert set(hit.keys()) >= {
            "id", "url", "title", "author", "source", "tags",
            "published_at", "saved_at", "is_own_content", "excerpt",
        }


def test_search_archive_reflects_is_own_content(live_server):
    lib = Library(live_server.db_path)
    own = _seed_article(lib, "My Own Writing", content="Original writing about strategic finance.",
                         is_own_content=1)
    external = _seed_article(lib, "Saved External Piece", content="Some external article content.")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_archive", {"query": "writing"}))
    hit = next(r for r in result if r["id"] == own["id"])
    assert hit["is_own_content"] is True

    result2 = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_archive", {"query": "external"}))
    hit2 = next(r for r in result2 if r["id"] == external["id"])
    assert hit2["is_own_content"] is False


def test_search_archive_excerpt_is_truncated(live_server):
    lib = Library(live_server.db_path)
    long_body = "word " * 200
    _seed_article(lib, "Long Article", content=long_body)
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_archive",
                                      {"query": "long article"}))
    hit = next(r for r in result if r["title"] == "Long Article")
    assert len(hit["excerpt"]) <= 301
    assert "content" not in hit  # full text is get_article's job, not search's


def test_search_archive_requires_admin_role(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_archive", {"query": "runway"})
    assert "admin" in _error_text(result).lower()


def test_search_archive_rejects_unauthenticated(live_server):
    # A bad/unknown token is rejected at the transport level (webapp.app's
    # `_mcp_auth_gate`, before any MCP protocol handling starts) with a bare
    # HTTP 401 — not a clean MCP tool-call error, so this checks the raw
    # HTTP response directly rather than routing through the MCP client
    # session (which raises on the non-2xx before a tool result exists),
    # same convention tests/test_mcp_server.py's own transport-level tests use.
    import httpx
    r = httpx.post(f"{live_server.base_url}/mcp",
                    headers={"authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# get_article
# ---------------------------------------------------------------------------

def test_get_article_by_id_returns_full_content(live_server):
    lib = Library(live_server.db_path)
    article = _seed_article(lib, "Full Detail Article", content="The complete body text goes here.")
    lib.close()

    result = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_article",
                                      {"id_or_url": str(article["id"])}))
    assert result["title"] == "Full Detail Article"
    assert result["content"] == "The complete body text goes here."
    assert "content_html" not in result


def test_get_article_by_http_www_variant(live_server):
    lib = Library(live_server.db_path)
    article = _seed_article(lib, "Variant Lookup Article")
    lib.close()
    variant = article["url"].replace("https://", "http://www.")
    result = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_article",
                                      {"id_or_url": variant}))
    assert result["id"] == article["id"]


def test_get_article_by_url(live_server):
    lib = Library(live_server.db_path)
    article = _seed_article(lib, "URL Lookup Article")
    lib.close()

    result = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_article",
                                      {"id_or_url": article["url"]}))
    assert result["id"] == article["id"]


def test_get_article_unknown_id_raises(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "get_article", {"id_or_url": "999999"})
    assert "no such article" in _error_text(result).lower()


def test_get_article_requires_admin_role(live_server):
    lib = Library(live_server.db_path)
    article = _seed_article(lib, "Gated Article")
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "get_article",
                         {"id_or_url": str(article["id"])})
    assert "admin" in _error_text(result).lower()


# ---------------------------------------------------------------------------
# browse_feed / search_feed
# ---------------------------------------------------------------------------

_FAKE_FEED_ITEMS = [
    {"title": "Runway Extends Series C", "url": "https://a.example.com/1", "source": "TechCrunch",
     "category": "News", "html_url": "https://a.example.com", "feed_url": "https://a.example.com/feed",
     "published_at": "2026-09-01T00:00:00+00:00", "summary": "Runway raised a Series C round.",
     "paywalled": False},
    {"title": "Hiring in a Downturn", "url": "https://b.example.com/2", "source": "Stratechery",
     "category": "Blogs", "html_url": "https://b.example.com", "feed_url": "https://b.example.com/feed",
     "published_at": "2026-08-30T00:00:00+00:00", "summary": "Thoughts on hiring discipline.",
     "paywalled": True},
]


def _fake_get_feed_items(opml_path, category="", max_total=100):
    items = [i for i in _FAKE_FEED_ITEMS if not category or i["category"] == category]
    return items[:max_total], sorted({i["category"] for i in _FAKE_FEED_ITEMS})


def test_browse_feed_returns_items(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "browse_feed", {}))
    titles = [r["title"] for r in result]
    assert "Runway Extends Series C" in titles
    assert "Hiring in a Downturn" in titles
    hit = next(r for r in result if r["title"] == "Hiring in a Downturn")
    assert hit["paywalled"] is True


def test_browse_feed_filters_by_category(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "browse_feed",
                                      {"category": "News"}))
    assert len(result) == 1
    assert result[0]["title"] == "Runway Extends Series C"


def test_browse_feed_requires_admin_role(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)
    result = _call_tool(live_server.base_url, live_server.member, "browse_feed", {})
    assert "admin" in _error_text(result).lower()


def test_search_feed_ranks_by_keyword_overlap(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)

    result = _list_result(_call_tool(live_server.base_url, live_server.admin, "search_feed",
                                      {"query": "hiring downturn"}))
    assert result
    assert result[0]["title"] == "Hiring in a Downturn"


def test_search_feed_requires_nonempty_query(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)
    result = _call_tool(live_server.base_url, live_server.admin, "search_feed", {"query": ""})
    assert "non-empty query" in _error_text(result).lower()


def test_search_feed_requires_admin_role(live_server, monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", _fake_get_feed_items)
    result = _call_tool(live_server.base_url, live_server.member, "search_feed", {"query": "hiring"})
    assert "admin" in _error_text(result).lower()
