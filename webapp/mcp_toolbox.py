"""MCP server, Phase 3: Toolbox & Communities content tools.

Eight read-only tools — `search_software`, `get_software`, `search_communities`,
`get_community`, `compare_software`, `compare_communities`, `search_benchmarking`,
`search_books` — registered onto the same FastMCP instance
`webapp/mcp_server.py` builds (mounted at `/mcp` inside webapp/app.py).
Kept in a separate module from `mcp_server.py` on purpose: that module's
own scope is tightly focused on the three admin-gated schema-introspection
tools plus the auth/host-security plumbing every /mcp tool shares; this
module is pure Toolbox/Communities domain content, reusing that plumbing
rather than duplicating it.

`search_benchmarking`/`search_books` (PR 8, 2026-09) close a real coverage
gap found when the site was about to claim the whole Toolbox works over
MCP: of the Toolbox's five components (software, communities, FP&A Buddy,
benchmarking resources, book recommendations), the first three were
already reachable; benchmarking resources and book recommendations had no
tool at all. Both read the same `benchmarks` table (a `section` column
discriminates `'benchmarking'`/`'books'` — confirmed in Phase 0 that this
is one table with a type discriminator, not two, so the two tools share
`list_benchmarks(section=...)` rather than each owning a query). No
`compare_benchmarking` — considered and declined: a benchmarking/book row
carries none of the structured comparable fields that make
`compare_software`/`compare_communities` useful, there's no Compare page
for Resources on the site to mirror, and a text search over the backing
rows covers the real need.

`search_tools`/`get_tool`/`compare_tools` were renamed to
`search_software`/`get_software`/`compare_software` in the same PR, for
consistency with `search_communities`/`get_community`/`compare_communities`
— "tools" was ambiguous once the Toolbox grew to five components. Old
names are gone, not aliased.

Auth model — the one deliberate departure from mcp_server.py's three
tools: these eight require a **valid token, any role**, not admin-only. The
underlying web pages (`/tools/software`, `/tools/communities`, their
profile pages, and both Compare pages) are fully public, so these tools
mirror that — `webapp.mcp_server.require_caller` resolves and re-verifies
the caller exactly like `_require_admin` does (same fail-closed
`_caller_from_ctx`, same "no request context / no header / unknown or
revoked token" -> `ToolError`), it just never rejects on role. The
caller's role changes only what's visible *within* a result: every
review-state decision goes through `linklib.gates` exactly as the HTML
routes do (`gates.field_state`, `gates.badge_text`, `gates.EMPTY_COPY`,
`gates.COMPARE_EMPTY_LABELS`) — no parallel gating logic anywhere in this
module. `authed = caller.get("role") == "admin"` is the one place that
mapping happens, mirroring `webapp.app._is_authed`'s own "authed means
admin" convention exactly.

Two different single-vs-compare content strategies, both real, deliberate
choices (see CLAUDE.md's MCP Phase 3 pointer bullet / the Step 0 report
for the full reasoning):

- `get_software`/`get_community` build their own lightweight dicts directly
  from `linklib.gates`, over the FULL field text (`tools.description`,
  not the Compare page's summary-preferring excerpt) — matching the real
  profile page's own field selection, not Compare's condensed view.
- `compare_software`/`compare_communities` call `linklib.compare.
  build_software_compare`/`build_communities_compare` **unmodified** —
  the exact same serializer the web Compare pages use — then apply
  `gates.badge_text`/`gates.COMPARE_EMPTY_LABELS` per field to render the
  audience-specific text. Existing entity caps (4 tools / 3 communities)
  are enforced by rejecting an over-cap request outright (`ToolError`),
  not silently truncating the way the web route's own `id_list[:4]` does
  — an agentic caller should learn its request was too large, not
  silently get a partial answer.

Compare Phase 2's cached AI comparison summary is included **cache-hit
only** (Brian's explicit approval, Step 0 item 6): a plain
`Library.get_compare_summary` lookup against the exact same cache key the
web route computes (entity-id-set + content hash) is read-only and free,
so it's always attempted; a miss omits the `summary` field entirely.
`generate_compare_summary` is never called from here — an agentic
conversation comparing many different entity sets could otherwise spend
against the shared daily cost cap with no human ever seeing a web page.
"""
from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from linklib import compare, gates
from linklib.db import Library

