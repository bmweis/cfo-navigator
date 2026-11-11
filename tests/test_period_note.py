"""The site's note on 2020 to 2022 data. Invented fixtures only."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import org_spend_fixtures as F  # noqa: E402
import test_table_render as ttr  # noqa: E402

from linklib import period_note as pn, voice_review as vr  # noqa: E402
from webapp.answer_render import render_answer_markdown  # noqa: E402

pytestmark = pytest.mark.usefixtures("anthropic_env")


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


@pytest.fixture
def lib(tmp_path):
    return F.make_lib(tmp_path / "t.db")


@pytest.mark.parametrize("period,expect", [
    ("Q2 2022", True), ("2022", True), ("2021", True), ("2020", True), ("FY2021", True),
    ("LTM to Dec 2022", True), ("2021 to 2022", True), ("2019-2020", True), ("2021/22", True),
    ("2023", False), ("2019", False), ("Q4 2024", False), ("2026E", False),
    ("LTM to Jan-Feb 2026", False), ("LTM, submitted July 2025", False), ("2025 survey", False),
    ("not stated (survey Nov-Dec 2024)", False), ("2026-05-09", False),
    ("change 2021 to 2022", False), ("", False), ("not stated", False), (None, False),
])
def test_which_periods_trigger(period, expect):
    assert pn.in_window(period) is expect


def test_edition_year_alone_never_triggers():
    # A 2023 edition holding 2024 data: periods are what is read, never report_year.
    assert pn.triggering_periods(["2024"], []) == []


def test_shift_endpoint_triggers():
    sh = {"from": {"period": "Q2 2021"}, "to": {"period": "Q2 2025"}}
    assert pn.triggering_periods([], [sh]) == ["Q2 2021"]
    assert pn.triggering_periods([], [{"from": {"period": "2023"}, "to": {"period": "2024"}}]) == []


def test_note_is_appended_once_and_empty_means_off():
    out, info = pn.apply_note("Body.", "Hello.", ["Q2 2022"])
    assert out == "Body.\n\nNote: Hello." and info["shown"] and info["periods"] == ["Q2 2022"]
    again, info2 = pn.apply_note(out, "Hello.", ["Q2 2022"])
    assert again == out and not info2["shown"]
    off, info3 = pn.apply_note("Body.", "", ["Q2 2022"])
    assert off == "Body." and info3["off"] and not info3["shown"]
    none, info4 = pn.apply_note("Body.", "Hello.", [])
    assert none == "Body." and not info4["shown"]


def test_default_text_passes_the_voice_checks_and_is_a_hedge():
    assert vr.mechanical_findings(pn.DEFAULT_NOTE) == [] and vr.typography_findings_plain(pn.DEFAULT_NOTE) == []
    assert "points to a reversion" in pn.DEFAULT_NOTE and "shows" not in pn.DEFAULT_NOTE.lower()


def test_stored_copy_wins_and_stored_empty_is_off(lib):
    assert pn.note_text(lib) == pn.DEFAULT_NOTE
    lib.set_setting(pn.NOTE_KEY, "My words.")
    assert pn.note_text(lib) == "My words."
    lib.set_setting(pn.NOTE_KEY, "")
    assert pn.note_text(lib) == ""


def _age(lib, slug_prefix, period):
    lib.conn.execute("UPDATE benchmark_figures SET data_period=? WHERE report_slug LIKE ?", (period, slug_prefix + "%"))
    lib.conn.commit()


def test_end_to_end_triggers_on_a_2022_printed_row(lib, monkeypatch):
    _age(lib, "eb-2025", "Q2 2022")
    ans, _ = ttr.run_turn(lib, monkeypatch, ttr.PROSE)
    assert ans.text.rstrip().endswith("Note: " + pn.DEFAULT_NOTE)
    assert ans.text.count("Note: Figures covering") == 1
    assert ans.prestep["period_note"]["shown"] and "Q2 2022" in ans.prestep["period_note"]["periods"]
    html = render_answer_markdown(ans.text, ans.citations)
    assert "Figures covering 2020 to 2022" in html and html.count("Figures covering") == 1


def test_end_to_end_no_note_for_recent_periods(lib, monkeypatch):
    # The fixture's 2023 edition holds 2022 data (CY-2022), which correctly triggers; move every period to 2025.
    _age(lib, "", "2025")
    ans, _ = ttr.run_turn(lib, monkeypatch, ttr.PROSE)
    assert "Note: " not in ans.text and not ans.prestep["period_note"]["shown"]


def test_end_to_end_an_edition_year_alone_does_not_trigger_but_its_old_data_does(lib, monkeypatch):
    _age(lib, "", "2025")
    lib.conn.execute("UPDATE benchmark_figures SET report_year='2022'")   # edition year in the window, data not
    lib.conn.commit()
    ans, _ = ttr.run_turn(lib, monkeypatch, ttr.PROSE)
    assert "Note: " not in ans.text


def test_end_to_end_off_when_empty(lib, monkeypatch):
    _age(lib, "eb-2025", "2022")
    lib.set_setting(pn.NOTE_KEY, "")
    ans, _ = ttr.run_turn(lib, monkeypatch, ttr.PROSE)
    assert "Note: " not in ans.text


# --- where the note shows: the stored row, the CSV, the server-rendered history, both renderers ----------------

import json, os, shutil, subprocess, tempfile  # noqa: E402

NOTE_LINE = "Note: " + pn.DEFAULT_NOTE


def _noted_answer(lib, monkeypatch):
    _age(lib, "eb-2025", "Q2 2022")
    ans, _ = ttr.run_turn(lib, monkeypatch, ttr.PROSE)
    assert NOTE_LINE in ans.text
    return ans


@pytest.fixture
def app_env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_note_is_in_the_stored_row_the_csv_and_the_history_page(lib, monkeypatch, app_env):
    from fastapi.testclient import TestClient
    from linklib.db import Library
    ans = _noted_answer(lib, monkeypatch)
    appmod, db = app_env
    store = Library(db)
    uid = store.create_user("member1", "supersecret", role="user")
    store.record_ask_question(uid, "Org structure?", ans.text, ans.model, "standard", True, False, False,
                              cost_usd=0.03, citations=ans.citations)
    assert NOTE_LINE in store.conn.execute("SELECT answer FROM ask_questions").fetchone()[0]   # the stored row
    store.create_user("boss", "adminadmin", role="admin")
    store.close()
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "boss", "password": "adminadmin"}, follow_redirects=False)
    csv_text = c.get("/admin/fpa-buddy/report/export.csv").text
    assert "Figures covering 2020 to 2022" in csv_text and "Note: " in csv_text            # the CSV
    c2 = TestClient(appmod.app)
    c2.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    page = c2.get("/ask/history").text
    assert page.count("Figures covering 2020 to 2022") == 1                                # server-rendered history


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not available")
def test_live_js_renderer_shows_the_note_and_matches_the_python_twin(lib, monkeypatch, tmp_path):
    import webapp.app as appmod
    ans = _noted_answer(lib, monkeypatch)
    src = pathlib.Path(appmod.__file__).read_text()
    i = src.index("function escapeHtml(s) {{")
    j = src.index("// Below-answer list")
    js = src[i:j].replace("{{", "{").replace("}}", "}").replace("\\\\", "\\")
    cites = [{"url": c.get("url", ""), "title": c.get("title", "")} for c in ans.citations]
    script = tmp_path / "md.js"
    script.write_text("var CITES=" + json.dumps(cites) + ";\nvar CITE_SCOPE='';\n" + js +
                      "\nprocess.stdout.write(mdToHtml(require('fs').readFileSync(0,'utf8')));")
    live = subprocess.run([NODE, str(script)], input=ans.text, capture_output=True, text=True, check=True).stdout
    assert "Figures covering 2020 to 2022" in live
    # Whole-answer equality is not asserted: a citation with no link renders <sup> live and plain [n] on the server
    # (a known, documented difference that has nothing to do with the note). The note paragraph itself is identical.
    py = render_answer_markdown(ans.text, ans.citations, "")
    assert live.rsplit("<p>", 1)[-1] == py.rsplit("<p>", 1)[-1] and live.rstrip().endswith("</p>")
