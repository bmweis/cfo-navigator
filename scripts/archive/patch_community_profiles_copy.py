#!/usr/bin/env python3
"""Patch already-imported community_profiles rows with the copy-quality fixes
from PR #153 (em dashes, "braintrust"/generic-word cleanup) — WITHOUT
re-running the bulk import, so it never re-triggers voice-rewrite or spends
API budget a second time.

Why this exists: scripts/archive/import_community_profiles.py voice-rewrites the 11
narrative fields via a live Claude call, so a row already imported against
production does NOT contain the raw text in scripts/_community_profile_data.py
— it contains Claude's rewritten version of whatever that file said at import
time. If it was imported before PR #153's cleanup, the live text still has
the original issues, but a blind file-level replace using the old/new pairs
from that cleanup won't reliably match, since voice-rewrite may have
reworded things unpredictably. This script instead tries three tiers, from
most to least confident, per field:

  1. Exact full-field match — the live text is byte-identical to the
     pre-cleanup source (this happens when the import ran without
     ANTHROPIC_API_KEY and saved the un-rewritten source text verbatim, per
     import_community_profiles.py's own documented fallback). Full
     replacement with the cleaned-up text.
  2. Fragment match — word-level diff fragments (with context padding) from
     the same cleanup, checked against the live text one at a time. Applied
     only where a fragment's old text is found in the live text EXACTLY
     ONCE, so nothing is ever guessed against ambiguous or absent text.
  3. Safe fallback — any spaced em dash (" — ") still present after tiers 1
     and 2 is converted to an unspaced em dash ("—"), never anything more
     opinionated. This is always policy-compliant on its own (an unspaced em
     dash is allowed) even when the specific fragment fix didn't match, and
     is also exactly what's wanted for The F Suite's notable_members
     exception if tier 1/2 didn't already handle it.

Everything not covered by tiers 1-2, after the tier-3 fallback still leaves
no spaced em dash, is left alone — if a field's live text doesn't contain a
matched fragment AND has no leftover spaced em dash, it's reported as
"unchanged" for manual review rather than silently skipped.

No ANTHROPIC_API_KEY needed — this never calls Claude. Costs nothing.

Usage:
    python -m scripts.archive.patch_community_profiles_copy --db library.db            # dry run (default)
    python -m scripts.archive.patch_community_profiles_copy --db library.db --apply    # write changes
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from scripts.archive._community_profile_copy_fixes import FULL_FIXES, FRAGMENT_FIXES


def _apply_fallback(text: str) -> tuple[str, bool]:
    """Tier 3: collapse any remaining spaced em dash to unspaced. Returns
    (new_text, changed)."""
    if " — " not in text:
        return text, False
    return text.replace(" — ", "—"), True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is dry-run: report only)")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)

    fragments_by_key: dict[tuple[str, str], list[dict]] = {}
    for frag in FRAGMENT_FIXES:
        fragments_by_key.setdefault((frag["name"], frag["field"]), []).append(frag)
    full_by_key = {(fx["name"], fx["field"]): fx for fx in FULL_FIXES}

    n_exact = n_fragment = n_fallback = n_unchanged = n_no_row = 0

    for key, full_fix in full_by_key.items():
        name, field = key
        row = lib.conn.execute(
            "SELECT communities.id, community_profiles.%s FROM communities "
            "LEFT JOIN community_profiles ON community_profiles.community_id = communities.id "
            "WHERE communities.name = ?" % field, (name,),
        ).fetchone()
        if not row or row[0] is None:
            print(f"NO ROW: {name} | {field} (community not found)")
            n_no_row += 1
            continue
        community_id, live_text = row
        live_text = live_text or ""
        if not live_text:
            # Nothing imported for this field yet (row exists but this field
            # is still empty) — not a review case, just not imported.
            continue
        if live_text == full_fix["new"]:
            # Already matches the cleaned-up text (e.g. hand-edited since
            # import, or coincidentally already compliant) — nothing to do.
            continue

        if live_text == full_fix["old"]:
            new_text = full_fix["new"]
            tier = "exact"
            n_exact += 1
        else:
            new_text = live_text
            applied_any = False
            for frag in fragments_by_key.get(key, []):
                count = new_text.count(frag["old_frag"])
                if count == 1:
                    new_text = new_text.replace(frag["old_frag"], frag["new_frag"], 1)
                    applied_any = True
            new_text, fell_back = _apply_fallback(new_text)
            if applied_any and fell_back:
                tier = "fragment+fallback"
                n_fragment += 1
            elif applied_any:
                tier = "fragment"
                n_fragment += 1
            elif fell_back:
                tier = "fallback-only"
                n_fallback += 1
            else:
                tier = "unchanged"
                n_unchanged += 1

        if new_text != live_text:
            print(f"[{tier}] {name} | {field}")
            print(f"  LIVE: {live_text}")
            print(f"  NEW:  {new_text}")
            if args.apply:
                lib.conn.execute(
                    f"UPDATE community_profiles SET {field}=?, updated_at=? WHERE community_id=?",
                    (new_text.strip(), _now(), community_id),
                )
        elif tier == "unchanged":
            print(f"[unchanged — needs manual review] {name} | {field}")
            print(f"  LIVE: {live_text}")

    if args.apply:
        lib.conn.commit()

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: "
          f"{n_exact} exact, {n_fragment} fragment-matched, {n_fallback} fallback-only, "
          f"{n_unchanged} unchanged (needs manual review), {n_no_row} rows not found.")
    if not args.apply:
        print("Re-run with --apply to write these changes.")

    lib.close()
    return 0


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
