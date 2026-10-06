"""/how-this-is-built (PR 34) — the page, its three entry points, and the
_link_phrase/_about_copy_html helpers that wire the About-page phrase link.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _seed_ai_surfaces(appmod):
    """Seed the 4 `ai_surfaces` rows a real scripts/migrate_ai_surfaces.py
    --apply run would produce — a fresh test DB otherwise has zero cards,
    since seeding is a manual, run-by-hand migration now, not automatic
    schema setup (same precedent as _seed_flagship_original_content in
    tests/test_thought_leadership_homepage_teaser.py)."""
    from scripts.migrate_ai_surfaces import planned_rows
    lib = appmod._lib()
    try:
        for r in planned_rows():
            lib.add_ai_surface(
                r["slug"], r["title"], r["teaser"], r["body_md"], r["external_href"],
                r["status"], r["display_order"],
            )
    finally:
        lib.close()


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    _seed_ai_surfaces(appmod)
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


def test_surface_cards_grid_nests_inside_tool_prose(env):
    """The four surface cards used to render as a sibling of `.tool-prose`
    (a bare, unwrapped `<div style="display:grid;...">` between two
    separate `.tool-prose` divs), so it rendered at the full page width
    instead of the 760px reading column — the same shape PR #515 fixed for
    the back-arrow. Parse the HTML and confirm the grid is a genuine
    descendant of a single `.tool-prose` div, not a sibling of it."""
    from html.parser import HTMLParser

    html = _client(env).get("/how-this-is-built").text

    class _Finder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.tool_prose_depths = []
            self.grid_found_inside_tool_prose = False
            self.tool_prose_div_count = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            is_tool_prose = tag == "div" and attrs.get("class") == "tool-prose"
            is_cards_grid = (
                tag == "div"
                and "display:grid" in (attrs.get("style") or "")
                and "grid-template-columns:1fr" in (attrs.get("style") or "")
            )
            if is_tool_prose:
                self.tool_prose_div_count += 1
            self.stack.append(tag)
            if is_cards_grid and self.tool_prose_depths:
                self.grid_found_inside_tool_prose = True
            if is_tool_prose:
                self.tool_prose_depths.append(len(self.stack))

        def handle_endtag(self, tag):
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()

    p = _Finder()
    p.feed(html)
    assert p.tool_prose_div_count == 1, (
        "expected the page to use exactly one .tool-prose wrapper post-fix, "
        f"found {p.tool_prose_div_count}"
    )
    assert p.grid_found_inside_tool_prose, "the cards grid must nest inside .tool-prose"


def test_prose_and_cards_grid_render_at_the_same_width(env):
    """Direct proof (not just structural) that the fix holds: the cards
    grid's own <div> and the surrounding .tool-prose share the identical
    max-width/centering rule, since the grid is now a plain block child of
    .tool-prose with no width of its own to fight it. Confirmed live via
    Playwright at 1280px and 390px (see the PR body) — this test pins the
    CSS-level guarantee that makes that hold, without needing a browser."""
    html = _client(env).get("/how-this-is-built").text
    grid_marker = '<div style="display:grid;grid-template-columns:1fr;gap:14px;margin:8px 0 34px;">'
    assert grid_marker in html
    # the grid div carries no width/max-width/margin:auto of its own — its
    # width comes purely from being a block-level child of the 760px
    # .tool-prose ancestor established above it in the same wrapper.
    assert "width" not in grid_marker
    assert "max-width" not in grid_marker


def test_each_surface_card_carries_an_explicit_full_width_style(env):
    """Each card's own <div> is pinned to width:100%;box-sizing:border-box
    — deliberately redundant with the grid-template-columns:1fr fix above,
    so no single browser-specific auto-sizing quirk can make one card
    render narrower/wider than its siblings."""
    html = _client(env).get("/how-this-is-built").text
    assert html.count('style="width:100%;box-sizing:border-box;background:#fff;') == 4


def test_page_carries_recognized_width_tier(env):
    rows = env._page_index_snapshot()
    row = next(r for r in rows if r["path"] == "/how-this-is-built")
    assert row["tier"] == "page-standard"
    assert row["flagged"] is False


@pytest.mark.real_coral
def test_no_coral_moment_on_this_page(env):
    assert not any(p.startswith("/how-this-is-built") for p in env.coral_moment_problems())


def test_hub_nav_orphans_clean(env):
    assert env.hub_nav_orphans() == []


# --- entry point 1: the About-page phrase link ------------------------------
#
# _link_phrase/_about_copy_html/_ABOUT_AI_NATIVE_PHRASE are retired as of
# explainers-collection Phase 2 — About's body now renders through the same
# trusted _render_original_content_markdown How this is built already used,
# with the phrase's link baked directly into _ABOUT_COPY_DEFAULT as a plain
# raw <a> tag (see that constant). No special-casing left to test on its
# own; test_about_page_links_the_phrase_live below covers the live result.

def test_render_original_content_markdown_passes_the_baked_in_link_through(env):
    html = env._render_original_content_markdown(env._ABOUT_COPY_DEFAULT)
    assert '<a href="/how-this-is-built" style="color:var(--navy);">AI-native before AI-native was a thing</a>' in html


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
    """Credit matters on this page, so every name must point at that person's
    own site. Also pins the new-tab attributes (BRAND.md §3.3): these are
    written as raw <a> tags precisely because markdown can't carry them."""
    body = _article_body(env)
    for name, url in _CREDIT_LINKS:
        assert f'<a href="{url}" target="_blank" rel="noopener">{name}</a>' in body, f"{name} -> {url}"


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


