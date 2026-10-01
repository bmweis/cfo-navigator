"""MCP field parity (2026-10): structured Program details, CPE gating,
`warm_intro_available`, and the parity registry that forces an MCP decision
for every stored column on the four profile tables.

Reuses tests/test_mcp_toolbox.py's real-server fixture and helpers.
"""
import json

import httpx
import pytest

from linklib import compare
from linklib.db import Library

from tests import test_mcp_toolbox as _base
from tests.test_mcp_toolbox import _call_tool, _dict_result, _seed_community, _seed_tool


@pytest.fixture
def live_server(monkeypatch):
    """The real-server fixture from test_mcp_toolbox, reused as is."""
    yield from _base.live_server.__wrapped__(monkeypatch)


_TABLES = ("tools", "communities", "community_profiles", "tool_feature_links")


def _columns(table: str) -> dict[str, str]:
    import os, tempfile
    path = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(path)
        cols = {r[1]: (r[2] or "").upper() for r in lib.conn.execute(f"PRAGMA table_info({table})")}
        lib.close()
        return cols
    finally:
        if os.path.exists(path):
            os.remove(path)


def _kind(entry: str) -> str:
    return entry.split(":", 1)[0].strip()


# --- Test 1: the registry covers every column, nothing more ----------------

def test_registry_covers_every_column_exactly():
    registered = set(compare.MCP_PARITY)
    actual = {f"{t}.{c}" for t in _TABLES for c in _columns(t)}
    assert actual - registered == set(), (
        "stored column with no MCP decision: add it to linklib.compare.MCP_PARITY "
        f"(mcp:/mcp-admin:/admin-only:/excluded:/retired/internal:): {sorted(actual - registered)}")
    assert registered - actual == set(), f"registry names a column that no longer exists: {sorted(registered - actual)}"


def test_registry_entries_use_a_known_category_and_a_reason():
    for key, entry in compare.MCP_PARITY.items():
        kind = _kind(entry)
        assert kind in compare.MCP_PARITY_KINDS, (key, entry)
        if kind in ("admin-only", "excluded", "internal", "retired"):
            reason = entry.split(":", 1)[1].strip() if ":" in entry else ""
            assert reason, f"{key}: {kind} needs a stated reason"


def test_retired_lists_are_registered_as_retired():
    from linklib.community_profile import RETIRED_PROFILE_FIELDS, RETIRED_COMMUNITY_FIELDS
    for c in RETIRED_PROFILE_FIELDS:
        assert _kind(compare.MCP_PARITY[f"community_profiles.{c}"]) == "retired", c
    for c in RETIRED_COMMUNITY_FIELDS:
        assert _kind(compare.MCP_PARITY[f"communities.{c}"]) == "retired", c


# --- shared seeding ---------------------------------------------------------

def _sentinel(col: str) -> str:
    return f"SENTINEL_{col.upper()}_XYZ"


