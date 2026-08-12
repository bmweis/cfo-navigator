#!/usr/bin/env python3
"""One-off verification helper: runs the real, production
linklib.screenshots.capture_homepage() (not a diagnostic bypass — the exact
function the "Recapture" admin button and the bulk backfill script call)
against a URL and writes the resulting PNG somewhere it can be inspected.

Exists for the ApprovalMax cookie-banner follow-up: after adding a new
selector to _COOKIE_BANNER_SELECTORS, this is how to actually confirm the
fix worked — a real screenshot to look at — rather than just trusting that
the new selector string is correct. Same "don't assume it works" discipline
scripts/diagnose_cookie_banner.py was built for.

Requires real network access to the target site — run this from `railway
ssh` or a dev machine, not from a sandboxed build session.

Usage:
    python -m scripts.verify_screenshot_capture
    python -m scripts.verify_screenshot_capture --url https://approvalmax.com --out /tmp/approvalmax_verify.png
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="https://approvalmax.com", help="page to capture")
    ap.add_argument("--out", default="/tmp/screenshot_verify.png", help="path to write the PNG to")
    args = ap.parse_args()

    from linklib.screenshots import capture_homepage

    print(f"Capturing {args.url} via the real capture_homepage() (same code the "
          f"'Recapture' admin button uses)…")
    ok = capture_homepage(args.url, args.out)
    if ok:
        print(f"OK — wrote {args.out}. Open it and confirm visually — this script "
              f"can't see the image itself, only whether the capture succeeded.")
        return 0
    print("FAILED — capture_homepage() returned False (bad URL, timeout, or the "
          "site blocked headless Chromium). See linklib/screenshots.py for what "
          "that covers.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
