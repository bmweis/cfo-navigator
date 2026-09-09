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
import shutil
import subprocess
import tempfile

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


# --- BRAND.md §7 ↔ live :root sync -------------------------------------------
def brand_docs_problems() -> list[str]:
    from scripts.generate_brand_docs import stale as brand_docs_stale
    brand_md = (_ROOT / "BRAND.md").read_text(encoding="utf-8")
    if brand_docs_stale(brand_md, _app_src()):
        return ["BRAND.md §7 is out of date with webapp/app.py's :root block"
                "—run `python -m scripts.generate_brand_docs` and commit the diff."]
    return []


# --- hub-nav orphans (every real /admin route has a hub card) ---------------
def hub_nav_orphan_problems() -> list[str]:
    from webapp import app
    return app.hub_nav_orphans()


# --- shared <script> blocks parse as valid JS --------------------------------
# 2026-08 lesson: a fix once validated raw source text via regex and reported
# success, but every _JS constant below is a plain (non-f-string) Python
# string, so Python resolves its escape sequences (\', \\, etc.) between
# source and the value actually embedded in the page—regexing app.py's own
# text checks a different string than the one the browser gets. Reading the
# constants as real, already-resolved Python string values (imported from
# webapp.app, not reparsed from source) is what makes this catch that class
# of bug instead of repeating it. A syntax error anywhere in a shared script
# tag aborts parsing of the whole tag—no function in it gets defined, not
# just the one nearest the typo—so this class of bug can silently disable
# unrelated features (search, select-all, bulk edit all went with it once)
# with no exception thrown anywhere. See CLAUDE.md for the full writeup.


def _js_constants() -> dict[str, str]:
    """Every module-level string in webapp.app that holds JS, by this
    codebase's naming convention (name ends in _JS)—not a maintained list, so
    a newly added shared block is covered automatically."""
    from webapp import app as appmod
    out: dict[str, str] = {}
    for name in dir(appmod):
        if not name.endswith("_JS"):
            continue
        val = getattr(appmod, name)
        if isinstance(val, str):
            out[name] = val
    return out


def _concatenated_script_tags() -> list[tuple[str, str]]:
    """(A, B) pairs from every <script>{A}{B}</script> in webapp/app.py's
    source—two _JS constants rendered back-to-back into one tag, checked as
    the single unit actually sent to the browser (not just each half on its
    own), since that's the exact shape the 2026-08 regression shipped in."""
    return re.findall(r"<script>\{(_[A-Za-z_]+_JS)\}\{(_[A-Za-z_]+_JS)\}</script>", _app_src())


def _node_check(js_text: str) -> str | None:
    """Runs `node --check` against js_text; returns Node's error message, or
    None if it parses clean."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js_text)
        path = f.name
    try:
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=10)
        return result.stderr.strip() if result.returncode != 0 else None
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def script_syntax_problems() -> list[str] | None:
    """Every shared inline <script> block, syntax-checked against Node. None
    (not a failure) when Node isn't installed: GitHub-hosted CI runners ship
    Node by default so this always runs there, and it runs live wherever
    Node happens to be available in dev, but production's Docker image
    (python:3.11-slim) doesn't need Node added just for this."""
    if shutil.which("node") is None:
        return None
    problems: list[str] = []
    consts = _js_constants()
    for name, js_text in sorted(consts.items()):
        err = _node_check(js_text)
        if err:
            problems.append(f"{name}: {err.splitlines()[0] if err else 'syntax error'}")
    for a, b in _concatenated_script_tags():
        if a in consts and b in consts:
            err = _node_check(consts[a] + consts[b])
            if err:
                problems.append(f"{a}+{b} (as concatenated in <script>): "
                                 f"{err.splitlines()[0] if err else 'syntax error'}")
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
        "name": "Brand standards", "where": "Live + CI", "ok": not bf,
        "what": "Every color is a brand token or a documented exception; only on-brand fonts.",
        "detail": "; ".join(bf) if bf else "All colors and fonts on palette. (test_brand_standards)"})

    vf = voice_review.mechanical_findings(src)
    results.append({
        "name": "Voice standards", "where": "Live + CI", "ok": not vf,
        "what": "No banned buzzwords, filler, or performative phrases in the site copy.",
        "detail": ", ".join(f"{rule}: “{phrase}”" for rule, phrase in vf) if vf else "Copy is on-voice. (test_voice_standards)"})

    op = open_source_problems()
    results.append({
        "name": "Open-source showcase in sync", "where": "Live + CI", "ok": not op,
        "what": "Every dependency is celebrated, and nothing showcased is no longer a dependency.",
        "detail": "; ".join(op) if op else "Showcase matches requirements. (test_open_source)"})

    bd = brand_docs_problems()
    results.append({
        "name": "BRAND.md §7 in sync", "where": "Live + CI", "ok": not bd,
        "what": "BRAND.md's token table is generated from the live :root block, not hand-copied.",
        "detail": "; ".join(bd) if bd else "BRAND.md matches the live CSS. (test_brand_docs_sync)"})

    hn = hub_nav_orphan_problems()
    results.append({
        "name": "Hub-nav orphans", "where": "Live + CI", "ok": not hn,
        "what": "Every real /admin route has a corresponding hub-nav card—nothing reachable only by guessing the URL.",
        "detail": "; ".join(hn) if hn else "Every admin route has a hub-nav card. (test_hub_nav_orphans)"})

    pf = _pyflakes_problems()
    if pf is None:
        results.append({
            "name": "Dead code / unused imports (pyflakes)", "where": "CI", "ok": None,
            "what": "No unused imports or dead code in linklib / webapp / scripts.",
            "detail": "Lint step in the QA workflow."})
    else:
        results.append({
            "name": "Dead code / unused imports (pyflakes)", "where": "Live + CI", "ok": not pf,
            "what": "No unused imports or dead code in linklib / webapp / scripts.",
            "detail": "; ".join(pf[:6]) if pf else "No unused imports or dead code. (pyflakes lint step)"})

    sp = script_syntax_problems()
    if sp is None:
        results.append({
            "name": "Shared <script> blocks parse (node --check)", "where": "CI", "ok": None,
            "what": "Every shared inline <script> block in webapp/app.py is valid JS.",
            "detail": "Runs wherever Node is available; GitHub-hosted CI runners ship it by default."})
    else:
        results.append({
            "name": "Shared <script> blocks parse (node --check)", "where": "Live + CI", "ok": not sp,
            "what": "Every shared inline <script> block in webapp/app.py is valid JS.",
            "detail": "; ".join(sp) if sp else "Every shared script block parses clean. (test_admin_js_syntax)"})

    results.append({
        "name": "Rest of the test suite (pytest)", "where": "CI", "ok": None,
        "what": "Everything else the suite covers—access tiers, auth, dedupe, publish dates, tagging, users.",
        "detail": "Runs the whole suite (including the three above) on every commit."})

    results.append({
        "name": "Secret scan (TruffleHog)", "where": "CI", "ok": None,
        "what": "No verified, live secrets committed anywhere in the repo.",
        "detail": "Separate secret-scan job on every commit."})

    return results
