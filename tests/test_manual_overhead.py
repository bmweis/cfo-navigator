"""Vendor-spend ledger for /admin/overhead-spend's "Vendor totals" section
(linklib.db list/add/update/delete_manual_overhead + manual_overhead_total).

Unlike overhead_cost_breakdown (article_embeddings/enrichment_cost/
ask_questions, computed token cost), manual_overhead is hand-typed receipt
amounts — the sole source for "total cost of the site." The two are never
summed together and this table doesn't reconcile against them; that's the
whole point of the design, so there's nothing here pinning any relationship
between the two.
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


def test_add_and_list_entries_newest_first(lib):
    lib.add_manual_overhead("Railway", "2026-07-01", 5.00, "Infrastructure", "Hobby plan")
    lib.add_manual_overhead("Anthropic", "2026-07-25", 42.10, "AI & API")
    rows = lib.list_manual_overhead()
    assert [r["vendor"] for r in rows] == ["Anthropic", "Railway"]
    assert rows[1]["note"] == "Hobby plan"


def test_add_requires_vendor_and_date(lib):
    with pytest.raises(ValueError):
        lib.add_manual_overhead("", "2026-07-01", 5.00)
    with pytest.raises(ValueError):
        lib.add_manual_overhead("Railway", "", 5.00)


def test_total_is_plain_sum_across_all_vendors(lib):
    lib.add_manual_overhead("Railway", "2026-07-01", 5.00, "Infrastructure")
    lib.add_manual_overhead("Cloudflare", "2026-07-05", 0.00, "Infrastructure")
    lib.add_manual_overhead("Anthropic", "2026-07-25", 198.12, "AI & API")
    assert lib.manual_overhead_total() == pytest.approx(203.12)


def test_category_is_display_filter_only_not_a_subtotal_key(lib):
    lib.add_manual_overhead("Railway", "2026-07-01", 5.00, "Infrastructure")
    lib.add_manual_overhead("OpenAI", "2026-08-03", 10.63, "AI & API")
    # filtering changes what's summed/listed, but category never partitions
    # the underlying total on its own — it's just a WHERE clause.
    assert lib.manual_overhead_total(category="Infrastructure") == pytest.approx(5.00)
    assert lib.manual_overhead_total(category="AI & API") == pytest.approx(10.63)
    assert lib.manual_overhead_total() == pytest.approx(15.63)
    assert [r["vendor"] for r in lib.list_manual_overhead(category="AI & API")] == ["OpenAI"]


def test_manual_overhead_categories_lists_distinct_alphabetical(lib):
    lib.add_manual_overhead("Railway", "2026-07-01", 5.00, "Infrastructure")
    lib.add_manual_overhead("Cloudflare", "2026-07-05", 3.00, "Infrastructure")
    lib.add_manual_overhead("Exa", "2026-07-10", 20.00, "AI & API")
    lib.add_manual_overhead("Domain", "2026-07-15", 12.00, "")
    assert lib.manual_overhead_categories() == ["AI & API", "Infrastructure"]


def test_update_entry(lib):
    entry_id = lib.add_manual_overhead("Railway", "2026-07-01", 5.00, "Infrastructure", "Hobby")
    lib.update_manual_overhead(entry_id, "Railway", "2026-07-01", 6.00, "Infrastructure", "Hobby, bumped")
    row = lib.list_manual_overhead()[0]
    assert row["amount"] == pytest.approx(6.00)
    assert row["note"] == "Hobby, bumped"


def test_update_missing_entry_rejected(lib):
    with pytest.raises(ValueError):
        lib.update_manual_overhead(999, "Railway", "2026-07-01", 5.00)


def test_update_requires_vendor_and_date(lib):
    entry_id = lib.add_manual_overhead("Railway", "2026-07-01", 5.00)
    with pytest.raises(ValueError):
        lib.update_manual_overhead(entry_id, "", "2026-07-01", 5.00)
    with pytest.raises(ValueError):
        lib.update_manual_overhead(entry_id, "Railway", "", 5.00)


def test_delete_entry(lib):
    entry_id = lib.add_manual_overhead("Railway", "2026-07-01", 5.00)
    assert lib.delete_manual_overhead(entry_id) is True
    assert lib.list_manual_overhead() == []
    assert lib.manual_overhead_total() == 0.0


def test_delete_unknown_entry_is_noop(lib):
    assert lib.delete_manual_overhead(999) is False


def test_manual_overhead_never_touches_toolbox_usage_ledgers(lib, monkeypatch):
    """The whole point of the redesign: Section 1 (manual_overhead) and
    Section 2 (overhead_cost_breakdown, computed from article_embeddings/
    enrichment_cost/ask_questions) are independent. Adding a vendor entry
    must not perturb the usage-ledger totals, and vice versa."""
    before = lib.overhead_cost_breakdown()
    lib.add_manual_overhead("Anthropic", "2026-07-25", 198.12, "AI & API")
    after = lib.overhead_cost_breakdown()
    assert before == after
