"""Splitter guards (PR 756, follow-up to 755): A2 headcount never becomes ARR per head,
A3 a segment no stored row for the metric uses is dropped with a note, A4 parts that
resolve to the same lookup are merged. Invented fixtures only; the fake splitter returns
the shape seen in production (headcount rewritten as "ARR per Head", Enterprise attached to
every part, the same lookup asked twice)."""
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

REG = (
    "canonical_metric\tcard_url\tcontains\n"
    "Expenses as a % of Revenue\thttps://cards.example.test/e\tContains: expenses as a percent of revenue, "
    "expense as a percent of revenue\n"
    "Total Employees\thttps://cards.example.test/h\tContains: total employees, headcount\n"
    "ARR per FTE\thttps://cards.example.test/p\tContains: arr per fte, arr per head, arr per employee\n"
)
SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}
QUESTION = ("I run a company with $20-$30M in ARR selling to Enterprise customers. How has our headcount "
            "and expense as a percent of revenue changed over the past few years?")


def _part(intent, metric, text, band="$20-$30M ARR", segment="Enterprise", year=""):
    return {"intent": intent, "metric": metric, "band": band, "segment": segment, "year": year, "text": text}


BAD_SPLIT = {"subqueries": [
    _part("benchmark", "ARR per Head", "ARR per head at $20-$30M ARR for Enterprise"),
    _part("benchmark", "Expenses as a % of Revenue", "Expense as a percent of revenue at $20-$30M for Enterprise"),
    _part("benchmark", "Expenses as a % of Revenue", "How expense as a percent of revenue changed", year="all"),
    _part("benchmark", "ARR per Head", "How ARR per head changed over time", year="all"),
], "rest_of_question": ""}


def fake_client(payload):
    def factory():
        def create(**kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                                   usage=SimpleNamespace(input_tokens=300, output_tokens=200))
        return SimpleNamespace(messages=SimpleNamespace(create=create))
    return factory


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    for yr in ("2023", "2025"):
        load_report(db, f"eb-{yr}", [
            frow(source="Example Benchmarks", report=f"Example Benchmarks {yr}", year=yr, data_period=f"CY-{int(yr)-1}",
                 basis="ARR", band="$20M-$30M", segment="All", metric="Expenses as a % of Revenue",
                 denominator="percent of revenue", value="80", unit="percent"),
            frow(source="Example Benchmarks", report=f"Example Benchmarks {yr}", year=yr, data_period=f"CY-{int(yr)-1}",
                 basis="ARR", band="$20M-$30M", segment="All", metric="Total employees", value="150", unit="count"),
            frow(source="Example Benchmarks", report=f"Example Benchmarks {yr}", year=yr, data_period=f"CY-{int(yr)-1}",
                 basis="ARR", band="$20M-$30M", segment="All", metric="ARR per FTE", value="170", unit="USD thousand"),
        ])
    return db


def plan(db, payload=BAD_SPLIT, q=QUESTION):
    return bp.plan_turn(db, q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6",
                        remaining_usd=None, client_factory=fake_client(payload))


def report(ps):
    out = []
    for i, s in enumerate(ps.subs, 1):
        out.append(f"part {i} [{s.intent}] metrics={s.metrics} band={s.band!r} segment={s.segment!r} "
                   f"year={s.year!r} rows={s.rows} mix={s.match_mix}")
    mix = {}
    for s in ps.subs:
        for k, v in s.match_mix.items():
            mix[k] = mix.get(k, 0) + v
    out.append(f"total parts={len(ps.subs)} rows={sum(s.rows for s in ps.subs)} mix={mix}")
    return "\n".join(out)


def _metrics(ps):
    return [m.lower() for x in ps.subs for m in x.metrics]


def test_a2_headcount_never_becomes_arr_per_head(lib):
    ps = plan(lib)
    ms = _metrics(ps)
    assert not any("per head" in m or "per fte" in m or "per employee" in m for m in ms), ms
    assert any("employees" in m or "headcount" in m for m in ms), ms
    body = "\n".join(d["body"] for x in ps.subs for d in x.docs)
    assert "Total employees" in body and "ARR per FTE" not in body


def test_a2_arr_per_head_stays_when_the_question_asks_for_it(lib):
    q = "At $20-$30M ARR, what is ARR per employee and total headcount?"
    payload = {"subqueries": [_part("benchmark", "ARR per Head", "ARR per head at $20-$30M", segment=""),
                              _part("benchmark", "Total Employees", "Total employees at $20-$30M", segment="")],
               "rest_of_question": ""}
    ps = plan(lib, payload, q)
    ms = _metrics(ps)
    assert any("per head" in m for m in ms) and any("employees" in m for m in ms), ms


def test_a3_unused_segment_is_dropped_with_a_note_and_the_band_is_kept(lib):
    ps = plan(lib)
    notes = " | ".join(n for x in ps.subs for n in x.notes)
    assert "Segment not used by the data: Enterprise" in notes
    assert all(x.rows for x in ps.subs if x.intent == "benchmark")
    assert any(x.band for x in ps.subs if x.intent == "benchmark")


def test_a3_a_segment_the_metric_does_use_is_kept(lib):
    load_report(lib, "eb-seg", [frow(source="Example Benchmarks", report="Example Benchmarks 2026", year="2026",
                                     data_period="CY-25", basis="ARR", band="$20M-$30M", segment="Enterprise",
                                     metric="Total employees", value="200", unit="count"),
                                frow(source="Example Benchmarks", report="Example Benchmarks 2026", year="2026",
                                     data_period="CY-25", basis="ARR", band="$20M-$30M", segment="Enterprise",
                                     metric="Expenses as a % of Revenue", denominator="percent of revenue",
                                     value="70", unit="percent")])
    payload = {"subqueries": [_part("benchmark", "Total Employees", "Total employees at $20-$30M for Enterprise")],
               "rest_of_question": ""}
    ps = plan(lib, payload, "Total headcount and expense as a percent of revenue for Enterprise at $20-$30M ARR")
    notes = " | ".join(n for x in ps.subs for n in x.notes)
    assert "Segment not used" not in notes


def test_a4_duplicate_parts_are_merged_and_change_over_time_keeps_all_editions(lib):
    ps = plan(lib)
    keys = [(tuple(sorted(m.lower() for m in x.metrics)), x.band.lower(), x.intent) for x in ps.subs if x.intent != "other"]
    assert len(keys) == len(set(keys)), keys
    exp = [x for x in ps.subs if any("expenses as a %" in m.lower() for m in x.metrics)]
    assert len(exp) == 1 and exp[0].year == "all"


def test_a4_freed_slot_covers_a_detected_term_no_part_names(lib):
    payload = {"subqueries": [_part("benchmark", "Expenses as a % of Revenue", "Expense as a percent of revenue",
                                    segment="")], "rest_of_question": ""}
    ps = plan(lib, payload)
    assert ps.split_ran
    print([x.metrics for x in ps.subs], ps.detected)
    assert any("headcount" in m or "employees" in m for m in _metrics(ps))
    assert len(ps.subs) <= bp.MAX_SUBQUERIES


def test_step2_report(lib, capsys):
    """Prints the parts, rows and match mix for the literal question (read with -s)."""
    ps = plan(lib)
    with capsys.disabled():
        print("\n--- STEP 2 REPORT ---\n" + report(ps) + "\nnotes: " + " | ".join(n for s in ps.subs for n in s.notes))
