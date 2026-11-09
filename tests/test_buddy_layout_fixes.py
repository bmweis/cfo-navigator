"""FP&A Buddy layout fixes (2026-11): the past-questions page and the history page use the
main page's width (no inner 760px reading column), Hide shares one footer row with Make
private and Resume (first), and the real sample card is short, top-aligned beside the form
on a desktop and below the form on a phone."""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library
from tests import test_buddy_phone_fixes as T


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    boss = lib.create_user("boss", "supersecret", role="admin")
    other = lib.create_user("other", "supersecret", role="user")
    member = lib.create_user("member", "supersecret", role="user")
    mk = lambda uid, q: lib.record_ask_question(uid, q, "Answer.", "m", "standard", True, False, True)
    mk(boss, "Boss own question")
    mk(other, "Other users question")
    mk(member, "Member own question")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def get(user, path):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c.get(path).text
    yield get
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _row(html, title):
    i = html.index(title)
    start = html.rindex('<details class="ask-pq-row">', 0, i)
    return html[start:html.index("</details>", i)]


@pytest.mark.parametrize("path", ["/tools/fpa-buddy/questions", "/ask/history"])
def test_list_pages_use_the_main_page_width(site, path):
    html = site("boss", path)
    assert '<div class="page page-standard">' in html
    assert 'class="tool-prose"' not in html          # the 760px reading column is gone
    assert 'class="ask-list-page"' in html
    assert ".ask-list-page .ask-pq-form{max-width:640px;}" in html
    assert ".ask-list-page .ask-hist-answer{max-width:820px;}" in html


def test_hide_is_first_in_the_same_footer_as_make_private_and_resume(site):
    row = _row(site("boss", "/tools/fpa-buddy/questions"), "Boss own question")
    assert row.count('class="ask-pq-foot"') == 1
    foot = row[row.index('class="ask-pq-foot"'):]
    hide, priv, resume = foot.index(">Hide<"), foot.index(">Make private<"), foot.index(">Resume<")
    assert hide < priv < resume
    assert "border-top:1px solid var(--line)" not in row       # the old separate admin row


def test_admin_on_someone_elses_row_has_hide_alone_in_the_footer(site):
    row = _row(site("boss", "/tools/fpa-buddy"), "Other users question")
    foot = row[row.index('class="ask-pq-foot"'):]
    assert ">Hide<" in foot and ">Make private<" not in foot and ">Resume<" not in foot


def test_non_admin_has_no_hide_but_keeps_make_private_and_resume(site):
    html = site("member", "/tools/fpa-buddy/questions")
    assert ">Hide<" not in html and ">Unhide<" not in html
    foot = _row(html, "Member own question")
    foot = foot[foot.index('class="ask-pq-foot"'):]
    assert ">Make private<" in foot and ">Resume<" in foot


def test_footer_wraps_so_hide_stays_first_on_a_phone(site):
    html = site("boss", "/tools/fpa-buddy/questions")
    assert ".ask-pq-foot{display:flex;flex-wrap:wrap;justify-content:flex-end" in html
    assert re.search(r"\.ask-pq-foot,\.fu-foot\{display:flex;flex-wrap:wrap;justify-content:flex-end", site("boss", "/tools/fpa-buddy"))


def test_sample_card_is_clipped_short_top_aligned_and_below_the_form_on_a_phone(site):
    html = site("member", "/tools/fpa-buddy")
    assert ".ask-example.ask-ex-js:not(.open) .ask-answer{max-height:120px" in html
    assert ".fpa-intro-area-example{grid-area:example;align-self:start;}" in html
    # phone grid: the example comes after the question (and the similar-questions card)
    m = re.search(r"@media\(max-width:900px\)\{\.fpa-intro-layout\{[^}]*grid-template-areas:([^;]+);", html)
    assert m, "phone grid areas not found"
    areas = re.findall(r'"(\w+)"', m.group(1))
    assert areas.index("question") < areas.index("example")
    assert 'class="ask-example"' in html and "A real question and answer, shown as asked." in html


@pytest.fixture
def page_html(site):
    return site("member", "/tools/fpa-buddy")


def _geometry(html, width):
    got = T._launch()
    if got is None:
        pytest.skip("no Chromium available")
    pw, b = got
    try:
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.route("**/*", lambda r: r.abort())
        pg.set_content(html, wait_until="domcontentloaded")
        return pg.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect();
            const e = r('.fpa-intro-area-example'), q = r('.fpa-intro-area-question');
            return {exTop: e.top, exBottom: e.bottom, exH: e.height, qTop: q.top, qBottom: q.bottom,
                    exLeft: e.left, qLeft: q.left}; }""")
    finally:
        b.close()
        pw.stop()


def test_desktop_example_is_beside_the_form_short_and_not_stretched(page_html):
    g = _geometry(page_html, 1280)
    assert g["exLeft"] > g["qLeft"]            # beside, in the right column
    assert g["exH"] < 460                      # was ~680 before the clip
    assert g["exBottom"] < g["qBottom"] - 100  # top-aligned, no stretch to the form's height


def test_phone_example_is_below_the_form(page_html):
    g = _geometry(page_html, 390)
    assert g["exTop"] >= g["qBottom"] - 1
