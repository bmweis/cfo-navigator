#!/usr/bin/env python3
"""Manual QA / one-off diagnostic — Reader content backfill failure-cluster
cleanup (2026-08).

Read-only. Writes nothing to the database, calls no pipeline code, wires
into nothing — same "manual-QA tool, not an automated pass/fail eval" shape
as scripts/medium_platform_scale_check.py and scripts/eval_retrieval.py.
Meant to be run by hand (e.g. via `railway ssh`) against production and its
output read/pasted back for review — this sandbox has no production DB and
no outbound network access to any external host (confirmed against known-
good hosts, not just the ones below), so none of this could be run or
verified directly in the session that wrote it.

Three tasks, matching the three open questions in the failure-cluster
cleanup:

1. **www-mismatch investigation** (--task www): for each of a small
   hand-picked set of hosts suspected of failing on a bare-vs-www
   prefix mismatch (codingvc.com, elizabethyin.com, inc.com, cfodive.com,
   opexengine.com, tomtunguz.com), prints every currently-saved article's
   URL, its most recent content_refetch_log attempt (status/reason/detail),
   and — separately — issues a live HEAD request against both the bare and
   `www.`-prefixed form of that URL's host, reporting the resulting status
   code/redirect target for each. A real www-prefix bug would show up as
   one form succeeding live while the other doesn't; if both forms behave
   identically, the recorded failure reason has some other cause and the
   fix (if any) is per-host, not a fetcher normalization change. Code-level
   note: linklib.extract.fetch_page (via `requests.get`) follows redirects
   transparently regardless of a bare/www mismatch, and every host-matching
   function in this codebase (linklib.pipeline._domain_migration_target/
   _defunct_service_domain, linklib.medium_platform.is_recognized_blocked_host,
   Library.articles_needing_content_backfill's host_suffixes matching,
   Library.content_refetch_failure_domains) already strips a leading
   "www." before comparing — so if this task finds a real live mismatch,
   it's a site-specific quirk (e.g. a cert or WAF rule scoped to one
   variant), not a bug in this codebase's own comparison logic.

2. **bettereveryday.vc scope** (--task bettereveryday): counts saved
   articles on this host, their most recent content_refetch_log status,
   and whether any already carry a url_correction_log entry (meaning a
   corrected URL was already applied by hand) — the facts needed before
   deciding migration vs. delete for this one, without guessing.

3. **bulk-delete candidate list** (--task delete-candidates): every saved
   article whose host is quora.com, twitter.com, x.com,
   thetechnologyletter.com, or gainsight.com (www-normalized), for review
   before feeding into the existing bulk-delete CSV tool
   (linklib/purge_csv.py) or the manual-review CSV import's delete path.

Usage:
    python -m scripts.diagnose_reader_backfill_failures --db /data/library.db --task www
    python -m scripts.diagnose_reader_backfill_failures --db /data/library.db --task bettereveryday
    python -m scripts.diagnose_reader_backfill_failures --db /data/library.db --task delete-candidates
    python -m scripts.diagnose_reader_backfill_failures --db /data/library.db --task all

--skip-live-check skips the live HEAD-request half of --task www (useful
when run somewhere without outbound network access, or to avoid hitting
these hosts at all until the DB-only findings have been reviewed).
"""
from __future__ import annotations

import argparse
from urllib.parse import urlsplit

from linklib.db import Library, resolve_db_path

WWW_SUSPECT_HOSTS = [
    "codingvc.com",
    "elizabethyin.com",
    "inc.com",
    "cfodive.com",
    "opexengine.com",
    "tomtunguz.com",
]

DELETE_CANDIDATE_HOSTS = [
    "quora.com",
    "twitter.com",
    "x.com",
    "thetechnologyletter.com",
    "gainsight.com",
]


def _host(url: str) -> str:
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _articles_for_hosts(lib: Library, hosts: list[str]) -> list[dict]:
    wanted = {h.lower() for h in hosts}
    return [a for a in lib.all_articles() if _host(a["url"]) in wanted]


def _latest_refetch_attempt(lib: Library, article_id: int) -> dict | None:
    row = lib.conn.execute(
        "SELECT status, reason, detail, attempted_at FROM content_refetch_log "
        "WHERE article_id=? ORDER BY attempted_at DESC LIMIT 1",
        (article_id,),
    ).fetchone()
    if not row:
        return None
    return {"status": row[0], "reason": row[1], "detail": row[2], "attempted_at": row[3]}


