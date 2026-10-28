#!/usr/bin/env python3
"""Read-only report: how many loaded benchmark reports resolve to a landing
URL, by which rule, and which do not (issue #738, step B0).

Resolution order (linklib.figures.resolve_landings): the URL typed on
/admin/reader/figures, then the stored landing_url, then the linked digest
article's URL, then the one digest article whose URL carries
?digest=<report_slug>; the `digest` query parameter is removed from the
result. A report that resolves to nothing is cited by name, "no link on file".

The database is opened with SQLite `mode=ro`; nothing is written and there is
no network or model call.

Usage (production, over `railway ssh`):
    python -m scripts.report_figure_landings --db /data/library.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter

from linklib import figures
from linklib.db import resolve_db_path


class _Shim:
    def __init__(self, conn):
        self.conn = conn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=None, help="path to library.db (or set LINKLIB_DB)")
    args = ap.parse_args(argv)
    path = resolve_db_path(args.db)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    land = figures.resolve_landings(_Shim(conn))
    names = {r["report_slug"]: r["report"] for r in conn.execute("SELECT report_slug, report FROM benchmark_reports")}
    how = Counter(v["how"] for v in land.values())
    linked = sum(1 for v in land.values() if v["url"])
    print(f"{linked} of {len(land)} reports resolve to a landing URL.")
    for k in ("manual", "landing_url", "article", "digest_slug", "none"):
        print(f"  {k:12} {how.get(k, 0)}")
    missing = sorted(s for s, v in land.items() if not v["url"])
    if missing:
        print("\nUnresolved (cited by name until a URL is set on /admin/reader/figures):")
        for s in missing:
            print(f"  {s}  {names.get(s, '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
