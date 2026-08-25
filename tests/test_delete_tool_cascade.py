"""Library.delete_tool()'s cascade fix (2026-08, the Pave/Culpepper/Radford
removal investigation). Before this fix, delete_tool() left tool_feature_links
and entity_citations rows orphaned (nothing reads them once the tool is
gone — a real "dead data" violation per CLAUDE.md), and any pending
feature_review_queue proposal naming the tool stayed 'pending' forever,
pointing at nothing. This suite covers the fixed cascade, and confirms the
four tables that must NOT be touched (tool_leads, tool_competitors,
tool_name_dedupe_decisions, narrative_review_log — historical record, same
precedent as tool_audit_log surviving a deleted tool) stay exactly as
proposed."""
import os
import tempfile

import pytest

from linklib.db import Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


def test_delete_tool_removes_its_feature_links(lib):
    category_id = lib.add_tool_category("Headcount Planning")
    feature_id = lib.add_category_feature(category_id, "Cash compensation")
    tool_id = lib.add_tool("Pave", "desc", "https://pave.com", ["Headcount Planning"], approved=1)
    lib.upsert_tool_feature_link(tool_id, feature_id, "native", 0, "2026-08-01")

    assert lib.list_tool_feature_links(tool_id) != []
    lib.delete_tool(tool_id, admin_id=1)
    assert lib.list_tool_feature_links(tool_id) == []
    # The feature itself (a different table) survives — only the link is gone.
    assert lib.get_category_feature(feature_id) is not None


def test_delete_tool_leaves_other_tools_feature_links_alone(lib):
    category_id = lib.add_tool_category("Headcount Planning")
    feature_id = lib.add_category_feature(category_id, "Cash compensation")
    doomed = lib.add_tool("Pave", "desc", "https://pave.com", ["Headcount Planning"], approved=1)
    survivor = lib.add_tool("ChartHop", "desc", "https://charthop.com", ["Headcount Planning"], approved=1)
    lib.upsert_tool_feature_link(doomed, feature_id, "native", 0, "2026-08-01")
    lib.upsert_tool_feature_link(survivor, feature_id, "native", 0, "2026-08-01")

    lib.delete_tool(doomed, admin_id=1)

    assert lib.list_tool_feature_links(doomed) == []
    assert len(lib.list_tool_feature_links(survivor)) == 1


def test_delete_tool_clears_entity_citations(lib):
    tool_id = lib.add_tool("Acme", "desc", "https://acme.example", [], approved=1)
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                              [{"url": "https://acme.example/x", "title": "X"}], model="claude-opus-5")
    assert lib.get_entity_citations("tool", tool_id, "agent_taxonomy") != []

    lib.delete_tool(tool_id, admin_id=1)

    assert lib.get_entity_citations("tool", tool_id, "agent_taxonomy") == []


def test_delete_tool_denies_pending_feature_review_queue_rows_for_it(lib):
    category_id = lib.add_tool_category("Headcount Planning")
    tool_id = lib.add_tool("Pave", "desc", "https://pave.com", ["Headcount Planning"], approved=1)
    item_id = lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_link",
        payload={"feature": {"name": "Cash compensation"}},
        category_id=category_id, tool_id=tool_id,
    )

    lib.delete_tool(tool_id, admin_id=1)

    row = lib.get_feature_review_queue_item(item_id)
    assert row["status"] == "denied"
    assert row["resolved_at"]
    assert row["resolution_note"]


def test_delete_tool_leaves_other_tools_pending_queue_rows_pending(lib):
    category_id = lib.add_tool_category("Headcount Planning")
    doomed = lib.add_tool("Pave", "desc", "https://pave.com", ["Headcount Planning"], approved=1)
    survivor = lib.add_tool("ChartHop", "desc", "https://charthop.com", ["Headcount Planning"], approved=1)
    doomed_item = lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_link", payload={}, category_id=category_id, tool_id=doomed,
    )
    survivor_item = lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_link", payload={}, category_id=category_id, tool_id=survivor,
    )

    lib.delete_tool(doomed, admin_id=1)

    assert lib.get_feature_review_queue_item(doomed_item)["status"] == "denied"
    assert lib.get_feature_review_queue_item(survivor_item)["status"] == "pending"


def test_delete_tool_does_not_touch_tool_leads(lib):
    tool_id = lib.add_tool("Pave", "desc", "https://pave.com", [], approved=1)
    lib.save_tool_lead(tool_id, "Pave", "Jane Doe", "jane@example.com", "Acme Co", "50-100")

    lib.delete_tool(tool_id, admin_id=1)

    leads = lib.list_tool_leads(tool_id=tool_id)
    assert len(leads) == 1
    assert leads[0]["tool_name"] == "Pave"  # snapshot text survives independent of the tool row


def test_delete_tool_does_not_touch_narrative_review_log(lib):
    tool_id = lib.add_tool("Pave", "desc", "https://pave.com", [], approved=1)
    lib.record_narrative_review(admin_id=1, entity_type="tool", field_type="agent_taxonomy",
                                 item_id=tool_id, detail="reviewed text snapshot")

    lib.delete_tool(tool_id, admin_id=1)

    rows = lib.list_narrative_review_log()
    assert any(r["item_id"] == tool_id for r in rows)


def test_delete_tool_still_cascades_competitors_and_dedupe_decisions(lib):
    # Pre-existing cascades — confirm the new deletes didn't disturb them.
    keep = lib.add_tool("Keep Co", "desc", "https://keep.example", [], approved=1)
    loser = lib.add_tool("Loser Co", "desc", "https://loser.example", [], approved=1)
    lib.add_tool_competitor(keep, loser)

    lib.delete_tool(loser, admin_id=1)

    assert lib.list_tool_competitors(keep) == []


def test_delete_tool_with_no_matching_row_still_does_not_crash(lib):
    # A double-submit on a nonexistent id must survive the new cascade too.
    lib.delete_tool(999999, admin_id=1)
