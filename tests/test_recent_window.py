"""Over-time questions: a recent window, newest first, a smaller figures block, one metric one lookup
(2026-11, follow-up to PRs 753 to 757). Invented fixtures only ("Example Benchmarks", "Sample Survey
Co"): no third-party figures are committed. Shaped like ask_questions id 31 and 32: editions from
2011 to 2025 for one metric family and band, where the oldest rows used to fill the cap."""
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Expenses as a % of Revenue\thttps://cards.example.test/e\tContains: expense as a percent of revenue\n")
YEARS = (2011, 2017, 2021, 2022, 2023, 2025)
PUBLISHERS = ("Example Benchmarks", "Sample Survey Co")
METRIC = "S&M % of Revenue"


def _edition_rows(src, y, metric=METRIC, band="$20M-$30M"):
    return [frow(source=src, report=f"{src} {y}", year=str(y), metric=metric, band=band, statistic=st,
                 segment=seg, value="40", unit="percent")
            for st in ("median", "p25", "p75", "mean") for seg in ("All", "Sales-led", "PLG")]


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    for src in PUBLISHERS:
        for y in YEARS:
            load_report(db, f"{src[:2].lower()}-{y}", _edition_rows(src, y))
    yield db
    db.close()


def _years(rows):
    return sorted({int(r["report_year"]) for r in rows})


def _reports(lib):
    return {r["report_slug"]: r for r in lib.list_benchmark_reports()}


# --- Part A: a recent window, newest first -------------------------------------------

def test_all_editions_is_newest_first_so_a_cap_cuts_the_oldest(lib):
    res = fg.find_figures(lib, METRIC, band="$25M", all_editions=True, limit=30)
    years = [int(r["report_year"]) for r in res["rows"]]
    assert years == sorted(years, reverse=True)
    assert 2025 in years and 2011 not in years
    assert any("newest first; the oldest are left out" in n for n in res["notes"])


def test_default_window_leaves_out_a_2011_edition(lib):
    res = fg.find_figures(lib, METRIC, band="$25M", all_editions=True, recent_window=True, limit=500)
    assert _years(res["rows"]) == [2021, 2022, 2023, 2025]        # 4 editions, none more than 5 years back
    note = next(n for n in res["notes"] if n.startswith(bp.WINDOW_NOTE_PREFIX))
    assert "(2011)" in note and "(2017)" in note and "historically" in note


def test_since_a_year_includes_it_and_everything_after(lib):
    res = fg.find_figures(lib, METRIC, band="$25M", all_editions=True, from_year=2015, limit=500)
    assert _years(res["rows"]) == [2017, 2021, 2022, 2023, 2025]


def test_historically_returns_every_edition(lib):
    res = fg.find_figures(lib, METRIC, band="$25M", all_editions=True, limit=500)
    assert _years(res["rows"]) == list(YEARS)


def test_a_publisher_whose_only_editions_are_old_is_left_out_of_the_window(lib):
    """One window for every publisher (PR 761): a publisher whose newest edition is 2012 is outside a
    window that starts at 2020, and the note names it."""
    for y in (2010, 2012):
        load_report(lib, f"ol-{y}", _edition_rows("Old Publisher Co", y))
    res = fg.find_figures(lib, METRIC, band="$25M", all_editions=True, recent_window=True, limit=500)
    assert not [r for r in res["rows"] if r["source"] == "Old Publisher Co"]
    assert any("Old Publisher Co 2012" in n for n in res["notes"])


@pytest.mark.parametrize("question,history", [
    ("How has S&M changed over the past few years?", ""),
    ("How has S&M changed since 2015?", "2015"),
    ("What is the historical trend in S&M, historically?", "full"),
    ("Show me S&M across all editions", "full"),
    ("How has S&M changed over the last 12 years?", "full"),
])
def test_history_signal(lib, question, history):
    sig = bp.detect(lib, question)
    assert sig["over_time"] and sig["history"] == history


def test_the_id_31_shape_the_newest_editions_reach_the_model(lib):
    """Before: year=all fetched the 150 oldest rows and only the 2011 and 2017 editions were sent."""
    sub = bp.Sub(intent="benchmark", text="S&M expense as a percent of revenue", metrics=[METRIC],
                 band="$20-$30M", year="all")
    bp.lookup(lib, sub, "standard", _reports(lib))
    titles = " | ".join(d["sent"]["title"] for d in sub.docs)
    assert "2025" in titles and "2023" in titles
    assert "2011" not in titles and "2017" not in titles


def test_when_the_row_cap_cuts_the_newest_edition_survives(lib):
    sub = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all", history="full")
    bp.lookup(lib, sub, "deep", _reports(lib), row_cap=6)
    sent_years = {int(d["sent"]["title"].rsplit("(", 1)[1].rstrip(")")) for d in sub.docs}
    assert 2025 in sent_years and 2011 not in sent_years
    assert sub.capped_rows + sub.fetch_cut > 0
    assert sub.rows == 6


def test_a_since_lookup_through_the_prestep_includes_the_older_edition(lib):
    sub = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all", history="2015")
    bp.lookup(lib, sub, "deep", _reports(lib), row_cap=200)
    assert any("2017" in d["sent"]["title"] for d in sub.docs) and not any("2011" in d["sent"]["title"] for d in sub.docs)
    sub2 = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all", history="full")
    bp.lookup(lib, sub2, "deep", _reports(lib), row_cap=200)
    assert any("2011" in d["sent"]["title"] for d in sub2.docs)


