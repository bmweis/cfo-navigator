#!/usr/bin/env python3
"""Seed the CFO Toolbox Communities directory with a curated list of finance
and CFO peer communities, associations, and Slack groups.

Safe to run multiple times — adds any community missing by URL and syncs
name/notes on existing rows (same contract as tools/benchmarks' name+
description re-sync), plus advisor (mirroring tools.advisor: bumped True
here if the source says so, never demoted). Every other field —
reach, local_markets, featured, cost_band, cost_note, sponsorship_type,
sponsor_name, access, format, categories, approved — is admin-owned once
seeded, edited at /admin/tools/communities, and never touched by a re-run.

Usage:
    python -m scripts.seed_communities [--db library.db]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import DuplicateURLError, Library, normalize_url

CATEGORIES = [
    ("CFO-specific invite-only", "Elite, invite-only networks for CFOs and senior finance leaders."),
    ("Broad paid association", "Dues-based professional associations open to a wide range of finance titles."),
    ("Free vendor-sponsored", "Free communities backed by a vendor, publisher, or open to anyone."),
    ("Women/DEI", "Communities centered on women and underrepresented groups in finance."),
    ("Fractional CFO", "Communities for fractional and part-time CFOs building their own practice."),
    ("Controller/accounting", "Communities focused on controllers and accounting-team leadership."),
    ("Treasury", "Communities focused on corporate treasury and cash management."),
    ("Industry-specific", "Communities scoped to one industry vertical (healthcare, nonprofit, life sciences, tech)."),
]

# cost_band is one of: "Free", "Undisclosed dues", "<$1k/yr", "<$2,500/yr", "$2,500+/yr"
# — bucketed by the individual/base rate; multi-seat or corporate pricing goes in cost_note.
COMMUNITIES = [
    # Tier 1 — CFO-specific, invite-only, high-growth/enterprise
    {
        "name": "The F Suite",
        "url": "https://www.fsuite.co",
        "demographic": "CFOs & VP Finance at high-growth tech companies and VC funds",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "sponsor_name": "The Suite, Inc.",
        "access": "Invite-only (peer nomination)",
        "format": "Hybrid (Braintrust platform + app + in-person conferences)",
        "notes": "750+ CFOs. Merged with the Seattle Tech CFO Group in May 2024; that group's founder, Evan Fein, became Chairman. Excludes fractional CFOs and vendors from the core network. Brian is a Founding Member of The F Suite as well as supported them as an advisor over the years.",
        "categories": ["CFO-specific invite-only"],
        "advisor": True,
    },
    {
        "name": "Operators Guild",
        "url": "https://operators-guild.com",
        "demographic": "Finance & operations leaders (CFO/COO/VP Finance) at high-growth companies",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "access": "Invite-only (~10% acceptance, ~95% referral rate)",
        "format": "Hybrid (OG Live forum + in-person events)",
        "notes": "1,000+ members. Cross-functional finance + operations, not finance-exclusive.",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "NeuGroup",
        "url": "https://www.neugroup.com",
        "demographic": "Senior corporate treasury & finance professionals at large/mega-cap firms",
        "cost_band": "Undisclosed dues",
        "cost_note": "Annual per-seat fees, not published.",
        "sponsorship_type": "Independent",
        "access": "Invite-only",
        "format": "Hybrid (18-20+ peer groups, ~2 in-person meetings/yr + monthly virtual)",
        "notes": "500+ members (1,100+ across the network). Founded 1994; groups capped ~25-30. Also builds PE-portfolio finance communities.",
        "categories": ["CFO-specific invite-only", "Treasury"],
    },
    {
        "name": "The Circle (Founders Circle Capital)",
        "url": "https://thecircle.founderscircle.com",
        "demographic": "CEOs & CFOs at growth-stage companies scaling to IPO",
        "cost_band": "Free",
        "sponsorship_type": "Investor-sponsored",
        "sponsor_name": "Founders Circle Capital (CFO community also sponsored by Tropic)",
        "access": "Invite-only (portfolio + select non-portfolio)",
        "format": "Hybrid",
        "notes": "250-600+ leaders across cohorts.",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "CFO Executive Forum (Open Future Forum)",
        "url": "https://openfutureforum.com",
        "demographic": "CFOs & senior finance executives",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "access": "Application/invite",
        "format": "Hybrid",
        "notes": "Private, invite-only. Confirmed active.",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "Evanta CFO Community",
        "url": "https://www.evanta.com/cfo",
        "demographic": "CFOs at large enterprises",
        "cost_band": "Free",
        "cost_note": "Free to qualified members; Gartner-funded.",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Gartner",
        "access": "Application (vetted by Gartner)",
        "format": "In-person (Executive Summits, Inner Circles, Town Halls) + virtual",
        "notes": "Regional chapters across 30+ North American cities. A local Governing Body of CFOs sets each chapter's agenda (\"By CFOs, For CFOs\").",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "Private Funds CFO Network",
        "url": "https://www.peievents.com",
        "demographic": "CFOs, COOs, and CCOs at PE/private-credit firms",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "sponsor_name": "PEI Media (part of M&G)",
        "access": "Application",
        "format": "Hybrid (think tanks, summits, webinars)",
        "notes": "Private-fund finance/operations focus. Offers CPE credits and a \"New Faces of Finance\" list.",
        "categories": ["CFO-specific invite-only"],
    },
    # Tier 2 — CFO/finance leadership, broader paid access
    {
        "name": "CFO Leadership Council",
        "url": "https://www.cfoleadershipcouncil.com",
        "demographic": "CFOs, SVP/VP Finance, Directors of FP&A, CAO, Controllers",
        "cost_band": "<$1k/yr",
        "cost_note": "$475/yr.",
        "sponsorship_type": "Independent",
        "sponsor_name": "Chief Executive Group",
        "access": "Application (vetted)",
        "format": "Hybrid (chapter meetings, conferences, masterminds, online forum)",
        "notes": "33 North American chapters. 2,500+ finance leaders. Vertical groups include VC/PE portfolio, life sciences, tech, nonprofit, women, and contract CFOs.",
        "categories": ["Broad paid association"],
    },
    {
        "name": "CFO Alliance",
        "url": "https://www.cfoalliance.com",
        "demographic": "CFOs & senior finance executives at middle-market/emerging enterprises",
        "cost_band": "Undisclosed dues",
        "cost_note": "Subscription-based, rate not published.",
        "sponsorship_type": "Independent",
        "access": "Application/subscription",
        "format": "Hybrid (quarterly regional roundtables, groups)",
        "notes": "9,000+ members.",
        "categories": ["Broad paid association"],
    },
    {
        "name": "Controllers Council",
        "url": "https://www.controllerscouncil.org",
        "demographic": "Controllers, CFOs, and corporate accounting/finance professionals",
        "cost_band": "<$1k/yr",
        "cost_note": "$300/yr individual; $750/yr corporate plan covers 3 seats.",
        "sponsorship_type": "Independent",
        "sponsor_name": "Modern Associations, Inc.",
        "access": "Open (paid)",
        "format": "Online (forum, directory, webinars, CPE)",
        "notes": "100,000+ subscribers/members. Runs the Controller of the Year awards.",
        "categories": ["Broad paid association", "Controller/accounting"],
    },
    {
        "name": "Financial Executives International (FEI)",
        "url": "https://www.financialexecutives.org",
        "demographic": "Senior financial executives (CFO, CAO, Controller, Treasurer, VP Finance)",
        "cost_band": "<$1k/yr",
        "cost_note": "$309/yr Professional Membership. Eligibility requires $2M+ net worth, $6M+ capital, or $10M+ annual revenue.",
        "sponsorship_type": "Independent",
        "access": "Application (gated by title + company size)",
        "format": "Hybrid (chapters, conferences, FEIconnect online, CPE)",
        "notes": "~10,000 members. Local chapters exist in 55+ US cities (e.g. New York, Chicago) plus internationally; some chapters run their own site (e.g. chicagocfo.org for Chicago, chartered 1933) alongside the national FEIconnect platform.",
        "categories": ["Broad paid association"],
    },
    {
        "name": "Association for Financial Professionals (AFP)",
        "url": "https://www.financialprofessionals.org",
        "demographic": "Treasury & corporate finance professionals",
        "cost_band": "<$1k/yr",
        "cost_note": "$545/yr, raised from $495 on January 1, 2026.",
        "sponsorship_type": "Independent",
        "access": "Open",
        "format": "Hybrid (annual conference, FP&A Forum, CTP/FPAC certifications, online community)",
        "notes": "16,000+ members. Best known for the CTP and FPAC credentials.",
        "categories": ["Broad paid association", "Treasury"],
    },
    {
        "name": "Modern Finance Forum for CFOs",
        "url": "https://www.fsn.co.uk",
        "demographic": "Senior finance professionals/CFOs",
        "cost_band": "Free",
        "sponsorship_type": "Independent",
        "sponsor_name": "FSN (publisher/research firm)",
        "access": "Open (LinkedIn group)",
        "format": "LinkedIn group + FSN research + European events",
        "notes": "~54,000 members, ~80% in senior finance roles. Founded 2012, run by FSN (not Bramasol).",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "CFO Circle (Blueprint for Growth)",
        "url": "https://www.cfo-circle.com",
        "demographic": "CFOs & senior finance leaders at companies with $5M+ annual revenue",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "sponsor_name": "Blueprint for Growth, Inc.",
        "access": "Application/invite",
        "format": "In-person facilitated peer groups",
        "notes": "Private, invite-only. Distinct from HighRadius CFO Circle.",
        "categories": ["CFO-specific invite-only"],
    },
    # Tier 3 — Free, vendor-sponsored or open practitioner communities
    {
        "name": "Off The Ledger",
        "url": "https://www.airbase.com/off-the-ledger",
        "demographic": "Finance, accounting & procurement professionals",
        "cost_band": "Free",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Airbase (acquired by Paylocity)",
        "access": "Application (vetted, \"sales-free zone\")",
        "format": "Slack",
        "notes": "5,000+ members. Launched late 2019.",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "Close Club",
        "url": "https://www.rillet.com/community",
        "demographic": "CFOs, Controllers, and finance practitioners",
        "cost_band": "Free",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Rillet",
        "access": "Application (vetted)",
        "format": "Slack + in-person dinners + events",
        "notes": "700+ members (April 2025). AI/close-process focus.",
        "categories": ["Free vendor-sponsored", "Controller/accounting"],
    },
    {
        "name": "CFO Connect",
        "url": "https://cfoconnect.eu",
        "demographic": "CFOs & senior finance leaders",
        "cost_band": "Free",
        "cost_note": "Pro tier is €625/yr (free to Spendesk customers).",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Spendesk",
        "access": "Application (vetted)",
        "format": "Hybrid (invite-only Slack, webinars, workshops, in-person dinners)",
        "notes": "12,000+ vetted members: ~70% CFOs, ~15% VPs/Directors of Finance, ~10% Heads of Finance. Founded 2017.",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "FP&A Community (Datarails)",
        "url": "https://www.datarails.com/community",
        "demographic": "FP&A professionals & finance leaders",
        "cost_band": "Free",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Datarails",
        "access": "Application (3+ years experience)",
        "format": "Slack + resource hub",
        "notes": "Member count not published.",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "Finance Alliance",
        "url": "https://www.financealliance.io",
        "demographic": "CFOs, VPs Finance, and Finance Directors/Managers",
        "cost_band": "Free",
        "sponsorship_type": "Independent",
        "access": "Open",
        "format": "Slack + content + regional CFO Summits",
        "notes": "Launched 2022. Also runs invite-only in-person CFO Summits (NY, Boston, Chicago) for C-suite/VP finance at 200+ employee or $50M+ revenue orgs.",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "CFO Chat",
        "url": "https://cfo.chat",
        "demographic": "CFOs, full-time and fractional",
        "cost_band": "Free",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Shiny (fractional-executive marketplace)",
        "access": "Application (vetted)",
        "format": "Slack",
        "notes": "Explicitly welcomes both part-time and full-time CFOs.",
        "categories": ["Free vendor-sponsored", "Fractional CFO"],
    },
    {
        "name": "Proformative",
        "url": "https://www.proformative.com",
        "demographic": "Corporate finance & accounting professionals",
        "cost_band": "Free",
        "sponsorship_type": "Vendor-sponsored",
        "sponsor_name": "Informa TechTarget",
        "access": "Open",
        "format": "Online Q&A forum + webinars",
        "notes": "No longer an independent community — acquired by Argyle Executive Forum in 2016, now operated by Informa TechTarget; content now channels to CFO Dive. Historically 600,000+ registered members.",
        "categories": ["Free vendor-sponsored"],
    },
    # Tier 4 — Women/DEI-focused finance communities
    {
        "name": "FIF Collective (Females in Finance)",
        "url": "https://www.fifcollective.com",
        "demographic": "Women & allies: CFOs, VP Finance, VC partners, founders",
        "cost_band": "<$1k/yr",
        "cost_note": "$1,000/yr for members; $2,000/yr for allies (since Jan 2025).",
        "sponsorship_type": "Independent",
        "sponsor_name": "Sponsor-funded (Deloitte, Amazon, Davis Polk among sponsors)",
        "access": "Invite-only (curated)",
        "format": "Hybrid (events, members-only WhatsApp group, job placement)",
        "notes": "1,300+ members. Founded 2023 by Meghan McKenna; honored at NYSE at the 1,000-member milestone.",
        "categories": ["Women/DEI"],
    },
    {
        "name": "Financial Women's Association (FWA)",
        "url": "https://www.fwa.org",
        "demographic": "Women (& allies) in financial services & finance roles",
        "cost_band": "<$1k/yr",
        "cost_note": "Full membership <$1/day; Associate <50 cents/day; Student <15 cents/day.",
        "sponsorship_type": "Independent",
        "access": "Application",
        "format": "Hybrid (events, mentorship, scholarships)",
        "notes": "800-1,000 members. Founded 1956; open to all genders.",
        "categories": ["Women/DEI"],
    },
    # Regional/local communities
    {
        "name": "CFOMeet",
        "url": "https://www.cfomeet.org",
        "demographic": "CFOs, Chief Accounting Officers, Chief Procurement Officers, Chief Audit Executives, Treasurers, Controllers, VPs of Finance, and senior finance leaders from mid-market and enterprise organizations",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "sponsor_name": "CXO Inc.",
        "access": "Invite-only",
        "format": "In-person (panel lunches, roundtables, CFO dinners)",
        "notes": "~50 local CFOs per event. Local chapters exist in Boston, Chicago, and Toronto (each with its own dedicated page, e.g. cfomeet.org/newyork).",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "Boston Corporate Finance Community (BCFC)",
        "url": "https://www.bostoncorporatefinancecommunity.com",
        "demographic": "Corporate finance, banking, PE, and specialty-finance professionals & advisors",
        "cost_band": "Undisclosed dues",
        "cost_note": "Event-based, cost not published.",
        "sponsorship_type": "Independent",
        "access": "Open/networking",
        "format": "In-person (annual \"Gathers to Give Back\" charity event)",
        "notes": "Running since 2017; raises funds for the More Than Words nonprofit.",
        "categories": ["Free vendor-sponsored"],
    },
    {
        "name": "Private Equity CFO Association (PECFOA)",
        "url": "https://www.privateequitycfo.org",
        "demographic": "PE fund CFOs",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "access": "Application",
        "format": "In-person (chapter meetings)",
        "notes": "Boston is the founding chapter (2001); each other chapter meets locally on its own schedule.",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "CFO Mastermind Group",
        "url": "https://www.cfomastermindgroup.com",
        "demographic": "CFOs & finance executives",
        "cost_band": "$2,500+/yr",
        "cost_note": "$3,000/yr, or $1,750 semi-annual; $500 discount after 5 consecutive years.",
        "sponsorship_type": "Independent",
        "access": "Application",
        "format": "In-person peer group",
        "categories": ["CFO-specific invite-only"],
    },
    {
        "name": "Senior Executive Network (CFO/VP Finance)",
        "url": "https://www.seniorexecutivenetwork.com",
        "demographic": "CFOs & VPs of Finance",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "access": "Application",
        "format": "In-person facilitated peer groups",
        "notes": "Places non-competing industry peers into professionally facilitated groups.",
        "categories": ["CFO-specific invite-only"],
    },
    # Industry-vertical communities
    {
        "name": "Healthcare Financial Management Association (HFMA)",
        "url": "https://www.hfma.org",
        "demographic": "Healthcare finance professionals & hospital/health-system CFOs",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "access": "Open",
        "format": "Hybrid (chapters, certifications, online forums, events)",
        "notes": "56,000+ followers. Offers 11 certifications.",
        "categories": ["Industry-specific"],
    },
    {
        "name": "Startup CFO",
        "url": "https://www.startupcfo.tech",
        "demographic": "CFOs & FDs at high-growth tech businesses",
        "cost_band": "Undisclosed dues",
        "cost_note": "Private membership, rate not published.",
        "sponsorship_type": "Independent",
        "access": "Application",
        "format": "Hybrid (Slack + events)",
        "notes": "900-1,000+ members. Describes itself as the largest global tech CFO network; embedded AI Q&A on its Slack.",
        "categories": ["Industry-specific"],
    },
    {
        "name": "Finance & Accounting for Bioscience (Informa Connect)",
        "url": "https://informaconnect.com/finance-bioscience-east",
        "demographic": "Life-sciences finance & accounting leaders",
        "cost_band": "Undisclosed dues",
        "cost_note": "Paid conference registration.",
        "sponsorship_type": "Independent",
        "sponsor_name": "Informa Connect",
        "access": "Open (registration)",
        "format": "In-person annual conference (CPE-accredited)",
        "notes": "Annual event, part of Biotech Week Boston — not a standing community.",
        "categories": ["Industry-specific"],
    },
    # Fractional CFO communities
    {
        "name": "The CFO Accelerator Inner Circle",
        "url": "https://www.thecfoaccelerator.com/innercircle",
        "demographic": "Fractional CFOs building/scaling their own firms",
        "cost_band": "Undisclosed dues",
        "sponsorship_type": "Independent",
        "sponsor_name": "Michael King",
        "access": "Open (paid)",
        "format": "Hybrid (Slack + monthly group coaching)",
        "notes": "250-350+ fractional CFOs. Focus on firm-building: pricing, hiring, systems.",
        "categories": ["Fractional CFO"],
    },
    {
        "name": "Fractionals United",
        "url": "https://www.fractionalsunited.com",
        "demographic": "Fractional executives, including fractional CFOs",
        "cost_band": "<$1k/yr",
        "cost_note": "$10/mo or $100/yr, 15-day free trial.",
        "sponsorship_type": "Independent",
        "sponsor_name": "Karina Mikhli",
        "access": "Application",
        "format": "Slack + events",
        "notes": "~8,000 members. Cross-functional fractional community; finance is one segment, not exclusive.",
        "categories": ["Fractional CFO"],
    },
    # Treasury communities
    {
        "name": "Association of Corporate Treasurers (ACT)",
        "url": "https://www.treasurers.org",
        "demographic": "Corporate treasury professionals",
        "cost_band": "<$1k/yr",
        "cost_note": "AMCT membership is £424/yr; other tiers vary.",
        "sponsorship_type": "Independent",
        "access": "Qualification-based + open affiliate tier",
        "format": "Hybrid (credentials, treasury networks, mentoring, conferences)",
        "notes": "5,200+ members. The UK's chartered professional body for treasury, established 1979.",
        "categories": ["Treasury"],
    },
    {
        "name": "The Conference Board Corporate Treasurers Council",
        "url": "https://www.conference-board.org/councils/corporate-treasurers",
        "demographic": "Senior corporate treasurers",
        "cost_band": "Undisclosed dues",
        "cost_note": "Requires Conference Board membership.",
        "sponsorship_type": "Independent",
        "access": "Invite-only",
        "format": "In-person (Chatham House Rule peer council)",
        "notes": "Small, confidential senior-treasurer peer group.",
        "categories": ["Treasury"],
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed the CFO Toolbox Communities directory.")
    parser.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    args = parser.parse_args()

    lib = Library(args.db)
    added = updated = skipped = 0
    try:
        for cat_name, cat_desc in CATEGORIES:
            existing = lib.conn.execute(
                "SELECT 1 FROM community_categories WHERE name = ? COLLATE NOCASE", (cat_name,)
            ).fetchone()
            if not existing:
                lib.add_community_category(cat_name, cat_desc)
                print(f"  ADDED category: {cat_name}")

        # Normalized comparison (not exact string) so a trailing-slash/www/http
        # variant of an already-seeded URL is recognized as the same community
        # rather than tripping Library.add_community's own duplicate check below.
        existing_by_url = {normalize_url(r["url"]): r for r in lib.conn.execute("SELECT * FROM communities").fetchall()}
        for c in COMMUNITIES:
            existing = existing_by_url.get(normalize_url(c["url"]))
            fields = dict(
                name=c["name"], url=c["url"],
                demographic=c["demographic"], cost_band=c["cost_band"],
                categories=c["categories"], cost_note=c.get("cost_note", ""),
                sponsorship_type=c.get("sponsorship_type", "Independent"),
                sponsor_name=c.get("sponsor_name", ""), access=c.get("access", ""),
                format=c.get("format", ""), notes=c.get("notes", ""),
            )
            if existing:
                # name/notes/advisor re-sync — cost_band/access/categories/
                # featured/etc. are admin-owned once seeded (edited at
                # /admin/tools/communities), same contract as tools/benchmarks'
                # name+description re-sync (advisor mirrors tools.advisor: only
                # bumped True here, never demoted, same as seed_tools.py's main()).
                if c.get("advisor") and not existing["advisor"]:
                    lib.conn.execute("UPDATE communities SET advisor=1 WHERE id=?", (existing["id"],))
                    lib.conn.commit()
                    print(f"  UPDATED advisor flag: {c['name']}")
                if existing["name"] != fields["name"] or existing["notes"] != fields["notes"]:
                    lib.update_community_content(existing["id"], fields["name"], fields["notes"])
                    print(f"  UPDATED: {c['name']}")
                    updated += 1
                else:
                    print(f"  SKIP  {c['name']}")
                skipped += 1
                continue
            try:
                community_id = lib.add_community(**fields, approved=1, advisor=int(c.get("advisor", False)))
            except DuplicateURLError as e:
                # Defense in depth — the existing_by_url lookup above should already
                # catch this, so this only fires if two COMMUNITIES entries themselves
                # share a normalized URL. Skip rather than crash the whole run.
                print(f"  SKIP  {c['name']} — URL collides with existing {e.entry_id} ({e.name!r})")
                continue
            print(f"  ADDED {c['name']} (id={community_id})")
            added += 1
    finally:
        lib.close()

    print(f"\n{added} added, {updated} updated, {skipped - updated} unchanged.")


if __name__ == "__main__":
    main()
