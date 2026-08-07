#!/usr/bin/env python3
"""Read-only: dump every community's name, URL, and slug from the
communities table — a plain listing, no filtering or formatting.

Run against prod (Railway volume) with:
    railway run python -m scripts.dump_communities --db /data/library.db

or locally against a copy of the DB:
    python -m scripts.dump_communities --db library.db

Makes no writes and takes no lock beyond a normal read connection.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path


def main():
    parser = argparse.ArgumentParser(description="Dump communities.name/url/slug.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)
    try:
        rows = lib.conn.execute(
            "SELECT name, url, slug FROM communities ORDER BY name COLLATE NOCASE"
        ).fetchall()
    finally:
        lib.close()

    print(f"{len(rows)} communities:\n")
    for r in rows:
        print(f"{r['name']} | {r['url']} | {r['slug']}")


if __name__ == "__main__":
    main()