def test_the_log_says_what_was_left_out(lib):
    sub = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all")
    bp.lookup(lib, sub, "standard", _reports(lib))
    ps = bp.Prestep(active=True, subs=[sub])
    entry = ps.log()["subs"][0]
    assert entry["rows_capped"] > 0 and entry["figure_rows_left_out"] >= entry["rows_capped"]
    assert entry["editions_left_out"].startswith(bp.WINDOW_NOTE_PREFIX)


# --- Part B: a smaller figures block -----------------------------------------------------

def _row(src, basis="revenue", y=2025, i=0):
    return {"source": src, "basis_group": basis, "report_year": str(y), "match": "exact", "i": i}


def test_pick_rows_keeps_each_publisher_and_each_basis_before_filling():
    rows = ([_row("A", "revenue", i=i) for i in range(10)] + [_row("B", "revenue", i=i) for i in range(10)]
            + [_row("A", "arr", i=i) for i in range(3)] + [_row("C", "revenue", y=2021)])
    out = bp.pick_rows(rows, 5)
    assert len(out) == 5
    assert {r["source"] for r in out} == {"A", "B", "C"}
    assert {r["basis_group"] for r in out} == {"revenue", "arr"}
    assert out == [r for r in rows if r in out]                      # the sorted order is kept


def test_pick_rows_under_the_cap_is_untouched():
    rows = [_row("A", i=i) for i in range(4)]
    assert bp.pick_rows(rows, 40) == rows


def test_allocate_caps_shares_the_turn_cap_and_hands_back_unused_room():
    subs = [SimpleNamespace(candidates=[0] * n) for n in (150, 150, 150, 8)]
    caps = bp.allocate_caps(subs)
    assert caps[id(subs[3])] == 8
    assert all(caps[id(s)] <= bp.PART_ROWS_CAP for s in subs)
    assert sum(caps.values()) <= bp.TURN_ROWS_CAP
    assert caps[id(subs[0])] == (bp.TURN_ROWS_CAP - 8) // 3


def test_allocate_caps_a_single_big_part_gets_the_part_cap():
    s = SimpleNamespace(candidates=[0] * 150)
    assert bp.allocate_caps([s])[id(s)] == bp.PART_ROWS_CAP


def test_before_and_after_input_size_on_the_id_31_shape(lib):
    """The figures block, in characters (4 characters is about one token), at the old cap of 150
    rows and the new part cap. Standard tier, same fixture."""
    sub_old = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all", history="full")
    bp.lookup(lib, sub_old, "deep", _reports(lib), row_cap=150)
    sub_new = bp.Sub(intent="benchmark", text="S&M", metrics=[METRIC], band="$20-$30M", year="all")
    bp.lookup(lib, sub_new, "deep", _reports(lib))
    old = sum(len(d["body"]) for d in sub_old.docs)
    new = sum(len(d["body"]) for d in sub_new.docs)
    assert sub_new.rows <= bp.PART_ROWS_CAP < sub_old.rows
    assert new < old * 0.4


def test_only_related_metric_rows_are_sent_as_a_few_pointers(lib, monkeypatch):
    fake = [dict(frow(source=f"S{i}", report=f"R{i} 2025", year="2025", metric="Headcount ratio"),
                 match="related_metric", gaps=[], basis_rank=0, band_context=False, basis_group="",
                 report_slug=f"r{i}", figure_key=str(i), band="x", band_basis="", segment="", statistic="",
                 denominator="", value="1", unit="", n="", page="", data_period="", link="", canonical_metric="",
                 report="R", report_year="2025", source_="x") for i in range(8)]
    monkeypatch.setattr(fg, "find_figures", lambda *a, **k: {"rows": list(fake), "notes": [], "total_matching": 8, "returned": 8,
                                                             "illustrative_ids": []})
    sub = bp.Sub(intent="benchmark", text="total employees", metrics=["Total employees"])
    bp.gather(lib, sub)
    assert len(sub.candidates) == bp.RELATED_METRIC_CAP


# --- Part C: one metric, one lookup ----------------------------------------------------------

@pytest.fixture
def gm_lib(tmp_path):
    db = Library(str(tmp_path / "g.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(
        "canonical_metric\tcard_url\tcontains\nBurn multiple\thttps://c.example.test/b\tContains: burn multiple\n")
    assert not err
    db.seed_metric_registry(rows)
    load_report(db, "eb-2099", [frow(metric="Gross Margin - Total", value="77", unit="percent"),
                                frow(metric="Gross Margin", value="75", unit="percent"),
                                frow(metric="Gross Margin - Subscription", value="81", unit="percent")])
    yield db
    db.close()


def test_gross_margin_and_gross_margin_total_are_one_metric(gm_lib):
    sig = bp.detect(gm_lib, "What are typical gross margins for SaaS?")
    assert sig["n_metrics"] == 1 and not sig["needs_split"] and not sig["split_reasons"]


def test_one_metric_question_makes_one_part_and_no_splitter_call(gm_lib):
    def boom():
        raise AssertionError("the splitter must not be called for a one-metric question")
    ps = bp.plan_turn(gm_lib, "What are typical gross margins for SaaS?", tier="standard",
                      settings={"max_library": 8, "source_chars": 1800, "global_chars": 16000},
                      model="claude-sonnet-4-6", remaining_usd=None, client_factory=boom)
    assert not ps.split_ran and not ps.split_fallback and len(ps.subs) == 1


def test_the_one_lookup_finds_both_stored_names(gm_lib):
    res = fg.find_figures(gm_lib, "Gross Margin - Total")
    assert {r["metric"] for r in res["rows"]} == {"Gross Margin - Total", "Gross Margin"}


def test_a_different_metric_still_counts_as_another(gm_lib):
    sig = bp.detect(gm_lib, "Compare gross margin and the burn multiple for SaaS")
    assert sig["n_metrics"] == 2 and sig["needs_split"]
