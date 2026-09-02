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

import re
import sqlite3
from typing import Callable, Sequence

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from linklib.db import Library

_DEFAULT_SAMPLE_N = 5
_MAX_SAMPLE_N = 25
_DEFAULT_MAX_CELL_CHARS = 500

# Matches a leading "bearer" scheme, case-insensitive, with 0+ whitespace
# after it — deliberately tolerant of the scheme/token boundary going
# missing in transit (see bearer_token_candidates' docstring).
_BEARER_PREFIX_RE = re.compile(r"^bearer\s*", re.IGNORECASE)


def bearer_token_candidates(header: str) -> list[str]:
    """Extract candidate bearer tokens from a raw Authorization header
    value, tolerant of real-world format variance — case, a missing
    "Bearer " scheme entirely, and the scheme/token space going missing in
    transit.

    Shared by webapp.app's `_mcp_auth_gate` (transport-level fast reject)
    and this module's own `_caller_from_ctx` (per-tool re-verification) so
    the two can't drift into accepting different things.

    2026-09 production diagnostics (now removed — see CLAUDE.md's MCP
    section) traced a real connector-vs-curl auth mismatch to exactly this:
    one client sent a bare 43-char token with no scheme at all; another
    sent "Bearer"+token with the separating space lost in transit
    (`header_len=49, has_space=False`). Formatting ceremony isn't a real
    security boundary here — the token itself is the only secret, and it's
    hashed and compared server-side regardless of how it arrived — so this
    tries the trimmed header AS the token first (the bare-token case), and
    only if that fails to verify does it strip a leading "bearer" scheme
    and retry. A non-bearer scheme (e.g. "Basic ...") never matches the
    bearer-prefix strip, so it only ever produces the one, failing,
    candidate — rejected the same as before, just by the token failing to
    verify rather than an upfront scheme check.

    IMPORTANT: never log a `scheme` value split out of a header this way —
    a header with no space at all puts the ENTIRE header (i.e. the
    credential itself) into whatever a naive `partition(" ")` calls
    "scheme". This function deliberately returns only token candidates,
    never a parsed-out scheme, so there's nothing here a caller could
    accidentally log that would leak a credential.
    """
    trimmed = header.strip()
    if not trimmed:
        return []
    candidates = [trimmed]
    stripped = _BEARER_PREFIX_RE.sub("", trimmed).strip()
    if stripped and stripped != trimmed:
        candidates.append(stripped)
    return candidates


# DNS-rebinding-protection host allowlist, used ONLY when the caller doesn't
# pass its own (webapp.app always does — see build_mcp's docstring). Kept
# here too so this module stays independently correct/testable without
# webapp.app's constants.
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_DEFAULT_ALLOWED_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def _caller_from_ctx(ctx: Context, lib_factory: Callable[[], Library]) -> dict:
    """Resolve the caller from the live request this specific tool call was
    given. See the module docstring for why this never trusts anything
    cached from an earlier layer."""
    request = ctx.request_context.request
    if request is None:
        raise ToolError("unauthorized: no HTTP request context available for this call")
    auth_header = request.headers.get("authorization") or ""
    candidates = bearer_token_candidates(auth_header)
    if not candidates:
        raise ToolError("unauthorized: missing or malformed Authorization header")
    lib = lib_factory()
    try:
        caller = None
        for candidate in candidates:
            caller = lib.verify_api_token(candidate)
            if caller:
                break
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


def _shadow_of(name: str, virtual_names: set[str]) -> str | None:
    """Is `name` an extension-internal bookkeeping table for one of
    `virtual_names`? Derived from the virtual table names actually present
    in this schema — not a hardcoded suffix list — so it labels correctly
    whatever FTS5/sqlite-vec (or a future virtual-table extension) actually
    creates, rather than needing its own maintenance the next time a
    virtual table is added. FTS5 always creates exactly
    `{name}_data`/`_idx`/`_docsize`/`_config`; sqlite-vec's vec0 creates
    `{name}_rowids`/`_chunks`/`_vector_chunksNN`/`_info` — both families
    are, in every case, real SQL tables whose name starts with
    `{virtual_table_name}_`, which is the one property this checks. A false
    positive would need an unrelated real table deliberately named to start
    with e.g. `articles_fts_...` — nothing in this schema does, and nothing
    ever should, since that naming convention is exactly what marks a table
    as extension-owned."""
    for v in virtual_names:
        if name.startswith(v + "_"):
            return v
    return None


