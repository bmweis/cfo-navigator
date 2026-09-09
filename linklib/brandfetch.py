"""Shared Brandfetch **Brand API** logic (`api.brandfetch.io/v2/brands/domain/{domain}`,
Bearer-token auth — see scripts/backfill_logos.py's own module docstring for
the full product/quota background: this is the sanctioned, real-JSON-response
product, distinct from the browser-embed-only free CDN Logo API).

Extracted here (2026-08, manual logo override "revert + re-fetch" follow-up)
so there is exactly ONE implementation of "how we talk to Brandfetch" —
scripts/backfill_logos.py's monthly batch run and webapp/app.py's admin
"Revert to automatic" live re-fetch both import from here rather than each
carrying their own copy that could quietly drift apart (same "one choke
point" discipline as the domain-echo guard below, which both callers now
get automatically).
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import requests

BRAND_API_URL = "https://api.brandfetch.io/v2/brands/domain/{domain}"
REQUEST_TIMEOUT = 15  # seconds

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,}$", re.IGNORECASE)

# Format preference, carried over from the original Phase D investigation's
# recommendation: SVG scales cleanly across card-size and profile-page-size
# renders with zero quality loss; PNG is the fallback when a brand has no SVG.
FORMAT_PREFERENCE = ("svg", "png")

# Logo "theme" preference: our pages sit on a light background (#F5F4EF,
# BRAND.md), so a logo variant designed for light backgrounds reads better
# than one designed for dark. Not every brand publishes both.
THEME_PREFERENCE = ("light", "dark")

# Logo "type" preference (Logo Tile Fit fix, 2026-09) — every on-site render
# surface (_logo_box() in webapp/app.py: 64px directory tile, 56px profile
# header, 32px Competitors table) is a fixed SQUARE. Brandfetch's Brand API
# returns three shapes under "type": "icon"/"symbol" (a square mark, built
# for exactly this kind of tile) and "logo" (the full wordmark/lockup,
# usually wide). The ORIGINAL preference order here put "logo" first — that
# was backwards for a square tile and is what produced the thin-sliver
# wordmark renders an audit (scripts/audit_tool_logo_dimensions.py) flagged
# on ~48 assets (Airbase 4:1, Airwallex 7.3:1, NetSuite 14:1, ...): a wide
# wordmark SVG shrunk by object-fit:contain to fit a square box leaves most
# of the box empty. Square marks now win; the wordmark is still selected
# when a brand publishes no icon/symbol asset at all — never worse than the
# old behavior, just no longer the default. "icon" is ranked ahead of
# "symbol" (both square) since Brandfetch's own docs describe "icon" as the
# primary square mark and "symbol" as a secondary/alternate one.
TYPE_PREFERENCE = ("icon", "symbol", "logo")


def extract_domain(url: str) -> str | None:
    """Bare registrable-ish domain from a stored tool/community URL, e.g.
    "https://www.brex.com/pricing" -> "brex.com". Returns None for a URL too
    malformed to parse at all."""
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


def best_logo_asset(data: dict) -> tuple[str, str, str] | None:
    """Given a Brand API /v2/brands/domain/{domain} response body, pick the
    best logo asset: prefer a square "icon"/"symbol" mark over the "logo"
    wordmark (see TYPE_PREFERENCE above), prefer a "light"-theme variant,
    prefer SVG over PNG — falling back gracefully at each step since not
    every brand publishes every combination. Returns
    (src_url, format_ext, asset_type) or None if the response has no usable
    logo asset at all. `asset_type` is one of TYPE_PREFERENCE's values (or
    "" for a type Brandfetch didn't label) — callers use it to tell "found a
    real square mark" from "fell back to the wordmark, no icon exists"."""
    logos = data.get("logos") or []
    if not logos:
        return None

    def type_rank(logo: dict) -> int:
        try:
            return TYPE_PREFERENCE.index(logo.get("type"))
        except ValueError:
            return len(TYPE_PREFERENCE)  # unknown/missing type sorts last

    def theme_rank(logo: dict) -> int:
        theme = logo.get("theme")
        try:
            return THEME_PREFERENCE.index(theme)
        except ValueError:
            return len(THEME_PREFERENCE)  # unknown/missing theme sorts last, not first

    ordered = sorted(logos, key=lambda logo: (type_rank(logo), theme_rank(logo)))
    for fmt_pref in FORMAT_PREFERENCE:
        for logo in ordered:
            for fmt in logo.get("formats") or []:
                if (fmt.get("format") or "").lower() == fmt_pref and fmt.get("src"):
                    return fmt["src"], fmt_pref, (logo.get("type") or "")
    return None


def fetch_logo_asset(domain: str, api_key: str, session: requests.Session | None = None) -> tuple[tuple[str, str, str] | None, str | None]:
    """Calls the Brand API for one domain. Returns ((src_url, ext, asset_type),
    None) on success, or (None, reason) on any kind of miss/failure. `reason` starting
    with "QUOTA" signals a caller doing a multi-record run should stop the
    whole run, not just skip this record — see scripts/backfill_logos.py's
    main() for that handling; a single-record caller just surfaces it.

    Includes the domain-echo guard (2026-08, manual logo override
    investigation): the Brand API response carries its own "domain" field for
    the brand it actually matched, now checked against the domain requested
    before its logo asset is ever trusted — closing a real gap where a
    fuzzy/mismatched match on Brandfetch's side would otherwise be accepted
    silently (the suspected, though not independently confirmed, cause of
    Aleph showing Zapier's logo — see CLAUDE.md)."""
    session = session or requests.Session()
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

    resp_domain = (data.get("domain") or "").strip().lower()
    if resp_domain and resp_domain not in (domain.lower(), f"www.{domain.lower()}"):
        return None, f"MISMATCH: requested {domain!r} but response is for {resp_domain!r} — skipped, not saved"

    asset = best_logo_asset(data)
    if not asset:
        return None, "200 OK but no usable svg/png logo asset in the response"
    return asset, None


def download_asset(src_url: str, dest_path: str, session: requests.Session | None = None) -> None:
    """Downloads a logo asset URL to disk. Raises requests.RequestException
    on any failure — callers decide how to report that."""
    session = session or requests.Session()
    resp = session.get(src_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(resp.content)
