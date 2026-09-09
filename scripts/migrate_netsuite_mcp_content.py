#!/usr/bin/env python3
"""One-time data migration (Original Content Phase 4a): sets body_md on the
existing `netsuite-mcp` original_content row, so it can be served through the
shared article template (GET /thought-leadership/{slug}) instead of the
bespoke Python route. See CLAUDE.md's Original Content Phase 4a entry and
docs/original_content_phase4_investigation (chat record) for the full
reasoning.

Copy is extracted VERBATIM from the retired webapp/app.py `netsuite_mcp()`
route — no rewriting, no paraphrasing. Structure only changed: prose/headings
became real markdown; the visually-designed elements (use-case cards, phase
tracks, both tables, the note box, the quick-reference box) are preserved as
raw HTML blocks using their existing page-specific CSS classes (`.ns-*`),
which this same PR moves into the shared `.oc-body`-scoped CSS
(`_OC_ARTICLE_CSS` in webapp/app.py) so they render correctly once served
through the shared template.

Also sets date_label to "June 2026" (derives sort_key from it) — the
original bespoke page's byline read "By Brian Weisberg · June 2026", but the
Phase 1 migration seeded this row with a blank date_label (the
_TL_FEATURED_CARDS tuple format never carried one), so today the article
template renders just "By Brian Weisberg" with no date. Restoring the date
is a visual-parity fix, not new content.

Deliberately a manual, run-by-hand script — NOT wired into an automatic boot
hook, same standing rule as every other production DATA write in this repo.
Safe by default (preview only, no writes) — same --apply convention as
scripts/archive/migrate_original_content.py.

Idempotent: guarded by checking whether the row's body_md already matches
what this script would set — a second run reports "already applied" and
does nothing.

Per the write-then-read-back standing practice, an --apply run re-reads the
row afterward and confirms body_md/date_label/sort_key match what was set.

Usage:
    python -m scripts.migrate_netsuite_mcp_content --db library.db            # preview
    python -m scripts.migrate_netsuite_mcp_content --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

SLUG = "netsuite-mcp"
DATE_LABEL = "June 2026"

# Verbatim copy from the retired netsuite_mcp() route. Prose/headings are
# real markdown; the designed elements (use-case cards, phase tracks, both
# tables, the note box, the quick-reference box) are raw HTML blocks using
# their original .ns-* classes, unchanged.
BODY_MD = '''<p style="font-size:17px;font-style:italic;color:var(--ink-soft);margin:0 0 6px;line-height:1.5;">An end-to-end guide to the two-role OAuth setup for finance teams</p>

This guide walks through connecting Claude to NetSuite so you can ask questions about your financial data and get answers directly—no logging into NetSuite, no writing queries, no manual exports.

Once connected, you can ask things like *"how much did we spend with this vendor last year?"* or *"what's the deferred revenue balance for this customer?"* and Claude will query NetSuite and return the answer in plain language, a table, or a formatted report. The connection runs through something called an MCP integration. You don't need to understand the underlying technology to use it—this guide covers everything you need.

<div class="article-callout ns-callout">
  <div class="article-callout-title">Before you start</div>
  <p>Check that the NetSuite AI Connector SuiteApp is installed: <strong>Customization → SuiteCloud → Installed SuiteApps</strong>, look for <code>com.netsuite.mcpstandardtools</code>. It should show <strong>Status: COMPLETE</strong>. If it's not installed, go to the SuiteApp Marketplace and search for it by name before continuing.</p>
</div>

## What you can do with this

Three examples to get your wheels turning. The right use cases depend on your business, but the pattern is consistent: ask a question in plain language, Claude queries NetSuite, you get something ready to share or act on.

<div class="ns-cases">
  <div class="ns-case">
    <div class="ns-case-label">Use case 01</div>
    <div class="ns-case-title">Revenue movements reconciliation</div>
    <p>If you work with deferred revenue—annual contracts, prepaid arrangements, usage-based billing—it's hard to get a clear picture of how money is moving at any point in time. Claude can pull a month-by-month view showing how revenue is loading into deferred, releasing into recognized, and what the ending balance looks like. Run it for the whole business or for a specific customer.</p>
    <p>A typical output: a waterfall table (deferred loaded, released, ending balance by month), a transaction-level trace from invoice through recognition, and a findings section flagging anything off—like a balance that should have cleared at contract termination but didn't.</p>
    <div class="ns-tip"><strong>Tip:</strong> Ask Claude to include a math check confirming every ending balance ties back to the underlying arithmetic. Easy to add, catches rounding errors before they make it into something you share.</div>
  </div>
  <div class="ns-case">
    <div class="ns-case-label">Use case 02</div>
    <div class="ns-case-title">Vendor spend analysis</div>
    <p>Vendor spend can be deceptively messy in NetSuite. The same vendor might appear under different names across bills. Some vendors route through a spend management platform (Ramp, Navan, Brex), which means they show up as a single vendor with the actual vendor buried in a memo field. Others route through a marketplace, invisible unless you know where to look.</p>
    <p>Claude can learn these patterns. Once you show it how your vendors are recorded, for example <em>"this vendor always comes through as the platform with the name in the memo,"</em> it applies that logic consistently. The result is a spend picture that reflects reality, not just whatever's in the vendor field.</p>
    <div class="ns-tip"><strong>Tip:</strong> The first time you run a vendor spend query, ask Claude to show you a sample of raw transaction data before it aggregates anything. Easy way to spot non-obvious mappings before they roll up into a wrong total.</div>
  </div>
  <div class="ns-case">
    <div class="ns-case-label">Use case 03</div>
    <div class="ns-case-title">Per-employee benefit and stipend tracking</div>
    <p>If your company offers benefits employees draw on over time—L&amp;D stipends, wellness budgets, home office allowances—and those transactions flow through NetSuite in any form, Claude can extract and organize them by person. A useful output: each employee's YTD usage broken down by category, with transaction-level detail on demand. Useful for answering "who has used their full allocation?" without compiling spreadsheets manually.</p>
    <div class="ns-tip"><strong>Tip:</strong> Employee names in NetSuite memos are often inconsistent—nicknames, initials, misspellings. Ask Claude to show you the distinct name variations it finds before attributing spend, so you can confirm the mapping is right. It gets even easier if you have a public or internal page that lists your team—Claude picks up on it and learns how you want employee names represented, or how teams are structured.</div>
  </div>
</div>

## The security architecture

The setup involves creating a dedicated read-only role in NetSuite for Claude to authenticate as. The reason matters.

Claude's NetSuite integration includes tools that can create and update records, not just read them. If Claude is authenticated with a role that has write permissions, it could theoretically create transactions, edit customer records, or modify other data in your ledger. To prevent that, we create a read-only role and configure Claude to use it. No write permissions on the role means NetSuite blocks any write attempt at the permission level—regardless of what Claude tries to do. The protection is enforced by NetSuite, not by hoping Claude behaves.

<div class="article-warn ns-warn">
  <div class="article-warn-title ns-warn-title">One thing that trips people up</div>
  <p>When you connect Claude, you need to be logged into NetSuite under your <strong>normal working role</strong>—not the new read-only role you're about to create. You'll select the read-only role on a screen that appears during the connection flow. More on this in Part 2.</p>
</div>

## Part 1—NetSuite setup

<p style="color:var(--muted);font-size:14px;margin:-8px 0 20px;">You need Administrator access for these steps, or ask your NetSuite admin to complete them.</p>

<div class="ns-track">
  <div class="ns-step">
    <div class="ns-num">1</div>
    <div class="ns-body">
      <h3>Confirm the SuiteApp is installed</h3>
      <p>Go to <strong>Customization → SuiteCloud → Installed SuiteApps</strong>. Look for <strong>NetSuite AI Connector Service</strong> (bundle ID: <code>com.netsuite.mcpstandardtools</code>). Confirm it shows <strong>Status: COMPLETE</strong>. If it's not there, install it from the SuiteApp Marketplace before continuing.</p>
    </div>
  </div>
  <div class="ns-step">
    <div class="ns-num">2</div>
    <div class="ns-body">
      <h3>Create the read-only "Netsuite MCP" role</h3>
      <p>Go to <strong>Setup → Users/Roles → Manage Roles → New</strong>. Name it <strong>Netsuite MCP</strong> (this name appears on the authorization screen when you connect Claude). Check <strong>Web Services Only Role</strong>—this prevents anyone from using this role to log into NetSuite directly and ensures it appears correctly during the connection flow.</p>
      <p>On the <strong>Permissions tab → Setup subtab</strong>, add these six permissions at Full level:</p>
      <div class="ns-table-wrap">
        <table class="ns-table">
          <thead><tr><th>Permission</th><th>Level</th></tr></thead>
          <tbody>
            <tr><td>MCP Server Connection</td><td>Full</td></tr>
            <tr><td>REST Web Services</td><td>Full</td></tr>
            <tr><td>Log in using OAuth 2.0 Access Tokens</td><td>Full</td></tr>
            <tr><td>Log in using Access Tokens</td><td>Full</td></tr>
            <tr><td>User Access Tokens</td><td>Full</td></tr>
            <tr><td>SuiteScript</td><td>Full</td></tr>
          </tbody>
        </table>
      </div>
      <p>Save the role. Do not add any permissions related to creating, editing, approving, or posting transactions. This role should stay read-only.</p>
      <div class="article-warn ns-warn" style="margin-top:12px;">
        <div class="article-warn-title ns-warn-title">Known issue</div>
        <p>The Web Services Only Role checkbox is easy to miss but critical. Without it, the role may not show up correctly during the connection flow, and you may see a "does not support OAuth 2.0 login" error.</p>
      </div>
    </div>
  </div>
  <div class="ns-step">
    <div class="ns-num">3</div>
    <div class="ns-body">
      <h3>Assign the role to your user account</h3>
      <p>Go to <strong>Lists → Employees → Employees</strong>. Find and open your employee record. Click the <strong>Access tab</strong>, find the Roles section, and add <strong>Netsuite MCP</strong>. Save. You'll now see Netsuite MCP in the role selector in the top-right corner of NetSuite—though you won't need to switch into it during normal use.</p>
    </div>
  </div>
  <div class="ns-step">
    <div class="ns-num">4</div>
    <div class="ns-body">
      <h3>A note on the integration record (no action needed)</h3>
      <p>If you look at the integration record Claude uses (<strong>Setup → Integration → Manage Integrations</strong>, look for "NetSuite AI Connector Service"), you'll notice the REST Web Services checkbox is greyed out and can't be checked. That's normal—Anthropic created this integration and its settings are locked. Don't try to edit it. The role you created in Step 2 is what gives Claude the access it needs.</p>
    </div>
  </div>
</div>

## Part 2—Connecting Claude

<p style="color:var(--muted);font-size:14px;margin:-8px 0 20px;">NetSuite is set up. This part takes about two minutes per person.</p>

<div class="ns-track">
  <div class="ns-step">
    <div class="ns-num">5</div>
    <div class="ns-body">
      <h3>Make sure you're in the right NetSuite role first</h3>
      <p>Before going to Claude, check which role you're currently in on the NetSuite side. You need to be logged in under your <strong>normal working role</strong>—not the Netsuite MCP role you just created. Netsuite MCP is what you'll select during the connection flow, not what you're already in. Check the role indicator in the top-right corner of NetSuite. If it says Netsuite MCP, switch to your normal role first.</p>
      <div class="article-warn ns-warn" style="margin-top:8px;">
        <div class="article-warn-title ns-warn-title">Most common mistake when reconnecting</div>
        <p>Going straight to Claude without checking your NetSuite role first. Always confirm you're in your normal working role in NetSuite before clicking Connect in Claude. This step catches more than half of all connection failures.</p>
      </div>
    </div>
  </div>
  <div class="ns-step">
    <div class="ns-num">6</div>
    <div class="ns-body">
      <h3>Connect NetSuite in Claude</h3>
      <p>In Claude, go to <strong>Settings → Connectors</strong>. Find NetSuite and click <strong>Connect</strong>. A NetSuite page will open asking you to authorize the connection. On the role selector, choose <strong>Netsuite MCP</strong>. Click <strong>Authorize</strong>. You'll be brought back to Claude automatically.</p>
    </div>
  </div>
  <div class="ns-step">
    <div class="ns-num">7</div>
    <div class="ns-body">
      <h3>Test that it works</h3>
      <p>The NetSuite connector in Claude should now show as connected. Confirm it's actually working with a simple test: <em>"Run a quick NetSuite query to confirm the connection is working—just pull the first 3 rows from the transaction table."</em> If Claude returns a few rows, you're set. If it says the tools are unavailable, see Troubleshooting below.</p>
    </div>
  </div>
</div>

<div class="ns-note">
  <p><strong>Note:</strong> The connection is per person, not shared. Each person who wants to use Claude with NetSuite needs to go through setup themselves and connect their own Claude account. If a colleague's connection is working, that tells you nothing about whether yours is.</p>
</div>

## Tips for getting good results

### Ask for everything, not just bills

When you ask Claude to look something up, it may default to querying only vendor bills or invoices. This can miss a lot. Journal entries are a separate transaction type—and many common workflows post through JEs: month-end accruals, prepayment amortizations, corporate card programs. A query limited to vendor bills misses them entirely.

<div class="article-callout ns-callout">
  <div class="article-callout-title">Sanity check for any spend query</div>
  <p>Ask Claude to first pull a grand total with no filters other than the account and date range, then compare against the detailed results. If they don't match, something is being filtered out. The discrepancy tells you what to investigate next.</p>
</div>

### Filter at the line level, not the header

Revenue recognition journal entries are often posted as a single large entry covering many customers at once. The customer is recorded at the line level inside the entry, not on the entry itself. If Claude filters at the wrong level, it can return results for a completely different customer—or nothing at all.

If results for a customer look wrong—too high, too low, or zero when you know there should be activity—ask Claude: *"Are you filtering on the transaction line entity, not the transaction header entity?"* That question catches the most common mistake.

### If you get zero results, pull an unfiltered sample first

Zero results almost always mean a filter is wrong, not that the data is missing. Ask Claude to run a quick sample: *"Can you pull 5–10 raw rows with no filters so we can see what's actually there?"* This almost always reveals the issue—a filter too narrow, a date range that doesn't match, or a field with data in a slightly different format than expected.

### Claude can run saved searches, not create them

Claude can run existing saved searches and list available ones. It can't create new ones. If you need a new saved search built, ask Claude what criteria and columns to use, then create it yourself: **Reports → Saved Searches → New → Transaction**.

## Troubleshooting

<div class="ns-table-wrap">
  <table class="ns-trouble">
    <thead><tr><th>Error / symptom</th><th>Cause</th><th>Fix</th></tr></thead>
    <tbody>
      <tr>
        <td>"This connector has no tools available"</td>
        <td>Usually a stale session, not a permissions problem</td>
        <td>Open a new Claude conversation first. If that fails, go to Settings → Connectors, disconnect NetSuite, and reconnect—making sure to select Netsuite MCP on the authorization screen.</td>
      </tr>
      <tr>
        <td>"Your role does not support OAuth 2.0 login"</td>
        <td>You're logged into NetSuite under a role that can't initiate the connection flow—often happens when you're already in the Netsuite MCP role</td>
        <td>Switch to your normal working role in NetSuite first, then go back to Claude and connect. Select Netsuite MCP on the authorization screen.</td>
      </tr>
      <tr>
        <td>Netsuite MCP doesn't appear as an option on the authorization screen</td>
        <td>Role hasn't been assigned to your user yet, or Web Services Only Role isn't checked</td>
        <td>Ask your NetSuite admin to assign Netsuite MCP to your employee record and confirm Web Services Only Role is checked on the role definition.</td>
      </tr>
      <tr>
        <td>Connection keeps dropping</td>
        <td>Normal—the connection doesn't stay active indefinitely</td>
        <td>Open a new Claude conversation. Fixes it most of the time. If not, go to Settings → Connectors, disconnect, and reconnect.</td>
      </tr>
      <tr>
        <td>Can't find a Claude token in NetSuite's Access Tokens list</td>
        <td>Expected—the connection uses a different token type that doesn't appear there</td>
        <td>Nothing to do. Its absence from that list doesn't mean anything is wrong.</td>
      </tr>
      <tr>
        <td>Totals look wrong or suspiciously large</td>
        <td>Often filtering at the transaction header instead of the line entity on large journal entries</td>
        <td>Ask Claude: "Are you filtering on the transaction line entity, not the header?" Then ask it to pull an unfiltered sample to verify.</td>
      </tr>
    </tbody>
  </table>
</div>

## When the connection drops

The connection between Claude and NetSuite drops periodically. This is normal and doesn't mean anything is misconfigured. Try this first: **open a new Claude conversation.** The connection re-establishes on a new session most of the time.

If a new conversation doesn't fix it: go to **Customize** (bottom-left of the chat window) or **Settings → Connectors**. Find NetSuite, click Disconnect, then Connect. On the NetSuite authorization screen, confirm you're in your normal working role, then select Netsuite MCP and authorize. Test with a quick query.

<div class="ns-qr">
  <div class="ns-qr-title">📋 Quick reference</div>
  <h3>First-time setup (done once, by your NetSuite admin)</h3>
  <ul>
    <li>Install the NetSuite AI Connector SuiteApp (<code>com.netsuite.mcpstandardtools</code>)</li>
    <li>Create the Netsuite MCP role: Web Services Only Role checked, 6 permissions at Full, no write access</li>
    <li>Assign the role to each user who will connect Claude</li>
  </ul>
  <h3>Connecting Claude (done once per person)</h3>
  <ul>
    <li>In NetSuite, confirm you're logged in under your normal working role</li>
    <li>In Claude → Settings → Connectors, click Connect next to NetSuite</li>
    <li>On the authorization screen, select Netsuite MCP</li>
    <li>Test with a quick query to confirm it's working</li>
  </ul>
  <h3>If the connection drops</h3>
  <ul>
    <li>Open a new Claude conversation and try again—fixes it most of the time</li>
    <li>If that doesn't work: confirm your NetSuite role, then go to Customize or Settings → Connectors in Claude and reconnect</li>
  </ul>
  <h3>If results look wrong</h3>
  <ul>
    <li>Ask Claude if it's including journal entries, not just bills</li>
    <li>Ask Claude if it's filtering on the transaction line entity (not the transaction header)</li>
    <li>Ask Claude to pull a small unfiltered sample to see what's actually in the data</li>
  </ul>
</div>

<div style="border-top:1px solid var(--line-strong);margin-top:48px;padding-top:24px;">
  <p style="font-size:13px;color:var(--muted);margin:0;">Brian Weisberg is a tech CFO writing about finance leadership, AI adoption, and building finance teams that compound. <a href="/thought-leadership">More writing &rarr;</a></p>
</div>
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
        print(f"Row id={row['id']}, current body_md is {current_body_desc}, "
              f"current date_label={row['date_label']!r}.")
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
