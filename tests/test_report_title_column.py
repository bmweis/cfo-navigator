"""Brian prefers sideways scrolling to wrapped titles: the Title column on the digests preview uses one named
width and never wraps. The reports table on /admin/reader/figures pins its Report column instead (sticky, like the
vendors table), so a title wraps inside it. Invented titles only."""
import importlib
import re

import pytest
from fastapi.testclient import TestClient

from linklib.db import Library
from tests import figures_fixtures as fx

T90 = "The 2026 annual budgeting and planning benchmark report for growth-stage software company CFOs"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "pw")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "s.opml"))
    import webapp.app as appmod
    importlib.reload(appmod)
    db = Library(str(tmp_path / "t.db"))
    db.create_user("admin", "pw", role="admin")
    fx.load_report(db, "annual-planning-2026", [fx.frow(report=T90, year="2026")])
    db.close()
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "pw"})
    return c, appmod


def _td(html: str, title: str) -> str:
    return re.search(r"<td[^>]*>(?:<strong>)?[^<]*" + re.escape(title) + r".*?</td>", html).group(0)


def test_report_title_width_is_one_named_constant(client):
    """_COL_WIDTH_REPORT_TITLE sizes the digests preview's Title column; the reports table now pins its own Report
    column (_COL_WIDTH_REPORT_STICKY, below)."""
    c, appmod = client
    assert appmod._COL_WIDTH_REPORT_TITLE == 420


def test_figures_report_title_wraps_inside_its_sticky_column(client):
    """The reports table pins the Report column (the vendors-table pattern), so a long title wraps inside that column
    instead of pushing the page sideways. Deliberate exception to "report titles never wrap" (PR 738), which still
    holds for the digests preview below."""
    c, appmod = client
    html = c.get("/admin/reader/figures").text
    td = re.search(r'<td class="admin-sticky-col" data-label="Report"[^>]*>', html).group(0)
    assert "white-space:nowrap" not in td
    assert f"width:{appmod._COL_WIDTH_REPORT_STICKY}px" in html and T90 in html


def test_the_other_figures_columns_do_not_wrap_either(client):
    c, _ = client
    html = c.get("/admin/reader/figures").text
    row = re.search(r'<tbody id="figures-approved-tbody"><tr[^>]*>(.*?)</tr>', html, re.S).group(1)
    cells = re.findall(r'<td([^>]*)>', row)
    # Nine cells: the title (sticky, wraps), the source, series and republished-from cells (a publisher name or a
    # series name can be long, so they may wrap) and every short-token cell, which may not.
    assert len(cells) == 9
    may_wrap = r'class="admin-sticky-col"|data-label="(Source|Series|Republished from)"'
    assert all("white-space:nowrap" in c_ for c_ in cells if not re.search(may_wrap, c_))


def test_digests_preview_title_cell_does_not_wrap(client):
    c, appmod = client
    bundle = (f"TITLE: {T90}\nURL: https://reports.example.test/x?digest=x\nSOURCE: Example Benchmarks\n"
              "PUBLISHED: 2026\nSUMMARY: s\nCONTENT:\n" + "body text. " * 40 + "\n")
    html = c.post("/admin/reader/digests/preview", data={"bundle": bundle}).text
    th = re.search(r"<th[^>]*>Title</th>", html).group(0)
    assert f"min-width:{appmod._COL_WIDTH_REPORT_TITLE}px" in th
    td = re.search(r"<td[^>]*>(?=<strong>)", html).group(0)
    assert "white-space:nowrap" in td
