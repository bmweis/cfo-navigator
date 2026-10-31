"""Buddy answers, follow-up to PRs 747 to 750 (2026-10-28).

Part A: markdown tables render in answers (server twin and live JS).
Part B: the Depth cost footnote spans the Depth and Sources row.
Part C: an answer cut at the ceiling ends on a full sentence; a "Go deeper"
        line names the benchmark reports an answer cited.
Part D: the answer prompt carries each claim and number rule, with an example.

Every figure, publisher and report here is invented ("Example Benchmarks
2099"); nothing is third-party data. The model is never called.
"""
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib.benchmark_prestep import PRESTEP_RULES
from webapp.answer_render import go_deeper_html, go_deeper_items, render_answer_markdown

from test_buddy_phone_fixes import CITES, ORIGIN, Site, _launch, buddy_html  # noqa: E402,F401

TABLE = (
    "Intro line.\n\n"
    "| Metric | Band | Statistic | Value | Report | Match |\n"
    "|---|---|---|---|---|---|\n"
    "| ARR per FTE | $20-$50M | Median | $181,905 [1] | Example Benchmarks 2099 | exact |\n"
    "| ARR per FTE | $10-$25M | Median | $150,000 [2] | Example Benchmarks 2098 | adjacent band |\n\n"
    "Closing line."
)
LIVE_CITES = [{"url": "https://example.test/a", "title": "A"}, {"url": "https://example.test/b", "title": "B"}]
SCRATCH = pathlib.Path(os.environ.get("BUDDY_SHOTS", "/tmp"))


# --- Part A: tables ------------------------------------------------------------

def test_a_markdown_table_renders_as_a_framed_table():
    out = render_answer_markdown(TABLE, LIVE_CITES)
    assert out.count("<table>") == 1 and out.count("<th>") == 6 and out.count("<tr>") == 3
    assert 'class="table-frame ask-table"' in out and "overflow-x:auto" in out
    assert "|" not in out and "---" not in out
    assert out.startswith("<p>Intro line.</p>") and out.endswith("<p>Closing line.</p>")


def test_citation_markers_in_cells_render_like_prose():
    out = render_answer_markdown(TABLE, LIVE_CITES)
    assert '>$181,905 <sup class="cite"><a href="https://example.test/a"' in out
    prose = render_answer_markdown("$181,905 [1]", LIVE_CITES)
    assert '$181,905 <sup class="cite"><a href="https://example.test/a"' in prose


def test_cells_are_escaped_and_links_stay_inert():
    out = render_answer_markdown("| a | b |\n|---|---|\n| <script>x</script> | [t](https://evil.example) |", [])
    assert "<script" not in out and "&lt;script&gt;" in out
    assert "<a " not in out and "[t](https://evil.example)" in out


def test_escaped_pipe_is_a_literal_pipe_in_a_cell():
    out = render_answer_markdown("| a | b |\n|---|---|\n| x \\| y | z |", [])
    assert ">x | y</td>" in out


@pytest.mark.parametrize("text", [
    "| a | b |\n| 1 | 2 |",                                  # no separator row
    "| a | b |\n|---|---|\n| 1 |",                          # ragged body row
    "| a | b |\n|---|---|\n| 1 | 2 | 3 |",                  # ragged the other way
    "| a | b |\n|---|\n| 1 | 2 |",                          # separator width differs
    "| a |\n|---|\n| 1 |",                                  # one column is not a table
    "|\n|\n",
])
def test_a_malformed_table_renders_as_plain_text_and_drops_nothing(text):
    out = render_answer_markdown(text, [])
    assert "<table" not in out
    for cell in re.findall(r"[^|\s-]+", text):
        assert cell in out, cell


def test_fuzzed_pipe_text_never_throws_or_drops_words():
    rng = random.Random(7)
    pieces = ["|", "---", ":-:", " a ", "b", "\n", "[1]", "x y", "|---|---|", "\\|", "<i>", "", "**"]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 30)))
        out = render_answer_markdown(text, LIVE_CITES)
        for word in re.findall(r"[A-Za-z]{1,}", text):
            if word not in ("i",):          # `<i>` is escaped, its letter still shows
                assert word in re.sub(r"<[^>]+>", "", out), (text, word)


