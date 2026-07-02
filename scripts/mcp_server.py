#!/usr/bin/env python3
"""MCP server wrapping the hosted CFO Library search — lets an MCP-aware chat
client (Claude Desktop, Claude Code) search Brian's saved-article archive
directly, without going through the /ask web UI.

Talks to the deployed app over HTTPS (GET /api/search), the same endpoint
the archive search box uses — not the local library.db file — so this works
identically whether pointed at localhost or the hosted Railway instance.

Configure via environment variables and register as a stdio MCP server:

    LINKLIB_PUBLIC_BASE   Base URL of the site (default: http://localhost:8000)
    LINKLIB_SAVE_TOKEN    Token accepted by /api/search (X-Save-Token / ?token=)

Claude Desktop / Claude Code config example:

    {
      "mcpServers": {
        "cfo-library": {
          "command": "python3",
          "args": ["-m", "scripts.mcp_server"],
          "cwd": "/path/to/cfo-navigator",
          "env": {
            "LINKLIB_PUBLIC_BASE": "https://bmweis.com",
            "LINKLIB_SAVE_TOKEN": "..."
          }
        }
      }
    }
"""
from __future__ import annotations

import os

import requests
from mcp.server.fastmcp import FastMCP

_BASE = os.environ.get("LINKLIB_PUBLIC_BASE", "http://localhost:8000").rstrip("/")
_TOKEN = os.environ.get("LINKLIB_SAVE_TOKEN", "")

mcp = FastMCP(
    "cfo-library",
    instructions=(
        "Search Brian Weisberg's saved-article archive (CFO Library) — FP&A, "
        "GTM efficiency, and finance-leadership content he's read and kept."
    ),
)

# Fields worth handing to a chat model. The API also returns internal
# library-management fields (enrich_model, in_scope, scope_reason, ids, full
# `content`/`notes`) that would just burn context here without helping the
# caller ground an answer or decide whether to open the link.
_KEEP_FIELDS = ("title", "url", "source", "author", "published_at", "summary", "tags")


@mcp.tool()
def search_library(query: str, limit: int = 10) -> list[dict]:
    """Full-text search Brian's saved-article archive (CFO Library).

    Args:
        query: Search terms — matches title, author, source, summary,
            content, notes, and tags.
        limit: Max results to return (default 10, capped at 50).
    """
    limit = max(1, min(limit, 50))
    resp = requests.get(
        f"{_BASE}/api/search",
        params={"q": query, "limit": limit, "token": _TOKEN},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [{k: r.get(k) for k in _KEEP_FIELDS} for r in results]


if __name__ == "__main__":
    mcp.run(transport="stdio")
