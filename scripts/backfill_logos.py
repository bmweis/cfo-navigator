#!/usr/bin/env python3
"""Phase D logo backfill: fetches a company logo for every tool/community
still missing one, via Logo.dev's free image endpoint
(`https://img.logo.dev/{domain}`, `?token=<LOGODEV_API_KEY>`).

LOGO SOURCE HISTORY (2026-09) — this script originally used Brandfetch's
Brand API. Brandfetch's free tier was a one-time, non-resetting 100-credit
allotment, confirmed exhausted for good. Logo.dev replaced it as the
active source: see `linklib/logodev.py`'s own module docstring for the
full reasoning, including why this is a straight swap rather than a
Brandfetch-then-Logo.dev cascade. `linklib/brandfetch.py` is left
untouched and unused by default, kept as a dormant reference in case
Brandfetch credits are ever restored — restoring it is a matter of
switching this script's import back, not reconstructing anything. Logo.dev's
free tier is 500K requests/month with no credit card required, so the
old 100/month batching discipline no longer applies here — a full
catalog run fits comfortably in one pass.

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
processed — no Logo.dev calls are made (so a preview never spends
anything), no files are saved, no DB writes happen. Pass --apply to
actually fetch, download, and write. Per the write-then-read-back standing
practice, an --apply run re-SELECTs every row it touched after the run and
asserts logo_path took the expected value.

Usage:
    python -m scripts.backfill_logos --db /data/library.db                 # preview (default limit 500)
    python -m scripts.backfill_logos --db /data/library.db --apply          # fetch + save for real
    python -m scripts.backfill_logos --db /data/library.db --limit 50 --apply
    python -m scripts.backfill_logos --db /data/library.db --status         # coverage report only, no fetch

Requires LOGODEV_API_KEY in the environment for --apply (not required for
--status or a preview run).
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
from linklib.logodev import extract_domain, fetch_logo_asset, download_asset

DEFAULT_LIMIT = 500  # comfortably covers the whole catalog in one run — Logo.dev's free
                      # tier is 500K requests/month, so there's no quota reason to batch
DEFAULT_DELAY = 0.25  # seconds between Logo.dev calls — polite, not required

# Subdirectory per record type, to avoid a slug collision between a tool and
# a community silently overwriting each other's logo file (see module docstring).
_DIR_BY_KIND = {"tool": "tools", "community": "communities"}


def _logos_root(db_path: str) -> str:
    """The persistent directory logo files are saved under: next to the
    database file, mirroring _SCREENSHOT_DIR/_COMMUNITY_SCREENSHOT_DIR in
    webapp/app.py (same Railway volume in production, no new mount needed)."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "logos")


def _select_candidates(lib: Library, limit: int) -> list[tuple[str, sqlite3.Row]]:
    """Every tool then every community still missing a logo, each in a
    stable id order, truncated to `limit`. Deliberately not scoped to
    approved-only — a pending tool's logo is harmless to have ready before
    approval, and logo_path is orthogonal to the approval workflow.

    Excludes logo_manual_override=1 rows explicitly (2026-08, manual logo
    override) — Library.set_tool_logo/set_community_logo already refuse to
    write over one regardless, but skipping them here too means this run
    never spends a Logo.dev call it can't use anyway."""
    tools = lib.conn.execute(
        "SELECT id, name, slug, url FROM tools "
        "WHERE (logo_path='' OR logo_path IS NULL) AND logo_manual_override=0 ORDER BY id"
    ).fetchall()
    communities = lib.conn.execute(
        "SELECT id, name, slug, url FROM communities "
        "WHERE (logo_path='' OR logo_path IS NULL) AND logo_manual_override=0 ORDER BY id"
    ).fetchall()
    queue = [("tool", r) for r in tools] + [("community", r) for r in communities]
    return queue[:limit] if limit else queue


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
                     help="Actually call Logo.dev, download assets, and write to the DB. "
                          "Without this flag, only a preview is printed — no API calls, no writes.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                     help=f"seconds to sleep between Logo.dev calls (default {DEFAULT_DELAY})")
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
                "\nPREVIEW ONLY — no Logo.dev calls made, no files saved, no DB writes.\n"
                "This is exactly the record list --apply would process, in the same order.\n"
                "Whether each one actually resolves a logo can only be known by calling\n"
                "Logo.dev for real, which this preview deliberately skips. Re-run with\n"
                "--apply to fetch for real."
            )
            return 0

        api_key = os.environ.get("LOGODEV_API_KEY")
        if not api_key:
            print("\nERROR: LOGODEV_API_KEY is not set in the environment.", file=sys.stderr)
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

            asset, err = fetch_logo_asset(domain, api_key, session)
            if err:
                if err.startswith("QUOTA"):
                    print(f"{prefix} ({domain}): STOPPING — {err}")
                    quota_hit = True
                    break
                failed.append((kind, name, err))
                print(f"{prefix} ({domain}): MISS — {err}")
            else:
                image_bytes, ext, asset_type = asset
                rel_path = f"logos/{_DIR_BY_KIND[kind]}/{slug}.{ext}"
                dest_path = os.path.join(logos_root, _DIR_BY_KIND[kind], f"{slug}.{ext}")
                try:
                    download_asset(image_bytes, dest_path)
                except OSError as exc:
                    failed.append((kind, name, f"asset save failed: {exc}"))
                    print(f"{prefix} ({domain}): MISS — asset save failed: {exc}")
                else:
                    # Defense in depth: _select_candidates already excludes
                    # logo_manual_override=1 rows, but set_tool_logo/
                    # set_community_logo re-check and refuse to write over one
                    # regardless — a row could only get here on a race with
                    # a concurrent admin edit, but the write must still no-op
                    # rather than clobber a manual correction.
                    if kind == "tool":
                        wrote = lib.set_tool_logo(row["id"], rel_path)
                    else:
                        wrote = lib.set_community_logo(row["id"], rel_path)
                    if wrote:
                        updated.append((kind, row["id"], name, rel_path))
                        print(f"{prefix} ({domain}): OK — saved {rel_path} (type={asset_type or 'unlabeled'})")
                    else:
                        print(f"{prefix} ({domain}): SKIPPED — manual logo override is active, not overwritten")

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
