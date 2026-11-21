"""/admin/fpa-buddy/report, option 1 redesign: a conversation's turns drop the repeated date, asker and
settings badge; each turn gets a summary line of chips, its computed shift lines as text, and closed
disclosures for everything else. Shapes follow real ask_questions ids 84 and 85 (invented wording)."""
import csv
import importlib
import io
import json
import os
import tempfile

import pytest

LONG_UNBROKEN = "X" * 320
SHIFT_A = "Sales and Marketing Expenses % of Revenue at $20M-$50M (median): down 19 points, from 54% to 35%"
SHIFT_B = "Subscription gross margin at $20M-$50M (median): up 6 points, from 80% to 86%"
DOC = "Example Benchmarks, 2099 SaaS Report (2099)"


def _prestep(*, placed=2, shifts=None, with_tables=True):
    subs = [{"intent": "benchmark", "text": "part one", "metrics": ["Expenses as a % of Revenue"], "band": "$20-50M ARR",
             "figure_rows": 30, "cards": 0, "library_hits": 2,
             "documents": [{"title": DOC, "rows": 6}]},
            {"intent": "benchmark", "text": "part two", "metrics": ["Gross Margin"], "band": "$20-50M ARR",
             "figure_rows": 24, "cards": 0, "library_hits": 1,
             "documents": [{"title": DOC, "rows": 4}]}]
    d = {"outcome": "ran", "detected": ["Expenses as a % of Revenue", "Gross Margin"], "split_ran": True,
         "requested_subs": 2, "subs": subs, "documents": [{"title": DOC, "rows": 10, "parts": [1, 2]}],
         "shifts": shifts if shifts is not None else [{"line": SHIFT_A, "kind": "x"}, {"line": SHIFT_B, "kind": "x"}],
         "shifts_suppressed": 16, "shifts_skipped": ["G&A in Example Report: an estimate, not used as an endpoint"],
         "shifts_unpaired": {"single edition": 82, "other basis": 35},
         "shifts_unpaired_groups": {"single edition": ["Example Co: R&D expense only in 2025"]},
         "shifts_outliers": ["endpoint basis differs: research expense (from) vs R&D expense (to)"],
         "left_out_detail": {"Report One (2025)": 22, "Report Two (2022)": 16}, "match_mix": {"exact": 54}}
    if with_tables:
        d["tables"] = {"shifts": {"fallback": [], "lines": placed},
                       "cap_dropped": [{"figure_key": 1, "report": "cap-report", "metric": "S&M expense", "band": "$20M-$50M"}],
                       "not_in_table": [{"figure_key": 2, "report": "orphan-report", "metric": "# of 2022 Total", "kind": "other"}]}
    return d


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    lib = Library(db)
    uid = lib.create_user("asker1", "supersecret", role="user")
    first = lib.record_ask_question(uid, "What does the spend profile look like?", "a1", "claude-sonnet-4-6", "standard",
                                    True, False, False, cost_usd=0.11, duration_ms=24900,
                                    prestep=_prestep(shifts=[{"line": SHIFT_A, "kind": "x"}], placed=1))
    lib.record_ask_question(uid, "Which of those lines is growing fastest?", "a2", "claude-sonnet-4-6", "standard",
                            True, False, False, conversation_id=str(first), turn_index=1, cost_usd=0.09,
                            rewrite_cost_usd=0.003, rewrite_text="Growth rates for S&M and R&D at $20-50M ARR since 2022",
                            prestep=_prestep(),
                            lint_flags=[{"phrase": "significant", "snippet": "drop across S&M was significant"},
                                        {"phrase": "derived change without label", "snippet": "down 19 points"}],
                            stripped=[{"sentence": "Left out (not sent): Report One", "pattern": "left-out line with no figures table",
                                       "action": "removed"}])
    lib.record_ask_question(uid, "Older question with no lookup record", "a3", "claude-sonnet-4-6", "standard",
                            True, False, False, cost_usd=0.02)
    lib.close()
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield c, appmod, db, first
    if os.path.exists(db):
        os.remove(db)


def _html(env):
    return env[0].get("/admin/fpa-buddy/report").text


def _convo_rows(html, cid):
    return [seg for seg in html.split("<tr ") if f'data-convo="{cid}"' in seg]


def test_conversation_turn_rows_do_not_repeat_date_asker_or_settings(env):
    html = _html(env)
    rows = _convo_rows(html, env[3])
    assert len(rows) == 2
    for row in rows:
        assert "asker1" not in row                 # the asker is named on the header row only
        assert "2026" not in row and "202" not in row.split('data-label="Question"')[0]   # no date, time only
        assert row.count("As above") == 2          # asker and settings, same as the first turn
        assert "&#8627; Turn" in row
    body = html.split("<tbody>")[1]
    assert body.count("asker1") == 2               # the rollup row and the one single-turn row, not the two turns


