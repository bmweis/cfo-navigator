"""Admin-edited multi-paragraph copy fields (hero subhead, bio box lead line,
rest of bio, About page copy) must render as separate <p> tags on the live
site, not as one continuous block. Blank-line paragraph breaks survive in the
textarea and in the DB, but a raw {_esc(text)} interpolation into a single
<p> loses them, since HTML collapses literal newlines — this was the bug.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def test_copy_paragraphs_html_splits_on_blank_lines(env):
    html = env._copy_paragraphs_html("First paragraph.\n\nSecond paragraph.")
    assert html == "<p>First paragraph.</p><p>Second paragraph.</p>"


def test_copy_paragraphs_html_applies_style_to_every_p(env):
    html = env._copy_paragraphs_html("One.\n\nTwo.", style="color:red;")
    assert html == '<p style="color:red;">One.</p><p style="color:red;">Two.</p>'


def test_copy_paragraphs_html_single_paragraph_no_style(env):
    html = env._copy_paragraphs_html("Just one paragraph.")
    assert html == "<p>Just one paragraph.</p>"


def test_homepage_subhead_renders_multiple_paragraphs(env):
    lib = env._lib()
    try:
        lib.set_setting("homepage_subhead_copy", "First subhead paragraph.\n\nSecond subhead paragraph.")
    finally:
        lib.close()
    c = _client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert "<p" in r.text
    assert ('<p style="font-size:18px;line-height:1.65;color:var(--ink-soft);margin:0 0 12px;">'
            'First subhead paragraph.</p>') in r.text
    assert ('<p style="font-size:18px;line-height:1.65;color:var(--ink-soft);margin:0 0 12px;">'
            'Second subhead paragraph.</p>') in r.text


def test_homepage_status_renders_multiple_paragraphs(env):
    lib = env._lib()
    try:
        lib.set_setting("homepage_status_copy", "First status line.\n\nSecond status line.")
    finally:
        lib.close()
    r = _client(env).get("/")
    assert r.status_code == 200
    assert "<p>First status line.</p>" in r.text
    assert "<p>Second status line.</p>" in r.text
    assert ".home-status-body p{font-size:14px;line-height:1.55;color:var(--ink-soft);margin:0 0 10px;}" in r.text


def test_about_page_renders_multiple_paragraphs(env):
    lib = env._lib()
    try:
        lib.set_setting("about_page_copy", "About para one.\n\nAbout para two.")
    finally:
        lib.close()
    c = _client(env)
    r = c.get("/about")
    assert r.status_code == 200
    assert "<p>About para one.</p>" in r.text
    assert "<p>About para two.</p>" in r.text
