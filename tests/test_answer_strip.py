"""Answer discipline (2026-10): the high-precision strip, its record, and the prompt rules.
Invented text only. Whether the model now obeys the prompt is checked live; the strip is
the backstop that runs after the answer."""
import json
from types import SimpleNamespace

import pytest

from linklib import agent
from linklib.answer_strip import MIN_WORDS_LEFT, PATTERNS, strip_answer
from linklib.db import Library

PAD = " ".join(["The newest figures are printed in the Example Benchmarks 2026 report."] * 6) + " [1]"


def _with_pad(extra: str) -> str:
    return f"{PAD}\n\n{extra}"


@pytest.mark.parametrize("sentence,pattern", [
    ("That median is striking because the count fell.", "characterization"),
    ("The drop was dramatic.", "characterization"),
    ("It is almost entirely driven by headcount restraint.", "causal"),
    ("The change is due to hiring freezes.", "causal"),
    ("This is because of the market.", "causal"),
    ("The figure reflects the overhiring era.", "causal"),
    ("If you are growing fast, expect to sit at the high end.", "forecast or advice"),
    ("A sales-led company would likely show more.", "forecast or advice"),
    ("You should look at the top quartile.", "forecast or advice"),
])
def test_each_pattern_is_removed_when_uncited(sentence, pattern):
    clean, rec = strip_answer(_with_pad(sentence))
    assert sentence not in clean and clean.startswith(PAD)
    assert rec == [{"sentence": sentence, "pattern": pattern, "action": "removed"}]


@pytest.mark.parametrize("sentence", [
    "The dip was driven by hiring freezes [2].",
    "Median spend reflects 2025 data [2].",
    "The change is striking [1].",
])
def test_a_cited_sentence_is_never_stripped(sentence):
    clean, rec = strip_answer(_with_pad(sentence))
    assert sentence in clean and rec == []


def test_table_rows_and_headings_are_never_touched():
    t = _with_pad("| Metric | Note |\n|---|---|\n| S&M | driven by hiring |\n\n## Notably, the data")
    clean, rec = strip_answer(t)
    assert clean == t.strip() and rec == []


def test_only_the_flagged_sentence_of_a_paragraph_goes():
    clean, rec = strip_answer(_with_pad("Median S&M was 38% [2]. The drop was dramatic. A second cited fact [3]."))
    assert "Median S&M was 38% [2]." in clean and "A second cited fact [3]." in clean
    assert "dramatic" not in clean and len(rec) == 1


def test_a_paragraph_or_bullet_left_empty_is_dropped():
    clean, rec = strip_answer(_with_pad("The drop was dramatic.\n- You should wait.\n- A cited fact [2]."))
    assert "- You should wait" not in clean and "\n\n\n" not in clean
    assert clean.endswith("- A cited fact [2].") and len(rec) == 2


def test_a_short_answer_is_not_stripped_and_the_record_says_so():
    short = "Median S&M was 38% [1]. The drop was dramatic."
    assert len(short.split()) < MIN_WORDS_LEFT
    clean, rec = strip_answer(short)
    assert clean == short
    assert rec == [{"sentence": "The drop was dramatic.", "pattern": "characterization", "action": "kept_short"}]


def test_known_false_positives_are_listed_not_hidden():
    # "expect" is a whole word: "expected value" and "expects" do not match.
    assert strip_answer(_with_pad("The expected value is 4."))[1] == []
    # "reflects" in an uncited sentence IS removed: a label such as "the column reflects
    # the printed period" goes. That is the cost of a short list, shown on the admin report.
    assert strip_answer(_with_pad("The column reflects the printed period."))[1] != []
    assert [n for n, _ in PATTERNS] == ["causal", "forecast or advice", "characterization"]


# --- the example answers from the 2026-10-31 phone check (invented figures) ---
EXAMPLE = _with_pad(
    "That median is striking because it is down roughly 70 from 2024 and about 42% below 2022 "
    "— a dramatic compression in just a few years.\n"
    "That's a 30-point compression in three years — almost entirely driven by headcount restraint "
    "rather than structural change in the cost model.\n"
    "If your company is growing fast with a sales-led motion, expect to sit toward the high end of the S&M range.\n"
    "A pure enterprise sales-led company would likely show higher S&M than these all-segment medians suggest.")


def test_the_phone_check_examples_are_all_removed():
    clean, rec = strip_answer(EXAMPLE)
    # 2026-11: the second sentence has a figure clause before the flagged one, so only the cause goes.
    assert clean == PAD + "\n\nThat's a 30-point compression in three years." and len(rec) == 4
    assert [r["action"] for r in rec].count("clause_removed") == 1
    assert {r["pattern"] for r in rec} == {"characterization", "causal", "forecast or advice"}