def build_mcp(
    lib_factory: Callable[[], Library],
    extra_allowed_hosts: Sequence[str] = (),
    extra_allowed_origins: Sequence[str] = (),
) -> FastMCP:
    """Build the FastMCP instance with its three tools registered. Called
    once from webapp.app at import time, after `_lib` exists.

    `extra_allowed_hosts`/`extra_allowed_origins` — production incident,
    2026-09: the very first live authenticated call returned a bare-text
    `421 Invalid Host header` from BEFORE our own auth gate even mattered
    (an unauthenticated request still got our JSON 401; /health was fine)
    — traced to `FastMCP.__init__` itself, not anything in this codebase:
    with no `transport_security=` passed and the default `host="127.0.0.1"`,
    it auto-enables DNS-rebinding protection with an allowlist of ONLY
    `127.0.0.1`/`localhost`/`::1` (confirmed by reading `FastMCP.__init__`
    directly, not guessed) — exactly why local end-to-end testing against
    `127.0.0.1` never caught this: the allowlist that broke production is
    the same one local testing was implicitly running inside. Fixed by
    always passing an explicit `TransportSecuritySettings` — protection
    stays ON (never disabled wholesale), just with `mcp.bmweis.com` and the
    raw Railway origin added to the Host/Origin allowlist alongside the
    same localhost entries FastMCP would have auto-added, so local dev and
    `tests/test_mcp_server.py`'s real-server tests keep working unchanged.
    webapp.app passes the concrete production hostnames; any other caller
    (a test, a future embedder) gets localhost-only, matching FastMCP's own
    default posture for an unspecified deployment target.
    """
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*_DEFAULT_ALLOWED_HOSTS, *extra_allowed_hosts],
        allowed_origins=[*_DEFAULT_ALLOWED_ORIGINS, *extra_allowed_origins],
    )

    # streamable_http_path stays the library default ("/mcp") deliberately —
    # see webapp.app's mount-order comment for why: mounting this sub-app at
    # an EMPTY prefix, registered after every other route, means a request
    # to exactly "/mcp" (no trailing slash, no "/mcp/mcp") reaches this
    # sub-app's own "/mcp" route directly with no redirect. Overriding this
    # to "/" and mounting at "/mcp" instead was tried first and produces a
    # 307 redirect to "/mcp/" on every call (confirmed locally against a
    # running server) — fragile for a client that doesn't follow redirects
    # on POST, so it's not what's used here.
    mcp = FastMCP("cfo-navigator", transport_security=transport_security)

    @mcp.tool()
    async def list_tables(ctx: Context) -> list[dict]:
        """List every table/view in the live database with row counts.
        Virtual tables (FTS5 — articles_fts; sqlite-vec — articles_vec) are
        labeled `"virtual": true`. Their extension-internal bookkeeping
        tables (FTS5's `articles_fts_data`/`_idx`/`_docsize`/`_config`;
        sqlite-vec's `articles_vec_rowids`/`_chunks`/`_vector_chunks00`/
        `_info`) are labeled `"shadow_of": "<virtual table name>"` — they're
        real SQL tables with their own row counts, but that count reflects
        the extension's internal storage layout, not independent content,
        so don't read it as "N more rows of real data". `row_count` is null
        (with `row_count_error` explaining why) when it can't be determined
        — e.g. sqlite-vec's extension isn't loaded in this environment.
        Admin-role only."""
        _require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            rows = _table_rows(lib)
            virtual_names = {r["name"] for r in rows if _is_virtual(r["sql"])}
            out = []
            for row in rows:
                name, kind, sql = row["name"], row["type"], row["sql"]
                entry: dict = {
                    "name": name,
                    "kind": kind,
                    "virtual": _is_virtual(sql),
                    "shadow_of": _shadow_of(name, virtual_names),
                }
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
                           from_end: bool = False,
                           max_cell_chars: int = _DEFAULT_MAX_CELL_CHARS) -> dict:
        """Return up to n rows from a table or view (default 5, hard cap
        25). Strictly read-only — SELECT ... LIMIT only. `name` is
        validated against the live table/view list before it's ever used
        in a query; no raw tool input is interpolated without that check.

        Each string cell is capped at `max_cell_chars` (default 500) with a
        visible `"...[truncated, showing X of Y chars]"` marker appended —
        a production sample of 25 `articles` rows came back at 523KB with
        no cap, most of it full article body text nobody asked to see.
        Pass `max_cell_chars<=0` to disable truncation and get full cell
        content. `truncated` on the response is true if any cell was cut.

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
            truncated = False
            if max_cell_chars > 0:
                for result_row in result_rows:
                    for key, value in result_row.items():
                        if isinstance(value, str) and len(value) > max_cell_chars:
                            omitted = len(value) - max_cell_chars
                            result_row[key] = (
                                value[:max_cell_chars]
                                + f"...[truncated, showing {max_cell_chars} of "
                                  f"{len(value)} chars, {omitted} omitted]"
                            )
                            truncated = True
            return {
                "name": name,
                "count": len(result_rows),
                "rows": result_rows,
                "truncated": truncated,
            }
        finally:
            lib.close()

    return mcp
