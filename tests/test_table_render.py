"""Code-rendered tables, step 1 (spend and gross margin). Invented fixtures only: "Example Benchmarks",
"Sample Survey Co", "Example Capital", "Example Index"; every figure is made up."""
import json
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import org_spend_fixtures as F  # noqa: E402
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import agent, figures as fg  # noqa: E402
from linklib import benchmark_prestep as bp  # noqa: E402
from linklib import table_render as tr  # noqa: E402
from webapp.answer_render import render_answer_markdown  # noqa: E402

pytestmark = pytest.mark.usefixtures("anthropic_env")


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


@pytest.fixture
def lib(tmp_path):
    return F.make_lib(tmp_path / "t.db")


# --- turn harness: the model writes prose plus placeholders; we read what the user gets --------------------------

def run_turn(lib, monkeypatch, model_text, q=F.QUESTION_35, split=F.SPLIT_35, cite_doc=None):
    captured = {}

    def create(**kw):
        if kw.get("max_tokens") == bp.SPLIT_MAX_TOKENS:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(split))],
                                   usage=SimpleNamespace(input_tokens=300, output_tokens=200))
        captured.update(kw)
        cits = None
        if cite_doc is not None:
            cits = [SimpleNamespace(type="char_location", document_index=cite_doc(kw), cited_text="x",
                                    document_title="t", start_char_index=0, end_char_index=1)]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=model_text, citations=cits)],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0,
                                                     cache_read_input_tokens=0), stop_reason="end_turn")

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(agent, "_get_client", lambda: client)
    orig = bp.run_split
    monkeypatch.setattr(bp, "run_split", lambda qq, c, m, client_factory=None: orig(qq, c, m, client_factory=lambda: client))
    ans = agent.answer_question(lib, q, effort="standard", use_library=True, use_feed=False, use_web=False)
    assert not ans.failed, ans.error
    return ans, captured


def table_lines(text):
    return [l for l in text.split("\n") if l.startswith("|")]


PROSE = ("Sales and marketing runs 30% to 38% of revenue at this size.\n\n{{table:spend}}\n\nGross margin is 74%.\n\n"
         "{{table:margins}}\n\nThe data covers 2024 and 2025.")


# --- rendering rules ---------------------------------------------------------------------------------------------

def test_order_is_line_item_then_band_size_then_newest_edition():
    rows = [_spend_row(figure_key=1, metric="R&D % of Revenue", band="$20M-$50M", report_year="2025", value="30"),
            _spend_row(figure_key=2, metric="S&M % of Revenue", band="$50M+", report_year="2025", value="35"),
            _spend_row(figure_key=3, metric="S&M % of Revenue", band="$5M-$25M", report_year="2024", value="40"),
            _spend_row(figure_key=4, metric="S&M % of Revenue", band="$5M-$25M", report_year="2025", value="39", source="B"),
            _spend_row(figure_key=5, metric="S&M % of Revenue", band="", report_year="2025", value="36", source="Idx")]
    md, _ = tr.build_spend(rows, lambda _r: None)
    body = [l.split("|") for l in table_lines(md)[2:]]
    assert [(c[1].strip(), c[2].strip(), c[4].strip()) for c in body] == [
        ("S&M", "All sizes", "36%"), ("S&M", "$5M-$25M", "39%"), ("S&M", "$5M-$25M", "40%"),
        ("S&M", "$50M+", "35%"), ("R&D", "$20M-$50M", "30%")]


def _spend_row(**kw):
    base = frow(source="Example Capital", report="Example Capital 2026", year="2026", metric="S&M % of Revenue", band="$20M-$30M",
                value="30", unit="percent", denominator="percent of revenue", statistic="median", data_period="FY2025")
    base.update(figure_key=1, report_slug="x", match="exact", gaps=[], series=kw.get("source", "Example Capital"),
                also_printed_in=[], basis_group="revenue", band_context=False)
    base.update(kw)
    base["series"] = base["source"]
    base["value_num"] = fg.parse_value_num(base["value"])
    return base


