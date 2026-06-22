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
        "categories": ["Billing", "Contract Management"],
    },
    {
        "name": "Tabs",
        "url": "https://www.tabs.com",
        "description": "AI-powered billing and accounts receivable automation for B2B companies. Ingests contracts, automates invoicing, manages collections, and handles GAAP revenue recognition.",
        "categories": ["Billing", "Financial Close"],
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
        "categories": ["Billing"],
    },
    {
        "name": "Kintsugi",
        "url": "https://trykintsugi.com",
        "description": "AI-powered sales tax, VAT, and GST automation. Monitors nexus, calculates tax in real time, and handles filing and remittance automatically across the US and internationally.",
        "categories": ["Billing"],
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
                "SELECT id FROM tools WHERE url = ?", (t["url"],)
            ).fetchone()
            if existing:
                print(f"  SKIP  {t['name']}")
                skipped += 1
                continue
            tool_id = lib.add_tool(
                name=t["name"],
                description=t["description"],
                url=t["url"],
                categories=t["categories"],
                approved=1,
            )
            print(f"  ADDED {t['name']} (id={tool_id})")
            added += 1
    finally:
        lib.close()

    print(f"\n{added} added, {skipped} already present.")


if __name__ == "__main__":
    main()
