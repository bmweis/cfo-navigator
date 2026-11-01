"""Dangling citations, republished figures, computed shifts, the window on the default path, table order
(PR 763). Invented fixtures only ("Example Benchmarks", "Sample Survey Co", "Example Capital"): no
third-party figures. Shaped like ask_questions ids 40 and 41."""
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint, answer_rules
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib import citations as cit
from linklib.citations import extract_citations
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Gross Margin\thttps://c.example.test/gm\tContains: gross margin\n"
       "Total headcount\thttps://c.example.test/hc\tContains: headcount, total employees\n")
GM = "Gross Margin"


def _gm(src, rep, y, value, dp, band="$20M-$50M", stat="median", unit="percent"):
    return frow(source=src, report=rep, year=str(y), metric=GM, band=band, statistic=stat, value=str(value),
                unit=unit, data_period=dp)


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


# --- Part A: every marker resolves ------------------------------------------------------------

def _resp(texts_and_docs):
    blocks = []
    for text, doc in texts_and_docs:
        cites = [SimpleNamespace(document_index=doc, url=None, title=None)] if doc is not None else []
        blocks.append(SimpleNamespace(type="text", text=text, citations=cites))
    return blocks


def test_a_markers_above_the_sources_count_are_dropped_and_recorded():
    # The id 40 shape: the model types [7] [11] [15] [27] (document positions) while the API placed 1 and 2.
    sent = [{"title": f"Doc {i}", "url": f"https://x.example.test/{i}", "type": "library"} for i in range(30)]
    blocks = _resp([("Margin was 81%", 0), (". Table | 30% | [7] |\n| 40% | [11] [27] |", None),
                    (" and 38%", 5)])
    text, cites = extract_citations(blocks, sent)
    assert len(cites) == 2 and "[27]" in text            # base behavior: a typed marker survives
    clean, dropped = cit.drop_unresolved_markers(text, cites)
    assert dropped == ["[7]", "[11]", "[27]"]
    import re
    assert {int(n) for n in re.findall(r"\[(\d{1,2})\]", clean)} <= {c["n"] for c in cites}
    assert "[1]" in clean and "[2]" in clean


def test_a_a_year_in_brackets_and_a_link_are_not_markers():
    clean, dropped = cit.drop_unresolved_markers("Report [2024] and [text](http://a.test) and [3]", [{"n": 1}])
    assert "[2024]" in clean and "[text](http://a.test)" in clean and dropped == ["[3]"]


def test_a_prompt_says_to_cite_each_row_alike_and_not_to_type_numbers():
    r = answer_rules.ANSWER_RULES
    assert "Never one marker on the last row only" in r and "Never type a citation number yourself" in r


# --- Part B: republished figures ------------------------------------------------------------

def _republish(lib, original_stored=True):
    if original_stored:
        load_report(lib, "ha-2025", [_gm("Example Alpha", "Example Alpha SaaS Benchmarks 2025", 2025, 30, "FY2024",
                                         band="$20M-$30M")])
    load_report(lib, "jc-2026", [_gm("Example Capital", "Example Capital 2026 Report", 2026, 30, "FY2024", band="$20M-$30M")])
    assert lib.set_report_cited_source("jc-2026", "Example Alpha 2025 SaaS Benchmarks")


def test_b_a_republished_cell_appears_once_and_says_where_else(lib):
    _republish(lib)
    res = fg.find_figures(lib, GM, band="$25M")
    assert [r["report_slug"] for r in res["rows"]] == ["ha-2025"]
    assert res["rows"][0]["also_printed_in"] and "Example Capital" in res["rows"][0]["also_printed_in"][0]
    assert any("Republished figures were folded" in n for n in res["notes"])
    assert "also printed in Example Capital" in bp._row_line(res["rows"][0])


def test_b_when_the_original_is_not_stored_the_row_is_labelled_and_one_series(lib):
    _republish(lib, original_stored=False)
    res = fg.find_figures(lib, GM, band="$25M")
    row = res["rows"][0]
    assert row["report_slug"] == "jc-2026" and row["cited_source"] == "Example Alpha 2025 SaaS Benchmarks"
    assert row["series"] == "Example Alpha 2025 SaaS Benchmarks"
    assert "republished: this report cites Example Alpha 2025 SaaS Benchmarks" in bp._row_line(row)


def test_b_the_figures_document_title_is_the_rows_own_report(lib):
    _republish(lib, original_stored=False)
    row = fg.find_figures(lib, GM, band="$25M")["rows"][0]
    assert bp._doc_title(row).startswith("Example Capital")


def test_b_a_reload_keeps_cited_source(lib):
    _republish(lib, original_stored=False)
    load_report(lib, "jc-2026", [_gm("Example Capital", "Example Capital 2026 Report", 2026, 31, "FY2024", band="$20M-$30M")])
    assert {r["report_slug"]: r["cited_source"] for r in lib.list_benchmark_reports()}["jc-2026"]


