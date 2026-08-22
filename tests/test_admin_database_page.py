"""Phase I follow-up: /admin/system/database's Mermaid pan/zoom +
search-and-highlight, removal of the duplicate Cost & Spend section (that
data lives on /admin/overhead-spend), and the Content volume table's
collapsible section grouping.
"""
import pathlib
import sys
import tempfile
import os

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


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


# --- Part 2: Cost & Spend section fully removed, not just hidden -----------

def test_cost_and_spend_section_is_gone(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert "Cost &amp; spend" not in body
    assert "Cost & spend" not in body
    assert "Total spend" not in body


def test_links_to_overhead_spend_for_dollar_totals(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert '/admin/overhead-spend' in body


def test_enrichment_cost_and_manual_overhead_still_listed_as_plain_tables(env):
    """They keep appearing in the regular table listing (with row counts),
    just without the special dollar-total treatment."""
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert "enrichment_cost" in body
    assert "manual_overhead" in body


# --- Part 3: Content volume grouped into collapsible sections --------------

def test_grouped_table_sections_covers_every_schema_table_exactly_once(env):
    schema = env._db_schema_snapshot()
    sections = env._grouped_table_sections(schema)
    seen = []
    for _gname, names in sections:
        seen.extend(names)
    assert sorted(seen) == sorted(schema.keys())
    assert len(seen) == len(set(seen)), "a table appeared in more than one section"


def test_database_page_renders_a_collapsible_section_per_group(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    schema = env._db_schema_snapshot()
    nonempty_groups = [(g, names) for g, names in env._grouped_table_sections(schema) if names]
    assert body.count('class="admin-group"') == len(nonempty_groups)
    for gname, _names in nonempty_groups:
        assert env._esc(gname) in body


def test_no_leftover_other_bucket_for_known_schema():
    """Every real table in linklib/db.py's CREATE TABLE statements should be
    explicitly placed in _TABLE_GROUPS — an "Other" bucket showing up means
    the grouping map fell behind a schema change."""
    import webapp.app as appmod
    db = tempfile.mktemp(suffix=".db")
    os.environ["LINKLIB_DB"] = db
    from linklib.db import Library
    lib = Library(db)
    lib.close()
    schema = appmod._db_schema_snapshot()
    groups = dict(appmod._grouped_table_sections(schema))
    assert "Other" not in groups, f"Ungrouped tables: {groups.get('Other')}"
    os.remove(db)


# --- Part 1: real pan/zoom + search-and-highlight ---------------------------

def test_svg_pan_zoom_library_is_loaded(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert "svg-pan-zoom" in body
    assert "svgPanZoom(" in body


def test_search_box_present_with_table_names(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert '<input type="text" class="diagram-lightbox-search"' in body
    assert "searchDiagramTable(" in body
    # data-tables carries the JSON table-name list, HTML-attribute-escaped —
    # a real table name should show up somewhere inside that attribute.
    assert "data-tables=" in body
    assert "articles" in body


def test_zoom_controls_present(env):
    c = _admin_client(env)
    body = c.get("/admin/system/database").text
    assert "diagramZoomIn(" in body
    assert "diagramZoomOut(" in body
    assert "diagramResetView(" in body
    assert "Fit to screen" in body


def test_how_fpa_buddy_works_gets_pan_zoom_but_no_table_search(env):
    """The shared lightbox helper is also used by the flowchart page — it
    should get pan/zoom (generic to any Mermaid SVG) but not the table
    search box, since a flowchart has no erDiagram entity nodes to find."""
    c = _admin_client(env)
    body = c.get("/tools/fpa-buddy/how-it-works").text
    assert "svg-pan-zoom" in body
    assert '<input type="text" class="diagram-lightbox-search"' not in body
