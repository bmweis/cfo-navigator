"""Shared Logo.dev fetch logic — the active logo source for CFO Toolbox
tools/communities as of 2026-09, replacing Brandfetch (see
`linklib/brandfetch.py`'s own module docstring — that module is left
completely untouched and unused by default, kept only as a dormant
reference in case Brandfetch's Brand API credits are ever restored;
restoring it is a matter of switching the two callers' imports back, not
reconstructing anything).

WHY THE SWITCH: Brandfetch's Brand API free tier was a one-time, non-
resetting 100-credit allotment — confirmed exhausted for good, not just
low, so every future call is guaranteed to fail forever. A cascade that
tries Brandfetch first and falls through to Logo.dev on failure would
still work, but every single call would burn a doomed HTTP round-trip for
no reason — that isn't a fallback, it's dead code with latency. Logo.dev
is the sole active source instead.

WHY THIS IS SIMPLER THAN linklib/brandfetch.py: Brandfetch's Brand API
returns a JSON document listing several logo variants (icon/symbol/logo
types, light/dark themes, svg/png formats), so `best_logo_asset()` had a
real ranking problem to solve. Logo.dev's free, uncapped (500K requests/
month, no credit card) endpoint — `https://img.logo.dev/:domain` — is,
per Logo.dev's own docs, already scoped to return "the symbol (also
called the icon or mark): the graphic part with no text." There is no
JSON response to rank; the endpoint IS the square-icon request. (Logo.dev
does have a separate "brandmark" wordmark field, but it's a different,
credit-metered Brand API this project has no use for — the plain image
endpoint is exactly the asset this project's fixed-square logo tiles
need.)

`fallback=404` is ALWAYS forced on every request here — non-negotiable.
Without it, a domain with no real logo gets a 200 with a generated
monogram, which would silently write fake placeholder art into the
database as if it were a real vendor logo. Forcing `fallback=404` turns
that into a genuine, detectable miss instead.

One real, deliberate shape difference from brandfetch.py's two-step
fetch-then-download flow: Logo.dev's free endpoint IS the image (one HTTP
call gets both existence and content), so `fetch_logo_asset()` here
already carries the image bytes in its returned asset tuple — there's no
second network call for `download_asset()` to make. Both callers
(scripts/backfill_logos.py, webapp/app.py's `_live_refetch_logo`) pass
those bytes straight through.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import requests

LOGO_IMG_URL = "https://img.logo.dev/{domain}"
REQUEST_TIMEOUT = 15  # seconds

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,}$", re.IGNORECASE)

# Every on-site render is a fixed square tile (_logo_box() in webapp/app.py:
# 64px directory, 56px profile header, 32px Competitors table), so a
# transparent PNG is what this project actually wants — format=jpg (the
# API default) is opaque and sits on a white box. size/retina are tuned
# for those tile sizes; theme=light matches the site's light background
# (BRAND.md, #F5F4EF).
_REQUEST_PARAMS = {
    "format": "png",
    "size": "256",
    "retina": "true",
    "theme": "light",
    "fallback": "404",
}


def extract_domain(url: str) -> str | None:
    """Bare registrable-ish domain from a stored tool/community URL, e.g.
    "https://www.brex.com/pricing" -> "brex.com". Returns None for a URL
    too malformed to parse at all. Identical logic to
    linklib.brandfetch.extract_domain (duplicated rather than imported —
    see module docstring on why brandfetch.py stays untouched)."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlparse(url).netloc
    except ValueError:
        return None
    host = host.split("@")[-1].split(":")[0]  # strip userinfo/port if present
    if host.startswith("www."):
        host = host[4:]
    if not host or not _HOSTNAME_RE.match(host):
        return None
    return host


def fetch_logo_asset(domain: str, api_key: str, session: requests.Session | None = None) -> tuple[tuple[bytes, str, str] | None, str | None]:
    """Calls Logo.dev's free image endpoint for one domain, with
    fallback=404 always forced. Returns ((image_bytes, ext, asset_type),
    None) on a real hit, or (None, reason) on any kind of miss/failure.
    `asset_type` is always "icon" — kept as a 3-tuple (matching
    linklib.brandfetch.fetch_logo_asset's return shape) so both callers'
    existing unpacking code needs no restructuring, even though Logo.dev's
    plain image endpoint has no wordmark/icon choice to report — it only
    ever returns the icon.

    `reason` starting with "QUOTA" signals a caller doing a multi-record
    run should stop the whole run, not just skip this record — see
    scripts/backfill_logos.py's main() for that handling; a single-record
    caller just surfaces it."""
    session = session or requests.Session()
    url = LOGO_IMG_URL.format(domain=domain)
    params = dict(_REQUEST_PARAMS, token=api_key)
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return None, f"request error: {exc}"

    if resp.status_code == 404:
        return None, "404 — Logo.dev has no logo for this domain"
    if resp.status_code == 401:
        return None, "401 — LOGODEV_API_KEY missing or invalid"
    if resp.status_code == 429:
        return None, "QUOTA: 429 rate-limited — stopping the run, not just this record"
    if resp.status_code != 200:
        return None, f"{resp.status_code} {resp.text[:200]!r}"

    if not resp.content:
        return None, "200 OK but empty response body"
    return (resp.content, "png", "icon"), None


def download_asset(image_bytes: bytes, dest_path: str) -> None:
    """Writes already-fetched image bytes to disk. Unlike
    linklib.brandfetch.download_asset (a second HTTP call to a separate
    asset URL), fetch_logo_asset above already made the one HTTP call
    Logo.dev's free endpoint needs — there's nothing left to fetch, only
    to save. Kept as its own function, not inlined at each call site, so
    both callers write files exactly the same way."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(image_bytes)
