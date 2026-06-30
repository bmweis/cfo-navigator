"""The /admin/checks dashboard aggregator (webapp/checks.py).

Guards the dashboard wiring itself: results are well-formed, and every check that
runs live in-app is currently green (a regression in brand/voice/sync/dead-code
would fail here as well as in its own test).
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import checks


def test_run_all_well_formed():
    results = checks.run_all()
    assert results, "no checks returned"
    for r in results:
        assert {"name", "what", "where", "ok", "detail"} <= set(r), r
        assert r["where"] in ("In-app", "CI")
        assert r["name"] and r["what"] and r["detail"]


def test_live_checks_currently_pass():
    for r in checks.run_all():
        if r["where"] == "In-app":
            assert r["ok"] is True, f"{r['name']} failing: {r['detail']}"