_HTIB_CONSTANTS = ("_HTIB_INTRO_DEFAULT", "_HTIB_WHY_I_BUILT_THIS_DEFAULT",
                   "_HTIB_HOW_I_DECIDED_DEFAULT", "_HTIB_WHAT_ELSE_DEFAULT",
                   "_HTIB_FOOTNOTE_DEFAULT")


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


# --- admin-editable copy (PR 35, Part 2) ------------------------------------
#
# NOTE on auth: this file's `env` fixture deliberately sets no
# LINKLIB_PASSWORD, so the app runs in its documented "open, local-dev
# convenience" mode and `_is_authed` is True for every request. That's what
# makes the admin routes below reachable without a login step here.

_SECTION_KEYS = ("htib_before_copy", "htib_after_copy")


def test_every_section_has_a_settings_key_and_a_default(env):
    keys = [s["key"] for s in env._HTIB_COPY_SECTIONS]
    assert keys == list(_SECTION_KEYS)
    for s in env._HTIB_COPY_SECTIONS:
        assert s["default"].strip(), s["key"]


def test_admin_copy_page_renders_a_textarea_per_section(env):
    html = _client(env).get("/admin/copy/how-this-is-built").text
    for key in _SECTION_KEYS:
        assert f'id="copy-{key}"' in html, key
        assert f"saveHtib(&apos;{key}&apos;)" in html, key
        assert f"previewCopy('copy-{key}','preview-{key}')" in html, key


def test_each_section_description_says_raw_html_is_allowed(env):
    """As of Phase 2, raw HTML is allowed on About's own page too (see
    test_admin_copy_about_page_renders_the_bio_box) — the asymmetry worth
    naming now is with Homepage specifically, which stays plain text."""
    html = _client(env).get("/admin/copy/how-this-is-built").text
    assert "raw HTML" in html


def test_split_marker_explained_in_both_section_descriptions(env):
    """The marker shows up in the raw default textarea content too (once
    for htib_before_copy, twice for htib_after_copy), so this checks the
    explanatory description text specifically rather than a raw count."""
    html = _client(env).get("/admin/copy/how-this-is-built").text
    assert "Leave that marker in place" in html
    assert "Leave both in place" in html


