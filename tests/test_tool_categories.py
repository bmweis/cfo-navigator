"""CFO Toolbox category vocabulary (linklib.db list/add/rename/delete_tool_category).

Unlike article tags, tool categories are a curated list independent of usage —
a category can exist with zero tools tagged to it. Renaming cascades onto every
tool that has it; deleting strips the tag but leaves the tool in the directory
(it just falls back to showing under "All" instead of a specific pill).
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_add_and_list_categories(lib):
    lib.add_tool_category("Billing", "Invoicing and recurring revenue.")
    lib.add_tool_category("FP&A")
    cats = lib.list_tool_categories()
    assert [c["name"] for c in cats] == ["Billing", "FP&A"]
    assert cats[0]["description"] == "Invoicing and recurring revenue."
    assert cats[0]["tool_count"] == 0   # exists before any tool uses it


def test_add_duplicate_name_rejected_case_insensitive(lib):
    lib.add_tool_category("Billing")
    with pytest.raises(ValueError):
        lib.add_tool_category("billing")


def test_add_empty_name_rejected(lib):
    with pytest.raises(ValueError):
        lib.add_tool_category("   ")


def test_add_reserved_uncategorized_name_rejected_case_insensitive(lib):
    # "Uncategorized" is the directory's built-in hygiene filter (Phase J3,
    # the __uncategorized__ sentinel in the /tools/software and
    # /admin/tools/software pill filters) — a real category with this name
    # would be indistinguishable from it, so creation is blocked.
    with pytest.raises(ValueError):
        lib.add_tool_category("Uncategorized")
    with pytest.raises(ValueError):
        lib.add_tool_category("uncategorized")
    with pytest.raises(ValueError):
        lib.add_tool_category("  UNCATEGORIZED  ")


def test_rename_to_reserved_uncategorized_name_rejected(lib):
    cat_id = lib.add_tool_category("Billing")
    with pytest.raises(ValueError):
        lib.rename_tool_category(cat_id, "Uncategorized")


def test_tool_count_reflects_usage(lib):
    lib.add_tool_category("Billing")
    lib.add_tool_category("Procurement")
    lib.add_tool(name="Ramp", description="Spend mgmt", url="https://ramp.com",
                 categories=["Billing"], approved=1)
    lib.add_tool(name="Zip", description="Procurement", url="https://ziphq.com",
                 categories=["Billing", "Procurement"], approved=1)
    counts = {c["name"]: c["tool_count"] for c in lib.list_tool_categories()}
    assert counts["Billing"] == 2
    assert counts["Procurement"] == 1


def test_rename_cascades_to_tools(lib):
    cat_id = lib.add_tool_category("Sales Tax")
    lib.add_tool(name="Avalara", description="Tax", url="https://avalara.com",
                 categories=["Sales Tax"], approved=1)
    lib.add_tool(name="Other", description="Unrelated", url="https://other.com",
                 categories=["Billing"], approved=1)
    n = lib.rename_tool_category(cat_id, "Sales & VAT Tax", "Updated description")
    assert n == 1   # only the tool tagged with the old name changed
    cats = {c["id"]: c for c in lib.list_tool_categories()}
    assert cats[cat_id]["name"] == "Sales & VAT Tax"
    assert cats[cat_id]["description"] == "Updated description"
    tools = {t["name"]: t["categories"] for t in lib.list_tools(approved_only=True)}
    assert tools["Avalara"] == ["Sales & VAT Tax"]
    assert tools["Other"] == ["Billing"]   # untouched


def test_rename_to_existing_name_rejected(lib):
    lib.add_tool_category("Billing")
    cat_id = lib.add_tool_category("Procurement")
    with pytest.raises(ValueError):
        lib.rename_tool_category(cat_id, "Billing")


def test_rename_missing_category_rejected(lib):
    with pytest.raises(ValueError):
        lib.rename_tool_category(999, "Nonexistent")


def test_delete_strips_tag_but_keeps_tool(lib):
    cat_id = lib.add_tool_category("Procurement")
    tool_id = lib.add_tool(name="Zip", description="Procurement intake", url="https://ziphq.com",
                            categories=["Procurement"], approved=1)
    n = lib.delete_tool_category(cat_id)
    assert n == 1
    assert lib.list_tool_categories() == []
    tool = lib.get_tool(tool_id)
    assert tool is not None                 # tool itself is untouched
    assert tool["categories"] == []          # just untagged — shows under "All" only


def test_delete_only_affects_tools_that_have_it(lib):
    keep_id = lib.add_tool_category("Billing")
    drop_id = lib.add_tool_category("Procurement")
    lib.add_tool(name="Coupa", description="Both", url="https://coupa.com",
                 categories=["Billing", "Procurement"], approved=1)
    lib.delete_tool_category(drop_id)
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["categories"] == ["Billing"]
    assert [c["id"] for c in lib.list_tool_categories()] == [keep_id]


def test_delete_unknown_category_is_noop(lib):
    assert lib.delete_tool_category(999) == 0


# -- read-time alphabetical sort ---------------------------------------------
# Every reader of a tool's categories (card grid, profile page, compare
# matrix, admin table) must see them alphabetized regardless of the order
# they were saved in — Library._tool_to_dict is the one choke point every
# read path goes through, so the sort lives there rather than depending on
# every write path (add/update/quick-edit/bulk-edit) to sort before saving.

def test_get_tool_sorts_categories_alphabetically(lib):
    tool_id = lib.add_tool("Ramp", "Spend management", "https://ramp.com",
                           ["Revenue", "Accounting", "FP&A"], approved=1)
    assert lib.get_tool(tool_id)["categories"] == ["Accounting", "FP&A", "Revenue"]


def test_list_tools_sorts_categories_alphabetically(lib):
    lib.add_tool("Ramp", "Spend management", "https://ramp.com",
                ["Revenue", "Accounting", "FP&A"], approved=1)
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["categories"] == ["Accounting", "FP&A", "Revenue"]


def test_update_tool_out_of_order_still_reads_sorted(lib):
    tool_id = lib.add_tool("Ramp", "Spend management", "https://ramp.com", [], approved=1)
    lib.update_tool(tool_id, "Ramp", "Spend management", "https://ramp.com",
                    ["Tax Management", "BI/Analytics"])
    assert lib.get_tool(tool_id)["categories"] == ["BI/Analytics", "Tax Management"]