def test_columns_are_fixed_and_there_is_no_match_note_column(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, PROSE)
    head = table_lines(ans.text)[0]
    assert [c.strip() for c in head.strip("|").split("|")] == ["Line item", "Band", "Statistic", "Value", "Basis", "Report and period"]
    assert "match" not in ans.text.lower().replace("matched", "")


def test_a_cell_never_holds_a_pasted_figure_line(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, PROSE)
    for line in table_lines(ans.text):
        for cell in line.strip("|").split("|"):
            assert len(cell.strip()) < 70 and "value " not in cell and "table:" not in cell


def test_heading_names_both_bases_when_both_are_present():
    rows = [_spend_row(figure_key=1), _spend_row(figure_key=2, basis_group="arr", denominator="percent of ARR", value="31")]
    md, _ = tr.build_spend(rows, lambda _r: None)
    assert md.startswith("**Spend by function, percent of revenue and percent of ARR**")
    only = tr.build_spend([_spend_row(figure_key=1)], lambda _r: None)[0]
    assert only.startswith("**Spend by function, percent of revenue**")


def test_a_target_row_is_labelled_target_not_median():
    r = frow(source="Example Capital", report="Example Capital 2026", year="2026", metric="S&M % of Revenue", band="$20M-$30M",
             value="30", unit="percent", denominator="percent of revenue", statistic="benchmark target")
    r.update(figure_key=1, report_slug="x", match="exact", gaps=[], series="Example Capital", also_printed_in=[], basis_group="revenue")
    md, _ = tr.build_spend([r], lambda _r: 1)
    assert "| target |" in md and "median" not in md


def test_range_is_computed_from_sent_points_each_endpoint_cited_and_bases_never_merge():
    rows = [_spend_row(figure_key=1, source="Example Capital", report_year="2026", value="35"),
            _spend_row(figure_key=2, source="Sample Survey Co", report_year="2025", value="55", band="$20M-$50M"),
            _spend_row(figure_key=3, source="Example Index", report_year="2024", value="42"),
            # another basis and another statistic: never inside the revenue-median range
            _spend_row(figure_key=4, source="Example OV", report_year="2024", value="90", basis_group="arr", denominator="percent of ARR"),
            _spend_row(figure_key=5, source="Example P75", report_year="2024", value="99", statistic="p75")]
    cites = {"Example Capital": 1, "Sample Survey Co": 2, "Example Index": 3, "Example OV": 4, "Example P75": 5}
    md, meta = tr.build_spend(rows, lambda r: cites[r["source"]])
    rng = [l for l in md.split("\n") if l.startswith("- S&M, median, percent of revenue")]
    assert len(rng) == 1 and meta["ranges"] == 1
    assert "35% to 55% (3 sources)" in rng[0]
    assert "Low: Example Capital 2026, FY2025 [1]" in rng[0] and "High: Sample Survey Co 2025, FY2025 [2]" in rng[0]
    assert "90" not in rng[0] and "99" not in rng[0]


def test_no_range_with_fewer_than_two_points():
    r = frow(source="Example Capital", report="Example Capital 2026", year="2026", metric="S&M % of Revenue", band="$20M-$30M",
             value="30", unit="percent", denominator="percent of revenue", statistic="median")
    r.update(figure_key=1, report_slug="x", match="exact", gaps=[], series="Example Capital", also_printed_in=[], basis_group="revenue")
    md, meta = tr.build_spend([r], lambda _r: 1)
    assert meta["ranges"] == 0 and "Ranges" not in md


def test_adjacent_band_is_labelled():
    r = frow(source="Example Capital", report="Example Capital 2026", year="2026", metric="S&M % of Revenue", band="$20M-$100M",
             value="30", unit="percent", denominator="percent of revenue", statistic="median")
    r.update(figure_key=1, report_slug="x", match="adjacent_band", gaps=[], series="Example Capital", also_printed_in=[], basis_group="revenue")
    md, _ = tr.build_spend([r], lambda _r: 1)
    assert "$20M-$100M (adjacent band)" in md


