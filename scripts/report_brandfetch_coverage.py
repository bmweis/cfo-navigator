#!/usr/bin/env python3
"""Read-only dry-run for the Phase D logo-sourcing investigation: tests the
Brandfetch Logo API against every tool's and community's real `url` in the
live database and reports a hit/miss coverage rate. Makes NO writes of any
kind — no image is saved, no `logo_url` column exists yet, no DB row is
touched. Same safe-by-default shape as scripts/report_orphaned_categories.py.

Why this exists: Phase D's investigation report recommended Brandfetch's
free-tier Logo API (Clearbit's is dead; Logo.dev was ruled out over its
attribution requirement). That recommendation was pricing/policy research,
not a real test against our 216 actual tool/community URLs. This script is
that test — it answers "what % of OUR real records actually resolve a
logo", not a generic industry estimate, so Brian can decide whether the
hybrid (automated + manual fallback) approach is viable before any schema
or build work starts.

Method: for each tool/community URL, extract the bare registrable domain
and request `https://cdn.brandfetch.io/domain/{domain}?c={BRANDFETCH_CLIENT_ID}`
via GET (Brandfetch's CDN logo endpoint 302-redirects to the actual image
when it has one; `requests` follows redirects by default, so a 200 with an
image content-type is a hit). A short delay between requests keeps this
one-time run polite to Brandfetch's servers.

Usage:
    export BRANDFETCH_CLIENT_ID=...
    python -m scripts.report_brandfetch_coverage [--db library.db] [--delay 0.15]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,}$", re.IGNORECASE)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

BRANDFETCH_CDN = "https://cdn.brandfetch.io/domain/{domain}?c={client_id}"
REQUEST_TIMEOUT = 10  # seconds


def extract_domain(url: str) -> str | None:
    """Bare registrable-ish domain from a stored tool/community URL, e.g.
    "https://www.brex.com/pricing" -> "brex.com". Returns None for a URL
    too malformed to parse at all, which is itself a reportable finding
    (bad data in OUR table, not a real Brandfetch miss)."""
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


def check_logo(domain: str, client_id: str, session: requests.Session) -> tuple[bool, str]:
    """Returns (hit, detail). A hit is a 200 response with an image
    content-type. Anything else (404, non-image 200, timeout, connection
    error) is a miss, with `detail` explaining why."""
    fetch_url = BRANDFETCH_CDN.format(domain=domain, client_id=client_id)
    try:
        resp = session.get(fetch_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        return False, f"request error: {exc}"
    content_type = resp.headers.get("Content-Type", "")
    if resp.status_code == 200 and content_type.startswith("image/"):
        return True, f"200 {content_type}"
    return False, f"{resp.status_code} {content_type or '(no content-type)'}"


def main():
    parser = argparse.ArgumentParser(
        description="Dry-run test of Brandfetch Logo API coverage against real tool/community URLs. Read-only."
    )
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument(
        "--delay", type=float, default=0.15,
        help="Seconds to sleep between requests (default 0.15)",
    )
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    client_id = os.environ.get("BRANDFETCH_CLIENT_ID")
    if not client_id:
        print("ERROR: BRANDFETCH_CLIENT_ID is not set in the environment.", file=sys.stderr)
        sys.exit(1)

    lib = Library(args.db)
    try:
        tool_rows = lib.conn.execute(
            "SELECT id, name, url FROM tools ORDER BY name COLLATE NOCASE"
        ).fetchall()
        community_rows = lib.conn.execute(
            "SELECT id, name, url FROM communities ORDER BY name COLLATE NOCASE"
        ).fetchall()
    finally:
        lib.close()

    records = [("tool", r) for r in tool_rows] + [("community", r) for r in community_rows]
    print(f"Loaded {len(tool_rows)} tools + {len(community_rows)} communities = {len(records)} total records\n")

    hits: list[dict] = []
    misses: list[dict] = []
    bad_urls: list[dict] = []

    session = requests.Session()
    for i, (kind, row) in enumerate(records, start=1):
        name, url = row["name"], row["url"]
        domain = extract_domain(url)
        if not domain:
            bad_urls.append({"kind": kind, "name": name, "url": url})
            print(f"[{i}/{len(records)}] {kind:9s} {name!r}: SKIP (unparseable url={url!r})")
            continue

        hit, detail = check_logo(domain, client_id, session)
        entry = {"kind": kind, "name": name, "url": url, "domain": domain, "detail": detail}
        if hit:
            hits.append(entry)
            status = "HIT "
        else:
            misses.append(entry)
            status = "MISS"
        print(f"[{i}/{len(records)}] {kind:9s} {name!r} ({domain}): {status} — {detail}")

        if i < len(records):
            time.sleep(args.delay)

    total_checked = len(hits) + len(misses)
    print("\n" + "=" * 100)
    print("BRANDFETCH COVERAGE — DRY RUN RESULT (no writes made)")
    print("=" * 100)
    if total_checked:
        pct = 100.0 * len(hits) / total_checked
        print(f"Hit rate: {len(hits)}/{total_checked} ({pct:.1f}%)")
    else:
        print("Hit rate: n/a (no records had a parseable URL)")
    print(f"Misses:   {len(misses)}")
    if bad_urls:
        print(f"Unparseable URLs in our own data (not counted as Brandfetch misses): {len(bad_urls)}")

    if misses:
        print("\n--- FULL MISS LIST (review: obscure/small vendors expected; well-known")
        print("    companies missing would suggest a request problem, not real coverage) ---")
        for e in sorted(misses, key=lambda x: (x["kind"], x["name"].lower())):
            print(f"  [{e['kind']}] {e['name']} — {e['url']}  (domain={e['domain']}, {e['detail']})")

    if bad_urls:
        print("\n--- UNPARSEABLE URLS (bad data in our own tools/communities table, not Brandfetch's fault) ---")
        for e in bad_urls:
            print(f"  [{e['kind']}] {e['name']} — url={e['url']!r}")

    print()


if __name__ == "__main__":
    main()