def test_table_inside_lists_and_paragraphs_keeps_the_blocks_around_it():
    out = render_answer_markdown("- one\n- two\n| a | b |\n|---|---|\n| 1 | 2 |\n\nafter", [])
    assert out.startswith("<ul><li>one</li><li>two</li></ul><div") and out.endswith("<p>after</p>")


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
@pytest.mark.parametrize("text", [
    TABLE,
    "| a | b |\n|---|---|\n| 1 | 2 |",
    "| a | b |\n| 1 | 2 |",
    "| a | b |\n|---|---|\n| 1 |",
    "- x\n| a | b |\n|:--|--:|\n| x \\| y | <b>z</b> [1] |\n\n## After",
    "| a |\n|---|\n| 1 |",
])
def test_server_twin_matches_live_js_for_tables(text, tmp_path):
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    js = src[i:j].replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    script = tmp_path / "md.js"
    script.write_text("var CITES=" + json.dumps(LIVE_CITES) + ";\nvar CITE_SCOPE='';\n" + js +
                      "\nprocess.stdout.write(mdToHtml(require('fs').readFileSync(0,'utf8')));")
    live = subprocess.run([NODE, str(script)], input=text, capture_output=True, text=True, check=True).stdout
    assert render_answer_markdown(text, LIVE_CITES) == live


def test_history_page_and_past_answers_render_tables(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    from linklib.db import Library
    lib = Library(str(tmp_path / "t.db"))
    uid = lib.create_user("member1", "supersecret", role="user")
    lib.record_ask_question(uid, "Q", TABLE, "claude-sonnet-4-6", "standard", True, False, True,
                            cost_usd=0.03, citations=CITES)
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    page = c.get("/ask/history").text
    assert page.count('class="table-frame ask-table"') == 1 and "| Metric |" not in page


def test_the_mcp_result_carries_the_markdown_unchanged(monkeypatch, tmp_path):
    """run_ask is what ask_fpa_buddy returns: the answer text is the model's
    markdown, byte for byte, never rendered."""
    from linklib.db import Library
    from webapp import ask_orchestrator as ao
    lib = Library(str(tmp_path / "t.db"))
    uid = lib.create_user("m", "supersecret", role="user")
    monkeypatch.setattr(agent, "answer_question", lambda *a, **k: agent.Answer(
        text=TABLE, citations=[{"n": 1, "title": "Figures: Example Benchmarks 2099 (2099)",
                                "url": "https://example.test/eb", "type": "library"}],
        model="claude-sonnet-4-6"))
    out = ao.run_ask(lib, uid, "q", effort="standard", use_library=True, use_feed=False, use_web=False)
    assert out["answer"] == TABLE
    assert out["go_deeper"] == [{"name": "Example Benchmarks 2099 (2099)", "html": "Example Benchmarks 2099 (2099)",
                                 "url": "https://example.test/eb"}]
    lib.close()


# --- Part C: complete answers, Go deeper ---------------------------------------

BASE = "Intro sentence one is here and fine. Second sentence is also fine and has a cite.[1] "


@pytest.mark.parametrize("text,tail", [
    (BASE + "Beta is six and then it cu", "has a cite.[1]"),
    (BASE + "Ends cleanly.[2]", "Ends cleanly.[2]"),
    ("A fine opening line goes here.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 |", "| 1 | 2 |"),
    ("A fine opening line goes here.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4", "| 1 | 2 |"),
    (BASE + "Para one ends here. And a second sentence is cut off mid wo", "Para one ends here."),
])
def test_a_cut_off_answer_ends_on_a_full_sentence_or_row(text, tail):
    assert agent.trim_to_full_sentence(text).endswith(tail)


def test_trim_leaves_a_clean_answer_and_a_mostly_unfinished_one_alone():
    assert agent.trim_to_full_sentence("Done.") == "Done."
    no_break = "one long sentence that never ends " * 5
    assert agent.trim_to_full_sentence(no_break) == no_break.rstrip()


def _cites():
    return [
        {"n": 1, "title": "Figures: Example Benchmarks 2099 (2099)", "url": "https://example.test/eb99", "type": "library"},
        {"n": 2, "title": "Figures: Sample Survey 2098 (2098)", "url": "", "type": "library"},
        {"n": 3, "title": "Some article", "url": "https://example.test/a", "type": "library"},
        {"n": 4, "title": "Figures: Example Benchmarks 2099 (2099)", "url": "https://example.test/eb99", "type": "library"},
        {"n": 5, "title": "Figures: Bad Link 2099 (2099)", "url": "javascript:alert(1)", "type": "library"},
    ]


def test_go_deeper_names_each_cited_report_once_linked_when_there_is_a_url():
    items = go_deeper_items(_cites())
    assert [i["name"] for i in items] == ["Example Benchmarks 2099 (2099)", "Sample Survey 2098 (2098)", "Bad Link 2099 (2099)"]
    assert [i["url"] for i in items] == ["https://example.test/eb99", "", ""]
    html = go_deeper_html(_cites())
    assert html.count("<a ") == 1 and 'target="_blank"' in html and "Go deeper:" in html
    assert "Sample Survey 2098 (2098)" in html and "javascript" not in html


def test_go_deeper_is_empty_without_a_figures_citation():
    assert go_deeper_html([{"title": "Some article", "url": "https://example.test/a"}]) == ""
    assert go_deeper_html(None) == ""


@pytest.mark.skipif(not NODE, reason="node not available")
def test_go_deeper_js_twin_matches_python(tmp_path):
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    k = src.index("function goDeeperHtml(d) {{")
    m = src.index("// What this question cost")
    js = (src[i:j] + src[k:m]).replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    script = tmp_path / "gd.js"
    script.write_text(js + "\nprocess.stdout.write(goDeeperHtml({citations: " + json.dumps(_cites()) + "}));")
    live = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True).stdout
    assert live == go_deeper_html(_cites())