def _seed_everything(db_path: str, *, needs_review: int = 0):
    """Two tools and two communities with every column populated, every
    admin-only/excluded text column carrying a distinctive sentinel, plus a
    feature link. Returns (tool_a, tool_b, comm_a, comm_b) rows."""
    lib = Library(db_path)
    tools, comms = [], []
    for n in ("Parity Alpha", "Parity Beta"):
        t = _seed_tool(
            lib, n, competitive_differentiation=f"{n} bottom line.",
            agent_taxonomy_note=f"{n} agent note.", warm_intro_enabled=1,
            vendor_email=_sentinel("vendor_email") + "@leak.example",
            vendor_name=_sentinel("vendor_name"), needs_review=needs_review)
        tools.append(t)
        c = _seed_community(lib, n + " Community", needs_review=needs_review,
                            cpe_eligible="Yes (NASBA sponsor)", resources_included="Guides",
                            jobs_program="Job board")
        lib.conn.execute(
            "UPDATE communities SET cost_band='<$1k/yr', access='Open', format='Hybrid',"
            " sponsorship_type='Independent', reach='National' WHERE id=?", (c["id"],))
        comms.append(c)
    lib.conn.commit()
    # sentinels in every TEXT column the registry classes as admin-only/excluded
    for table, rows in (("tools", tools), ("communities", comms)):
        cols = _columns(table)
        for key, entry in compare.MCP_PARITY.items():
            t, col = key.split(".", 1)
            if t != table or _kind(entry) not in ("admin-only", "excluded") or cols[col] != "TEXT":
                continue
            for r in rows:
                lib.conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (_sentinel(col), r["id"]))
    for key, entry in compare.MCP_PARITY.items():
        t, col = key.split(".", 1)
        if t == "community_profiles" and _kind(entry) in ("admin-only", "excluded") \
                and _columns(t)[col] == "TEXT":
            for c in comms:
                lib.conn.execute(f"UPDATE community_profiles SET {col}=? WHERE community_id=?",
                                 (_sentinel(col), c["id"]))
    lib.conn.commit()
    # one feature link carrying sentinels on the internal columns
    cat = lib.add_tool_category("Parity Cat")
    fid = lib.add_category_feature(cat, "Parity feature", "Defines it.", "Pointer.")
    for t in tools:
        lib.upsert_tool_feature_link(t["id"], fid, "native", 1, _sentinel("verified_as_of"),
                                     note=_sentinel("note"), source_url="https://" + _sentinel("source_url").lower() + ".example",
                                     public_note="Public vendor text.")
    lib.close()
    return tools[0], tools[1], comms[0], comms[1]


def _walk_keys(o, out=None):
    out = set() if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_keys(v, out)
    return out


def _resolve(output: dict, path: str) -> bool:
    """True when `path` ("tool.dotted.keys", with `field[key]`,
    `program_details[Label]` and a trailing `[]` list selector) exists."""
    _tool, rest = path.split(".", 1)
    if rest.startswith("field["):
        key = rest[6:-1]
        return any(f.get("key") == key for s in output["sections"] for f in s["fields"])
    if rest.startswith("program_details["):
        label = rest[len("program_details["):-1]
        return any(d.get("label") == label for d in output["program_details"])
    cur = output
    for seg in rest.split("."):
        if seg.endswith("[]"):
            cur = cur.get(seg[:-2]) if isinstance(cur, dict) else None
            if not cur:
                return False
            cur = cur[0]
            continue
        if not isinstance(cur, dict) or seg not in cur:
            return False
        cur = cur[seg]
    return True


def _fetch(live_server, token, tool_a, tool_b, comm_a, comm_b):
    c = lambda name, args: _dict_result(_call_tool(live_server.base_url, token, name, args))
    return {
        "get_software": c("get_software", {"slug_or_id": tool_a["slug"]}),
        "get_community": c("get_community", {"slug_or_id": comm_a["slug"]}),
        "compare_software": c("compare_software", {"ids": [tool_a["id"], tool_b["id"]]}),
        "compare_communities": c("compare_communities", {"ids": [comm_a["id"], comm_b["id"]]}),
    }


# --- Test 2: every mcp: entry is really served ------------------------------

def test_every_mcp_entry_resolves_in_a_seeded_output(live_server):
    ta, tb, ca, cb = _seed_everything(live_server.db_path)
    out = _fetch(live_server, live_server.admin, ta, tb, ca, cb)
    for key, entry in compare.MCP_PARITY.items():
        if _kind(entry) not in ("mcp", "mcp-admin"):
            continue
        path = entry.split(":", 1)[1].strip()
        tool = path.split(".", 1)[0]
        assert _resolve(out[tool], path), f"{key} claims {path} but the output lacks it"
        if tool in ("get_community",):
            # the same field must be reachable through compare_communities too
            ent = out["compare_communities"]["entities"][0]
            if path.split(".", 1)[1].startswith(("field[", "program_details[")):
                assert _resolve(ent, "x." + path.split(".", 1)[1]), f"{key} missing from compare_communities"


# --- Test 3: the permission guard -------------------------------------------

