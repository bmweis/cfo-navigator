#!/usr/bin/env python3
"""Logo Tile Fit fix (2026-09) — re-fetches a square logo asset for every
tool/community whose CURRENT on-disk logo is a lopsided (wordmark-shaped)
wide/tall image (see scripts/audit_tool_logo_dimensions.py for the full
root-cause story: an audit found ~48 assets (Airbase 4:1, Airwallex 7.3:1,
NetSuite 14:1, ...) rendering as thin slivers inside the site's fixed-square
logo tiles because the fetch logic in use at the time always preferred the
wordmark shape).

SOURCE (2026-09): fetches via linklib/logodev.py, the same Logo.dev-backed
module scripts/backfill_logos.py and webapp/app.py's `_live_refetch_logo`
already switched to — Brandfetch's Brand API free tier is confirmed
exhausted for good, so linklib/brandfetch.py stays dormant/unused (see its
own module docstring). Logo.dev's free image endpoint is, per Logo.dev's
own docs, already scoped to return the square icon/symbol — there's no
wordmark-vs-icon ranking left to do the way Brandfetch's Brand API needed.

WHY THIS SCRIPT DOESN'T READ /data/logo_audit.csv: the original build brief
assumed re-running from that CSV's flagged list, but a CSV is a point-in-
time snapshot that goes stale the moment anything on disk changes (a manual
override, a prior partial run, ...). This script instead re-derives the
candidate set LIVE, at run time, by reusing
scripts/audit_tool_logo_dimensions.py's own dimension-probing/flagging
logic against whatever's actually on disk right now — always correct
against current state, and it naturally excludes a row someone already
fixed by hand since the audit last ran.

SCOPE, DELIBERATELY NARROW: only the "lopsided" (aspect-ratio) flag is in
scope here — never "undersized" (a low-resolution raster, a genuinely
different problem: no amount of re-fetching fixes a source image that's
just small; see scripts/audit_tool_logo_dimensions.py's own two-signal
model) and never "missing-file"/"unreadable" (different failure modes
entirely). Cube and Kintsugi — the two known genuinely-undersized rasters
from the original investigation — are structurally out of scope because
they're not flagged "lopsided" in the first place, and are ALSO skipped by
name defensively as a second, explicit guard, per instruction. Neither is
re-fetched by this script under any circumstance; they stay on Brian's
existing manual logo-override worklist.

NEVER TOUCHES A MANUAL OVERRIDE: rows with logo_manual_override=1 are
excluded from selection, and linklib.logodev's shared
set_tool_logo/set_community_logo (called here exactly as backfill_logos.py
and the admin "Revert & re-fetch" button call them) refuse to write over
one regardless, as defense in depth.

Safe by default, same convention as scripts/backfill_logos.py: with no
flags, this ONLY lists which records are currently flagged lopsided and
why — ZERO Logo.dev calls, so a preview never spends anything. Pass
--apply to actually call Logo.dev, download, and write. A record where
Logo.dev still has nothing but a wordmark-shaped asset (still wordmark-only
after the fetch-preference fix) is left UNCHANGED — never rewritten with
the same-shaped asset — and reported as a residual case for the manual
logo-override process. Per the write-then-read-back standing practice, an
--apply run re-SELECTs every row it wrote after the run and asserts
logo_path (and, for a changed asset, the new file's own dimensions) match
what was intended.

Usage:
    python -m scripts.refetch_lopsided_logos --db library.db            # preview, zero API calls
    python -m scripts.refetch_lopsided_logos --db library.db --apply     # fetch + save for real
    python -m scripts.refetch_lopsided_logos --db library.db --apply --limit 20
    python -m scripts.refetch_lopsided_logos --db library.db --max-ratio 2.0

Requires LOGODEV_API_KEY in the environment for --apply (not required for
a preview run).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from linklib.logodev import extract_domain, fetch_logo_asset, download_asset
from scripts.audit_tool_logo_dimensions import (
    DEFAULT_MAX_RATIO,
    _DIR_BY_KIND,
    _logos_root,
    probe_dimensions,
)

DEFAULT_DELAY = 0.25  # seconds between Logo.dev calls — matches backfill_logos.py

# Defensive, explicit skip for the two known genuinely-undersized rasters
# (low-resolution source images, not a wordmark/aspect-ratio problem — see
# module docstring). Structurally redundant with the "lopsided" flag scope
# below, but kept as a second, name-based guard per the build brief's
# explicit "do not touch Cube or Kintsugi" instruction — belt and
# suspenders, not a stand-in for the flag-based scoping.
_NEVER_TOUCH_NAMES = {"cube", "kintsugi"}

_SQUARE_TYPES = ("icon", "symbol")


def _candidates(lib: Library, root: str, max_ratio: float) -> list[tuple[str, dict, tuple[int, int], float]]:
    """(kind, row, (w, h), ratio) for every tool/community whose CURRENT
    on-disk logo is flagged "lopsided" right now — computed live, never
    from a stale CSV. Excludes logo_manual_override=1 rows and the
    defensive by-name skip list. Rows with no file, an unreadable file, or
    only an "undersized" (not lopsided) flag are not this script's job and
    are excluded."""
    out: list[tuple[str, dict, tuple[int, int], float]] = []

    def _scan(kind: str, table: str) -> None:
        rows = lib.conn.execute(
            f"SELECT id, name, slug, url, logo_path FROM {table} "
            f"WHERE logo_path != '' AND logo_path IS NOT NULL AND logo_manual_override=0"
        ).fetchall()
        for r in rows:
            row = dict(r)
            if row["name"].strip().lower() in _NEVER_TOUCH_NAMES:
                continue
            full = os.path.join(root, _DIR_BY_KIND[kind], os.path.basename(row["logo_path"]))
            if not os.path.isfile(full):
                continue  # "missing-file" — a different problem, not this script's job
            dims = probe_dimensions(full)
            if dims is None:
                continue  # "unreadable" — a different problem, not this script's job
            w, h = dims
            ratio = (max(w, h) / min(w, h)) if min(w, h) else float("inf")
            if ratio > max_ratio:
                out.append((kind, row, (w, h), ratio))

    _scan("tool", "tools")
    _scan("community", "communities")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO,
                     help=f"treat any on-disk logo above this long:short aspect ratio as a re-fetch "
                          f"candidate (default {DEFAULT_MAX_RATIO}, matching scripts/audit_tool_logo_dimensions.py)")
    ap.add_argument("--limit", type=int, default=0, help="max records to process this run (0 = no limit)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually call Logo.dev, download assets, and write to the DB. "
                          "Without this flag, only a preview is printed — no API calls, no writes.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                     help=f"seconds to sleep between Logo.dev calls (default {DEFAULT_DELAY})")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        logos_root = _logos_root(args.db)
        candidates = _candidates(lib, logos_root, args.max_ratio)
        if args.limit:
            candidates = candidates[: args.limit]

        if not candidates:
            print("No tool/community currently has a lopsided (wordmark-shaped) logo on disk — nothing to do.")
            return 0

        print(f"{len(candidates)} record(s) currently flagged lopsided (max-ratio={args.max_ratio}):\n")
        for kind, row, (w, h), ratio in candidates:
            print(f"  [{kind:9s}] {row['name']:<28} {w:.0f}x{h:.0f}  ratio={ratio:.1f}  {row['logo_path']}")

        if not args.apply:
            print(
                "\nPREVIEW ONLY — zero Logo.dev calls made, no files saved, no DB writes.\n"
                "This is exactly the record list --apply would process, in the same order. Whether\n"
                "each one actually resolves a square icon/symbol asset can only be known by calling\n"
                "Logo.dev for real, which this preview deliberately skips. Re-run with --apply to\n"
                "fetch for real."
            )
            return 0

        api_key = os.environ.get("LOGODEV_API_KEY")
        if not api_key:
            print("\nERROR: LOGODEV_API_KEY is not set in the environment.", file=sys.stderr)
            return 1

        print(f"\nSaving downloaded logos under: {logos_root}\n")

        session = requests.Session()
        changed: list[tuple[str, int, str, str]] = []  # (kind, id, name, logo_path)
        residual_wordmark: list[tuple[str, str]] = []  # (kind, name) — still no icon/symbol available
        failed: list[tuple[str, str, str]] = []  # (kind, name, reason)
        quota_hit = False

        for i, (kind, row, (old_w, old_h), old_ratio) in enumerate(candidates, start=1):
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
                if asset_type not in _SQUARE_TYPES:
                    # Logo.dev still has nothing but the wordmark for this
                    # brand — re-downloading it would just replace one
                    # wordmark file with an identically-shaped one. Leave the
                    # existing asset in place and flag it for manual review.
                    residual_wordmark.append((kind, name))
                    print(f"{prefix} ({domain}): STILL WORDMARK-ONLY — Logo.dev has no icon/symbol "
                          f"asset for this brand; left unchanged, flag for manual override")
                    if i < len(candidates):
                        time.sleep(args.delay)
                    continue

                rel_path = f"logos/{_DIR_BY_KIND[kind]}/{slug}.{ext}"
                dest_path = os.path.join(logos_root, _DIR_BY_KIND[kind], f"{slug}.{ext}")
                try:
                    download_asset(image_bytes, dest_path)
                except OSError as exc:
                    failed.append((kind, name, f"asset save failed: {exc}"))
                    print(f"{prefix} ({domain}): MISS — asset save failed: {exc}")
                else:
                    if kind == "tool":
                        wrote = lib.set_tool_logo(row["id"], rel_path)
                    else:
                        wrote = lib.set_community_logo(row["id"], rel_path)
                    if wrote:
                        new_dims = probe_dimensions(dest_path)
                        new_ratio = None
                        if new_dims and min(new_dims):
                            new_ratio = max(new_dims) / min(new_dims)
                        changed.append((kind, row["id"], name, rel_path))
                        ratio_note = f"ratio {old_ratio:.1f} -> {new_ratio:.1f}" if new_ratio else "ratio: unreadable post-write"
                        print(f"{prefix} ({domain}): OK — saved {rel_path} (type={asset_type}, {ratio_note})")
                    else:
                        # Defense in depth: _candidates already excludes
                        # logo_manual_override=1 rows, but set_tool_logo/
                        # set_community_logo re-check and refuse to write over
                        # one regardless — only reachable on a race with a
                        # concurrent admin edit.
                        print(f"{prefix} ({domain}): SKIPPED — manual logo override is active, not overwritten")

            if i < len(candidates):
                time.sleep(args.delay)

        processed = len(changed) + len(residual_wordmark) + len(failed)
        print(f"\n{processed} record(s) processed" + (" (stopped early on quota)" if quota_hit else "")
              + f": {len(changed)} switched to a square asset, "
              f"{len(residual_wordmark)} still wordmark-only, {len(failed)} missed.")

        if changed:
            print("\nWrite-then-read-back verification:")
            for kind, rid, name, rel_path in changed:
                table = "tools" if kind == "tool" else "communities"
                row = lib.conn.execute(f"SELECT logo_path FROM {table} WHERE id=?", (rid,)).fetchone()
                ok = bool(row) and row["logo_path"] == rel_path
                status = "OK" if ok else "MISMATCH — INVESTIGATE"
                print(f"  [{status}] {kind:9s} id={rid:>4} {name!r} -> {row['logo_path'] if row else '<missing row>'}")
                if not ok:
                    return 1

        if residual_wordmark:
            print("\nStill wordmark-only after re-fetch — add to the manual logo-override worklist:")
            for kind, name in residual_wordmark:
                print(f"  [{kind:9s}] {name}")

        if failed:
            print("\nFailed/skipped (not a wordmark-only case — a real miss):")
            for kind, name, reason in failed:
                print(f"  [{kind:9s}] {name}: {reason}")

        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
