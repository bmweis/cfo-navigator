"""MCP server, Phase 1: read-only schema-introspection tools mounted at
`/mcp` inside the main FastAPI app (webapp/app.py).

This module builds the FastMCP instance and its three admin-gated
introspection tools (list_tables / describe_table / sample_rows). It does
NOT import anything from webapp.app — webapp.app imports this module and
hands it a `lib_factory` callable (webapp.app._lib) after that's defined,
which avoids a circular import and keeps this module trivially testable on
its own with any Library-producing callable (e.g. a temp-DB fixture).

Auth model (see CLAUDE.md's MCP section for the full write-up): every tool
call independently re-derives "who's asking" from the live HTTP request
FastMCP hands the tool via `ctx.request_context.request` — never from a
cached/global value, never from anything the transport-level auth gate in
webapp.app might have already decided. This is deliberate defense in depth:
webapp.app's `_mcp_auth_gate` middleware rejects a request with no/invalid
bearer token with a 401 before any MCP protocol handling even starts (per
the build brief's requirement), but each tool ALSO re-verifies for itself,
so a bug or omission in that middleware can never turn into a tool silently
trusting an unauthenticated or under-privileged caller. Fails closed in
every direction: a missing request, a missing/malformed header, an unknown
or revoked token, or (for the introspection tools) a non-admin role all
raise ToolError and refuse the call. There is no default identity and no
fallback to an "admin" role — a caller that can't be resolved is refused,
full stop.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from linklib.db import Library

_DEFAULT_SAMPLE_N = 5
_MAX_SAMPLE_N = 25


def _caller_from_ctx(ctx: Context, lib_factory: Callable[[], Library]) -> dict:
    """Resolve the caller from the live request this specific tool call was
    given. See the module docstring for why this never trusts anything
    cached from an earlier layer."""
    request = ctx.request_context.request
    if request is None:
        raise ToolError("unauthorized: no HTTP request context available for this call")
    auth_header = request.headers.get("authorization") or ""
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ToolError("unauthorized: missing or malformed Authorization header")
    lib = lib_factory()
    try:
        caller = lib.verify_api_token(token)
    finally:
        lib.close()
    if not caller:
        raise ToolError("unauthorized: invalid or revoked token")
    return caller


def _require_admin(ctx: Context, lib_factory: Callable[[], Library]) -> dict:
    caller = _caller_from_ctx(ctx, lib_factory)
    if caller.get("role") != "admin":
        raise ToolError("forbidden: this tool requires the admin role")
    return caller


def _table_rows(lib: Library) -> list[sqlite3.Row]:
    """Every real table/view in the live schema, straight from sqlite_master
    — the single source of truth this whole module validates tool-supplied
    table names against before any name is interpolated into SQL."""
    return lib.conn.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()


def _is_virtual(create_sql: str | None) -> bool:
    return bool(create_sql) and create_sql.strip().upper().startswith("CREATE VIRTUAL TABLE")


def build_mcp(lib_factory: Callable[[], Library]) -> FastMCP:
    """Build the FastMCP instance with its three tools registered. Called
    once from webapp.app at import time, after `_lib` exists."""

    # streamable_http_path stays the library default ("/mcp") deliberately —
    # see webapp.app's mount-order comment for why: mounting this sub-app at
    # an EMPTY prefix, registered after every other route, means a request
    # to exactly "/mcp" (no trailing slash, no "/mcp/mcp") reaches this
    # sub-app's own "/mcp" route directly with no redirect. Overriding this
    # to "/" and mounting at "/mcp" instead was tried first and produces a
    # 307 redirect to "/mcp/" on every call (confirmed locally against a
    # running server) — fragile for a client that doesn't follow redirects
    # on POST, so it's not what's used here.
    mcp = FastMCP("cfo-navigator")

    @mcp.tool()
    async def list_tables(ctx: Context) -> list[dict]:
        """List every table/view in the live database with row counts.
        Virtual tables (FTS5 — articles_fts; sqlite-vec — articles_vec) are
        labeled `"virtual": true`. `row_count` is null (with
        `row_count_error` explaining why) when it can't be determined —
        e.g. sqlite-vec's extension isn't loaded in this environment.
        Admin-role only."""
        _require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            out = []
            for row in _table_rows(lib):
                name, kind, sql = row["name"], row["type"], row["sql"]
                entry: dict = {"name": name, "kind": kind, "virtual": _is_virtual(sql)}
                if kind == "table":
                    try:
                        n = lib.conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                        entry["row_count"] = n
                    except sqlite3.Error as e:
                        entry["row_count"] = None
                        entry["row_count_error"] = str(e)
                else:
                    entry["row_count"] = None
                out.append(entry)
            return out
        finally:
            lib.close()

    @mcp.tool()
    async def describe_table(ctx: Context, name: str) -> dict:
        """Columns, types, defaults, and indexes for one table or view, via
        PRAGMA. Works on virtual tables (FTS5/sqlite-vec) too, reporting
        whatever PRAGMA can see rather than erroring outright. Only one
        declared SQL foreign key exists anywhere in this schema
        (feeds.section_id) — this reports PRAGMA foreign_key_list's raw
        output and does not build or imply a broader relationship graph.
        Admin-role only."""
        _require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            valid = {r["name"]: r for r in _table_rows(lib)}
            if name not in valid:
                raise ToolError(f"no such table or view: {name!r}")
            create_sql = valid[name]["sql"]
            result: dict = {
                "name": name,
                "kind": valid[name]["type"],
                "virtual": _is_virtual(create_sql),
                "create_sql": create_sql,
            }
            try:
                cols = lib.conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                result["columns"] = [dict(c) for c in cols]
            except sqlite3.Error as e:
                result["columns"] = []
                result["columns_error"] = str(e)
            try:
                idx_rows = lib.conn.execute(f'PRAGMA index_list("{name}")').fetchall()
                indexes = []
                for idx in idx_rows:
                    idx_d = dict(idx)
                    try:
                        idx_d["columns"] = [
                            dict(c) for c in
                            lib.conn.execute(f'PRAGMA index_info("{idx["name"]}")').fetchall()
                        ]
                    except sqlite3.Error:
                        idx_d["columns"] = []
                    indexes.append(idx_d)
                result["indexes"] = indexes
            except sqlite3.Error as e:
                result["indexes"] = []
                result["indexes_error"] = str(e)
            try:
                fks = lib.conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
                result["foreign_keys"] = [dict(f) for f in fks]
            except sqlite3.Error:
                result["foreign_keys"] = []
            return result
        finally:
            lib.close()

    @mcp.tool()
    async def sample_rows(ctx: Context, name: str, n: int = _DEFAULT_SAMPLE_N,
                           from_end: bool = False) -> dict:
        """Return up to n rows from a table or view (default 5, hard cap
        25). Strictly read-only — SELECT ... LIMIT only. `name` is
        validated against the live table/view list before it's ever used
        in a query; no raw tool input is interpolated without that check.
        Admin-role only — returning real row contents is this tool's whole
        purpose, and it must never be exposed below admin in any future
        role model."""
        _require_admin(ctx, lib_factory)
        n = max(1, min(int(n), _MAX_SAMPLE_N))
        lib = lib_factory()
        try:
            valid = {r["name"] for r in _table_rows(lib)}
            if name not in valid:
                raise ToolError(f"no such table or view: {name!r}")
            order = "DESC" if from_end else "ASC"
            try:
                rows = lib.conn.execute(
                    f'SELECT * FROM "{name}" ORDER BY rowid {order} LIMIT ?', (n,)
                ).fetchall()
            except sqlite3.Error:
                # No rowid (a WITHOUT ROWID table, or a view) — plain LIMIT,
                # order is whatever SQLite's default scan order gives us.
                rows = lib.conn.execute(f'SELECT * FROM "{name}" LIMIT ?', (n,)).fetchall()
            result_rows = [dict(r) for r in rows]
            if from_end:
                result_rows.reverse()
            return {"name": name, "count": len(result_rows), "rows": result_rows}
        finally:
            lib.close()

    return mcp
