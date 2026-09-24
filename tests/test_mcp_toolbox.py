"""Tests for MCP Phase 3 (+ PR 8): the eight Toolbox/Communities content
tools (search_software, get_software, search_communities, get_community,
compare_software, compare_communities, search_benchmarking, search_books).

Uses the same real-running-server pattern tests/test_mcp_server.py
establishes (a TestClient's ASGI shortcut doesn't run a real event loop the
way FastMCP's session manager needs) — a real uvicorn server, a real
streamable-HTTP client, real tokens minted against a temp DB.

The most important coverage here is gate-enforcement: proving
`linklib.gates`'s verified/pending/empty rules apply IDENTICALLY through
the MCP path as through the HTML routes — a pending field's content is
never hidden, only the trailing badge differs by caller role, and an
empty field always carries the same placeholder copy `gates.EMPTY_COPY`/
`gates.COMPARE_EMPTY_LABELS` defines.
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
from linklib import compare


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


def _seed_tool(lib: Library, name: str, **overrides) -> dict:
    tool_id = lib.add_tool(
        name, overrides.pop("description", f"{name} description text, long enough to be real content."),
        overrides.pop("url", f"https://{name.lower().replace(' ', '')}.example.com"),
        overrides.pop("categories", ["FP&A"]), approved=1, needs_review=0,
        summary=overrides.pop("summary", f"{name} short summary."),
    )
    if overrides:
        sets = ", ".join(f"{k}=?" for k in overrides)
        lib.conn.execute(f"UPDATE tools SET {sets} WHERE id=?", (*overrides.values(), tool_id))
        lib.conn.commit()
    return lib.get_tool(tool_id)


def _seed_community(lib: Library, name: str, **profile_overrides) -> dict:
    community_id = lib.add_community(
        name, f"https://{name.lower().replace(' ', '')}.example.com",
        "Finance leaders", "Free", ["FP&A"], approved=1,
    )
    profile_kwargs = dict(
        ideal_member="A finance leader who wants peer benchmarking.",
        verdict_summary="Solid for mid-market FP&A leaders.",
        needs_review=0,
    )
    profile_kwargs.update(profile_overrides)
    lib.upsert_community_profile(community_id, **profile_kwargs)
    return lib.get_community(community_id)


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


def _dict_result(result):
    assert not result.isError, result.content[0].text if result.content else result
    return json.loads(result.content[0].text)


def _list_result(result):
    assert not result.isError, result.content[0].text if result.content else result
    return result.structuredContent["result"]


# ---------------------------------------------------------------------------
# search_software / search_communities
# ---------------------------------------------------------------------------

def test_search_software_matches_query_and_respects_approval(live_server):
    lib = Library(live_server.db_path)
    _seed_tool(lib, "Abacum FPA")
    _seed_tool(lib, "Runway Tool")
    unapproved_id = lib.add_tool("Hidden Vendor", "not yet live", "https://hidden.example.com",
                                  ["FP&A"], approved=0)
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_software",
                                      {"query": "abacum"}))
    names = {t["name"] for t in result}
    assert "Abacum FPA" in names
    assert "Runway Tool" not in names
    assert "Hidden Vendor" not in names
    assert unapproved_id  # sanity: it was really created, just excluded


def test_search_software_category_filter(live_server):
    lib = Library(live_server.db_path)
    _seed_tool(lib, "Close Co", categories=["Close Management"])
    _seed_tool(lib, "Plan Co", categories=["FP&A"])
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_software",
                                      {"category": "Close Management"}))
    names = {t["name"] for t in result}
    assert names == {"Close Co"}


def test_search_software_limit_is_capped_at_50(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_software", {"limit": 9999})
    assert not result.isError


def test_search_communities_matches_query_and_respects_approval(live_server):
    lib = Library(live_server.db_path)
    _seed_community(lib, "CFO Circle")
    unapproved_id = lib.add_community("Ghost Community", "https://ghost.example.com",
                                       "n/a", "Free", ["FP&A"], approved=0)
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_communities",
                                      {"query": "cfo"}))
    names = {c["name"] for c in result}
    assert "CFO Circle" in names
    assert "Ghost Community" not in names
    assert unapproved_id


# ---------------------------------------------------------------------------
# get_software — resolution and non-admin-gated auth
# ---------------------------------------------------------------------------

def test_get_software_by_slug_and_by_id(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Slug Test Tool")
    lib.close()

    by_slug = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                       {"slug_or_id": tool["slug"]}))
    by_id = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                     {"slug_or_id": str(tool["id"])}))
    assert by_slug["id"] == by_id["id"] == tool["id"]
    assert by_slug["name"] == "Slug Test Tool"


def test_get_software_rejects_unapproved_id(live_server):
    lib = Library(live_server.db_path)
    tool_id = lib.add_tool("Pending Tool", "desc", "https://pending.example.com", ["FP&A"], approved=0)
    lib.close()

    result = _call_tool(live_server.base_url, live_server.member, "get_software", {"slug_or_id": str(tool_id)})
    assert result.isError


def test_get_software_unknown_slug_is_an_error(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "get_software", {"slug_or_id": "no-such-tool"})
    assert result.isError


def test_get_software_callable_by_non_admin_member_token(live_server):
    """The auth-model distinction from the introspection tools: any valid
    token, any role, may call these six — never admin-only."""
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Member Reachable Tool")
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "get_software",
                         {"slug_or_id": tool["slug"]})
    assert not result.isError


def test_get_community_by_slug_and_by_id(live_server):
    lib = Library(live_server.db_path)
    community = _seed_community(lib, "Slug Test Community")
    lib.close()

    by_slug = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community",
                                       {"slug_or_id": community["slug"]}))
    by_id = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community",
                                     {"slug_or_id": str(community["id"])}))
    assert by_slug["id"] == by_id["id"] == community["id"]


def test_get_community_rejects_unapproved_id(live_server):
    lib = Library(live_server.db_path)
    community_id = lib.add_community("Pending Community", "https://pendingcomm.example.com",
                                      "n/a", "Free", ["FP&A"], approved=0)
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "get_community",
                         {"slug_or_id": str(community_id)})
    assert result.isError


# ---------------------------------------------------------------------------
# Gate-enforcement — the most important coverage in this file.
# ---------------------------------------------------------------------------

def test_get_software_pending_field_shows_content_with_visitor_badge_for_member(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Pending Field Tool",
                       description_needs_verification=1)
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                    {"slug_or_id": tool["slug"]}))
    desc = data["description"]
    assert desc["state"] == "pending"
    assert desc["text"]  # never hidden
    assert desc["badge"] == "under review"


def test_get_software_pending_field_shows_admin_badge_for_admin_caller(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Pending Field Tool Admin",
                       description_needs_verification=1)
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_software",
                                    {"slug_or_id": tool["slug"]}))
    desc = data["description"]
    assert desc["state"] == "pending"
    assert desc["text"]
    assert desc["badge"] == "unverified, visible to visitors"


def test_get_software_verified_field_has_no_badge(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Verified Field Tool", description_needs_verification=0)
    lib.close()

    for token in (live_server.member, live_server.admin):
        data = _dict_result(_call_tool(live_server.base_url, token, "get_software",
                                        {"slug_or_id": tool["slug"]}))
        assert data["description"]["state"] == "verified"
        assert "badge" not in data["description"]


def test_get_software_empty_field_carries_placeholder_and_admin_suffix_only_for_admin(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Empty Bottom Line Tool", competitive_differentiation="")
    lib.close()

    member_data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                           {"slug_or_id": tool["slug"]}))
    admin_data = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_software",
                                          {"slug_or_id": tool["slug"]}))
    assert member_data["bottom_line"]["state"] == "empty"
    assert member_data["bottom_line"]["text"] == ""
    assert member_data["bottom_line"]["placeholder"] == "Bottom line not available."
    assert admin_data["bottom_line"]["placeholder"].startswith("Bottom line not available.")
    assert admin_data["bottom_line"]["placeholder"] != member_data["bottom_line"]["placeholder"]


def test_get_software_needs_review_only_visible_to_admin(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Needs Review Tool", needs_review=1)
    lib.close()

    member_data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                           {"slug_or_id": tool["slug"]}))
    admin_data = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_software",
                                          {"slug_or_id": tool["slug"]}))
    assert "needs_review" not in member_data
    assert admin_data["needs_review"] is True


def test_get_community_pending_profile_field_gated_like_html_route(live_server):
    lib = Library(live_server.db_path)
    community = _seed_community(lib, "Pending Community Profile", needs_review=1)
    lib.close()

    member_data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community",
                                           {"slug_or_id": community["slug"]}))
    admin_data = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_community",
                                          {"slug_or_id": community["slug"]}))
    bottom_line_member = next(f for sec in member_data["sections"] for f in sec["fields"]
                               if f["key"] == "verdict_summary")
    bottom_line_admin = next(f for sec in admin_data["sections"] for f in sec["fields"]
                              if f["key"] == "verdict_summary")
    assert bottom_line_member["state"] == "pending"
    assert bottom_line_member["text"]
    assert bottom_line_member["badge"] == "under review"
    assert bottom_line_admin["badge"] == "unverified, visible to visitors"
    assert "needs_review" not in member_data
    assert admin_data["needs_review"] is True


def test_get_community_empty_profile_group_field_has_placeholder(live_server):
    lib = Library(live_server.db_path)
    community = _seed_community(lib, "Sparse Community Profile", anti_fit="")
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community",
                                    {"slug_or_id": community["slug"]}))
    anti_fit = next(f for sec in data["sections"] for f in sec["fields"] if f["key"] == "anti_fit")
    assert anti_fit["state"] == "empty"
    assert anti_fit["placeholder"] == "This section hasn't been researched."


# ---------------------------------------------------------------------------
# compare_software / compare_communities — caps + gating through compare.py
# ---------------------------------------------------------------------------

def test_compare_software_rejects_under_two_ids(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Solo Tool")
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "compare_software",
                         {"ids": [tool["id"]]})
    assert result.isError


def test_compare_software_rejects_over_cap_of_four(live_server):
    lib = Library(live_server.db_path)
    ids = [_seed_tool(lib, f"Cap Tool {i}")["id"] for i in range(5)]
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "compare_software", {"ids": ids})
    assert result.isError
    assert "4" in result.content[0].text


def test_compare_software_accepts_exactly_four(live_server):
    lib = Library(live_server.db_path)
    ids = [_seed_tool(lib, f"Four Tool {i}")["id"] for i in range(4)]
    lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software",
                                    {"ids": ids}))
    assert len(data["entities"]) == 4


def test_compare_software_pending_field_masked_by_role_matching_gates(live_server):
    lib = Library(live_server.db_path)
    a = _seed_tool(lib, "Compare Pending A", description_needs_verification=1)
    b = _seed_tool(lib, "Compare Pending B")
    lib.close()

    member_data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software",
                                           {"ids": [a["id"], b["id"]]}))
    admin_data = _dict_result(_call_tool(live_server.base_url, live_server.admin, "compare_software",
                                          {"ids": [a["id"], b["id"]]}))

    def _desc_field(payload, tool_id):
        entity = next(e for e in payload["entities"] if e["id"] == tool_id)
        section = next(s for s in entity["sections"] if s["title"] == "Description")
        return section["fields"][0]

    member_field = _desc_field(member_data, a["id"])
    admin_field = _desc_field(admin_data, a["id"])
    assert member_field["state"] == "pending"
    assert member_field["text"]
    assert member_field["badge"] == "under review"
    assert admin_field["badge"] == "unverified, visible to visitors"


def test_compare_software_empty_field_uses_compare_matrix_empty_copy(live_server):
    lib = Library(live_server.db_path)
    a = _seed_tool(lib, "Compare Empty A", competitive_differentiation="")
    b = _seed_tool(lib, "Compare Empty B", competitive_differentiation="")
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software",
                                    {"ids": [a["id"], b["id"]]}))
    entity = next(e for e in data["entities"] if e["id"] == a["id"])
    section = next(s for s in entity["sections"] if s["title"] == "Bottom line")
    field = section["fields"][0]
    assert field["state"] == "empty"
    # Compare-matrix empty copy is the shorter, no-admin-suffix family —
    # distinct wording from the profile-page family get_software uses.
    assert field["placeholder"] == "Not available."


def test_compare_communities_rejects_over_cap_of_three(live_server):
    lib = Library(live_server.db_path)
    ids = [_seed_community(lib, f"Cap Community {i}")["id"] for i in range(4)]
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "compare_communities", {"ids": ids})
    assert result.isError
    assert "3" in result.content[0].text


def test_compare_communities_accepts_exactly_three(live_server):
    lib = Library(live_server.db_path)
    ids = [_seed_community(lib, f"Three Community {i}")["id"] for i in range(3)]
    lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_communities",
                                    {"ids": ids}))
    assert len(data["entities"]) == 3


def test_compare_communities_shared_tags_present(live_server):
    lib = Library(live_server.db_path)
    a = _seed_community(lib, "Shared Tag Community A")
    b = _seed_community(lib, "Shared Tag Community B")
    lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_communities",
                                    {"ids": [a["id"], b["id"]]}))
    assert data["shared_tags"] == ["FP&A"]


# ---------------------------------------------------------------------------
# Compare Phase 2 AI summary — cache-hit-only (Step 0 item 6, approved)
# ---------------------------------------------------------------------------

def _content_text(entities: list) -> str:
    parts = []
    for e in entities:
        parts.append(e.name)
        parts.append(",".join(e.tags or []))
        for section in e.sections:
            for f in section.fields:
                if f.state.value == "empty":
                    continue
                parts.append(f"{f.label}:{f.text}")
    return "\n".join(parts)


def test_compare_software_includes_summary_only_on_cache_hit(live_server):
    lib = Library(live_server.db_path)
    a = _seed_tool(lib, "Cache Hit Tool A")
    b = _seed_tool(lib, "Cache Hit Tool B")
    tools = [lib.get_tool(a["id"]), lib.get_tool(b["id"])]
    entities, _tag_diff = compare.build_software_compare(
        tools,
        {(t["id"], f): [] for t in tools for f in ("description", "agent_taxonomy")},
        {t["id"]: [] for t in tools},
    )
    entity_ids = ",".join(str(e.id) for e in sorted(entities, key=lambda e: e.id))
    content_hash = Library.compare_summary_content_hash(_content_text(entities))
    lib.set_compare_summary("tool", entity_ids, content_hash, "Both are FP&A tools; A leans deeper on agent taxonomy.",
                             "claude-test-model")
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software",
                                    {"ids": [a["id"], b["id"]]}))
    assert data["summary"] is not None
    assert data["summary"]["text"] == "Both are FP&A tools; A leans deeper on agent taxonomy."
    assert data["summary"]["has_unverified_content"] is False
    assert data["summary"]["disclosure"] == "AI-generated summary, not human-verified."


def test_compare_software_summary_is_omitted_on_cache_miss(live_server):
    lib = Library(live_server.db_path)
    a = _seed_tool(lib, "No Summary Tool A")
    b = _seed_tool(lib, "No Summary Tool B")
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software",
                                    {"ids": [a["id"], b["id"]]}))
    assert data["summary"] is None


def test_compare_communities_summary_omitted_on_cache_miss(live_server):
    lib = Library(live_server.db_path)
    a = _seed_community(lib, "No Summary Community A")
    b = _seed_community(lib, "No Summary Community B")
    lib.close()

    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_communities",
                                    {"ids": [a["id"], b["id"]]}))
    assert data["summary"] is None


# ---------------------------------------------------------------------------
# search_benchmarking / search_books — PR 8's Toolbox coverage-gap fix.
# Both read the same `benchmarks` table (section='benchmarking'|'books'),
# so the seed helper takes a section kwarg rather than being two helpers.
# ---------------------------------------------------------------------------

def _seed_benchmark(lib: Library, name: str, section: str = "benchmarking", **kwargs) -> int:
    return lib.add_benchmark(
        name,
        kwargs.pop("url", f"https://{name.lower().replace(' ', '')}.example.com"),
        kwargs.pop("description", f"{name} description text."),
        kwargs.pop("coverage", "Private"),
        kwargs.pop("pricing", "free"),
        section=section,
    )


def test_search_benchmarking_matches_query_and_scopes_to_its_section(live_server):
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "OpenView SaaS Benchmarks", section="benchmarking")
    _seed_benchmark(lib, "Unrelated Metrics Report", section="benchmarking")
    _seed_benchmark(lib, "Traction by Gabriel Weinberg", section="books")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_benchmarking",
                                      {"query": "openview"}))
    names = {b["name"] for b in result}
    assert names == {"OpenView SaaS Benchmarks"}


def test_search_benchmarking_empty_query_returns_every_row_in_its_section(live_server):
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "Bench One", section="benchmarking")
    _seed_benchmark(lib, "Bench Two", section="benchmarking")
    _seed_benchmark(lib, "Some Book", section="books")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_benchmarking", {}))
    names = {b["name"] for b in result}
    assert names == {"Bench One", "Bench Two"}


def test_search_benchmarking_includes_coverage_and_pricing(live_server):
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "Priced Benchmark", section="benchmarking", coverage="Public", pricing="paid")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_benchmarking", {}))
    row = next(b for b in result if b["name"] == "Priced Benchmark")
    assert row["coverage"] == "Public"
    assert row["pricing"] == "paid"


def test_search_benchmarking_limit_is_capped_at_50(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_benchmarking", {"limit": 9999})
    assert not result.isError


def test_search_benchmarking_callable_by_non_admin_member_token(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_benchmarking", {})
    assert not result.isError


def test_search_books_matches_query_and_scopes_to_its_section(live_server):
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "Traction by Gabriel Weinberg", section="books")
    _seed_benchmark(lib, "The Hard Thing About Hard Things", section="books")
    _seed_benchmark(lib, "OpenView SaaS Benchmarks", section="benchmarking")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_books",
                                      {"query": "traction"}))
    names = {b["name"] for b in result}
    assert names == {"Traction by Gabriel Weinberg"}


def test_search_books_empty_query_returns_every_row_in_its_section(live_server):
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "Book One", section="books")
    _seed_benchmark(lib, "Book Two", section="books")
    _seed_benchmark(lib, "Some Benchmark", section="benchmarking")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_books", {}))
    names = {b["name"] for b in result}
    assert names == {"Book One", "Book Two"}


def test_search_books_omits_coverage_and_pricing(live_server):
    """The public /tools/resources page never renders Coverage/Pricing
    badges for books — this tool mirrors that by leaving the keys out
    entirely, not just blanking them."""
    lib = Library(live_server.db_path)
    _seed_benchmark(lib, "Undecorated Book", section="books")
    lib.close()

    result = _list_result(_call_tool(live_server.base_url, live_server.member, "search_books", {}))
    row = next(b for b in result if b["name"] == "Undecorated Book")
    assert "coverage" not in row
    assert "pricing" not in row


def test_search_books_limit_is_capped_at_50(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_books", {"limit": 9999})
    assert not result.isError


def test_search_books_callable_by_non_admin_member_token(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "search_books", {})
    assert not result.isError


# ---------------------------------------------------------------------------
# get_software key_features carry each feature's definition (2026-09)
# ---------------------------------------------------------------------------

def test_get_software_key_features_include_full_definition(live_server):
    lib = Library(live_server.db_path)
    tool = _seed_tool(lib, "Definition Tool")
    cat_id = lib.add_tool_category("DefCat")
    long_def = "Set spending rules up front. " + ("More detail about approvals and limits. " * 40).strip()
    with_def = lib.add_category_feature(cat_id, "Spend Controls", long_def,
                                        "See the Procurement category.")
    without_def = lib.add_category_feature(cat_id, "Card Issuance")
    lib.upsert_tool_feature_link(tool["id"], with_def, "native", 0, "2026-08-24",
                                 note="UNVERIFIED, keep pending",
                                 public_note="Limits are set per card or per team.")
    lib.upsert_tool_feature_link(tool["id"], without_def, "native", 0, "2026-08-24")
    lib.close()

    result = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software",
                                      {"slug_or_id": tool["slug"]}))
    feats = {f["name"]: f for f in result["key_features"]}
    spend = feats["Spend Controls"]
    assert spend["definition"]["state"] == "verified"
    assert spend["definition"]["text"] == long_def          # full text, not the short form
    assert spend["pointer_note"] == "See the Procurement category."
    assert spend["public_note"] == "Limits are set per card or per team."
    assert "vendor_note" not in spend and "note" not in spend
    assert "UNVERIFIED" not in str(result)          # internal note never leaves
    assert feats["Card Issuance"]["public_note"] == ""
    card = feats["Card Issuance"]["definition"]
    assert card["state"] == "empty" and card["text"] == ""
    from linklib import gates
    assert card["placeholder"] == gates.EMPTY_COPY["feature_definition"].visitor_text