def test_b_a_metric_family_limits_the_mark(lib):
    _republish(lib, original_stored=False)
    lib.set_report_cited_source("jc-2026", "Example Alpha 2025 SaaS Benchmarks", "Headcount")
    row = fg.find_figures(lib, GM, band="$25M")["rows"][0]
    assert row["cited_source"] == ""


def test_b_setting_it_through_the_admin_page_works(lib, tmp_path, monkeypatch):
    # The admin route is covered end to end in test_dangling_admin below; here the Library API refuses bad input.
    with pytest.raises(ValueError):
        lib.set_report_cited_source("jc-2026", "", "Headcount")
    assert lib.set_report_cited_source("nope", "x") is False


# --- Part C: computed shifts --------------------------------------------------------------

def _series(lib, v_old, v_new, src="Example Benchmarks", unit="percent", metric=GM):
    load_report(lib, "eb-2024", [frow(source=src, report=f"{src} 2024", year="2024", metric=metric, band="$20M-$50M",
                                      value=str(v_old), unit=unit, data_period="FY2023")])
    load_report(lib, "eb-2025", [frow(source=src, report=f"{src} 2025", year="2025", metric=metric, band="$20M-$50M",
                                      value=str(v_new), unit=unit, data_period="FY2024")])


def _shifts(lib, metric=GM):
    res = fg.find_figures(lib, metric, band="$30M", all_editions=True, recent_window=True, limit=500)
    return fg.compute_shifts(res["rows"], lib.shift_rules())


def test_c_a_six_point_gain_yields_a_line(lib):
    _series(lib, 70, 76)
    out = _shifts(lib)
    assert len(out["shifts"]) == 1
    s = out["shifts"][0]
    assert s["kind"] == "points" and s["size"] == 6 and s["delta"] == 6
    assert "up 6 points" in s["line"] and "derived from stored reports" in s["line"] and "one series" in s["line"]


def test_c_a_two_point_move_yields_none_and_is_counted(lib):
    _series(lib, 70, 72)
    out = _shifts(lib)
    assert out["shifts"] == [] and out["suppressed"] == 1


def test_c_exactly_at_the_threshold_is_not_reported(lib):
    _series(lib, 70, 73)
    assert _shifts(lib)["shifts"] == []


def test_c_a_twelve_percent_headcount_change_yields_a_line(lib):
    _series(lib, 100, 112, unit="FTE", metric="Total headcount")
    out = _shifts(lib, "Total headcount")
    assert len(out["shifts"]) == 1 and out["shifts"][0]["kind"] == "relative"
    assert "up 12 percent" in out["shifts"][0]["line"]


def test_c_a_nine_percent_headcount_change_is_below_threshold(lib):
    _series(lib, 100, 109, unit="FTE", metric="Total headcount")
    assert _shifts(lib, "Total headcount")["shifts"] == []


def test_c_the_registry_threshold_overrides_the_default(lib):
    _series(lib, 70, 72)
    assert lib.set_metric_shift(GM, 1.0, "points")
    assert len(_shifts(lib)["shifts"]) == 1


def test_c_a_republished_pair_collapses_to_one_series(lib):
    # The original and its republisher print the same cell in the same year: one series, no shift from itself.
    load_report(lib, "ha-2024", [_gm("Example Alpha", "Example Alpha SaaS Benchmarks 2024", 2024, 70, "FY2023")])
    load_report(lib, "ha-2025", [_gm("Example Alpha", "Example Alpha SaaS Benchmarks 2025", 2025, 80, "FY2024")])
    load_report(lib, "jc-2026", [_gm("Example Capital", "Example Capital 2026", 2026, 80, "FY2024")])
    lib.set_report_cited_source("jc-2026", "Example Alpha 2025 SaaS Benchmarks")
    out = _shifts(lib)
    assert len(out["shifts"]) == 1                      # one line, not a second from the republisher
    assert out["shifts"][0]["directional"] is False


def test_c_a_conflicting_cell_is_skipped_and_flagged(lib):
    load_report(lib, "eb-2024", [_gm("Example Benchmarks", "Example Benchmarks 2024", 2024, 201, "FY2023", unit="FTE"),
                                 _gm("Example Benchmarks", "Example Benchmarks 2024", 2024, 200, "FY2023", unit="FTE")])
    load_report(lib, "eb-2025", [_gm("Example Benchmarks", "Example Benchmarks 2025", 2025, 131, "FY2024", unit="FTE")])
    out = _shifts(lib)
    assert out["shifts"] == []
    assert out["skipped"] and "different values" in out["skipped"][0]


def test_c_different_surveys_are_directional_only_when_no_series_exists(lib):
    load_report(lib, "a", [_gm("Example Benchmarks", "Example Benchmarks 2023", 2023, 70, "FY2022")])
    load_report(lib, "b", [_gm("Sample Survey Co", "Sample Survey 2025", 2025, 80, "FY2024")])
    out = _shifts(lib)
    assert len(out["shifts"]) == 1 and out["shifts"][0]["directional"]
    assert "different surveys, directional only" in out["shifts"][0]["line"]


