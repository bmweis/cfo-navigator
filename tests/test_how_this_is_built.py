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
    for title in ("FP&amp;A Buddy", "Web search",
                  "Profile and description generation", "Matchmakers and compare summaries"):
        assert title in html


def test_web_search_card_says_four_jobs(env):
    """PR 35's copy counts Exa's jobs as four, not three — the number is the
    whole point of that card, so pin it rather than leaving it to prose drift."""
    html = _client(env).get("/how-this-is-built").text
    assert "One search engine doing four different jobs behind the scenes." in html


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


# --- PR 35: Brian's own copy, the credit links, and the skip link -----------

# The nine blog credits in "Why I built this", in the order the copy names
# them. Credit is the whole reason that section exists, so a wrong or dropped
# URL is a real defect, not a typo — pinned here rather than left to prose.
_CREDIT_LINKS = [
    ("Brad Feld", "https://feld.com"),
    ("Fred Wilson", "https://avc.xyz"),
    ("Mark Suster", "https://bothsidesofthetable.com"),
    ("Dave Kellogg", "https://kellblog.com"),
    ("David Skok", "https://forentrepreneurs.com"),
    ("Gordon Daugherty", "https://shockwaveinnovations.com"),
    ("CJ Gustafson", "https://mostlymetrics.com"),
    ("OnlyCFO", "https://onlycfo.io"),
    ("Feedly", "https://feedly.com"),
]


def _article_body(appmod) -> str:
    """Just the article, with the shared nav/footer chrome stripped off."""
    html = _client(appmod).get("/how-this-is-built").text
    return html.split("<h1>How this is built</h1>")[1].split("<footer")[0]


def test_every_credit_link_renders_with_the_right_url(env):
    body = _article_body(env)
    for name, url in _CREDIT_LINKS:
        assert f'<a href="{url}">{name}</a>' in body, f"{name} -> {url}"


def test_credit_links_appear_in_the_order_the_copy_names_them(env):
    body = _article_body(env)
    positions = [body.index(url) for _, url in _CREDIT_LINKS]
    assert positions == sorted(positions)


def test_footnote_records_the_avc_move(env):
    body = _article_body(env)
    assert 'href="https://avc.com"' in body      # the original archive
    assert "in 2024" in body


def test_section_headings_in_order(env):
    body = _article_body(env)
    headings = ["Why I built this", "Where AI shows up",
                "How I decided what AI should do", "What else I've built with AI"]
    positions = []
    for h in headings:
        assert h in body, h
        positions.append(body.index(h))
    assert positions == sorted(positions)


def test_skip_link_targets_the_surface_cards_section(env):
    """The skip link has to land on the heading that actually holds the four
    cards — an anchor pointing at nothing is worse than no skip link."""
    body = _article_body(env)
    assert 'href="#where-ai-shows-up"' in body
    assert 'id="where-ai-shows-up"' in body
    # The anchor precedes the cards it's skipping to.
    assert body.index('id="where-ai-shows-up"') < body.index("Explainer coming soon.")


def test_intro_is_the_feedly_renewal_line(env):
    body = _article_body(env)
    assert "Feedly sent me a renewal notice and I decided to build it myself instead." in body
    # The pre-PR-35 intro is gone, not merely pushed down the page.
    assert "I was AI-native before AI-native was a thing" not in body


def test_markdown_renders_as_real_html_not_literal_syntax(env):
    """The prose goes through the admin-trusted markdown renderer, so a
    [text](url) must become an anchor — if it ever regressed to the restricted
    renderer (webapp/markdown_render.py) the raw syntax would show instead."""
    body = _article_body(env)
    assert "[Brad Feld](https://feld.com)" not in body
    assert "<em>where the answers were</em>" in body


_HTIB_CONSTANTS = ("_HTIB_INTRO", "_HTIB_WHY_I_BUILT_THIS", "_HTIB_HOW_I_DECIDED",
                   "_HTIB_WHAT_ELSE", "_HTIB_FOOTNOTE")


def test_copy_passes_the_typography_lint(env):
    """Brian's copy shipped verbatim; this pins that it needs no exception.

    Lints each constant's own text rather than filtering whole-file findings
    by constant name: a finding is (kind, line, excerpt) and carries no name,
    so a name filter would match nothing and this test could never fail.
    See the negative control below.
    """
    from linklib.voice_review import typography_findings
    for name in _HTIB_CONSTANTS:
        src = f"X = {getattr(env, name)!r}"
        assert typography_findings(src) == [], name


def test_the_typography_check_above_can_actually_fail(env):
    """Negative control for the test directly above — same construction, on
    copy that really does violate both rules. Without this, a lint that
    silently stopped finding anything would look like a pass."""
    from linklib.voice_review import typography_findings
    bad = "Feeds & sources — the ones I read."
    kinds = {kind for kind, _line, _excerpt in typography_findings(f"X = {bad!r}")}
    assert kinds == {"bare-ampersand", "spaced-em-dash"}
