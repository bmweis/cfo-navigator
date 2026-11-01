"""Total headcount kept, statistic ranking, within-report duplicates, "not printed" vs "left out",
attribution of figures documents, and the "heuristic" word (follow-up to PRs 753 to 759).
Invented fixtures only: publishers are fictional ("Example Benchmarks", "Sample Survey Co")."""
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import org_spend_fixtures as F  # noqa: E402
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint, answer_rules, figures as fg, voice_review  # noqa: E402
from linklib import benchmark_prestep as bp  # noqa: E402

B = "$20M-$30M"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def sv(yr, metric, value, stat="median", unit="count", seg="All", **kw):
    return frow(source="Sample Survey Co", report=f"Sample Survey Co {yr}", year=str(yr), data_period=f"Q2 {yr}",
                basis="ARR", band=B, metric=metric, value=value, unit=unit, statistic=stat, segment=seg, **kw)


@pytest.fixture
def lib(tmp_path):
    db = F.make_lib(tmp_path / "t.db")
    rows = [sv(2025, "Headcount: Total", "131"), sv(2025, "Headcount: Total", "131", page="48"),
            sv(2025, "Headcount: Total", "200", page="49")]
    for fn in ("Engineering", "Sales", "G&A", "Customer Success", "Marketing", "Product"):
        for st in ("max", "min", "p25", "p75", "mean", "median"):
            for seg in ("All", "Enterprise", "SMB"):
                rows.append(sv(2025, f"Headcount: {fn}", "20", stat=st, unit="percent", seg=seg))
    load_report(db, "ss-2025", rows)
    return db


def hc_sub(text="total headcount by function at $20-$30M ARR"):
    return bp.Sub(intent="benchmark", text=text, metrics=[fg.HEADCOUNT_FAMILY_LABEL], band="$20-$30M ARR", year="all")


def total_rows(rows):
    return [r for r in rows if fg.is_total_fte_row(r)]


# --- Part A ----------------------------------------------------------------------------

def test_a_newest_total_fte_survives_a_function_mix_crowd(lib):
    sub = hc_sub()
    bp.gather(lib, sub, over_time=True)
    picked = bp.pick_rows(sub.candidates, 26)
    newest = [r for r in total_rows(picked) if r["source"] == "Sample Survey Co" and r["report_year"] == "2025"]
    assert newest, "the newest total FTE figure was crowded out by function-mix rows"
    assert newest[0]["segment"] == "All"
    # one row per publisher edition, not only the newest
    assert {(r["source"], r["report_year"]) for r in total_rows(picked)} >= {("Sample Survey Co", "2025"), ("Example Benchmarks", "2025")}


def test_a_statistics_rank_median_first_extremes_last(lib):
    sub = hc_sub()
    bp.gather(lib, sub, over_time=True)
    order = [r["statistic"] for r in sub.candidates if r["report_slug"] == "ss-2025" and not fg.is_total_fte_row(r)]
    assert order[:3] == ["median"] * 3
    assert order.index("mean") < order.index("p25") < order.index("max")
    assert fg.statistic_rank("median") < fg.statistic_rank("mean") < fg.statistic_rank("") < fg.statistic_rank("p75") < fg.statistic_rank("max")


def test_a_range_word_moves_quartiles_up_and_extremes_only_when_asked():
    r = fg.statistic_wants("what is the typical range of headcount")
    assert fg.statistic_rank("p75", r) < fg.statistic_rank("max", r)
    assert fg.statistic_rank("p25", r) == 1.5 and fg.statistic_rank("max", r) == 5
    e = fg.statistic_wants("what is the highest headcount")
    assert fg.statistic_rank("max", e) == 2.5 and fg.statistic_rank("p25", e) == 4
    assert fg.statistic_rank("max", fg.statistic_wants("median headcount")) == 5