def test_history_shows_go_deeper_under_the_answer(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    from linklib.db import Library
    lib = Library(str(tmp_path / "t.db"))
    uid = lib.create_user("member1", "supersecret", role="user")
    lib.record_ask_question(uid, "Q", "Text [1].", "claude-sonnet-4-6", "standard", True, False, True,
                            cost_usd=0.03, citations=_cites())
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    page = c.get("/ask/history").text
    assert "Go deeper: <a" in page and "Example Benchmarks 2099 (2099)</a>" in page


# --- Part D: the rules are in the prompt, each with an example ----------------

RULE_TEXT = {
    "cause only if cited": ["Do not state a cause or reason for a change unless a cited source states it",
                            "otherwise leave the cause out", "Good:", "The dip reflects the overhiring era"],
    "derived labelled inline with inputs": ["Label every derived number \"derived, not in report\" inline",
                                            "up 12% (derived, not in report: 150 to 168)"],
    "medians do not add": ["Medians do not add", "Never present a sum or a \"stack\" of medians",
                           "combine numbers only within one source, one band and one year"],
    "nothing about the future": ["Say nothing about the future or about years the data does not cover",
                                 "Likely similar or slightly lower next year"],
    "no unasked recommendations": ["Give no recommendation unless the question asks for one",
                                   "Sitting below the median means you are behind"],
    "table mechanics, six rows, fixed columns": ["If a table helps, its columns are", "Six rows at most",
                                                 "metric, band as printed, statistic, value, report and year, match note",
                                                 "Newest report first"],
    "short match note, shared caveat once": ["The match note is a short phrase",
                                             "stated once above the table and never repeated in each row"],
    "synthesize then point": ["Synthesize, then point to the source", "do not write one"],
    "two publishers disagree": ["When two publishers disagree, show both", "Do not average"],
}


def _system(figures):
    class _Lib:
        def get_setting(self, k):
            return "voice text"
    return agent._build_system(True, False, False, _Lib(), benchmark_rules=figures)


@pytest.mark.parametrize("name", list(RULE_TEXT))
def test_each_rule_and_its_example_is_in_the_figures_prompt(name):
    system = _system(True)
    for phrase in RULE_TEXT[name]:
        assert phrase in system, f"{name}: missing {phrase!r}"


# Since the retest fixes the claim and shape rules ride on every turn (tests/test_answer_rules.py);
# only the figures mechanics are withheld from a turn with no figures.
FIGURES_MECHANICS = ("table mechanics, six rows, fixed columns", "short match note, shared caveat once")


def test_figures_mechanics_are_not_sent_to_a_turn_without_figures():
    system = _system(False)
    for name in FIGURES_MECHANICS:
        assert RULE_TEXT[name][0] not in system


def test_the_prompt_for_a_figures_turn_reaches_the_model(tmp_path, monkeypatch):
    """A stubbed answer call: the system prompt it receives carries every rule."""
    import test_benchmark_prestep as tbp
    from linklib.db import Library
    import figures_fixtures as fx
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(agent, "_client", None)
    monkeypatch.setattr("linklib.db.Library.vector_search_available", lambda self: False)
    lib = Library(str(tmp_path / "t.db"))
    lib.seed_voice_prompts()
    fx.seed_standard(lib)
    c = tbp.FakeClient()
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    agent.answer_question(lib, "What is the median net revenue retention at $15M ARR?", use_feed=False)
    system = c.answer_calls()[0]["system"]
    for phrases in RULE_TEXT.values():
        for phrase in phrases:
            assert phrase in system
    lib.close()


def test_the_first_source_is_called_curated_archive_in_the_prompt():
    system = _system(True)
    assert "CURATED ARCHIVE" in system and "SAVED LIBRARY" not in system


def test_standard_ceiling_does_not_move():
    # Measured gate (see the PR): a 4,000-token figures turn is estimated past 70 s.
    assert agent.EFFORT_SETTINGS["standard"]["max_tokens"] == 2500


# --- Part A/B in a real browser -------------------------------------------------

@pytest.fixture
def page_html(request):
    return request.getfixturevalue("buddy_html")

def _page(html, width, height, mobile):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
    pg = ctx.new_page()
    site = Site(html)
    pg.route(re.compile(r"^http://buddy\.test/.*"), site.handle)
    pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
    return pw, browser, pg


def _payload():
    two = (TABLE + "\n\nSecond topic.\n\n| Metric | Value |\n|---|---|\n| Total employees | 74 [1] |\n")
    return {"answer": two, "citations": [
        {"n": 1, "title": "Figures: Example Benchmarks 2099 (2099)", "url": "https://example.test/eb", "type": "library"},
        {"n": 2, "title": "Figures: Sample Survey 2098 (2098)", "url": "", "type": "library"}],
        "sources": [], "feed_sources": [], "web_sources": [], "followups_left": 4,
        "conversation_id": "20", "turn_id": 20, "usage": {"spent": 1.5, "cap": 5.0},
        "is_private": False, "notice": "", "cost_line": "This question cost $0.12."}


@pytest.mark.parametrize("width,height,mobile", [(1280, 900, False), (390, 844, True)])
def test_tables_render_in_the_live_page_and_never_scroll_the_page(page_html, width, height, mobile):
    html, _ = page_html
    pw, browser, pg = _page(html, width, height, mobile)
    try:
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.route(re.compile(r"^http://buddy\.test/ask$"),
                 lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(_payload())))
        pg.goto(ORIGIN + "/tools/fpa-buddy")
        pg.fill("#ask-q", "A question about ARR per employee")
        pg.click("#ask-btn")
        pg.wait_for_selector("#ask-thread .ask-answer table", timeout=8000)
        pg.wait_for_timeout(400)
        assert not errors, errors
        assert pg.locator("#ask-thread .ask-answer table").count() == 2
        assert "| Metric |" not in pg.inner_text("#ask-thread .ask-answer")
        doc = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert doc[0] == doc[1], doc                      # the page never scrolls sideways
        frame = pg.evaluate("(() => { const f = document.querySelector('#ask-thread .ask-table'); "
                            "return [f.scrollWidth, f.clientWidth]; })()")
        if mobile:
            # Answer discipline (2026-10-31): below 600px rows stack as labelled cards, so the
            # frame has nothing to scroll and every cell sits inside the viewport.
            assert frame[0] <= frame[1], frame
            assert pg.evaluate("[...document.querySelectorAll('#ask-thread .ask-table td')].every("
                               "td => { const r = td.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; })")
            assert pg.evaluate("getComputedStyle(document.querySelector('#ask-thread .ask-table td'), '::before').content"
                               ).strip('"')
        assert "Go deeper:" in pg.inner_text("#ask-thread .ask-answer")
        pg.locator("#ask-thread .ask-answer").first.screenshot(path=str(SCRATCH / f"answer_tables_{width}.png"))
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("width,height,mobile", [(1280, 900, False), (390, 844, True)])
def test_footnote_spans_the_depth_and_sources_row(page_html, width, height, mobile):
    html, _ = page_html
    pw, browser, pg = _page(html, width, height, mobile)
    try:
        pg.goto(ORIGIN + "/tools/fpa-buddy")
        pg.wait_for_selector("#ask-dd-foot")
        box = pg.evaluate("""(() => {
          const r = el => el.getBoundingClientRect();
          const foot = document.querySelector('.fpa-intro-area-controls #ask-dd-foot');
          const row = document.querySelector('.fpa-intro-area-controls .ask-dd-row');
          const depth = row.querySelector('.ask-dd-col');
          const lh = parseFloat(getComputedStyle(foot).lineHeight);
          return {foot: r(foot).width, row: r(row).width, depthCol: r(depth).width,
                  lines: Math.round(r(foot).height / lh), parentIsRow: foot.parentElement === row};
        })()""")
        assert box["parentIsRow"]
        assert abs(box["foot"] - box["row"]) <= 6 + 4, box     # margins of 2px each side
        if not mobile:
            assert box["foot"] > box["depthCol"] * 1.6, box
            assert box["lines"] == 1, box
        pg.locator(".fpa-intro-area-controls").screenshot(path=str(SCRATCH / f"depth_row_{width}.png"))
    finally:
        browser.close()
        pw.stop()


