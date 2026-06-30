"""Keep the /admin/open-source showcase in sync with actual dependencies.

The descriptions are hand-written, but the *set* of projects shouldn't drift:
this fails if a dependency is added to requirements*.txt without being celebrated,
or if a showcased project is no longer a dependency. Add/remove accordingly.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import app

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Projects we celebrate that aren't *direct* lines in requirements*.txt:
# transitive deps pulled in by the direct ones, an optional extractor, and a
# one-off build tool. Keep this short and honest.
_EXTRAS = {"starlette", "pydantic", "lxml", "trafilatura", "pillow"}


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _declared(filename: str) -> set[str]:
    out = set()
    for raw in (ROOT / filename).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):   # blanks, comments, -r includes
            continue
        name = re.split(r"[<>=!~;\[ ]", line, 1)[0]
        if name:
            out.add(_norm(name))
    return out


def _declared_all() -> set[str]:
    return _declared("requirements.txt") | _declared("requirements-dev.txt")


def _showcased() -> set[str]:
    return {_norm(dist) for _, _, items in app._OPEN_SOURCE
            for (_name, dist, *_rest) in items if dist}


def test_every_dependency_is_celebrated():
    missing = _declared_all() - _showcased()
    assert not missing, (
        f"New dependency not in the open-source showcase — add it to _OPEN_SOURCE "
        f"in webapp/app.py: {sorted(missing)}")


def test_no_stale_projects_in_showcase():
    stale = _showcased() - _declared_all() - _EXTRAS
    assert not stale, (
        f"Showcased in _OPEN_SOURCE but no longer a dependency — remove it (or add "
        f"to _EXTRAS in this test if intentionally kept): {sorted(stale)}")


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
