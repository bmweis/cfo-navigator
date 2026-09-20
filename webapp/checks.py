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
# (VOICE_CORE_DEFAULT's own "- Avoid: ... delve, robust, seamless, ..."
# line) — but two of its OTHER rubric lines ("No performative openers or
# closers (...)", "No filler (...)") use a different marker shape the
# current mask doesn't cover, so adding agent.py to this list today would
# still need new markers, not just a one-line addition. Flagged here so a
# future sweep doesn't have to rediscover it.
#
# Kept as a real list, not a single hardcoded path, so a future file that
# DOES belong here is a one-line addition, not a refactor.
VOICE_SCANNED_FILES = (_APP_PY, _ENRICH_PY, _FEATURE_SCAN_PY)


def _voice_scanned_sources() -> list[tuple[pathlib.Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in VOICE_SCANNED_FILES]


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
def original_content_mirror_problems() -> list[str]:
    from webapp.app import _lib
    lib = _lib()
    try:
        rows = lib.list_unmirrored_original_content()
    finally:
        lib.close()
    return [f"{r['slug']!r} (id {r['id']}) has body_md but no working articles mirror" for r in rows]


# --- Voice review queue (2026-09) --------------------------------------------
# Informational, not pass/fail — an open queue count of >0 isn't a bug, it's
# work waiting on Brian, the same reason the pricing/model-freshness banners
# below the pass/fail list aren't rows in run_all() either. This IS a row
# (ok=None always) rather than a banner, per the brief's own ask: "report the
# open queue count... while still reporting that the check ran even at zero"
# — the DbScanReport execution-stats lesson (a report has to say it ran
# before "0 open" means anything) applies here too.
def voice_review_queue_status() -> dict:
    from webapp.app import _lib
    lib = _lib()
    try:
        n = lib.count_open_voice_review_items()
    finally:
        lib.close()
    return {"name": "Voice review queue", "where": "Live + CI", "ok": None,
            "what": "Every _voice_fix correction and unresolved scanner finding, queued for human "
                    "review at /admin/voice/review-queue rather than silently applied or reported "
                    "only as a count.",
            "detail": f"{n} open item{'s' if n != 1 else ''} awaiting review."
                      if n else "0 open items — the queue ran and found nothing pending."}


# --- coral discipline: at most one coral moment per public page (PR 16) -----
def coral_moment_problems() -> list[str]:
    from webapp import app
    return app.coral_moment_problems()


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
    results: list[dict] = []

    bf = brand_check.findings(src)
    results.append({
        "name": "Brand standards", "where": "Live + CI", "ok": not bf,
        "what": "Every color is a brand token or a documented exception; only on-brand fonts.",
        "detail": "; ".join(bf) if bf else "All colors and fonts on palette."})

    # Both the buzzword/filler/performative sweep and the ampersand/em-dash
    # sweep run over the identical VOICE_SCANNED_FILES list — see that
    # tuple's own comment for why the two rules share one file scope.
    vf = []
    for _path, _src in _voice_scanned_sources():
        vf.extend((_path.name, rule, phrase) for rule, phrase in voice_review.mechanical_findings(_src))
    results.append({
        "name": "Voice standards", "where": "Live + CI", "ok": not vf,
        "what": "No banned buzzwords, filler, or performative phrases in the scanned source's UI/prompt copy.",
        "detail": "; ".join(f"{fname} {rule}: “{phrase}”" for fname, rule, phrase in vf[:6])
                  if vf else "Copy is on-voice."})

    tf = []
    for _path, _src in _voice_scanned_sources():
        tf.extend((_path.name, rule, line, excerpt)
                  for rule, line, excerpt in voice_review.typography_findings(_src))
    results.append({
        "name": "Typography (ampersands, em dashes)", "where": "Live + CI", "ok": not tf,
        "what": "UI copy spells out \"and\" (except FP&A and friends) and never spaces an em dash.",
        "detail": "; ".join(f"{fname} {rule} (line {line}): {excerpt}" for fname, rule, line, excerpt in tf[:6])
                  if tf else "Copy follows both typographic rules."})

    # Semantic contradiction: voice_core's own prose names a word/phrase as
    # unwanted (quoted) that BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE don't
    # actually enforce. Deliberately checks VOICE_CORE_DEFAULT (the code
    # constant), not the live DB-backed `voice_core` setting — this stays a
    # CI-safe, source-only check for the same reason the mechanical lists
    # themselves stay source-only: CI has no route to the live database. An
    # admin edit to the live voice_core prose that names a new example isn't
    # caught by this row — only a drift in the code default is.
    from linklib.agent import VOICE_CORE_DEFAULT
    vg = voice_review.voice_core_gap_problems(VOICE_CORE_DEFAULT)
    # State execution, not just findings (2026-09 follow-up, same fix as the
    # DB scan's stats line below) — "0 gaps" and "this row never ran" must
    # not read the same. quoted_voice_examples() is the exact candidate set
    # voice_core_gap_problems() checks, so the count can't drift from what
    # was actually checked.
    _n_checked = len(voice_review.quoted_voice_examples(VOICE_CORE_DEFAULT))
    results.append({
        "name": "Voice guide names what it enforces", "where": "Live + CI", "ok": not vg,
        "what": "Every 2+-word phrase VOICE_CORE_DEFAULT quotes as an example to avoid is actually "
                "in BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE — the rubric never promises a rejection "
                "the mechanical lists don't back up.",
        "detail": (f"Checked {_n_checked} quoted example{'s' if _n_checked != 1 else ''} in "
                   f"VOICE_CORE_DEFAULT. 0 gaps." if not vg else "; ".join(vg[:6]))})

    ol = brand_check.outbound_link_problems(src)
    results.append({
        "name": "Outbound links open in a new tab", "where": "Live + CI", "ok": not ol,
        "what": "Every link leaving bmweis.com carries target=\"_blank\" rel=\"noopener\"; internal links stay same-tab.",
        "detail": "; ".join(ol[:6]) if ol
                  else "Every hand-written outbound link opens in a new tab."})

    op = open_source_problems()
    results.append({
        "name": "Open-source showcase in sync", "where": "Live + CI", "ok": not op,
        "what": "Every code library the site uses is credited on Open source, and nothing credited there has actually been removed.",
        "detail": "; ".join(op) if op else "Showcase matches requirements."})

    bd = brand_docs_problems()
    results.append({
        "name": "BRAND.md §7 in sync", "where": "Live + CI", "ok": not bd,
        "what": "The color table in BRAND.md always matches the site's real CSS—never hand-copied out of sync.",
        "detail": "; ".join(bd) if bd else "BRAND.md matches the live CSS."})

    hn = hub_nav_orphan_problems()
    results.append({
        "name": "Hub-nav orphans", "where": "Live + CI", "ok": not hn,
        "what": "Every real /admin route has a corresponding hub-nav card—nothing reachable only by guessing the URL.",
        "detail": "; ".join(hn) if hn else "Every admin route has a hub-nav card."})

    ac = ai_config_orphan_problems()
    results.append({
        "name": "AI config consolidated", "where": "Live + CI", "ok": not ac,
        "what": "AI settings only live in one place (/admin/system/ai)—nothing left over from the pages that used to hold them.",
        "detail": "; ".join(ac) if ac else "AI configuration lives only at /admin/system/ai."})

    ou = og_url_threading_problems()
    results.append({
        "name": "og:url threading", "where": "Live + CI", "ok": not ou,
        "what": "Every public page's _page() call passes request=request—so og:url reports its own real URL, never a silent fallback to the homepage.",
        "detail": "; ".join(ou) if ou else "Every public route threads request into og:url."})

    om = original_content_mirror_problems()
    results.append({
        "name": "Original content mirrored for retrieval", "where": "Live + CI", "ok": not om,
        "what": "Every original_content row with real body_md has a working articles mirror, so FP&A Buddy can find and cite it.",
        "detail": "; ".join(om) if om else "Every row with body_md has a valid mirror."})

    og = og_card_missing_problems()
    results.append({
        "name": "Every live piece has a share card", "where": "Live + CI", "ok": not og,
        "what": "Every Live original_content piece has its own committed webapp/static/og/<slug>.png—the publish gate stops new occurrences, this catches a card that goes missing afterward.",
        "detail": "; ".join(og) if og else "Every Live piece has its own share card."})

    results.append(voice_review_queue_status())

    cm = coral_moment_problems()
    results.append({
        "name": "Coral discipline (one moment per page)", "where": "Live + CI", "ok": not cm,
        "what": "No public page overuses the coral accent color—at most one coral moment per page. "
                "Best-effort: only checks signed-out pages, and only inline styles—a coral moment set through a CSS class wouldn't be caught.",
        "detail": "; ".join(cm) if cm else "Every checked page has at most one coral moment."})

    pf = _pyflakes_problems()
    if pf is None:
        results.append({
            "name": "Dead code / unused imports", "where": "CI", "ok": None,
            "what": "No leftover, unused code anywhere in the codebase.",
            "detail": "Runs automatically as part of every code check."})
    else:
        results.append({
            "name": "Dead code / unused imports", "where": "Live + CI", "ok": not pf,
            "what": "No leftover, unused code anywhere in the codebase.",
            "detail": "; ".join(pf[:6]) if pf else "No unused imports or dead code."})

    sp = script_syntax_problems()
    if sp is None:
        results.append({
            "name": "Script blocks are valid JavaScript", "where": "CI", "ok": None,
            "what": "Every reusable bit of JavaScript on the site is syntactically valid.",
            "detail": "Runs automatically as part of every code check."})
    else:
        results.append({
            "name": "Script blocks are valid JavaScript", "where": "Live + CI", "ok": not sp,
            "what": "Every reusable bit of JavaScript on the site is syntactically valid.",
            "detail": "; ".join(sp) if sp else "Every shared script block parses clean."})

    results.append({
        "name": "Everything else", "where": "CI", "ok": None,
        "what": "Everything else the suite covers—access tiers, auth, dedupe, publish dates, tagging, users.",
        "detail": "Runs the whole suite (including the three above) on every code change."})

    results.append({
        "name": "Secret scan", "where": "CI", "ok": None,
        "what": "No verified, live secrets committed anywhere in the repo.",
        "detail": "Runs separately, on every code change."})

    return results
