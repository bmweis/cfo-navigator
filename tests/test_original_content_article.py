"""Original Content Phase 2 — markdown rendering + the shared article
template at GET /thought-leadership/{slug}.
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
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _add(appmod, **kwargs):
    lib = appmod._lib()
    try:
        return lib.add_original_content(**kwargs)
    finally:
        lib.close()


SAMPLE_MD = (
    "# Heading One\n\n"
    "Some **bold** text and a [link](https://example.com).\n\n"
    "## Sub heading\n\n"
    "- item one\n- item two\n\n"
    "```python\nprint(\"hi\")\n```\n\n"
    "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
    "> A quote.\n"
)


# -- Markdown rendering -------------------------------------------------------

def test_headings_render(env):
    _add(env, slug="md-headings", title="Headings", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-headings").text
    assert '<div class="oc-body"><h1>Heading One</h1>' in html
    assert "<h2>Sub heading</h2>" in html


def test_fenced_code_block_renders(env):
    _add(env, slug="md-code", title="Code", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-code").text
    assert '<pre><code class="language-python">print(&quot;hi&quot;)' in html


def test_table_renders(env):
    _add(env, slug="md-table", title="Table", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-table").text
    assert "<table>" in html
    assert "<th>A</th>" in html
    assert "<td>1</td>" in html


def test_table_gets_overflow_wrapper_for_mobile_safety(env):
    """Same overflow-x:auto convention every other table on this site
    already uses — a wide admin-authored table shouldn't force the whole
    page to scroll horizontally on mobile."""
    _add(env, slug="md-table-wrap", title="Table Wrap", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-table-wrap").text
    assert '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;"><table>' in html


def test_multiple_tables_each_get_their_own_wrapper(env):
    two_tables_md = "| A | B |\n|---|---|\n| 1 | 2 |\n\nSome text between.\n\n| C | D |\n|---|---|\n| 3 | 4 |\n"
    _add(env, slug="md-two-tables", title="Two Tables", body_md=two_tables_md, status="live")
    html = _client(env).get("/thought-leadership/md-two-tables").text
    assert html.count('<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;"><table>') == 2


def test_blockquote_renders(env):
    _add(env, slug="md-quote", title="Quote", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-quote").text
    assert "<blockquote>\n<p>A quote.</p>\n</blockquote>" in html


def test_list_and_link_and_bold_render(env):
    _add(env, slug="md-mixed", title="Mixed", body_md=SAMPLE_MD, status="live")
    html = _client(env).get("/thought-leadership/md-mixed").text
    assert "<li>item one</li>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<strong>bold</strong>" in html


def test_raw_html_passthrough(env):
    """body_md is admin-authored only — raw HTML embedded in it is rendered
    as-is, not escaped, per the approved out-of-scope decision."""
    _add(env, slug="md-raw-html", title="Raw HTML", status="live",
         body_md="Before.\n\n<div class=\"custom-block\">A raw HTML block.</div>\n\nAfter.")
    html = _client(env).get("/thought-leadership/md-raw-html").text
    assert '<div class="custom-block">A raw HTML block.</div>' in html


def test_eyebrow_tag_label_and_date_label_render(env):
    _add(env, slug="md-meta", title="With Meta", tag_label="Essay", date_label="Mar 2027",
         body_md="Body text.", status="live")
    html = _client(env).get("/thought-leadership/md-meta").text
    assert ">Essay<" in html
    assert "By Brian Weisberg &middot; Mar 2027" in html


def test_no_date_label_omits_middot(env):
    _add(env, slug="md-nodate", title="No Date", body_md="Body text.", status="live")
    html = _client(env).get("/thought-leadership/md-nodate").text
    assert "By Brian Weisberg</p>" in html
    assert "By Brian Weisberg &middot;" not in html


def test_article_uses_shared_shell_matching_bespoke_pages(env):
    """Same shell as the three hand-built pages: page page-full
    article-atlantic, .tool-prose, and the &larr; Thought leadership
    back-link — per the Phase 0 investigation's shell findings. Sentence
    case per the 2026-08 sentence-case audit (BRAND.md §3.2) — "Thought
    Leadership" never made the named-product exception list."""
    _add(env, slug="md-shell", title="Shell Check", body_md="Body.", status="live")
    html = _client(env).get("/thought-leadership/md-shell").text
    assert 'class="page page-full article-atlantic"' in html
    assert '<div class="tool-prose">' in html
    assert '<a href="/thought-leadership" style="font-size:13px;color:var(--muted);">&larr; Thought leadership</a>' in html


# -- 404 behavior -------------------------------------------------------------

