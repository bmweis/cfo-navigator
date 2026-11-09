"""Code-rendered tables, step 2: total headcount and headcount mix, plus the step 1 ride-alongs (rounding, one
band spelling, margins order, uncited shift lines, one report's functions kept together). Invented data only."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import figures_fixtures as fx  # noqa: E402
from linklib import benchmark_prestep as bp, figures as fg, table_render as tr  # noqa: E402

FUNCS = [("Engineering", 30), ("Product and design", 10), ("Sales", 13), ("Marketing", 7),
         ("CS and support", 20), ("G&A", 11), ("Professional services", 8)]


def share(fn, val, key, slug="ex-high-2025", source="Example High", year="2025", band="$20-50M", metric_form="colon",
          cited="", stat="mean", period="Q2 2025"):
    name = f"headcount share: {fn[0].lower() + fn[1:] if fn[:2] not in ('CS', 'G&') else fn}" if metric_form == "colon" else f"headcount share {fn}"
    r = fx.frow(source=source, report=f"{source} {year}", year=year, metric=name, band=band, value=str(val), unit="%",
                denominator="percent of total employees", statistic=stat, data_period=period)
    r.update(figure_key=key, report_slug=slug, match="exact", gaps=[], series=source, also_printed_in=[],
             basis_group="", band_context=False, cited_source=cited, value_num=float(val))
    return r


def total(val, key, slug="ex-high-2025", source="Example High", year="2025", band="$20-50M", stat="mean", period="Q2 2025", cited=""):
    r = fx.frow(source=source, report=f"{source} {year}", year=year, metric="Total employees", band=band, value=str(val),
                unit="count", denominator="full-time employees", statistic=stat, data_period=period)
    r.update(figure_key=key, report_slug=slug, match="exact", gaps=[], series=source, also_printed_in=[],
             basis_group="", band_context=False, cited_source=cited, value_num=float(val))
    return r


def mix_rows():
    return [share(f, v, 100 + i) for i, (f, v) in enumerate(FUNCS)] + [total(200, 300)]


def lines(md):
    return [l for l in md.split("\n") if l.startswith("|")]


# --- Part A: one report's functions stay together under a cap -------------------------------------------------

def test_a_mix_report_is_kept_whole_under_a_tight_cap():
    other = [fx.frow(source=f"Pub{i}", metric=f"Filler {i}", value=str(i)) for i in range(30)]
    for i, r in enumerate(other):
        r.update(figure_key=1000 + i, report_slug=f"p{i}", match="adjacent_band", gaps=[], series=f"Pub{i}", also_printed_in=[],
                 basis_group="", band_context=False, cited_source="")
    rows = mix_rows()[:3] + other + mix_rows()[3:]       # functions split by many better-ranked rows
    picked = bp.pick_rows(rows, 8)
    kept = {r["figure_key"] for r in picked if r["figure_key"] < 200}
    assert kept == {100 + i for i in range(len(FUNCS))}, "all seven functions of the report survive the cap"


def test_other_rows_still_obey_the_cap():
    other = [fx.frow(source=f"Pub{i}", metric=f"Filler {i}", value=str(i)) for i in range(30)]
    for i, r in enumerate(other):
        r.update(figure_key=1000 + i, report_slug=f"p{i}", match="exact", gaps=[], series=f"Pub{i}", also_printed_in=[],
                 basis_group="", band_context=False, cited_source="")
    picked = bp.pick_rows(mix_rows() + other, 8)
    assert len(picked) <= 8 + len(FUNCS) - 1


# --- Part C: the mix table -----------------------------------------------------------------------------------

def test_mix_table_has_every_function_labelled_with_its_own_share_and_no_match_column():
    md, meta = tr.build_mix(mix_rows(), lambda r: 3)
    body = lines(md)
    assert "match" not in md.lower().split("\n")[2].lower()
    got = {l.split("|")[1].strip(): l.split("|")[2].strip().split(" ")[0] for l in body[2:]}
    assert got == {"Engineering": "30%", "Product and design": "10%", "Sales": "13%", "Marketing": "7%",
                   "CS and support": "20%", "G&A": "11%", "Professional services": "8%"}
    assert [l.split("|")[1].strip() for l in body[2:]] == [f for f, _ in FUNCS]        # as the report prints them
    assert tr.mix_selfcheck(md, mix_rows()) == []


def test_derived_counts_are_labelled_and_the_total_is_named_once_not_in_every_cell():
    md, meta = tr.build_mix(mix_rows(), lambda r: 3)
    assert meta["derived"] is True
    rows = {l.split("|")[1].strip(): l.split("|")[-2].strip() for l in lines(md)[2:]}
    assert rows["Sales"] == "26" and rows["Engineering"] == "60"          # 13% x 200, 30% x 200
    assert md.count("derived, not in the report") == 1 and "Derived people" in md
    assert "200" in md.split("Derived people =")[1]
    assert tr.selfcheck(md, mix_rows(), extra=meta["allowed_extra"]) == []


def test_no_total_means_no_count_column():
    md, meta = tr.build_mix(mix_rows()[:-1], lambda r: 3)
    assert meta["derived"] is False and "Derived people" not in md


def test_metric_name_form_and_colon_form_give_the_same_function_labels():
    a = share("Sales", 13, 1, metric_form="name", slug="ex-jur-2026", source="Example Jur", year="2026", cited="Example High 2025", stat="share")
    b = share("Sales", 13, 2)
    assert fg.share_function(a).lower() == fg.share_function(b).lower() == "sales"


def test_a_republisher_and_its_original_are_one_column_and_the_original_wins():
    jur = [share(f, v, 500 + i, metric_form="name", slug="ex-jur-2026", source="Example Jur", year="2026",
                 cited="Example High 2025 SaaS Benchmarks", stat="share") for i, (f, v) in enumerate(FUNCS)]
    for r in jur:
        r["series"] = "Example High"
    md, meta = tr.build_mix(mix_rows() + jur, lambda r: 3)
    assert meta["columns"] == 1
    assert len(lines(md)[2:]) == len(FUNCS)


def test_rows_the_data_does_not_name_are_listed_not_added():
    odd = share("x", 100, 900)
    odd["metric"] = "Headcount mix: sum of six functions"
    md, meta = tr.build_mix(mix_rows() + [odd], lambda r: 3)
    assert len(lines(md)[2:]) == len(FUNCS)


def test_two_editions_never_share_a_column_and_the_older_is_named_once():
    old = [share(f, v + 1, 700 + i, slug="ex-high-2024", year="2024", period="Q2 2024") for i, (f, v) in enumerate(FUNCS)]
    new = mix_rows()
    md, meta = tr.build_mix(old + new, lambda r: 3)
    assert meta["columns"] == 1 and "Not shown, another edition or band" in md and "2024" in md


# --- Part B: the total headcount table -----------------------------------------------------------------------

def test_count_table_has_no_notes_column_and_orders_newest_first_within_a_band():
    rows = [total(190, 1, slug="a", source="A", year="2024", period="Q2 2024"), total(200, 2, slug="b", source="B", year="2026", period="Q2 2026"),
            total(150, 3, slug="c", source="C", year="2025", band="$5-25M")]
    md, _ = tr.build_headcount(rows, lambda r: None)
    head = lines(md)[0]
    assert head == "| Report and period | Band | Statistic | Value | Basis |"
    firsts = [l.split("|")[1].strip() for l in lines(md)[2:]]
    assert firsts[0].startswith("C 2025") and firsts[1].startswith("B 2026") and firsts[2].startswith("A 2024")
    assert [l.split("|")[4].strip() for l in lines(md)[2:]] == ["150", "200", "190"]          # a count, no unit text


def test_different_statistics_and_denominators_are_not_merged():
    rows = [total(200, 1), total(180, 2, stat="median"), total(210, 3)]
    rows[2]["denominator"] = "FTEs and contractors"
    md, _ = tr.build_headcount(rows, lambda r: None)
    assert len(lines(md)[2:]) == 3


# --- placeholders, fallback, citations -----------------------------------------------------------------------

def test_placeholders_render_and_a_missing_one_is_appended_and_recorded():
    rows = mix_rows()
    text, info = tr.render_tables("Intro.\n\n{{table:headcount}}\n\n{{table:mix}}\n\nEnd.", rows, lambda r: 4)
    assert "Headcount mix" in text and "Total headcount" in text and info["fallback"] == []
    text2, info2 = tr.render_tables("Intro only.", rows, lambda r: 4)
    assert any("missing placeholder: mix" in f for f in info2["fallback"]) and "Headcount mix" in text2
    assert info["rendered"]["mix"]["rows"] == len(FUNCS)


def test_shape_rules_and_note_name_the_new_placeholders():
    from linklib.answer_rules import shape_rules, table_placeholder_note
    plan = shape_rules(["headcount", "mix", "spend"])
    assert "{{table:headcount}}" in plan and "{{table:mix}}" in plan and "{{table:spend}}" in plan
    assert "{{table:mix}}" in table_placeholder_note(["mix"])
    assert "Match note" not in bp.PRESTEP_RULES and "match note" not in bp.PRESTEP_RULES


# --- ride-alongs ---------------------------------------------------------------------------------------------

def test_a_long_decimal_is_rounded_to_one_decimal_and_a_short_one_is_left():
    assert tr.round_text("74.5783946473%") == "74.6%" and tr.round_text("74.5%") == "74.5%" and tr.round_text("0.25") == "0.25"


def test_band_spellings_are_one_spelling():
    assert tr.band_display("$20M-$50M") == tr.band_display("$20-50M") == "$20-50M"
    assert tr.band_display("Under $1M") == "<$1M" and tr.band_display("above $1M (report text)") == "above $1M (report text)"
    assert tr.band_display("$50M+") == "$50M+"


def test_margins_run_public_first_then_band_size_with_other_margins_in_the_same_order():
    def m(key, band, metric, val="70", year="2026"):
        r = fx.frow(source="B", report="B", year=year, metric=metric, band=band, value=val, unit="percent", denominator="revenue")
        r.update(figure_key=key, report_slug="b", match="exact", gaps=[], series="B", also_printed_in=[], basis_group="", band_context=False)
        return r
    rows = [m(1, "<$100M", "Gross Margin"), m(2, "$20M-$50M", "Gross Margin"), m(3, "$20M-$50M", "Subscription Gross Margin"), m(4, "", "Gross Margin")]
    md, meta = tr.build_margins(rows, lambda r: None)
    bands = [l.split("|")[2].strip() for l in lines(md)[2:]]
    assert bands == ["All sizes", "$20-50M", "$20-50M", "<$100M"]
    assert "sort" in meta and tr.selfcheck(md, rows) == []


def test_the_margin_value_prints_rounded_and_selfcheck_accepts_it():
    r = fx.frow(source="B", report="B", metric="Gross Margin", band="", value="74.5783946473", unit="percent", denominator="revenue")
    r.update(figure_key=1, report_slug="b", match="exact", gaps=[], series="B", also_printed_in=[], basis_group="", band_context=False)
    md, _ = tr.build_margins([r], lambda r: None)
    assert "74.6%" in md and "74.5783" not in md and tr.selfcheck(md, [r]) == []


def test_shift_lines_without_a_marker_get_the_reports_markers():
    shifts = [{"from": {"report_slug": "a", "source": "A", "report": "A rep", "year": "2024"},
               "to": {"report_slug": "b", "source": "B", "report": "B rep", "year": "2026"}}]
    nums = {"a": 1, "b": 2}
    text, n = tr.cite_shifts("**What shifted**\nGross margin up 3 points, 70% to 73% (derived from stored reports)\n", shifts,
                             lambda row: nums.get(row["report_slug"]))
    assert n == 1 and "[1] [2] (derived from stored reports)" in text
    again, n2 = tr.cite_shifts(text, shifts, lambda row: nums.get(row["report_slug"]))
    assert n2 == 0 and again == text


def test_a_sent_row_no_table_took_is_named_on_the_admin_row():
    r = fx.frow(metric="Weird thing", value="5", unit="x")
    r.update(figure_key=9, report_slug="w", match="exact", gaps=[], series="X", also_printed_in=[], basis_group="", band_context=False)
    _, info = tr.render_tables("{{table:mix}}", mix_rows() + [r], lambda r: 1)
    assert info["not_in_table"][0]["metric"] == "Weird thing"


def test_render_tables_gives_the_mix_builder_the_totals_so_the_derived_column_appears():
    text, info = tr.render_tables("{{table:mix}}", mix_rows(), lambda r: 3)
    assert info["rendered"]["mix"]["derived"] is True and "Derived people" in text and "| 26 |" in text
    assert "selfcheck" not in info
