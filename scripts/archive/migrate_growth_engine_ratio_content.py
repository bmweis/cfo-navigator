#!/usr/bin/env python3
"""One-time data migration (Original Content Phase 4c): sets body_md on the
existing `growth-engine-ratio` original_content row, so the article half of
the page can be served through the shared article template
(GET /thought-leadership/{slug}) instead of the bespoke Python route. See
CLAUDE.md's Original Content Phase 4c entry for the full reasoning — same
pattern as Phase 4a's scripts/archive/migrate_netsuite_mcp_content.py and
Phase 4b's scripts/archive/migrate_hackathon_playbook_content.py (both
archived once their runs were confirmed — this script hasn't run against
production yet, so it stays in scripts/ until it has).

Unlike 4a/4b, this port SPLITS the retired route rather than porting it
whole: the ~380-line live JS calculator (two dynamically-generated SVG
charts, live inputs) is genuinely interactive, not markdown-representable
content, so it moved to its own new standalone bespoke route,
GET /thought-leadership/growth-engine-calculator (webapp/app.py's
growth_engine_calculator()) — extracted verbatim, no logic/input/chart
change. Only the article prose, formula box, pull-quotes, tier table, CTA
box, and Suno player come here, as body_md.

Copy is extracted VERBATIM from the retired webapp/app.py
growth_engine_ratio() route — no rewriting, no paraphrasing. Structure only
changed: prose/headings became real markdown; the visually-designed
elements (the formula box, the 3 pull-quotes using the existing
.article-pull.ger-pull classes, the tier table using .ger-table, the
F Suite CTA box using the existing sitewide .article-cta class, and the
Suno embed) are raw HTML blocks. .ger-pull/.ger-table's page-specific CSS
moved into the shared template's scoped _OC_GER_CSS (webapp/app.py) — the
same treatment _OC_NETSUITE_MCP_CSS/_OC_HACKATHON_CSS already got in the
prior two phases. .article-cta/.article-pull themselves are NOT page-
specific — they're sitewide shared classes (webapp/app.py's base _CSS),
already available on every page, so nothing needed to move for those two.

One genuinely NEW piece of content, not ported: where the original page's
"## Calculate Your Ratio" section held the live calculator inline, this
migration replaces it with a CTA box linking to the new standalone
calculator page. Per this PR's process note, that CTA copy is new and was
surfaced to Brian for review before merge (see the PR description) — it is
NOT ported copy, unlike everything else in BODY_MD below.

Also sets date_label to "June 2026" (derives sort_key from it) — same
visual-parity fix as Phase 4a/4b, restoring the byline the bespoke page
always showed (blank since the Phase 1 seed, since _TL_FEATURED_CARDS
tuples never carried a date_label). The retired page's own byline carried
two additional lines the shared template's single date_label field can't
represent — "Published with The F Suite" (with a live link) and a
Contributor credit line for Katherine Zhang — both preserved verbatim as
the first two lines of BODY_MD itself, same "extra byline content becomes
body_md's leading content" precedent Phase 4b used for the hackathon
piece's italic subtitle line.

The retired page's own "Methodology note" paragraph (GTM/R&D GAAP
definitions plus a timeline-lookback explanation) is intentionally left
OUT of this article's body_md — it's calculator-specific
(references "the timeline carries five quarters of lookback...") and
stayed, unmodified, on the new calculator page exactly where the original
placed it (directly below the ger-card), rather than being split across
two pages.

title/tag_label/link_label were already seeded correctly in Phase 1 (no
double-escape bug here, unlike the hackathon row's title — GER's title,
"The Growth Engine Ratio", has no HTML entities in it) — this script only
touches body_md/date_label/sort_key.

Deliberately a manual, run-by-hand script — NOT wired into an automatic
boot hook, same standing rule as every other production DATA write in this
repo. Safe by default (preview only, no writes) — same --apply convention
as scripts/archive/migrate_netsuite_mcp_content.py / migrate_hackathon_playbook_content.py.

Idempotent: guarded by checking whether the row's body_md already matches
what this script would set — a second run reports "already applied" and
does nothing.

Per the write-then-read-back standing practice, an --apply run re-reads the
row afterward and confirms body_md/date_label/sort_key match what was set.

Usage:
    python -m scripts.archive.migrate_growth_engine_ratio_content --db library.db            # preview
    python -m scripts.archive.migrate_growth_engine_ratio_content --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

SLUG = "growth-engine-ratio"
DATE_LABEL = "June 2026"

# Verbatim copy from the retired growth_engine_ratio() route (the article
# portion only — the calculator moved to its own new bespoke route, see the
# module docstring). Prose/headings are real markdown; the formula box,
# pull-quotes, tier table, F Suite CTA box, and Suno player are raw HTML
# blocks using their original classes, unchanged. The one new paragraph is
# the calculator CTA replacing "## Calculate Your Ratio"'s original inline
# card — see the module docstring's "genuinely NEW" note.
BODY_MD = '''<p style="color:var(--muted);font-size:14px;margin:0 0 8px;">Published with <a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a></p>

<p style="color:var(--muted);font-size:13px;margin:0 0 32px;">Contributor: Katherine Zhang, CEO of OPEXEngine by Bain &amp; Company, whose benchmark database makes the company-level numbers in this piece possible.</p>

<div class="article-cta">
  <p>
    The full guide—including benchmark data from 200+ public and private SaaS companies via OPEXEngine—
    is available as a downloadable whitepaper on The F Suite.
    <strong><a href="https://www.fsuite.co" target="_blank" rel="noopener">Read the full article and download the guide &rarr;</a></strong>
    <em style="display:block;margin-top:6px;font-size:13px;color:var(--muted);">(Link will be live when The F Suite publishes—coming soon.)</em>
  </p>
</div>

## Why I Built This

Most SaaS efficiency metrics measure one engine at a time. CAC payback tells you how quickly GTM investment pays back on new logos. Magic Number tells you how much ARR you're getting per dollar of sales and marketing spend. Both are useful—I use them all the time—but they share a blind spot: they leave R&D entirely out of the efficiency equation.

That bothers me. At most companies, R&D is 20–30% of revenue. It's a meaningful investment, and it directly influences how easy or hard it is for GTM to do its job. A great product shortens sales cycles, reduces churn, and drives expansion. A product that's hard to understand or hasn't kept pace with customer needs makes every dollar of GTM spend work harder just to stay in place.

<div class="article-pull ger-pull"><p>Spending like a 50%+ growth company while delivering 25% = efficiency disaster.</p></div>

When product and GTM are evaluated in separate silos, it's almost impossible to answer the question that actually matters: are these two engines working together efficiently? I came up with the Growth Engine Ratio to answer that question.

## The Core Idea

The framework is built on a simple observation: revenue recognized today is the result of investments made over the past several quarters, not just last quarter. Features ship before they're sold. Pipeline built in Q1 converts in Q3. A single period's P&L doesn't capture that.

So instead of comparing today's revenue growth to today's spending, the Growth Engine Ratio distributes investment across the quarters that actually contributed to a given period's growth. I call this the **time-distributed contribution model**.

The formula:

<div style="background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin:0 0 24px;font-family:ui-monospace,monospace;font-size:14px;line-height:1.8;">
  <strong>Growth Engine Ratio = Annualized Revenue Growth &divide; (GTM Investment + R&amp;D Investment)</strong><br><br>
  Annualized Growth = (Revenue Q<sub>n</sub> &minus; Revenue Q<sub>n-1</sub>) &times; 4<br>
  GTM Investment = 0.25 &times; (GTM<sub>n-4</sub> + GTM<sub>n-3</sub> + GTM<sub>n-2</sub> + GTM<sub>n-1</sub>)<br>
  R&amp;D Investment = 0.25 &times; (R&amp;D<sub>n-5</sub> + R&amp;D<sub>n-4</sub>)
</div>

GTM uses a 4-quarter lookback because enterprise sales cycles run 6–9 months—pipeline built in Q<sub>n-4</sub> converts across subsequent quarters until it lands in Q<sub>n</sub>. R&D uses a 2-quarter lookback starting one quarter earlier (n-5, n-4) because features are built before they're sold. The build-then-sell sequence matters. Each contributing quarter is weighted at 25%, so GTM enters at a full quarterly run-rate (four quarters &times; 25%) while the shorter R&D build window enters at half (two quarters &times; 25%).

## What the Number Tells You

A ratio of **$1.00** means you're generating exactly $1 of annualized revenue growth for every $1 of combined R&D + GTM investment. That's the threshold that separates companies that are profitable on acquisition from those that aren't.

In my analysis of 11 public SaaS companies across 188 company-quarters, only 2 exceeded $1.00 in steady state. The guide names them: Reddit at $2.94, Palantir at $2.04. It benchmarks both against 200+ private SaaS companies via OPEXEngine's database.

<div class="article-pull ger-pull"><p>Don't benchmark against these outliers unless you have similar network effects.</p></div>

The other 9 need to retain customers for 1.2 to 2.8 years just to break even on acquisition costs. That changes how you think about churn—permanently.

<div class="article-pull ger-pull"><p>Every churned customer represents permanent capital loss.</p></div>

<div class="ger-table-wrap" style="background:#fff;border:1px solid var(--line);border-radius:12px;margin:0 0 32px;">
  <table class="ger-table" style="width:100%;border-collapse:collapse;font-size:14px;min-width:520px;">
    <thead><tr style="background:var(--navy);">
      <th style="padding:10px 14px;text-align:left;font-weight:600;color:#fff;">Tier</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;color:#fff;">Ratio</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;color:#fff;">Years to Break Even</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;color:#fff;">What It Means</th>
    </tr></thead>
    <tbody>
      <tr style="border-top:1px solid var(--line);">
        <td style="padding:10px 14px;">&#127942; Elite</td>
        <td style="padding:10px 14px;">&gt; $1.20</td>
        <td style="padding:10px 14px;">&lt; 0.8 years</td>
        <td style="padding:10px 14px;">Profitable on acquisition—invest aggressively</td>
      </tr>
      <tr style="border-top:1px solid var(--line);background:#fdfcfa;">
        <td style="padding:10px 14px;">&#11088; Strong</td>
        <td style="padding:10px 14px;">$0.70&ndash;$1.20</td>
        <td style="padding:10px 14px;">0.8&ndash;1.4 years</td>
        <td style="padding:10px 14px;">Above median—maintain efficiency as you scale</td>
      </tr>
      <tr style="border-top:1px solid var(--line);">
        <td style="padding:10px 14px;">&#10003; Typical</td>
        <td style="padding:10px 14px;">$0.50&ndash;$0.70</td>
        <td style="padding:10px 14px;">1.4&ndash;2.0 years</td>
        <td style="padding:10px 14px;">In the pack—retention must be a top priority</td>
      </tr>
      <tr style="border-top:1px solid var(--line);background:#fdfcfa;">
        <td style="padding:10px 14px;">&#9888;&#65039; Below target</td>
        <td style="padding:10px 14px;">&lt; $0.50</td>
        <td style="padding:10px 14px;">&gt; 2.0 years</td>
        <td style="padding:10px 14px;">Urgent review—fix retention before scaling acquisition</td>
      </tr>
    </tbody>
  </table>
</div>

## Calculate Your Ratio

<div class="article-cta">
  <p>
    See where your own numbers land. The interactive calculator applies this exact formula to your GTM and R&amp;D spend: plot a single quarter or a full multi-quarter timeline, complete with benchmark tiers and a visual breakdown of how each quarter contributes.
    <strong><a href="/thought-leadership/growth-engine-calculator">Try the Growth Engine Ratio calculator &rarr;</a></strong>
  </p>
</div>

## A Note on Retention

One of the more useful outputs of this framework is a simple break-even calculation: **Years to Break Even = 1 ÷ Efficiency Ratio**. If your ratio is $0.60, you need to retain each customer for 1.7 years just to recover acquisition costs—and that assumes flat renewal with no expansion. Strong NRR (above 110%) compresses that timeline; contraction can make it indefinitely long.

Companies below $1.00, which is most of them, need both high gross retention and strong net expansion for the economics to work. One without the other isn't sufficient. The ratio makes that constraint explicit in a way that's hard to argue with in a board room.

## Get the Full Guide

The whitepaper includes the complete methodology, a worked example using Datadog's public financials, benchmark data across 200+ companies via OPEXEngine, and a performance tier guide with specific actions to take based on where your ratio lands. It's published in partnership with The F Suite.

<a href="https://www.fsuite.co" target="_blank" rel="noopener" class="btn" style="font-size:15px;padding:12px 24px;">
  Download the full guide &rarr;
</a>
<p style="font-size:13px;color:var(--muted);margin-top:8px;">(Full link coming soon—check back or <a href="/contact">reach out</a> and I'll send it directly.)</p>

## Bonus: The Growth Engine Ratio, the Song

<p style="color:var(--muted);font-size:14px;margin:0 0 14px;">I couldn't resist. AI-generated, obviously.</p>
<iframe src="https://suno.com/embed/608201fd-d2b9-4774-af56-b65d477f3528" width="100%" height="240" style="border:none;border-radius:12px;max-width:760px;display:block;"
  allow="autoplay; encrypted-media; fullscreen" allowfullscreen loading="lazy" referrerpolicy="no-referrer-when-downgrade"
  title="The Growth Engine Ratio (song)"></iframe>
<p style="font-size:13px;color:var(--muted);margin-top:8px;">Player not loading? <a href="/static/growth-engine-ratio.mp3" download>Download the MP3</a> or <a href="https://suno.com/song/608201fd-d2b9-4774-af56-b65d477f3528" target="_blank" rel="noopener">listen on Suno</a>.</p>
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the migration. Without this flag, only a preview "
                          "is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        row = lib.get_original_content_by_slug(SLUG)
        if row is None:
            print(f"No original_content row with slug={SLUG!r} — nothing to do. "
                  "(Expected this row to already exist from the Phase 1 migration.)")
            return 1

        if row["body_md"] == BODY_MD and row["date_label"] == DATE_LABEL:
            print("Already applied — body_md and date_label already match. Nothing to do.")
            return 0

        current_body_desc = "NULL" if row["body_md"] is None else f"{len(row['body_md'])} chars"
        print(f"Row id={row['id']}, title={row['title']!r}, current body_md is "
              f"{current_body_desc}, current date_label={row['date_label']!r}.")
        print(f"Would set body_md to {len(BODY_MD)} chars of converted content, "
              f"date_label to {DATE_LABEL!r}.")

        if not args.apply:
            print("\nPREVIEW ONLY — no DB writes. Re-run with --apply to write for real.")
            return 0

        sort_key = _sort_key_from_date_label(DATE_LABEL)
        lib.update_original_content(
            row["id"], row["slug"], row["title"], row["teaser"], row["tag_label"],
            row["link_label"], BODY_MD, row["status"], row["featured_home"],
            DATE_LABEL, sort_key, row["display_order"],
        )
        print("\nApplied.")

        # Write-then-read-back.
        after = lib.get_original_content(row["id"])
        assert after["body_md"] == BODY_MD, "body_md did not round-trip"
        assert after["date_label"] == DATE_LABEL, "date_label did not round-trip"
        assert after["sort_key"] == sort_key, "sort_key did not round-trip"
        assert after["status"] == "live", "status changed unexpectedly"
        print(f"Verified — read back {len(after['body_md'])} chars of body_md, "
              f"date_label={after['date_label']!r}, sort_key={after['sort_key']!r}, "
              f"status={after['status']!r}.")
        return 0
    finally:
        lib.close()


def _sort_key_from_date_label(date_label: str) -> str:
    """Local copy of webapp.app._sort_key_from_date_label — kept in sync by
    hand rather than importing webapp.app here, since importing the full web
    app module (FastAPI routes, startup hooks) into a small DB-only script
    is more machinery than this one pure function is worth. Same logic,
    same "Mon YYYY"/"Month YYYY" -> "YYYY-MM" behavior."""
    from datetime import datetime
    s = date_label.strip()
    if not s:
        return ""
    for fmt in ("%b %Y", "%B %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return f"{dt.year:04d}-{dt.month:02d}"
        except ValueError:
            continue
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
