#!/usr/bin/env python3
"""One-off fix for 49 articles stuck as feedproxy.google.com redirect stubs
(0 words, never resolved) that were NOT in the needs-manual-review queue —
so the existing /admin/reader/backfill-content CSV-import tool couldn't
reach them (`Library.apply_article_url_correction`, gated at the CSV-parse
layer by `parse_manual_review_corrections_csv`, which only accepts an
article_id already in `list_articles_needing_manual_review()`). Brian
manually followed each feedproxy redirect to its real destination URL.

This script calls `Library.apply_article_url_correction` directly — the
Library method itself has no needs-manual-review gate, only the CSV-import
route does, so this is a legitimate direct use of an existing, tested
mechanism, not a new one. Same durable url_correction_log trace, same
UNIQUE-constraint collision handling as the CSV-import route
(`webapp/app.py`'s `admin_backfill_content_apply_corrections`): a
corrected URL that collides with an already-saved article is skipped and
reported, never crashes the batch (the earlier 500-bug scenario this
mirrors).

After the URL corrections, runs `pipeline.backfill_article_content` for
every successfully-corrected article so it picks up real content against
its new URL immediately, rather than waiting on the next general Reader
content-backfill sweep.

Safe by default: preview only (prints the planned old->new url change per
row), no writes, unless --apply is passed. Per the standing one-off-fix
write-then-read-back practice, an --apply run re-reads each corrected row
afterward and confirms articles.url now matches the intended value before
running the backfill fetch.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from linklib.db import Library, resolve_db_path

# (article_id, corrected_url) — Brian's manually-resolved feedproxy destinations.
PAIRS: list[tuple[int, str]] = [
    (44, "https://avc.com/2019/01/executive-sessions-and-continuous-feedback/"),
    (52, "https://feld.com/archives/2019/04/reflections-on-board-members-by-some-great-ones/"),
    (54, "https://avc.com/2019/08/striking-the-right-balance/"),
    (60, "https://avc.com/2020/07/when-do-i-create-a-board/"),
    (61, "https://avc.com/2020/07/reviewing-the-ceos-performance/"),
    (62, "https://avc.com/2020/06/board-diversity/"),
    (65, "https://avc.com/2020/08/independent-director-compensation/"),
    (124, "https://avc.com/2018/10/fully-diluted-market-value/"),
    (125, "https://avc.com/2018/11/what-happens-when-a-founder-is-fully-vested/"),
    (126, "https://avc.com/2019/03/golden-handcuffs/"),
    (134, "https://avc.com/2010/11/employee-equity-how-much/"),
    (148, "https://avc.com/2020/08/pay-and-precedent/"),
    (208, "https://avc.com/2019/02/the-doubling-model-for-fundraising/"),
    (213, "https://avc.com/2019/08/hypothetical-value-to-real-value/"),
    (214, "https://avc.com/2020/07/haggling/"),
    (378, "https://avc.com/2019/03/the-finance-function-looking-back-and-looking-forward/"),
    (413, "https://avc.com/2018/12/the-profit-motive/"),
    (417, "https://avc.com/2019/11/cash-management-in-startups/"),
    (492, "https://avc.com/2019/11/priorities/"),
    (494, "https://feld.com/archives/2019/12/changing-how-you-think-about-budgets/"),
    (596, "https://avc.com/2019/03/the-ipo-price-and-the-s1/"),
    (606, "https://feld.com/archives/2019/07/why-gross-profit-is-more-important-than-revenue/"),
    (671, "https://avc.com/2019/02/raising-a-safe-or-convertible-note-in-between-rounds/"),
    (755, "https://avc.com/2018/11/broken-syndicates/"),
    (760, "https://feld.com/archives/2019/05/linkedin-learning-raising-venture-capital-and-validating-your-startup-idea/"),
    (998, "https://avc.com/2018/12/missing-the-forest-through-the-trees/"),
    (1003, "https://avc.com/2017/01/reserves/"),
    (1004, "https://avc.com/2019/08/returning-the-fund/"),
    (1006, "https://avc.com/2019/10/pacing/"),
    (1007, "https://avc.com/2020/02/the-long-buy/"),
    (1015, "https://avc.com/2020/08/subscription-agreements/"),
    (1017, "https://feld.com/archives/2021/02/dealing-with-reality-in-business/"),
    (1021, "https://avc.com/2021/05/half-of-all-vcs-beat-the-stock-market/"),
    (1059, "https://feld.com/archives/2018/10/disagree-and-commit/"),
    (1067, "https://feld.com/archives/2019/10/thought-leadership-vs-cult-of-personality/"),
    (1079, "https://avc.com/2021/05/grit-resilience-and-determination/"),
    (1090, "https://feld.com/archives/2019/02/three-year-future-org-chart-exercise/"),
    (1096, "https://avc.com/2020/10/removing-the-ceo/"),
    (1100, "https://feld.com/archives/2021/04/calculating-leader-leverage/"),
    (1117, "https://avc.com/2019/07/turning-a-loss-into-a-win/"),
    (1119, "https://avc.com/2020/07/crawl-walk-run/"),
    (1131, "https://www.fastcompany.com/90213545/exclusive-spotify-ceo-daniel-ek-on-apple-facebook-netflix-and-the-future-of-music"),
    (1146, "https://avc.com/2018/12/negotiating-drawing-a-hard-line-or-building-a-negotiating-cushion/"),
    (1195, "https://feld.com/archives/2019/03/a-boundless-decision/"),
    (1234, "https://feld.com/archives/2021/05/427-mondays-in-a-row/"),
    (1261, "https://avc.com/2021/04/paternalism-in-the-office/"),
    (1316, "https://www.usv.com/writing/2017/12/how-to-hire-for-hr-lessons-from-18-usv-people-ops-leaders/"),
    (1327, "https://www.lennysnewsletter.com/p/how-todays-fastest-growing-b2b-businesses-b11"),
    (1402, "https://tomtunguz.com/selling-your-product-as-you-build-it/"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true", help="Actually write the changes (default: preview only)")
    ap.add_argument("--skip-backfill", action="store_true",
                     help="Skip the post-correction content backfill fetch (URL fix only)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"Mode: {'APPLY (writing)' if args.apply else 'PREVIEW (no writes)'}")
    print()

    lib = Library(db_path)

    corrected_ids: list[int] = []
    skipped: list[str] = []
    not_found: list[str] = []

    for article_id, new_url in PAIRS:
        current = lib.get_article(article_id)
        if current is None:
            not_found.append(f"article #{article_id}: not found in database")
            print(f"  #{article_id}: NOT FOUND — skipping")
            continue

        old_url = current.get("url") or ""
        print(f"  #{article_id}: {old_url!r} -> {new_url!r}")

        if not args.apply:
            continue

        try:
            ok = lib.apply_article_url_correction(article_id, new_url, source="feedproxy-fix")
        except sqlite3.IntegrityError:
            owner = lib.conn.execute(
                "SELECT id FROM articles WHERE url=?", (new_url,)).fetchone()
            owner_id = owner["id"] if owner else "?"
            msg = f"article #{article_id}: corrected_url already belongs to article #{owner_id} — skipped"
            skipped.append(msg)
            print(f"    SKIPPED: {msg}")
            continue
        except Exception as e:
            msg = f"article #{article_id}: failed — {e}"
            skipped.append(msg)
            print(f"    FAILED: {msg}")
            continue

        if not ok:
            not_found.append(f"article #{article_id}: apply_article_url_correction returned False")
            print("    FAILED: update did not apply")
            continue

        # Write-then-read-back verification, per CLAUDE.md's one-off-fix rule.
        verify = lib.get_article(article_id)
        if not verify or verify.get("url") != new_url:
            msg = f"article #{article_id}: post-write verification FAILED (url={verify.get('url') if verify else None!r})"
            skipped.append(msg)
            print(f"    VERIFY FAILED: {msg}")
            continue

        print(f"    OK — verified url={verify['url']!r}")
        corrected_ids.append(article_id)

    print()
    print(f"Planned: {len(PAIRS)} rows")
    if args.apply:
        print(f"Corrected: {len(corrected_ids)}")
        print(f"Skipped (collision/error): {len(skipped)}")
        print(f"Not found: {len(not_found)}")
        if skipped:
            print("\nSkipped rows:")
            for s in skipped:
                print(f"  - {s}")
        if not_found:
            print("\nNot-found rows:")
            for s in not_found:
                print(f"  - {s}")

    if not args.apply:
        print("\nPreview only — pass --apply to write these changes.")
        return 0

    if args.skip_backfill:
        print("\n--skip-backfill passed — not re-fetching content. Run the general "
              "Reader content backfill separately to pick up real content for these rows.")
        return 0

    if not corrected_ids:
        print("\nNo articles were successfully corrected — nothing to backfill.")
        return 0

    print(f"\nRunning content backfill for {len(corrected_ids)} corrected article(s)...")
    from linklib.pipeline import backfill_article_content

    backfill_ok = 0
    backfill_fail: list[str] = []
    for article_id in corrected_ids:
        article = lib.get_article(article_id)
        if not article:
            backfill_fail.append(f"#{article_id}: article vanished before backfill")
            continue
        ok, reason = backfill_article_content(lib, article)
        if ok:
            backfill_ok += 1
            print(f"  #{article_id}: content backfilled OK")
        else:
            backfill_fail.append(f"#{article_id}: {reason}")
            print(f"  #{article_id}: backfill failed — {reason}")

    print(f"\nBackfill: {backfill_ok} succeeded, {len(backfill_fail)} failed")
    if backfill_fail:
        print("Failed backfill rows (see content_refetch_log for detail):")
        for s in backfill_fail:
            print(f"  - {s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
