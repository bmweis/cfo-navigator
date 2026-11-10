"""Rendered tables, step 4: the mix family keeps "Product and design", prose limits, shift lines, headcount table hygiene.
Fixtures are invented ("High Alpha"-shaped names are the publisher labels the loader prints; every number is made up)."""
from linklib import figures as fg
from linklib import table_render as tr
from linklib import answer_lint as al
from linklib import answer_rules as ar
from linklib import benchmark_prestep as bp


def _row(metric, value, key, **kw):
    r = dict(figure_key=key, report_slug="ex-high-2025", source="Example High", report="2025 Example Benchmarks", report_year="2025",
             data_period="Q2 2025", band="$20-50M", segment="", metric=metric, denominator="percent of total employees",
             statistic="mean", value=str(value), value_num=float(value), unit="%", match="adjacent_band", cited_source="",
             series="Example High", band_context=False)
    r.update(kw)
    return r


# --- Part A: the headcount family keeps "product and design" ------------------------------------------------

def test_family_rule_accepts_product_and_design():
    assert fg.headcount_family_row("headcount share: product and design")


def test_family_rule_still_refuses_a_ratio_and_a_combined_row():
    assert not fg.headcount_family_row("ARR per employee")
    r = _row("headcount share: engineering plus product and design", 40, 9)
    assert not fg.mix_function_ok(r)           # two functions in one row is not one function


def test_mix_table_has_all_seven_functions():
    names = ["engineering", "product and design", "sales", "marketing", "cs and support", "g&a", "professional services"]
    rows = [_row(f"headcount share: {n}", 10 + i, i + 1) for i, n in enumerate(names)]
    md, info = tr.build_mix(rows, lambda r: 4)
    assert info["rows"] == 7 and "Product and design" in md


# --- Part B: prose limits --------------------------------------------------------------------------------------

def test_rules_forbid_describing_the_mix_table_and_writing_a_left_out_sentence():
    note = ar.table_placeholder_note(["mix", "spend"])
    low = note.lower()
    assert "columns, editions, statistics or sources" in low
    assert "left out" in low and "never write" in low


def test_spend_percent_of_revenue_sentence_is_not_a_mix_claim():
    rows = [_row("headcount share: sales", 13, 1), _row("headcount share: marketing", 7, 2)]
    text = ("Mostly Metrics (LTM through early 2026) shows sales at 38% and marketing at 19% of revenue at the $25M-$50M band, "
            "with engineering at 30%, product at 18%, G&A at 16%, and support at 9%.[2]")
    assert al.mix_prose_problems(text, rows) == []


def test_old_mix_prose_sentence_is_still_flagged():
    rows = [_row("headcount share: sales", 13, 1), _row("headcount share: marketing", 7, 2), _row("headcount share: cs and support", 20, 3)]
    text = "The mix is 20% sales, 13% marketing and 7% CS."
    assert al.mix_prose_problems(text, rows)


# --- Part D: shift lines ----------------------------------------------------------------------------------------

def _shift(metric, size, **kw):
    s = {"metric": metric, "band": "$20-50M", "segment": "", "statistic": "median", "kind": "points", "size": size, "threshold": 3.0,
         "line": f"{metric} line", "from": {"report_slug": "a", "source": "S", "report": "R", "year": "2022"},
         "to": {"report_slug": "b", "source": "S", "report": "R", "year": "2025"}}
    s.update(kw)
    return s


def test_headcount_shift_is_not_pushed_off_by_two_spend_lines():
    shifts = [_shift("Sales and Marketing Expenses % of Revenue", 19.0),
              _shift("S&M expense", 17.0),
              _shift("employees", 41.8, kind="relative", unit="count", threshold=10.0),
              _shift("Subscription gross margin", 6.0)]
    shifts[2]["unit"] = "count"
    for s in shifts[:2] + shifts[3:]:
        s["unit"] = "percent"
    out, _ = fg.rank_shifts(shifts, 4, text="total employees and expense as a percent of revenue")
    assert "employees" in [s["metric"] for s in out[:2]]


def test_shift_line_names_the_publisher_once():
    a = {"source": "KeyBanc Capital Markets and Sapphire Ventures", "report": "KeyBanc Capital Markets and Sapphire Ventures SaaS Survey 2025",
         "data_period": "2024"}
    assert fg._ends(a) == "KeyBanc Capital Markets and Sapphire Ventures SaaS Survey 2025, 2024"


