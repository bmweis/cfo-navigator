#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 5 of the
post-#159 vocabulary overhaul.

Dues (`paid_free_tags`) moves from a `communities.cost_band`-derived
computation (a single value — `["free"]` if `cost_band == "Free"` else
`["paid"]`) to its own dedicated tags column, because a single value can
never represent a freemium community that genuinely has both a free tier
and a paid tier. This script:

  1. Seeds every community's `paid_free_tags` from its *current* `cost_band`
     (read live from the DB, not hardcoded — unlike the other
     recategorize_*.py scripts, the base fact here already lives on the
     `communities` row), replicating today's derived behavior exactly so no
     community's Dues match silently changes just from this migration.
  2. Applies the known-freemium override — Finance Alliance, GaapSavvy,
     Startup CFO, and CFO Connect — to `["free", "paid"]`, per their
     business_model research text (a free tier plus a separate paid Pro/
     team tier).
  3. Deliberately leaves Proformative single-tagged (whatever its cost_band
     derives to) even though its own research text ("Free and premium
     content...") reads similarly ambiguous — per Brian's instruction, this
     one gets a manual review rather than an automatic dual-tag.

Usage:
    python -m scripts.recategorize_dues --db library.db
    python -m scripts.recategorize_dues --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# Communities confirmed by research to have both a free tier and a separate
# paid tier — see each one's business_model prose in
# scripts/_community_profile_data.py.
FREEMIUM_DUAL_TAG = {
    "Finance Alliance",
    "GaapSavvy",
    "Startup CFO",
    "CFO Connect",
}

# Deliberately excluded from FREEMIUM_DUAL_TAG despite similarly ambiguous
# freemium language in its own research text — Brian wants to review this
# one manually before it gets dual-tagged.
LEAVE_SINGLE_TAG_PENDING_REVIEW = {"Proformative"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")
    args = parser.parse_args()

    lib = Library(args.db)
    try:
        rows = lib.conn.execute("SELECT id, name, cost_band FROM communities").fetchall()

        matched = 0
        for r in rows:
            name, cost_band = r["name"], r["cost_band"]
            profile = lib.get_community_profile(r["id"])
            if profile is None:
                print(f"  skip (no profile row yet): {name}")
                continue
            if name in FREEMIUM_DUAL_TAG:
                tags = ["free", "paid"]
            else:
                # Replicates the retiring derived logic exactly.
                tags = ["free"] if cost_band == "Free" else ["paid"]
            matched += 1
            note = " [known freemium, dual-tagged]" if name in FREEMIUM_DUAL_TAG else ""
            note += " [ambiguous freemium text, left single-tagged pending manual review]" \
                if name in LEAVE_SINGLE_TAG_PENDING_REVIEW else ""
            print(f"  {name} (cost_band={cost_band!r}): {tags}{note}")
            if not args.dry_run:
                lib.update_community_weight_tags(r["id"], paid_free_tags=tags)

        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(rows)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
