"""One consistent source list under an answer (2026-10): a collapsed "Sources (N)" disclosure,
one source per row with the same structure, and [n] markers that open the disclosure and jump
to their row. Invented citations only."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from webapp.answer_render import new_scope, render_answer_markdown, sources_disclosure_html  # noqa: E402

CITES = [
    {"n": 1, "title": "Example Benchmarks 2025", "url": "https://example.test/a", "type": "library",
     "label": {"org": "Example Benchmarks", "work": "SaaS Spend Survey", "year": "2025", "kind": "report"}},
    {"n": 2, "title": "A very long article title that goes on and on about how finance teams size spend by function "
                      "across many stages", "url": "https://example.test/b", "type": "web", "trusted": False},
    {"n": 3, "title": "Report with no landing page", "url": "", "type": "library"},
    {"n": 4, "title": "Own piece", "url": "https://example.test/o", "type": "library", "own_content": True,
     "article_id": 7},
]
TEXT = "First claim [1]. Second claim [2]. Third [3] and fourth [4]."


def _lis(html):
    return re.findall(r'<li id="[^"]+" class="ask-src-row"[^>]*>(.*?)</li>', html, re.S)


def test_disclosure_is_collapsed_by_default_with_a_count():
    html = sources_disclosure_html(CITES, "s1")
    assert html.startswith('<details class="ask-src-disc"><summary>Sources (4)</summary>')
    assert " open" not in html.split(">", 1)[0]


def test_every_row_shares_one_structure():
    rows = _lis(sources_disclosure_html(CITES, "s1"))
    assert len(rows) == 4
    for r in rows:
        assert re.match(r'<span class="ask-src-n">\[\d\]</span><span class="ask-src-ic" aria-hidden="true">.*?</span>'
                        r'<span class="ask-src-body">', r), r


def test_rows_carry_the_label_tags_and_link_rules():
    html = sources_disclosure_html(CITES, "s1")
    assert "<i>SaaS Spend Survey</i>" in html and "Example Benchmarks" in html and "(2025)" in html
    assert html.count("Open web") == 1 and "(own writing)" in html and "archive #7" in html
    assert html.count("no link on file") == 1
    assert html.count('target="_blank"') == 3 and html.count('rel="noopener"') == 3


def test_markers_point_at_rows_of_the_same_answer():
    scope = new_scope()
    answer = render_answer_markdown(TEXT, CITES, scope)
    src = sources_disclosure_html(CITES, scope)
    targets = re.findall(r'data-cite="([^"]+)"', answer)
    assert len(targets) == 4
    for t in targets:
        assert f'id="{t}"' in src
    assert all(f'href="#{t}"' in answer for t in targets)


def test_two_answers_on_one_page_never_share_ids():
    a, b = new_scope(), new_scope()
    assert a != b
    assert f'id="src-{a}-1"' in sources_disclosure_html(CITES, a)
    assert f'id="src-{a}-1"' not in sources_disclosure_html(CITES, b)


def test_hostile_citation_content_is_escaped():
    evil = [{"n": 1, "title": '<script>x</script>', "url": 'https://e.test/"><script>z</script>', "type": "web",
             "label": {"org": "<b>o</b>", "work": '"><img src=x>', "year": "2025", "kind": "article"}}]
    html = sources_disclosure_html(evil, "s1") + render_answer_markdown("[1]", evil, "s1")
    assert "<script>" not in html and "<img" not in html and "<b>o</b>" not in html


def test_no_citations_no_disclosure():
    assert sources_disclosure_html([], "s1") == ""
    assert sources_disclosure_html(None, "s1") == ""


def test_mcp_keeps_label_text_and_has_no_chip_markup():
    ask_src = pathlib.Path("webapp/ask_orchestrator.py").read_text()
    assert "label_text=label_text(c, markdown=True)" in ask_src
    mcp_src = pathlib.Path("webapp/mcp_qa.py").read_text()
    assert "ask-src" not in mcp_src and "srcListHtml" not in mcp_src


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
def test_live_js_twin_matches_the_server_rows(tmp_path):
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    k = src.index("function citeDomain(u) {{")
    l = src.index('// "Go deeper": one line naming each benchmark report')
    m = src.index("function srcListHtml(d) {{")
    n = src.index("// Per-answer feedback: one tap records")
    js = (src[i:j] + src[k:l] + src[m:n]).replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    script = tmp_path / "src.js"
    script.write_text("var CITES=[];var CITE_SCOPE='t';\n" + js +
                      "\nprocess.stdout.write(srcListHtml({citations: " + json.dumps(CITES) + "}));")
    live = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True).stdout
    norm = lambda s: s.replace("&#x27;", "&#39;").replace("&quot;", '"').replace("&#39;", "'")
    # The live list has no archive id (server-rendered history only), so compare without it.
    server = sources_disclosure_html([{k: v for k, v in c.items() if k != "article_id"} for c in CITES], "t")
    assert norm(live).startswith(norm(server))      # the live version adds the "suggest a source" line after it


def _chromium():
    import glob
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    for kw in ({},) + tuple({"executable_path": p} for p in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")):
        try:
            return pw, pw.chromium.launch(**kw)
        except Exception:
            continue
    pw.stop()
    return None


def _page_html(text=TEXT, cites=CITES):
    import webapp.app as appmod
    a, s = appmod._render_cited_answer(text, json.dumps(cites))
    return appmod._page("Sources test", "", f'<div class="ask-answer">{a}{s}</div>')


def test_clicking_a_marker_opens_the_disclosure_and_lands_on_its_row():
    got = _chromium()
    if not got:
        pytest.skip("no Chromium")
    pw, br = got
    try:
        pg = br.new_page(viewport={"width": 390, "height": 844})
        pg.set_content(_page_html())
        assert pg.evaluate("document.querySelector('details.ask-src-disc').open") is False
        row = pg.locator("li.ask-src-row").nth(1)
        assert not row.is_visible()
        pg.locator('a[data-cite]').nth(1).click()
        assert pg.evaluate("document.querySelector('details.ask-src-disc').open") is True
        assert row.is_visible()
        assert pg.evaluate("document.activeElement.className") == "ask-src-row ask-src-hit"
    finally:
        br.close(); pw.stop()


def test_rows_align_and_nothing_overflows_at_390():
    got = _chromium()
    if not got:
        pytest.skip("no Chromium")
    pw, br = got
    try:
        pg = br.new_page(viewport={"width": 390, "height": 844})
        pg.set_content(_page_html())
        pg.locator("details.ask-src-disc > summary").click()
        xs = pg.evaluate("""() => [...document.querySelectorAll('li.ask-src-row')].map(li => ({
            n: li.querySelector('.ask-src-n').getBoundingClientRect().left,
            ic: li.querySelector('.ask-src-ic').getBoundingClientRect().left,
            body: li.querySelector('.ask-src-body').getBoundingClientRect().left}))""")
        assert len({round(x["n"]) for x in xs}) == 1 and len({round(x["ic"]) for x in xs}) == 1
        assert len({round(x["body"]) for x in xs}) == 1
        assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    finally:
        br.close(); pw.stop()
