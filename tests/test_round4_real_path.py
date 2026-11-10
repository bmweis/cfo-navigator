"""Round 4 (after the ask_questions ids 51 to 53 reruns): the shift lookup the pre-step runs for a real turn.
Invented publishers ("Example High", "Example OV", "Example Index", "Example Sapphire"); the metric, denominator,
unit, band, segment and period STRINGS and the numbers are the ones id 53 showed. The earlier tests called
find_figures("Employees", band="$30M") directly; the pre-step calls it with the lookup LABEL "Headcount by
function", the sub-question's band "$20-$30M ARR" and its segment "Enterprise", so these go through
benchmark_prestep._shift_notes exactly as plan_turn does. No third-party report is committed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def _emp(src, rep, y, v, dp, denom, metric="Employees", statistic="median", unit="count", band="$20-50M", segment=""):
    return frow(source=src, report=rep, year=str(y), metric=metric, band=band, value=str(v), unit=unit,
                data_period=dp, denominator=denom, statistic=statistic, segment=segment)


def _load_real_shape(lib):
    """The ten rows from the brief, plus a report that prints a segment called Enterprise on another metric
    (that is what makes the sub-question's segment 'usable' for the shift lookup)."""
    load_report(lib, "ha-24", [
        _emp("Example High", "Example High 2024", 2024, 200, "Q2 2024", "full-time employees"),
        _emp("Example High", "Example High 2024", 2024, 35, "change 2023 to 2024", "full-time employees", statistic="other"),
    ])
    load_report(lib, "ha-25", [
        _emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 201, "Q2 2024", "count", "employees (implied 2024 median)"),
        _emp("Example High", "Example High 2025", 2025, 200, "Q2 2024", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 164, "Q2 2023", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 225, "Q2 2022", "count", "employees"),
    ])
    load_report(lib, "ov-22", [_emp("Example OV", "Example OV 2022", 2022, 226, "Q2 2022",
                                     "full-time equivalent employees at end of Q2 2022")])
    load_report(lib, "ov-23", [
        _emp("Example OV", "Example OV 2023", 2023, 165, "Q2 2023", "full-time equivalent employees, end of Q2 2023"),
        _emp("Example OV", "Example OV 2023", 2023, -61, "change Q2 2022 to Q2 2023", "full-time equivalent employees, end of Q2 2023",
             statistic="other"),
    ])
    load_report(lib, "idx-26", [
        frow(source="Example Index", report="Example Index 2026", year="2026", metric="Gross margin", band="$100M-$500M",
             segment="Enterprise", value="70", unit="percent", data_period="2026", statistic="median"),
    ])


def _sub():
    return bp.Sub(intent="benchmark", text="How has headcount changed over the past few years for Enterprise SaaS businesses "
                  "with sales-led motion at $20-$30M ARR?", metrics=["Headcount by function"], band="$20-$30M ARR",
                  segment="Enterprise", year="all")


def test_the_pre_step_lookup_pairs_the_headcount_editions_on_the_real_row_shape(lib):
    _load_real_shape(lib)
    ps = bp.Prestep()
    block = bp._shift_notes(lib, ps, [_sub()])
    froms = {(s["from"]["source"], s["from"]["value"], s["to"]["value"]) for s in ps.shifts}
    assert ("Example High", "225", "131") in froms or ("Example High", "200", "131") in froms, (ps.shifts, ps.shifts_unpaired)
    assert ("Example OV", "226", "165") in froms, (ps.shifts, ps.shifts_unpaired)
    assert "COMPUTED SHIFTS: none" not in block
    assert not any("segment" in k for k in ps.shifts_unpaired), ps.shifts_unpaired


def test_a_segment_the_asked_metric_does_not_print_is_dropped_for_the_shift_lookup_as_the_part_lookup_does(lib):
    """gather() probes whether THIS metric's rows print the segment and drops it if not; the shift lookup must do
    the same, or every report without that segment is flagged 'different segment' and nothing pairs."""
    _load_real_shape(lib)
    res = fg.find_figures(lib, "Headcount by function", band="$20-$30M ARR", segment="enterprise", all_editions=True,
                          recent_window=True, limit=150)
    assert {r["match"] for r in res["rows"]} == {"different_segment"}      # the cause, shown on the raw lookup
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [_sub()])
    assert ps.shifts_unpaired.get("not an exact or adjacent-band figure", 0) == 0, ps.shifts_unpaired


