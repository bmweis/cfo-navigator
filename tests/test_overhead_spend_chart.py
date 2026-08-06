"""Monthly-spend-by-category stacked bar chart on /admin/overhead-spend and
its /admin/overhead-spend/details full-history page: chart rendering
(webapp.app._overhead_stacked_bar_chart), page layout/ordering, and the
click-to-edit table's "back" redirect (edits/deletes made from the details
page return there, not to the summary page).

See tests/test_manual_overhead.py for manual_overhead_monthly_by_category
itself (the data this chart renders).
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c._db = db
    c._appmod = appmod
    yield c
    if os.path.exists(db):
        os.remove(db)


def _add(client, vendor, date, amount, category="", note=""):
    from linklib.db import Library
    lib = Library(client._db)
    try:
        return lib.add_manual_overhead(vendor, date, amount, category, note)
    finally:
        lib.close()


# -- chart rendering (pure function, no DB) ----------------------------------

def test_chart_renders_no_data_message_when_empty(client):
    appmod = client._appmod
    html = appmod._overhead_stacked_bar_chart(
        {"months": [], "categories": [], "series": {}}, width=420)
    assert "No vendor spend recorded yet" in html


def test_chart_renders_one_rect_per_nonzero_segment(client):
    appmod = client._appmod
    chart_data = {
        "months": ["2026-06", "2026-07"],
        "categories": ["AI & API", "Infrastructure"],
        "series": {"AI & API": [10.0, 0.0], "Infrastructure": [5.0, 3.0]},
    }
    html = appmod._overhead_stacked_bar_chart(chart_data, width=420)
    # 3 nonzero segments total (10, 5, 3) — the zero segment is skipped, not
    # drawn as an empty rect.
    assert html.count("<rect") == 3
    assert "<svg" in html and "</svg>" in html


def test_chart_legend_lists_every_category_even_if_zero_in_some_months(client):
    appmod = client._appmod
    chart_data = {
        "months": ["2026-07"],
        "categories": ["AI & API", "Infrastructure"],
        "series": {"AI & API": [10.0], "Infrastructure": [0.0]},
    }
    html = appmod._overhead_stacked_bar_chart(chart_data, width=420)
    assert "AI &amp; API" in html
    assert "Infrastructure" in html


def test_category_colors_cycle_and_are_from_the_brand_palette(client):
    appmod = client._appmod
    colors = appmod._overhead_category_colors(["A", "B", "C"])
    assert len(colors) == 3
    assert all(c in appmod._OVERHEAD_CHART_PALETTE for c in colors)
    assert len(set(colors)) == 3  # distinct for a small category count
    # cycles once past the palette length
    many = appmod._overhead_category_colors([f"cat{i}" for i in range(len(appmod._OVERHEAD_CHART_PALETTE) + 2)])
    assert many[0] == many[len(appmod._OVERHEAD_CHART_PALETTE)]


# -- page layout ---------------------------------------------------------

def test_summary_page_card_order_chart_then_add_then_csv(client):
    r = client.get("/admin/overhead-spend")
    html = r.text
    i_chart = html.find("Monthly spend by category")
    i_add = html.find(">Add a charge<")
    i_csv = html.find(">Upload CSV<")
    assert i_chart != -1 and i_add != -1 and i_csv != -1
    assert i_chart < i_add < i_csv


def test_summary_page_links_to_details(client):
    r = client.get("/admin/overhead-spend")
    assert "/admin/overhead-spend/details" in r.text


def test_toolbox_usage_left_column_order_then_by_month_on_right(client):
    r = client.get("/admin/overhead-spend")
    html = r.text
    i_est = html.find("Estimated usage, all time")
    i_month_stat = html.find("This calendar month")
    i_by_source = html.find("By source</h3>")
    i_by_month = html.find("By month</h3>")
    assert -1 not in (i_est, i_month_stat, i_by_source, i_by_month)
    # left column stacks in this order
    assert i_est < i_month_stat < i_by_source
    # "By month" heading appears after all three left-column pieces (right column)
    assert i_by_month > i_by_source


# -- details page ---------------------------------------------------------

def test_details_page_lists_every_entry_read_only_with_edit_toggle(client):
    _add(client, "Railway", "2026-07-01", 5.00, "Infrastructure", "Hobby")
    r = client.get("/admin/overhead-spend/details")
    assert r.status_code == 200
    assert "Railway" in r.text
    assert "toggleOverheadEdit" in r.text
    assert 'style="display:none;' in r.text  # edit form starts hidden


def test_details_page_chart_covers_36_months(client):
    appmod = client._appmod
    r = client.get("/admin/overhead-spend/details")
    assert r.status_code == 200
    # sanity: the chart card is present and non-empty even with no data
    assert "Monthly spend by category" in r.text
    assert "Last 36 months" in r.text


def test_edit_from_details_page_redirects_back_to_details(client):
    eid = _add(client, "Railway", "2026-07-01", 5.00, "Infrastructure")
    r = client.post(f"/admin/overhead-spend/{eid}/edit", data={
        "vendor": "Railway", "date": "2026-07-01", "amount": "6.00",
        "category": "Infrastructure", "note": "", "back": "details",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/overhead-spend/details")


def test_edit_without_back_field_redirects_to_summary_page(client):
    eid = _add(client, "Railway", "2026-07-01", 5.00, "Infrastructure")
    r = client.post(f"/admin/overhead-spend/{eid}/edit", data={
        "vendor": "Railway", "date": "2026-07-01", "amount": "6.00",
        "category": "Infrastructure", "note": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/overhead-spend?msg=Saved."


def test_delete_from_details_page_redirects_back_to_details(client):
    eid = _add(client, "Railway", "2026-07-01", 5.00, "Infrastructure")
    r = client.post(f"/admin/overhead-spend/{eid}/delete", data={"back": "details"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/overhead-spend/details")


def test_delete_without_back_field_redirects_to_summary_page(client):
    eid = _add(client, "Railway", "2026-07-01", 5.00, "Infrastructure")
    r = client.post(f"/admin/overhead-spend/{eid}/delete", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/overhead-spend?msg=Deleted."
