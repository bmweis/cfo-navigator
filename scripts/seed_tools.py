#!/usr/bin/env python3
"""Seed the CFO Toolbox with a curated list of well-known vendors.

Safe to run multiple times — skips any tool whose URL is already in the DB.

Usage:
    python -m scripts.seed_tools [--db library.db]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library

TOOLS = [
    {
        "name": "Sequence",
        "url": "https://www.sequencehq.com",
        "description": "All-in-one CPQ, billing, usage metering, and revenue recognition for B2B SaaS. Automates the full quote-to-cash workflow — from contract to invoice to recognized revenue.",
        "categories": ["Billing"],
    },
    {
        "name": "Tabs",
        "url": "https://www.tabs.com",
        "description": "AI-powered billing and accounts receivable automation for B2B companies. Ingests contracts, automates invoicing, manages collections, and handles GAAP revenue recognition.",
        "categories": ["Billing"],
        "advisor": True,
    },
    {
        "name": "Numeric",
        "url": "https://www.numeric.io",
        "description": "AI-powered financial close automation for accounting teams. Centralizes close checklists, account reconciliations, flux analysis, and ERP integrations in one purpose-built platform.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Abacum",
        "url": "https://www.abacum.io",
        "description": "AI-native FP&A platform for modern finance teams. Consolidates data from across the business for budgeting, forecasting, scenario modeling, and management reporting.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Campfire",
        "url": "https://campfire.ai",
        "description": "AI-native ERP built for high-growth finance teams. Combines general ledger, revenue automation, close management, and reporting so lean teams can scale without adding headcount.",
        "categories": ["Financial Close", "FP&A"],
    },
    {
        "name": "Stripe",
        "url": "https://stripe.com",
        "description": "Financial infrastructure platform for internet businesses. Handles payments, subscriptions, usage-based billing, revenue recognition, and tax collection across 135+ currencies.",
        "categories": ["Billing"],
    },
    {
        "name": "Ledge",
        "url": "https://www.ledge.co",
        "description": "AI-powered close management and reconciliation platform for finance teams. Automates account reconciliations, journal entries, and flux prep so controllers can close faster.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Ramp",
        "url": "https://ramp.com",
        "description": "Corporate cards, expense management, and AP automation in one platform. Provides real-time spend visibility, automated policy enforcement, and integrations with major ERPs.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Zip",
        "url": "https://ziphq.com",
        "description": "Procurement intake and orchestration platform. Creates a single front door for purchase requests, routes approvals automatically, and connects to your ERP and CLM systems.",
        "categories": ["Procurement"],
    },
    {
        "name": "Concourse",
        "url": "https://www.concourse.co",
        "description": "AI agents for corporate finance teams. Connects to your ERP, CRM, and data warehouse to generate reports, forecasts, and variance analysis through natural language — no SQL required.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Drivetrain",
        "url": "https://www.drivetrain.ai",
        "description": "AI-native FP&A and business planning platform. Supports multi-dimensional modeling, scenario analysis, and KPI reporting with 800+ integrations and a familiar spreadsheet-like interface.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Anrok",
        "url": "https://www.anrok.com",
        "description": "Global sales tax and VAT compliance platform built for SaaS. Monitors nexus in real time, calculates tax on invoices, and automates filing across 100+ countries.",
        "categories": ["Sales Tax"],
    },
    {
        "name": "Kintsugi",
        "url": "https://trykintsugi.com",
        "description": "AI-powered sales tax, VAT, and GST automation. Monitors nexus, calculates tax in real time, and handles filing and remittance automatically across the US and internationally.",
        "categories": ["Sales Tax"],
    },
    {
        "name": "Maxio",
        "url": "https://www.maxio.com",
        "description": "Subscription billing and revenue management platform for B2B SaaS. Manages the full quote-to-cash process including CPQ, billing, revenue recognition, and SaaS metrics reporting.",
        "categories": ["Billing"],
    },
    {
        "name": "Zenskar",
        "url": "https://www.zenskar.com",
        "description": "AI-native billing and revenue recognition automation for complex pricing models. Handles usage-based billing, ASC 606-compliant RevRec, and integrates with CRMs, ERPs, and payment systems.",
        "categories": ["Billing"],
    },
    {
        "name": "RightRev",
        "url": "https://www.rightrev.com",
        "description": "ASC 606 and IFRS 15 revenue recognition automation for SaaS and usage-based businesses. Processes complex contracts at scale with full audit readiness and native Salesforce integration.",
        "categories": ["Financial Close"],
    },
    {
        "name": "HubiFi",
        "url": "https://www.hubifi.com",
        "description": "Automated revenue recognition and reconciliation platform for high-volume businesses. Handles ASC 606 compliance, continuous close, and real-time revenue reporting across complex billing models.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Runway",
        "url": "https://runway.com",
        "description": "FP&A platform built for high-growth teams. Connects 750+ data sources for financial modeling, forecasting, and reporting with human-readable formulas and real-time actuals sync.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Lumera",
        "url": "https://www.lumerahq.com",
        "description": "Governed AI infrastructure for finance teams. Lets controllers and FP&A teams build automations, agents, and scripts for close workflows — reconciliations, flux, journal entries — with full audit trails.",
        "categories": ["Financial Close", "FP&A"],
    },
    {
        "name": "Planful",
        "url": "https://planful.com",
        "description": "End-to-end financial performance management platform covering planning, budgeting, forecasting, close, consolidation, and reporting. Built for the Office of the CFO at mid-market to enterprise scale.",
        "categories": ["FP&A", "Financial Planning", "Financial Close"],
    },
    {
        "name": "Aleph",
        "url": "https://www.getaleph.com",
        "description": "AI-native FP&A platform that works alongside your existing spreadsheets. Connects 150+ data sources for financial modeling, budget planning, reporting, and close — with a familiar spreadsheet interface.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Pigment",
        "url": "https://www.pigment.com",
        "description": "Enterprise AI business planning platform for finance, sales, and HR. Covers FP&A, headcount planning, and sales performance management in a single connected model with real-time scenario analysis.",
        "categories": ["FP&A", "Financial Planning", "Headcount Planning"],
    },
    {
        "name": "Glidely",
        "url": "https://glidely.ai",
        "description": "AI agents for procurement renewals and new purchasing. Automates RFx generation, vendor scoring, and negotiation prep — built for teams without a dedicated procurement function.",
        "categories": ["Procurement"],
    },
    {
        "name": "TaxJar",
        "url": "https://www.taxjar.com",
        "description": "Sales tax automation platform for e-commerce and SaaS. Handles real-time calculations, nexus tracking, and returns filing across all US states and select international jurisdictions.",
        "categories": ["Sales Tax"],
    },
    {
        "name": "Orb",
        "url": "https://www.withorb.com",
        "description": "Usage-based billing infrastructure for modern SaaS. Ingests raw events, models complex pricing plans, generates invoices, and integrates with your CRM, data warehouse, and payment processor.",
        "categories": ["Billing"],
    },
    {
        "name": "FinQuery",
        "url": "https://www.finquery.com",
        "description": "Lease and contract accounting software for ASC 842, IFRS 16, and GASB 87 compliance. Automates journal entries, disclosures, and audit-ready reporting for lease portfolios.",
        "categories": ["Financial Close", "Contract Management"],
    },
    {
        "name": "Ironclad",
        "url": "https://ironcladapp.com",
        "description": "Enterprise contract lifecycle management platform. Automates contract creation, negotiation, approvals, and analytics — giving legal and finance teams a single system of record.",
        "categories": ["Contract Management"],
    },
    {
        "name": "LinkSquares",
        "url": "https://www.linksquares.com",
        "description": "AI-powered contract management and analytics platform. Extracts key terms from executed contracts, tracks obligations and renewals, and provides a searchable legal data repository.",
        "categories": ["Contract Management"],
    },
    {
        "name": "Coupa",
        "url": "https://www.coupa.com",
        "description": "Enterprise business spend management platform covering procurement, invoicing, expense management, and supply chain. Provides end-to-end spend visibility and compliance for mid-market to enterprise.",
        "categories": ["Spend Management", "Procurement"],
    },
    {
        "name": "Tropic",
        "url": "https://www.tropicapp.io",
        "description": "Software spend management and procurement automation. Tracks SaaS contracts, benchmarks pricing, and manages renewals and purchasing workflows to reduce software costs.",
        "categories": ["Spend Management", "Procurement"],
    },
    {
        "name": "Glean",
        "url": "https://www.glean.ai",
        "description": "SaaS spend management platform that analyzes software invoices and contracts to surface savings opportunities, flag duplicates, and benchmark pricing against market rates.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Vendr",
        "url": "https://www.vendr.com",
        "description": "SaaS purchasing platform combining procurement software with expert negotiation services. Manages vendor discovery, renewal workflows, and price benchmarking to reduce software spend.",
        "categories": ["Procurement", "Spend Management"],
    },
    {
        "name": "Tipalti",
        "url": "https://www.tipalti.com",
        "description": "Global payables automation platform. Handles supplier onboarding, invoice processing, multi-currency payments, tax compliance (W-9/W-8/1099), and AP reconciliation at scale.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Expensify",
        "url": "https://www.expensify.com",
        "description": "Expense management and corporate card platform. Automates receipt capture, approvals, reimbursements, and accounting sync — covering both employee expenses and corporate card spend.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Teampay",
        "url": "https://www.teampay.com",
        "description": "Distributed spend management platform with conversational intake. Employees request purchases via chat, approvals route automatically, and spend hits corporate cards with built-in controls.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Metronome",
        "url": "https://www.metronome.com",
        "description": "Usage-based billing and revenue infrastructure for cloud and AI companies. Ingests product usage events, models flexible pricing plans, and generates accurate invoices at any scale.",
        "categories": ["Billing"],
    },
    {
        "name": "BILL",
        "url": "https://www.bill.com",
        "description": "AP and AR automation platform for SMBs. Digitizes invoice processing, automates approval workflows, handles domestic and international payments, and syncs with major accounting software.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Airbase",
        "url": "https://www.airbase.com",
        "description": "Spend management platform combining corporate cards, AP automation, and expense reimbursement. Provides pre-approval workflows, real-time spend controls, and ERP integrations in one system.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Datarails",
        "url": "https://www.datarails.com",
        "description": "FP&A platform that works within Excel. Consolidates data from ERPs and other sources into a governed model, enabling automated reporting, budgeting, and forecasting without leaving spreadsheets.",
        "categories": ["FP&A", "Financial Planning"],
    },
    {
        "name": "Rillet",
        "url": "https://www.rillet.com",
        "description": "AI-native general ledger and financial close platform built for SaaS. Automates month-end close, revenue recognition, and reporting — purpose-built for subscription and usage-based businesses.",
        "categories": ["Financial Close"],
        "advisor": True,
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed the CFO Toolbox with curated vendors.")
    parser.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    args = parser.parse_args()

    lib = Library(args.db)
    added = skipped = 0
    try:
        for t in TOOLS:
            existing = lib.conn.execute(
                "SELECT id, advisor FROM tools WHERE url = ?", (t["url"],)
            ).fetchone()
            if existing:
                if t.get("advisor") and not existing["advisor"]:
                    lib.conn.execute("UPDATE tools SET advisor=1 WHERE id=?", (existing["id"],))
                    lib.conn.commit()
                    print(f"  UPDATED advisor flag: {t['name']}")
                else:
                    print(f"  SKIP  {t['name']}")
                skipped += 1
                continue
            tool_id = lib.add_tool(
                name=t["name"],
                description=t["description"],
                url=t["url"],
                categories=t["categories"],
                approved=1,
                advisor=int(t.get("advisor", False)),
            )
            print(f"  ADDED {t['name']} (id={tool_id})")
            added += 1
    finally:
        lib.close()

    print(f"\n{added} added, {skipped} already present.")


if __name__ == "__main__":
    main()