def test_a_pair_is_found_even_when_the_newest_edition_prints_more_rows_than_the_part_fetch_limit(lib):
    """The shift lookup reads every matching row, not the 150 the part lookup fetches (newest first): with 160 rows in
    the newest edition the older edition was cut and the pair never formed."""
    load_report(lib, "ha-25", [_emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees")]
                + [_emp("Example High", "Example High 2025", 2025, 10 + i, "Q2 2025", "count", "employees", segment=f"slice {i}")
                   for i in range(160)])
    load_report(lib, "ha-24", [_emp("Example High", "Example High 2024", 2024, 200, "Q2 2024", "full-time employees")])
    sub = bp.Sub(intent="benchmark", text="headcount over time at $20-$30M ARR", metrics=["Headcount by function"],
                 band="$20-$30M ARR", year="all")
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [sub])
    assert [(s["from"]["value"], s["to"]["value"]) for s in ps.shifts] == [("200", "131")], (ps.shifts, ps.shifts_unpaired)


def test_the_computed_line_states_the_span(lib):
    _load_real_shape(lib)
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [_sub()])
    ha = [s for s in ps.shifts if s["from"]["source"] == "Example High"][0]
    assert "225 count" in ha["line"] and "3-year span" in ha["line"] and "own history" in ha["line"], ha["line"]


# --- Part 3: rows chosen in code before the model writes the tables --------------------------------------

def _mix(fn, v, slug, year, src="Example High", cited="", series=None, period=None):
    return {"report_slug": slug, "source": src, "report": f"{src} {year}", "report_year": str(year), "data_period": period or f"Q2 {year}",
            "band": "$20-50M", "band_basis": "ARR", "statistic": "median", "metric": f"{fn} headcount mix", "denominator": "percent of headcount",
            "unit": "%", "value": str(v), "value_num": float(v), "figure_key": hash((slug, fn)) % 100000, "match": "exact",
            "band_context": False, "cited_source": cited, "series": series or src, "gaps": [], "also_printed_in": []}


def _spend(line, v, slug, src, year, band, match="exact", ctx=False):
    return {"report_slug": slug, "source": src, "report": f"{src} {year}", "report_year": str(year), "data_period": str(year - 1),
            "band": band, "band_basis": "ARR", "statistic": "median", "metric": f"{line} expense", "denominator": "percent of revenue",
            "unit": "%", "value": str(v), "value_num": float(v), "figure_key": hash((slug, line, band)) % 100000, "match": match,
            "band_context": ctx, "cited_source": "", "series": src, "gaps": [], "also_printed_in": []}


def test_the_mix_table_gets_one_edition_the_newest_and_the_spend_table_two_rows_a_line_item():
    mix25 = [_mix(f, 10 + i, "ha-25", 2025) for i, f in enumerate(("Engineering", "Sales", "Marketing", "G&A", "CS & Support"))]
    mix24 = [_mix(f, 9 + i, "ha-24", 2024) for i, f in enumerate(("Engineering", "Product & Design", "Sales", "Marketing", "CS & Support", "G&A", "Professional Services"))]
    spend = [_spend("G&A", 20 + i, f"s{i}", f"Pub{i}", 2022 + i % 4, ("$20-50M", "$25-50M", "<$100M", "$5-25M", "$10-25M")[i % 5],
                    match="exact" if i % 5 < 3 else "adjacent_band") for i in range(7)]
    spend += [_spend("R&D", 30 + i, f"r{i}", f"Pub{i}", 2023, "$20-50M") for i in range(4)]
    keep, dropped = bp.shape_for_tables(mix25 + mix24 + spend)
    kept = [r for r in mix25 + mix24 + spend if id(r) in keep]
    assert {r["report_year"] for r in kept if fg.row_table_kind(r) == "headcount_share"} == {"2025"}
    per_line = {}
    for r in kept:
        if fg.row_table_kind(r) == "spend_share":
            per_line[r["metric"]] = per_line.get(r["metric"], 0) + 1
    assert per_line == {"G&A expense": 3, "R&D expense": 2}, per_line   # two plus one newer adjacent-band edition
    assert len(dropped) == 7 + (7 - 3) + (4 - 2)
    assert sum(1 for r in kept) <= 5 + 3 + 2             # the id 53 shape was 12 + 12 mix and spend rows