def test_unknown_slug_404s(env):
    r = _client(env).get("/thought-leadership/no-such-slug")
    assert r.status_code == 404


def test_draft_404s_for_anonymous_visitor(env):
    _add(env, slug="a-draft", title="A Draft", body_md="Draft body.", status="draft")
    r = _client(env).get("/thought-leadership/a-draft")
    assert r.status_code == 404


def test_null_body_md_row_404s(env):
    """A card-metadata-only row (body_md IS NULL) must 404 at this route —
    defense in depth even though the three bespoke literal routes are what
    actually keeps this from ever being reached for them in practice."""
    _add(env, slug="card-only", title="Card Only", body_md=None, status="live")
    r = _client(env).get("/thought-leadership/card-only")
    assert r.status_code == 404


# -- Draft visibility for admin -----------------------------------------------

def test_draft_visible_to_authenticated_admin_at_canonical_url(env):
    _add(env, slug="admin-draft", title="Admin Draft", body_md="Secret draft body.", status="draft")
    r = _admin_client(env).get("/thought-leadership/admin-draft")
    assert r.status_code == 200
    assert "Secret draft body." in r.text


def test_draft_not_visible_to_signed_out_visitor_even_with_correct_slug(env):
    _add(env, slug="admin-draft-2", title="Admin Draft 2", body_md="Another secret.", status="draft")
    r = _client(env).get("/thought-leadership/admin-draft-2")
    assert r.status_code == 404


# -- Route precedence ----------------------------------------------------------
#
# All three of the original bespoke /thought-leadership/* pieces
# (netsuite-mcp, ai-hackathon-playbook, growth-engine-ratio) have now been
# retired — see test_netsuite_mcp_now_served_by_catch_all,
# test_ai_hackathon_playbook_now_served_by_catch_all, and
# test_growth_engine_ratio_now_served_by_catch_all below. There is no
# longer a literal-route-wins-over-catch-all scenario to prove for any
# /thought-leadership/* piece — the "bespoke literal routes win by
# registration order" test that used to live here (against
# growth-engine-ratio, the last one still bespoke) has no subject left and
# was removed rather than retargeted at a nonexistent bespoke page.


def test_netsuite_mcp_now_served_by_catch_all(env):
    """Original Content Phase 4a — netsuite-mcp's bespoke route was
    retired; it's now an ordinary original_content row (still reserved
    from admin editing as a slug no more — see
    test_original_content_admin.py's slug-collision tests), reachable
    through the same catch-all as any brand-new piece."""
    _add(env, slug="netsuite-mcp", title="Connecting Claude to NetSuite",
         body_md="Ported content.", status="live")
    r = _client(env).get("/thought-leadership/netsuite-mcp")
    assert r.status_code == 200
    assert "Connecting Claude to NetSuite" in r.text
    assert "Ported content." in r.text


def test_ai_hackathon_playbook_now_served_by_catch_all(env):
    """Original Content Phase 4b — ai-hackathon-playbook's bespoke route
    was retired the same way netsuite-mcp's was in Phase 4a; it's now an
    ordinary original_content row, reachable through the catch-all."""
    _add(env, slug="ai-hackathon-playbook", title="Sail, Don't Row",
         body_md="Ported hackathon content.", status="live")
    r = _client(env).get("/thought-leadership/ai-hackathon-playbook")
    assert r.status_code == 200
    assert "Sail, Don't Row" in r.text
    assert "Ported hackathon content." in r.text


def test_growth_engine_ratio_now_served_by_catch_all(env):
    """Original Content Phase 4c — growth-engine-ratio's bespoke route was
    retired the same way netsuite-mcp's (4a) and ai-hackathon-playbook's
    (4b) were; it's now an ordinary original_content row, reachable through
    the catch-all. Unlike those two synthetic-content tests, this one runs
    the real migrate_growth_engine_ratio_content.py BODY_MD through the
    template — a light integration check that the actual ported content
    (not just a placeholder string) renders without error and carries the
    split-page pieces this phase specifically produced: the CTA linking out
    to the new standalone calculator page, and the tier table's page-
    specific CSS classes."""
    from scripts.migrate_growth_engine_ratio_content import BODY_MD
    _add(env, slug="growth-engine-ratio", title="The Growth Engine Ratio",
         tag_label="Framework", date_label="June 2026", body_md=BODY_MD, status="live")
    r = _client(env).get("/thought-leadership/growth-engine-ratio")
    assert r.status_code == 200
    assert "The Growth Engine Ratio" in r.text
    assert "By Brian Weisberg &middot; June 2026" in r.text
    assert 'href="/thought-leadership/growth-engine-calculator"' in r.text
    assert "Try the Growth Engine Ratio calculator" in r.text
    assert "ger-table" in r.text
    assert "ger-pull" in r.text
    # The calculator's own JS/inputs must NOT be on the article page — that
    # content moved entirely to the new standalone calculator route.
    assert "function calcGER" not in r.text
    assert 'id="rev_n"' not in r.text


