#!/usr/bin/env python3
"""Read-only diagnostic for the JS-render vendor-research grounding defect
(Lumera, 2026-09-25): `generate_tool_description`/`generate_tool_agent_taxonomy`/
`generate_community_profile`/`generate_community_listing` (linklib/enrich.py) all
decide "did the page fetch succeed" with `not bool(page.content.strip())` — ANY
non-empty text counts as success, including the near-empty shell a client-
rendered (JS) page returns to a plain HTTP GET. A vendor whose homepage needs
JavaScript to render can end up with a "Source page fetch: Succeeded" badge, an
empty Sources list, and a low-confidence draft that reads as a real finding
("this vendor's workflows aren't established") rather than a failed fetch.

Two modes, no writes either way:

1. Default — audits `tools`/`community_profiles` for the exact triple Lumera
   showed: fetch marked "succeeded" (`*_low_confidence=0`), the model's own
   self-report was NOT confident (`*_ai_confident=0`), and nothing was actually
   recorded in `entity_citations` for that field. `entity_citations` is an
   UPSERT (linklib/db.py `set_entity_citations`) that writes `citations_json=
   '[]'` rather than deleting the row on a clear — so "no sources recorded"
   means EITHER no row at all OR a row present with `citations_json='[]'`, and
   this query treats both as the same thing (`citations_json != '[]'` as the
   "has real citations" test, not a bare `IS NULL` check on the join).

   Reports two numbers, not one: IDENTIFIABLE (rows that have actually been
   through this exact instrumented code path — the `*_low_confidence` column is
   non-NULL for tools, or a `community_profiles` row exists at all for
   communities) and UNKNOWABLE (everything else — predates the column, or was
   never regenerated under it). A single combined count would understate the
   real backlog by roughly 4x per the 2026-09-26 sample (~24% of tools carry
   any `description_low_confidence` signal at all). Resolving the unknowable
   bucket means re-fetching and comparing — a separate job, not built here.

2. `--probe <url>` — runs the real `extract.fetch_page()` + `extract.
   assess_extraction_quality()` against one URL and prints what a human would
   need to judge it: character count, word count, the (ok, reason) verdict, and
   the first 300 characters of extracted text. No AI call, no DB dependency,
   free. This is how the Lumera reproduction actually happens — run it from
   wherever has real network egress (railway ssh, or a dev machine), not a
   sandboxed build session, which is how this defect was originally
   investigated with no way to make this exact call.

   `--exa` (with `--name "Vendor Name"`) additionally runs the canonical
   feature scan's own Exa search (linklib.feature_scan.research_vendor_domain)
   against the same URL and reports whether Exa's crawl already gets past the
   JS shell where a plain fetch can't — settles whether the feature scan (a
   completely separate fetch mechanism, no shared code with the four
   generate_*() functions above) has the same gap or not. This makes a REAL,
   PAID Exa API call (a few cents) — never run in tests or CI, opt-in only.

Usage:
    python -m scripts.audit_thin_fetch_grounding --db /data/library.db
    python -m scripts.audit_thin_fetch_grounding --probe https://www.lumerahq.com
    python -m scripts.audit_thin_fetch_grounding --probe https://www.lumerahq.com \\
        --exa --name Lumera
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

# The 12 Community profile fields carrying a real self-reported confidence
# signal (linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS) — the other 11 of the 23
# COMMUNITY_PROFILE_FIELDS are short factual/categorical values with no
# *_ai_confident column at all.
_COMMUNITY_CONFIDENCE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
    "engagement_level", "sponsor_relationship_note", "application_friction",
    "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary",
]

_NO_SOURCES_PREDICATE = """NOT EXISTS (
        SELECT 1 FROM entity_citations ec
        WHERE ec.entity_type = ? AND ec.entity_id = {id_expr} AND ec.field_name = ?
          AND ec.citations_json != '[]'
    )"""


def _tool_field_audit(lib: Library, field: str) -> dict:
    """One field's counts + affected rows for `tools` (field is 'description'
    or 'agent_taxonomy'). identifiable/unknowable partition ALL approved
    tools by whether `{field}_low_confidence` has ever been set at all."""
    lc_col = f"{field}_low_confidence"
    conf_col = f"{field}_ai_confident"
    conn = lib.conn

    identifiable = conn.execute(
        f"SELECT COUNT(*) AS n FROM tools WHERE approved=1 AND {lc_col} IS NOT NULL"
    ).fetchone()["n"]
    unknowable = conn.execute(
        f"SELECT COUNT(*) AS n FROM tools WHERE approved=1 AND {lc_col} IS NULL"
    ).fetchone()["n"]

    rows = conn.execute(
        f"""SELECT id, name, slug FROM tools
            WHERE approved=1 AND {lc_col}=0 AND {conf_col}=0
              AND {_NO_SOURCES_PREDICATE.format(id_expr='id')}
            ORDER BY name""",
        ("tool", field),
    ).fetchall()

    return {
        "field": field, "identifiable": identifiable, "unknowable": unknowable,
        "affected": [dict(r) for r in rows],
    }


def _community_audit(lib: Library) -> dict:
    """Communities' own shape: one whole-profile `low_confidence` (not per-
    field), one `'community_profile'` citations row, and 12 per-field
    `*_ai_confident` columns — reported as a not-confident-count out of 12
    per affected community rather than picked apart into 12 separate lists."""
    conn = lib.conn

    identifiable = conn.execute(
        """SELECT COUNT(*) AS n FROM communities c
           JOIN community_profiles cp ON cp.community_id = c.id
           WHERE c.approved=1"""
    ).fetchone()["n"]
    unknowable = conn.execute(
        """SELECT COUNT(*) AS n FROM communities c
           WHERE c.approved=1
             AND NOT EXISTS (SELECT 1 FROM community_profiles cp WHERE cp.community_id = c.id)"""
    ).fetchone()["n"]

    not_confident_expr = " + ".join(
        f"CASE WHEN cp.{f}_ai_confident=0 THEN 1 ELSE 0 END" for f in _COMMUNITY_CONFIDENCE_FIELDS
    )
    rows = conn.execute(
        f"""SELECT c.id, c.name, c.slug, ({not_confident_expr}) AS not_confident_count
            FROM community_profiles cp
            JOIN communities c ON c.id = cp.community_id
            WHERE c.approved=1 AND cp.low_confidence=0
              AND {_NO_SOURCES_PREDICATE.format(id_expr='cp.community_id')}
            ORDER BY not_confident_count DESC, c.name""",
        ("community", "community_profile"),
    ).fetchall()

    return {
        "identifiable": identifiable, "unknowable": unknowable,
        "affected": [dict(r) for r in rows],
    }


def _print_tool_result(result: dict) -> None:
    field = result["field"]
    print(f"\n=== tools.{field} ===")
    print(f"  identifiable (has a {field}_low_confidence value at all): {result['identifiable']}")
    print(f"  unknowable (predates the column / never regenerated):     {result['unknowable']}")
    affected = result["affected"]
    print(f"  affected (succeeded + not confident + no sources recorded): {len(affected)}")
    for r in affected:
        print(f"    - #{r['id']:>5}  {r['name']}  ({r['slug']})")


def _print_community_result(result: dict) -> None:
    print("\n=== community_profiles (whole-profile) ===")
    print(f"  identifiable (has a community_profiles row at all): {result['identifiable']}")
    print(f"  unknowable (no profile drafted yet):                {result['unknowable']}")
    affected = result["affected"]
    print(f"  affected (succeeded + no sources recorded), by not-confident-field count: {len(affected)}")
    for r in affected:
        print(f"    - #{r['id']:>5}  {r['name']}  ({r['slug']})  "
              f"{r['not_confident_count']}/12 fields not confident")


def _run_audit(db_path: str) -> None:
    lib = Library(db_path)
    try:
        for field in ("description", "agent_taxonomy"):
            _print_tool_result(_tool_field_audit(lib, field))
        _print_community_result(_community_audit(lib))
    finally:
        lib.close()
    print(
        "\nNote: the UNKNOWABLE counts above cannot be resolved from this data — "
        "a NULL/no-row state means the field predates this instrumentation or was "
        "never regenerated since. Determining whether any of those are ALSO thin-"
        "fetch false positives means re-fetching and comparing, which is a separate "
        "job (file it as an issue, don't build it as part of this audit)."
    )


def _run_probe(url: str, exa: bool, name: str | None) -> None:
    from linklib import extract

    print(f"Fetching {url} ...")
    page = extract.fetch_page(url)
    if page.fetch_error:
        print(f"fetch_error: {page.fetch_error}")
        return

    char_count = len(page.content)
    word_count = len(page.content.split())
    ok, reason = extract.assess_extraction_quality(page.raw_html, page.content, page.blocked)

    print(f"title: {page.title!r}")
    print(f"blocked (looks paywalled): {page.blocked}")
    print(f"plain-text character count: {char_count}")
    print(f"plain-text word count: {word_count}")
    print(f"assess_extraction_quality: ok={ok!r} reason={reason!r}")
    print("first 300 characters of extracted text:")
    print(repr(page.content[:300]))

    # For comparison against the buggy rule this audit exists to catch:
    old_rule_low_confidence = not bool(page.content.strip())
    print(
        f"\nold rule (`not bool(page.content.strip())`): "
        f"low_confidence={old_rule_low_confidence} "
        f"({'Failed' if old_rule_low_confidence else 'Succeeded'})"
    )

    if not exa:
        return

    if not name:
        sys.exit("--exa requires --name \"Vendor Name\" (Exa's query is name-based).")
    if not os.environ.get("EXA_API_KEY"):
        sys.exit("--exa requires EXA_API_KEY to be set — this makes a real, paid Exa call.")

    print(f"\nRunning Exa search for {name!r} against {url} — this spends real Exa API "
          f"credit (a few cents) ...")
    from linklib.feature_scan import research_vendor_domain

    hits, cost = research_vendor_domain(name, url)
    print(f"Exa cost: ${cost:.4f}")
    print(f"Exa hits: {len(hits)}")
    for h in hits:
        print(f"  - tier {h.tier}  {h.title!r}  {h.url}")
        print(f"    text length: {len(h.text)} chars — {h.text[:200]!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument("--probe", metavar="URL", default=None,
                         help="Run extract.fetch_page()+assess_extraction_quality() against one URL "
                              "instead of the default DB audit. No DB is touched in this mode.")
    parser.add_argument("--exa", action="store_true",
                         help="With --probe: also run a REAL, PAID Exa search (research_vendor_domain) "
                              "against the same URL. Requires --name and EXA_API_KEY.")
    parser.add_argument("--name", default=None, help="Vendor name, required by --probe --exa.")
    args = parser.parse_args()

    if args.probe:
        _run_probe(args.probe, exa=args.exa, name=args.name)
        return

    db_path = resolve_db_path(args.db)
    _run_audit(db_path)


if __name__ == "__main__":
    main()
