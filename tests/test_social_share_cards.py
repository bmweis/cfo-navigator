"""Social share cards, Phase 1 (2026-09) — Open Graph / Twitter Card metadata
emitted from _page(), plus the committed webapp/static/og/ image directory,
its serving route, and the /admin/brand download list.

Phase 2 (image generation) was investigated and killed outright — see
CLAUDE.md's "Social share cards" bullet for the reasoning. This file covers
Phase 1 only: metadata emission, the two escaping paths (pre-encoded
original_content.teaser vs. plain ai_surfaces.teaser), and the
filename-existence-check mechanism for og:image.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


@pytest.fixture
def og_dir(env, monkeypatch, tmp_path):
    """Points webapp.app._OG_DIR at an isolated temp directory and resets
    the filename-set cache — the pattern every test that adds/removes files
    under _OG_DIR must use (see _og_image_slugs' own docstring)."""
    d = tmp_path / "og"
    d.mkdir()
    monkeypatch.setattr(env, "_OG_DIR", str(d))
    monkeypatch.setattr(env, "_OG_IMAGE_SLUGS", None)
    yield d


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _write_png(path):
    # A minimal valid 1x1 PNG — content doesn't matter, only that the file exists
    # and the route can serve it; no test in this file inspects pixel data.
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
        "de0000000c4944415478da6360000002000155ee2a1c0000000049454e44ae42"
        "6082"))


def _add_oc(appmod, **kwargs):
    lib = appmod._lib()
    try:
        return lib.add_original_content(**kwargs)
    finally:
        lib.close()


def _add_ai_surface(appmod, **kwargs):
    lib = appmod._lib()
    try:
        return lib.add_ai_surface(**kwargs)
    finally:
        lib.close()


# -- Basic tag emission --------------------------------------------------

def test_homepage_emits_og_and_twitter_meta_tags(env):
    html = _client(env).get("/").text
    assert '<meta property="og:title" content=' in html
    assert '<meta property="og:description" content=' in html
    assert '<meta property="og:image" content=' in html
    assert '<meta property="og:url" content=' in html
    assert '<meta property="og:type" content="website">' in html
    assert '<meta property="og:site_name" content="CFO Navigator">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title" content=' in html
    assert '<meta name="twitter:description" content=' in html
    assert '<meta name="twitter:image" content=' in html
    assert '<meta name="description" content=' in html


