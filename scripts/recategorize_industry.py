#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 6 of the
post-#159 vocabulary overhaul.

Industry (`industry_tags`) is a wholly new dimension — no existing free-text
column to read back from. Vocabulary: Life sciences | Healthcare | Private
equity/funds | Industry-neutral. Per the Phase 0 investigation, this is
deliberately narrower than the existing "Industry-specific" category's own
framing (which also gestures at nonprofit and tech):
  - No community in the current 38 is nonprofit-focused — the one candidate
    (Non-Profit CFO Roundtable) was already removed via
    scripts/_community_profile_data.py's REMOVALS before this Recommender
    build even started.
  - "Tech" overlaps the stage_focus placeholder's in-flight Research Round 4
    work, so it's left out here rather than added as a contested option.

Only 4 of the 38 communities get a non-neutral tag: Finance & Accounting for
Bioscience (life sciences), Healthcare Financial Management Association
(healthcare), and Private Equity CFO Association + Private Funds CFO
Network (PE/funds) — all four explicitly scoped to one vertical in their own
ideal_member/value_prop text. Every other community is industry-neutral by
design (explicitly cross-industry, or simply not vertical-scoped).

Usage:
    python -m scripts.recategorize_industry --db library.db
    python -m scripts.recategorize_industry --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

NON_NEUTRAL_INDUSTRY_TAGS = {
    "Finance & Accounting for Bioscience (Informa Connect)": ["life_sciences"],
    "Healthcare Financial Management Association (HFMA)": ["healthcare"],
    "Private Equity CFO Association (PECFOA)": ["pe_funds"],
    "Private Funds CFO Network": ["pe_funds"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")
    args = parser.parse_args()

    lib = Library(args.db)
    try:
        rows = lib.conn.execute("SELECT id, name FROM communities").fetchall()

        matched = 0
        for r in rows:
            profile = lib.get_community_profile(r["id"])
            if profile is None:
                print(f"  skip (no profile row yet): {r['name']}")
                continue
            tags = NON_NEUTRAL_INDUSTRY_TAGS.get(r["name"], ["industry_neutral"])
            matched += 1
            print(f"  {r['name']}: {tags}")
            if not args.dry_run:
                lib.update_community_weight_tags(r["id"], industry_tags=tags)

        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(rows)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
