"""Aggregates the project's automated checks for the /admin/checks dashboard.

The deterministic checks (brand, voice, open-source sync, and — where the tool is
installed — dead code) run live in-app and report real pass/fail. The heavier
CI-only checks (full test suite, secret scan) are listed with what they guard, so
the page is a complete inventory either way. Same rules the GitHub QA workflow runs.
"""
from __future__ import annotations

import io
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

_HERE = pathlib.Path(__file__).resolve()
_APP_PY = _HERE.parent / "app.py"
_ROOT = _HERE.parents[1]
_ENRICH_PY = _ROOT / "linklib" / "enrich.py"
_FEATURE_SCAN_PY = _ROOT / "linklib" / "feature_scan.py"

# Link the dashboard to where these actually gate merges.
GITHUB_ACTIONS_URL = "https://github.com/bmweis/cfo-navigator/actions/workflows/qa.yml"


def _app_src() -> str:
    return _APP_PY.read_text(encoding="utf-8")


# --- Voice-copy scanned file list (PR 10 rider; extended PR 15; renamed and
# --- shared with mechanical_findings in the voice-enforcement PR) -----------
# `typography_findings()` and `mechanical_findings()`'s scope is both UI copy
# a reader sees rendered in HTML — webapp/app.py is where that copy actually
# lives — and LLM prompt-assembly text, since the model reads and imitates a
# prompt's own wording (see below). PR 10's investigation checked the other
# linklib/ modules (agent.py, matchmaker.py, enrich.py, compare.py,
# feature_scan.py, dedupe.py, tagstyle.py) and found every real hit there was
# LLM system-/generation-prompt assembly text — so none were added at the
# time.
#
# PR 15 revisits that call for enrich.py and feature_scan.py specifically
# (69 and 23 typography violations respectively), on the reasoning that got
# VOICE_CORE_DEFAULT/VOICE_FPA_BUDDY_DEFAULT held to this standard: prompt
# text isn't ordinary code, a spaced em dash inside a prompt demonstrates the
# exact thing the prompt forbids. Both files were swept and fixed (mechanical
# only — spaced em dashes collapsed, no rewording) in that PR; "CFOs & VP
# Finance" and "Flux Analysis & Summaries" are real terms, not lazy "and"s,
# so they're allowlisted in AMPERSAND_NAMES rather than rewritten.
#
# The 2026-09 voice-enforcement PR renamed this from TYPOGRAPHY_SCANNED_FILES
# — it was typography-only in name, but mechanical_findings (banned words/
# filler/performative) belongs on the exact same file list for the same
# reason: both rules are about UI/prompt copy, not code, and there's no
# reason for the two rules to scan a different set of files. Its own prior
# CI wiring only ever scanned webapp/app.py (via a separate, reimplemented
# regex in tests/test_voice_standards.py, not mechanical_findings itself —
# see that file's own note on why it's retired) — this closes that one-file
# gap and the two-implementation drift risk together.
#
# The rest of the PR 10 module list (agent.py, matchmaker.py, compare.py,
# dedupe.py, tagstyle.py) is still unchanged — not swept as part of this PR.
# agent.py in particular has the identical "rubric enumerates its own banned
# words" shape voice_review._mask_rubric_enumerations was built to handle
# (VOICE_CORE_DEFAULT's own "- Avoid:" line, which lists banned words) — but two of its OTHER rubric lines ("No performative openers or
# closers (...)", "No filler (...)") use a different marker shape the
# current mask doesn't cover, so adding agent.py to this list today would
# still need new markers, not just a one-line addition. Flagged here so a
# future sweep doesn't have to rediscover it.
#
# Kept as a real list, not a single hardcoded path, so a future file that
# DOES belong here is a one-line addition, not a refactor.
#
# webapp/checks.py joined in 2026-09: its check descriptions and details
# render on /admin/checks, so they're UI copy like anything in app.py.
_CHECKS_PY = pathlib.Path(__file__).resolve()
VOICE_SCANNED_FILES = (_APP_PY, _ENRICH_PY, _FEATURE_SCAN_PY, _CHECKS_PY)


