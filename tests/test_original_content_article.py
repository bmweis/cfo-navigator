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
    article-atlantic, .tool-prose, and the &larr; Thought Leadership
    back-link — per the Phase 0 investigation's shell findings."""
    _add(env, slug="md-shell", title="Shell Check", body_md="Body.", status="live")
    html = _client(env).get("/thought-leadership/md-shell").text
    assert 'class="page page-full article-atlantic"' in html
    assert '<div class="tool-prose">' in html
    assert '<a href="/thought-leadership" style="font-size:13px;color:var(--muted);">&larr; Thought Leadership</a>' in html


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

def test_bespoke_literal_routes_win_over_catch_all_even_on_slug_collision(env):
    """The three literal /thought-leadership/* routes are registered before
    this catch-all, so they always win by FastAPI's registration order —
    proven here by inserting an original_content row with a colliding slug
    and a real body_md, and confirming the bespoke page's own content wins,
    not the DB row's."""
    _add(env, slug="growth-engine-ratio", title="Fake GER Impostor",
         body_md="# This should never render", status="live")
    r = _client(env).get("/thought-leadership/growth-engine-ratio")
    assert r.status_code == 200
    assert "The Growth Engine Ratio" in r.text
    assert "Fake GER Impostor" not in r.text
    assert "This should never render" not in r.text


def test_bespoke_pages_unaffected_by_new_catch_all(env):
    """Spot-check the one remaining bespoke page still loads exactly as
    before — the new catch-all must not intercept or otherwise change
    it. netsuite-mcp (Phase 4a) and ai-hackathon-playbook (Phase 4b) are
    no longer bespoke — both routes were retired, and their slugs now go
    through the catch-all like any other original_content row — covered
    separately below."""
    c = _client(env)
    ger = c.get("/thought-leadership/growth-engine-ratio")
    assert ger.status_code == 200
    assert "The Growth Engine Ratio" in ger.text


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