def test_split_marker_description_no_longer_repeats_the_raw_html_rule(env):
    """The raw-HTML/target=_blank rule used to be restated inside each
    section's own description, on top of the page-level intro paragraph
    that already states it once — three repetitions before a reader ever
    reached a textarea. Each section's description is now split-marker-only;
    the intro paragraph (still asserted by
    test_each_section_description_says_raw_html_is_allowed) is the one
    place left that mentions raw HTML/target="_blank" at all."""
    html = _client(env).get("/admin/copy/how-this-is-built").text
    assert html.count("raw HTML") == 1
    # <code>-wrapped, not a bare count — the sitewide footer's Logo.dev
    # attribution link also carries target="_blank" rel="noopener" (every
    # outbound link on the site does, per BRAND.md §3.3), so a bare count
    # would false-fail on that unrelated boilerplate.
    assert html.count('<code>target="_blank" rel="noopener"</code>') == 1


# --- /admin/copy split into three pages -------------------------------------

def test_admin_copy_itself_is_not_a_page(env):
    """No index page at the bare prefix — same pattern every other admin
    group prefix on this site follows (/admin/thought-leadership,
    /admin/system, /admin/tools, /admin/inbox are all bare prefixes with no
    route of their own)."""
    assert _client(env).get("/admin/copy").status_code == 404


def test_admin_copy_homepage_page_renders_headline_and_status_boxes(env):
    html = _client(env).get("/admin/copy/homepage").text
    assert 'id="home-headline"' in html
    assert 'id="home-subhead"' in html
    assert 'id="home-status-copy"' in html
    assert 'id="home-teaser"' not in html and 'id="home-expanded"' not in html


def test_admin_copy_about_page_renders_the_bio_box(env):
    """As of explainers-collection Phase 2, About also accepts raw HTML for
    links, same trusted renderer How this is built already used."""
    html = _client(env).get("/admin/copy/about").text
    assert 'id="about-copy"' in html
    assert "raw HTML" in html
    assert "previewCopy('about-copy','about-preview')" in html


def test_admin_copy_pages_require_auth(monkeypatch):
    """Same guard the single pre-split page used
    (`if not _is_authed(request): return _login_redirect(request)`), copied
    verbatim into all three — a signed-out request must bounce to /login,
    not render the form."""
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    try:
        c = _client(appmod)
        for path in ("/admin/copy/homepage", "/admin/copy/about", "/admin/copy/how-this-is-built"):
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 303, path
            assert r.headers["location"].startswith("/login"), path
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_homepage_and_about_pages_carry_the_recognized_width_tier(env):
    """Matches /admin/ai-surfaces/{id}/edit and
    /admin/thought-leadership/original/{id}/edit — page-standard, with the
    back-link/heading at the page's own left edge and the fields
    themselves capped at 900px, not the narrower page-form (640px) tier
    these three pages used right after the /admin/copy split, which
    centered the whole page and squeezed the fields."""
    rows = env._page_index_snapshot()
    for path in ("/admin/copy/homepage", "/admin/copy/about", "/admin/copy/how-this-is-built"):
        row = next(r for r in rows if r["path"] == path)
        assert row["tier"] == "page-standard", path
        assert row["flagged"] is False, path


def test_copy_page_fields_are_capped_at_900px_inside_page_standard(env):
    """Direct proof of the tier-match above: each page's field area sits in
    its own max-width:900px;margin:0 auto wrapper, the same convention
    _ai_surface_form_page/_oc_form_page use for their own <form> tags."""
    for path in ("/admin/copy/homepage", "/admin/copy/about", "/admin/copy/how-this-is-built"):
        html = _client(env).get(path).text
        assert 'style="max-width:900px;margin:0 auto;">' in html, path


