#!/usr/bin/env python3
"""Original Content tag taxonomy migration: moves any `original_content` row
still carrying a LEGACY free-text tag (anything outside the closed set
Guide / Playbook / Framework, e.g. the old "Setup Guide") onto the closed
set, with its derived link_label.

A row that already carries one of the three valid tags is an editorial
decision and is never retagged. That covers both a row this script already
normalized and a tag Brian set by hand in /admin/thought-leadership/original
after the taxonomy shipped: once a tag is valid, the script can't and
shouldn't tell the two apart, and it doesn't need to. The same standing rule
as the seed sync: scripts never silently overwrite an editorial decision.
(2026-09: the first version mapped chart-of-accounts to Playbook
unconditionally, so a run after Brian set it to Guide by hand would have
overwritten his choice. That's the bug this version fixes.)

For a valid-tag row the only thing this script may touch is link_label,
and only when it disagrees with the tag. link_label isn't editorial: the
admin form derives it from the tag on every save and won't accept one typed
by hand.

Legacy mappings (used only while a row's tag is still outside the closed set):

  slug                     legacy tag    ->  tag_label   link_label
  growth-engine-ratio      (any legacy)  ->  Framework   Read the framework
  ai-hackathon-playbook    (any legacy)  ->  Playbook    Read the playbook
  chart-of-accounts        (any legacy)  ->  Playbook    Read the playbook
  netsuite-mcp             (any legacy)  ->  Guide       Read the guide

A row with a legacy tag and a slug not in that table stops the run: the
script refuses to guess a tag and writes nothing.

Safe by default: preview only, no writes, unless --apply is passed. An
--apply run re-reads each row afterward and asserts tag_label/link_label
match (write-then-read-back, per CLAUDE.md's one-off-fix discipline).

Brian runs it via `railway ssh` with an absolute `--db /data/library.db`,
after reviewing the preview output.

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

# The closed tag set and each tag's derived link label. Mirrors
# webapp.app._OC_TAG_INFO; kept here so the script doesn't import the web app.
_TAG_LINK = {
    "Guide": "Read the guide",
    "Playbook": "Read the playbook",
    "Framework": "Read the framework",
}

# Used ONLY for a row whose tag is still outside _TAG_LINK.
_LEGACY_TARGETS = {
    "growth-engine-ratio": "Framework",
    "ai-hackathon-playbook": "Playbook",
    "chart-of-accounts": "Playbook",
    "netsuite-mcp": "Guide",
}


def plan(rows: list[dict]) -> tuple[list[tuple[dict, str, str, str]], list[str], list[dict]]:
    """Return (changes, messages, unknown).

    changes: (row, new_tag, new_link, why) for rows that need a write.
    unknown: legacy-tag rows whose slug has no mapping; any of these stops
    the run before a single write."""
    changes, messages, unknown = [], [], []
    for row in rows:
        slug, tag, link = row["slug"], row["tag_label"], row["link_label"]
        if tag in _TAG_LINK:
            want_link = _TAG_LINK[tag]
            if link == want_link:
                messages.append(f"  left alone: {slug} -> tag_label={tag!r} is already a valid tag "
                                f"(editorial, never retagged)")
            else:
                changes.append((row, tag, want_link, "link label out of step with tag"))
                messages.append(f"  would change: {slug} -- tag_label {tag!r} kept; link_label "
                                f"{link!r} -> {want_link!r}")
            continue
        if slug not in _LEGACY_TARGETS:
            unknown.append(row)
            continue
        new_tag = _LEGACY_TARGETS[slug]
        changes.append((row, new_tag, _TAG_LINK[new_tag], "legacy tag"))
        messages.append(f"  would change: {slug} -- legacy tag_label {tag!r} -> {new_tag!r}, "
                        f"link_label {link!r} -> {_TAG_LINK[new_tag]!r}")
    return changes, messages, unknown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write. Without this flag, only a preview is printed.")
    args = ap.parse_args(argv)
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Target database: {args.db}\n")

    lib = Library(args.db)
    try:
        to_change, messages, unknown = plan(lib.list_original_content())
        for m in messages:
            print(m)
        if unknown:
            print(f"STOP: {len(unknown)} row(s) carry a legacy tag this script has no mapping for: "
                  f"{sorted((r['slug'], r['tag_label']) for r in unknown)}. Not touching anything. "
                  f"Set the tag in /admin/thought-leadership/original instead.", file=sys.stderr)
            return 1
        if not to_change:
            print("\nNothing to do.")
            return 0
        if not args.apply:
            print("\nPREVIEW ONLY. No DB writes. Re-run with --apply to write for real.")
            return 0

        for row, new_tag, new_link, _why in to_change:
            lib.update_original_content(
                row["id"], row["slug"], row["title"], row["teaser"], new_tag, new_link,
                row["body_md"], row["status"], bool(row["featured_home"]), row["date_label"],
                row["sort_key"], row["display_order"], source="script",
            )
        print(f"\nApplied. Updated {len(to_change)} row(s).\n")

        ok = True
        for row, new_tag, new_link, _why in to_change:
            fresh = lib.get_original_content(row["id"])
            if fresh["tag_label"] != new_tag or fresh["link_label"] != new_link:
                ok = False
                print(f"  MISMATCH: {row['slug']}: expected {new_tag!r}/{new_link!r}, got "
                      f"{fresh['tag_label']!r}/{fresh['link_label']!r}", file=sys.stderr)
            else:
                print(f"  confirmed: {row['slug']} -> tag_label={fresh['tag_label']!r}, "
                      f"link_label={fresh['link_label']!r}")
        if not ok:
            print("\nERROR: one or more rows did not verify after the update.", file=sys.stderr)
            return 1
        print(f"\nVerified {len(to_change)} row(s).")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
