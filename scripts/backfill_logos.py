#!/usr/bin/env python3
"""Phase D logo backfill: fetches a company logo for every tool/community
still missing one, via Brandfetch's **Brand API**
(`https://api.brandfetch.io/v2/brands/domain/{domain}`, `Authorization:
Bearer <BRANDFETCH_API_KEY>`) — NOT the free CDN Logo API
(`cdn.brandfetch.io?c={BRANDFETCH_CLIENT_ID}`) the original Phase D
investigation assumed. That CDN product is browser-embed-only and
explicitly disallows programmatic/backend access per Brandfetch's own docs
and ToS; a follow-up investigation confirmed our dry-run against it (see
scripts/report_brandfetch_coverage.py) returned a uniform blocked-request
response for all 216 records, not real "no logo" misses. The Brand API is
the correct, sanctioned product for this — a real JSON response with logo
asset URLs, meant for exactly this kind of one-time server-side fetch — but
its free tier is only 100 requests/month, hence the --limit default below
and the 3-monthly-batch plan this script is designed around.

WHERE FILES ARE SAVED — a deliberate deviation from the original build
instruction. The instruction said `webapp/static/logos/{slug}.{ext}`, but
`webapp/static/` ships baked into the Docker image and does NOT persist
across a Railway deploy (see webapp/app.py's _SCREENSHOT_DIR comment — the
exact same durability problem was already solved once, for homepage
screenshots, by storing next to library.db on the persistent volume
instead). A logo downloaded there via `railway ssh` would be silently wiped
by the very next `git push`-triggered deploy — a severe problem for a
process explicitly designed to accumulate results across 3 separate monthly
runs. So this script mirrors the screenshot precedent instead: files land
under a `logos/` directory next to the database file itself (`/data/logos`
in production, alongside `/data/library.db`), split into `logos/tools/` and
`logos/communities/` subdirectories — Software and Communities slugs are a
separate namespace but CAN collide on the same value (Phase 0 found
airbase/datarails/rillet shared across both — see _COMMUNITY_SCREENSHOT_DIR
in webapp/app.py), so a single shared "{slug}.ext" scheme would silently
let one type's logo overwrite the other's on a colliding slug. The stored
`logo_path` column value (e.g. "logos/tools/abacum.svg") is relative to that
directory's parent, not to webapp/static/ — Phase F (rendering logos on
profile pages/cards, out of scope here) decides the actual serving route
when it's built, the same way the screenshot feature serves from a
dedicated `GET /tools/software/screenshot/{filename}` route rather than
`/static/{filename}`.

Selection: every tool/community where logo_path is still empty — a row that
already has one (e.g. a future manual-upload admin flow) is never touched,
so this script and manual curation can safely coexist. Software tools are
processed first, then communities, each in a stable `id` order, matching
the priority Brian asked for across the 3 planned batches.

Safe by default: with no flags, this ONLY reports which records WOULD be
processed — no Brand API calls are made (so a preview never spends quota),
no files are saved, no DB writes happen. Pass --apply to actually fetch,
download, and write. Per the write-then-read-back standing practice, an
--apply run re-SELECTs every row it touched after the run and asserts
logo_path took the expected value.

Usage:
    python -m scripts.backfill_logos --db library.db                 # preview (default limit 90)
    python -m scripts.backfill_logos --db library.db --apply          # fetch + save for real
    python -m scripts.backfill_logos --db library.db --limit 50 --apply
    python -m scripts.backfill_logos --db library.db --status         # coverage report only, no fetch

Requires BRANDFETCH_API_KEY in the environment for --apply (not required
for --status or a preview run). Distinct from BRANDFETCH_CLIENT_ID, which is
the unrelated CDN-embed credential and is not used here.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from scripts.report_brandfetch_coverage import extract_domain

BRAND_API_URL = "https://api.brandfetch.io/v2/brands/domain/{domain}"
REQUEST_TIMEOUT = 15  # seconds
DEFAULT_LIMIT = 90  # safety margin below the hard 100/month free-tier cap
DEFAULT_DELAY = 0.25  # seconds between Brand API calls — sanctioned use, but no reason to hammer it

# Subdirectory per record type, to avoid a slug collision between a tool and
# a community silently overwriting each other's logo file (see module docstring).
_DIR_BY_KIND = {"tool": "tools", "community": "communities"}

# Format preference, carried over from the original Phase D investigation's
# recommendation: SVG scales cleanly across card-size and profile-page-size
# renders with zero quality loss; PNG is the fallback when a brand has no SVG.
_FORMAT_PREFERENCE = ("svg", "png")

# Logo "theme" preference: our pages sit on a light background (#F5F4EF,
# BRAND.md), so a logo variant designed for light backgrounds reads better
# than one designed for dark. Not every brand publishes both.
_THEME_PREFERENCE = ("light", "dark")


def _logos_root(db_path: str) -> str:
    """The persistent directory logo files are saved under: next to the
    database file, mirroring _SCREENSHOT_DIR/_COMMUNITY_SCREENSHOT_DIR in
    webapp/app.py (same Railway volume in production, no new mount needed)."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "logos")