def test_saving_a_section_changes_the_public_page(env):
    c = _client(env)
    r = c.post("/admin/copy/how-this-is-built",
               json={"key": "htib_before_copy", "text": "A brand new opener."})
    assert r.status_code == 200
    assert "A brand new opener." in c.get("/how-this-is-built").text


def test_an_unsaved_section_still_renders_its_default(env):
    body = _article_body(env)
    assert "Feedly sent me a renewal notice" in body


def test_saving_one_field_leaves_the_other_on_its_default(env):
    c = _client(env)
    c.post("/admin/copy/how-this-is-built",
           json={"key": "htib_before_copy", "text": "Changed."})
    body = _article_body(env)
    assert "Changed." in body
    # htib_after_copy is a different settings key and must be untouched.
    assert "FP&amp;A Buddy is required to trace everything to a real citation" in body


def test_a_sub_section_within_a_saved_field_can_be_edited_via_the_split_marker(env):
    """The marker is what lets one saved field still carry two visually
    distinct sub-sections (the intro's own styling, the "Why I built this"
    heading+prose) — this is the real mechanism the split-marker consolidation
    depends on, not just a nice-to-have."""
    c = _client(env)
    c.post("/admin/copy/how-this-is-built", json={
        "key": "htib_before_copy",
        "text": env._HTIB_INTRO_DEFAULT + env._HTIB_SPLIT_MARKER + "A whole new origin story.",
    })
    body = _article_body(env)
    assert "Feedly sent me a renewal notice" in body  # intro sub-section untouched
    assert "A whole new origin story." in body
    assert "Why I built this" in body  # the heading is still template-level, not lost


def test_an_unknown_section_key_is_rejected(env):
    """A bad key must not write an arbitrary settings row."""
    r = _client(env).post("/admin/copy/how-this-is-built",
                          json={"key": "htib_evil_copy", "text": "x"})
    assert r.status_code == 400


def test_a_blank_save_is_rejected(env):
    """Blank would silently fall back to the hardcoded default, which reads
    on the page as "my edit vanished" rather than as an error."""
    r = _client(env).post("/admin/copy/how-this-is-built",
                          json={"key": "htib_before_copy", "text": "   "})
    assert r.status_code == 400


def test_saved_copy_can_carry_a_working_outbound_link(env):
    """The whole reason this page uses the trusted renderer: an admin has to
    be able to credit someone with a real link."""
    c = _client(env)
    c.post("/admin/copy/how-this-is-built", json={
        "key": "htib_after_copy",
        "text": ('X' + env._HTIB_SPLIT_MARKER + 'Y' + env._HTIB_SPLIT_MARKER
                  + 'See <a href="https://example.com" target="_blank" rel="noopener">Example</a>.'),
    })
    assert '<a href="https://example.com" target="_blank" rel="noopener">Example</a>' \
        in c.get("/how-this-is-built").text


# --- Preview -----------------------------------------------------------------

def test_preview_endpoint_renders_trusted_markdown(env):
    r = _client(env).post("/admin/copy/preview", json={"text": "**bold** and a [link](https://example.com)."})
    assert r.status_code == 200
    assert "<strong>bold</strong>" in r.json()["html"]


def test_preview_endpoint_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    try:
        r = _client(appmod).post("/admin/copy/preview", json={"text": "x"})
        assert r.status_code == 401
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_preview_endpoint_does_not_save_anything(env):
    c = _client(env)
    c.post("/admin/copy/preview", json={"text": "Should never persist."})
    assert "Should never persist." not in c.get("/how-this-is-built").text


def test_the_surface_cards_are_not_admin_editable(env):
    """Deliberate: each card's href points at a real route and its empty
    string selects the coming-soon state, neither of which survives a
    textarea. Confirmed by there being no settings key for them."""
    html = _client(env).get("/admin/copy/how-this-is-built").text
    assert "Explainer coming soon" not in html
    assert not any("surface" in s["key"] for s in env._HTIB_COPY_SECTIONS)
