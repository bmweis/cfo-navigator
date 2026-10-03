"""Stored FP&A Buddy answers render as blocks on the server surfaces.

The history used to show `_esc(answer)` inside a <p>: paragraph breaks,
`---` rules, `**bold**` and `##` headings all appeared as literal characters.
The fixture below is the real answer stored in ask_questions id 5 (Sonnet,
standard tier), trimmed only where noted; OPUS_STYLE is the older `##` shape.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from webapp.answer_render import render_answer_markdown

STORED = (
    "The two most useful frameworks in the library here are a benchmark-and-ROI model "
    "for general R&D, and a Growth Engine Ratio that ties R&D to GTM outcomes. AI is "
    "starting to reshape both.\n\n---\n\n"
    "**General R&D effectiveness: benchmark + roadmap ROI + team performance**\n\n"
    "The practical approach is three steps: (1) use R&D benchmarks from comparable "
    "companies to ballpark whether overall spend is in range; (2) map each dollar of "
    "spend to a specific roadmap initiative; and (3) performance-manage product and "
    "engineering teams as leading indicators.[1]\n\n"
    "A more decision-useful lens: classify R&D bets on a 2x2 as offensive vs. defensive.[1] "
    "That forces explicit conversations about *why* you're spending, not just *how much*.\n\n---\n\n"
    "**Connecting R&D to GTM: the Growth Engine Ratio**\n\n"
    "Read efficiency as a trend across multiple quarters.[2] Cited year [2026] stays text.\n\n---\n\n"
    "**The practical implication**\n\nThe frameworks themselves haven't been replaced by AI."
)
OPUS_STYLE = (
    "## Summary\n\nR&D efficiency is best read as a trend.[1]\n\n"
    "## Steps\n\n1. Benchmark spend\n2. Map to roadmap\n3. Review quarterly\n\n"
    "- first point\n- second point\n"
)
CITES = [
    {"n": 1, "title": "How to Think of R&D Spend", "url": "https://a16z.com/how-to-think-of-rd-spend/",
     "type": "library", "article_id": 367},
    {"n": 2, "title": "Going Beyond CAC Payback", "url": "https://fsuite.co/blog/growth-engine-ratio",
     "type": "library", "article_id": 4768},
]


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _seed(db, answer, cites=CITES, model="claude-sonnet-4-6"):
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user")
    lib.record_ask_question(uid, "How do SaaS firms measure R&D?", answer, model,
                            "standard", True, False, True, cost_usd=0.03,
                            citations=cites)
    lib.close()


def _history(appmod, db):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    return c.get("/ask/history").text


def test_history_renders_stored_answer_as_blocks(env):
    appmod, db = env
    _seed(db, STORED)
    page = _history(appmod, db)
    assert "---" not in page.split('class="ask-hist-answer"', 1)[-1].split("</div>")[0]
    assert page.count("<hr>") == 3
    assert "<strong>General R&amp;D effectiveness" in page
    assert "**" not in page
    assert "<em>why</em>" in page and "<em>how much</em>" in page
    # paragraph breaks survive: more than one <p> in the answer
    assert page.count("<p>") >= 6
    # citation markers link against the turn's own snapshot; [2026] stays text
    assert 'href="https://a16z.com/how-to-think-of-rd-spend/"' in page
    assert "[2026]" in page and "<sup" in page


def test_history_renders_opus_style_headings_and_lists(env):
    appmod, db = env
    _seed(db, OPUS_STYLE, model="claude-opus-4-8")
    page = _history(appmod, db)
    assert "##" not in page
    assert "<h4>Summary</h4>" in page and "<h4>Steps</h4>" in page
    assert "<ol><li>Benchmark spend</li>" in page
    assert "<ul><li>first point</li>" in page


def test_answer_is_not_inside_a_paragraph(env):
    """Block markup inside <p> is invalid HTML; the container is a div."""
    appmod, db = env
    _seed(db, STORED)
    page = _history(appmod, db)
    assert re.search(r'<p[^>]*>\s*<p>', page) is None
    assert 'class="ask-hist-answer"' in page


def test_raw_html_and_links_are_inert():
    evil = ('<script>alert(1)</script> <img src=x onerror=alert(1)> '
            '[click](https://evil.example/x) [javascript](javascript:alert(1)) '
            '<a href="https://evil.example">raw</a>')
    out = render_answer_markdown(evil, CITES)
    assert "<script" not in out and "<img" not in out and "<a " not in out
    assert "&lt;script&gt;" in out and "&lt;img" in out
    assert "href" not in out.replace("&lt;a href", "")  # only inside escaped text
    assert "[click](https://evil.example/x)" in out     # link stays literal text


def test_marker_never_links_to_non_http_url():
    out = render_answer_markdown("See [1].", [{"url": "javascript:alert(1)", "title": "x"}])
    assert "<a " not in out and "[1]" in out


def test_marker_title_attribute_is_escaped():
    out = render_answer_markdown("See [1].", [{"url": "https://a.com", "title": 'x" onmouseover="y'}])
    assert 'onmouseover="y' not in out


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
@pytest.mark.parametrize("text", [STORED, OPUS_STYLE, "Plain para.\nsecond line\n\n* a\n* b\n\n***\n\n`code` and __strong__ _em_",
    "See [the memo](https://evil.example/x?a=b) and <b>raw</b> then [1] and [2026]."])
def test_server_twin_matches_live_js(text, tmp_path):
    """The live page's browser renderer and the server twin emit the same blocks.

    Markdown links are inert on both sides (the live page used to linkify
    https links; it no longer does), so the last fixture carries one.
    """
    import webapp.app as appmod
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    js = src[i:j].replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    cites = [{"url": c["url"], "title": c["title"]} for c in CITES]
    script = tmp_path / "md.js"
    script.write_text("var CITES=" + json.dumps(cites) + ";\n" + js +
                      "\nprocess.stdout.write(mdToHtml(require('fs').readFileSync(0,'utf8')));")
    live = subprocess.run([NODE, str(script)], input=text, capture_output=True,
                          text=True, check=True).stdout
    assert render_answer_markdown(text, CITES) == live
