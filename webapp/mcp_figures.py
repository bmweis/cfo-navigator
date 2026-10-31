"""MCP server, Phase B (issue #738): benchmark-figure tools.

Four read-only tools registered onto the same FastMCP instance as the rest:
`find_figures`, `coverage`, `get_digest`, `metric_definition`. All wrap
`linklib.figures` unmodified. **Admin role only** (`require_admin`), like the
Library tools: `get_digest` returns the full stored text of a third-party
digest, which non-admins never browse directly. (FP&A Buddy shows a user the
figures and their citations; it does not hand over the digest.)

`linklib.compare.MCP_PARITY` is deliberately not extended: it enforces an
exact column-for-column registry for the four profile tables
(tools, communities, community_profiles, tool_feature_links), and the figures
tables are not profile tables. The guard for these tools is
`tests/test_mcp_figures.py` (a non-admin token is refused on every one).

Plain dict/list/str only, no markup, like every other /mcp tool. Nothing here
calls a model or writes to the database.
"""
from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from linklib import figures as fg
from linklib.db import Library

from webapp.mcp_server import require_admin


def register_figures_tools(mcp: FastMCP, lib_factory: Callable[[], Library]) -> None:

    @mcp.tool()
    async def find_figures(ctx: Context, metric: str, band: str = "", segment: str = "",
                           statistic: str = "", year: str = "", basis: str = "",
                           denominator: str = "", all_editions: bool = False,
                           limit: int = fg.DEFAULT_LIMIT, include_illustrative: bool = False) -> dict:
        """Stored benchmark figures for a metric, exactly as the reports print
        them. `metric` may be a canonical name, an alias or a raw report name.
        Optional narrowing: `band` (a printed band or a size such as "$25M"),
        `segment`, `statistic` (as printed, e.g. median, p75: p75 and
        top_quartile are never merged), `year`, `basis` (ARR or revenue) and
        `denominator`. Every row carries `match` (exact, adjacent_band,
        different_segment, different_statistic, different_basis,
        different_definition or related_metric) and `gaps`, the plain-words
        list of how it differs from the request. A size between printed bands
        returns the bands either side, flagged. Default is the newest edition
        per publisher; `all_editions=True` returns every edition, oldest first.
        `notes` says what was left out. For a function's spend (S&M, R&D, G&A, customer
        success, opex by function) the percent-of-revenue and percent-of-ARR forms come back
        together: each row has `basis_group` and `other_basis`, `groups` lists them, the asked
        basis is first, and nothing is converted or averaged. Rows from a worked example company
        ("illustrative", "example", "hypothetical", "sample company") are left out
        unless `include_illustrative=True`; the count is in `illustrative_left_out`. Each row has `link` (the report's
        landing URL) or `link_note` "no link on file". Admin role only."""
        require_admin(ctx, lib_factory)
        if not (metric or "").strip():
            raise ToolError("metric is required")
        lib = lib_factory()
        try:
            return fg.find_figures(lib, metric, band=band or None, segment=segment or None,
                                   statistic=statistic or None, year=year or None,
                                   basis=basis or None, denominator=denominator or None,
                                   all_editions=all_editions, limit=max(1, min(int(limit), 200)),
                                   include_illustrative=include_illustrative)
        finally:
            lib.close()

    @mcp.tool()
    async def coverage(ctx: Context, metric: str = "") -> dict:
        """What the stored figures contain: reports (publisher, year, figure
        count, link), and the bands, bases, segments, statistics and
        denominators present, optionally for one metric. Use it to learn what
        can be asked for before calling `find_figures`. Admin role only."""
        require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            return fg.coverage(lib, metric.strip() or None)
        finally:
            lib.close()

    @mcp.tool()
    async def get_digest(ctx: Context, id_or_slug: str) -> dict:
        """The FULL stored text of one benchmark digest, by article id or
        report slug. Not cut off the way FP&A Buddy's prompt cuts a source.
        Refuses when nothing matches or the article is not a digest.
        Admin role only."""
        require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            d = fg.get_digest(lib, id_or_slug)
        finally:
            lib.close()
        if d is None:
            raise ToolError(f"no such digest: {id_or_slug!r}")
        return d

    @mcp.tool()
    async def metric_definition(ctx: Context, name: str) -> dict:
        """The workbook metric card for a metric (canonical name or alias)
        plus its alias list. The workbook definition governs; other sources
        define a metric differently and the newer of those wins. An alias that
        belongs to more than one metric returns every card, `ambiguous`
        true. `card` is null when the registry knows the metric but no card
        article is stored. Admin role only."""
        require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            return fg.metric_definition(lib, name)
        finally:
            lib.close()