def _select_candidates(lib: Library, limit: int) -> list[tuple[str, sqlite3.Row]]:
    """Every tool then every community still missing a logo, each in a
    stable id order, truncated to `limit`. Deliberately not scoped to
    approved-only — a pending tool's logo is harmless to have ready before
    approval, and logo_path is orthogonal to the approval workflow."""
    tools = lib.conn.execute(
        "SELECT id, name, slug, url FROM tools WHERE logo_path='' OR logo_path IS NULL ORDER BY id"
    ).fetchall()
    communities = lib.conn.execute(
        "SELECT id, name, slug, url FROM communities WHERE logo_path='' OR logo_path IS NULL ORDER BY id"
    ).fetchall()
    queue = [("tool", r) for r in tools] + [("community", r) for r in communities]
    return queue[:limit] if limit else queue


def _best_logo_asset(data: dict) -> tuple[str, str] | None:
    """Given a Brand API /v2/brands/domain/{domain} response body, pick the
    best logo asset: prefer the "logo" type (primary brand mark) over
    "icon"/"symbol", prefer a "light"-theme variant, prefer SVG over PNG —
    falling back gracefully at each step since not every brand publishes
    every combination. Returns (src_url, format_ext) or None if the response
    has no usable logo asset at all."""
    logos = data.get("logos") or []
    if not logos:
        return None

    def type_rank(logo: dict) -> int:
        return 0 if logo.get("type") == "logo" else 1

    def theme_rank(logo: dict) -> int:
        theme = logo.get("theme")
        try:
            return _THEME_PREFERENCE.index(theme)
        except ValueError:
            return len(_THEME_PREFERENCE)  # unknown/missing theme sorts last, not first

    ordered = sorted(logos, key=lambda logo: (type_rank(logo), theme_rank(logo)))
    for fmt_pref in _FORMAT_PREFERENCE:
        for logo in ordered:
            for fmt in logo.get("formats") or []:
                if (fmt.get("format") or "").lower() == fmt_pref and fmt.get("src"):
                    return fmt["src"], fmt_pref
    return None


def _fetch_logo_asset(domain: str, api_key: str, session: requests.Session) -> tuple[tuple[str, str] | None, str | None]:
    """Calls the Brand API for one domain. Returns ((src_url, ext), None) on
    success, or (None, reason) on any kind of miss/failure. `reason` starting
    with "QUOTA" signals the caller should stop the whole run, not just skip
    this record — see main()'s handling."""
    url = BRAND_API_URL.format(domain=domain)
    try:
        resp = session.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return None, f"request error: {exc}"

    if resp.status_code == 429:
        return None, "QUOTA: 429 rate-limited/quota-exceeded — stopping the run, not just this record"
    if resp.status_code == 404:
        return None, "404 — Brandfetch has no record for this domain"
    if resp.status_code != 200:
        return None, f"{resp.status_code} {resp.text[:200]!r}"

    try:
        data = resp.json()
    except ValueError:
        return None, "non-JSON response"

    asset = _best_logo_asset(data)
    if not asset:
        return None, "200 OK but no usable svg/png logo asset in the response"
    return asset, None