from webapp.mcp_server import require_caller

_MAX_SEARCH_RESULTS = 50
_DEFAULT_SEARCH_LIMIT = 20


# ---------------------------------------------------------------------------
# Shared helpers — search / resolve / gated-field serialization. No HTML,
# no markup, ever (same import-path discipline gates.py's own docstring
# describes for MCP Phase 3) — every result here is plain dict/list/str/
# bool/int, safe to JSON-serialize as MCP tool output.
# ---------------------------------------------------------------------------

def _matches(query: str, *texts: str | None) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    return any(q in (t or "").lower() for t in texts)


def _resolve_tool(lib: Library, slug_or_id: str) -> dict | None:
    s = str(slug_or_id).strip()
    if s.isdigit():
        t = lib.get_tool(int(s))
        return t if t and t.get("approved") else None
    return lib.get_tool_by_slug(s)


def _resolve_community(lib: Library, slug_or_id: str) -> dict | None:
    s = str(slug_or_id).strip()
    if s.isdigit():
        c = lib.get_community(int(s))
        return c if c and c.get("approved") else None
    return lib.get_community_by_slug(s)


def _empty_copy_text(empty_key: str, authed: bool) -> str:
    """Profile-page-family empty copy (`gates.EMPTY_COPY`) — visitor text,
    plus the admin "go fill this in" suffix only for an admin caller.
    Mirrors `webapp.app._empty_state_text` exactly, just returning plain
    text instead of building an HTML card."""
    copy = gates.EMPTY_COPY[empty_key]
    if authed and copy.admin_suffix:
        return f"{copy.visitor_text} {copy.admin_suffix}".strip()
    return copy.visitor_text


def _gated_field(key: str, label: str, empty_key: str, text: str | None,
                  unverified: bool, authed: bool, citations: list | None = None) -> dict:
    """One field's full gated representation for `get_software`/`get_community`
    — the profile-page family of empty copy (`gates.EMPTY_COPY`, with the
    admin suffix), unlike `compare_software`/`compare_communities`'s own
    `_compare_field` below which uses the shorter compare-matrix family
    (`gates.COMPARE_EMPTY_LABELS`, no admin suffix) — same split the HTML
    routes themselves make between a profile page and a compare cell."""
    stripped = (text or "").strip()
    state = gates.field_state(stripped, unverified)
    out: dict = {"key": key, "label": label, "state": state.value}
    if state == gates.GateState.EMPTY:
        out["text"] = ""
        out["placeholder"] = _empty_copy_text(empty_key, authed)
        return out
    out["text"] = stripped
    badge = gates.badge_text(state, authed)
    if badge:
        out["badge"] = badge
    if citations:
        out["citations"] = citations
    return out


def _chip_list(title: str, empty_key: str, items: list[dict], authed: bool) -> dict:
    out: dict = {"title": title, "items": items}
    if not items:
        out["placeholder"] = _empty_copy_text(empty_key, authed)
    return out


# The compare-matrix (`gates.COMPARE_EMPTY_LABELS`) empty-copy key for each
# `compare.CompareField.key` this module ever serializes — mirrors
# webapp.app's own positional `_section_empty_keys` lists exactly (tools_
# software_compare / tools_communities_compare) but keyed by field key
# instead of section index, so it works regardless of a section's position.
_COMPARE_FIELD_EMPTY_KEY = {
    "description": "tool_description",
    "agent_taxonomy": "tool_agent_taxonomy",
    "competitive_differentiation": "tool_differentiation",
    "verdict_summary": "community_bottom_line",
}


