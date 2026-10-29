"""Retest fixes, Part D: Chicago-style citation labels (organization, title, year).

Invented sources only. Covers the label builder, the server renderers, the figures
document title, the Go deeper line, the MCP text and a JS twin parity run in node."""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from linklib.citation_label import label_html, label_text, make_label
from webapp.answer_render import go_deeper_html, go_deeper_items

REPORT = {"label": make_label(source="Example Benchmarks", title="The Annual Planning Guide 2099 Edition",
                              year="2099", kind="report")}
ARTICLE = {"label": make_label(author="Jane Example", title="Why Revenue Isn't the Whole Story",
                               year="2013-05-01T00:00:00", url="https://blog.example.test/x")}
URL_TITLE = {"label": make_label(source="", title="https://www.sample.test/a?id=1", year="2020",
                                 url="https://www.sample.test/a?id=1")}
LEGACY = {"title": "A title stored before labels", "url": "https://x.test/p"}
LEGACY_URL = {"title": "https://www.sample.test/a?id=1", "url": "https://www.sample.test/a?id=1"}


def test_report_is_italic_article_is_quoted_and_year_is_last():
    assert label_text(REPORT) == "Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)"
    assert label_html(REPORT) == "Example Benchmarks, <i>The Annual Planning Guide 2099 Edition</i> (2099)"
    assert label_text(ARTICLE) == 'Jane Example, "Why Revenue Isn\'t the Whole Story" (2013)'
    assert label_html(ARTICLE).startswith("Jane Example, &quot;Why Revenue Isn&#x27;t")


def test_author_beats_source_beats_domain():
    assert make_label(author="A", source="S", title="T", url="https://d.test")["org"] == "A"
    assert make_label(source="S", title="T", url="https://d.test")["org"] == "S"
    assert make_label(title="T", url="https://www.d.test/x")["org"] == "d.test"


def test_a_url_title_is_never_printed_it_shows_the_domain():
    assert label_text(URL_TITLE) == "sample.test (2020)"
    assert "http" not in label_html(URL_TITLE)
    assert label_text(LEGACY_URL) == "sample.test"          # stored before labels existed


def test_a_legacy_citation_shows_its_title_as_before():
    assert label_text(LEGACY) == "A title stored before labels"
    assert label_html(LEGACY) == "A title stored before labels"


def test_a_title_that_is_only_the_org_is_not_repeated():
    assert label_text({"label": make_label(source="Sample Newsletter", title="Sample Newsletter", year="2024")}) == "Sample Newsletter (2024)"


def test_html_is_escaped():
    lab = {"label": make_label(source="<b>x</b>", title="<script>", year="2024", kind="report")}
    out = label_html(lab)
    assert "<script" not in out and "<b>" not in out


def test_go_deeper_names_the_publisher_and_italicises_the_report():
    cites = [dict(REPORT, n=1, title="Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)",
                  url="https://example.test/guide", type="library", figures=True),
             {"n": 2, "title": "Not a report", "url": "https://x.test", "type": "library"}]
    items = go_deeper_items(cites)
    assert [i["name"] for i in items] == ["Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)"]
    html = go_deeper_html(cites)
    assert "<i>The Annual Planning Guide 2099 Edition</i>" in html and 'href="https://example.test/guide"' in html


def test_go_deeper_still_reads_a_legacy_figures_title():
    cites = [{"n": 1, "title": "Figures: Old Report 2098 (2098)", "url": "", "type": "library"}]
    assert go_deeper_items(cites)[0]["name"] == "Old Report 2098 (2098)"


def test_figures_document_is_titled_source_report_year_and_carries_its_label(tmp_path):
    from linklib.db import Library
    from linklib import benchmark_prestep as bp
    import figures_fixtures as fx
    lib = Library(str(tmp_path / "t.db"))
    fx.load_report(lib, "eb-2099", [fx.frow(report="The Annual Planning Guide 2099 Edition", year="2099",
                                           source="Example Benchmarks", metric="Gross Margin", value="79")])
    sub = bp.Sub(intent="benchmark", text="q", metrics=["Gross Margin"])
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()})
    d = sub.docs[0]
    assert d["title"] == "Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)"
    assert d["sent"]["figures"] is True
    assert label_text({"label": d["sent"]["label"]}) == "Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)"
    lib.close()


