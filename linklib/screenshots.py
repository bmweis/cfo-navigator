"""Homepage screenshot capture for the CFO Toolbox Software directory (search
overhaul, screenshot follow-up). Playwright + Chromium, homepage-only by
design — deliberately not attempting to auto-identify a "real product
screenshot," which needs human judgment (see the profile-page caption logic
in webapp/app.py). Shared by scripts/capture_tool_screenshots.py (bulk
backfill/CLI) and the live "Recapture" admin button, so both write through
the exact same capture logic and land in the exact same fixed viewport —
consistency across the whole directory was the point.

Requires the `playwright` package and its Chromium browser installed
(`playwright install chromium` — the production Dockerfile does this at
build time). Returns False rather than raising on any failure (bad URL,
timeout, site blocks headless browsers) — screenshotting ~150 external
sites means some will fail, and that's an expected, non-fatal outcome for
callers to report, not treat as a crash.
"""
from __future__ import annotations

import os

SCREENSHOT_VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_TIMEOUT_MS = 15000


def capture_homepage(url: str, dest_path: str) -> bool:
    """Screenshot `url` at a fixed viewport and write a PNG to dest_path.
    Creates parent directories as needed. Returns True on success, False on
    any failure — never raises."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=SCREENSHOT_VIEWPORT)
                page.goto(url, timeout=SCREENSHOT_TIMEOUT_MS, wait_until="load")
                page.wait_for_timeout(1000)  # let hero animations/lazy images settle
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                page.screenshot(path=dest_path)
            finally:
                browser.close()
        return True
    except Exception:
        return False
