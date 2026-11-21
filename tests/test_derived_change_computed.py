"""Derived-change lint false positives (ask_questions ids 83 and 85, 2026-11-20).

The shifts fixture has the stored `prestep_json.shifts` shape of id 85 (kind, size, threshold, metric, band,
statistic, from, to, carried). Publisher names and line text are replaced with "Example Benchmarks": third-party
report text stays out of the repo; only the numbers and keys are real.
"""
from linklib import answer_lint as al


def _shift(kind, size, metric, band, threshold, carried=False):
    return {"line": f"{metric} at {band}: example line, derived from stored reports", "kind": kind, "size": size,
            "threshold": threshold, "directional": False, "metric": metric, "band": band, "statistic": "median",
            "from": {"report_slug": "example-2023", "source": "Example Benchmarks", "period": "2021", "value": "0"},
            "to": {"report_slug": "example-2023", "source": "Example Benchmarks", "period": "2022", "value": "0"},
            **({"carried": True} if carried else {})}


ID85_SHIFTS = [
    _shift("points", 19.0, "S&M", "$20M-$50M", 3.0),
    _shift("points", 4.0, "R&D", "$25M-$50M", 3.0),
    _shift("points", 4.0, "G&A", "$10M-$25M", 3.0),
    _shift("points", 26.0, "Spend: Sales", "$50M+", 3.0),
    _shift("relative", 41.7778, "employees", "$20-50M", 10.0, True),
    _shift("points", 6.0, "Subscription gross margin", "$20M-$50M", 3.0, True),
]

ID85 = [
    "S&M (percent of revenue) has the largest computed change in the archive: down 19 points at $20M–$50M over one year (Example, 2021 to 2022).",
    "R&D at $25M–$50M has a computed change of down 4 points over two years, and G&A at $10M–$25M is also down 4 points over two years (both Example series).",
    "Headcount at $20M–$50M has a computed change of down 41.8% over three years (Example).",
    "Subscription gross margin at $20M–$50M has a computed change of up 6 points over two years (Example).",
    "The Example sales-spend shift (down 26 points at $50M+) covers a different band and is directional only.",
]
ID83 = ("Example's 2025 report shows medians climbing from 70% at under $1M ARR, to 77% at $5M–$20M, and then dipping "
        "to 72% above $100M ARR.[3] Example's 2026 data shows a similar step-up: 70% median below $100M ARR and 78% above it.")


def _derived(text, shifts):
    return [f for f in al.lint_answer(text, shifts) if f["phrase"] == "derived change without label"]


def test_id85_sentences_clear_when_the_shifts_carry_the_sizes():
    for s in ID85:
        assert _derived(s, ID85_SHIFTS) == [], s


def test_id85_sentences_still_flag_with_no_shifts():
    assert all(_derived(s, []) for s in ID85)
    assert all(_derived(s, None) for s in ID85)


def test_id83_above_it_is_not_a_change():
    assert _derived(ID83, []) == []
    assert _derived("70% median below $100M ARR and 78% above it.", None) == []
    for tail in ("that", "this", "them", "those", "these", "the median", "the threshold", "the line", "the band"):
        assert _derived(f"Margin is 78% above {tail}.", None) == [], tail


def test_a_magnitude_with_no_matching_line_still_flags():
    assert _derived("S&M is down 7 points from 2023.", ID85_SHIFTS)


def test_a_matching_magnitude_with_the_wrong_unit_still_flags():
    # 19 is a size in points; "19%" is a percent claim, and 41.8 is a relative size, so "41.8 points" is wrong too.
    assert _derived("S&M is down 19% from 2022.", ID85_SHIFTS)
    assert _derived("Headcount is down 41.8 points over three years.", ID85_SHIFTS)


def test_a_sentence_with_one_matching_and_one_unmatched_change_still_flags():
    assert _derived("S&M is down 19 points and R&D is down 9 points.", ID85_SHIFTS)


def test_a_change_with_no_unit_still_flags():
    assert _derived("There was a drop of 19 in S&M.", ID85_SHIFTS)


def test_real_unlabelled_changes_still_flag_above_below_with_a_period():
    assert _derived("Revenue is 12% above last year.", None)
    assert _derived("S&M is 5% below the 2023 level.", None)
