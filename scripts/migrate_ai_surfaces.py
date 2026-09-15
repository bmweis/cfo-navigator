#!/usr/bin/env python3
"""One-time data migration (explainers-collection PR): moves the four AI
surface cards hardcoded in `webapp/app.py`'s `_AI_SURFACES` tuple into the
new `ai_surfaces` DB table, which the /how-this-is-built route now reads
from instead. See CLAUDE.md's "How this is built" / explainers-collection
entry and `linklib/db.py`'s `ai_surfaces` table comment for the full
reasoning.

Deliberately a manual, run-by-hand script — NOT wired into an automatic
boot hook, same "human review before a production write" rule
`scripts/archive/migrate_original_content.py` follows.

Migrated losslessly: each tuple entry supplies title/teaser and either an
existing href (FP&A Buddy's /tools/fpa-buddy/how-it-works — stored as
external_href, body_md left NULL, since that explainer's own page lives
outside this system entirely) or nothing (the three not-yet-written
explainers — slug derived from the title, status='draft', body_md NULL,
so each card renders exactly as it did before: unlinked, "Explainer
coming soon."). display_order preserves the tuple's original order.

Idempotent: guarded by an empty `ai_surfaces` table — if the table already
has any rows, the script reports that and does nothing.

Usage:
    python -m scripts.migrate_ai_surfaces --db library.db            # preview
    python -m scripts.migrate_ai_surfaces --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from webapp.app import _AI_SURFACES

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_from_title(title: str) -> str:
    return _SLUG_RE.sub("-", title.lower()).strip("-")


def planned_rows() -> list[dict]:
    """Flatten _AI_SURFACES into insert-ready dicts, in the tuple's own
    order (display_order = its index). Shared with tests that need the
    same seed data a real migration run would produce."""
    planned = []
    for i, (title, teaser, href) in enumerate(_AI_SURFACES):
        planned.append({
            "slug": _slug_from_title(title),
            "title": title,
            "teaser": teaser,
            "body_md": None,
            "external_href": href,
            # FP&A Buddy's own explainer already exists and is linked —
            # ship it Live. The other three have nothing to show yet, so
            # they start Draft (unlinked "coming soon.", same as today).
            "status": "live" if href else "draft",
            "display_order": i,
        })
    return planned


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"  order={r['display_order']} slug={r['slug']!r} status={r['status']!r} "
              f"{r['title']!r} (external_href={r['external_href']!r})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        existing = lib.list_ai_surfaces()
        if existing:
            print(f"ai_surfaces already has {len(existing)} row(s) — nothing to do. "
                  "(Idempotency guard: this script only ever runs against an empty table.)")
            return 0

        planned = planned_rows()
        print(f"{len(planned)} row(s) would be inserted:\n")
        _print_rows(planned)

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no DB writes. This is exactly the row list --apply would "
                "insert, in the same order. Re-run with --apply to write for real."
            )
            return 0

        for r in planned:
            lib.add_ai_surface(
                r["slug"], r["title"], r["teaser"], r["body_md"], r["external_href"],
                r["status"], r["display_order"],
            )
        print(f"\nApplied — {len(planned)} row(s) inserted.\n")

        after = lib.list_ai_surfaces()
        assert len(after) == len(planned), f"expected {len(planned)} rows, found {len(after)}"
        by_slug = {r["slug"]: r for r in after}
        for spot in (planned[0], planned[-1]):
            fresh = by_slug.get(spot["slug"])
            assert fresh is not None, f"{spot['slug']!r} missing after migration"
            assert fresh["title"] == spot["title"] and fresh["status"] == spot["status"], \
                f"{spot['slug']!r} landed with mismatched fields"
        print(f"Verified — {len(after)} row(s) read back from ai_surfaces, "
              "spot-checked fields match.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