def _row(**kw):
    base = frow(source="Example Index", report="Example Index 2026", year="2026", metric="Gross Margin %", band="",
                value="78", unit="percent", denominator="total revenue", statistic="median", data_period="FY2025")
    base.update(figure_key=1, report_slug="x", match="exact", gaps=[], series="Example Index", also_printed_in=[],
                basis_group="", band_context=False)
    base.update(kw)
    base["value_num"] = fg.parse_value_num(base["value"])
    return base


def test_margins_public_first_then_bands_small_to_large_newest_first():
    rows = [_row(figure_key=1, band="$50M+", value="70", source="B", report_year="2025"),
            _row(figure_key=2, band="$5M-$25M", value="72", source="B", report_year="2025"),
            _row(figure_key=3, band="", value="78"),
            _row(figure_key=4, band="$5M-$25M", value="73", source="C", report_year="2026"),
            _row(figure_key=5, band="$25M-$50M", value="71", source="B", report_year="2025")]
    md, _ = tr.build_margins(rows, lambda _r: None)
    bands = [l.split("|")[2].strip() for l in table_lines(md)[2:]]
    assert bands == ["All sizes", "$5M-$25M", "$5M-$25M", "$25M-$50M", "$50M+"]
    # inside one band the newest edition leads
    vals = [l.split("|")[4].strip() for l in table_lines(md)[2:]]
    assert vals[1:3] == ["73%", "72%"]


def test_margins_segment_column_only_when_more_than_one_segment():
    one = [_row(figure_key=1, segment="private"), _row(figure_key=2, segment="private", value="77")]
    two = [_row(figure_key=1, segment="private"), _row(figure_key=2, segment="public", value="77")]
    assert "Segment" not in tr.build_margins(one, lambda _r: None)[0]
    assert "Segment" in tr.build_margins(two, lambda _r: None)[0]


def test_basis_values_stay_separate_in_margins():
    rows = [_row(figure_key=1, denominator="subscription revenue"), _row(figure_key=2, denominator="total revenue", value="74")]
    md, _ = tr.build_margins(rows, lambda _r: None)
    assert "subscription revenue" in md and "total revenue" in md


# --- placeholder handling and fallback ---------------------------------------------------------------------------

def test_missing_placeholder_appends_the_table_once_and_records_it(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, "Sales and marketing runs 30% to 38% of revenue at this size.")
    assert ans.text.count("| Line item |") == 1
    assert any("missing placeholder" in f for f in ans.prestep["tables"]["fallback"])


def test_repeated_and_unknown_placeholders_degrade_safely(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, "Intro.\n\n{{table:spend}}\n\nMiddle.\n\n{{table:spend}}\n\n{{table:bogus}}\n\nEnd.")
    assert ans.text.count("| Line item |") == 1 and "{{" not in ans.text
    fb = " ".join(ans.prestep["tables"]["fallback"])
    assert "repeated placeholder" in fb and "unknown placeholder" in fb


def test_a_half_written_placeholder_at_the_end_is_dropped():
    out, info = tr.render_tables("Text.\n\n{{table:sp", [], lambda r: None)
    assert out == "Text." and info["fallback"] == []


def test_no_rows_means_no_table_and_the_placeholder_is_removed():
    out, info = tr.render_tables("A.\n\n{{table:margins}}\n\nB.", [], lambda r: None)
    assert "{{" not in out and "no rows for margins" in info["fallback"][0]


# --- citation numbers --------------------------------------------------------------------------------------------

def test_every_rendered_marker_resolves_to_a_sources_entry(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, PROSE)
    ns = {c["n"] for c in ans.citations}
    import re
    used = {int(n) for l in table_lines(ans.text) + ans.text.split("\n") for n in re.findall(r"\[(\d+)\]", l)}
    assert used and used <= ns
    assert sorted(ns) == list(range(1, len(ns) + 1))        # contiguous, as extract_citations makes them
    assert all(c.get("figures") for c in ans.citations)