def test_a_cap_cuts_the_lowest_ranked_statistic_first_and_counts_it(lib):
    sub = hc_sub()
    bp.gather(lib, sub, over_time=True)
    kept = bp.pick_rows(sub.candidates, 20)
    cut = [r for r in sub.candidates if r not in kept]
    ss_kept = [fg.statistic_rank(r["statistic"]) for r in kept if r["report_slug"] == "ss-2025" and not fg.is_total_fte_row(r)]
    ss_cut = [fg.statistic_rank(r["statistic"]) for r in cut if r["report_slug"] == "ss-2025"]
    assert ss_cut and max(ss_kept) <= max(ss_cut)
    assert sum(1 for r in cut if r["statistic"] == "max") > sum(1 for r in cut if r["statistic"] == "median")


def test_a_identical_rows_in_one_report_collapse_and_a_conflict_is_flagged(lib):
    res = fg.find_figures(lib, "Headcount: Total", band="$20-$30M ARR")
    ss = [r for r in res["rows"] if r["report_slug"] == "ss-2025"]
    assert sorted(r["value"] for r in ss) == ["131", "200"]          # 131 stored twice -> one
    assert all(any("values differ within the report" in g for g in r["gaps"]) for r in ss)
    assert any("duplicate row" in n for n in res["notes"])


# --- Part B ----------------------------------------------------------------------------

def _row(slug, key, metric, src="Example Capital", yr="2026"):
    return {"report_slug": slug, "figure_key": key, "metric": metric, "denominator": "", "unit": "percent", "source": src,
            "report": f"{src} {yr}", "report_year": yr}


def test_b_stored_but_cut_row_says_left_out_never_not_printed():
    a, b, c = _row("ec", 1, "Gross Margin"), _row("ec", 2, "Cost of Revenue"), _row("bk", 3, "Gross Margin", "Bench Co")
    notes = bp.status_notes([a, b, c], [], {("ec", 2), ("bk", 3)})      # ec's gross margin was cut
    text = "\n".join(notes)
    assert "Gross Margin / Example Capital, Example Capital 2026 (2026): left out (1 more row" in text
    assert "Never write \"not printed\"" in text
    line = [x for x in notes if x.strip().startswith("Gross Margin / Example Capital")][0]
    assert "not printed (" not in line


def test_b_no_stored_row_says_not_printed():
    a, c = _row("ec", 1, "Gross Margin"), _row("bk", 3, "Gross Margin", "Bench Co")
    d = _row("bk", 4, "Cost of Revenue", "Bench Co")
    notes = "\n".join(bp.status_notes([a, c, d], [], {("ec", 1), ("bk", 3), ("bk", 4)}))
    assert "Cost of Revenue / Example Capital, Example Capital 2026 (2026): not printed" in notes


def test_b_all_shown_adds_nothing():
    a, c = _row("ec", 1, "Gross Margin"), _row("bk", 3, "Gross Margin", "Bench Co")
    assert bp.status_notes([a, c], [], {("ec", 1), ("bk", 3)}) == []


def test_b_end_to_end_cut_row_reaches_the_lookup_notes(lib):
    sub = hc_sub()
    bp.gather(lib, sub, over_time=True)
    bp.build(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()}, 8)
    joined = "\n".join(sub.notes)
    assert "Line-item status by report" in joined and "left out" in joined


def test_b_rules_carry_the_three_states_and_the_newest_period_pair():
    assert "left out (3 more rows)" in answer_rules.ANSWER_RULES
    assert "Lead with the newest period sent" in answer_rules.ANSWER_RULES
    assert "line-item status" in answer_rules.SHAPE_RULES


# --- Part C ----------------------------------------------------------------------------

