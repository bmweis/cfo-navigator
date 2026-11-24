"""The newest edition must not be dropped from shifts (invented data: "Sample Survey Co")."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import figures as fg
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Total headcount\thttps://c.example.test/hc\tContains: headcount, total employees, employees\n")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


def _emp(y, value, dp):
    return frow(source="Sample Survey Co", report=f"Sample Survey {y}", year=str(y), metric="Employees",
                band="$20M-$50M", value=str(value), unit="count", data_period=dp)


def test_newest_edition_point_is_paired_alongside_the_within_report_history(lib):
    load_report(lib, "s-2025", [_emp(2025, 225, "Q2 2022"), _emp(2025, 164, "Q2 2023"),
                                _emp(2025, 200, "Q2 2024"), _emp(2025, 131, "Q2 2025")])
    load_report(lib, "s-2026", [_emp(2026, 99, "Q2 2026")])
    res = fg.find_figures(lib, "Employees", band="$30M", all_editions=True, recent_window=True, limit=500)
    out = fg.compute_shifts(res["rows"], lib.shift_rules())
    lines = [s["line"] for s in out["shifts"]]
    assert any("from 225" in l and "to 131" in l for l in lines), lines      # within-report history kept
    assert any("from 131" in l and "to 99" in l for l in lines), lines       # newest edition included


def test_a_role_headcount_is_not_a_total_headcount_row():
    for name in ("AE headcount", "CS headcount"):
        assert fg.is_total_fte_row({"metric": name}) is False, name
    for name in ("Total employees", "Headcount: Total", "FTEs", "No. of employees"):
        assert fg.is_total_fte_row({"metric": name}) is True, name


def test_ae_headcount_row_stays_out_of_the_total_headcount_table():
    from linklib import table_render as tr
    ae = {"metric": "AE headcount", "unit": "count", "value_num": 7.0, "value": "7", "denominator": "", "data_period": "2024",
          "statistic": "median", "band": "$20M-$50M", "report_slug": "k-25", "report_year": "2025", "source": "Sample Survey Co"}
    assert tr.count_rows([ae]) == []


def _sp(**kw):
    from test_table_render import _spend_row
    return _spend_row(**kw)


def test_ranges_count_a_publisher_once_and_never_pool_unbounded_bands():
    from linklib import table_render as tr
    rows = [_sp(figure_key=1, source="Example Capital", report_year="2026", value="35", band="$20M-$30M"),
            _sp(figure_key=2, source="Example Capital", report_year="2024", value="60", band="$20M-$30M"),   # same publisher, older edition
            _sp(figure_key=3, source="Sample Survey Co", report_year="2025", value="45", band="$20M-$30M"),
            _sp(figure_key=4, source="Example Index", report_year="2025", value="10", band="<$100M"),
            _sp(figure_key=5, source="Example Big", report_year="2025", value="90", band="$100M and above")]
    cites = {r["figure_key"]: i for i, r in enumerate(rows, 1)}
    md, meta = tr.build_spend(rows, lambda r: cites[r["figure_key"]])
    rng = [l for l in md.split("\n") if l.startswith("- S&M")]
    assert len(rng) == 1, rng
    assert "35% to 45% (2 sources)" in rng[0], rng[0]            # 60 is Example Capital's older edition, not a third source
    assert "10" not in rng[0].split("sources")[0] and "90" not in rng[0]


def test_bands_comparable_requires_bounded_bands():
    assert fg.bands_comparable("$20M-$50M", "$25M-$50M") is True
    assert fg.bands_comparable("<$100M", "$20M-$50M") is False
    assert fg.bands_comparable("$50M+", "$100M+") is False
    assert fg.bands_comparable("$50M+", "$50M+") is True