def _voice_scanned_sources() -> list[tuple[pathlib.Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in VOICE_SCANNED_FILES]


# --- Relative --db paths in production script examples (2026-09) -----------
# Production's database is /data/library.db, on the Railway volume. A script
# run with a relative `--db library.db` from the wrong directory used to have
# SQLite create an empty database and report a clean result — the Corpay
# incident's failure shape. `resolve_db_path` now refuses a missing path, and
# PR 593 pointed all 47 production-facing examples at /data/library.db; this
# keeps them there. Scans source, never rendered pages: the script registry
# behind /admin/system/scripts, every script's module docstring (it's the
# `--help` text), and the three operator docs. scripts/archive/ is out of
# scope — frozen one-time migrations, never run again.
_DB_ARG_RE = re.compile(r"--db[ =]+([^\s\"'`)<,;]+)")
_DB_DOC_FILES = ("CLAUDE.md", "README.md", "RUNBOOK.md")

# Every allowed relative example, keyed by (file, a substring of the line)
# with the reason it's allowed. Each is a local-dev command, never one meant
# to run against production. Source: PR 593's own "Left alone" list.
DB_PATH_ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    ("scripts/import_archive.py", "scripts.import_archive",
     "Local-dev quick start: the one-time Feedly import builds a brand-new local library.db."),
    ("scripts/enrich_backfill.py", "scripts.enrich_backfill",
     "Local-dev quick start: backfills the local library.db the import just built."),
    ("scripts/embed_backfill.py", "scripts.embed_backfill",
     "Local-dev quick start: embeds the local library.db the import just built."),
    ("scripts/dump_communities.py", "scripts.dump_communities --db library.db",
     "Explicitly labeled \"locally against a copy of the DB\"; the prod line above it uses /data."),
    ("CLAUDE.md", "scripts.import_archive --zip feedly-archive.zip --db library.db",
     "The \"Running locally\" block: local-dev quick start."),
    ("CLAUDE.md", "scripts.enrich_backfill --db library.db",
     "The \"Running locally\" block: local-dev quick start."),
    ("CLAUDE.md", "scripts.embed_backfill --db library.db",
     "The \"Running locally\" block: local-dev quick start."),
    ("README.md", "scripts.import_archive --zip feedly-archive.zip --db library.db",
     "\"The library pipeline\" walkthrough of the local quick start (same three scripts)."),
    ("README.md", "scripts.enrich_backfill --db library.db",
     "\"The library pipeline\" walkthrough of the local quick start (same three scripts)."),
)


def _db_path_ok(path: str) -> bool:
    """Absolute, an env-var reference (resolves to LINKLIB_DB, which is
    /data/library.db in production), or a <placeholder>. Anything else that
    looks like a path is relative. Prose like "--db explicitly" isn't a
    path at all and is skipped by _looks_like_path."""
    return path.startswith("/") or path.startswith("$") or path.startswith("{")


def _looks_like_path(token: str) -> bool:
    return token.endswith(".db") or "/" in token or token.startswith(("$", "~", "."))


