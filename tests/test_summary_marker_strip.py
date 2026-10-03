"""A Short summary carries no `[n]` citation markers (follow-up to PR 2a.2).

Two automatic paths could put one there, both closed here:
  * `generate_tool_description`: the API can attach a citation to the SUMMARY
    text block, and `extract_citations` splices `[n]` into it;
  * the empty-summary copy in `Library.__init__`, which copies the Description
    (which carries markers) into a blank summary on every open.
Guards: the generator strips only this draft's own citation numbers; the copy
strips only 1-2 digit markers; a year such as "[2024]" survives both; text a
person typed into a summary is never touched.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from linklib import enrich
from linklib.citations import strip_citation_markers
from linklib.db import Library
from test_description_citations import (  # noqa: E402
    DESC_BODY, LONG_PAGE_CONTENT, _mock_anthropic_citing, _mock_fetch_page)


def _draft(monkeypatch, summary_line):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [
        (DESC_BODY, [0]),
        (f"\n\nSUMMARY: {summary_line}", [0]),   # the citation lands on the SUMMARY block
        ("\n\nCONFIDENT: true", []),
    ])
    return enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")


def test_generator_strips_a_marker_the_api_attached_to_the_summary(monkeypatch):
    draft = _draft(monkeypatch, "Runway is an FP&A platform for growth-stage finance teams.")
    assert draft is not None
    assert "[" not in draft.summary and "]" not in draft.summary
    assert draft.summary == "Runway is an FP&A platform for growth-stage finance teams."
    # The long write-up keeps its marker and its Sources.
    assert "[1]" in draft.description and len(draft.citations) == 1


def test_generator_leaves_a_year_in_brackets_alone(monkeypatch):
    draft = _draft(monkeypatch, "Runway, launched [2024], is an FP&A platform.")
    assert "[2024]" in draft.summary
    assert "[1]" not in draft.summary


@pytest.fixture
def db_path():
    p = tempfile.mktemp(suffix=".db")
    yield p
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(p + ext):
            os.remove(p + ext)


def _blank_summary(path, description):
    lib = Library(path)
    tid = lib.add_tool("Acme", description, "https://acme.example", [], approved=1, summary="placeholder")
    lib.close()
    c = sqlite3.connect(path)
    c.execute("UPDATE tools SET summary='' WHERE id=?", (tid,))
    c.commit()
    c.close()
    return tid


def test_empty_summary_copy_strips_markers_from_the_description(db_path):
    tid = _blank_summary(db_path, "Acme closes the books faster.[1] It reconciles daily [2].")
    lib = Library(db_path)          # the copy runs on open
    row = lib.get_tool(tid)
    lib.close()
    assert row["summary"] == "Acme closes the books faster. It reconciles daily."
    assert "[1]" in row["description"]      # the Description itself is untouched


def test_empty_summary_copy_keeps_a_year_in_brackets(db_path):
    tid = _blank_summary(db_path, "Acme, founded [2024], closes the books.[1]")
    lib = Library(db_path)
    row = lib.get_tool(tid)
    lib.close()
    assert row["summary"] == "Acme, founded [2024], closes the books."


def test_a_summary_a_person_typed_is_never_touched(db_path):
    lib = Library(db_path)
    tid = lib.add_tool("Acme", "d", "https://acme.example", [], approved=1, summary="Typed by hand [1].")
    lib.close()
    lib = Library(db_path)          # reopen runs the copy; summary is not empty
    assert lib.get_tool(tid)["summary"] == "Typed by hand [1]."
    lib.close()


def test_strip_helper_limits_to_given_numbers():
    assert strip_citation_markers("a [7] b [3]", [3]) == "a [7] b"
    assert strip_citation_markers("a [7] b [3]") == "a b"
    assert strip_citation_markers("In [2024] [1]") == "In [2024]"
    assert strip_citation_markers("") == ""