def test_span_under_a_year_is_said_in_months():
    a = {"data_period": "LTM to 2025-11-15", "report_year": "2025"}
    b = {"data_period": "as of 2026-05-09", "report_year": "2026"}
    assert fg.span_text(a, b) == "a span of about 6 months"
    assert fg.span_text({"data_period": "2021", "report_year": "2023"}, {"data_period": "2023", "report_year": "2023"}) == "a 2-year span"
    assert fg.span_text({"data_period": "2023", "report_year": "2023"}, {"data_period": "2023", "report_year": "2023"}) == ""


def test_shift_line_names_the_segment_when_there_is_one():
    from linklib.figures import compute_shifts
    def mk(slug, per, val, seg, key, yr):
        return dict(figure_key=key, report_slug=slug, source="Example Mostly", report="Club " + slug, report_year=yr, data_period=per,
                    band="", segment=seg, metric="Gross Margin % (LTM)", denominator="percent of revenue", statistic="median",
                    value=str(val), value_num=float(val), unit="%", match="exact", cited_source="", series="Example Mostly",
                    band_context=False, n="10", band_basis="none", gaps=[], basis_group="", other_basis=False, period_gap=0, canonical_metric="",
                    also_printed_in=[], link="", page="")
    rows = [mk("nov", "LTM to 2025-11-15", 78, "public, top 10 median", 1, "2025"), mk("may", "as of 2026-05-09", 73, "public, top 10 median", 2, "2026")]
    out = compute_shifts(rows)
    assert out["shifts"] and "public, top 10 median" in out["shifts"][0]["line"]
    assert "about 6 months" in out["shifts"][0]["line"]


class _Cite:
    def __init__(self):
        self.calls = []

    def __call__(self, row):
        self.calls.append(row["report_slug"])
        return {"a": 9, "b": 12}.get(row["report_slug"])


def test_shift_line_cites_both_endpoints():
    sh = _shift("S&M expense", 17.0, line="S&M expense at $25M-$50M (median): down 17 points; derived from stored reports")
    line = tr.shift_line(sh, _Cite())
    assert "[9]" in line and "[12]" in line


def test_table_cite_adds_a_report_that_was_not_sent_when_asked():
    citations = [{"n": 1, "title": "Sent", "url": "u", "type": "library", "figures": True}]
    sent_docs = [{"title": "Sent", "url": "u", "type": "library", "identity": ["s1", "x", "y", "2025"]}]

    def extra(row):
        return {"title": "Old report (2022)", "url": "http://old", "type": "library", "label": {"org": "Old"}}
    cite = bp.table_cite(citations, sent_docs, extra=extra)
    n = cite({"report_slug": "old", "source": "Old", "report": "Old report", "report_year": "2022"})
    assert n == 2 and citations[-1]["title"] == "Old report (2022)" and citations[-1]["figures"] is True
    assert cite({"report_slug": "old", "source": "Old", "report": "Old report", "report_year": "2022"}) == 2   # no second entry
    assert len(citations) == 2
    # without `extra`, behaviour is unchanged: a report that was not sent gets none
    assert bp.table_cite([], sent_docs)({"report_slug": "old", "source": "o", "report": "o", "report_year": "2022"}) is None


# --- Part E: total headcount hygiene ----------------------------------------------------------------------------

def _total(key, stat, val, band, match="exact", **kw):
    r = _row("employees", val, key, denominator="count", unit="count", statistic=stat, band=band, match=match,
             source="Example Key", report="Example Key Survey", report_slug="ex-key", report_year="2025", series="Example Key",
             data_period="2025", cited_source="")
    r.update(kw)
    return r


def test_target_and_context_rows_stay_out_of_the_total_headcount_table():
    rows = [_total(1, "median", 131, "$20-50M"),
            _total(2, "target", 11, "", match="adjacent_band", band_context=True, data_period="2026E")]
    md, info = tr.build_headcount(rows, lambda r: 2)
    assert "| 11 |" not in md and "| 131 |" in md
    assert info["rows"] == 1 and info.get("excluded") == 1
    assert md.count("Not shown, not a headcount count") == 1 and "2026E, target" in md.split("Not shown")[1]


def test_band_cell_does_not_repeat_itself():
    r = _total(3, "median", 181, "", match="adjacent_band", band_context=True)
    assert tr._band_text(r) == "All sizes (context)"
