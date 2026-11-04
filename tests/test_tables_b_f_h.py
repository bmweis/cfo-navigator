"""Part B (table label integrity), F (counts and shares in separate tables, a percent of headcount is not
spend) and H (size order). Invented data only."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow  # noqa: E402

from linklib import answer_lint
from linklib import benchmark_prestep as bp
from linklib import figures as fg


def _row(**kw):
    r = frow(**kw)
    r.update({"match": "exact", "gaps": [], "report_slug": "x", "figure_key": 1, "also_printed_in": [], "cited_source": ""})
    return r


EMP = dict(metric="Employees", unit="count", denominator="full-time employees", band="$20-50M")
SENT = [_row(source="Sample Survey Co", report="Sample Survey Co 2024", year="2024", value="200", data_period="Q2 2024", **EMP),
        _row(source="Sample Survey Co", report="Sample Survey Co 2025", year="2025", value="131", data_period="Q2 2025", **EMP)]


def test_a_swapped_edition_label_is_flagged():
    text = ("| Employees | $20-50M | median | 200 | Sample Survey Co 2025 | exact |\n"
            "| Employees | $20-50M | median | 131 | Sample Survey Co 2024 | exact |\n")
    flags = answer_lint.lint_answer(text, [], rows=SENT)
    assert [f["phrase"] for f in flags].count("table label does not match the figure") == 2, flags


def test_a_correct_edition_label_is_not_flagged():
    text = ("| Employees | $20-50M | median | 131 | Sample Survey Co 2025 | exact |\n"
            "| Employees | $20-50M | median | 200 | Sample Survey Co 2024 | exact |\n")
    flags = answer_lint.lint_answer(text, [], rows=SENT)
    assert not any(f["phrase"] == "table label does not match the figure" for f in flags), flags


def test_a_label_check_without_sent_rows_judges_nothing():
    assert answer_lint.label_problems("| Employees | 200 | Sample Survey Co 2025 |", None) == []


def test_a_percent_of_headcount_row_is_headcount_mix_never_spend():
    mix = _row(metric="Engineering headcount mix", unit="%", denominator="percent of headcount", value="32")
    spend = _row(metric="S&M expense", unit="%", denominator="percent of revenue", value="38")
    count = _row(value="131", **EMP)
    assert fg.row_table_kind(mix) == "headcount_share"
    assert fg.row_table_kind(spend) == "spend_share"
    assert fg.row_table_kind(count) == "headcount_count"
    assert "table: headcount mix" in bp._row_line(mix) and "table: spend" not in bp._row_line(mix)


def test_counts_and_shares_are_told_to_be_separate_tables():
    mix = _row(metric="Engineering headcount mix", unit="%", denominator="percent of headcount", value="32")
    note = bp.table_kinds_note([mix, _row(value="131", **EMP)])
    assert "Separate tables" in note and "never share a column" in note
    assert bp.table_kinds_note([_row(value="131", **EMP)]) == ""


def test_bands_are_listed_smallest_to_largest():
    rows = [_row(band=b, **{k: v for k, v in EMP.items() if k != "band"}) for b in ("$50-100M", "$10-20M", "$20-50M")]
    note = bp.band_order_note(rows)
    assert note.index("$10-20M") < note.index("$20-50M") < note.index("$50-100M"), note
    assert fg.band_sort_key("$5M") < fg.band_sort_key("$10-20M") < fg.band_sort_key("$100M+") < fg.band_sort_key("All")
