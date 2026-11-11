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
