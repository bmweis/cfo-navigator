"""PR 1: spend and margin labels, a headcount growth rate is not a function share, and the draft-leak flag.
Fixtures are invented; the metric and denominator strings are the shapes production row 75 printed."""
import pytest

from linklib import figures as fg
from linklib import table_render as tr
from linklib import answer_lint as al


def _row(metric, value, key, **kw):
    r = dict(figure_key=key, report_slug="ex-mm-2026", source="Example Metrics", report="2026 Example Club", report_year="2026",
             data_period="LTM through May 2026", band="$25-50M", segment="", metric=metric, denominator="percent of revenue",
             statistic="median", value=str(value), value_num=float(value), unit="%", match="adjacent_band", cited_source="",
             series="Example Metrics", band_context=False)
    r.update(kw)
    return r


# --- Part A: labels -------------------------------------------------------------------------------------------

def test_split_sales_and_marketing_lines_print_as_words():
    rows = [_row("Sales expense", 30, 1), _row("Marketing expense", 12, 2)]
    assert {fg.expense_line_of(r["metric"], r["denominator"], r["unit"]) for r in rows} == {"sales", "mkt"}
    md, _ = tr.build_spend(rows, lambda r: 1)
    body = [ln.split("|")[1].strip() for ln in md.split("\n") if ln.startswith("| ") and not ln.startswith("| Line item |")]
    assert body == ["Sales", "Marketing"]


def test_margin_labels_are_one_form_per_table():
    rows = [_row("gross margin", 74, 1, band="Public"), _row("Gross margin", 73, 2, band="$25-50M"),
            _row("Gross Margin", 70, 3, band="$50M+"), _row("net margin", 5, 4)]
    md, _ = tr.build_margins(rows, lambda r: 1)
    labels = [ln.split("|")[1].strip() for ln in md.split("\n") if ln.startswith("| ") and not ln.startswith("| Metric |")]
    assert len(labels) == 4
    assert {x for x in labels if x.lower().startswith("gross")} == {"Gross margin"}
    assert "Net margin" in labels


# --- Part B: a growth rate is not a function share ------------------------------------------------------------

REAL_DENOMS = ["yoy change headcount period not stated", "prior year headcount yoy", "change total employe",
               "change headcount ltm basi", "prior year headcount"]


@pytest.mark.parametrize("metric,denom", [("headcount growth", d) for d in REAL_DENOMS] + [("Headcount Growth", "change total employe")])
def test_headcount_growth_is_not_a_mix_row(metric, denom):
    r = _row(metric, 11, 7, denominator=denom)
    assert fg.row_table_kind(r) == "headcount_share"      # unchanged classification
    assert not fg.mix_function_ok(r)
    assert tr.mix_share_rows([r]) == []


def test_growth_row_lands_in_not_in_table_and_no_mix_table_is_built():
    rows = [_row("headcount growth", 11, 7, denominator="yoy change headcount period not stated")]
    out, info = tr.render_tables("Intro {{table:mix}}", rows, lambda r: 1)
    assert "mix" not in info["rendered"]
    assert "Growth" not in out
    assert [x["figure_key"] for x in info["not_in_table"]] == [7]


def test_real_function_shares_still_render_beside_a_growth_row():
    rows = [_row("headcount share: sales", 13, 1, denominator="percent of total employees"),
            _row("headcount growth", 11, 2, denominator="prior year headcount yoy")]
    md, info = tr.build_mix(rows, lambda r: 1)
    assert "Sales" in md and "Growth" not in md and info["rows"] == 1


# --- Part C: draft-leak flag ------------------------------------------------------------------------------------

LEAKS = ["ahead of $5M-$25M at 44%, wait, actually $5M-$25M printed higher.", "Wait, that is wrong.",
         "The value is 12. Hold on, it is 13.", "Let me recheck the band.", "Hmm, that does not match.",
         "Median is 30%—wait, actually 28%."]
CLEAN = ["She will wait for the next edition.", "Actually 13 reports printed it.", "The wait time is two weeks.",
         "Both bands held on, and hold their level."]


@pytest.mark.parametrize("text", LEAKS)
def test_draft_leak_is_flagged(text):
    assert any(f["phrase"] == "draft leak" for f in al.lint_answer(text))


@pytest.mark.parametrize("text", CLEAN)
def test_clean_prose_is_not_a_draft_leak(text):
    assert not any(f["phrase"] == "draft leak" for f in al.lint_answer(text))


def test_draft_leak_flag_does_not_change_text():
    t = "Up 5%, wait, actually 4%."
    al.lint_answer(t)
    assert t == "Up 5%, wait, actually 4%."
