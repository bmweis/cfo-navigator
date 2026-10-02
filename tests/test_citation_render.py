"""Server-side citation rendering for the ask surfaces (#94).

Covers the one shared helper (webapp.app._render_cited_answer — marker
linkification against the turn's persisted citations_json snapshot, safe
truncation, escaping, legacy-'[]' degradation) and its three call sites
(/ask/history, /tools/fpa-buddy's past-questions section, /admin/fpa-buddy/feedback),
plus the CSV export's deliberately-raw markers with the new plain-text
citations column.
"""
import csv
import io
import json
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


CITATIONS = [
    {"n": 1, "title": "Saved piece", "url": "https://ex.com/a", "type": "library", "article_id": 42},
    {"n": 2, "title": "Feed item", "url": "https://ex.com/b", "type": "feed"},
    {"n": 3, "title": "Web hit", "url": "https://ex.com/c", "type": "web"},
]
CITED_ANSWER = "CAC payback is months to recover CAC [1]. Benchmarks vary [2][3]."


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

@pytest.fixture
def render():
    from webapp.app import _render_cited_answer
    return _render_cited_answer


def test_markers_become_links_and_source_list_renders(render):
    a_html, src_html = render(CITED_ANSWER, json.dumps(CITATIONS))
    assert '<a href="https://ex.com/a"' in a_html
    assert '>[1]</a>' in a_html and '>[2]</a>' in a_html and '>[3]</a>' in a_html
    assert 'sup class="cite"' in a_html
    # Source list: numbered to match, typed icons, library entry traceable.
    assert "[1] Saved piece" in src_html and "https://ex.com/b" in src_html
    assert "archive #42" in src_html
    assert "archive #" not in src_html.split("ex.com/b")[1]  # feed/web carry no id


def test_out_of_range_and_year_like_markers_stay_literal(render):
    a_html, _ = render("See [1] and [9] but not [2026].", json.dumps(CITATIONS[:1]))
    assert '>[1]</a>' in a_html
    assert "[9]" in a_html and "<a" not in a_html.split(">[1]</a>")[1]
    assert "[2026]" in a_html


def test_markdown_style_links_not_double_linkified(render):
    # The client regex excludes [n]( — same contract here.
    a_html, _ = render("A [1](https://other.example) reference.", json.dumps(CITATIONS))
    assert "[1](https://other.example)" in a_html
    assert "<sup" not in a_html


def test_legacy_empty_snapshot_degrades_to_plain_text(render):
    # Rows predating citations_json are backfilled with '[]': markers must
    # stay literal, no source list, no error — never fabricated links.
    a_html, src_html = render("Old answer with [1] and [2].", "[]")
    assert "[1]" in a_html and "[2]" in a_html
    assert "<a" not in a_html and "<sup" not in a_html
    assert src_html == ""


@pytest.mark.parametrize("bad", ["not json", "{}", '{"n": 1}', "null", '["str", 3]', None, ""])
def test_malformed_snapshots_never_raise(render, bad):
    a_html, src_html = render("Answer [1].", bad)
    assert "[1]" in a_html
    assert src_html == ""


def test_citation_without_url_stays_literal(render):
    a_html, _ = render("Answer [1].", json.dumps([{"n": 1, "title": "T", "type": "web"}]))
    assert "<a" not in a_html and "[1]" in a_html


def test_long_answer_renders_in_full_with_markers_intact(render):
    # Never cut off text: no length limit, no ellipsis, a late marker still
    # resolves to its link.
    text = ("word " * 3000) + "closing claim [3]."
    a_html, _ = render(text, json.dumps(CITATIONS))
    assert "&hellip;" not in a_html
    assert a_html.count("word ") == 3000
    assert a_html.endswith("closing claim " + '<sup class="cite"><a href="https://ex.com/c" target="_blank" '
                           'rel="noopener" title="Web hit">[3]</a></sup>.')


def test_render_cited_answer_has_no_truncate_parameter(render):
    import inspect
    assert "truncate" not in inspect.signature(render).parameters


def test_hostile_snapshot_content_is_escaped(render):
    evil = [{"n": 1, "title": '<script>x</script>" onmouseover="y',
             "url": 'https://ex.com/"><script>z</script>', "type": "web"}]
    a_html, src_html = render("Answer <b>[1]</b>.", json.dumps(evil))
    for html in (a_html, src_html):
        assert "<script>" not in html
        assert '"><script>' not in html
    assert "&lt;b&gt;" in a_html  # answer text itself stays escaped


