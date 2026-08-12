#!/usr/bin/env python3
"""One-off diagnostic (not a fix) for the ApprovalMax cookie-banner
investigation: reproduces linklib.screenshots.capture_homepage's exact
navigation/wait sequence against a real URL, then dumps the rendered DOM so
we can see what's actually there — instead of guessing from a screenshot.

Why this exists: capture_homepage() runs inside the Railway container
(confirmed via ipinfo.io to egress as plain US — AWS us-west-1 — so the
earlier geolocation theory is ruled out), which has real network access this
build environment deliberately doesn't. This script is meant to be run from
somewhere that DOES have real egress — `railway ssh` into the production
container, or a developer machine — not from a sandboxed build session.

What it answers, that a screenshot alone can't:
  - The exact HTML of the cookie-banner container (id/class/attributes) —
    tells us whether this is a findable-selector case (a one-line addition
    to _COOKIE_BANNER_SELECTORS) or a fully custom implementation with no
    stable hook.
  - Whether the banner lives inside a Shadow DOM. A plain
    `page.content()` dump does NOT serialize shadow-tree content (shadow
    roots are deliberately excluded from a host element's outerHTML/
    innerHTML) — so a custom element that shows empty/near-empty light-DOM
    content is itself evidence of shadow-DOM encapsulation. This script
    also explicitly walks the DOM for elements exposing a `.shadowRoot` and
    dumps their content directly, which a plain HTML dump can't see at all.
  - Whether it's a cross-origin iframe (page.frames() lists every frame;
    an iframe hosted on a different origin than the page itself is a
    likely cookie-consent-vendor widget).

Deliberately does NOT change linklib/screenshots.py, does NOT touch the
banner (no clicking, no injected CSS) — this is read-only reconnaissance.

Usage:
    python -m scripts.diagnose_cookie_banner
    python -m scripts.diagnose_cookie_banner --url https://approvalmax.com --out /tmp/approvalmax.html
"""
from __future__ import annotations

import argparse
import sys

SCREENSHOT_VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_TIMEOUT_MS = 15000

# Heuristic only — used to point the raw-HTML text search at likely
# candidates and to seed the shadow-DOM walk. Not the curated selector list
# from linklib/screenshots.py; this script doesn't hide anything.
_COOKIE_KEYWORDS = ("cookie", "consent", "gdpr", "ccpa")


def _find_cookie_ish_snippets(html: str, context: int = 300) -> list[str]:
    """Best-effort: locate raw-HTML regions mentioning cookie/consent
    keywords near an opening tag, so the relevant container is easy to spot
    in a large dump without reading the whole file by hand."""
    import re

    snippets = []
    seen_spans = []
    for kw in _COOKIE_KEYWORDS:
        for m in re.finditer(re.escape(kw), html, flags=re.IGNORECASE):
            start = max(0, m.start() - context)
            end = min(len(html), m.end() + context)
            # Skip near-duplicate windows (adjacent keyword hits in the same block)
            if any(abs(start - s) < context for s in seen_spans):
                continue
            seen_spans.append(start)
            snippets.append(html[start:end])
    return snippets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="https://approvalmax.com", help="page to inspect")
    ap.add_argument("--out", default="/tmp/cookie_banner_diagnostic.html",
                     help="path to write the full rendered HTML to")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run this where the app's deps are "
              "installed (railway ssh, or a dev machine with `pip install playwright "
              "&& playwright install chromium`).", file=sys.stderr)
        return 2

    print(f"Navigating to {args.url} (same wait sequence as capture_homepage: "
          f"wait_until='load' + 1000ms settle)…")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=SCREENSHOT_VIEWPORT)
                page.goto(args.url, timeout=SCREENSHOT_TIMEOUT_MS, wait_until="load")
                page.wait_for_timeout(1000)

                # 1. Full rendered HTML (light DOM only — shadow trees excluded by spec).
                html = page.content()
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"\nWrote {len(html):,} bytes of rendered HTML to {args.out}")

                # 2. Raw-text search for cookie/consent-ish snippets, to point at the
                #    right spot in that dump.
                snippets = _find_cookie_ish_snippets(html)
                print(f"\n{len(snippets)} cookie/consent-keyword snippet(s) found in the light DOM:")
                for i, s in enumerate(snippets, 1):
                    print(f"\n--- snippet {i} " + "-" * 50)
                    print(s.strip())
                if not snippets:
                    print("  (none — if the banner is visibly on the page but no keyword hit "
                          "shows up here, that's itself a signal: check the shadow-DOM walk "
                          "and frame list below.)")

                # 3. Walk the DOM for elements exposing a non-null shadowRoot — these
                #    won't show up in page.content() at all.
                shadow_hosts = page.evaluate("""
                    () => {
                      const hosts = [];
                      document.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) {
                          hosts.push({
                            tag: el.tagName.toLowerCase(),
                            id: el.id || null,
                            className: (el.className && el.className.toString) ? el.className.toString() : null,
                            shadowHTML: el.shadowRoot.innerHTML.slice(0, 2000),
                          });
                        }
                      });
                      return hosts;
                    }
                """)
                print(f"\n{len(shadow_hosts)} element(s) with an open shadow root found on the page:")
                for h in shadow_hosts:
                    print(f"\n--- shadow host <{h['tag']}> id={h['id']!r} class={h['className']!r} ---")
                    print(h["shadowHTML"])
                if not shadow_hosts:
                    print("  (none with an OPEN shadow root — note this can't see CLOSED shadow "
                          "roots at all; a closed shadow root is invisible to any page-level "
                          "script, including this one, and would need devtools on a real "
                          "browser session to confirm.)")

                # 4. List frames — a cross-origin cookie-consent widget often lives in
                #    its own iframe.
                frames = page.frames
                print(f"\n{len(frames)} frame(s) on the page:")
                for fr in frames:
                    print(f"  - {fr.url!r}")

            finally:
                browser.close()
    except Exception as e:
        print(f"ERROR: capture failed: {e!r}", file=sys.stderr)
        return 1

    print("\nDone. Read the snippets/shadow-host output above, or open the full dump at "
          f"{args.out}, to see the banner's real markup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
