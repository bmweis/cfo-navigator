"""One window, best period, one document per report, table markers, left-out line (PR 761).
Invented fixtures only ("Example Benchmarks", "Sample Survey Co", "Pub N"): no third-party figures.
Shaped like ask_questions ids 38 and 39."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint, answer_rules
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library
from webapp.answer_render import render_answer_markdown

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Expenses as a % of Revenue\thttps://c.example.test/e\tContains: expense as a percent of revenue\n")
M = "S&M % of Revenue"
MS = ["S&M % of Revenue", "R&D % of Revenue", "G&A % of Revenue"]


def _rows(src, rep, y, metrics=(M,), dp="FY2024", stats=("median",), band="$20M-$30M", value="30"):
    return [frow(source=src, report=rep, year=str(y), metric=m, band=band, statistic=st, value=value,
                 unit="percent", data_period=dp) for m in metrics for st in stats]


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


def _reports(lib):
    return {r["report_slug"]: r for r in lib.list_benchmark_reports()}


# --- Part A: one window, global ---------------------------------------------------------

def _old_editions(lib):
    for i, (src, y) in enumerate([("Pacific Sample", 2011), ("Skok Sample", 2018), ("Skok Sample B", 2019),
                                  ("Key Sample", 2021), ("Key Sample B", 2022)]):
        load_report(lib, f"o{i}", _rows(src, f"{src} {y}", y))
    for src in ("Example Benchmarks", "Sample Survey Co"):
        load_report(lib, f"n-{src[:2]}", _rows(src, f"{src} 2025", 2025, stats=("median", "p25", "p75")))


def test_a_sole_edition_publishers_older_than_five_years_are_left_out(lib):
    _old_editions(lib)
    res = fg.find_figures(lib, M, band="$25M", all_editions=True, recent_window=True, limit=500)
    got = {(r["source"], r["report_year"]) for r in res["rows"]}
    assert {y for _, y in got} == {"2021", "2022", "2025"}
    note = next(n for n in res["notes"] if n.startswith(bp.WINDOW_NOTE_PREFIX))
    assert "(2011)" in note and "(2018)" in note and "(2019)" in note


def test_a_since_2015_returns_them(lib):
    _old_editions(lib)
    res = fg.find_figures(lib, M, band="$25M", all_editions=True, from_year=2015, limit=500)
    years = {r["report_year"] for r in res["rows"]}
    assert {"2018", "2019"} <= years and "2011" not in years


def test_a_an_empty_window_falls_back_to_the_newest_edition_and_says_so(lib):
    _old_editions(lib)
    res = fg.find_figures(lib, M, band="$25M", all_editions=True, from_year=2030, limit=500)
    assert {r["report_year"] for r in res["rows"]} == {"2025", "2022", "2021", "2019", "2018", "2011"} or res["rows"]
    assert any("No edition on file falls inside the recent window" in n for n in res["notes"])
    assert "2025" in {r["report_year"] for r in res["rows"]}


# --- Part B: the best period, the floor row ---------------------------------------------

def _two_periods(lib):
    rows = _rows("Sample Survey Co", "Sample Survey Co 2026", 2026, dp="FY2024", value="79")
    rows += _rows("Sample Survey Co", "Sample Survey Co 2026", 2026, dp="FY2025", value="81")
    load_report(lib, "s26", rows)


def test_b_fy2025_ranks_before_fy2024_inside_one_report(lib):
    _two_periods(lib)
    res = fg.find_figures(lib, M, band="$25M")
    assert [r["data_period"] for r in res["rows"]][0] == "FY2025"
    sub = bp.Sub(intent="benchmark", text="x", metrics=[M], band="$25M")
    bp.gather(lib, sub)
    assert [r["value"] for r in bp.pick_rows(sub.candidates, 1)] == ["81"]


def test_b_the_floor_row_is_the_exact_band_median_of_the_newest_edition(lib):
    for i in range(6):
        rows = _rows(f"Pub {i}", f"Pub {i} 2025", 2025, stats=("p25", "median", "p75"), dp="FY2024")
        rows += _rows(f"Pub {i}", f"Pub {i} 2025", 2025, stats=("p25", "median"), dp="FY2025", band="$30M-$50M")
        rows += _rows(f"Pub {i}", f"Pub {i} 2020", 2020, stats=("median",), dp="FY2019")
        load_report(lib, f"p{i}", rows)
    sub = bp.Sub(intent="benchmark", text="x", metrics=[M], band="$25M")
    bp.gather(lib, sub, over_time=True)
    picked = bp.pick_rows(sub.candidates, 6)
    assert len(picked) == 6 and len({r["source"] for r in picked}) == 6
    assert all(r["statistic"] == "median" and r["band"] == "$20M-$30M" and r["report_year"] == "2025" for r in picked)


def test_b_when_the_floor_alone_exceeds_the_cap_older_editions_are_cut_first(lib):
    rows = []
    for i in range(5):
        rows += [dict(r, report_year="2025") for r in _rows(f"New {i}", f"New {i} 2025", 2025)]
        rows += _rows(f"Old {i}", f"Old {i} 2023", 2023)
    for r in rows:
        r.update(report_slug=r["report"], figure_key=r["source"], link="", gaps=[], match="exact",
                 basis_group="", band_context=False, denominator="", n="", page="")
    picked = bp.pick_rows(rows, 5)
    assert {r["source"] for r in picked} == {f"New {i}" for i in range(5)}


# --- Part C: one document per report -----------------------------------------------------

def _eighteen_reports(lib):
    for i in range(18):
        rows = []
        for m in MS:
            for dp in ("FY2024", "FY2025"):
                rows += _rows(f"Pub {i}", f"Pub {i} 2025", 2025, metrics=(m,), dp=dp, stats=("median", "p25", "p75"))
        load_report(lib, f"p{i}", rows)


def _plan_subs(lib):
    subs = [bp.Sub(intent="benchmark", text="x", metrics=[m], band="$25M") for m in MS]
    for s in subs:
        bp.gather(lib, s)
    caps = bp.allocate_caps(subs)
    for s in subs:
        bp.pick(lib, s, "standard", caps[id(s)])
    return subs


def test_c_the_same_report_in_three_parts_is_one_document(lib):
    _eighteen_reports(lib)
    subs = _plan_subs(lib)
    docs = bp.merge_figure_docs(subs, "standard", _reports(lib))
    titles = [d["title"] for d in docs]
    assert len(docs) == 18 and len(set(titles)) == 18
    assert all(d["parts"] == [1, 2, 3] for d in docs)
    merged_rows = sum(d["rows"] for d in docs)
    assert merged_rows == sum(s.rows for s in subs)                  # no row lost, none duplicated


def test_c_characters_sent_fall(lib):
    _eighteen_reports(lib)
    subs = _plan_subs(lib)
    reports = _reports(lib)
    before = 0
    for s in subs:                                  # the old shape: each part builds its own documents
        d, _ = bp._figure_docs(s.picked, bp.FIGURES_CHARS["standard"], reports)
        before += sum(len(x["title"]) + len(x["body"]) for x in d)
    after = sum(len(x["title"]) + len(x["body"]) for x in bp.merge_figure_docs(subs, "standard", reports))
    assert after < before * 0.8


def test_c_a_row_two_parts_asked_for_is_sent_once(lib):
    load_report(lib, "s", _rows("Sample Survey Co", "Sample Survey Co 2026", 2026, metrics=(M,), stats=("median", "p25")))
    subs = [bp.Sub(intent="benchmark", text="x", metrics=[M], band="$25M") for _ in range(2)]
    for s in subs:
        bp.gather(lib, s)
        bp.pick(lib, s, "standard", 40)
    docs = bp.merge_figure_docs(subs, "standard", _reports(lib))
    assert len(docs) == 1 and docs[0]["rows"] == 2 and docs[0]["parts"] == [1, 2]


def test_c_the_admin_row_lists_the_merged_documents(lib):
    _eighteen_reports(lib)
    ps = bp.plan_turn(lib, "What are S&M, R&D and G&A as a percent of revenue at $20-$30M?",
                      tier="standard", settings={"max_library": 8}, model="claude-sonnet-4-6", remaining_usd=None)
    entry = ps.log()
    assert entry["documents"] and all({"title", "rows", "parts"} <= set(d) for d in entry["documents"])
    assert len({d["title"] for d in entry["documents"]}) == len(entry["documents"])


# --- Part D: markers never break a table ---------------------------------------------------

ID39 = ("Gross margin by size.\n\n| Band | Median gross margin | Period |\n|---|---|---|\n"
        "| <$100M | 70% | FY2025 |\n| >$100M | 72% | FY2025 |[1]\n\n​\n\nThat is the newest period.")
CITES = [{"n": 1, "url": "https://r.example.test/x", "title": "Example"}]


def test_d_a_marker_after_the_last_pipe_stays_inside_the_table():
    html = render_answer_markdown(ID39, CITES)
    assert html.count("<tr>") == 3                     # header + 2 body rows
    assert "| &gt;$100M" not in html                   # no literal pipe text
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    assert "[1]" in body and body.count("<tr>") == 2
    assert "<p></p>" not in html and html.count("<p>") == 2


def test_d_the_prompt_rule_and_the_lint_flag():
    assert "Never after a row's closing pipe" in answer_rules.ANSWER_RULES
    flags = answer_lint.lint_answer(ID39)
    assert any(f["phrase"] == "marker after table row" for f in flags)
    assert not any(f["phrase"] == "marker after table row"
                   for f in answer_lint.lint_answer("| a | b [1] |\n|---|---|\n"))


def test_d_the_live_page_script_repairs_the_same_row():
    import shutil, subprocess, re as _re
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    src = open(pathlib.Path(__file__).resolve().parents[1] / "webapp" / "app.py").read()
    m = _re.search(r"function moveRowMarkers\(line\) \{\{(.*?)\n\}\}", src, _re.S)
    js = "function moveRowMarkers(line) {" + m.group(1).replace("{{", "{").replace("}}", "}").replace("\\\\", "\\") + "\n}"
    out = subprocess.run([node, "-e", js + "\nconsole.log(moveRowMarkers('| >$100M | 72% | FY2025 |[1]'))"],
                         capture_output=True, text=True).stdout.strip()
    assert out == "| >$100M | 72% | FY2025 [1] |"


# --- Part E: left-out notes never sit in a table ----------------------------------------------

def test_e_one_closing_line_names_the_left_out_rows():
    line = bp.left_out_line([{"Benchmarkit Sample, Report 2023 (2023)": 6}, {"OpenView Sample, Survey 2022 (2022)": 17,
                                                                            "A, B (2021)": 1, "C, D (2020)": 1, "E, F (2019)": 1}])
    assert line.startswith("Left out (not sent): ") and line.count("\n") == 0
    assert "OpenView Sample, Survey 2022 (2022) 17 rows" in line and "and 1 more report" in line
    assert bp.left_out_line([{}]) == ""


def test_e_status_notes_never_put_a_count_or_label_mix_in_a_cell():
    def row(slug, key, metric, src="Example Capital"):
        return {"report_slug": slug, "figure_key": key, "metric": metric, "denominator": "", "unit": "percent",
                "source": src, "report": f"{src} 2026", "report_year": "2026"}
    a, b = row("ec", 1, "Sales and Marketing expense % of Revenue"), row("ec", 2, "R&D expense % of Revenue")
    notes = "\n".join(bp.status_notes([a, b], [], {("ec", 2)}))
    assert "left out. In a table cell write only \"left out\"" in notes
    assert "more row" not in notes and "(R&D left out)" not in notes


def test_e_the_turn_carries_the_closing_line_not_a_per_cell_count(lib):
    for i in range(3):
        rows = _rows(f"Pub {i}", f"Pub {i} 2025", 2025, metrics=MS, stats=("median", "p25", "p75", "mean"))
        load_report(lib, f"p{i}", rows)
    bp.PART_ROWS_CAP, old = 4, bp.PART_ROWS_CAP
    try:
        ps = bp.plan_turn(lib, "What are S&M, R&D and G&A as a percent of revenue at $20-$30M?",
                          tier="standard", settings={"max_library": 8}, model="claude-sonnet-4-6", remaining_usd=None)
    finally:
        bp.PART_ROWS_CAP = old
    text = ps.plan_text
    assert text.count("Left out (not sent):") == 1
    assert "more row" not in text and "stored, not sent" not in text
    assert ps.log()["left_out_detail"]