def test_the_spend_rows_kept_are_the_closest_band_and_one_per_publisher_first():
    a = _spend("G&A", 18, "a", "PubA", 2025, "$5-25M", match="adjacent_band")
    b = _spend("G&A", 26, "b", "PubB", 2025, "$20-50M")
    c = _spend("G&A", 25, "c", "PubB", 2024, "$20-50M")
    d = _spend("G&A", 27, "d", "PubC", 2025, "<$100M")
    keep, dropped = bp.shape_for_tables([a, b, c, d])
    kept_ids = {r["report_slug"] for r in (a, b, c, d) if id(r) in keep}
    assert kept_ids == {"b", "d"}, kept_ids      # the exact $20-50M row, then another publisher's containing band; not the adjacent one


def test_a_share_a_republisher_prints_for_its_original_gets_a_derived_count_from_the_originals_total():
    total = {"report_slug": "ha-25", "source": "Example High", "report": "Example High 2025", "report_year": "2025", "data_period": "Q2 2025",
             "band": "$20-50M", "statistic": "median", "metric": "employees", "denominator": "count", "unit": "count", "value": "131",
             "value_num": 131.0, "figure_key": 1, "series": "Example High", "cited_source": ""}
    share = _mix("Marketing", 7, "jur-26", 2026, src="Example Jurassic", cited="Example High 2025 SaaS Benchmarks", series="Example High", period="Q2 2025")
    share["statistic"] = "share"
    out = fg.derive_headcount_counts([total, share])
    assert len(out) == 1 and "about 9 people" in out[0] and "share times a median total" in out[0], out


def test_a_real_org_spend_turn_sends_at_most_two_spend_rows_a_line_item_and_names_what_was_left_out(tmp_path, monkeypatch):
    import org_spend_fixtures as F
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db = F.make_lib(tmp_path / "o.db")
    ps = bp.plan_turn(db, F.QUESTION_35, tier="standard", settings=F.SETTINGS, model="claude-sonnet-4-6",
                      remaining_usd=None, client_factory=F.fake_client(F.SPLIT_35))
    sent = {(r["report_slug"], r["figure_key"]): r for s in ps.subs for r in s.picked}.values()
    per_line = {}
    for r in sent:
        if fg.row_table_kind(r) == "spend_share":
            per_line[bp._item_key(r)] = per_line.get(bp._item_key(r), 0) + 1
    assert per_line and max(per_line.values()) <= bp.SPEND_ROWS_PER_LINE, per_line
    assert ps.table_rows_shaped > 0 and ps.log()["table_rows_shaped"] == ps.table_rows_shaped
    assert "Left out (not sent)" in ps.plan_text


def test_the_answer_rules_say_levels_only_without_a_computed_line_and_no_restating():
    from linklib import answer_rules as ar
    assert "describe levels only" in ar.ANSWER_RULES and "different editions" in ar.ANSWER_RULES
    assert "Do not repeat a table's rows in a sentence" in ar.SHAPE_RULES


def test_a_newer_adjacent_band_edition_is_shown_next_to_older_exact_band_rows_and_stays_labelled_adjacent():
    old1 = _spend("S&M", 40, "o1", "PubOld1", 2022, "$20-50M")
    old2 = _spend("S&M", 35, "o2", "PubOld2", 2023, "$20-50M")
    new_adj = _spend("S&M", 38, "n", "PubNew", 2025, "$25-50M", match="adjacent_band")
    keep, _ = bp.shape_for_tables([old1, old2, new_adj])
    assert id(new_adj) in keep and id(old1) in keep and id(old2) in keep
    assert new_adj["match"] == "adjacent_band"
    same_year = _spend("S&M", 41, "s", "PubSame", 2022, "$25-50M", match="adjacent_band")
    keep, _ = bp.shape_for_tables([old1, old2, same_year])
    assert id(same_year) not in keep                     # no extra slot unless the edition is newer