def test_c_the_turn_hands_the_model_exact_lines_and_no_model_call(lib):
    _series(lib, 70, 76)
    ps = bp.Prestep()
    sub = bp.Sub(intent="benchmark", text="gross margin at $30M", metrics=[GM], band="$30M")
    block = bp._shift_notes(lib, ps, [sub])
    assert "COMPUTED SHIFTS" in block and "up 6 points" in block and "What shifted" in block
    assert ps.shifts and ps.log is not None


def test_c_lint_flags_a_shift_claim_with_no_computed_line():
    shifts = [{"size": 6.0}]
    assert answer_lint.lint_answer("Gross margin rose, up 6 points (derived from stored reports).", shifts=shifts) == []
    flags = answer_lint.lint_answer("Gross margin was up 9 points.", shifts=shifts)
    assert any(f["phrase"] == "shift claim without a computed line" for f in flags)
    assert not any(f["phrase"] == "shift claim without a computed line"
                   for f in answer_lint.lint_answer("Gross margin was up 9 points.", shifts=None))


# --- Part D: the window on the default path ------------------------------------------------

def test_d_the_default_path_leaves_out_a_report_older_than_five_years(lib):
    load_report(lib, "old", [_gm("Pub Old", "Pub Old 2011", 2011, 60, "FY2010")])
    load_report(lib, "mid", [_gm("Pub Mid", "Pub Mid 2019", 2019, 65, "FY2018")])
    load_report(lib, "new", [_gm("Example Benchmarks", "Example Benchmarks 2025", 2025, 80, "FY2024")])
    base = fg.find_figures(lib, GM, band="$30M")
    assert {r["report_year"] for r in base["rows"]} == {"2011", "2019", "2025"}       # base behavior
    res = fg.find_figures(lib, GM, band="$30M", default_window=True)
    assert {r["report_year"] for r in res["rows"]} == {"2025"}
    assert any(n.startswith(bp.WINDOW_NOTE_PREFIX) and "(2011)" in n and "(2019)" in n for n in res["notes"])


def test_d_gather_uses_it_and_since_reaches_older(lib):
    load_report(lib, "old", [_gm("Pub Old", "Pub Old 2011", 2011, 60, "FY2010")])
    load_report(lib, "new", [_gm("Example Benchmarks", "Example Benchmarks 2025", 2025, 80, "FY2024")])
    sub = bp.Sub(intent="benchmark", text="gross margin", metrics=[GM], band="$30M")
    bp.gather(lib, sub)
    assert {r["report_year"] for r in sub.candidates} == {"2025"}
    sub2 = bp.Sub(intent="benchmark", text="gross margin", metrics=[GM], band="$30M", history="2010")
    bp.gather(lib, sub2)
    assert "2011" in {r["report_year"] for r in sub2.candidates}


# --- Part E: order, one value, consistent with ----------------------------------------------

def test_e_the_total_fte_order_note_puts_the_newest_period_first(lib):
    picked = [frow(source="Example Benchmarks", report="EB 2024", year="2024", value="200", data_period="FY2023"),
              frow(source="Example Benchmarks", report="EB 2025", year="2025", value="131", data_period="FY2024"),
              frow(source="Sample Survey Co", report="SS 2025", year="2025", value="113", data_period="FY2023")]
    note = bp._order_note(picked)
    assert note.startswith("Table row order, newest data period first: Example Benchmarks 2025 (FY2024)")
    assert note.index("2025 (FY2024)") < note.index("2024 (FY2023)")


def test_e_rows_rank_newest_data_period_across_reports(lib):
    load_report(lib, "a", [_gm("Pub A", "Pub A 2025", 2025, 70, "FY2023")])
    load_report(lib, "b", [_gm("Pub B", "Pub B 2025", 2025, 80, "FY2024")])
    rows = fg.find_figures(lib, GM, band="$30M")["rows"]
    assert rows[0]["source"] == "Pub B"


def test_e_lint_flags_two_values_in_one_cell_and_consistent_with():
    flags = answer_lint.lint_answer("| S&M + R&D | 33% + 40% |\n|---|---|")
    assert any(f["phrase"] == "two values in one cell" for f in flags)
    flags = answer_lint.lint_answer("The drop is consistent with the headcount contraction.")
    assert any(f["phrase"] == "consistent with" for f in flags)
    assert answer_lint.lint_answer("| S&M | 33% |\n| R&D | 40% |") == []


def test_e_rules_carry_order_and_one_value_pairs():
    r = answer_rules.ANSWER_RULES
    assert "newest data period first within a metric" in r and "One value to a table cell" in r
    assert "Republished figures" in r and "COMPUTED SHIFTS" in r
