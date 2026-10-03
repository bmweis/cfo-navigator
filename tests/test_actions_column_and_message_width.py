"""The Actions heading rule and the contact Message column width (Refs 655).

Two things measured before the fix on the contact submissions table (real
Chromium, both viewports): Message got 109px of an 800px table because the
width-hinted Date, Name and Email columns took the minimum first, and the
last column, which holds the Delete button, had an empty header.

Rule: any table whose last column holds row buttons heads it "Actions".
Measured engine: Chromium only (the geometry tests skip where none exists)."""
import importlib
import os
import re
import tempfile
from pathlib import Path

import pytest

from linklib import brand_check

SRC = Path(__file__).resolve().parent.parent / "webapp" / "app.py"


# --- the source scan ---------------------------------------------------------

def _problems(table_html: str) -> list[str]:
    return brand_check.actions_header_problems(f'x = """{table_html}"""\n')


def test_empty_header_is_flagged():
    assert _problems("<table><thead><tr><th>Name</th><th></th></tr></thead><tbody></tbody></table>")


def test_trailing_buttons_under_another_heading_is_flagged():
    html = ('<table><thead><tr><th>Name</th><th>Tools</th></tr></thead><tbody>'
            '<tr><td>a</td><td><button>Delete</button></td></tr></tbody></table>')
    assert _problems(html)


def test_actions_heading_passes():
    html = ('<table><thead><tr><th>Name</th><th>Actions</th></tr></thead><tbody>'
            '<tr><td>a</td><td><button>Delete</button></td></tr></tbody></table>')
    assert _problems(html) == []


def test_the_shared_helper_counts_as_actions():
    html = ('<table><thead><tr><th>Name</th>{_actions_th("padding:8px;")}</tr></thead><tbody>'
            '<tr><td>a</td><td><button>Delete</button></td></tr></tbody></table>')
    assert _problems(html) == []


def test_a_checkbox_or_aria_labelled_header_is_not_empty():
    html = ('<table><thead><tr><th><input type="checkbox"></th>'
            '<th aria-label="Select"></th><th>Name</th></tr></thead><tbody></tbody></table>')
    assert _problems(html) == []


def test_real_source_has_no_blank_or_misnamed_actions_header():
    assert brand_check.actions_header_problems(SRC.read_text()) == []


# --- rendered pages ----------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    lib = appmod.Library(db)
    long = "A long free text value that wraps across many lines on a phone, like a real message. " * 2
    lib.save_contact("Pat Lee", "pat@example.com", long)
    lib.log_email_failure("contact-form", long)
    lib.add_compare_summary_feedback("software", "1,2", "h", long, long)
    lib.add_tool_category("Payroll")
    lib.close()
    yield client, db
    if os.path.exists(db):
        os.remove(db)


def _headers(html: str, table_index: int = 0) -> list[str]:
    table = re.findall(r"<table\b.*?</table>", html, re.S)[table_index]
    head = re.search(r"<thead.*?</thead>", table, re.S).group(0)
    return [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th\b[^>]*>.*?</th>", head, re.S)]


@pytest.mark.parametrize("route", [
    "/admin/inbox/contact-submissions",
    "/admin/inbox/email-failures",
    "/admin/compare-summary-feedback",
    "/admin/tools/software/categories",
])
def test_last_header_is_actions(env, route):
    client, _ = env
    assert _headers(client.get(route).text)[-1] == "Actions"


def test_software_categories_splits_the_count_from_the_buttons(env):
    client, _ = env
    html = client.get("/admin/tools/software/categories").text
    assert _headers(html) == ["Name", "Description", "Tools", "Actions"]
    row = re.findall(r"<tr\b.*?</tr>", re.findall(r"<tbody>.*?</tbody>", html, re.S)[0], re.S)[1]
    cells = re.findall(r"<td\b.*?</td>", row, re.S)
    assert len(cells) == 4
    assert "tool" in cells[2] and "<button" not in cells[2]
    assert "Save" in cells[3] and "Delete" in cells[3]


# --- geometry ----------------------------------------------------------------

def _message_width(client, tmp_path, width):
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception:
        pytest.skip("Chromium not installed in this environment")
    try:
        f = tmp_path / "t.html"
        f.write_text(client.get("/admin/inbox/contact-submissions").text, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        page.wait_for_timeout(150)
        return page.evaluate("""() => {
            const ths = [...document.querySelectorAll('.table-frame table thead th')];
            const th = ths.find(t => t.textContent.trim() === 'Message');
            return th.getBoundingClientRect().width;
        }""")
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("width", [390, 874])
def test_message_column_is_at_least_its_named_floor(env, tmp_path, width):
    client, _ = env
    import webapp.app as appmod
    assert _message_width(client, tmp_path, width) >= appmod._COL_WIDTH_MESSAGE - 1
