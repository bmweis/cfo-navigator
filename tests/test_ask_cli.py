"""Regression coverage for the scripts/ask.py CLI.

The Quick/Standard/Deep redesign removed answer_question's max_sources
parameter but the CLI kept passing it, so every `python -m scripts.ask ...`
run crashed with a TypeError before it could ask anything. These tests run
main() for real (stubbing only the model call boundary) so any future
signature drift between the CLI and linklib.agent breaks loudly here.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.agent import Answer
from scripts import ask as ask_cli


def _run(monkeypatch, capsys, tmp_path, argv_extra, answer):
    seen = {}

    def fake_answer_question(lib, question, **kwargs):
        seen["question"] = question
        seen.update(kwargs)
        return answer

    monkeypatch.setattr(ask_cli, "answer_question", fake_answer_question)
    db_path = tmp_path / "cli.db"
    db_path.touch()  # resolve_db_path now requires the file to exist
    monkeypatch.setattr(sys, "argv",
                        ["ask", "--db", str(db_path)] + argv_extra + ["what is NRR?"])
    assert ask_cli.main() == 0
    return seen, capsys.readouterr().out


def test_cli_runs_and_passes_effort(monkeypatch, capsys, tmp_path):
    seen, out = _run(monkeypatch, capsys, tmp_path, ["--effort", "deep"],
                     Answer(text="NRR is net revenue retention."))
    assert seen["question"] == "what is NRR?"
    assert seen["effort"] == "deep"
    assert "max_sources" not in seen          # the crashing parameter stays gone
    assert "NRR is net revenue retention." in out


def test_cli_prints_cited_sources(monkeypatch, capsys, tmp_path):
    ans = Answer(text="Answer.[1]",
                 citations=[{"n": 1, "title": "T", "url": "https://t", "type": "library"}],
                 sources=[{"title": "Other", "url": "https://o"}])
    _, out = _run(monkeypatch, capsys, tmp_path, [], ans)
    assert "Sources:" in out and "[1] T" in out and "https://t" in out
    assert "Other" not in out                 # cited list wins over retrieved


def test_cli_falls_back_to_retrieved_when_uncited(monkeypatch, capsys, tmp_path):
    ans = Answer(text="Plain answer.", sources=[{"title": "R", "url": "https://r"}])
    _, out = _run(monkeypatch, capsys, tmp_path, [], ans)
    assert "Retrieved (uncited):" in out and "[1] R" in out


def test_cli_rejects_unknown_effort(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["ask", "--effort", "turbo", "q"])
    with pytest.raises(SystemExit):
        ask_cli.main()
