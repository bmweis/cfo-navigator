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
     team tier that gates real community access — Slack, events, deeper
     content).
  3. Leaves Proformative single-tagged as `["free"]` — confirmed, not
     pending. Its research text ("Free and premium content...") reads
     similarly to the freemium four at a glance, but Brian's read is that
     the pattern is actually different: the free tier (forums, webinars) IS
     the community/product itself, and the paid CPE courses are an add-on
     purchase on top of it, not a membership gate the way Finance Alliance/
     GaapSavvy/Startup CFO/CFO Connect's paid tiers are. Single-tagging
     reflects that distinction rather than a data gap.

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

# Deliberately excluded from FREEMIUM_DUAL_TAG despite similarly-worded
# freemium language in its own research text — confirmed by Brian as a
# different pattern from the freemium four: Proformative's free tier is the
# actual community/forum, and its paid CPE courses are an add-on purchase,
# not a membership gate. Single-tagged as Free only.
CONFIRMED_SINGLE_TAG = {"Proformative"}


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
            note += " [confirmed single-tag: free tier is the product, paid CPE is an add-on]" \
                if name in CONFIRMED_SINGLE_TAG else ""
            print(f"  {name} (cost_band={cost_band!r}): {tags}{note}")
            if not args.dry_run:
                lib.update_community_weight_tags(r["id"], paid_free_tags=tags)

        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(rows)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
