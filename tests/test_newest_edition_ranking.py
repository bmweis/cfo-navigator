"""The newest-edition line must survive the print cap (invented publishers; shape of ask_questions id 95)."""
from linklib import figures as fg


def _mk(key, slug, source, report, year, per, val, metric, unit="%", band="$20-50M", denom=""):
    return dict(figure_key=key, report_slug=slug, source=source, report=report, report_year=year, data_period=per,
                band=band, segment="", metric=metric, denominator=denom, statistic="median",
                value=str(val), value_num=float(val), unit=unit, match="exact", cited_source="", series=source,
                band_context=False, n="", band_basis="ARR", gaps=[], basis_group="", other_basis=False, period_gap=0,
                canonical_metric="", also_printed_in=[], link="", page="")


def _pool():
    rows = [
        # Employees: one edition's own history (225 to 131) plus a newer edition with one later point (99)
        _mk(1, "ss-2025", "Sample Survey Co", "Sample Survey 2025", "2025", "Q2 2022", 225, "employees", unit="count", denom="count"),
        _mk(2, "ss-2025", "Sample Survey Co", "Sample Survey 2025", "2025", "Q2 2025", 131, "employees", unit="count", denom="count"),
        _mk(3, "ss-2026", "Sample Survey Co", "Sample Survey 2026", "2026", "Q2 2026", 99, "employees", unit="count", denom="count"),
    ]
    # three other families, each a bigger-or-equal-ranked older-only line
    for i, (metric, a, b) in enumerate([("S&M expense", 54, 35), ("R&D expense", 34, 29), ("G&A expense", 29, 25)]):
        slug = f"ex-{i}"
        rows.append(_mk(10 + 2 * i, slug, f"Example Pub {i}", f"Example Report {i}", "2024", "2021", a, metric, denom="revenue"))
        rows.append(_mk(11 + 2 * i, slug, f"Example Pub {i}", f"Example Report {i}", "2024", "2023", b, metric, denom="revenue"))
    return rows


def test_pool_emits_the_newest_edition_line():
    out = fg.compute_shifts(_pool())
    lines = [s["line"] for s in out["shifts"]]
    assert any("from 131" in l and "to 99" in l for l in lines), lines     # cause (a) ruled out: it is emitted


def test_newest_edition_line_is_not_cut_by_the_cap_of_four():
    out = fg.compute_shifts(_pool())
    chosen, _ = fg.rank_shifts(out["shifts"], 4, asked_band="$20-30M")
    lines = [s["line"] for s in chosen]
    assert len(chosen) == 4
    assert any("from 131" in l and "to 99" in l for l in lines), lines      # the newest figure prints
    assert any("from 225" in l and "to 131" in l for l in lines), lines     # and so does the history


def test_newest_edition_line_outranks_a_bigger_older_only_line_in_another_family():
    shifts = fg.compute_shifts(_pool())["shifts"]
    for s in shifts:                       # make every older-only line from the other families far larger
        if s["metric"] != "employees":
            s["size"] = s["size"] * 10
    chosen, _ = fg.rank_shifts(shifts, 2, asked_band="$20-30M")
    assert any("from 131" in s["line"] and "to 99" in s["line"] for s in chosen)


def test_a_small_newest_edition_line_does_not_displace_more_than_the_slots_it_needs():
    chosen, _ = fg.rank_shifts(fg.compute_shifts(_pool())["shifts"], 4, asked_band="$20-30M")
    assert len(chosen) == 4                 # SHIFT_LINES stays 4: nothing is added, one older-only line gives way