def test_admin_only_entries_never_reach_a_member_token(live_server):
    ta, tb, ca, cb = _seed_everything(live_server.db_path)
    out = _fetch(live_server, live_server.member, ta, tb, ca, cb)
    blob = json.dumps(out)
    # Keys are scanned inside the subtree each table feeds, so a key that is
    # legitimately served elsewhere (CPE's `note` on a community) cannot be
    # mistaken for tool_feature_links.note.
    scope = {
        "tools": [out["get_software"], out["compare_software"]],
        "tool_feature_links": [out["get_software"]["key_features"]],
        "communities": [out["get_community"], out["compare_communities"]],
        "community_profiles": [out["get_community"], out["compare_communities"]],
    }
    for key, entry in compare.MCP_PARITY.items():
        kind = _kind(entry)
        if kind not in ("admin-only", "mcp-admin"):
            continue
        table, col = key.split(".", 1)
        keys = set()
        for o in scope[table]:
            _walk_keys(o, keys)
        assert col not in keys, f"{key} ({kind}) appears as a key in a member-token response"
        assert _sentinel(col) not in blob, f"{key} value leaked to a member token"
    assert "leak.example" not in blob


def test_mcp_admin_entries_are_served_to_admin_only(live_server):
    ta, tb, ca, cb = _seed_everything(live_server.db_path, needs_review=1)
    admin = _fetch(live_server, live_server.admin, ta, tb, ca, cb)
    member = _fetch(live_server, live_server.member, ta, tb, ca, cb)
    for key, entry in compare.MCP_PARITY.items():
        if _kind(entry) != "mcp-admin":
            continue
        path = entry.split(":", 1)[1].strip()
        tool = path.split(".", 1)[0]
        assert _resolve(admin[tool], path), f"{key}: admin should see {path}"
        assert not _resolve(member[tool], path), f"{key}: member must not see {path}"


# --- Test 5: excluded stays unserved (a deliberate decision, not a leak) ----

def test_excluded_entries_stay_unserved_until_the_registry_changes(live_server):
    ta, tb, ca, cb = _seed_everything(live_server.db_path)
    out = _fetch(live_server, live_server.admin, ta, tb, ca, cb)
    blob = json.dumps(out)
    keys = set()
    for o in out.values():
        _walk_keys(o, keys)
    for key, entry in compare.MCP_PARITY.items():
        if _kind(entry) != "excluded":
            continue
        col = key.split(".", 1)[1]
        assert col not in keys, f"{key} is excluded but is now served; update MCP_PARITY"
        assert _sentinel(col) not in blob, f"{key} is excluded but its value is served"


# --- Test 4: Program details labels agree across page, MCP, constant --------

def test_program_details_labels_match_page_and_mcp(live_server):
    ta, tb, ca, cb = _seed_everything(live_server.db_path)
    out = _fetch(live_server, live_server.member, ta, tb, ca, cb)
    mcp_labels = [d["label"] for d in out["get_community"]["program_details"]]
    assert mcp_labels == list(compare.PROGRAM_DETAILS_LABELS)
    cmp_labels = [d["label"] for d in out["compare_communities"]["entities"][0]["program_details"]]
    assert cmp_labels == list(compare.PROGRAM_DETAILS_LABELS)
    html = httpx.get(f"{live_server.base_url}/tools/communities/{ca['slug']}", timeout=180).text
    import re
    page_labels = re.findall(r'class="tp-detail-label">([^<]+)<', html)
    assert page_labels == list(compare.PROGRAM_DETAILS_LABELS)


# --- program_details content + CPE gating -----------------------------------

def _detail(data, label):
    return next(d for d in data["program_details"] if d["label"] == label)


def test_program_details_are_structured_and_key_facts_alias_remains(live_server):
    lib = Library(live_server.db_path)
    c = _seed_community(lib, "Struct Community", cpe_eligible="Yes (NASBA sponsor)")
    lib.conn.execute("UPDATE communities SET cost_band='<$1k/yr', access='Open', format='Hybrid' WHERE id=?", (c["id"],))
    lib.conn.commit(); lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community", {"slug_or_id": c["slug"]}))
    assert _detail(data, "Cost band")["value"] == "<$1k/yr"
    cpe = _detail(data, "CPE eligible")
    assert cpe["value"] == "Yes" and cpe["note"] == "NASBA sponsor"
    assert cpe["state"] == "verified" and "badge" not in cpe
    kf = {k["label"]: k["value"] for k in data["key_facts"]}
    assert kf["CPE eligible"] == "Yes (NASBA sponsor)"
    assert kf["Cost band"] == "<$1k/yr"


