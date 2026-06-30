"""Keep the /admin/open-source showcase in sync with actual dependencies.

The descriptions are hand-written, but the *set* of projects shouldn't drift:
this fails if a dependency is added to requirements*.txt without being celebrated,
or if a showcased project is no longer a dependency. The sync logic lives in
``webapp/checks.py`` (the single source, also used by the /admin/checks dashboard).
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import app, checks


def test_open_source_showcase_in_sync():
    problems = checks.open_source_problems()
    assert not problems, "Open-source showcase out of sync with requirements:\n- " + "\n- ".join(problems)


def test_showcase_entries_well_formed():
    seen = set()
    for _group, _blurb, items in app._OPEN_SOURCE:
        for entry in items:
            assert len(entry) == 5, f"bad entry shape: {entry!r}"
            name, _dist, lic, url, role = entry
            assert name and lic and url and role, entry
            assert url.startswith("http"), url
            assert name not in seen, f"duplicate showcase entry: {name}"
            seen.add(name)
