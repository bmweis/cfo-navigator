"""Aggregates the project's automated checks for the /admin/checks dashboard.

The deterministic checks (brand, voice, open-source sync, and — where the tool is
installed — dead code) run live in-app and report real pass/fail. The heavier
CI-only checks (full test suite, secret scan) are listed with what they guard, so
the page is a complete inventory either way. Same rules the GitHub QA workflow runs.
"""
from __future__ import annotations

import io
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve()
_APP_PY = _HERE.parent / "app.py"
_ROOT = _HERE.parents[1]

# Link the dashboard to where these actually gate merges.
GITHUB_ACTIONS_URL = "https://github.com/bmweis/cfo-navigator/actions/workflows/qa.yml"


def _app_src() -> str:
    return _APP_PY.read_text(encoding="utf-8")


# --- open-source showcase ↔ dependencies sync -------------------------------
# Celebrated projects that aren't direct lines in requirements*.txt: transitive
# deps, the optional extractor, and a one-off build tool. Single source of truth.
OSS_EXTRAS = {"starlette", "pydantic", "lxml", "trafilatura", "pillow"}


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _declared() -> set[str]:
    out: set[str] = set()
    for fn in ("requirements.txt", "requirements-dev.txt"):
        for raw in (_ROOT / fn).read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~;\[ ]", line, 1)[0]
            if name:
                out.add(_norm(name))
    return out


def _showcased() -> set[str]:
    from webapp import app  # lazy — app imports nothing from us at module load
    return {_norm(dist) for _g, _b, items in app._OPEN_SOURCE
            for (_n, dist, *_rest) in items if dist}


def open_source_problems() -> list[str]:
    declared, showcased = _declared(), _showcased()
    problems = []
    missing = declared - showcased
    if missing:
        problems.append("Add to showcase: " + ", ".join(sorted(missing)))
    stale = showcased - declared - OSS_EXTRAS
    if stale:
        problems.append("No longer a dependency: " + ", ".join(sorted(stale)))
    return problems


# --- dead code (pyflakes) — runs live only where pyflakes is installed -------
def _pyflakes_problems() -> list[str] | None:
    try:
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except Exception:
        return None   # not installed in this environment → CI-only
    out, err = io.StringIO(), io.StringIO()
    reporter = Reporter(out, err)
    for d in ("linklib", "webapp", "scripts"):
        for path in sorted((_ROOT / d).rglob("*.py")):
            checkPath(str(path), reporter)
    return [ln for ln in (out.getvalue() + err.getvalue()).splitlines() if ln.strip()]


def run_all() -> list[dict]:
    """Each check as {name, what, where ('In-app'|'CI'), ok (bool|None), detail}."""
    src = _app_src()
    from linklib import brand_check, voice_review
    results: list[dict] = []

    bf = brand_check.findings(src)
    results.append({
        "name": "Brand standards", "where": "In-app", "ok": not bf,
        "what": "Every color is a brand token or a documented exception; only on-brand fonts.",
        "detail": "; ".join(bf) if bf else "All colors and fonts on palette."})

    vf = voice_review.mechanical_findings(src)
    results.append({
        "name": "Voice standards", "where": "In-app", "ok": not vf,
        "what": "No banned buzzwords, filler, or performative phrases in the site copy.",
        "detail": ", ".join(f"{rule}: “{phrase}”" for rule, phrase in vf) if vf else "Copy is on-voice."})

    op = open_source_problems()
    results.append({
        "name": "Open-source showcase in sync", "where": "In-app", "ok": not op,
        "what": "Every dependency is celebrated, and nothing showcased is no longer a dependency.",
        "detail": "; ".join(op) if op else "Showcase matches requirements."})

    pf = _pyflakes_problems()
    if pf is None:
        results.append({
            "name": "Dead code / unused imports", "where": "CI", "ok": None,
            "what": "pyflakes finds no unused imports or dead code in linklib/webapp/scripts.",
            "detail": "Runs in CI on every push."})
    else:
        results.append({
            "name": "Dead code / unused imports", "where": "In-app", "ok": not pf,
            "what": "pyflakes finds no unused imports or dead code in linklib/webapp/scripts.",
            "detail": "; ".join(pf[:6]) if pf else "No unused imports or dead code."})

    results.append({
        "name": "Full test suite (pytest)", "where": "CI", "ok": None,
        "what": "Access tiers, auth, dedupe, publish dates, tagging, users, and more.",
        "detail": "Runs the whole suite in CI on every push."})

    results.append({
        "name": "Secret scan (TruffleHog)", "where": "CI", "ok": None,
        "what": "No verified, live secrets committed anywhere in the repo.",
        "detail": "Runs in CI on every push and pull request."})

    return results
