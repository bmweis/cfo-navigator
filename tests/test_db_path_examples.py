"""Mechanical guard: production script examples use an absolute --db.

Production's database is /data/library.db. A relative `--db library.db` run
from the wrong directory used to make SQLite create an empty database and
report a clean result. PR 593 fixed all 47 instances; this keeps them fixed.
See webapp.checks.db_path_example_problems.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from webapp import checks


def test_current_tree_passes():
    assert checks.db_path_example_problems() == []


def test_planted_violation_fails_and_names_file_and_line():
    planted = [("scripts/backfill_logos.py", 12,
                "    python -m scripts.backfill_logos --db library.db --apply")]
    problems = checks.db_path_example_problems(planted)
    assert len(problems) == 1
    assert problems[0].startswith("scripts/backfill_logos.py:12 ")
    assert "--db library.db" in problems[0]
    assert "/data/library.db" in problems[0]


def test_planted_violation_in_registry_and_docs_fails():
    for fname in ("webapp/app.py", "CLAUDE.md", "README.md", "RUNBOOK.md"):
        planted = [(fname, 7, "python -m scripts.seed_tools --db=./library.db")]
        assert checks.db_path_example_problems(planted), fname


def test_violation_in_the_real_sources_is_caught(monkeypatch):
    """Plant a violation in the real candidate stream (not a hand-built
    list) to prove the file readers feed the check."""
    real = checks._db_path_candidates
    monkeypatch.setattr(checks, "_db_path_candidates",
                        lambda: real() + [("RUNBOOK.md", 999, "railway ssh python -m scripts.x --db library.db")])
    problems = checks.db_path_example_problems()
    assert problems == [p for p in problems if p.startswith("RUNBOOK.md:999 ")] and problems


def test_each_allowlisted_entry_passes_and_has_a_reason():
    for fname, sub, reason in checks.DB_PATH_ALLOWLIST:
        assert reason.strip(), (fname, sub)
        line = f"    python -m {sub}" if sub.startswith("scripts.") and "--db" in sub else \
               f"    python -m {sub} --db library.db"
        assert checks.db_path_example_problems([(fname, 1, line)]) == [], (fname, sub)


def test_allowlist_entry_is_scoped_to_its_own_file():
    line = "python -m scripts.import_archive --zip feedly-archive.zip --db library.db"
    assert checks.db_path_example_problems([("RUNBOOK.md", 3, line)])


def test_archive_scripts_are_out_of_scope():
    assert checks.db_path_example_problems(
        [("scripts/archive/migrate_thing.py", 4, "python -m scripts.archive.migrate_thing --db library.db")]) == []


def test_absolute_env_and_prose_forms_pass():
    ok = [
        ("RUNBOOK.md", 1, "python -m scripts.x --db /data/library.db"),
        ("RUNBOOK.md", 2, "python -m scripts.x --db $LINKLIB_DB"),
        ("RUNBOOK.md", 3, "pass --db explicitly, never rely on the default"),
        ("RUNBOOK.md", 4, "python -m scripts.x --db <path>"),
    ]
    assert checks.db_path_example_problems(ok) == []


def test_run_all_carries_the_guard():
    """Structural rather than a full run_all() pass (~10s): the guard is
    wired in. test_checks.py's live-pass test covers the real result."""
    import inspect
    assert "db_path_example_problems()" in inspect.getsource(checks.run_all)
