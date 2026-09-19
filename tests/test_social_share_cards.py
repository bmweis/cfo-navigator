"""Social share cards, Phase 1 (2026-09) — Open Graph / Twitter Card metadata
emitted from _page(), plus the committed webapp/static/og/ image directory,
its serving route, and the /admin/brand download list.

Phase 2 (image generation) was investigated and killed outright — see
CLAUDE.md's "Social share cards" bullet for the reasoning. This file covers
Phase 1 only: metadata emission, the single-rule escaping helper
(_esc_attr_normalize — html.unescape() then _esc(), correct for both
original_content.teaser's pre-encoded convention and ai_surfaces.teaser's/
homepage_teaser's plain-text convention), and the filename-existence-check
mechanism for og:image.
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


# -- Escaping: one shared rule, evaluated against two storage conventions --
#
# original_content.teaser is stored PRE-ENCODED (real HTML entities already
# in the string, e.g. "R&amp;D"/"&mdash;"); ai_surfaces.teaser and
# homepage_teaser are stored as plain text. _esc_attr_normalize() —
# html.unescape() then _esc() — is one shared rule that produces correct
# output for both conventions, replacing an earlier two-helper design (one
# helper per convention) after evaluating this single-rule alternative and
# finding it not just simpler but harder to get wrong — see its own
# docstring and test_esc_attr_normalize_handles_inconsistent_admin_input
# below for the concrete gap the two-helper version had.

def test_original_content_teaser_is_not_double_escaped(env):
    """A pre-encoded &amp; must not become &amp;amp; — this is the exact
    production teaser for growth-engine-ratio. html.unescape() first
    decodes it back to a raw "&", then _esc() re-encodes it exactly once."""
    _add_oc(env, slug="ger", title="The Growth Engine Ratio", tag_label="Framework",
            teaser="A metric for how R&amp;D and GTM investments work together to "
                   "drive growth&mdash;with an interactive calculator.",
            body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/ger").text
    desc_tag = [l for l in html.split("\n") if 'property="og:description"' in l][0]
    assert "R&amp;D" in desc_tag
    assert "R&amp;amp;D" not in desc_tag
    assert "&amp;amp;" not in desc_tag
    # A decoded-and-not-re-escaped entity like &mdash; (not one of _esc()'s
    # four dangerous characters) renders as its literal Unicode character —
    # harmless in a UTF-8 attribute, and expected under this design.
    assert "—with an interactive calculator" in desc_tag


def test_original_content_teaser_quote_is_escaped(env):
    """A literal double quote in an already-pre-encoded teaser would
    otherwise break out of the content="..." attribute."""
    _add_oc(env, slug="quoted", title="Quoted Piece", tag_label="Framework",
            teaser='A piece about the "real" numbers.',
            body_md="# hi", status="live")
    html = _client(env).get("/thought-leadership/quoted").text
    assert 'content="A piece about the &quot;real&quot; numbers.">' in html


def test_ai_surface_teaser_is_escaped_normally(env):
    """ai_surfaces.teaser is stored as PLAIN text (no pre-encoding) — the
    opposite convention from original_content.teaser. A literal ampersand
    here must still end up escaped exactly once."""
    _add_ai_surface(env, slug="fp-a-buddy", title="FP&A Buddy",
                     teaser="Answers questions from my archive & the web.",
                     body_md="Body.", status="live")
    html = _client(env).get("/how-this-is-built/fp-a-buddy").text
    desc_tag = [l for l in html.split("\n") if 'property="og:description"' in l][0]
    assert "archive &amp; the web" in desc_tag
    assert "archive & the web" not in desc_tag


def test_esc_attr_normalize_handles_both_conventions(env):
    helper = env._esc_attr_normalize
    assert helper("R&amp;D done") == "R&amp;D done"  # pre-encoded, stays single-encoded
    assert helper("R&D done") == "R&amp;D done"       # plain, gets encoded
    assert helper('a "quoted" phrase') == "a &quot;quoted&quot; phrase"
    assert helper("") == ""
    # Matches _esc()'s own established str(s)-or-"" pattern exactly (same
    # module, same convention) — str(None) is the truthy string "None", so
    # this isn't a None-safety guarantee, deliberately consistent with the
    # existing helper rather than diverging from it.
    assert helper(None) == "None"


def test_esc_attr_normalize_handles_inconsistent_admin_input(env):
    """The concrete robustness gap that motivated replacing the two-helper
    design: a teaser with ONE raw, un-pre-encoded ampersand mixed with one
    already-encoded one (a plausible admin typo, not a contrived case).
    The retired _esc_attr_quote_only() trusted original_content.teaser's
    "&" completely and would have shipped the raw one un-escaped — invalid
    markup. The single decode-then-re-encode rule can't have that failure
    mode: every "&" in the output is a real, correctly-escaped entity
    regardless of how it arrived."""
    helper = env._esc_attr_normalize
    mixed = "Ben & Jerry's &amp; Associates"
    result = helper(mixed)
    assert result == "Ben &amp; Jerry's &amp; Associates"
    assert "Ben & Jerry" not in result  # no raw, unescaped ampersand survives


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


# -- The real committed images (webapp/static/og/, not a temp-dir fixture) --
#
# Deliberately asserts the RELATIONSHIP between what's on disk and what the
# route serves, not a hardcoded count or filename list — a hardcoded list
# is exactly what broke here originally: chart-of-accounts.png was
# committed after this test was written, and the test kept expecting only
# four names. Adding a fifth (or Nth) card should require zero changes to
# this test; only default.png's presence is asserted by name, since it's
# the one card every route always falls back to (see _og_image_url's own
# fallback logic) and losing it is a real regression no other assertion
# here would catch.

def test_default_og_card_exists_and_serves(env):
    real_dir = pathlib.Path(env._OG_DIR)
    assert (real_dir / "default.png").is_file()
    r = _client(env).get("/static/og/default.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_every_real_committed_og_card_serves_with_the_right_content_type(env):
    real_dir = pathlib.Path(env._OG_DIR)
    pngs = list(real_dir.glob("*.png"))
    assert pngs, "expected at least one committed card (default.png) — found none"
    c = _client(env)
    for png in pngs:
        r = c.get(f"/static/og/{png.name}")
        assert r.status_code == 200, png.name
        assert r.headers["content-type"] == "image/png", png.name


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


# -- og:url threading audit (mechanical drift guard, mirrors hub_nav_orphans) -
#
# A real, shipped gap: the original Phase 1 build only threaded request=
# through 5 hand-picked routes, leaving 44 other genuinely public _page()
# calls across 39 routes — including every individual tool/community
# profile page — silently reporting the homepage's og:url. Fixed the same
# day; this is the permanent, mechanical guard against it recurring,
# wired into webapp.checks.run_all() ("og:url threading") the same way
# hub_nav_orphans() is wired in as "Hub-nav orphans".

def test_every_public_route_threads_request_today(env):
    """The real, current state — every public route's _page() call passes
    request=request. Proves the fix, not just the detector."""
    assert env.og_url_threading_problems() == []


def test_detector_catches_a_missing_request_kwarg():
    """Unit-tests the pure detection helper (_og_url_call_missing_request)
    directly against synthetic source snippets — including the two false-
    positive shapes the detector's own first draft actually hit and had to
    fix (a route-function name ending in "_page", and a comment mentioning
    _page()) — rather than mutating the real 34k-line source file on disk
    mid-test."""
    import webapp.app as appmod

    # The real bug shape: a genuine _page() call with no request=request.
    missing = '''
def tools_landing(request: Request):
    body = "<div></div>"
    return HTMLResponse(_page("CFO Toolbox—Brian Weisberg", "CFO Toolbox", body, role=_role(request)))
'''
    assert appmod._og_url_call_missing_request(missing) is True

    # Fixed shape: request=request present.
    fixed = '''
def tools_landing(request: Request):
    body = "<div></div>"
    return HTMLResponse(_page("CFO Toolbox—Brian Weisberg", "CFO Toolbox", body, role=_role(request), request=request))
'''
    assert appmod._og_url_call_missing_request(fixed) is False

    # False-positive shape 1: a function name ending in "_page" must never
    # be mistaken for a call to the _page() function itself.
    false_positive_name = '''
def login_page(request: Request, next: str = ""):
    return HTMLResponse(_page("Sign in—Brian Weisberg", "", body, request=request))
'''
    assert appmod._og_url_call_missing_request(false_positive_name) is False

    # False-positive shape 2: a comment mentioning _page() is not a call.
    false_positive_comment = '''
def some_route(request: Request):
    # threaded through _page()'s ~250 call sites, see the docstring
    return HTMLResponse(body)
'''
    assert appmod._og_url_call_missing_request(false_positive_comment) is False


def test_detector_wired_into_admin_checks():
    """The live /admin/checks entry — mirrors "Hub-nav orphans"'s own
    wiring exactly."""
    from webapp import checks
    row = [r for r in checks.run_all() if r["name"] == "og:url threading"][0]
    assert row["ok"] is True
    assert row["where"] == "Live + CI"


# -- Social share card publish gate: og_card_missing_problems() (2026-09) ----
#
# Defense in depth for the publish-time hard block (_oc_publish_gate_error,
# tests/test_original_content_admin.py) — a card can still go missing AFTER
# publish (a slug change, a deleted file, or a row that predates the gate),
# so this is the mechanical check catching that, same shape as
# original_content_mirror_problems() and wired into run_all() the same way.

def test_og_card_missing_problems_flags_only_live_pieces_without_a_card(env, og_dir):
    _write_png(og_dir / "has-card.png")
    _add_oc(env, slug="missing-card", title="Missing", tag_label="Guide", teaser="t", status="live")
    _add_oc(env, slug="has-card", title="Has Card", tag_label="Guide", teaser="t", status="live")
    _add_oc(env, slug="draft-no-card", title="Draft Missing", tag_label="Guide", teaser="t", status="draft")

    problems = env.og_card_missing_problems()
    assert len(problems) == 1
    assert "missing-card" in problems[0]


def test_og_card_missing_problems_clean_when_every_live_piece_has_a_card(env, og_dir):
    _write_png(og_dir / "covered.png")
    _add_oc(env, slug="covered", title="Covered", tag_label="Guide", teaser="t", status="live")
    assert env.og_card_missing_problems() == []


def test_og_card_missing_problems_wired_into_admin_checks(env, og_dir):
    """Mirrors test_detector_wired_into_admin_checks's own og:url pattern —
    reused here for a check on a different concept, not a new mechanism."""
    _write_png(og_dir / "missing-check-card.png")
    _add_oc(env, slug="uncarded-for-check-test", title="Uncarded", tag_label="Guide",
            teaser="t", status="live")
    from webapp import checks
    row = [r for r in checks.run_all() if r["name"] == "Every live piece has a share card"][0]
    assert row["ok"] is False
    assert row["where"] == "Live + CI"
    assert "uncarded-for-check-test" in row["detail"]
