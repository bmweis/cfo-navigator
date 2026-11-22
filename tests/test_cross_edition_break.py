"""A chart whose summed total moves between editions is not paired across them (Mostly Metrics sales spend,
ask_questions ids 78 and 79 shape). Invented publishers and figures only: "Sample Survey Co" prints a
"Spend by ARR band" chart of six function lines, percent of revenue, in three editions."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

BANDS = ["$1M-$5M", "$5M-$25M", "$25M-$50M", "$50M+"]
# summed totals per band, by edition
TOT = {"jul": [136, 130, 134, 114], "feb": [138, 128, 130, 112], "may": [100, 100, 92, 87],
       "apr": [136, 130, 134, 114], "nov": [137, 129, 132, 113]}
SALES = {"jul": [48, 42, 40, 40], "feb": [48, 40, 38, 38], "may": [30, 22, 17, 14],
         "apr": [48, 42, 40, 40], "nov": [48, 41, 39, 39]}
MKT = {"jul": 20, "feb": 19, "may": 11, "apr": 20, "nov": 19}
ENG = {"jul": 32, "feb": 31, "may": 22, "apr": 32, "nov": 31}
EDITIONS = {
    "jul": ("sss-jul25", "Sample Survey Club July 2025 edition", "2025", "CY2024"),
    "feb": ("sss-feb26", "Sample Survey Club February 2026 edition", "2026", "CY2025"),
    "may": ("sss-may26", "Sample Survey Club May 2026 edition", "2026", "LTM Q1 2026"),
}


def _chart(ed, source="Sample Survey Co", drop=(), eds=None):
    slug, title, year, dp = (eds or EDITIONS)[ed]
    rows = []
    for i, band in enumerate(BANDS):
        total, sales = TOT[ed][i], SALES[ed][i]
        fixed = {"Sales": sales, "Marketing": MKT[ed], "Engineering": ENG[ed], "Product": 10, "Support": 8}
        fixed["G&A"] = total - sum(fixed.values())
        assert fixed["G&A"] > 0
        for fn, v in fixed.items():
            if fn in drop:
                continue
            rows.append(frow(report=title, year=year, source=source, metric=f"spend: {fn}", band=band, basis="ARR",
                             segment="private", denominator="percent of revenue", statistic="other", value=str(v),
                             unit="%", n="", page="p. 26", data_period=dp))
    return slug, title, year, rows


REG = "canonical_metric\tcard_url\tcontains\nSales spend\thttps://c.example.test/s\tContains: sales spend\n"


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    r, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(r)
    yield db
    db.close()


def _load(lib, eds, **kw):
    for ed in eds:
        slug, title, year, rows = _chart(ed, **kw)
        load_report(lib, slug, rows, report=title, year=year)


def _shifts(lib, metric, band):
    res = fg.find_figures(lib, metric, band=band, all_editions=True, recent_window=True, limit=500)
    kw = {}
    if hasattr(fg, "load_stack_rows"):
        kw["stack_rows"] = fg.load_stack_rows(lib, res["rows"])
    return fg.compute_shifts(res["rows"], lib.shift_rules(), **kw)


def test_sales_not_paired_across_the_feb_to_may_break_and_note_prints(lib):
    _load(lib, ["jul", "feb", "may"])
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    lines = [s["line"] for s in out["shifts"]]
    assert not any("Sales" in ln for ln in lines), lines          # nothing prints for July to May (nor July to Feb)
    notes = out.get("breaks") or []
    assert len(notes) == 1
    n = notes[0]
    assert n["kind"] == "break" and n["from"]["report_slug"] == "sss-feb26" and n["to"]["report_slug"] == "sss-may26"
    assert "from 130 to 92 percent of revenue" in n["line"]        # Feb 130, May 92 for this band
    assert "Feb 2026" in n["line"] and "May 2026" in n["line"] and "p. 26" in n["line"]
    assert "mean" not in n["line"] and "median" not in n["line"]      # no cause named
    assert any("chart total moved between editions" in s for s in out["skipped"])


def test_default_newest_edition_alone_prints_no_july_to_feb_shift(lib):
    _load(lib, ["jul", "feb", "may"])
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    assert out["shifts"] == []


def test_july_and_feb_only_pair_normally_no_note(lib):
    _load(lib, ["jul", "feb"])
    out = _shifts(lib, "spend: Sales", "$5M-$25M")
    assert not out.get("breaks")
    # a 2-point move is under the shift threshold, so no line; but nothing is blocked
    assert not any("chart total" in s for s in out["skipped"])


def test_marketing_and_engineering_blocked_by_the_total(lib):
    _load(lib, ["jul", "feb", "may"])
    for metric in ("spend: Marketing", "spend: Engineering"):
        out = _shifts(lib, metric, "$25M-$50M")
        assert out["shifts"] == [], metric
        assert len(out.get("breaks") or []) == 1


def test_missing_totals_fail_open_and_say_so(lib):
    _load(lib, ["jul", "feb"])
    slug, title, year, rows = _chart("may", drop=("Support",))       # May has no Support line: no total
    load_report(lib, slug, rows, report=title, year=year)
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    assert not out.get("breaks")
    assert any("Sales" in s["line"] for s in out["shifts"])            # paired July (or Feb) to May as before
    assert any("break check not possible" in w for w in out["outliers"])


def test_another_publisher_is_unaffected(lib):
    _load(lib, ["jul", "feb", "may"])
    # a second publisher prints two editions of the same chart with a steady total
    for ed, sales in (("jul", 50), ("may", 30)):
        slug, title, year, rows = _chart(ed, source="Other Survey Co")
        rows = [dict(r, report=title.replace("Sample", "Other"), report_year=year) for r in rows]
        for r in rows:
            if r["metric"] == "spend: Sales" and r["band"] == "$25M-$50M":
                r["value"], r["value_num"] = str(sales), float(sales)
            if r["metric"] == "spend: G&A":
                r["value"], r["value_num"] = "1", 1.0
        load_report(lib, "oth-" + ed, rows, report=title.replace("Sample", "Other"), year=year, source="Other Survey Co")
    totals = fg.stack_totals(fg.load_stack_rows(lib, fg.find_figures(lib, "spend: Sales", band="$25M-$50M", all_editions=True, recent_window=True, limit=500)["rows"]))
    assert {t["key"][0] for t in totals} >= {"other survey co", "sample survey co"}


def test_same_report_pairs_unaffected(lib):
    # one report prints the same line for two periods: the within-report path never consults the stack
    rows = [frow(report="Sample Survey Club May 2026 edition", year="2026", source="Sample Survey Co", metric="spend: Sales",
                 band="$25M-$50M", basis="ARR", segment="private", denominator="percent of revenue", statistic="other",
                 value=str(v), unit="%", n="120", page="p. 26", data_period=dp) for v, dp in ((40, "2023"), (20, "2025"))]
    load_report(lib, "sss-one", rows)
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    assert len(out["shifts"]) == 1 and not out.get("breaks")


def test_stack_totals_need_all_six_functions():
    rows = _chart("may")[3]
    for r in rows:
        r["report_slug"] = "x"
    assert len(fg.stack_totals(rows)) == 4
    assert fg.stack_totals([r for r in rows if r["metric"] != "spend: Support"]) == []


def test_prestep_puts_the_break_note_first_and_in_the_skipped_list(lib):
    _load(lib, ["jul", "feb", "may"])
    sub = bp.Sub(text="how has sales spend changed at $25M-$50M ARR", intent="benchmark", metrics=["spend: Sales"], band="$25M-$50M")
    ps = bp.Prestep()
    block = bp._shift_notes(lib, ps, [sub])
    assert ps.shifts and ps.shifts[0]["kind"] == "break"
    assert "Line changes are not computed across these editions" in block
    assert any("chart total moved between editions" in s for s in ps.shifts_skipped)
    log = ps.to_dict() if hasattr(ps, "to_dict") else {}
    if log:
        assert log["shifts"][0]["kind"] == "break"


# ---- the real Mostly Metrics edition titles, slugs and years (figures invented) -------------------------------
# The May 2026 title carries a "Mostly Metrics" prefix the others lack, and its slug (1q26) sorts BEFORE 2q25, so a slug
# fallback would put May before February. Order must come from the month the title prints.
MM = {
    "apr": ("mm-benchmarking-1q25", "Benchmarking Club April 2025 edition", "2025", "CY2024"),
    "jul": ("mm-benchmarking-2q25", "Benchmarking Club July 2025 edition", "2025", "CY2024"),
    "nov": ("mm-benchmarking-3q25", "Benchmarking Club November 2025 edition", "2025", "CY2025"),
    "feb": ("mm-benchmarking-4q25", "Benchmarking Club February 2026 edition", "2026", "CY2025"),
    "may": ("mm-benchmarking-1q26", "Mostly Metrics Benchmarking Club May 2026 edition", "2026", "LTM Q1 2026"),
}
MM_SLUG_ORDER = ["mm-benchmarking-1q25", "mm-benchmarking-1q26", "mm-benchmarking-2q25",
                 "mm-benchmarking-3q25", "mm-benchmarking-4q25"]          # what a slug fallback would produce


def _load_mm(lib, eds):
    for ed in eds:
        slug, title, year, rows = _chart(ed, source="Mostly Metrics", eds=MM)
        load_report(lib, slug, rows, report=title, year=year)


def test_real_titles_order_editions_by_the_month_in_the_title():
    reps = [{"report": t, "report_year": y, "data_period": dp, "report_slug": slug} for slug, t, y, dp in MM.values()]
    by_slug = sorted(reps, key=lambda r: r["report_slug"])
    assert [r["report_slug"] for r in by_slug] == MM_SLUG_ORDER        # May (1q26) sorts before July by slug alone
    got = [r["report_slug"] for r in sorted(reps, key=fg._edition_order)]
    print("edition order:", got)
    assert got == ["mm-benchmarking-1q25", "mm-benchmarking-2q25", "mm-benchmarking-3q25",
                   "mm-benchmarking-4q25", "mm-benchmarking-1q26"]


def test_real_titles_guard_output_and_newest_side_is_may_alone(lib):
    _load_mm(lib, ["jul", "feb", "may"])
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    print("note:", [b["line"] for b in out["breaks"]])
    print("skipped:", out["skipped"])
    assert out["shifts"] == []                                          # no Sales shift prints; July to Feb is not the latest
    assert len(out["breaks"]) == 1
    b = out["breaks"][0]
    assert b["from"]["report_slug"] == "mm-benchmarking-4q25" and b["to"]["report_slug"] == "mm-benchmarking-1q26"
    assert "Feb 2026" in b["line"] and "May 2026" in b["line"]
    assert "summed spend lines" in b["line"] and "printed spend totals" not in b["line"]
    assert any("chart total moved between editions" in s for s in out["skipped"])


def test_real_titles_all_five_editions_pair_up_to_feb_and_block_at_may(lib):
    _load_mm(lib, ["apr", "jul", "nov", "feb", "may"])
    out = _shifts(lib, "spend: Sales", "$25M-$50M")
    assert [b["from"]["report_slug"] for b in out["breaks"]] == ["mm-benchmarking-4q25"]
    assert out["shifts"] == []
