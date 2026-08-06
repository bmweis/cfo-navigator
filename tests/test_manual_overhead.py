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


# -- manual_overhead_monthly_by_category (stacked-bar chart data) -----------

def _this_month():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


def test_monthly_by_category_zero_fills_months_with_no_data(lib):
    result = lib.manual_overhead_monthly_by_category(months=3)
    assert len(result["months"]) == 3
    assert result["months"][-1] == _this_month()
    assert result["categories"] == []
    assert result["series"] == {}


def test_monthly_by_category_sums_within_current_month(lib):
    today = _this_month()
    lib.add_manual_overhead("Railway", f"{today}-01", 5.00, "Infrastructure")
    lib.add_manual_overhead("Cloudflare", f"{today}-02", 3.00, "Infrastructure")
    lib.add_manual_overhead("Anthropic", f"{today}-03", 20.00, "AI & API")
    result = lib.manual_overhead_monthly_by_category(months=1)
    assert result["months"] == [today]
    # largest total first: AI & API (20.00) beats Infrastructure (5+3=8.00)
    assert result["categories"] == ["AI & API", "Infrastructure"]
    assert result["series"]["Infrastructure"] == [8.00]
    assert result["series"]["AI & API"] == [20.00]


def test_monthly_by_category_blank_category_becomes_uncategorized(lib):
    today = _this_month()
    lib.add_manual_overhead("Domain", f"{today}-01", 12.00, "")
    result = lib.manual_overhead_monthly_by_category(months=1)
    assert result["categories"] == ["Uncategorized"]
    assert result["series"]["Uncategorized"] == [12.00]


def test_monthly_by_category_excludes_rows_before_the_window(lib):
    today = _this_month()
    lib.add_manual_overhead("OldVendor", "2020-01-15", 999.00, "Ancient")
    lib.add_manual_overhead("Railway", f"{today}-01", 5.00, "Infrastructure")
    result = lib.manual_overhead_monthly_by_category(months=1)
    assert "Ancient" not in result["categories"]
    assert result["categories"] == ["Infrastructure"]


def test_monthly_by_category_series_length_matches_months_param(lib):
    result = lib.manual_overhead_monthly_by_category(months=12)
    assert len(result["months"]) == 12
    result36 = lib.manual_overhead_monthly_by_category(months=36)
    assert len(result36["months"]) == 36
    assert result36["months"][-1] == _this_month()
