"""README.md may only point at repo paths and routes that exist.

Deliberately simple. It reads inline backtick spans outside fenced code blocks
and checks two kinds:
  - a route: a span starting with "/" (an optional leading HTTP method is
    allowed) must match a path registered in the app, or be /mcp (mounted);
  - a repo path: a span with no spaces that contains "/" or ends in a known
    file extension must exist relative to the repo root.
It ignores: fenced code blocks, spans with spaces (commands), spans containing
placeholders or globs ({ } * < >), URLs, absolute server paths such as
/data/..., .db files (library.db is deliberately not in the repo), and bare
words with no slash or extension (env vars, identifiers).
"""
import os
import pathlib
import re
import tempfile

import pytest

from linklib.db import Library

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
FILE_EXT = (".py", ".md", ".opml", ".toml", ".yml", ".yaml", ".txt", ".example", ".json")


def _spans() -> list[str]:
    text = re.sub(r"```.*?```", "", README.read_text(encoding="utf-8"), flags=re.S)
    return re.findall(r"`([^`\n]+)`", text)


def _classify(span: str):
    span = re.sub(r"^(GET|POST|PUT|DELETE|PATCH)\s+", "", span)
    if " " in span or any(c in span for c in "{}*<>") or "://" in span:
        return None
    if span.startswith("/"):
        return None if span.startswith("/data/") else ("route", span)
    if span.endswith(".db"):
        return None
    if "/" in span or span.endswith(FILE_EXT):
        return ("path", span)
    return None


def test_readme_paths_exist():
    missing = [s for kind, s in filter(None, map(_classify, _spans()))
               if kind == "path" and not (ROOT / s.rstrip("/")).exists()]
    assert not missing, f"README.md references repo paths that do not exist: {missing}"


@pytest.fixture
def appmod(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "x")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    Library(db).close()
    import importlib
    import webapp.app as mod
    importlib.reload(mod)
    yield mod
    if os.path.exists(db):
        os.remove(db)


def test_readme_routes_are_registered(appmod):
    registered = {getattr(r, "path", "") for r in appmod.app.routes}
    registered.add("/mcp")  # mounted sub-app, not a plain route
    missing = [s for kind, s in filter(None, map(_classify, _spans()))
               if kind == "route" and s.rstrip("/") not in registered and s not in registered]
    assert not missing, f"README.md references routes the app does not register: {missing}"
