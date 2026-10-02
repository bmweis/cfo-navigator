"""BRAND.md section 5: on a page with a `.tool-prose` reading column, a table
or grid belongs inside the column. `brand_check.reading_column_problems` is a
source scan (not a rendered one: rendering from inside run_all() re-enters
itself across threadpool threads). Tested two-sided: it must flag the shapes
that broke /how-this-is-built and /tools/fpa-buddy/how-it-works, and must not
flag the allowed shapes."""
import subprocess
from pathlib import Path

from linklib.brand_check import reading_column_problems

_APP = Path(__file__).resolve().parent.parent / "webapp" / "app.py"


def test_live_source_is_clean():
    assert reading_column_problems(_APP.read_text(encoding="utf-8")) == []


def test_flags_a_card_grid_beside_the_column():
    src = 'BODY = """<div class="page"><div class="tool-prose"><p>a</p></div>' \
          '<div style="display:grid;gap:14px;"><div>card</div></div>' \
          '<div class="tool-prose"><p>b</p></div></div>"""'
    assert len(reading_column_problems(src)) == 1


def test_flags_a_table_beside_the_column_even_in_an_overflow_wrapper():
    src = 'BODY = """<div class="page"><div class="tool-prose"><p>a</p></div>' \
          '<div style="overflow-x:auto;"><table><tr><td>x</td></tr></table></div></div>"""'
    assert len(reading_column_problems(src)) == 1


def test_flags_inside_an_fstring_literal():
    src = 'x = 1\nBODY = f"""<div class="tool-prose"><p>{x}</p></div><table><tr><td>y</td></tr></table>"""'
    problems = reading_column_problems(src)
    assert len(problems) == 1 and problems[0].startswith("line 2:")


def test_allows_blocks_inside_the_column():
    src = 'BODY = """<div class="tool-prose"><p>a</p><div style="display:grid;gap:14px;">c</div>' \
          '<div style="overflow-x:auto;"><table><tr><td>x</td></tr></table></div></div>"""'
    assert reading_column_problems(src) == []


def test_allows_a_full_width_card_beside_the_column():
    """The Growth Engine Ratio calculator is a deliberate wide card next to
    narrow prose; grids inside a styled card are not flagged."""
    src = 'BODY = """<div class="tool-prose"><p>a</p></div>' \
          '<div class="ger-card"><div style="display:grid;gap:20px;">fields</div></div>"""'
    assert reading_column_problems(src) == []


def test_pages_without_a_reading_column_are_ignored():
    src = 'BODY = """<div class="page"><div style="display:grid;">x</div><table></table></div>"""'
    assert reading_column_problems(src) == []


def test_catches_the_real_pre_fix_how_it_works_page():
    """The page this rule was written for, from the main commit before it was
    fixed. Skips where history that old is not available (a shallow clone)."""
    try:
        old = subprocess.check_output(
            ["git", "show", "82006cd^1:webapp/app.py"], cwd=_APP.parent.parent,
            stderr=subprocess.DEVNULL).decode()
    except Exception:
        import pytest
        pytest.skip("pre-fix history not available in this clone")
    kinds = {p.split(": ")[1].split(" ")[0] for p in reading_column_problems(old)}
    assert {"grid", "table"} <= kinds


def test_shows_up_as_a_live_check_row(monkeypatch):
    import importlib
    import tempfile
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    import webapp.app as appmod
    importlib.reload(appmod)
    import webapp.checks as checks
    importlib.reload(checks)
    names = [r["name"] for r in checks.run_all()]
    assert "Reading column holds its tables and grids" in names
