"""Past-questions list card: question once, selectable text, a separate toggle at the lower right.

Mirrors the earlier-turn row (.ask-turn-row): the card is a container, not a <details>/<summary>, so a click
or long-press on the question text never toggles; only the button does. Markup tests only (no Chromium).
"""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library

QUESTION = "Which stage planning assumptions matter most for a mid-sized SaaS runway model"


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    author = lib.create_user("author", "supersecret", role="user")
    lib.create_user("reader", "supersecret", role="user")
    lib.record_ask_question(author, QUESTION, "Plain answer [1].", "claude-sonnet-4-6", "standard", True, False,
                            True, conversation_id="c1", turn_index=0,
                            citations=[{"n": 1, "title": "T", "url": "https://example.com/a", "type": "library"}])
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "reader", "password": "supersecret"},
                  follow_redirects=False).status_code in (302, 303)
    yield c
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _row(html):
    i = html.index('class="ask-pq-row"')
    start = html.rindex("<", 0, i)
    return html[start:html.index("</button>\n</div>", i) + len("</button>\n</div>")]


@pytest.mark.parametrize("path", ["/tools/fpa-buddy", "/tools/fpa-buddy/questions"])
def test_row_has_no_summary_and_a_separate_toggle_button(client, path):
    row = _row(client.get(path).text)
    own = row.split('<details class="ask-src-disc"')[0]      # the nested Sources disclosure is a different thing
    assert "<details" not in own and "<summary" not in own
    assert row.startswith('<div class="ask-pq-row"')
    m = re.search(r'<button type="button" class="ask-pq-toggle" aria-expanded="false" aria-label="Show answer"[^>]*>', row)
    assert m, row
    assert 'class="ask-pq-rc"' in row            # the sitewide 14px triangle
    assert '<div class="ask-pq-body" hidden>' in row


@pytest.mark.parametrize("path", ["/tools/fpa-buddy", "/tools/fpa-buddy/questions"])
def test_question_appears_once_and_outside_the_toggle(client, path):
    row = _row(client.get(path).text)
    assert row.count(QUESTION) == 1
    head = row[:row.index('<div class="ask-pq-body"')]
    assert QUESTION in head
    toggle = row[row.index('<button type="button" class="ask-pq-toggle"'):]
    assert QUESTION not in toggle


def test_toggle_handler_keeps_aria_in_step(client):
    row = _row(client.get("/tools/fpa-buddy/questions").text)
    onclick = re.search(r'onclick="([^"]*)"', row).group(1)
    for part in ("classList.toggle('open')", ".hidden=!o", "aria-expanded", "Hide answer", "Show answer"):
        assert part in onclick


def test_css_puts_the_chevron_lower_right_and_the_text_selectable(client):
    html = client.get("/tools/fpa-buddy/questions").text
    toggle = re.search(r"\.ask-pq-toggle\{([^}]*)\}", html).group(1)
    assert "position:absolute" in toggle and "right:0" in toggle and "bottom:0" in toggle
    assert "width:44px" in toggle and "height:44px" in toggle
    assert "cursor:pointer" not in re.search(r"\.ask-pq-sum\{([^}]*)\}", html).group(1)
    q = re.search(r"\.ask-pq-q\{([^}]*)\}", html).group(1)
    assert "user-select:text" in q
    assert ".ask-pq-toggle[aria-expanded=\"true\"] .ask-pq-rc{transform:rotate(90deg);}" in html
    assert ".ask-pq-toggle:focus-visible{outline:2px solid var(--navy)" in html


@pytest.mark.parametrize("path", ["/tools/fpa-buddy", "/tools/fpa-buddy/questions"])
def test_every_row_loads_collapsed(client, path):
    html = client.get(path).text
    assert 'class="ask-pq-row open"' not in html
    assert 'class="ask-pq-toggle" aria-expanded="true"' not in html
