#!/usr/bin/env python3
"""Original Content tag taxonomy migration — normalizes the four existing
`original_content` rows onto the new closed three-value tag set (Guide /
Playbook / Framework) and their derived link_label, per CLAUDE.md's
"Original content — tag taxonomy" build.

Two of the four rows are a real reclassification, not a mechanical
rename — flagged explicitly rather than silently mapped:

  slug                     tag_label now  ->  tag_label after   link_label after
  growth-engine-ratio      Framework      ->  Framework         Read the framework
  ai-hackathon-playbook    Playbook       ->  Playbook          Read the playbook
  chart-of-accounts        Setup Guide    ->  Playbook          Read the playbook
  netsuite-mcp             Setup Guide    ->  Guide              Read the guide

chart-of-accounts is steps for designing and maintaining a chart of
accounts — an action, which is a Playbook — and its teaser/link label
already said so; only tag_label was out of step. netsuite-mcp is a setup
manual you follow once and refer back to — a Guide.

Only these four slugs are touched. If a fifth original_content row exists
in the live database, this script refuses to guess at its tag and reports
it instead of silently leaving it alone or normalizing it incorrectly.

Safe by default: preview only, no writes, unless --apply is passed. An
--apply run re-reads each row afterward and asserts tag_label/link_label
match the expected new values before reporting success (write-then-
read-back, per CLAUDE.md's standing one-off-fix discipline).

Idempotent: a row already at its target tag_label/link_label is reported
as "already normalized" and left untouched.

NOT run with --apply against production as part of building this PR —
Brian runs it via `railway ssh` (absolute `--db /data/library.db`), after
reviewing this preview output.

Usage:
    python -m scripts.normalize_original_content_tags --db /data/library.db            # preview
    python -m scripts.normalize_original_content_tags --db /data/library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

# slug -> (new tag_label, new link_label)
_TARGETS = {
    "growth-engine-ratio": ("Framework", "Read the framework"),
    "ai-hackathon-playbook": ("Playbook", "Read the playbook"),
    "chart-of-accounts": ("Playbook", "Read the playbook"),
    "netsuite-mcp": ("Guide", "Read the guide"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the new tag_label/link_label values. Without this "
                          "flag, only a preview is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Target database: {args.db}\n")

    lib = Library(args.db)
    try:
        all_rows = lib.list_original_content()
        known_slugs = set(_TARGETS)
        live_slugs = {r["slug"] for r in all_rows}
        unexpected = live_slugs - known_slugs
        missing = known_slugs - live_slugs
        if missing:
            print(f"ERROR: expected slug(s) not found in original_content: {sorted(missing)} — "
                  f"refusing to guess. Live slugs: {sorted(live_slugs)}", file=sys.stderr)
            return 1
        if unexpected:
            print(f"STOP: original_content has {len(unexpected)} slug(s) this script doesn't "
                  f"know about: {sorted(unexpected)}. Not touching anything — confirm what tag "
                  f"they should have before running this, rather than guessing.", file=sys.stderr)
            return 1

        rows_by_slug = {r["slug"]: r for r in all_rows}
        to_change = []
        for slug, (new_tag, new_link) in _TARGETS.items():
            row = rows_by_slug[slug]
            if row["tag_label"] == new_tag and row["link_label"] == new_link:
                print(f"  already normalized: {slug} -> tag_label={new_tag!r}, link_label={new_link!r}")
                continue
            to_change.append((row, new_tag, new_link))
            print(f"  would change: {slug} -- tag_label {row['tag_label']!r} -> {new_tag!r}, "
                  f"link_label {row['link_label']!r} -> {new_link!r}")

        if not to_change:
            print("\nNothing to do — every row already matches its target tag/link label.")
            return 0

        if not args.apply:
            print("\nPREVIEW ONLY — no DB writes. Re-run with --apply to write for real.")
            return 0

        for row, new_tag, new_link in to_change:
            lib.update_original_content(
                row["id"], row["slug"], row["title"], row["teaser"], new_tag, new_link,
                row["body_md"], row["status"], bool(row["featured_home"]), row["date_label"],
                row["sort_key"], row["display_order"], source="script",
            )
        print(f"\nApplied — updated {len(to_change)} row(s).\n")

        # Write-then-read-back.
        ok = True
        for row, new_tag, new_link in to_change:
            fresh = lib.get_original_content(row["id"])
            if fresh["tag_label"] != new_tag or fresh["link_label"] != new_link:
                ok = False
                print(f"  MISMATCH: {row['slug']} — expected tag_label={new_tag!r}/"
                      f"link_label={new_link!r}, got tag_label={fresh['tag_label']!r}/"
                      f"link_label={fresh['link_label']!r}", file=sys.stderr)
            else:
                print(f"  confirmed: {row['slug']} -> tag_label={fresh['tag_label']!r}, "
                      f"link_label={fresh['link_label']!r}")
        if not ok:
            print("\nERROR: one or more rows did not verify after the update — see MISMATCH lines above.",
                  file=sys.stderr)
            return 1
        print(f"\nVerified {len(to_change)} row(s). Normalization complete.")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
