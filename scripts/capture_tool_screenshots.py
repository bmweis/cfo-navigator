#!/usr/bin/env python3
"""Bulk homepage screenshot capture for the CFO Toolbox Software directory
(search overhaul, screenshot follow-up). Screenshots each selected tool's
homepage at a fixed viewport via linklib.screenshots.capture_homepage — the
exact same capture logic the live "Recapture" admin button uses, so a bulk
backfill and a one-off manual recapture land on identical images/state.

Requires Playwright's Chromium browser to be installed (`playwright install
chromium`) and real network access to the target sites — run this from
wherever both of those are true (your machine, or `railway ssh` into the
production container), not from a sandboxed build session.

    python -m scripts.capture_tool_screenshots --db library.db --tools "Ramp,Brex" --dry-run
    python -m scripts.capture_tool_screenshots --db library.db --tools "Ramp,Brex"
    python -m scripts.capture_tool_screenshots --db library.db --limit 20

Re-running is safe: a tool that already has a screenshot_url is skipped
unless --force. Screenshots are written to the directory the app itself
serves from (next to the DB file — the persistent Railway volume in
production), so a capture run here and a "Recapture" click in the admin UI
are interchangeable.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.screenshots import capture_homepage


def _select_tools(lib: Library, names: str, limit: int) -> list[dict]:
    all_tools = lib.list_tools(approved_only=True)
    if names:
        wanted = {n.strip().lower() for n in names.split(",") if n.strip()}
        selected = [t for t in all_tools if t["name"].lower() in wanted]
        missing = wanted - {t["name"].lower() for t in selected}
        if missing:
            print(f"WARNING: no approved tool found for: {', '.join(sorted(missing))}", file=sys.stderr)
        return selected
    if limit:
        return all_tools[:limit]
    return all_tools


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    ap.add_argument("--tools", default="", help="comma-separated tool names to run (test batch)")
    ap.add_argument("--limit", type=int, default=0, help="run the first N approved tools instead")
    ap.add_argument("--dry-run", action="store_true", help="print what would be captured, capture nothing")
    ap.add_argument("--force", action="store_true", help="recapture even tools that already have a screenshot")
    args = ap.parse_args()

    if not args.tools and not args.limit:
        print("ERROR: pass --tools \"Name1,Name2\" or --limit N — running against the whole "
              "catalog unscoped isn't supported here on purpose.", file=sys.stderr)
        return 2

    lib = Library(args.db)
    try:
        tools = _select_tools(lib, args.tools, args.limit)
        if not tools:
            print("No matching tools found.", file=sys.stderr)
            return 1

        screenshot_dir = os.path.join(os.path.dirname(os.path.abspath(args.db)) or ".", "tool_screenshots")
        succeeded = failed = skipped = 0

        for t in tools:
            if (t.get("screenshot_url") or "").strip() and not args.force:
                print(f"[{t['name']}] already has a screenshot — skipping (use --force to recapture)")
                skipped += 1
                continue

            if args.dry_run:
                print(f"[{t['name']}] would capture {t['url']}")
                continue

            dest = os.path.join(screenshot_dir, f"{t['slug']}.png")
            print(f"[{t['name']}] capturing {t['url']}…", end=" ", flush=True)
            ok = capture_homepage(t["url"], dest)
            if ok:
                served_url = f"/tools/software/screenshot/{t['slug']}.png?v={int(time.time())}"
                lib.set_tool_screenshot_capture(t["id"], served_url)
                print("OK")
                succeeded += 1
            else:
                print("FAILED (timeout, blocked, or bad URL)")
                failed += 1
            time.sleep(0.5)  # light courtesy between real page loads

        print(f"\n{len(tools)} tool(s) processed: {succeeded} captured, {failed} failed, {skipped} skipped."
              + (" (dry run — nothing written)" if args.dry_run else ""))
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