def test_sources_dropdown_still_opens_under_its_button(page_html):
    html, _ = page_html
    pw, browser, pg = _page(html, 1280, 900, False)
    try:
        pg.goto(ORIGIN + "/tools/fpa-buddy")
        pg.click('.fpa-intro-area-controls [data-dd="sources"].ask-dd-btn')
        panel = pg.locator('.fpa-intro-area-controls .ask-dd-panel[data-dd="sources"]')
        assert panel.is_visible()
        btn = pg.locator('.fpa-intro-area-controls [data-dd="sources"].ask-dd-btn').bounding_box()
        p = panel.bounding_box()
        assert p["y"] >= btn["y"] + btn["height"] - 1 and abs(p["x"] - btn["x"]) <= 2
    finally:
        browser.close()
        pw.stop()


def test_figures_prompt_is_not_oversized():
    # The added rules stay a short block (they ride on every figures turn).
    assert len(PRESTEP_RULES) < 6000


def test_how_it_works_archive_bullet_names_benchmark_lookup(admin_client=None):
    import re
    from webapp import app as appmod
    src = open(appmod.__file__).read()
    m = re.search(r"<li><strong>The archive\.</strong>(.*?)</li>", src, re.S)
    assert m and m.group(1).rstrip().endswith(
        "Benchmark numbers are looked up in stored report data and cited to the report they came from.")
    assert src.count("Benchmark numbers are looked up in stored report data") == 1