def _has_url_correction(lib: Library, article_id: int) -> bool:
    row = lib.conn.execute(
        "SELECT 1 FROM url_correction_log WHERE article_id=? LIMIT 1", (article_id,)
    ).fetchone()
    return row is not None


def task_www(lib: Library, skip_live_check: bool) -> None:
    print("=" * 78)
    print("TASK: www-prefix mismatch investigation")
    print("=" * 78)
    articles = _articles_for_hosts(lib, WWW_SUSPECT_HOSTS)
    if not articles:
        print("No saved articles found on any of:", ", ".join(WWW_SUSPECT_HOSTS))
    by_host: dict[str, list[dict]] = {}
    for a in articles:
        by_host.setdefault(_host(a["url"]), []).append(a)

    for host in WWW_SUSPECT_HOSTS:
        rows = by_host.get(host, [])
        print(f"\n--- {host} ({len(rows)} saved article(s)) ---")
        for a in rows:
            attempt = _latest_refetch_attempt(lib, a["id"])
            attempt_str = (
                f"{attempt['status']}/{attempt['reason']} — {attempt['detail']} "
                f"({attempt['attempted_at']})"
                if attempt else "(never attempted)"
            )
            print(f"  id={a['id']} url={a['url']}")
            print(f"    last attempt: {attempt_str}")

    if skip_live_check:
        print("\n(--skip-live-check: not issuing live HEAD requests)")
        return

    print("\n--- Live bare-vs-www check (HEAD, 10s timeout) ---")
    import requests
    for host in WWW_SUSPECT_HOSTS:
        for variant in (host, f"www.{host}"):
            url = f"https://{variant}/"
            try:
                resp = requests.head(url, timeout=10, allow_redirects=True,
                                      headers={"User-Agent": "Mozilla/5.0"})
                print(f"  {url} -> {resp.status_code} (final: {resp.url})")
            except Exception as exc:
                print(f"  {url} -> ERROR: {type(exc).__name__}: {exc}")


def task_bettereveryday(lib: Library) -> None:
    print("=" * 78)
    print("TASK: bettereveryday.vc scope")
    print("=" * 78)
    articles = _articles_for_hosts(lib, ["bettereveryday.vc"])
    print(f"Saved articles on bettereveryday.vc: {len(articles)}")
    for a in articles:
        attempt = _latest_refetch_attempt(lib, a["id"])
        corrected = _has_url_correction(lib, a["id"])
        attempt_str = (
            f"{attempt['status']}/{attempt['reason']} — {attempt['detail']}"
            if attempt else "(never attempted)"
        )
        print(f"  id={a['id']} url={a['url']} title={a.get('title', '')!r}")
        print(f"    last attempt: {attempt_str}  |  has url_correction_log entry: {corrected}")


def task_delete_candidates(lib: Library) -> None:
    print("=" * 78)
    print("TASK: bulk-delete candidate list")
    print("=" * 78)
    articles = _articles_for_hosts(lib, DELETE_CANDIDATE_HOSTS)
    print(f"Total candidates: {len(articles)}")
    by_host: dict[str, list[dict]] = {}
    for a in articles:
        by_host.setdefault(_host(a["url"]), []).append(a)
    for host in DELETE_CANDIDATE_HOSTS:
        rows = by_host.get(host, [])
        print(f"\n--- {host} ({len(rows)}) ---")
        for a in rows:
            print(f"  id={a['id']} url={a['url']} title={a.get('title', '')!r} "
                  f"saved_at={a.get('created_at', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db")
    ap.add_argument("--task", choices=["www", "bettereveryday", "delete-candidates", "all"],
                     default="all")
    ap.add_argument("--skip-live-check", action="store_true",
                     help="Skip the live HEAD-request half of --task www")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Using database: {db_path}\n")
    lib = Library(db_path)

    if args.task in ("www", "all"):
        task_www(lib, args.skip_live_check)
        print()
    if args.task in ("bettereveryday", "all"):
        task_bettereveryday(lib)
        print()
    if args.task in ("delete-candidates", "all"):
        task_delete_candidates(lib)


if __name__ == "__main__":
    main()