def _compare_field_empty_key(field_key: str) -> str:
    # Every Community profile group sub-field key not covering the Bottom
    # line (verdict_summary) falls back to "community_profile_group" — the
    # exact same generic key COMPARE_EMPTY_LABELS/webapp.app's own
    # `_section_empty_keys` use for every group subfield regardless of
    # which of the 4 themed groups it's in.
    return _COMPARE_FIELD_EMPTY_KEY.get(field_key, "community_profile_group")


def _compare_field(f: "compare.CompareField", authed: bool) -> dict:
    out: dict = {"key": f.key, "label": f.label, "state": f.state.value}
    if f.state == gates.GateState.EMPTY:
        out["text"] = ""
        out["placeholder"] = gates.COMPARE_EMPTY_LABELS.get(
            _compare_field_empty_key(f.key), "Not available.")
        return out
    out["text"] = f.text
    badge = gates.badge_text(f.state, authed)
    if badge:
        out["badge"] = badge
    if f.citations:
        out["citations"] = f.citations
    return out


def _compare_chip_list(cl: "compare.CompareChipList", authed: bool) -> dict:
    return _chip_list(
        cl.title, cl.empty_copy_key,
        [{"name": i.name, "url": i.url} for i in cl.items],
        authed,
    )


def _compare_key_fact(kf: "compare.CompareKeyFact") -> dict:
    return {"label": kf.label, "value": kf.value, "needs_verification": kf.needs_verification}


def _serialize_compare_entity(e: "compare.CompareEntity", authed: bool) -> dict:
    return {
        "id": e.id,
        "slug": e.slug,
        "name": e.name,
        "profile_url": e.profile_url,
        "promoted": e.promoted,
        "advisor": e.advisor,
        "tags": e.tags,
        "key_facts": [_compare_key_fact(kf) for kf in e.key_facts],
        "sections": [
            {"title": sec.title, "fields": [_compare_field(f, authed) for f in sec.fields]}
            for sec in e.sections
        ],
        "chip_lists": [_compare_chip_list(cl, authed) for cl in e.chip_lists],
    }


