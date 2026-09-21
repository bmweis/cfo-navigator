"""Regression test for the 2026-09 sys.path bug fix (Part 2 of the voice
review queue safety PR).

Confirmed in production: `python3 scripts/backfill_voice_review_queue_source.py`
raised `ModuleNotFoundError: No module named 'linklib'` when invoked from the
repo root with no `PYTHONPATH` set, because the script imported `linklib.db`
directly with no `sys.path` shim — unlike `scripts/backfill_voice_review_queue.py`,
which already had one. `scripts/regen_ai_drafted_fields.py` had the identical
gap.

An in-process import test cannot catch this class of bug: pytest's own test
harness already has the repo root on `sys.path` before any test module runs,
so `import linklib` always succeeds inside a test process regardless of
whether the *script itself* would resolve it correctly when run standalone.
Only a real subprocess, launched from the repo root with a clean environment
(no inherited `PYTHONPATH`), can prove the fix.

Widened (Phase 2, PR #590 verification) to cover every scripts/*.py added
or touched across both #588 and #590, not just the two that had a
confirmed bug — the original PR fixed the two known-broken scripts but
never added a test proving the other ~10 files #588 touched also run
cleanly standalone. All of them already carry their own sys.path shim
(confirmed by direct inspection before writing this list), so this is
expected to pass for every entry — its job is to keep it that way, the
same way the original two entries exist to keep *their* fix from
regressing.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AFFECTED_SCRIPTS = [
    "scripts/backfill_voice_review_queue.py",
    "scripts/backfill_voice_review_queue_source.py",
    "scripts/regen_ai_drafted_fields.py",
    # The rest of #588's touched scripts (PR #590 Phase 2 widening) — each
    # already had its own sys.path shim, so this locks that in rather than
    # fixing a live bug.
    "scripts/add_current_feed_link_to_web_search_explainer.py",
    "scripts/enrich_agent_taxonomy.py",
    "scripts/enrich_community_profiles.py",
    "scripts/migrate_ai_surfaces.py",
    "scripts/normalize_original_content_tags.py",
    "scripts/retire_comp_benchmarking_features.py",
    "scripts/seed_book_recommendations.py",
    "scripts/seed_communities.py",
    "scripts/seed_feature_taxonomy.py",
    "scripts/seed_tools.py",
]


@pytest.mark.parametrize("script", AFFECTED_SCRIPTS)
def test_script_runs_as_subprocess_with_no_pythonpath(script):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{script} --help failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert "No module named 'linklib'" not in result.stderr
