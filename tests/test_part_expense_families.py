"""A splitter part that names S&M, R&D and G&A looks up those lines, not total operating expenses.
Invented data only. The prior metrics are NOT inherited into the lookup (Killed, #795)."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402
from test_split_guards import SETTINGS, fake_client, _part  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Operating Expenses\thttps://c.example.test/o\tContains: operating expenses, opex\n"
       "Total Employees\thttps://c.example.test/h\tContains: total employees, headcount\n")
Q = ("At $20-$30M ARR, how has headcount and spend changed? Which of those lines, Sales and marketing, R&D and G&A "
     "as a percent of revenue, is growing fastest over the past few years?")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    for yr in ("2024", "2025"):
        load_report(db, f"eb-{yr}", [
            frow(source="Example Benchmarks", report=f"Example Benchmarks {yr}", year=yr, data_period=f"CY-{int(yr)-1}",
                 band="$20M-$30M", segment="All", metric=m, denominator="percent of revenue", value="30", unit="percent")
            for m in ("Operating Expenses", "S&M expense", "R&D expense", "G&A expense")])
    return db


def test_part_text_with_named_functions_looks_up_those_lines_not_total_opex(lib):
    payload = {"subqueries": [
        _part("benchmark", "Operating Expenses", "Sales and marketing, R&D and G&A expense as a percent of revenue at $20-$30M",
              segment="", year="all"),
        _part("benchmark", "Total Employees", "Total employees at $20-$30M", segment="")], "rest_of_question": ""}
    ps = bp.plan_turn(lib, Q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6",
                      remaining_usd=None, client_factory=fake_client(payload))
    p1 = ps.subs[0].metrics
    assert "Operating Expenses" not in p1, p1
    assert any("Sales and marketing" in m for m in p1) and any("General and administrative" in m for m in p1), p1
    assert ps.subs[1].metrics == ["Total Employees"]

