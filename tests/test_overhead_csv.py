"""CSV parsing for the /admin/overhead-spend "Upload CSV" import path
(linklib.overhead_csv.parse_overhead_csv).

Pure parsing/validation logic only — no DB, no webapp. The route layer is
responsible for actually calling Library.add_manual_overhead with whatever
this returns as "valid_rows"; see tests/test_manual_overhead.py for that
validation. One malformed row must never take down the rest of the batch,
and a broken header must fail loudly rather than guess column order.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.overhead_csv import parse_overhead_csv


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_all_valid_rows_parsed():
    data = _csv(
        "vendor,date,amount,category,note\n"
        "Railway,2026-07-01,5.00,Infrastructure,Hobby plan\n"
        "Anthropic,2026-07-25,42.10,AI & API,\n"
    )
    valid, skipped = parse_overhead_csv(data)
    assert skipped == []
    assert len(valid) == 2
    assert valid[0] == {
        "vendor": "Railway", "date": "2026-07-01", "amount": 5.00,
        "category": "Infrastructure", "note": "Hobby plan",
    }
    assert valid[1]["note"] == ""


def test_header_columns_can_be_in_any_order_and_case_insensitive():
    data = _csv(
        "Amount,Vendor,DATE\n"
        "12.50,Cloudflare,2026-07-05\n"
    )
    valid, skipped = parse_overhead_csv(data)
    assert skipped == []
    assert valid == [{"vendor": "Cloudflare", "date": "2026-07-05", "amount": 12.50,
                       "category": "", "note": ""}]


def test_optional_columns_may_be_omitted_entirely():
    data = _csv("vendor,date,amount\nDomain,2026-07-15,12.00\n")
    valid, skipped = parse_overhead_csv(data)
    assert skipped == []
    assert valid[0]["category"] == ""
    assert valid[0]["note"] == ""


def test_missing_required_column_raises_for_whole_file():
    data = _csv("vendor,amount\nRailway,5.00\n")  # no date column
    with pytest.raises(ValueError, match="date"):
        parse_overhead_csv(data)


def test_empty_file_raises():
    with pytest.raises(ValueError):
        parse_overhead_csv(b"")


def test_bad_row_is_skipped_with_reason_not_whole_batch_failure():
    data = _csv(
        "vendor,date,amount\n"
        "Railway,2026-07-01,5.00\n"
        ",2026-07-02,10.00\n"           # missing vendor
        "OpenAI,not-a-date,3.00\n"      # bad date
        "Exa,2026-07-03,not-a-number\n" # bad amount
        "Cloudflare,2026-07-04,7.00\n"
    )
    valid, skipped = parse_overhead_csv(data)
    assert [r["vendor"] for r in valid] == ["Railway", "Cloudflare"]
    assert len(skipped) == 3
    assert "Vendor is required" in skipped[0]["reason"]
    assert "date" in skipped[1]["reason"].lower()
    assert "number" in skipped[2]["reason"].lower()
    # line numbers count the header as line 1
    assert skipped[0]["line"] == 3


def test_date_accepts_iso_and_us_slash_format():
    data = _csv(
        "vendor,date,amount\n"
        "Railway,2026-07-01,5.00\n"
        "Cloudflare,07/05/2026,3.00\n"
    )
    valid, skipped = parse_overhead_csv(data)
    assert skipped == []
    assert valid[0]["date"] == "2026-07-01"
    assert valid[1]["date"] == "2026-07-05"


def test_date_rejects_other_formats():
    data = _csv("vendor,date,amount\nRailway,July 1 2026,5.00\n")
    valid, skipped = parse_overhead_csv(data)
    assert valid == []
    assert "isn't in YYYY-MM-DD or MM/DD/YYYY format" in skipped[0]["reason"]


def test_amount_strips_dollar_sign_and_thousands_separator():
    data = _csv('vendor,date,amount\nRailway,2026-07-01,"$1,234.56"\n')
    valid, skipped = parse_overhead_csv(data)
    assert skipped == []
    assert valid[0]["amount"] == pytest.approx(1234.56)


def test_blank_lines_are_ignored_not_reported_as_failures():
    data = _csv("vendor,date,amount\nRailway,2026-07-01,5.00\n\n\n")
    valid, skipped = parse_overhead_csv(data)
    assert len(valid) == 1
    assert skipped == []


def test_overlong_vendor_is_skipped():
    data = _csv(f"vendor,date,amount\n{'X' * 121},2026-07-01,5.00\n")
    valid, skipped = parse_overhead_csv(data)
    assert valid == []
    assert "exceeds 120 characters" in skipped[0]["reason"]