def test_summary_chips_state_a_value_and_label_placed_lines_honestly(env):
    html = _html(env)
    turn2 = _convo_rows(html, env[3])[1]
    assert "Shifts: 2 computed · 2 placed in the answer by code" in turn2
    assert "Rows: 54 sent · 38 left out" in turn2
    assert "Flags: 2" in turn2 and "Removed: 1" in turn2
    clean = _convo_rows(html, env[3])[0]
    assert "Flags: None" in clean and "Removed: None" in clean
    assert "Shifts: 1 computed · 1 placed in the answer by code" in clean


def test_placed_is_not_shown_when_the_turn_did_not_store_it(env):
    appmod = env[1]
    r = {"prestep_json": json.dumps(_prestep(with_tables=False)), "lint_flags": "", "stripped_json": ""}
    chips = appmod._report_summary_chips(appmod._report_turn_facts(r))
    assert "Shifts: 2 computed" in chips and "placed" not in chips


def test_a_turn_with_no_lookup_record_says_so(env):
    html = _html(env)
    assert "Shifts: no lookup record" in html and "Rows: no lookup record" in html


def test_computed_shift_lines_render_as_text_before_any_disclosure(env):
    turn2 = _convo_rows(_html(env), env[3])[1]
    assert SHIFT_A in turn2 and SHIFT_B in turn2
    assert turn2.index(SHIFT_A) < turn2.index("<details")


def test_rewrite_is_its_own_readable_line(env):
    turn2 = _convo_rows(_html(env), env[3])[1]
    assert '<div class="rp-rw"><b>Rewritten as:</b> Growth rates for S&amp;M and R&amp;D' in turn2


def test_shift_and_table_details_render_every_field_and_load_closed(env):
    html = _html(env)
    assert "<details open" not in html
    assert "Shift and table details" in html
    for needle in ("single edition: 82", "Example Co: R&amp;D expense only in 2025", "an estimate, not used as an endpoint",
                   "endpoint basis differs", "S&amp;M expense", "cap-report", "orphan-report", "Below the threshold", ">16<"):
        assert needle in html, needle


def test_empty_fields_say_none_not_blank(env):
    appmod = env[1]
    d = _prestep()
    for k in ("shifts_unpaired", "shifts_unpaired_groups", "shifts_skipped", "shifts_outliers"):
        d[k] = {} if k.startswith("shifts_unpaired") else []
    d["tables"]["cap_dropped"] = []
    d["tables"]["not_in_table"] = []
    d["shifts"] = []
    f = appmod._report_turn_facts({"prestep_json": json.dumps(d)})
    out = appmod._report_why_html(f) + appmod._report_shift_lines_html(f)
    assert out.count('rp-none">None<') == 6


def test_each_report_is_listed_once_per_turn_in_lookup_details(env):
    turn2 = _convo_rows(_html(env), env[3])[1]
    assert turn2.count(DOC) == 1
    assert "Reports sent, each listed once (1)" in turn2


def test_csv_is_unchanged(env):
    rows = list(csv.reader(io.StringIO(env[0].get("/admin/fpa-buddy/report/export.csv").text)))
    assert rows[0] == ["date", "asker", "conversation_id", "turn", "question", "answer", "model", "effort",
                       "use_library", "use_feed", "use_web", "input_tokens", "output_tokens",
                       "cache_creation_tokens", "cache_read_tokens", "cost_usd", "rewrite_cost_usd",
                       "citations", "web_scope", "local_time", "rewritten_question"]
    assert len(rows) == 4                           # flat, one row per turn


def test_the_one_sentence_removed_summary_is_a_native_summary(env):
    assert "<summary" in _html(env) and "1 sentence removed</summary>" in _html(env)


def _browser():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except Exception:
        return None
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pw.stop()
        return None


@pytest.mark.parametrize("width", [390, 1280])
def test_a_long_shift_line_wraps_and_never_widens_the_page(env, tmp_path, width):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db, _ = env
        from linklib.db import Library
        lib = Library(db)
        uid = lib.get_user("asker1")["id"]
        lib.record_ask_question(uid, "Long line", "a", "claude-sonnet-4-6", "standard", True, False, False, cost_usd=0.01,
                                prestep=_prestep(shifts=[{"line": "S&M shift " + LONG_UNBROKEN, "kind": "x"}]))
        lib.close()
        f = tmp_path / "r.html"
        f.write_text(client.get("/admin/fpa-buddy/report").text, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        over = page.evaluate("""() => ({doc: document.documentElement.scrollWidth, win: window.innerWidth,
            frame: (() => { const e = document.querySelector('.table-frame'); return e.scrollWidth - e.clientWidth; })()})""")
        page.close()
        assert over["doc"] <= over["win"], over
        assert over["frame"] <= 0, over
    finally:
        browser.close()
        pw.stop()