def _download_asset(src_url: str, dest_path: str, session: requests.Session) -> None:
    resp = session.get(src_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(resp.content)


def _pct(done: int, total: int) -> str:
    return f"{100.0 * done / total:.1f}%" if total else "n/a"


def _print_status(lib: Library) -> None:
    tool_total = lib.conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    tool_done = lib.conn.execute(
        "SELECT COUNT(*) FROM tools WHERE logo_path!='' AND logo_path IS NOT NULL"
    ).fetchone()[0]
    community_total = lib.conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
    community_done = lib.conn.execute(
        "SELECT COUNT(*) FROM communities WHERE logo_path!='' AND logo_path IS NOT NULL"
    ).fetchone()[0]

    print("Logo backfill status")
    print("=" * 60)
    print(f"Tools:       {tool_done}/{tool_total} have a logo ({_pct(tool_done, tool_total)})")
    print(f"Communities: {community_done}/{community_total} have a logo ({_pct(community_done, community_total)})")
    total, done = tool_total + community_total, tool_done + community_done
    print(f"Overall:     {done}/{total} have a logo ({_pct(done, total)})")
    remaining = total - done
    if remaining:
        batches = -(-remaining // DEFAULT_LIMIT)  # ceiling division
        print(f"\n{remaining} record(s) still need a logo — roughly {batches} more "
              f"batch(es) at the default --limit {DEFAULT_LIMIT}.")
    else:
        print("\nEvery tool and community has a logo.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                     help=f"max records to process this run (default {DEFAULT_LIMIT})")
    ap.add_argument("--apply", action="store_true",
                     help="Actually call the Brand API, download assets, and write to the DB. "
                          "Without this flag, only a preview is printed — no API calls, no writes.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                     help=f"seconds to sleep between Brand API calls (default {DEFAULT_DELAY})")
    ap.add_argument("--status", action="store_true",
                     help="print logo-coverage counts and exit — no selection, no fetch, no writes")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        if args.status:
            _print_status(lib)
            return 0

        candidates = _select_candidates(lib, args.limit)
        if not candidates:
            print("No tools or communities are missing a logo — nothing to do.")
            return 0

        print(f"{len(candidates)} record(s) selected (limit={args.limit}), software tools first:\n")
        for kind, row in candidates:
            print(f"  [{kind:9s}] id={row['id']:>4} {row['name']!r} ({row['url']})")

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no Brand API calls made, no files saved, no DB writes.\n"
                "This is exactly the record list --apply would process, in the same order.\n"
                "Whether each one actually resolves a logo can only be known by calling the\n"
                "Brand API for real, which this preview deliberately skips so a dry run never\n"
                "spends any of the 100/month free-tier quota. Re-run with --apply to fetch for real."
            )
            return 0

        api_key = os.environ.get("BRANDFETCH_API_KEY")
        if not api_key:
            print("\nERROR: BRANDFETCH_API_KEY is not set in the environment "
                  "(this is the Brand API Bearer token, distinct from BRANDFETCH_CLIENT_ID).",
                  file=sys.stderr)
            return 1

        logos_root = _logos_root(args.db)
        print(f"\nSaving downloaded logos under: {logos_root}\n")

        session = requests.Session()
        updated: list[tuple[str, int, str, str]] = []  # (kind, id, name, logo_path)
        failed: list[tuple[str, str, str]] = []  # (kind, name, reason)
        quota_hit = False

        for i, (kind, row) in enumerate(candidates, start=1):
            name, slug, url = row["name"], row["slug"], row["url"]
            prefix = f"[{i}/{len(candidates)}] {kind:9s} {name!r}"

            domain = extract_domain(url)
            if not domain:
                failed.append((kind, name, "unparseable url in our own data"))
                print(f"{prefix}: SKIP (unparseable url={url!r})")
                continue

            asset, err = _fetch_logo_asset(domain, api_key, session)
            if err:
                if err.startswith("QUOTA"):
                    print(f"{prefix} ({domain}): STOPPING — {err}")
                    quota_hit = True
                    break
                failed.append((kind, name, err))
                print(f"{prefix} ({domain}): MISS — {err}")
            else:
                src_url, ext = asset
                rel_path = f"logos/{_DIR_BY_KIND[kind]}/{slug}.{ext}"
                dest_path = os.path.join(logos_root, _DIR_BY_KIND[kind], f"{slug}.{ext}")
                try:
                    _download_asset(src_url, dest_path, session)
                except requests.RequestException as exc:
                    failed.append((kind, name, f"asset download failed: {exc}"))
                    print(f"{prefix} ({domain}): MISS — asset download failed: {exc}")
                else:
                    if kind == "tool":
                        lib.set_tool_logo(row["id"], rel_path)
                    else:
                        lib.set_community_logo(row["id"], rel_path)
                    updated.append((kind, row["id"], name, rel_path))
                    print(f"{prefix} ({domain}): OK — saved {rel_path}")

            if i < len(candidates):
                time.sleep(args.delay)

        processed = len(updated) + len(failed)
        print(f"\n{processed} record(s) processed" + (" (stopped early on quota)" if quota_hit else "")
              + f": {len(updated)} saved, {len(failed)} missed.")

        if updated:
            print("\n--- WRITE-THEN-READ-BACK VERIFICATION ---")
            for kind, rid, name, expected_path in updated:
                after = lib.get_tool(rid) if kind == "tool" else lib.get_community(rid)
                actual = after["logo_path"] if after else None
                assert actual == expected_path, (
                    f"{kind} id={rid} {name!r}: expected logo_path={expected_path!r}, got {actual!r} — "
                    "the UPDATE did not take."
                )
                print(f"  [{kind}] {name!r} (id={rid}): logo_path={actual!r} confirmed")

        if failed:
            print("\n--- MISSES (no logo saved, logo_path left empty for a future retry) ---")
            for kind, name, reason in failed:
                print(f"  [{kind}] {name}: {reason}")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
