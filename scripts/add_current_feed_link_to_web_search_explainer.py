#!/usr/bin/env python3
"""One-time production content fix: adds a real link to the new
/current-feed page inside the "web-search" ai_surfaces row's body_md.

That row's body_md already describes the allowlist ("Which sites? The
same ones that fill my own feed... One list doing two jobs: it populates
my reader, and it tells FP&A Buddy where to look.") but had nothing to
link to when it was written — /current-feed didn't exist yet. This is
the "placeholder note where this link should go" the /current-feed build
brief names as its third entry point.

This is a targeted string replacement against the row's CURRENT body_md,
not a full-text rewrite — the row is real, already-migrated production
content (edited at least once via /admin/ai-surfaces, per its own
updated_at), and this script has no way to know whether Brian has since
edited that paragraph further. It refuses to run (prints the current text
and exits nonzero) if the exact sentence it expects to replace isn't
found verbatim, rather than silently no-opping or clobbering something
else — same "don't guess past what's actually there" discipline as every
other single-record admin fix in this repo.

Preview by default; --apply to write for real, write-then-read-back
verified (per CLAUDE.md's "one-off admin fixes" standing rule).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linklib.db import Library, resolve_db_path

SLUG = "web-search"

OLD_SENTENCE = (
    "One list doing two jobs: it populates my reader, and it tells FP&A "
    "Buddy where to look."
)
NEW_SENTENCE = (
    "One list doing two jobs: it populates my reader, and it tells FP&A "
    "Buddy where to look. See the [current feed](/current-feed)."
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the change. Without this flag, only a preview "
                          "is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        row = lib.get_ai_surface_by_slug(SLUG)
        if row is None:
            print(f"No ai_surfaces row with slug={SLUG!r} — nothing to do.")
            return 1

        body = row["body_md"] or ""
        if NEW_SENTENCE in body:
            print("Already applied — the current-feed link is already present. Nothing to do.")
            return 0
        if OLD_SENTENCE not in body:
            print("Expected sentence not found verbatim in the current body_md — refusing to "
                  "guess. The row has likely been edited since this script was written. "
                  "Add the /current-feed link by hand via /admin/ai-surfaces instead.\n")
            print("--- current body_md ---")
            print(body)
            return 1

        new_body = body.replace(OLD_SENTENCE, NEW_SENTENCE, 1)
        print(f"Row id={row['id']}, title={row['title']!r}.")
        print("Would replace:\n  " + OLD_SENTENCE)
        print("with:\n  " + NEW_SENTENCE)

        if not args.apply:
            print("\nPREVIEW ONLY — no DB writes. Re-run with --apply to write for real.")
            return 0

        lib.update_ai_surface(
            row["id"], row["slug"], row["title"], row["teaser"], new_body,
            row["external_href"], row["status"], row["display_order"], source="script",
        )
        print("\nApplied.")

        # Write-then-read-back.
        after = lib.get_ai_surface_by_slug(SLUG)
        assert after["body_md"] == new_body, "body_md did not round-trip"
        print(f"Verified — read back {len(after['body_md'])} chars of body_md, "
              "link present.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
