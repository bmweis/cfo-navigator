"""One table format for every table on the site (2026-09).

Brian's rule: light-blue header row, white rows, a line between rows, and a
rounded navy-light border. Tables with subheading rows (the Compare pages'
section bands) use the secondary format: navy-light bands with white text,
and a white label column. It all lives in ONE CSS block in _CSS scoped to
main.site-main, so no table has to remember it; these tests pin that block
and check real pages render inside its scope.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library

T = ".site-main table:not(.tp-competitor-table):not(.rr-reader-body table)"


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.add_tool_category("FP&A", "Planning")
    lib.add_community_category("Peer groups", "desc")
    ids = []
    for n in ("Abacum", "Runway"):
        tid = lib.add_tool(n, "A planning tool.", f"https://{n.lower()}.example", ["FP&A"])
        lib.approve_tool(tid)
        ids.append(tid)
    lib.add_community("Comm", "https://c.example", "CFOs", "Free", ["Peer groups"], approved=1)
    lib.add_benchmark("Bench", "https://b.example", "desc")
    lib.add_thought_leadership("writing", "TL", url="https://tl.example", date_label="Jun 2026")
    lib.add_original_content("oc", "OC", teaser="t", tag_label="Guide", link_label="Read the guide")
    lib.add_ai_surface("ai", "AI", teaser="t")
    lib.add_voice_review_item("tools", ids[0], "description", "buzzword", "seamless")
    lib.close()
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    yield appmod, c, ids
    if os.path.exists(db):
        os.remove(db)


def _css_block(css: str) -> str:
    start = css.index(f"{T}{{border-collapse")
    return css[start:css.index("@media(max-width:700px)", start)]


def test_border_token_is_navy_light():
    from webapp.app import _CSS
    assert "--table-border:var(--navy-light);" in _CSS


def test_the_one_table_format_lives_in_shared_css():
    from webapp.app import _CSS
    block = _css_block(_CSS)
    assert "border:1px solid var(--table-border)!important" in block
    assert "border-radius:12px!important" in block
    assert "background:var(--accent-light)!important" in block  # header row
    assert f"{T} tr{{background:var(--surface);}}" in block  # white rows
    assert f"{T} td{{border-top:1px solid var(--line)!important" in block  # row lines
    # Sticky-column tables carry the frame on their scroll wrapper instead.
    assert ".site-main .table-frame{" in block


def test_secondary_format_for_tables_with_subheadings():
    from webapp.app import _CSS, _CMP_SHARED_CSS
    block = _css_block(_CSS)
    assert f"{T} td.cc-section{{background:var(--table-border)!important;color:#fff!important;}}" in block
    assert f"{T}>tbody td.cc-label{{background:var(--surface)!important;}}" in block
    assert "color:#fff;\n  background:var(--table-border);" in _CMP_SHARED_CSS
    assert "seafoam);padding:8px 16px" not in _CMP_SHARED_CSS


def test_every_page_renders_tables_inside_the_scope(env):
    """Every no-parameter page with a table, admin and public, renders it
    inside <main class="site-main">, the scope the format keys off."""
    appmod, c, ids = env
    paths = sorted({r.path for r in appmod.app.routes
                    if getattr(r, "methods", None) and "GET" in r.methods and "{" not in r.path
                    and not r.path.endswith((".csv", "download-db"))})
    paths.append(f"/tools/software/compare?ids={ids[0]},{ids[1]}")
    checked = 0
    for path in paths:
        r = c.get(path)
        if r.status_code != 200 or "<table" not in r.text or "<main" not in r.text:
            continue
        main = r.text[r.text.index("<main"):r.text.index("</main>")]
        assert '<main class="site-main">' in r.text, path
        assert "<table" in main, path
        checked += 1
    assert checked >= 20


def test_compare_wrappers_carry_the_frame(env):
    _, c, ids = env
    html = c.get(f"/tools/software/compare?ids={ids[0]},{ids[1]}").text
    i = html.index('id="cmp-scroll-wrap"')
    assert 'class="table-frame"' in html[i - 120:i]
    assert 'class="cc-cell cc-label' in html  # software Compare now has label rows, no bands


def test_sticky_admin_tables_frame_their_wrapper(env):
    _, c, _ = env
    html = c.get("/admin/tools/software").text
    i = html.index('id="cmp-scroll-wrap"')
    assert 'class="table-frame"' in html[i - 200:i]


# --- The live guard (/admin/checks "One table format") ---------------------
# Each test plants a real violation of the shape the guard exists for and
# confirms it's caught, then confirms the live source is clean. A guard
# that only ever passes proves nothing.

def _src():
    return pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text()


def test_live_source_passes_the_guard():
    from linklib import brand_check
    from webapp.app import _CSS
    assert brand_check.table_standard_problems(_CSS) == []
    assert brand_check.table_override_problems(_src()) == []


def test_guard_catches_a_missing_rule():
    from linklib import brand_check
    from webapp.app import _CSS
    broken = _CSS.replace(f"{T} td{{border-top:1px solid var(--line)!important", f"{T} td{{border-top:0")
    assert any("line between rows" in p for p in brand_check.table_standard_problems(broken))
    broken = _CSS.replace("--table-border:var(--navy-light);", "--table-border:var(--line);")
    assert any("navy-light border token" in p for p in brand_check.table_standard_problems(broken))
    broken = _CSS.replace("td.cc-section{background:var(--table-border)!important", "td.cc-section{background:var(--seafoam)!important")
    assert any("subheading band" in p for p in brand_check.table_standard_problems(broken))


def test_guard_catches_a_new_exclusion():
    from linklib import brand_check
    from webapp.app import _CSS
    broken = _CSS.replace(":not(.rr-reader-body table)", ":not(.rr-reader-body table):not(.my-special-table)")
    assert any(".my-special-table" in p for p in brand_check.table_standard_problems(broken))


def test_guard_catches_a_competing_important_override():
    from linklib import brand_check
    planted = _src() + "\n_X = '.pricing-table th{background:var(--navy)!important;color:#fff;}'\n"
    problems = brand_check.table_override_problems(planted)
    assert len(problems) == 1 and ".pricing-table th" in problems[0]
    # Doubled braces, as inside an f-string, are caught too.
    planted = _src() + "\n_Y = f'.leader-table tr{{background:#fafafa!important;}}'\n"
    assert any(".leader-table tr" in p for p in brand_check.table_override_problems(planted))
    # A plain (non-important) table rule can't beat the standard, so it's fine.
    planted = _src() + "\n_Z = '.leader-table td{padding:4px;background:#fafafa;}'\n"
    assert brand_check.table_override_problems(planted) == []


def test_guard_runs_on_the_checks_page():
    from webapp import checks
    row = next(r for r in checks.run_all() if r["name"] == "One table format")
    assert row["ok"] is True and row["where"] == "Live + CI"