def _cached_compare_summary(lib: Library, entity_type: str,
                             entities: list["compare.CompareEntity"]) -> dict | None:
    """Cache-hit-only lookup of Compare Phase 2's AI comparison summary
    (Step 0 item 6, Brian-approved) — never generates. `entity_ids`/
    `content_hash` are computed identically to `webapp.app.
    _cmp_summary_block_html`'s own cache key so an MCP call hits exactly
    when the web page's own cache would."""
    entity_ids = ",".join(str(e.id) for e in sorted(entities, key=lambda e: e.id))
    entities_data = []
    for e in entities:
        sections = []
        for section in e.sections:
            for f in section.fields:
                if f.state == gates.GateState.EMPTY:
                    continue
                sections.append((f.label, f.text, f.state == gates.GateState.PENDING))
        entities_data.append({"name": e.name, "tags": e.tags, "sections": sections})
    parts = []
    for e in entities_data:
        parts.append(e.get("name", ""))
        parts.append(",".join(e.get("tags") or []))
        for label, text, _unverified in e.get("sections", []):
            parts.append(f"{label}:{text}")
    content_hash = Library.compare_summary_content_hash("\n".join(parts))
    cached = lib.get_compare_summary(entity_type, entity_ids, content_hash)
    if not cached:
        return None
    has_unverified = any(unverified for e in entities_data for _l, _t, unverified in e["sections"])
    return {
        "text": cached["summary"],
        "has_unverified_content": has_unverified,
        "disclosure": (
            "AI-generated summary, not human-verified. Includes catalog content still under review."
            if has_unverified else
            "AI-generated summary, not human-verified."
        ),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_toolbox_tools(mcp: FastMCP, lib_factory: Callable[[], Library]) -> None:
    """Register the six Toolbox/Communities content tools onto an already-
    built FastMCP instance (webapp.app calls this right after
    `webapp.mcp_server.build_mcp`). Kept as a separate registration step,
    not folded into `build_mcp` itself, so `mcp_server.py` stays scoped to
    the introspection tools + transport/auth plumbing it was built for."""

    @mcp.tool()
    async def search_software(ctx: Context, query: str = "", category: str = "",
                            limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
        """Search the CFO Toolbox software directory — mirrors `/tools/
        software`'s own client-side filtering (there's no server-side
        search today; this replicates the same simple substring/category
        match over the same approved-only tool list). `query` matches
        case-insensitively against name/summary/description; `category`
        matches one of the tool's own category tags exactly (case-
        sensitive, matching the site's own curated category names).
        Returns approved tools only — same as the public directory.
        `limit` is capped at 50. Any valid token, any role."""
        require_caller(ctx, lib_factory)
        limit = max(1, min(int(limit), _MAX_SEARCH_RESULTS))
        lib = lib_factory()
        try:
            tools = lib.list_tools(approved_only=True)
        finally:
            lib.close()
        out = []
        for t in tools:
            if category and category not in (t.get("categories") or []):
                continue
            if not _matches(query, t.get("name"), t.get("summary"), t.get("description")):
                continue
            out.append({
                "id": t["id"], "slug": t["slug"], "name": t["name"],
                "profile_url": f"/tools/software/{t['slug']}",
                "categories": t.get("categories") or [],
                "summary": (t.get("summary") or "").strip(),
                "promoted": bool(t.get("promoted")), "advisor": bool(t.get("advisor")),
            })
            if len(out) >= limit:
                break
        return out

    @mcp.tool()
    async def get_software(ctx: Context, slug_or_id: str) -> dict:
        """Full profile detail for one Software tool — mirrors `/tools/
        software/{slug}`. `slug_or_id` may be the tool's slug or its
        numeric id; an unapproved tool (by either lookup) is refused, same
        as the public profile page 404ing on one. Description/Agent
        taxonomy/Bottom line each carry a `state` (verified/pending/empty)
        and, for a caller without the admin role, a "pending" field's
        content is still shown with a visitor-facing "under review" badge
        — never hidden — exactly like the public page (the radical-
        transparency standard: content always renders, only the badge
        differs by audience). An admin caller additionally sees the
        whole-record `needs_review` flag, which has no visitor-facing
        rendering on the web page either. Any valid token, any role."""
        caller = require_caller(ctx, lib_factory)
        authed = caller.get("role") == "admin"
        lib = lib_factory()
        try:
            tool = _resolve_tool(lib, slug_or_id)
            if not tool:
                raise ToolError(f"no such approved tool: {slug_or_id!r}")
            competitors = lib.list_tool_competitors(tool["id"])
            feature_links = lib.list_tool_feature_links_with_details(tool["id"])
            description_citations = lib.get_entity_citations("tool", tool["id"], "description")
            taxonomy_citations = lib.get_entity_citations("tool", tool["id"], "agent_taxonomy")
        finally:
            lib.close()

        result = {
            "id": tool["id"], "slug": tool["slug"], "name": tool["name"],
            "url": tool.get("url") or "", "profile_url": f"/tools/software/{tool['slug']}",
            "categories": tool.get("categories") or [],
            "promoted": bool(tool.get("promoted")), "advisor": bool(tool.get("advisor")),
            "summary": (tool.get("summary") or "").strip(),
            "description": _gated_field(
                "description", "Description", "tool_description",
                tool.get("description"), bool(tool.get("description_needs_verification")),
                authed, description_citations),
            "agent_taxonomy": _gated_field(
                "agent_taxonomy", "Agent taxonomy", "tool_agent_taxonomy",
                tool.get("agent_taxonomy_note"), bool(tool.get("agent_taxonomy_needs_verification")),
                authed, taxonomy_citations),
            "bottom_line": _gated_field(
                "competitive_differentiation", "Bottom line", "tool_differentiation",
                tool.get("competitive_differentiation"),
                bool(tool.get("competitive_differentiation_needs_verification")), authed),
            "competitors": _chip_list(
                "Competitors", "tool_competitors",
                [{"id": c["id"], "slug": c["slug"], "name": c["name"],
                  "profile_url": f"/tools/software/{c['slug']}"} for c in competitors],
                authed),
            # Each feature carries its full category-level definition (same
            # gated-field shape as description/bottom_line, profile-page
            # empty copy) and pointer_note, so FP&A Buddy and other MCP
            # callers get the text the public Key features card renders, in
            # full rather than the card's derived short form.
            # public_note is the tool's own publishable vendor-specific text
            # ("" when Brian hasn't written one). tool_feature_links.note is
            # deliberately omitted: it's a curation log with reviewer
            # caveats, not public copy, and this tool is callable by any
            # token.
            "key_features": [
                {"category": f["category_name"], "name": f["feature_name"],
                 "availability": f.get("availability"), "ai_enabled": bool(f.get("ai_enabled")),
                 "definition": _gated_field(
                     "definition", "Definition", "feature_definition",
                     " ".join((f.get("feature_definition") or "").split()), False, authed),
                 "pointer_note": (f.get("feature_pointer_note") or "").strip(),
                 "public_note": (f.get("public_note") or "").strip()}
                for f in feature_links
            ],
        }
        if authed:
            result["needs_review"] = bool(tool.get("needs_review"))
        return result

    @mcp.tool()
    async def search_communities(ctx: Context, query: str = "", category: str = "",
                                  limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
        """Search the CFO Toolbox Communities directory — mirrors `/tools/
        communities`'s own client-side filtering, same reasoning as
        `search_software`. Returns approved communities only. `limit` capped
        at 50. Any valid token, any role."""
        require_caller(ctx, lib_factory)
        limit = max(1, min(int(limit), _MAX_SEARCH_RESULTS))
        lib = lib_factory()
        try:
            communities = lib.list_communities_for_directory()
        finally:
            lib.close()
        out = []
        for c in communities:
            if category and category not in (c.get("categories") or []):
                continue
            # PR 2a: matches on name, Bottom line and Ideal member (the two
            # profile fields that replaced the retired demographic/notes).
            if not _matches(query, c.get("name"), c.get("bottom_line"), c.get("ideal_member")):
                continue
            out.append({
                "id": c["id"], "slug": c["slug"], "name": c["name"],
                "profile_url": f"/tools/communities/{c['slug']}",
                "categories": c.get("categories") or [],
                "bottom_line": (c.get("bottom_line") or "").strip(),
                "promoted": bool(c.get("featured")), "advisor": bool(c.get("advisor")),
            })
            if len(out) >= limit:
                break
        return out

    @mcp.tool()
    async def get_community(ctx: Context, slug_or_id: str) -> dict:
        """Full profile detail for one Community — mirrors `/tools/
        communities/{slug}`. Reuses `linklib.compare.
        build_communities_compare` internally (a single-entity call) for
        the Bottom line/4 profile-group sections and Key facts, so this
        tool's gating and field selection is byte-for-byte the same code
        the web Compare page runs, not a re-derived approximation.
        `slug_or_id` may be the slug or numeric id; an unapproved community
        is refused. An admin caller additionally sees the whole-profile
        `needs_review` flag. Any valid token, any role."""
        caller = require_caller(ctx, lib_factory)
        authed = caller.get("role") == "admin"
        lib = lib_factory()
        try:
            community = _resolve_community(lib, slug_or_id)
            if not community:
                raise ToolError(f"no such approved community: {slug_or_id!r}")
            profile = lib.get_community_profile(community["id"]) or {}
            citations = lib.get_entity_citations("community", community["id"], "community_profile")
            similar = lib.list_community_competitors(community["id"])
        finally:
            lib.close()

        entities, _tag_diff = compare.build_communities_compare(
            [community], {community["id"]: profile},
            {community["id"]: citations}, {community["id"]: similar})
        entity = entities[0]

        result = {
            "id": community["id"], "slug": community["slug"], "name": community["name"],
            "url": community.get("url") or "", "profile_url": entity.profile_url,
            "categories": entity.tags, "promoted": entity.promoted, "advisor": entity.advisor,
            "key_facts": [_compare_key_fact(kf) for kf in entity.key_facts],
            "sections": [
                {"title": sec.title,
                 "fields": [_gated_field_from_compare(f, authed) for f in sec.fields]}
                for sec in entity.sections
            ],
            "similar_communities": _compare_chip_list(entity.chip_lists[0], authed),
        }
        if authed:
            result["needs_review"] = bool(profile.get("needs_review")) if profile else False
        return result

    @mcp.tool()
    async def compare_software(ctx: Context, ids: list[int]) -> dict:
        """Side-by-side comparison of 2-4 Software tools — mirrors `/tools/
        software/compare`. Calls `linklib.compare.build_software_compare`
        unmodified, the exact serializer the web Compare page uses.
        Rejects (does not silently truncate) a request with fewer than 2
        or more than 4 ids, matching the web page's own 4-entity cap.
        Unapproved/unknown ids are dropped before the count check, so a
        request with 4 ids where only 1 resolves is rejected as
        insufficient rather than silently compared against fewer entities.
        Includes the cached Compare Phase 2 AI summary only on a cache
        hit — never generates one. Any valid token, any role."""
        caller = require_caller(ctx, lib_factory)
        authed = caller.get("role") == "admin"
        if len(ids) > 4:
            raise ToolError("compare_software accepts at most 4 tool ids, matching /tools/software/compare's own cap")
        if len(ids) < 2:
            raise ToolError("compare_software needs at least 2 tool ids")
        lib = lib_factory()
        try:
            tools = []
            seen: set[int] = set()
            for tid in ids:
                if tid in seen:
                    continue
                seen.add(tid)
                t = lib.get_tool(tid)
                if t and t.get("approved"):
                    tools.append(t)
            if len(tools) < 2:
                raise ToolError("at least 2 of the given ids must resolve to an approved tool")
            citations: dict = {}
            competitors: dict = {}
            for t in tools:
                citations[(t["id"], "description")] = lib.get_entity_citations("tool", t["id"], "description")
                citations[(t["id"], "agent_taxonomy")] = lib.get_entity_citations("tool", t["id"], "agent_taxonomy")
                competitors[t["id"]] = lib.list_tool_competitors(t["id"])
            entities, tag_diff = compare.build_software_compare(tools, citations, competitors)
            summary = _cached_compare_summary(lib, "tool", entities)
        finally:
            lib.close()
        return {
            "entities": [_serialize_compare_entity(e, authed) for e in entities],
            "shared_tags": tag_diff.shared,
            "summary": summary,
        }

    @mcp.tool()
    async def compare_communities(ctx: Context, ids: list[int]) -> dict:
        """Side-by-side comparison of 2-3 Communities — mirrors `/tools/
        communities/compare`. Calls `linklib.compare.
        build_communities_compare` unmodified. Rejects (does not silently
        truncate) a request with fewer than 2 or more than 3 ids, matching
        the web page's own 3-entity cap. Includes the cached Compare
        Phase 2 AI summary only on a cache hit. Any valid token, any
        role."""
        caller = require_caller(ctx, lib_factory)
        authed = caller.get("role") == "admin"
        if len(ids) > 3:
            raise ToolError("compare_communities accepts at most 3 community ids, matching /tools/communities/compare's own cap")
        if len(ids) < 2:
            raise ToolError("compare_communities needs at least 2 community ids")
        lib = lib_factory()
        try:
            communities = []
            seen: set[int] = set()
            for cid in ids:
                if cid in seen:
                    continue
                seen.add(cid)
                c = lib.get_community(cid)
                if c and c.get("approved"):
                    communities.append(c)
            if len(communities) < 2:
                raise ToolError("at least 2 of the given ids must resolve to an approved community")
            profiles = {c["id"]: (lib.get_community_profile(c["id"]) or {}) for c in communities}
            citations = {c["id"]: lib.get_entity_citations("community", c["id"], "community_profile")
                         for c in communities}
            similar = {c["id"]: lib.list_community_competitors(c["id"]) for c in communities}
            entities, tag_diff = compare.build_communities_compare(communities, profiles, citations, similar)
            summary = _cached_compare_summary(lib, "community", entities)
        finally:
            lib.close()
        return {
            "entities": [_serialize_compare_entity(e, authed) for e in entities],
            "shared_tags": tag_diff.shared,
            "summary": summary,
        }

    @mcp.tool()
    async def search_benchmarking(ctx: Context, query: str = "",
                                   limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
        """Search the Resources page's benchmarking sources
        (`/tools/resources`) — mirrors `search_communities`'s own
        in-memory case-insensitive substring shape, over the same
        `benchmarks` table row set the public page reads
        (`section='benchmarking'`; the separate `section='books'` rows are
        `search_books`'s job, not this tool's — see that tool's own
        docstring for why they're two tools, not one, despite sharing a
        table). `query` matches against name/description; pass "" to
        return every benchmarking source. `limit` capped at 50. Any valid
        token, any role — the underlying page has no auth gate."""
        require_caller(ctx, lib_factory)
        limit = max(1, min(int(limit), _MAX_SEARCH_RESULTS))
        lib = lib_factory()
        try:
            rows = lib.list_benchmarks(section="benchmarking")
        finally:
            lib.close()
        out = []
        for b in rows:
            if not _matches(query, b.get("name"), b.get("description")):
                continue
            out.append({
                "id": b["id"], "name": b["name"], "url": b.get("url") or "",
                "description": (b.get("description") or "").strip(),
                "coverage": b.get("coverage") or "", "pricing": b.get("pricing") or "",
            })
            if len(out) >= limit:
                break
        return out

    @mcp.tool()
    async def search_books(ctx: Context, query: str = "",
                            limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
        """Search the Resources page's book recommendations
        (`/tools/resources`) — same `benchmarks` table
        `search_benchmarking` reads, filtered to `section='books'`
        instead. Kept as its own tool rather than folded into
        `search_benchmarking` with a type filter: they're two separate
        things a caller asks for separately, and the public page itself
        renders them as two headed sections, not one filterable list.
        `coverage`/`pricing` are omitted from a book hit — the public page
        doesn't render those badges for books either, since they encode a
        data-access tier that doesn't apply to a reading list. `query`
        matches against name/description; pass "" to return every book.
        `limit` capped at 50. Any valid token, any role."""
        require_caller(ctx, lib_factory)
        limit = max(1, min(int(limit), _MAX_SEARCH_RESULTS))
        lib = lib_factory()
        try:
            rows = lib.list_benchmarks(section="books")
        finally:
            lib.close()
        out = []
        for b in rows:
            if not _matches(query, b.get("name"), b.get("description")):
                continue
            out.append({
                "id": b["id"], "name": b["name"], "url": b.get("url") or "",
                "description": (b.get("description") or "").strip(),
            })
            if len(out) >= limit:
                break
        return out


def _gated_field_from_compare(f: "compare.CompareField", authed: bool) -> dict:
    """`get_community`'s field serialization — same profile-page empty-copy
    family (`gates.EMPTY_COPY`, with the admin suffix) as `_gated_field`
    uses for `get_software`, applied to a `CompareField` already built by
    `build_communities_compare` rather than to raw dict fields. Kept
    separate from `_compare_field` (used by `compare_software`/
    `compare_communities`), which deliberately uses the shorter
    compare-matrix empty family (`gates.COMPARE_EMPTY_LABELS`, no admin
    suffix) — same split the HTML routes make between a profile page and a
    compare-matrix cell."""
    out: dict = {"key": f.key, "label": f.label, "state": f.state.value}
    if f.state == gates.GateState.EMPTY:
        out["text"] = ""
        out["placeholder"] = _empty_copy_text(_compare_field_empty_key(f.key), authed)
        return out
    out["text"] = f.text
    badge = gates.badge_text(f.state, authed)
    if badge:
        out["badge"] = badge
    if f.citations:
        out["citations"] = f.citations
    return out