def test_a_report_the_prose_already_cited_keeps_its_number(lib, monkeypatch):
    def first_figures_doc(kw):
        blocks = kw["messages"][-1]["content"]
        docs = [b for b in blocks if b.get("type") == "document"]
        return next(i for i, d in enumerate(docs) if "Stored benchmark figures" in d["source"]["data"] and "S&M" in d["source"]["data"])

    ans, _ = run_turn(lib, monkeypatch, PROSE, cite_doc=first_figures_doc)
    first = ans.citations[0]
    assert first["n"] == 1
    titles = [c["title"] for c in ans.citations]
    assert len(titles) == len(set(titles))                  # appended entries never duplicate a cited report


# --- stored answer, rendering twins ------------------------------------------------------------------------------

def test_the_answer_text_carries_the_final_markdown_and_the_python_renderer_shows_a_table(lib, monkeypatch):
    ans, _ = run_turn(lib, monkeypatch, PROSE)
    assert "{{" not in ans.text
    html = render_answer_markdown(ans.text, ans.citations)
    assert html.count("<table>") == 2 and "<th>Line item</th>" in html and 'data-label="Value"' in html


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
def test_the_live_js_renderer_matches_the_python_twin_on_a_rendered_table(lib, monkeypatch, tmp_path):
    ans, _ = run_turn(lib, monkeypatch, PROSE)
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    js = src[i:j].replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    # Fixture reports have no landing link; a link-less cite renders plain in the Python twin and as <sup> live
    # (a difference that predates this change), so give every cite a link to compare the table markup itself.
    cites = [{"url": c["url"] or "https://example.test/r", "title": c["title"]} for c in ans.citations]
    script = tmp_path / "md.js"
    script.write_text("var CITES=" + json.dumps(cites) + ";\nvar CITE_SCOPE='';\n" + js +
                      "\nprocess.stdout.write(mdToHtml(require('fs').readFileSync(0,'utf8')));")
    live = subprocess.run([NODE, str(script)], input=ans.text, capture_output=True, text=True, check=True).stdout
    assert render_answer_markdown(ans.text, cites) == live
    assert live.count("<table>") == 2


# --- prompt and lint ---------------------------------------------------------------------------------------------

def test_the_model_is_told_to_write_placeholders_and_never_sees_a_rendered_table(lib, monkeypatch):
    ans, cap = run_turn(lib, monkeypatch, PROSE)
    blocks = cap["messages"][-1]["content"]
    plan = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    assert "{{table:spend}}" in plan and "TABLES THE SYSTEM BUILDS" in plan
    assert "| Line item |" not in plan


def test_model_written_tables_are_still_linted_but_rendered_ones_are_not(lib, monkeypatch):
    bad = ("Headcount counts.\n\n| Function | Q2 2025 |\n|---|---|\n| Total | 131 |\n| Eng | 40 |\n| Sales | 30 |\n\n{{table:spend}}")
    ans, _ = run_turn(lib, monkeypatch, bad)
    flags = " ".join(f["phrase"] for f in ans.lint_flags)
    assert "{{table" not in flags
    # the rendered table never produces an order or left-out cell flag
    assert "left out" not in " ".join(f.get("snippet", "") for f in ans.lint_flags if "cell" in f["phrase"])


def test_selfcheck_is_clean_for_a_rendered_table_and_flags_a_foreign_value():
    r = _row(figure_key=1)
    md, _ = tr.build_margins([r], lambda _r: 1)
    assert tr.selfcheck(md, [r]) == []
    assert tr.selfcheck(md.replace("78%", "99%"), [r])


def test_shape_rules_swap_the_spend_instructions_for_placeholders():
    from linklib import answer_rules as ar
    out = ar.shape_rules(["spend", "margins"])
    assert "{{table:spend}}" in out and "{{table:margins}}" in out and "Columns by basis" not in out
    assert ar.shape_rules([]) == ar.SHAPE_RULES


def test_the_left_out_line_survives_when_the_only_table_is_a_placeholder():
    from linklib.answer_strip import drop_left_out_without_table
    t = "Intro.\n\n{{table:spend}}\n\nLeft out (not sent): Example 2024 (3)."
    assert drop_left_out_without_table(t) == (t, [])