def test_og_title_has_no_site_prefix(env):
    """og:title reuses _short_title(title) — the stripped title BEFORE the
    "BMW CFO · " prefix _page() adds to the real <title> tag, and AFTER
    _short_title() strips the "—Brian Weisberg" suffix the <title> tag
    itself carries."""
    _add_oc(env, slug="ger", title="The Growth Engine Ratio", tag_label="Framework",
            teaser="A metric.", body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/ger").text
    assert '<meta property="og:title" content="The Growth Engine Ratio">' in html
    assert '<title>BMW CFO · The Growth Engine Ratio</title>' in html


def test_og_url_and_image_are_absolute(env):
    html = _client(env).get("/").text
    for tag in ('property="og:url"', 'property="og:image"', 'name="twitter:image"'):
        segment = html.split(tag, 1)[1][:200]
        content = segment.split('content="', 1)[1].split('"', 1)[0]
        assert content.startswith("https://bmweis.com"), (tag, content)


def test_og_url_reflects_the_actual_request_path(env):
    _add_oc(env, slug="ger", title="The Growth Engine Ratio", tag_label="Framework",
            teaser="A metric.", body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/ger").text
    assert '<meta property="og:url" content="https://bmweis.com/thought-leadership/ger">' in html


def test_admin_page_falls_back_to_site_root_without_request_threaded(env):
    """Not every _page() call threads request= through — those pages (mostly
    admin, plus two small nested-form helpers) fall back to the bare
    PUBLIC_BASE for og:url rather than erroring or omitting the tag."""
    c = _admin_client(env)
    html = c.get("/admin").text
    assert '<meta property="og:url" content="https://bmweis.com">' in html


def test_default_description_used_when_no_teaser_given(env):
    html = _client(env).get("/about").text
    assert "Brian Weisberg&#x27;s CFO Navigator" not in html  # sanity: not double-escaped junk
    assert env._OG_DEFAULT_DESCRIPTION.split(":")[0] in html


# -- The two escaping paths ------------------------------------------------

def test_original_content_teaser_is_not_double_escaped(env):
    """original_content.teaser is stored PRE-ENCODED (real &amp;/&mdash;
    entities already in the string) — og:description must interpolate it
    raw plus a bare-quote guard, never through the normal _esc(), or an
    existing &amp; becomes &amp;amp; (renders as literal "&amp;D" to a
    scraper). This is the exact production teaser for growth-engine-ratio."""
    _add_oc(env, slug="ger", title="The Growth Engine Ratio", tag_label="Framework",
            teaser="A metric for how R&amp;D and GTM investments work together to "
                   "drive growth&mdash;with an interactive calculator.",
            body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/ger").text
    desc_tag = [l for l in html.split("\n") if 'property="og:description"' in l][0]
    assert "R&amp;D" in desc_tag
    assert "R&amp;amp;D" not in desc_tag
    assert "&amp;amp;" not in desc_tag


def test_original_content_teaser_quote_is_escaped(env):
    """The one character _esc_attr_quote_only guards against: a literal
    double quote in an already-pre-encoded teaser would otherwise break out
    of the content="..." attribute."""
    _add_oc(env, slug="quoted", title="Quoted Piece", tag_label="Framework",
            teaser='A piece about the "real" numbers.',
            body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/quoted").text
    assert 'content="A piece about the &quot;real&quot; numbers.">' in html


def test_ai_surface_teaser_is_escaped_normally(env):
    """ai_surfaces.teaser is stored as PLAIN text (no pre-encoding) — the
    opposite convention from original_content.teaser. A literal ampersand
    here must go through the normal _esc(), or it would render unescaped
    (invalid HTML / a raw & inside an attribute)."""
    _add_ai_surface(env, slug="fp-a-buddy", title="FP&A Buddy",
                     teaser="Answers questions from my archive & the web.",
                     body_md="Body.", status="live")
    html = _client(env).get("/how-this-is-built/fp-a-buddy").text
    desc_tag = [l for l in html.split("\n") if 'property="og:description"' in l][0]
    assert "archive &amp; the web" in desc_tag
    assert "archive & the web" not in desc_tag


def test_esc_attr_quote_only_leaves_ampersand_and_entities_alone(env):
    helper = env._esc_attr_quote_only
    assert helper("R&amp;D&mdash;done") == "R&amp;D&mdash;done"
    assert helper('a "quoted" phrase') == "a &quot;quoted&quot; phrase"
    assert helper("") == ""
    # Matches _esc()'s own established str(s)-or-"" pattern exactly (same
    # module, same convention) — str(None) is the truthy string "None", so
    # this isn't a None-safety guarantee, deliberately consistent with the
    # existing helper rather than diverging from it.
    assert helper(None) == "None"


# -- og:image slug lookup ---------------------------------------------------

def test_og_image_falls_back_to_default_when_no_file_exists(env, og_dir):
    _write_png(og_dir / "default.png")
    _add_oc(env, slug="no-card-yet", title="No Card Yet", tag_label="Framework",
            teaser="Teaser.", body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/no-card-yet").text
    assert '<meta property="og:image" content="https://bmweis.com/static/og/default.png">' in html


def test_og_image_uses_matching_slug_when_present(env, og_dir):
    _write_png(og_dir / "default.png")
    _write_png(og_dir / "growth-engine-ratio.png")
    _add_oc(env, slug="growth-engine-ratio", title="The Growth Engine Ratio",
            tag_label="Framework", teaser="A metric.", body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/growth-engine-ratio").text
    assert ('<meta property="og:image" content='
            '"https://bmweis.com/static/og/growth-engine-ratio.png">') in html


def test_og_image_slugs_never_includes_default(env, og_dir):
    """default.png is the fallback image, not itself a per-page card — a
    page literally slugged "default" (which can't happen in practice, since
    every slug is admin-chosen text, but the mechanism shouldn't rely on
    that) must not accidentally "match" the fallback file."""
    _write_png(og_dir / "default.png")
    assert "default" not in env._og_image_slugs()


def test_og_image_slug_cache_is_computed_once_and_reused(env, og_dir):
    slugs_before = env._og_image_slugs()
    assert slugs_before == frozenset()
    _write_png(og_dir / "late-arrival.png")
    # Cache already populated (to an empty set) above — a file added after
    # the first call is NOT picked up without an explicit reset, by design.
    slugs_after = env._og_image_slugs()
    assert slugs_after == frozenset()
    env._OG_IMAGE_SLUGS = None
    assert env._og_image_slugs() == frozenset({"late-arrival"})


# -- /static/og/{filename} route --------------------------------------------

def test_static_og_route_serves_a_real_file(env, og_dir):
    _write_png(og_dir / "default.png")
    r = _client(env).get("/static/og/default.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_static_og_route_404s_for_a_missing_file(env, og_dir):
    r = _client(env).get("/static/og/nonexistent.png")
    assert r.status_code == 404


def test_static_og_route_traversal_guard(env, og_dir):
    """Same os.path.basename() guard as /static/{filename} — a path
    component in the filename segment can't escape _OG_DIR."""
    outside = pathlib.Path(og_dir).parent / "secret.txt"
    outside.write_text("nope")
    r = _client(env).get("/static/og/..%2Fsecret.txt")
    assert r.status_code in (404, 400)


def test_flat_static_route_does_not_serve_from_og_dir(env, og_dir):
    """The real Phase 0 finding: /static/{filename} is a flat, single-
    segment route over _STATIC_DIR — a file that only exists under the
    separate _OG_DIR is invisible to it, confirming the two directories
    are genuinely distinct rather than one silently falling through to
    the other."""
    _write_png(og_dir / "only-in-og-dir.png")
    r = _client(env).get("/static/only-in-og-dir.png")
    assert r.status_code == 404


# -- /admin/brand download section ------------------------------------------

def test_admin_brand_lists_committed_og_images_for_download(env, og_dir):
    _write_png(og_dir / "default.png")
    _write_png(og_dir / "growth-engine-ratio.png")
    c = _admin_client(env)
    html = c.get("/admin/brand").text
    assert "Social share cards" in html
    assert '<a href="/static/og/default.png" download' in html
    assert '<a href="/static/og/growth-engine-ratio.png" download' in html
    # default.png sorts first regardless of alphabetical order among the rest
    assert html.index("default.png") < html.index("growth-engine-ratio.png")


def test_admin_brand_shows_empty_state_with_no_images(env, og_dir):
    c = _admin_client(env)
    html = c.get("/admin/brand").text
    assert "Social share cards" in html
    assert "None committed yet." in html


def test_admin_brand_requires_auth(env, og_dir):
    r = _client(env).get("/admin/brand", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


# -- Mechanical checks stay clean --------------------------------------------

def test_typography_and_brand_checks_pass_on_new_code(env):
    from linklib import brand_check, voice_review
    src = pathlib.Path(env.__file__).read_text(encoding="utf-8")
    assert not brand_check.findings(src)
    assert not voice_review.typography_findings(src)
    assert not brand_check.outbound_link_problems(src)


# -- The real committed images (webapp/static/og/, landed via a separate --
# -- commit before this branch was rebased onto it) -----------------------

def test_the_four_real_committed_cards_exist_and_serve(env):
    """Not a temp-dir fixture — the actual files Brian committed directly
    to webapp/static/og/ (default.png, growth-engine-ratio.png,
    ai-hackathon-playbook.png, netsuite-mcp.png). If this ever fails, the
    directory's real contents changed — that's exactly what this test
    exists to catch."""
    real_dir = pathlib.Path(env._OG_DIR)
    expected = {"default", "growth-engine-ratio", "ai-hackathon-playbook", "netsuite-mcp"}
    on_disk = {p.stem for p in real_dir.glob("*.png")}
    assert on_disk == expected, on_disk
    c = _client(env)
    for slug in expected:
        r = c.get(f"/static/og/{slug}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


def test_growth_engine_ratio_slug_resolves_to_its_real_committed_card(env):
    """End-to-end, no temp-dir override: the real webapp/static/og/ directory
    plus a real original_content row for growth-engine-ratio must produce
    the matching card URL — the actual promotion-deadline page this whole
    feature exists for."""
    _add_oc(env, slug="growth-engine-ratio", title="The Growth Engine Ratio",
            tag_label="Framework",
            teaser="A metric for how R&amp;D and GTM investments work together to "
                   "drive growth&mdash;with an interactive calculator.",
            body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/growth-engine-ratio").text
    assert ('<meta property="og:image" content='
            '"https://bmweis.com/static/og/growth-engine-ratio.png">') in html