def _registry_example_lines() -> list[tuple[int, str]]:
    """(line, text) for every string literal inside _SCRIPT_REGISTRY, read
    straight from webapp/app.py's source. Parses only the registry's own
    span, not the 36k-line file."""
    import ast
    lines = _app_src().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("_SCRIPT_REGISTRY = ["))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "]")
    tree = ast.parse("\n".join(lines[start:end + 1]))
    return [(start + node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _script_docstring_lines() -> list[tuple[str, int, str]]:
    import ast
    out = []
    for path in sorted((_ROOT / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = tree.body[0] if tree.body else None
        if not (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
                and isinstance(doc.value.value, str)):
            continue
        rel = f"scripts/{path.name}"
        for offset, line in enumerate(doc.value.value.splitlines()):
            out.append((rel, doc.lineno + offset, line))
    return out


def _db_path_candidates() -> list[tuple[str, int, str]]:
    """Every (file, line, text) the guard reads."""
    out = [("webapp/app.py", ln, text) for ln, text in _registry_example_lines()]
    out += _script_docstring_lines()
    for name in _DB_DOC_FILES:
        path = _ROOT / name
        if path.exists():
            out += [(name, i, line) for i, line in
                    enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)]
    return out


def _db_path_allowed(fname: str, line: str) -> bool:
    return any(fname == f and sub in line for f, sub, _reason in DB_PATH_ALLOWLIST)


def db_path_example_problems(candidates=None) -> list[str]:
    """Every production-facing `--db` example whose path isn't absolute.
    `candidates` is for tests; the live check reads the real files."""
    problems = []
    for fname, line_no, text in (candidates if candidates is not None else _db_path_candidates()):
        if fname.startswith("scripts/archive/"):
            continue
        for m in _DB_ARG_RE.finditer(text):
            path = m.group(1)
            if not _looks_like_path(path) or _db_path_ok(path):
                continue
            if _db_path_allowed(fname, text):
                continue
            problems.append(f"{fname}:{line_no} passes --db {path}, a relative path. Use "
                            f"--db /data/library.db (production's database), or add a reasoned "
                            f"entry to DB_PATH_ALLOWLIST if this is a local-only example.")
    return problems


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


# --- AI config consolidation guard (PR 10) -----------------------------------
def ai_config_orphan_problems() -> list[str]:
    from webapp import app
    return app.ai_config_editable_outside_ai_page()


# --- Social share cards: og:url threading (2026-09) --------------------------
def og_url_threading_problems() -> list[str]:
    from webapp import app
    return app.og_url_threading_problems()


# --- Social share cards: every Live piece has its own card (2026-09) --------
def og_card_missing_problems() -> list[str]:
    from webapp import app
    return app.og_card_missing_problems()


# --- Original content mirroring invariant (2026-09) --------------------------
# sync_original_content_article() only ever fires from the two admin save
# routes (see linklib/original_content_sync.py) — a write via any other path
# (a migration script, a future bulk edit) can leave a live row with real
# body_md and no working articles mirror, with nothing surfacing it until
# FP&A Buddy quietly fails to cite content that actually exists. Found live
# 2026-09: two rows (ai-hackathon-playbook, netsuite-mcp) sat exactly like
# this for three weeks after scripts/archive/migrate_*_content.py wrote
# body_md directly, bypassing the sync — both self-healed the moment an
# admin opened them and clicked Save, since any save re-runs the sync
# unconditionally. Deliberately kept in the routes rather than moved into
# Library.update_original_content() itself — see CLAUDE.md's Published-
# Content Ingestion entry for the full trade-off; this check is the
# alternative safety net that decision requires: a real, no-judgment-call,
# mechanically-enforced invariant, the same shape as hub_nav_orphan_problems
# above, not a dated manual attestation like the pricing/model-freshness
# banners on /admin/checks.
#
# 2026-09 durability follow-up: extended to also catch DRIFT, not just a
# missing mirror outright — a row whose mirrored_article_id points at a
# real articles row, but whose content no longer matches the current
# body_md (see Library.list_drifted_original_content_mirrors' own
# docstring for why this compares content, not updated_at timestamps).
# Every flagged row, missing-mirror or drifted alike, names the exact fix:
# open it at /admin/thought-leadership/original/{id}/edit and click Save,
# which re-runs sync_original_content_article() unconditionally.
#
# Same 2026-09 pass also closed the actual write-time gap this check used
# to be the only defense against: every voice review queue action that can
# write original_content.<column> (Edit, Revert, the bulk "Replace
# ampersands with and" apply route) now fires sync_original_content_
# article() itself, synchronously, at the point of write — see
# webapp.app._resolve_voice_item_action's own docstring. This check is a
# safety net for a write path that ISN'T wired to the sync (a future
# script, a future admin route), not the fix for the queue's own actions
# any more — detection alone was never a substitute for firing the sync,
# and a real production finding (original_content.body_md id 36, sitting
# open in the review queue with no corresponding sync) is what surfaced
# the gap.
def original_content_mirror_problems() -> list[str]:
    from webapp.app import _lib
    lib = _lib()
    try:
        missing = lib.list_unmirrored_original_content()
        drifted = lib.list_drifted_original_content_mirrors()
    finally:
        lib.close()
    problems = [
        f"{r['slug']!r} (id {r['id']}) has body_md but no working articles "
        f"mirror. Fix: open /admin/thought-leadership/original/{r['id']}/edit "
        f"and click Save"
        for r in missing
    ]
    problems += [
        f"{r['slug']!r} (id {r['id']}) mirror is stale: its articles row "
        f"no longer matches body_md. Fix: open "
        f"/admin/thought-leadership/original/{r['id']}/edit and click Save"
        for r in drifted
    ]
    return problems


# --- Voice review queue (2026-09) --------------------------------------------
# Informational, not pass/fail — an open queue count of >0 isn't a bug, it's
# work waiting on Brian, the same reason the pricing/model-freshness banners
# below the pass/fail list aren't rows in run_all() either. This IS a row
# (ok=None always) rather than a banner, per the brief's own ask: "report the
# open queue count... while still reporting that the check ran even at zero"
# — the DbScanReport execution-stats lesson (a report has to say it ran
# before "0 open" means anything) applies here too.
#
# This count and the Database-backed copy scan's own violation count
# (_db_copy_scan_banner, webapp/app.py) will LEGITIMATELY differ, and that's
# not a bug either time: the queue also holds seed-disagreement items
# (source='startup-sync' — _seed_toolbox's per-boot re-sync of a tools/
# communities/benchmarks row finding its live value doesn't match its static
# seed source, an ordinary, expected state) and already-applied
# auto-corrections, neither of which the live DB-copy scan counts at all — it
# only ever scans CURRENT column text for a violation, with no concept of a
# queue history. Reported separately (seed_disagreement_count) so a caller
# (the /admin/checks summary) can label the two counts apart, rather than a
# real discrepancy between them reading as identical-looking-but-mismatched
# numbers for the same thing.
def homepage_status_check(now=None) -> dict:
    """Is the homepage Status note current? One settings read, no route
    renders, and deliberately not behind cached_static_check: the answer
    changes with the clock, not with the source tree."""
    from linklib import homepage_status as hs
    from webapp.app import _lib
    lib = _lib()
    try:
        stamp = lib.get_setting(hs.STATUS_REVISED_KEY)
    finally:
        lib.close()
    age = hs.status_age(stamp, now)
    return {"name": "Homepage status is current", "where": "Live + CI", "ok": not age["stale"],
            "what": f"The Status note under your photo was revised or confirmed in the last {hs.STATUS_STALE_DAYS} days. "
                    "Edit it at /admin/copy/homepage.",
            "detail": hs.check_detail(age)}


def voice_review_queue_status() -> dict:
    from webapp.app import _lib
    lib = _lib()
    try:
        n = lib.count_open_voice_review_items()
        n_seed = lib.count_open_voice_review_seed_disagreements()
    finally:
        lib.close()
    seed_note = (f" {n_seed} of them {'are' if n_seed != 1 else 'is a'} seed disagreement"
                 f"{'s' if n_seed != 1 else ''}, which is normal." if n_seed else "")
    return {"name": "Voice review queue", "where": "Live + CI", "ok": None,
            "count": n, "seed_disagreement_count": n_seed,
            "what": "Voice fixes and findings waiting for a person to review. Its count won't match "
                    "Database copy, since it also holds auto-fixes and seed-list conflicts.",
            "detail": f"{n} open item{'s' if n != 1 else ''} awaiting review.{seed_note}"
                      if n else "0 open items. Nothing is waiting."}


# --- coral discipline: at most one coral moment per public page (PR 16) -----
def coral_moment_problems() -> list[str]:
    from webapp import app
    return app.coral_moment_problems()


# --- voice guide checks: live-or-default resolution (2026-09 /admin/checks --
# --- summary work) ------------------------------------------------------------
# Before this, "Voice guide names what it enforces" always checked
# VOICE_CORE_DEFAULT (the code constant), even when running live with a real
# DB connection available — so an admin edit at /admin/voice that drifted
# from the shipped default was invisible to every check on this page. Brian
# edits the live voice_core; CI only ever sees the code default (it has no
# DB to read from at all, or does, from a fresh/unseeded test fixture, which
# is functionally the same as "nothing to read"). This resolves once, shared
# by all three voice-guide-prose checks below, so they can never independently
# disagree about which text they're validating.
def _resolve_checked_voice_core() -> tuple[str, str, bool]:
    """(text, source_label, differs_from_default). Prefers the live
    voice_core setting when reachable and non-empty; falls back to
    VOICE_CORE_DEFAULT otherwise — the same degrade-gracefully contract
    every other DB-touching check in this module already uses (a fresh/CI/
    unreachable DB is not a check failure). `differs_from_default` is only
    ever True when a real live value was actually read — there's nothing to
    "differ" from when the fallback IS the default."""
    from linklib.agent import VOICE_CORE_DEFAULT
    live_text = ""
    try:
        from webapp.app import _lib
        lib = _lib()
        try:
            live_text = (lib.get_setting("voice_core") or "").strip()
        finally:
            lib.close()
    except Exception:
        live_text = ""
    if live_text:
        return live_text, "the live voice_core setting", live_text != VOICE_CORE_DEFAULT
    return VOICE_CORE_DEFAULT, "VOICE_CORE_DEFAULT", False


# --- disk space (2026-09) ----------------------------------------------------
# Found via a real incident, not a hypothetical: a script copying library.db
# ran out of space mid-copy ("No space left on device") on a volume that was
# only 58% full — it tried to write a 243M copy into 171M of free space and
# consumed the rest before failing. The volume itself was never the problem;
# the problem was that nothing anywhere reported where it stood, so the
# investigation spent an hour looking at the wrong thing before a manual
# `df -h` over SSH finally showed the real numbers — the same silent-failure
# pattern this whole voice/checks workstream exists to close, one layer below
# the application. This closes it mechanically instead: read live via
# shutil.disk_usage (never shell out to `df`), state the numbers plainly even
# when everything is green (a green row that says nothing looks identical to
# a row that never ran), and flag specifically whether there's room to run a
# classic VACUUM in place (it needs roughly the database's own size again in
# free scratch space — a real operational fact worth surfacing before a
# VACUUM is attempted mid-incident, not discovered by it failing).
_DISK_VOLUME_PATH = "/data"
_DISK_WARN_PERCENT = 75
_DISK_CRITICAL_PERCENT = 85


def disk_space_status(db_path: str | None = None) -> dict | None:
    """Volume + database-file disk usage, read live. Returns None (not a
    failure) when _DISK_VOLUME_PATH doesn't exist on this host — the
    documented Railway volume mount, present in production, absent in
    dev/test/CI/this sandbox — same "can't run here" contract as
    script_syntax_problems()/_pyflakes_problems() above, so a local dev
    session or CI run degrades gracefully instead of crashing /admin/checks
    or reporting bogus numbers for whatever local directory happens to
    exist instead of the real volume."""
    if not os.path.isdir(_DISK_VOLUME_PATH):
        return None
    total, used, free = shutil.disk_usage(_DISK_VOLUME_PATH)
    percent_used = (used / total * 100.0) if total else 0.0
    if db_path is None:
        from webapp.app import DB_PATH
        db_path = DB_PATH
    db_size = os.path.getsize(db_path) if db_path and os.path.isfile(db_path) else 0
    # A classic (non-incremental) VACUUM writes a full second copy of the
    # database before swapping it in, so it needs roughly the DB's own size
    # again in free space to run in place — not "some" free space, that much.
    can_vacuum = free >= db_size if db_size else None
    if percent_used >= _DISK_CRITICAL_PERCENT:
        level = "critical"
    elif percent_used >= _DISK_WARN_PERCENT:
        level = "warn"
    else:
        level = "ok"
    return {
        "volume_path": _DISK_VOLUME_PATH, "total": total, "used": used, "free": free,
        "percent_used": percent_used, "db_path": db_path, "db_size": db_size,
        "can_vacuum": can_vacuum, "level": level,
    }


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
    """Each check as {name, what, where ('Live + CI'|'CI'), ok (bool|None), detail}."""
    src = _app_src()
    from linklib import brand_check, voice_review
    from webapp import tasks as _tasks
    results: list[dict] = []

    bf = brand_check.findings(src)
    results.append({
        "name": "Brand standards", "where": "Live + CI", "ok": not bf,
        "what": "Every color is a brand token or a documented exception; only on-brand fonts.",
        "detail": "; ".join(bf) if bf else "All colors and fonts on palette."})

    # Both the buzzword/filler/performative sweep and the ampersand/em-dash
    # sweep run over the identical VOICE_SCANNED_FILES list — see that
    # tuple's own comment for why the two rules share one file scope.
    #
    # Cached (2026-09 test-suite-runtime PR): both scans are pure functions
    # of on-disk source (VOICE_SCANNED_FILES — Python files, never database
    # content, confirmed via mechanical_findings'/typography_findings' own
    # docstrings) — see webapp.tasks.cached_static_check's own module
    # comment for why that's safe to compute once per process.
    def _compute_vf():
        out = []
        for _path, _src in _voice_scanned_sources():
            out.extend((_path.name, rule, phrase) for rule, phrase in voice_review.mechanical_findings(_src))
        return out
    vf = _tasks.cached_static_check("voice_standards", _compute_vf)
    results.append({
        "name": "Voice standards", "where": "Live + CI", "ok": not vf,
        "what": "No banned buzzwords, filler, or performative phrases in site copy or prompts.",
        "detail": "; ".join(f"{fname} {rule}: “{phrase}”" for fname, rule, phrase in vf[:6])
                  if vf else "Copy is on-voice."})

    def _compute_tf():
        out = []
        for _path, _src in _voice_scanned_sources():
            out.extend((_path.name, rule, line, excerpt)
                      for rule, line, excerpt in voice_review.typography_findings(_src))
        return out
    tf = _tasks.cached_static_check("typography", _compute_tf)
    results.append({
        "name": "Typography (ampersands, em dashes)", "where": "Live + CI", "ok": not tf,
        "what": "Copy spells out \"and\" (terms like FP&A excepted) and never spaces an em dash.",
        "detail": "; ".join(f"{fname} {rule} (line {line}): {excerpt}" for fname, rule, line, excerpt in tf[:6])
                  if tf else "Copy follows both typographic rules."})

    # Semantic contradiction: voice_core's own prose names a word/phrase as
    # unwanted (quoted) that BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE don't
    # actually enforce. As of the 2026-09 /admin/checks summary work, this
    # validates the LIVE voice_core setting when this process can reach a
    # real DB with a real value in it — Brian edits the live guide at
    # /admin/voice, so checking only the shipped code default meant a live
    # edit could drift from what's actually enforced with nothing on this
    # page ever noticing. CI (and any fresh/unseeded DB) falls back to
    # VOICE_CORE_DEFAULT, same as before.
    checked_voice_core, voice_core_source, voice_core_differs = _resolve_checked_voice_core()
    vg = voice_review.voice_core_gap_problems(checked_voice_core)
    # State execution, not just findings (2026-09 follow-up, same fix as the
    # DB scan's stats line below) — "0 gaps" and "this row never ran" must
    # not read the same. quoted_voice_examples() is the exact candidate set
    # voice_core_gap_problems() checks, so the count can't drift from what
    # was actually checked.
    _n_checked = len(voice_review.quoted_voice_examples(checked_voice_core))
    _diff_note = (" The live voice guide differs from the code default, which is expected. "
                   "This checks the live one." if voice_core_differs else "")
    results.append({
        "name": "Voice guide names what it enforces", "where": "Live + CI", "ok": not vg,
        "what": "Every phrase the voice guide says to avoid is one the checks actually catch.",
        "detail": (f"Checked {_n_checked} quoted example{'s' if _n_checked != 1 else ''} in "
                   f"{voice_core_source}. 0 gaps.{_diff_note}" if not vg
                   else "; ".join(vg[:6]) + _diff_note)})

    # The mirror-image check, same live-or-default text: an ampersand-joined
    # acronym the guide's own prose names as PERMITTED ("FP&A, T&E, R&D, and
    # similar") that isn't actually in AMPERSAND_ACRONYMS/AMPERSAND_NAMES —
    # the scanner would flag a term the guide itself says is fine. See
    # linklib.voice_review.voice_core_ampersand_gap_problems's own docstring.
    vag = voice_review.voice_core_ampersand_gap_problems(checked_voice_core)
    results.append({
        "name": "Voice guide's permitted ampersand terms are honored", "where": "Live + CI", "ok": not vag,
        "what": "Every ampersand term the voice guide allows, like FP&A, passes the typography check.",
        "detail": (f"Checked {voice_core_source}. 0 gaps." if not vag else "; ".join(vag[:6]))})

    # Typography and invisible characters ARE checked against the voice
    # guide's own prose (unlike banned words/filler/performative, which stay
    # exempt — the rubric legitimately quotes those as examples of what to
    # avoid). Neither exemption reason applies to a spaced em dash, a bare
    # ampersand, or an invisible character: the guide's own prose shouldn't
    # break the typography rules it teaches, and a model imitates the style
    # of its own instructions. Uses typography_findings_plain (a single
    # plain-text value, not Python source) since voice_core is prose, not a
    # Python string literal to extract from a file.
    vt = (voice_review.typography_findings_plain(checked_voice_core)
          + voice_review.invisible_character_findings(checked_voice_core))
    results.append({
        "name": "Voice guide follows its own typography rules", "where": "Live + CI", "ok": not vt,
        "what": "The voice guide follows its own rules. Claude copies the style of its instructions.",
        "detail": (f"Checked {voice_core_source}. Clean." if not vt
                   else "; ".join(f"{rule}: {excerpt}" for rule, excerpt in vt[:6]))})

    dp = db_path_example_problems()
    results.append({
        "name": "Production script examples use an absolute --db", "where": "Live + CI", "ok": not dp,
        "what": "Production script examples point at /data/library.db. A relative path can quietly create an empty database.",
        "detail": "; ".join(dp[:6]) if dp else
                  f"Every production example is absolute. {len(DB_PATH_ALLOWLIST)} local-only examples are allowlisted."})

    ol = brand_check.outbound_link_problems(src)
    results.append({
        "name": "Outbound links open in a new tab", "where": "Live + CI", "ok": not ol,
        "what": "Every link leaving bmweis.com carries target=\"_blank\" rel=\"noopener\"; internal links stay same-tab.",
        "detail": "; ".join(ol[:6]) if ol
                  else "Every hand-written outbound link opens in a new tab."})

    rc = _tasks.cached_static_check("reading_column", lambda: brand_check.reading_column_problems(src))
    results.append({
        "name": "Reading column holds its tables and grids", "where": "Live + CI", "ok": not rc,
        "what": "On a page with a narrow reading column, a table or grid sits inside the column, not beside it.",
        "detail": "; ".join(rc[:6]) if rc
                  else "No table or grid sits beside a reading column in the page source."})

    ah = _tasks.cached_static_check("actions_header", lambda: brand_check.actions_header_problems(src))
    results.append({
        "name": "Table actions column is headed Actions", "where": "Live + CI", "ok": not ah,
        "what": "A table whose last column holds row buttons heads it Actions, and no table header is left blank.",
        "detail": "; ".join(ah[:6]) if ah
                  else "Every table header is named, and every button column in view of the scan is headed Actions."})

    from webapp.app import _CSS as _app_css
    # Cached (2026-09): both halves are pure functions of on-disk source —
    # table_override_problems specifically is ~6.3s of run_all()'s ~15s
    # total (a regex scan of the full webapp/app.py source, see the PR
    # description), the single most expensive individual check.
    tbf = _tasks.cached_static_check(
        "table_format",
        lambda: brand_check.table_standard_problems(_app_css) + brand_check.table_override_problems(src))
    results.append({
        "name": "One table format", "where": "Live + CI", "ok": not tbf,
        "what": "Every table uses the one standard: light-blue header, white rows, row lines, navy-light rounded border.",
        "detail": "; ".join(tbf[:6]) if tbf
                  else f"The standard is whole and nothing overrides it ({len(brand_check.TABLE_SCOPE_EXCLUSIONS)} approved exceptions)."})

    op = open_source_problems()
    results.append({
        "name": "Open-source showcase in sync", "where": "Live + CI", "ok": not op,
        "what": "Every library the site uses is credited on Open source, and nothing removed is still credited.",
        "detail": "; ".join(op) if op else "Showcase matches requirements."})

    bd = brand_docs_problems()
    results.append({
        "name": "BRAND.md §7 in sync", "where": "Live + CI", "ok": not bd,
        "what": "BRAND.md's color table matches the site's real CSS.",
        "detail": "; ".join(bd) if bd else "BRAND.md matches the live CSS."})

    hn = hub_nav_orphan_problems()
    results.append({
        "name": "Hub-nav orphans", "where": "Live + CI", "ok": not hn,
        "what": "Every admin page has a card on /admin.",
        "detail": "; ".join(hn) if hn else "Every admin route has a hub-nav card."})

    ac = ai_config_orphan_problems()
    results.append({
        "name": "AI config consolidated", "where": "Live + CI", "ok": not ac,
        "what": "AI settings live in one place: /admin/system/ai.",
        "detail": "; ".join(ac) if ac else "AI configuration lives only at /admin/system/ai."})

    ou = og_url_threading_problems()
    results.append({
        "name": "og:url threading", "where": "Live + CI", "ok": not ou,
        "what": "Every public page reports its own URL when shared.",
        "detail": "; ".join(ou) if ou else "Every public route threads request into og:url."})

    om = original_content_mirror_problems()
    results.append({
        "name": "Original content mirrored for retrieval", "where": "Live + CI", "ok": not om,
        "what": "Every original piece with a body is mirrored, so FP&A Buddy can find and cite it.",
        "detail": "; ".join(om) if om else "Every row with body_md has a valid mirror."})

    og = og_card_missing_problems()
    results.append({
        "name": "Every live piece has a share card", "where": "Live + CI", "ok": not og,
        "what": "Every live original piece has its own share card.",
        "detail": "; ".join(og) if og else "Every Live piece has its own share card."})

    results.append(voice_review_queue_status())
    results.append(homepage_status_check())

    cm = coral_moment_problems()
    results.append({
        "name": "Coral discipline (one moment per page)", "where": "Live + CI", "ok": not cm,
        "what": "Each public page uses coral at most once. Checks inline styles on signed-out pages only.",
        "detail": "; ".join(cm) if cm else "Every checked page has at most one coral moment."})

    # Cached (2026-09): a pure function of the .py files on disk under
    # linklib/webapp/scripts — see webapp.tasks.cached_static_check's
    # module comment. The None-when-not-installed case caches correctly
    # too (cached_static_check keys on membership, not truthiness).
    pf = _tasks.cached_static_check("pyflakes", _pyflakes_problems)
    if pf is None:
        results.append({
            "name": "Dead code / unused imports", "where": "CI", "ok": None,
            "what": "No unused code or imports.",
            "detail": "Runs automatically as part of every code check."})
    else:
        results.append({
            "name": "Dead code / unused imports", "where": "Live + CI", "ok": not pf,
            "what": "No unused code or imports.",
            "detail": "; ".join(pf[:6]) if pf else "No unused imports or dead code."})

    # Cached (2026-09): a pure function of the shared _JS source constants
    # (webapp.app module attributes, loaded from source at import time) —
    # see webapp.tasks.cached_static_check's module comment.
    sp = _tasks.cached_static_check("script_syntax", script_syntax_problems)
    if sp is None:
        results.append({
            "name": "Script blocks are valid JavaScript", "where": "CI", "ok": None,
            "what": "Every shared script block is valid JavaScript.",
            "detail": "Runs automatically as part of every code check."})
    else:
        results.append({
            "name": "Script blocks are valid JavaScript", "where": "Live + CI", "ok": not sp,
            "what": "Every shared script block is valid JavaScript.",
            "detail": "; ".join(sp) if sp else "Every shared script block parses clean."})

    results.append({
        "name": "Everything else", "where": "CI", "ok": None,
        "what": "Everything else the test suite covers: access, auth, dedupe, publish dates, tagging, users.",
        "detail": "Runs the whole suite (including the three above) on every code change."})

    results.append({
        "name": "Secret scan", "where": "CI", "ok": None,
        "what": "No live secrets committed to the repo.",
        "detail": "Runs separately, on every code change."})

    return results