def test_growth_engine_ratio_table_header_and_cta_button_styled_correctly(env):
    """Post-merge regression, found live on mobile Safari (and, it turned
    out, present at every viewport width — see the CSS comments this fix
    added): two real rendering bugs, both invisible text, both fixed here.

    (1) The tier table's <thead><tr style="background:var(--navy)"> inline
    style was silently covered by _OC_ARTICLE_CSS's generic
    `.oc-body th{background:var(--accent-light)}` rule — not a specificity
    loss, a CSS table background PAINTING LAYER order fact (a cell's own
    background always paints above its row's, regardless of specificity).
    White header text on a near-white cell background read as
    "near-invisible, with an unexplained gap of white space above it" — one
    bug, not two. Fixed by giving `.ger-table th` its own explicit
    background/color, the same pattern already used for `.ns-table th`.

    (2) `.article-cta`'s "Download the full guide" <a class="btn"> lost its
    white text to `.oc-body a{color:var(--navy)}`, which has *higher*
    specificity (one class + one tag) than `.btn`'s own `color:#fff` rule
    (one class alone) — navy text on a navy background. Fixed with
    `.oc-body .btn{color:#fff}` (two classes beats one class + one tag by
    CSS's class-count-first specificity comparison), a permanent fix for
    any future body_md piece using this same sitewide button, not just this
    one — this was the first body_md content anywhere to use it."""
    from scripts.migrate_growth_engine_ratio_content import BODY_MD
    _add(env, slug="growth-engine-ratio-styling", title="The Growth Engine Ratio",
         tag_label="Framework", date_label="June 2026", body_md=BODY_MD, status="live")
    r = _client(env).get("/thought-leadership/growth-engine-ratio-styling")
    assert r.status_code == 200
    assert ".oc-body .ger-table th{background:var(--navy);color:#fff;}" in r.text
    assert ".oc-body .btn{color:#fff;}" in r.text


def test_growth_engine_ratio_table_wrap_has_no_visible_gap(env):
    """Second post-merge regression, found live: the tier table's wrapper
    (.ger-table-wrap, styled with its own white background/border/radius
    matching the retired bespoke page) has zero padding, meant to fit the
    table flush against its edges — but _OC_ARTICLE_CSS's generic
    `.oc-body table{margin:1.5em 0}` rule (written for markdown-generated
    tables with no wrapper of their own) still applied to `.ger-table`,
    since its own CSS never reset `margin`. The table sat 21px inset from
    the wrapper's border on all sides, reading as an unintentional blank
    box rather than a design choice — confirmed by measuring the actual
    gap (21px, matching the table's computed margin) live in a browser,
    not guessed at. `.ns-table`'s wrapper never showed this because it has
    no background/border of its own to reveal the same inherited margin
    against — the gap is identically present there too, just invisible.
    Fixed by resetting `.ger-table`'s own margin to 0, so the wrapper (which
    already carries the correct outer spacing via its own inline
    `margin:0 0 32px`) is the single source of the box's outer edge."""
    from scripts.migrate_growth_engine_ratio_content import BODY_MD
    _add(env, slug="growth-engine-ratio-table-gap", title="The Growth Engine Ratio",
         tag_label="Framework", date_label="June 2026", body_md=BODY_MD, status="live")
    r = _client(env).get("/thought-leadership/growth-engine-ratio-table-gap")
    assert r.status_code == 200
    assert ".oc-body .ger-table{margin:0;}" in r.text


def test_growth_engine_calculator_page_loads(env):
    """Original Content Phase 4c — the new standalone bespoke route at its
    own URL, independent of any original_content row. Confirms the
    extracted calculator markup/JS is present and functioning — both modes'
    UI, the chart-generation functions, and the back-link to the article."""
    r = _client(env).get("/thought-leadership/growth-engine-calculator")
    assert r.status_code == 200
    assert "Growth Engine Ratio calculator" in r.text
    assert '<a href="/thought-leadership/growth-engine-ratio"' in r.text
    assert 'id="tab-point"' in r.text
    assert 'id="tab-timeline"' in r.text
    assert "function calcGER" in r.text
    assert "function calcTimeline" in r.text
    assert "function contributionSVG" in r.text
    assert "function buildChart" in r.text
