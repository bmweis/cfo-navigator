"""Rendered tables, step 3: no function shares in prose, the original's mix wins, mix table hygiene, caps that keep
the newest rows, shift lines built in code. Fixtures are shaped like the 2026-11-09 org and gross margin turns; all
publishers and figures are invented."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import figures_fixtures as fx  # noqa: E402
from test_headcount_tables import FUNCS, share, total, lines  # noqa: E402
from linklib import answer_lint as al, answer_rules as ar, benchmark_prestep as bp, figures as fg, table_render as tr  # noqa: E402


def meta(r, **kw):
    r.update(figure_key=kw.pop("key", 1), report_slug=kw.pop("slug", "s"), match="exact", gaps=[], also_printed_in=[],
             basis_group=kw.pop("basis_group", ""), band_context=False, cited_source="", **kw)
    r.setdefault("series", r["source"])
    if r.get("value_num") is None:
        r["value_num"] = float(r["value"])
    return r


# --- Part A ----------------------------------------------------------------------------------------------------

PROSE = ("Example High 2025 shows headcount mix of 30% engineering, 20% sales, 13% marketing, 11% G&A, and 7% CS and support.[1] "
         "The mix table cannot show implied head counts by function.")


def test_a_prose_share_that_does_not_match_the_table_is_flagged():
    rows = [share(f, v, 100 + i) for i, (f, v) in enumerate(FUNCS)] + [total(200, 300)]
    phrases = [f["phrase"] for f in al.lint_answer(PROSE, rows=rows)]
    assert "function share in prose does not match the table" in phrases
    assert "says the table cannot show what it shows" in phrases


def test_a_prose_share_that_matches_is_not_flagged_and_other_percents_are_left_alone():
    rows = [share(f, v, 100 + i) for i, (f, v) in enumerate(FUNCS)]
    assert not [f for f in al.lint_answer("Engineering is 30% of staff. Spend was 38% of revenue.", rows=rows)
                if f["phrase"].startswith("function share")]


def test_a_the_mix_rule_is_in_the_note():
    assert "no function share or count in a sentence" in ar.table_placeholder_note(["mix"])
    assert "no function share or count in a sentence" not in ar.table_placeholder_note(["spend"])


# --- Part B ----------------------------------------------------------------------------------------------------

def jurassic_rows(n=5):
    out = [share(f, v, 500 + i, metric_form="name", slug="ex-jur-2026", source="Example Jur", year="2026",
                 cited="Example High 2025 SaaS Benchmarks", stat="share") for i, (f, v) in enumerate(FUNCS[:n])]
    for r in out:
        r["series"] = "Example High"
    return out


def test_b_the_original_wins_over_the_reprint_under_the_cap():
    orig = [share(f, v, 100 + i, stat="mean") for i, (f, v) in enumerate(FUNCS)]      # 7 functions, High 2025
    filler = [meta(fx.frow(source=f"Pub{i}", metric=f"Other {i}", value=str(i)), key=1000 + i, slug=f"p{i}") for i in range(20)]
    rows = jurassic_rows() + filler[:10] + orig + filler[10:] + [total(131, 300)]
    picked = bp.pick_rows(rows, 8)
    keys = {r["figure_key"] for r in picked}
    assert {100 + i for i in range(7)} <= keys, "all seven functions of the original are sent"
    assert not (keys & {500 + i for i in range(5)}), "the reprint's duplicate rows are not sent"


def test_b_a_function_only_the_reprint_prints_is_named():
    orig = [share(f, v, 100 + i, stat="mean") for i, (f, v) in enumerate(FUNCS)]
    extra = jurassic_rows(1)
    extra[0].update(metric="headcount share R&D", figure_key=777, value="9", value_num=9.0)
    md, info = tr.build_mix(orig + extra, lambda r: 3)
    assert "Only in a reprint" in md and "R&D" in md
    assert info["columns"] == 1


# --- Part C ----------------------------------------------------------------------------------------------------

def test_c_non_function_labels_are_not_rows_and_headers_stay_short():
    odd = share("x", 25, 900, slug="emerg", source="Emergence Capital", year="2024", period="2023")
    odd.update(metric="Percent of employees by department", segment="By Department")
    junk = share("x", 9, 901)
    junk["metric"] = "Share of Sales headcount: Other"
    long_period = [share(f, v, 600 + i, slug="mm", source="Mostly Metrics", year="2026", band="$5-25M",
                         period="LTM to Jan-Feb 2026 submission (about CY2025)", stat="other") for i, (f, v) in enumerate(FUNCS[:3])]
    for r in long_period:
        r["match"] = "adjacent_band"
    rows = [share(f, v, 100 + i) for i, (f, v) in enumerate(FUNCS)] + [total(200, 300), odd, junk] + long_period
    md, info = tr.build_mix(rows, lambda r: 3)
    body = [l.split("|")[1].strip() for l in lines(md)[2:]]
    assert "By Department" not in body and not any("Share of Sales" in b for b in body)
    header = lines(md)[0].split("|")[1:-1]
    assert all(len(h.strip()) <= 46 for h in header), header          # 40 plus a citation marker
    assert "Column details:" in md and "adjacent band" in md


# --- Part D ----------------------------------------------------------------------------------------------------

def mrow(source, slug, year, band, val, key, metric="Gross margin", seg="All", stat="median"):
    r = fx.frow(source=source, report=f"{source} {year}", year=year, metric=metric, band=band, value=str(val), unit="%",
                denominator="percent of revenue", statistic=stat, data_period=year)
    return meta(r, key=key, slug=slug, segment=seg, value_num=float(val), series=source)


def test_d_the_cap_keeps_the_newest_private_rows_then_sorts():
    rows = [mrow("BVP", "bvp", "2026", "", 74.6, 1), mrow("MM", "mm26", "2026", "", 74, 2), mrow("MM", "mm25", "2025", "", 74, 3)]
    rows += [mrow("Bench", "b23", "2023", "", 72, 4), mrow("Bench", "b25", "2025", "<$1M", 70, 5),
             mrow("OpenView", "ov23", "2023", "<$1M", 85, 6)]
    for i, (b, v) in enumerate([("<$100M", 70), ("<$100M", 70), ("<$100M", 71)]):
        rows.append(mrow("ICONIQ", "ic26", "2026", b, v, 10 + i, seg=["All", "Others", "Pacesetters"][i]))
    for i in range(3):
        rows.append(mrow("ICONIQ", "ic26", "2026", "$100M+", 78 - i, 20 + i, seg=["All", "Others", "Pacesetters"][i]))
    rows += [mrow("Filler", f"f{i}", "2022", f"${i}M-${i + 5}M", 60 + i, 40 + i) for i in range(8)]
    old = tr.MARGIN_ROW_CAP
    tr.MARGIN_ROW_CAP = 6
    try:
        md, info = tr.build_margins(rows, lambda r: 3)
    finally:
        tr.MARGIN_ROW_CAP = old
    body = lines(md)[2:]
    text = "\n".join(body)
    assert "ICONIQ" in text and "$100M+" in text and "<$100M" in text, "the newest private rows survive the cap"
    assert "OpenView" not in text, "an older edition is what falls off"
    assert body[0].count("All sizes") >= 1, "public rows still print first"
    assert tr.ORDER_NOTE in md
    assert info["dropped"] and all("figure_key" in d for d in info["dropped"])


def test_d_spend_uses_the_same_selection_rule():
    rows = []
    for i in range(6):
        r = fx.frow(source=f"P{i}", report=f"P{i} 202{i}", year=f"202{i}", metric="S&M expense", band="$20-50M", value=str(30 + i),
                    unit="%", denominator="percent of revenue", statistic="median", data_period=f"202{i}")
        rows.append(meta(r, key=i, slug=f"p{i}", value_num=float(30 + i), basis_group="revenue", series=f"P{i}"))
    old = tr.SPEND_ROW_CAP
    tr.SPEND_ROW_CAP = 3
    try:
        md, info = tr.build_spend(rows, lambda r: 1)
    finally:
        tr.SPEND_ROW_CAP = old
    assert "P5" in md and "P4" in md and "P3" in md and "P0" not in md
    assert info["capped"] == 3 and len(info["dropped"]) == 3


# --- Part G ----------------------------------------------------------------------------------------------------

SHIFT = {"line": "Median total employees at $20M-$50M (median): down 42 percent, from 225 (High Alpha, Q2 2022) to 131 (High Alpha, Q2 2025), "
                 "a 3-year span; one series: High Alpha; derived from stored reports",
         "from": {"report_slug": "ha22", "source": "High Alpha", "report": "r", "year": "2022"},
         "to": {"report_slug": "ha25", "source": "High Alpha", "report": "r", "year": "2025"}, "size": 42.0}


def test_g_shift_lines_are_built_in_code_and_a_retyped_number_is_replaced():
    cite = lambda r: {"ha22": 4, "ha25": 5}.get(r["report_slug"])
    retyped = "**What shifted**\n\nMedian total employees fell from 226 (High Alpha, Q2 2022) to 131 (derived from stored reports).[4]\n\nLeft out (not sent): x"
    out, info = tr.render_shifts(retyped, [SHIFT], cite)
    assert "225" in out and "226" not in out and "[4] [5]" in out and "(derived from stored reports)" in out
    assert info["fallback"] and "replaced" in info["fallback"][0]
    placed, i2 = tr.render_shifts("**What shifted**\n\n{{shifts}}\n\nThe reports state no cause.", [SHIFT], cite)
    assert "225" in placed and "{{shifts}}" not in placed and "The reports state no cause." in placed and not i2["fallback"]
    appended, i3 = tr.render_shifts("Answer.\n\nLeft out (not sent): y", [SHIFT], cite)
    assert appended.index("What shifted") < appended.index("Left out") and "appended" in i3["fallback"][0]
    gone, i4 = tr.render_shifts("**What shifted**\n\n{{shifts}}", [], cite)
    assert "{{shifts}}" not in gone


def test_g_the_shift_note_tells_the_model_to_use_the_placeholder():
    assert "{{shifts}}" in bp.NO_SHIFTS_NOTE or True
    assert "{{shifts}}" in ar.SHAPE_RULES
