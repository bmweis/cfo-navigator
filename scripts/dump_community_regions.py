#!/usr/bin/env python3
"""Read-only: dump distinct `region` values currently stored in the
communities table, with a per-value count, so they can be diffed against
the seed-based reach/metros migration mapping before Phase 1 writes
anything.

Run against prod (Railway volume) with:
    railway run python -m scripts.dump_community_regions --db /data/library.db

or locally against a copy of the DB:
    python -m scripts.dump_community_regions --db library.db

Makes no writes and takes no lock beyond a normal read connection.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library


def main():
    parser = argparse.ArgumentParser(description="Dump distinct communities.region values + counts.")
    parser.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    args = parser.parse_args()

    lib = Library(args.db)
    try:
        rows = lib.conn.execute(
            "SELECT region, COUNT(*) AS n, GROUP_CONCAT(name, ' | ') AS names "
            "FROM communities GROUP BY region ORDER BY region COLLATE NOCASE"
        ).fetchall()
    finally:
        lib.close()

    total = sum(r["n"] for r in rows)
    print(f"{len(rows)} distinct region value(s) across {total} communities:\n")
    for r in rows:
        print(f"[{r['n']:>2}] {r['region']!r}")
        print(f"      {r['names']}\n")


if __name__ == "__main__":
    main()
