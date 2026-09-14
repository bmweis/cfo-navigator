"""/how-this-is-built (PR 34) — the page, its three entry points, and the
_link_phrase/_about_copy_html helpers that wire the About-page phrase link.
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


# --- the page itself --------------------------------------------------------

def test_page_renders_200(env):
    r = _client(env).get("/how-this-is-built")
    assert r.status_code == 200
    assert "How this is built" in r.text


def test_page_not_in_top_nav(env):
    html = _client(env).get("/").text
    nav = html.split("<nav")[1].split("</nav>")[0]
    assert "/how-this-is-built" not in nav


def test_fpa_buddy_is_the_only_linked_surface(env):
    html = _client(env).get("/how-this-is-built").text
    assert 'href="/tools/fpa-buddy/how-it-works"' in html
    assert html.count("Explainer coming soon.") == 3


def test_all_four_surfaces_named(env):
    html = _client(env).get("/how-this-is-built").text
    for title in ("FP&amp;A Buddy", "Web search, restricted to sites I trust",
                  "Profile and description generation", "Matchmakers and compare summaries"):
        assert title in html


def test_page_carries_recognized_width_tier(env):
    rows = env._page_index_snapshot()
    row = next(r for r in rows if r["path"] == "/how-this-is-built")
    assert row["tier"] == "page-standard"
    assert row["flagged"] is False


def test_no_coral_moment_on_this_page(env):
    assert not any(p.startswith("/how-this-is-built") for p in env.coral_moment_problems())


def test_hub_nav_orphans_clean(env):
    assert env.hub_nav_orphans() == []


# --- entry point 1: the About-page phrase link ------------------------------

def test_link_phrase_wraps_the_match_and_escapes_the_rest(env):
    out = env._link_phrase("a & b AI-native thing c", "AI-native", "/x")
    assert out == 'a &amp; b <a href="/x" style="color:var(--navy);">AI-native</a> thing c'


def test_link_phrase_falls_back_to_plain_escaped_text_when_absent(env):
    out = env._link_phrase("no match here & there", "AI-native", "/x")
    assert out == "no match here &amp; there"
    assert "<a " not in out


def test_about_copy_html_links_the_ai_native_phrase_in_default_copy(env):
    html = env._about_copy_html(env._ABOUT_COPY_DEFAULT)
    assert '<a href="/how-this-is-built" style="color:var(--navy);">AI-native before AI-native was a thing</a>' in html


def test_about_copy_html_falls_back_when_phrase_is_edited_out(env):
    html = env._about_copy_html("Para one.\n\nPara two, no special phrase here.")
    assert html == "<p>Para one.</p><p>Para two, no special phrase here.</p>"
    assert "<a " not in html


def test_about_page_links_the_phrase_live(env):
    html = _client(env).get("/about").text
    assert '<a href="/how-this-is-built" style="color:var(--navy);">AI-native before AI-native was a thing</a>' in html


# --- entry point 2: the fourth About-page button ----------------------------

def test_about_page_has_four_buttons_linkedin_last(env):
    html = _client(env).get("/about").text
    assert 'href="/how-this-is-built" class="btn btn-ghost">How this is built</a>' in html
    # LinkedIn stays the last of the four (the one external link).
    idx_htib = html.index('href="/how-this-is-built"')
    idx_linkedin = html.index("linkedin.com/in/bmw-cfo")
    assert idx_htib < idx_linkedin


# --- entry point 3: the homepage link ---------------------------------------

def test_homepage_links_to_the_page(env):
    html = _client(env).get("/").text
    assert 'href="/how-this-is-built"' in html
    assert "See how AI powers this site" in html
