#!/usr/bin/env python3
"""One-off backfill: populate `reach` + `metros` on existing communities rows
from the migration mapping Brian approved (the Communities geography model
rework). Matches by URL — the same natural key `seed_communities.py`
uses — so it's safe to re-run; any row not in MAPPING is left untouched.

The old `region` column was untouched by this script — it was later dropped
entirely once confirmed unused for any public filtering/display.

Historical: `metros_json` (which this script populates) was later retired in
favor of a free-text `local_markets` column — see
Library._migrate_community_local_markets, which backfilled local_markets from
whatever metros_json already held. Re-running this script would update
metros_json only, not local_markets, so it's kept for the historical record
rather than as a script to run again.

Usage:
    python -m scripts.backfill_community_geo [--db library.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library

# url -> (reach, metros). Only communities present in this mapping are
# touched. reach is one of "Regional" | "National" | "Global".
MAPPING: dict[str, tuple[str, list[str]]] = {
    "https://www.fsuite.co": ("National", ["Seattle"]),
    "https://operators-guild.com": ("National", []),
    "https://www.neugroup.com": ("National", []),
    "https://thecircle.founderscircle.com": ("National", []),
    "https://openfutureforum.com": ("National", []),
    "https://www.evanta.com/cfo": ("National", []),
    "https://www.peievents.com": ("National", []),
    "https://www.cfoleadershipcouncil.com": ("National", []),
    "https://www.cfoalliance.com": ("National", []),
    "https://www.controllerscouncil.org": ("National", []),
    "https://www.financialexecutives.org": ("National", ["New York", "Chicago"]),
    "https://www.financialprofessionals.org": ("National", []),
    "https://www.fsn.co.uk": ("Global", []),
    "https://www.cfo-circle.com": ("National", []),
    "https://www.airbase.com/off-the-ledger": ("National", []),
    "https://www.rillet.com/community": ("National", []),
    "https://cfoconnect.eu": ("Global", ["London", "Paris", "Berlin"]),
    "https://www.datarails.com/community": ("National", []),
    "https://www.financealliance.io": ("National", ["New York", "Boston", "Chicago"]),
    "https://cfo.chat": ("National", []),
    "https://www.proformative.com": ("National", []),
    "https://www.fifcollective.com": ("National", ["New York"]),
    "https://www.femalesandfinance.com": ("National", []),
    "https://www.fwa.org": ("Regional", ["New York"]),
    "https://www.cfomeet.org": ("National", ["Boston", "Chicago", "Toronto"]),
    "https://www.bostoncorporatefinancecommunity.com": ("Regional", ["Boston"]),
    "https://www.privateequitycfo.org": (
        "National",
        ["Boston", "New York", "Washington DC", "Baltimore", "Chicago", "Dallas–Fort Worth", "Houston", "Los Angeles"],
    ),
    "https://www.cfomastermindgroup.com": ("Regional", ["Chicago"]),
    "https://www.seniorexecutivenetwork.com": ("National", []),
    "https://www.hfma.org": ("National", []),
    "https://www.cfogroups.com": ("National", []),
    "https://www.startupcfo.tech": ("Global", []),
    "https://www.biocom.org": ("Regional", ["California"]),
    "https://informaconnect.com/finance-bioscience-east": ("Regional", ["Boston"]),
    "https://www.thecfoaccelerator.com/innercircle": ("National", []),
    "https://www.fractionalsunited.com": ("National", []),
    "https://www.treasurers.org": ("Global", ["UK"]),
    "https://www.conference-board.org/councils/corporate-treasurers": ("National", []),
}


def main():
    parser = argparse.ArgumentParser(description="Backfill communities.reach/metros from the approved mapping.")
    parser.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing.")
    args = parser.parse_args()

    lib = Library(args.db)
    updated = missing = 0
    try:
        rows = {r["url"]: r for r in lib.conn.execute("SELECT id, url, name, reach, metros_json FROM communities").fetchall()}
        for url, (reach, metros) in MAPPING.items():
            row = rows.get(url)
            if not row:
                print(f"  NOT FOUND in DB: {url}")
                missing += 1
                continue
            metros_json = json.dumps(metros)
            if row["reach"] == reach and row["metros_json"] == metros_json:
                print(f"  SKIP  {row['name']} (already {reach}, {metros})")
                continue
            print(f"  {'WOULD UPDATE' if args.dry_run else 'UPDATE'} {row['name']}: reach={reach}, metros={metros}")
            if not args.dry_run:
                lib.conn.execute(
                    "UPDATE communities SET reach=?, metros_json=? WHERE id=?",
                    (reach, metros_json, row["id"]),
                )
            updated += 1
        if not args.dry_run:
            lib.conn.commit()
    finally:
        lib.close()

    print(f"\n{updated} {'would be ' if args.dry_run else ''}updated, {missing} URL(s) in the mapping not found in the DB.")


if __name__ == "__main__":
    main()