# --- stored, shown, identical in the poll payload ---
class _Client:
    def __init__(self, text):
        self.text, self.messages = text, self

    def create(self, **kw):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text, citations=[])],
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=400,
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=0),
                               stop_reason="end_turn")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(agent, "_client", None)
    monkeypatch.setattr("linklib.db.Library.vector_search_available", lambda self: False)
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def test_the_record_is_written_and_stored_text_equals_the_poll_text(lib, monkeypatch):
    from webapp import ask_orchestrator as orch
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(EXAMPLE))
    uid = lib.create_user("asker", "supersecret", role="user")
    out = orch.run_ask(lib, uid, "How are costs structured?", request_id="req-0123456789abcdef")
    assert out["answer"] == PAD + "\n\nThat's a 30-point compression in three years." and "striking" not in out["answer"]
    row = dict(lib.conn.execute("SELECT * FROM ask_questions").fetchone())
    assert row["answer"] == out["answer"]
    rec = json.loads(row["stripped_json"])
    assert len(rec) == 4 and rec[0]["action"] == "removed"
    polled = orch.result_for_request(lib, uid, "req-0123456789abcdef")
    assert polled["status"] == "done" and polled["answer"] == out["answer"]


def test_a_clean_answer_records_nothing(lib, monkeypatch):
    from webapp import ask_orchestrator as orch
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(PAD))
    uid = lib.create_user("asker", "supersecret", role="user")
    orch.run_ask(lib, uid, "Q?")
    assert lib.conn.execute("SELECT stripped_json FROM ask_questions").fetchone()[0] == ""


def test_the_admin_report_row_shows_the_removed_sentences(lib):
    from webapp.app import _stripped_html
    html = _stripped_html({"stripped_json": json.dumps(
        [{"sentence": "The drop was dramatic.", "pattern": "characterization", "action": "removed"}])})
    assert "1 sentence removed" in html and "The drop was dramatic." in html
    kept = _stripped_html({"stripped_json": json.dumps(
        [{"sentence": "x", "pattern": "causal", "action": "kept_short"}])})
    assert "kept (answer too short to strip)" in kept
    assert _stripped_html({"stripped_json": ""}) == ""


# --- the prompt carries each rule and its pair, first ---
def _system(effort="standard"):
    class _L:
        def get_setting(self, k):
            return "voice text"
    return agent._build_system(True, False, False, _L(), effort=effort)


@pytest.mark.parametrize("phrase", [
    "No characterization", "\"striking\"", "No cause without a cited statement",
    "labelled and shows both inputs in the same sentence", "derived, not in report: 112 in 2024 to 42 in 2025",
    "No advice and no prediction", "\"expect\"", "\"would likely\"",
    "first sentence says in plain words", "Never quote an internal metric label",
])
def test_prompt_carries_each_discipline_rule(phrase):
    assert phrase in _system()


def test_discipline_rules_come_before_the_shape_rules():
    s = _system()
    assert s.index("Hard rules (every turn") < s.index("How to shape the answer") < s.index("Rules for numbers and claims")


@pytest.mark.parametrize("effort,n", [("quick", 6), ("standard", 12), ("deep", 18)])
def test_length_ceiling_by_tier_and_max_tokens_unchanged(effort, n):
    assert f"at most {n} sentences in the whole answer" in _system(effort)
    assert agent.EFFORT_SETTINGS["standard"]["max_tokens"] == 2500


def test_lookup_note_never_hands_the_model_a_label_to_quote():
    import inspect
    from linklib import benchmark_prestep as bp
    src = inspect.getsource(bp)
    assert "That label is internal: never quote it" in src and "No stored figure matches" not in src


# --- Part C: answer tables on a phone ---
def test_answer_table_cells_carry_their_column_name():
    from webapp.answer_render import render_answer_markdown
    out = render_answer_markdown("| Metric | Value |\n|---|---|\n| S&M | 38% |", [])
    assert '<td data-label="Metric">S&amp;M</td>' in out and '<td data-label="Value">38%</td>' in out


def test_phone_css_stacks_rows_and_resets_the_floor():
    import webapp.app as appmod
    src = open(appmod.__file__).read()
    block = src.split("@media(max-width:600px){\n  .ask-table>table{min-width:0;}")[1].split("}\n", 1)[0] + src
    assert "attr(data-label)" in src and ".ask-table thead{display:none;}" in src and block


def test_live_page_script_emits_the_same_data_label():
    import webapp.app as appmod
    src = open(appmod.__file__).read()
    assert "'<td data-label=\"' + escapeHtml(heads[k])" in src


def test_second_column_is_reachable_at_390px():
    pw = pytest.importorskip("playwright.sync_api")
    import webapp.app as appmod
    from webapp.answer_render import render_answer_markdown
    css = open(appmod.__file__).read().split(".ask-table{margin:0 0 14px;}")[1].split("/* Phase Q")[0]
    html = render_answer_markdown("| Metric | Band | Value | Report |\n|---|---|---|---|\n"
                                  "| S&M | $20M-$30M | 38% | Example Benchmarks 2026 |", [])
    page = f"<style>.table-frame{{overflow:auto}}.ask-table{{margin:0}}{css}</style>{html}"
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 700})
            pg.set_content(page)
            ok = pg.evaluate("""() => [...document.querySelectorAll('.ask-table td')].every(td => {
                const r = td.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; })""")
            labels = pg.evaluate("getComputedStyle(document.querySelector('.ask-table td'), '::before').content")
            b.close()
    except Exception as e:  # no browser in this environment
        pytest.skip(f"no Chromium: {e}")
    assert ok and "Metric" in labels