def test_c_a_document_title_comes_only_from_its_own_rows():
    r1 = {**_row("jc", 1, "Headcount: Engineering", "Jurassic Capital", "2026"), "link": "https://j.example.test", "band": B,
          "band_basis": "ARR", "segment": "All", "statistic": "median", "value": "30", "n": "", "page": "", "data_period": "",
          "denominator": "", "gaps": [], "match": "exact"}
    # same file (slug), but these rows print a different publisher
    r2 = {**r1, "figure_key": 2, "source": "Sample Survey Co", "report": "Sample Survey Co 2025", "report_year": "2025"}
    docs, _ = bp._figure_docs([r1, r2], 9000, {"jc": {"source": "Jurassic Capital", "report": "Jurassic Capital 2026", "article_id": 0}})
    titles = {d["title"]: d for d in docs}
    assert set(titles) == {"Jurassic Capital, Jurassic Capital 2026 (2026)", "Sample Survey Co, Sample Survey Co 2025 (2025)"}
    other = titles["Sample Survey Co, Sample Survey Co 2025 (2025)"]
    assert other["rows"] == 1 and other["sent"]["url"] == ""           # not linked to the other report's page
    assert "Cite it by name" in other["body"] and "Jurassic Capital 2026" in other["body"]
    assert titles["Jurassic Capital, Jurassic Capital 2026 (2026)"]["sent"]["url"] == "https://j.example.test"


def test_c_two_reports_never_share_a_title(lib):
    sub = hc_sub()
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()}, row_cap=40)
    for d in sub.docs:
        # Sample Survey Co 2025 rows carry a "Q2 ..." period; every other fixture report a "CY-..." one
        prefix = "Q2" if "Sample Survey Co 2025" in d["title"] else "CY"
        periods = set(re.findall(r"period ([^|]+?)(?: \||$)", d["body"], re.M))
        assert periods and all(p.startswith(prefix) for p in periods), (d["title"], periods)
    assert all(x["rows"] >= 1 for x in sub.documents)
    assert [x["title"] for x in sub.documents] == [d["title"] for d in sub.docs]


def test_c_admin_row_lists_each_document_with_its_row_count(lib):
    sub = hc_sub()
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()}, row_cap=40)
    ps = bp.Prestep(active=True, subs=[sub], outcome="ran")
    docs = ps.log()["subs"][0]["documents"]
    assert docs and all(set(d) == {"title", "rows"} for d in docs)


# --- Part D ----------------------------------------------------------------------------

def test_d_rule_of_thumb_is_banned_and_flagged():
    assert "rule of thumb" in voice_review.FILLER_PHRASES
    flags = answer_lint.lint_answer("As a rule of thumb, 75% is generally the target.")
    assert any(f["phrase"] == "rule of thumb" for f in flags)


@pytest.mark.parametrize("text,phrase", [
    ("That is toward the high end of what's typical.", "high end of"),
    ("Figures were relatively stable over the past few years.", "relatively stable"),
    ("Retention stays stable at 98%.", "stable"),
    ("It would structurally drag the multiple.", "structurally"),
    ("Margins sit toward the middle.", "toward the"),
    ("That looks healthy.", "healthy"),
    ("An attractive margin profile.", "attractive"),
])
def test_d_characterization_is_flagged_not_stripped(text, phrase):
    assert any(f["phrase"] == phrase for f in answer_lint.lint_answer(text))


def test_d_a_named_comparison_is_not_flagged():
    assert answer_lint.lint_answer("81%, the highest of the three bands shown [1].") == []


def test_d_no_rule_of_thumb_left_in_code_docs_or_copy():
    allowed = {"linklib/voice_review.py", "linklib/answer_rules.py", "linklib/answer_lint.py", "tests/test_total_fte_states.py",
               "CLAUDE.md"}
    hits = []
    for base in ("linklib", "webapp", "scripts", "tests", "docs"):
        for p in (ROOT / base).rglob("*"):
            if p.suffix in (".py", ".md", ".html", ".csv", ".tsv", ".txt", ".json") and p.is_file():
                rel = str(p.relative_to(ROOT))
                if rel not in allowed and re.search(r"rule[- ]of[- ]thumb", p.read_text(errors="ignore"), re.I):
                    hits.append(rel)
    for p in ROOT.glob("*.md"):
        if p.name not in allowed and re.search(r"rule[- ]of[- ]thumb", p.read_text(errors="ignore"), re.I):
            hits.append(p.name)
    assert hits == []
