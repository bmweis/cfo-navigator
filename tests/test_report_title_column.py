"""Brian prefers sideways scrolling to wrapped titles: the Report column on
/admin/reader/figures and the Title column on the digests preview use one
named width and never wrap. Invented titles only."""
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
    c, appmod = client
    assert appmod._COL_WIDTH_REPORT_TITLE == 420
    html = c.get("/admin/reader/figures").text
    th = re.search(r"<th[^>]*>Report</th>", html).group(0)
    assert f"min-width:{appmod._COL_WIDTH_REPORT_TITLE}px" in th


def test_figures_report_title_cell_does_not_wrap(client):
    c, _ = client
    assert "white-space:nowrap" in _td(c.get("/admin/reader/figures").text, T90)


def test_the_other_figures_columns_do_not_wrap_either(client):
    c, _ = client
    html = c.get("/admin/reader/figures").text
    row = re.search(r"<tbody><tr>(.*?)</tr></tbody>", html, re.S).group(1)
    cells = re.findall(r"<td([^>]*)>", row)
    # Eight cells now; the link and republished-from cells may wrap (a long address, a source name),
    # every short-token cell and the title still may not.
    assert len(cells) == 8
    assert all("white-space:nowrap" in c_ for c_ in cells if not re.search(r'data-label="(Landing URL|Republished from)"', c_))


def test_digests_preview_title_cell_does_not_wrap(client):
    c, appmod = client
    bundle = (f"TITLE: {T90}\nURL: https://reports.example.test/x?digest=x\nSOURCE: Example Benchmarks\n"
              "PUBLISHED: 2026\nSUMMARY: s\nCONTENT:\n" + "body text. " * 40 + "\n")
    html = c.post("/admin/reader/digests/preview", data={"bundle": bundle}).text
    th = re.search(r"<th[^>]*>Title</th>", html).group(0)
    assert f"min-width:{appmod._COL_WIDTH_REPORT_TITLE}px" in th
    td = re.search(r"<td[^>]*>(?=<strong>)", html).group(0)
    assert "white-space:nowrap" in td