# ---------------------------------------------------------------------------
# The three surfaces + the CSV export
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user", name="Member One")
    cited_id = lib.record_ask_question(
        uid, "What is CAC payback?", CITED_ANSWER,
        "claude-sonnet-4-6", "standard", True, True, True,
        cost_usd=0.02, citations=CITATIONS)
    legacy_id = lib.record_ask_question(
        uid, "Legacy question?", "Legacy answer citing [1] before snapshots.",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    lib.record_ask_feedback(cited_id, uid, "inaccurate", comment="check it")
    lib.record_ask_feedback(legacy_id, uid, "helpful")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _login(appmod, username, password):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    return c


@pytest.mark.parametrize("path,who", [
    ("/ask/history", "member1"),
    ("/admin/fpa-buddy/feedback", "admin"),
])
def test_surface_renders_linked_markers_and_source_list(env, path, who):
    pw = "adminpass" if who == "admin" else "supersecret"
    html = _login(env, who, pw).get(path).text
    # Cited turn: markers are links against the turn's own snapshot…
    assert '<a href="https://ex.com/a"' in html
    assert '>[1]</a>' in html
    # …with the numbered source list underneath.
    assert "[2] Feed item" in html
    # Legacy '[]' turn on the same page: literal marker, no broken link.
    assert "Legacy answer citing [1] before snapshots." in html


LONG_TAIL = "THE-END-OF-A-LONG-ANSWER [3]."


def _long_answer():
    return ("Filler sentence number one hundred. " * 400) + LONG_TAIL   # ~14,000 chars


def test_history_and_past_questions_show_long_answers_in_full(env):
    lib = Library(os.environ["LINKLIB_DB"])
    uid = lib.get_user("member1")["id"]
    first = lib.record_ask_question(
        uid, "Long one?", _long_answer(), "claude-sonnet-4-6", "deep", True, True, True,
        cost_usd=0.05, citations=CITATIONS)
    lib.conn.execute("UPDATE ask_questions SET conversation_id=? WHERE id=?", (str(first), first))
    lib.conn.commit()
    # A follow-up turn in the same conversation renders through _turn_block.
    lib.record_ask_question(
        uid, "Long follow-up?", _long_answer(), "claude-sonnet-4-6", "deep", True, True, True,
        conversation_id=str(first), turn_index=1, cost_usd=0.05, citations=CITATIONS)
    lib.record_ask_feedback(first, uid, "helpful")
    lib.conn.commit()
    lib.close()
    c = _login(env, "member1", "supersecret")
    history = c.get("/ask/history").text
    assert history.count("THE-END-OF-A-LONG-ANSWER") == 2   # first turn and follow-up
    assert history.count('>[3]</a>') >= 2
    past = c.get("/tools/fpa-buddy").text
    assert "THE-END-OF-A-LONG-ANSWER" in past
    # No answer was cut: the text before each tail is whole and unbroken.
    assert "Filler sentence number one hundred. THE-END" in history
    assert "Filler sentence number one hundred. THE-END" in past
    assert "[3]</a></sup>&hellip;" not in history


def test_past_questions_section_only_shows_helpful_rated(env):
    # /tools/fpa-buddy's past-questions search filters to helpful-rated
    # answers only (Phase 2) — unlike /ask/history and /admin/fpa-buddy/feedback
    # above, which show every turn regardless of rating. This fixture is a
    # genuine mixed-rating case: cited_id is rated 'inaccurate', legacy_id
    # is rated 'helpful' — only legacy_id's content should render here.
    html = _login(env, "member1", "supersecret").get("/tools/fpa-buddy").text
    assert "Legacy answer citing [1] before snapshots." in html
    assert "CAC payback is months to recover CAC" not in html
    assert "https://ex.com/a" not in html


def test_csv_export_stays_raw_with_citations_column(env):
    admin = _login(env, "admin", "adminpass")
    body = admin.get("/admin/fpa-buddy/report/export.csv").text
    rows = list(csv.reader(io.StringIO(body)))
    header = rows[0]
    assert header[-1] == "citations"          # appended LAST, positions stable
    by_q = {r[header.index("question")]: r for r in rows[1:]}
    cited = by_q["What is CAC payback?"]
    # Markers stay literal — deliberately no link conversion in the CSV.
    assert "[1]" in cited[header.index("answer")]
    assert "<a" not in cited[header.index("answer")]
    # The new column resolves them, one plain-text line per source.
    cites = cited[header.index("citations")]
    assert "[1] Saved piece—https://ex.com/a" in cites
    assert "[3] Web hit—https://ex.com/c" in cites
    # Legacy row: empty citations cell, nothing invented.
    assert by_q["Legacy question?"][header.index("citations")] == ""


def test_history_paginates_without_cutting_any_answer(env):
    # 30 conversations -> 25 on page 1 and 5 on page 2; every answer on a page
    # is whole, and "Older"/"Newer" move between the pages.
    lib = Library(os.environ["LINKLIB_DB"])
    uid = lib.get_user("member1")["id"]
    for i in range(30):
        qid = lib.record_ask_question(
            uid, f"Paged question {i:02d}?", _long_answer(), "claude-sonnet-4-6", "deep",
            True, True, True, cost_usd=0.01, citations=CITATIONS)
        lib.conn.execute("UPDATE ask_questions SET conversation_id=? WHERE id=?", (str(qid), qid))
    lib.conn.commit()
    lib.close()
    c = _login(env, "member1", "supersecret")
    p1 = c.get("/ask/history").text
    assert "Older &rarr;" in p1 and "Newer" not in p1
    assert p1.count("THE-END-OF-A-LONG-ANSWER") == 25
    assert "Paged question 29?" in p1 and "Paged question 04?" not in p1
    p2 = c.get("/ask/history?page=2").text
    assert "&larr; Newer" in p2 and "Older &rarr;" not in p2
    assert "Paged question 04?" in p2
    # Out-of-range and junk page values clamp instead of erroring.
    assert c.get("/ask/history?page=99").status_code == 200
    assert c.get("/ask/history?page=0").status_code == 200
    # Everything, across both pages, is present and whole.
    assert (p1 + p2).count("THE-END-OF-A-LONG-ANSWER") >= 30