def test_cpe_pending_review_state_by_role(live_server):
    lib = Library(live_server.db_path)
    c = _seed_community(lib, "Pending Cpe", needs_review=1, cpe_eligible="No")
    lib.close()
    m = _detail(_dict_result(_call_tool(live_server.base_url, live_server.member, "get_community", {"slug_or_id": c["slug"]})), "CPE eligible")
    a = _detail(_dict_result(_call_tool(live_server.base_url, live_server.admin, "get_community", {"slug_or_id": c["slug"]})), "CPE eligible")
    assert m["state"] == a["state"] == "pending"
    assert m["badge"] == "under review"
    assert a["badge"] == "unverified, visible to visitors"
    assert "note" not in m


def test_cpe_empty_reads_not_assessed_with_no_note(live_server):
    lib = Library(live_server.db_path)
    c = _seed_community(lib, "No Cpe", cpe_eligible="")
    lib.close()
    cpe = _detail(_dict_result(_call_tool(live_server.base_url, live_server.member, "get_community", {"slug_or_id": c["slug"]})), "CPE eligible")
    assert cpe["value"] == "Not assessed" and "note" not in cpe


def test_program_details_skips_empty_rows_like_the_page(live_server):
    lib = Library(live_server.db_path)
    c = _seed_community(lib, "Sparse Details")
    lib.conn.execute("UPDATE communities SET access='', format='', cost_band='' WHERE id=?", (c["id"],))
    lib.conn.commit(); lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "get_community", {"slug_or_id": c["slug"]}))
    labels = [d["label"] for d in data["program_details"]]
    assert "Access" not in labels and "Format" not in labels and "Cost band" not in labels
    assert "CPE eligible" in labels


def test_compare_software_entities_carry_an_empty_program_details(live_server):
    lib = Library(live_server.db_path)
    a, b = _seed_tool(lib, "Pd Tool A"), _seed_tool(lib, "Pd Tool B")
    lib.close()
    data = _dict_result(_call_tool(live_server.base_url, live_server.member, "compare_software", {"ids": [a["id"], b["id"]]}))
    assert all(e["program_details"] == [] for e in data["entities"])


# --- warm_intro_available ---------------------------------------------------

def test_warm_intro_available_is_a_boolean_never_the_email(live_server):
    lib = Library(live_server.db_path)
    on = _seed_tool(lib, "Warm On", warm_intro_enabled=1, vendor_email="hello@warm.example", vendor_name="Pat")
    no_email = _seed_tool(lib, "Warm No Email", warm_intro_enabled=1, vendor_email="")
    off = _seed_tool(lib, "Warm Off", warm_intro_enabled=0, vendor_email="x@y.example")
    lib.close()
    get = lambda t: _dict_result(_call_tool(live_server.base_url, live_server.member, "get_software", {"slug_or_id": t["slug"]}))
    assert get(on)["warm_intro_available"] is True
    assert get(no_email)["warm_intro_available"] is False
    assert get(off)["warm_intro_available"] is False
    blob = json.dumps(get(on))
    assert "warm.example" not in blob and "Pat" not in blob.replace("Parity", "")


# --- tool descriptions carry the two sentences ------------------------------

def test_tool_descriptions_explain_citation_markers_and_advisor(live_server):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    import asyncio

    async def go():
        async with streamablehttp_client(f"{live_server.base_url}/mcp",
                                          headers={"Authorization": f"Bearer {live_server.member}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return {t.name: t.description for t in (await s.list_tools()).tools}
    tools = asyncio.run(go())
    for name in ("get_software", "get_community"):
        assert "[n]" in tools[name] and "citations" in tools[name], name
        assert "advisor" in tools[name], name
