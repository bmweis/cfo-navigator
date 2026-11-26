"""Denominator grouping for shift pairing (invented data: "Sample Survey Co", "Example Benchmarks")."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import figures as fg
from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def _gm(y, value, den, dp):
    return frow(source="Example Benchmarks", report=f"Example Benchmarks {y}", year=str(y),
                metric="Gross margin", band="$20M-$50M", value=str(value), unit="percent",
                denominator=den, data_period=dp)


def _pair(lib, den_a, den_b):
    load_report(lib, "eb-2023", [_gm(2023, 70, den_a, "2022")])
    load_report(lib, "eb-2025", [_gm(2025, 76, den_b, "2024")])
    res = fg.find_figures(lib, "Gross margin", band="$30M", all_editions=True, recent_window=True, limit=500)
    return fg.compute_shifts(res["rows"], lib.shift_rules())


def test_gaap_revenue_and_revenue_pair_and_print_each_endpoints_own_basis(lib):
    out = _pair(lib, "revenue", "gaap revenue")
    assert out["shifts"], out["unpaired_groups"]
    line = out["shifts"][0]["line"]
    assert "from 70" in line and "to 76" in line
    assert "'revenue'" in line and "'gaap revenue'" in line      # each endpoint's own stated basis stays visible


def test_tier1_formatting_equivalents_are_one_group():
    same = ["revenue", "% of revenue", "percent of revenue", "as a percent of revenue", "Total Revenue",
            "total revenue line", "Percent  of  REVENUES", "revenue, Q2 2024"]
    groups = {fg.denominator_group(s)[0] for s in same}
    assert len(groups) == 1, groups


def test_synonym_tier_is_labelled():
    assert fg.denominator_group("gaap revenue") == ("revenue", "synonym")
    assert fg.denominator_group("revenue") == ("revenue", "")      # formatting-only: no tier-2 label


@pytest.mark.parametrize("a,b", [
    ("revenue", "arr"), ("percent of revenue", "percent of ARR"), ("mrr", "arr"),
    ("subscription revenue", "revenue"), ("recurring revenue", "revenue"),
    ("gaap subscription revenue", "revenue"), ("gaap recurring revenue", "gaap revenue"),
    ("margin-adjusted CAC", "CAC"), ("gaap arr", "revenue"),
])
def test_never_merge_across_basis(a, b):
    assert fg.denominator_group(a)[0] != fg.denominator_group(b)[0]


def test_never_merge_across_unit_scale(lib):
    # same denominator text, different unit: percent vs count vs multiple are different series keys
    rows = [frow(metric="X", unit=u, denominator="revenue", value="5") for u in ("percent", "count", "x", "months")]
    keys = {fg._unit_key(r["unit"]) for r in rows}
    assert len(keys) == 4


def test_not_stated_and_empty_group_alone():
    ns, e = fg.denominator_group("not stated"), fg.denominator_group("")
    assert ns[1] == "own" and e[1] == "own"
    assert ns[0] not in ("", "revenue") and e[0] == ""
    for stated in ("revenue", "arr", "employees"):
        assert fg.denominator_group(stated)[0] not in (ns[0], e[0])


def test_not_stated_does_not_pair_with_a_stated_basis(lib):
    out = _pair(lib, "not stated", "revenue")
    assert not out["shifts"]


def test_audit_csv_carries_group_and_tier(tmp_path, lib):
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from scripts import audit_benchmark_alignment as ab
    rows, err = fg.parse_registry_tsv("canonical_metric\tcard_url\tcontains\nGross margin\thttps://c.example.test/gm\tContains: gross margin\n")
    assert not err
    lib.seed_metric_registry(rows)
    load_report(lib, "eb-2023", [_gm(2023, 70, "revenue", "2022"), _gm(2023, 71, "gaap revenue", "2022"),
                                 _gm(2023, 72, "% of revenue", "2022"), _gm(2023, 73, "arr", "2022")])
    lib.close()
    out = tmp_path / "audit"
    ab.main(["--db", str(tmp_path / "t.db"), "--out-dir", str(out)])
    head = (out / "denominators.csv").read_text().splitlines()
    assert head[0].split(",")[-2:] == ["group", "tier"], head[0]
    rows = {l.split(",")[1]: l.split(",")[-2:] for l in head[1:]}
    assert rows["gaap revenue"] == ["revenue", "synonym"], rows
    assert rows["% of revenue"] == ["revenue", "mechanical"], rows
    assert rows["arr"][1] == "own", rows