def test_library_and_feed_sources_carry_a_label_into_the_citation():
    from linklib.agent import _build_source_documents
    from linklib.citations import extract_citations
    hits = [{"id": 1, "title": "Why Revenue Isn't the Whole Story", "url": "https://blog.example.test/x",
             "author": "Jane Example", "source": "Example Blog", "published_at": "2013-05-01", "summary": "s", "content": "c"},
            {"id": 2, "title": "https://raw.example.test/p", "url": "https://raw.example.test/p",
             "author": "", "source": "", "published_at": "", "summary": "s", "content": "c"},
            {"id": 3, "title": "Digest of a report", "url": "https://r.example.test/d", "author": "",
             "source": "Example Benchmarks", "published_at": "2099-01-01", "summary": "s", "content": "c",
             "tags": ["benchmark-digest"]}]
    _, sent = _build_source_documents(hits, [{"title": "Feed post", "url": "https://f.example.test/1",
                                              "source": "Example Feed", "published_at": "2024-03-01T00:00:00", "summary": "s"}])
    labels = [label_text({"label": d["label"]}) for d in sent]
    assert labels == ['Jane Example, "Why Revenue Isn\'t the Whole Story" (2013)',
                      "raw.example.test",
                      "Example Benchmarks, Digest of a report (2099)",
                      'Example Feed, "Feed post" (2024)']
    assert sent[2]["label"]["kind"] == "report"

    class B:       # a response block citing document 1
        type = "text"
        text = "x"
        citations = [{"document_index": 1}]
    _, cites = extract_citations([B()], sent)
    assert cites[0]["label"]["org"] == "raw.example.test"


def test_past_answers_render_the_label(tmp_path, monkeypatch):
    import importlib, os, tempfile
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    cites = [dict(ARTICLE, n=1, title="Why Revenue Isn't the Whole Story", url="https://blog.example.test/x", type="library"),
             dict(REPORT, n=2, title="Example Benchmarks, The Annual Planning Guide 2099 Edition (2099)",
                  url="", type="library", figures=True),
             dict(LEGACY, n=3, type="library")]
    _, src = appmod._render_cited_answer("Text [1][2][3].", json.dumps(cites))
    assert "Jane Example" in src and "&quot;Why Revenue" in src
    assert "<i>The Annual Planning Guide 2099 Edition</i>" in src and "no link on file" in src
    assert "A title stored before labels" in src
    if os.path.exists(db):
        os.remove(db)


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
@pytest.mark.parametrize("cite", [REPORT, ARTICLE, URL_TITLE, LEGACY, LEGACY_URL,
                                  {"label": make_label(source="A & B <C>", title='Say "hi"', year="2024")},
                                  {"label": make_label(title="T", url="https://www.d.test/x")}])
def test_js_twin_matches_python(cite, tmp_path):
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    esc = src[i:j]
    k = src.index("function citeDomain(u) {{")
    l = src.index('// "Go deeper": one line naming each benchmark report')
    js = (esc + src[k:l]).replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    script = tmp_path / "label.js"
    script.write_text("var CITES=[];\n" + js +
                      "\nvar c=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                      "process.stdout.write(JSON.stringify({h:citeLabelHtml(c),t:citeLabelText(c)}));")
    out = json.loads(subprocess.run([NODE, str(script)], input=json.dumps(cite), capture_output=True,
                                    text=True, check=True).stdout)
    # The two escapers differ only in how they write an apostrophe or quote entity.
    norm = lambda s: s.replace("&#x27;", "&#39;").replace("&quot;", '"').replace("&#39;", "'")
    assert norm(out["h"]) == norm(label_html(cite))
    assert out["t"] == label_text(cite)
