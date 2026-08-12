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

Cookie consent banners (investigation, 2026-08): captures sometimes showed a
consent overlay covering part of the page (e.g. ApprovalMax). There's no
universal fix — every site implements consent differently — so this is
deliberately a best-effort CSS-injection hide, not a click-through-and-accept
flow: `_COOKIE_BANNER_HIDE_CSS` targets the handful of dominant
consent-management platforms (OneTrust, Cookiebot, Osano, TrustArc,
Quantcast/IAB-TCF) by their known container selectors, plus a generic
class/id substring catch-all for common homegrown implementations, and is
injected before the screenshot is taken. This covers a meaningful share of
bannered sites, not all of them — a fully custom banner with no recognizable
selector, or one rendered inside a cross-origin iframe, won't be hidden by
this and still needs the manual re-capture/crop-and-upload path (Phase E).
Deliberately not click-to-accept: a failed/mis-timed click risks landing on
a worse screenshot (e.g. an opened preferences panel) than just hiding the
banner outright, and CMP button markup drifts over time in a way a CSS
selector for the banner *container* is less exposed to.
"""
from __future__ import annotations

import os

SCREENSHOT_VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_TIMEOUT_MS = 15000

# Container selectors for the dominant cookie-consent management platforms,
# plus a generic substring catch-all for common homegrown banners. Hiding
# (not removing) the node is enough — `display:none` takes it out of the
# screenshot without needing the page's own "Accept" flow to run.
_COOKIE_BANNER_SELECTORS = [
    # OneTrust
    "#onetrust-banner-sdk", "#onetrust-consent-sdk", "#onetrust-pc-sdk",
    # Cookiebot
    "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay",
    # Osano
    ".osano-cm-window", ".osano-cm-dialog",
    # TrustArc / TRUSTe
    "#truste-consent-track", "#trustarc-banner-container",
    # Quantcast Choice / IAB TCF
    "#qc-cmp2-container", "#qc-cmp2-main",
    # Didomi
    "#didomi-host", "#didomi-notice",
    # Generic homegrown catch-all: common naming conventions for cookie
    # banners that aren't a recognized third-party CMP.
    "[id*='cookie-banner' i]", "[class*='cookie-banner' i]",
    "[id*='cookie-consent' i]", "[class*='cookie-consent' i]",
    "[id*='cookie-notice' i]", "[class*='cookie-notice' i]",
    "[id*='consent-banner' i]", "[class*='consent-banner' i]",
]

_COOKIE_BANNER_HIDE_CSS = ", ".join(_COOKIE_BANNER_SELECTORS) + " { display: none !important; }"


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
                try:
                    page.add_style_tag(content=_COOKIE_BANNER_HIDE_CSS)
                except Exception:
                    pass  # best-effort — never let banner-hiding block the capture
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                page.screenshot(path=dest_path)
            finally:
                browser.close()
        return True
    except Exception:
        return False
