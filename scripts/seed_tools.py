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
from linklib.db import DuplicateURLError, Library, normalize_url, resolve_db_path

TOOLS = [
    {
        "name": "Sequence",
        "url": "https://www.sequencehq.com",
        "description": "CPQ, billing, usage metering, and revenue recognition for B2B SaaS in one platform. Automates the quote-to-cash workflow end to end, from contract terms through invoicing to recognized revenue.",
        "categories": ["Revenue"],
    },
    {
        "name": "Tabs",
        "url": "https://www.tabs.com",
        "description": "Billing and accounts receivable automation for B2B companies. Reads contracts directly to automate invoicing, collections, and GAAP revenue recognition, cutting the manual translation of contract terms into billing logic.",
        "categories": ["Revenue"],
        "advisor": True,
    },
    {
        "name": "Numeric",
        "url": "https://www.numeric.io",
        "description": "Financial close automation for accounting teams. Centralizes close checklists, account reconciliations, flux analysis, and ERP integrations in one system built specifically around the close, not general ledger work.",
        "categories": ["Accounting"],
    },
    {
        "name": "Abacum",
        "url": "https://www.abacum.io",
        "description": "AI-native FP&A for finance teams: consolidates data across the business for budgeting, forecasting, scenario modeling, and management reporting in one connected model.",
        "categories": ["FP&A"],
    },
    {
        "name": "Campfire",
        "url": "https://campfire.ai",
        "description": "AI-native ERP for high-growth finance teams: general ledger, revenue automation, close management, and reporting in one system, aimed at scaling without adding headcount to run it.",
        "categories": ["ERP"],
    },
    {
        "name": "Stripe",
        "url": "https://stripe.com",
        "description": "Payments infrastructure for internet businesses, extended into subscriptions, usage-based billing, revenue recognition, and tax collection across 135+ currencies. The default payments layer most SaaS companies build on first.",
        "categories": ["Revenue"],
    },
    {
        "name": "Ledge",
        "url": "https://www.ledge.co",
        "description": "Close management and reconciliation for finance teams. Automates account reconciliations, journal entries, and flux prep so controllers close faster without a separate reconciliation spreadsheet.",
        "categories": ["Accounting"],
    },
    {
        "name": "Ramp",
        "url": "https://ramp.com",
        "description": "Corporate cards, expense management, and AP automation in one platform, with real-time spend visibility, automated policy enforcement, and integrations with major ERPs.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Zip",
        "url": "https://ziphq.com",
        "description": "Procurement intake and orchestration: a single front door for purchase requests, automated approval routing, and connections to your ERP and CLM systems.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Concourse",
        "url": "https://www.concourse.co",
        "description": "AI agents for corporate finance teams: connects to your ERP, CRM, and data warehouse to generate reports, forecasts, and variance analysis through natural language instead of SQL.",
        "categories": ["FP&A"],
    },
    {
        "name": "Drivetrain",
        "url": "https://www.drivetrain.ai",
        "description": "FP&A and business planning with multi-dimensional modeling, scenario analysis, and KPI reporting. 800+ integrations and a spreadsheet-like interface for teams not ready to leave that mental model.",
        "categories": ["FP&A"],
    },
    {
        "name": "Anrok",
        "url": "https://www.anrok.com",
        "description": "Global sales tax and VAT compliance built for SaaS: monitors nexus in real time, calculates tax on invoices, and automates filing across 100+ countries.",
        "categories": ["Tax Management"],
    },
    {
        "name": "Kintsugi",
        "url": "https://trykintsugi.com",
        "description": "AI-powered sales tax, VAT, and GST automation: monitors nexus, calculates tax in real time, and handles filing and remittance across the US and internationally.",
        "categories": ["Tax Management"],
    },
    {
        "name": "Maxio",
        "url": "https://www.maxio.com",
        "description": "Subscription billing and revenue management for B2B SaaS, formed from the SaaSOptics/Chargify merger. Covers CPQ, billing, revenue recognition, and SaaS metrics reporting for mid-market subscription businesses.",
        "categories": ["Revenue"],
    },
    {
        "name": "Zenskar",
        "url": "https://www.zenskar.com",
        "description": "Billing and revenue recognition automation built for complex, usage-based pricing models. Handles ASC 606-compliant RevRec and integrates with CRMs, ERPs, and payment systems without custom engineering work.",
        "categories": ["Revenue"],
    },
    {
        "name": "RightRev",
        "url": "https://www.rightrev.com",
        "description": "ASC 606 and IFRS 15 revenue recognition automation for SaaS and usage-based businesses. Processes complex, multi-element contracts at scale with native Salesforce integration and audit-ready output.",
        "categories": ["Revenue"],
    },
    {
        "name": "HubiFi",
        "url": "https://www.hubifi.com",
        "description": "Automated revenue recognition and reconciliation for high-volume businesses. Handles ASC 606 compliance and continuous close so revenue reporting doesn't wait for month-end.",
        "categories": ["Revenue"],
    },
    {
        "name": "Runway",
        "url": "https://runway.com",
        "description": "FP&A built for high-growth teams, connecting 750+ data sources for modeling, forecasting, and reporting. Formulas stay human-readable and actuals sync in real time rather than batch-refreshing.",
        "categories": ["FP&A"],
    },
    {
        "name": "Lumera",
        "url": "https://www.lumerahq.com",
        "description": "Governed AI infrastructure for finance teams to build their own automations, agents, and scripts for close workflows — reconciliations, flux, journal entries — with full audit trails rather than a black-box AI layer.",
        "categories": ["FP&A"],
    },
    {
        "name": "Planful",
        "url": "https://planful.com",
        "description": "End-to-end financial performance management: planning, budgeting, forecasting, close, consolidation, and reporting in one platform, built for the Office of the CFO at mid-market to enterprise scale.",
        "categories": ["FP&A"],
    },
    {
        "name": "Aleph",
        "url": "https://www.getaleph.com",
        "description": "AI-native FP&A that works alongside existing spreadsheets rather than replacing them. Connects 150+ data sources for modeling, budget planning, reporting, and close with a familiar spreadsheet interface.",
        "categories": ["FP&A"],
    },
    {
        "name": "Pigment",
        "url": "https://www.pigment.com",
        "description": "Enterprise business planning across finance, sales, and HR in a single connected model, with real-time scenario analysis spanning FP&A, headcount planning, and sales performance management.",
        "categories": ["FP&A"],
    },
    {
        "name": "Glidely",
        "url": "https://glidely.ai",
        "description": "AI agents for procurement renewals and new purchasing: automates RFx generation, vendor scoring, and negotiation prep, built for teams without a dedicated procurement function.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "TaxJar",
        "url": "https://www.taxjar.com",
        "description": "Sales tax automation for e-commerce and SaaS: real-time calculations, nexus tracking, and returns filing across all US states and select international jurisdictions.",
        "categories": ["Tax Management"],
    },
    {
        "name": "Orb",
        "url": "https://www.withorb.com",
        "description": "Usage-based billing infrastructure for SaaS. Ingests raw usage events, models pricing plans, and generates invoices, with native integrations to CRM, data warehouse, and payment processor rather than a standalone ledger.",
        "categories": ["Revenue"],
    },
    {
        "name": "FinQuery",
        "url": "https://www.finquery.com",
        "description": "Lease and contract accounting for ASC 842, IFRS 16, and GASB 87 compliance: automates journal entries, disclosures, and audit-ready reporting for lease portfolios.",
        "categories": ["Legal and Contracting"],
    },
    {
        "name": "Ironclad",
        "url": "https://ironcladapp.com",
        "description": "Enterprise contract lifecycle management: automates contract creation, negotiation, approvals, and analytics, giving legal and finance a single system of record.",
        "categories": ["Legal and Contracting"],
    },
    {
        "name": "LinkSquares",
        "url": "https://www.linksquares.com",
        "description": "AI-powered contract management and analytics: extracts key terms from executed contracts, tracks obligations and renewals, and provides a searchable legal data repository.",
        "categories": ["Legal and Contracting"],
    },
    {
        "name": "Coupa",
        "url": "https://www.coupa.com",
        "description": "Business spend management covering procurement, invoicing, expense management, and supply chain, with end-to-end spend visibility and compliance for mid-market to enterprise.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Tropic",
        "url": "https://www.tropicapp.io",
        "description": "Software spend management and procurement automation: tracks SaaS contracts, benchmarks pricing, and manages renewals and purchasing workflows to reduce software costs.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Glean",
        "url": "https://www.glean.ai",
        "description": "SaaS spend management that analyzes software invoices and contracts to surface savings opportunities, flag duplicates, and benchmark pricing against market rates.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Tipalti",
        "url": "https://www.tipalti.com",
        "description": "Global payables automation: supplier onboarding, invoice processing, multi-currency payments, tax compliance (W-9/W-8/1099), and AP reconciliation at scale, aimed at companies paying suppliers or contractors in many countries at once.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Expensify",
        "url": "https://www.expensify.com",
        "description": "Expense management and corporate card platform automating receipt capture, approvals, reimbursements, and accounting sync, covering both employee expenses and corporate card spend in one workflow.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Teampay (acquired by PayStand)",
        "url": "https://www.teampay.com",
        "description": "Distributed spend management with conversational intake: employees request purchases via chat, approvals route automatically, and spend hits corporate cards with built-in controls.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Metronome",
        "url": "https://www.metronome.com",
        "description": "Usage-based billing for cloud and AI companies. Ingests product usage events, models flexible pricing plans, and generates invoices at high transaction volume — built for metering, not flat subscriptions.",
        "categories": ["Revenue"],
    },
    {
        "name": "BILL",
        "url": "https://www.bill.com",
        "description": "AP and AR automation for SMBs: digitizes invoice processing, automates approval workflows, and handles domestic and international payments, syncing with major accounting software rather than replacing it.",
        "categories": ["Procurement/Spend", "Revenue"],
    },
    {
        "name": "Airbase (acquired by Paylocity)",
        "url": "https://www.airbase.com",
        "description": "Spend management combining corporate cards, AP automation, and expense reimbursement, with pre-approval workflows, real-time spend controls, and ERP integrations in one system.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Datarails",
        "url": "https://www.datarails.com",
        "description": "FP&A that works inside Excel. Consolidates data from ERPs and other sources into a governed model, automating reporting, budgeting, and forecasting without moving the team out of spreadsheets.",
        "categories": ["FP&A"],
    },
    {
        "name": "Rillet",
        "url": "https://www.rillet.com",
        "description": "AI-native ERP and general ledger for SaaS. Automates month-end close, revenue recognition, and reporting, purpose-built for subscription and usage-based businesses rather than adapted from a generic ledger.",
        "categories": ["ERP"],
        "advisor": True,
    },
    {
        "name": "GoClose",
        "url": "https://www.goclose.com",
        "description": "Close management that centralizes close checklists, reconciliations, and task assignments, giving controllers real-time visibility into close status and where things are stuck.",
        "categories": ["Accounting"],
    },
    {
        "name": "Lumos",
        "url": "https://www.lumos.ai",
        "description": "AI-native accounts receivable and collections: automates dunning workflows, payment follow-ups, and dispute resolution to accelerate cash collection and reduce DSO.",
        "categories": ["Revenue"],
        "advisor": True,
    },
    {
        "name": "Chargebee",
        "url": "https://www.chargebee.com",
        "description": "Subscription management and recurring billing for SaaS. Handles flexible pricing models, dunning, and revenue recognition, with integrations to the major payment gateways and ERPs finance teams already run.",
        "categories": ["Revenue"],
    },
    {
        "name": "Recurly",
        "url": "https://recurly.com",
        "description": "Subscription billing with flexible pricing, dunning management, and revenue recognition automation, aimed at recovering failed payments and reducing involuntary churn for recurring-revenue businesses.",
        "categories": ["Revenue"],
    },
    {
        "name": "HighRadius",
        "url": "https://www.highradius.com",
        "description": "Enterprise AI-powered order-to-cash: cash application, credit risk management, collections, and deductions in one suite, purpose-built for mid-market and enterprise finance teams running high transaction volume.",
        "categories": ["Revenue"],
    },
    {
        "name": "Billtrust",
        "url": "https://www.billtrust.com",
        "description": "B2B accounts receivable automation: invoice delivery, payment portals, cash application, and collections, aimed at accelerating cash flow and reducing manual AR work for mid-market and enterprise.",
        "categories": ["Revenue"],
    },
    {
        "name": "Versapay",
        "url": "https://www.versapay.com",
        "description": "Collaborative accounts receivable connecting suppliers and buyers on a shared network for invoice resolution, payment, and cash application, reducing DSO through buyer-seller collaboration rather than one-sided dunning.",
        "categories": ["Revenue"],
    },
    {
        "name": "BlackLine",
        "url": "https://www.blackline.com",
        "description": "Financial close and accounting automation for mid-market to enterprise. Centralizes account reconciliations, transaction matching, journal entries, and intercompany accounting with full audit trails — one of the category's original players.",
        "categories": ["Accounting"],
    },
    {
        "name": "Spendesk",
        "url": "https://www.spendesk.com",
        "description": "Spend management combining corporate cards, invoice processing, and expense reimbursements in one product, with real-time visibility, approval workflows, and accounting integrations for finance teams that want one system instead of three.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Sage Intacct",
        "url": "https://www.sageintacct.com",
        "description": "Cloud ERP and accounting for mid-market businesses, part of Sage. Provides multi-entity consolidation, project accounting, revenue recognition, and financial reporting with deep integrations across the finance stack.",
        "categories": ["ERP"],
    },
    {
        "name": "NetSuite (acquired by Oracle)",
        "url": "https://www.netsuite.com",
        "description": "Cloud ERP and the dominant mid-market to enterprise accounting and operations system, owned by Oracle. Covers financial management, inventory, CRM, and ecommerce in one suite.",
        "categories": ["ERP"],
    },
    {
        "name": "Vareto",
        "url": "https://www.vareto.com",
        "description": "FP&A for enterprise finance teams, focused on real-time reporting and a modern system of record rather than a spreadsheet replacement layer.",
        "categories": ["FP&A"],
    },
    {
        "name": "Brex",
        "url": "https://www.brex.com",
        "description": "Corporate cards, expense management, and bill pay for growing companies: high credit limits, automated expense categorization, real-time spend controls, and integrations with major ERPs.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Airwallex",
        "url": "https://www.airwallex.com",
        "description": "Global payments infrastructure: multi-currency business accounts, FX at interbank rates, international AP, and card issuing, built for companies operating across borders rather than bolted onto a domestic-first platform.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Carta",
        "url": "https://carta.com",
        "description": "Equity management for private companies: cap tables, 409A valuations, employee equity grants, and investor reporting — the most widely used cap table tool among venture-backed startups.",
        "categories": ["Equity Management"],
    },
    {
        "name": "Pulley",
        "url": "https://www.pulley.com",
        "description": "Cap table management built for startups: equity grants, scenario modeling, and 409A valuations, positioned on a cleaner interface and faster 409A turnaround than legacy tools.",
        "categories": ["Equity Management"],
    },
    {
        "name": "Ledgy",
        "url": "https://www.ledgy.com",
        "description": "Equity management for European and global startups: cap table, employee equity plans, vesting schedules, and investor relations, with strong multi-jurisdiction support.",
        "categories": ["Equity Management"],
    },
    {
        "name": "Fidelity Private Shares",
        "url": "https://www.fidelityprivateshares.com",
        "description": "Fidelity's equity management for private companies: cap table management, 409A valuations, and equity plan administration backed by Fidelity's brokerage infrastructure.",
        "categories": ["Equity Management"],
    },
    {
        "name": "CaptivateIQ",
        "url": "https://www.captivateiq.com",
        "description": "Sales commission management: automates complex commission calculations, handles plan design, and gives reps real-time earnings visibility, reducing finance time spent on spreadsheet reconciliation.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Everstage",
        "url": "https://www.everstage.com",
        "description": "No-code sales commission software: automates incentive calculations, provides real-time rep dashboards, and integrates with CRMs and ERPs, for ops teams that don't want to manage commission logic in code.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "QuotaPath",
        "url": "https://www.quotapath.com",
        "description": "Commission tracking and compensation management: simplifies complex commission plans, automates calculations, and gives sales teams earnings transparency without back-and-forth with finance.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Xactly",
        "url": "https://www.xactlycorp.com",
        "description": "Enterprise incentive compensation management: complex commission plan design, automated calculations, and analytics for large sales organizations with multi-tier, multi-currency commission structures.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Cube",
        "url": "https://www.cubesoftware.com",
        "description": "FP&A that integrates with Excel and Google Sheets rather than replacing them. Centralizes financial data and automates consolidation and reporting while keeping collaborative planning inside spreadsheets teams already use.",
        "categories": ["FP&A"],
    },
    {
        "name": "Pave",
        "url": "https://www.pave.com",
        "description": "Compensation management and benchmarking: real-time salary, equity, and total compensation data to help companies build and communicate competitive comp structures, with direct HRIS integrations.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Culpepper",
        "url": "https://www.culpeppercomp.com",
        "description": "Compensation survey and benchmarking data: base pay, bonuses, and long-term incentives across industries, segmented by company size, geography, and sector.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Radford (acquired by Aon)",
        "url": "https://radford.aon.com",
        "description": "Compensation benchmarking surveys and data, part of Aon. One of the most widely used comp survey providers, covering tech and life sciences with a deep cut of data by role, level, and geography.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Hemrock",
        "url": "https://www.hemrock.com",
        "description": "Financial models built by a fractional CFO (Taylor Davidson), editable with AI and runnable as a spreadsheet, in-browser, or through an AI assistant. A lighter alternative to a full FP&A platform for teams not ready to make that jump.",
        "categories": ["FP&A"],
    },
    # Treasury / Cash Management
    {
        "name": "Agicap",
        "url": "https://agicap.com",
        "description": "European cash flow management: aggregates bank accounts, ERPs, and invoices into a real-time cash position and rolling forecast, with strong multi-currency support for companies operating across Europe.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Trovata",
        "url": "https://trovata.io",
        "description": "Treasury and cash management connecting directly to bank APIs: automates cash reporting and balance aggregation across accounts and currencies, and builds rolling cash forecasts without manual bank file imports.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Tresio",
        "url": "https://www.tresio.io",
        "description": "Cash flow forecasting and liquidity planning for SMBs and scale-ups, pulling from bank accounts and ERPs for rolling forecasts and scenario analysis.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Trezy",
        "url": "https://trezy.com",
        "description": "European cash flow management and forecasting, connecting to bank accounts across multiple currencies for real-time cash visibility and multi-entity consolidation.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Sibill",
        "url": "https://www.sibill.com",
        "description": "European cash flow forecasting connecting to bank accounts and ERPs across multiple currencies to deliver rolling forecasts, payment planning, and liquidity alerts.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Tidely",
        "url": "https://www.tidely.com",
        "description": "Cash flow planning and liquidity management with strong European bank connectivity: rolling forecasts, multi-currency support, and scenario modeling for SMBs operating across EMEA.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Viably",
        "url": "https://www.viably.com",
        "description": "Working capital and cash flow for e-commerce businesses, combining forecasting with embedded financing to manage inventory cycles and seasonal cash gaps.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Nilus",
        "url": "https://nilus.com",
        "description": "Treasury management for mid-market companies: real-time cash visibility across bank accounts and entities, liquidity forecasting, and payment operations — a lighter-weight alternative to enterprise TMS platforms.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Kyriba",
        "url": "https://www.kyriba.com",
        "description": "Enterprise treasury management: cash visibility across hundreds of bank accounts and entities, liquidity forecasting, payments, and FX risk management. The platform most large finance teams evaluate first for treasury.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "GTreasury",
        "url": "https://www.gtreasury.com",
        "description": "Treasury and risk management for mid-to-large enterprises: cash positioning, debt and investment management, FX hedging, and global bank integration, competing directly with Kyriba.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Vesto",
        "url": "https://www.vesto.io",
        "description": "Startup-focused treasury: automates investing idle operating cash into T-bills, money market funds, and other short-duration instruments so companies earn yield without manual banking operations.",
        "categories": ["Treasury/Cash Management"],
    },
    {
        "name": "Corpay",
        "url": "https://www.corpay.com",
        "description": "Global corporate payments and FX risk management: cross-border payments, currency hedging, and international AP automation for companies with significant foreign currency exposure.",
        # Deliberately Procurement/Spend + Travel Management, not
        # Treasury/Cash Management (the seed file's stale original value) —
        # this is a named exception from the original out-of-scope cleanup
        # (see the deleted-tools-reappearing fix). categories_json isn't
        # re-synced from this list for existing rows (see _seed_toolbox's
        # docstring), so this only matters for a brand-new DB's first-time
        # seed or a row that was hard-deleted and needs re-adding by hand —
        # but it should read correctly either way.
        "categories": ["Procurement/Spend", "Travel Management"],
    },
    {
        "name": "TreasurySpring",
        "url": "https://www.treasuryspring.com",
        "description": "UK-based corporate treasury investment platform giving companies direct access to short-term fixed-income instruments — T-bills, money market funds, term deposits — at institutional-grade terms.",
        "categories": ["Treasury/Cash Management"],
    },
    # Sales Tax additions
    {
        "name": "Avalara",
        "url": "https://www.avalara.com",
        "description": "Sales tax compliance across all US states and 100+ countries: monitors nexus, calculates tax in real time, and automates filing and remittance, with integrations across virtually every ERP and billing system.",
        "categories": ["Tax Management"],
    },
    {
        "name": "Zamp",
        "url": "https://www.zamp.com",
        "description": "Fully managed sales tax service: handles registration, collection, filing, and remittance on your behalf so your finance team doesn't touch the workflow — for teams that want to hand the whole problem off.",
        "categories": ["Tax Management"],
    },
    # Financial Reporting
    {
        "name": "Liveflow",
        "url": "https://www.liveflow.io",
        "description": "Financial reporting automation pulling live data from QuickBooks and Xero directly into Google Sheets, for P&L, cash flow, and board reporting without leaving spreadsheets.",
        "categories": ["Accounting"],
    },
    {
        "name": "EMAsphere",
        "url": "https://www.emasphere.com",
        "description": "Automated financial and management reporting, consolidating ERP data into board-ready reports, dashboards, and multi-entity consolidations, cutting the manual spreadsheet work behind recurring board packs. Widely used across Europe.",
        "categories": ["Accounting"],
    },
    {
        "name": "Klipfolio",
        "url": "https://www.klipfolio.com",
        "description": "KPI dashboard and business metrics platform connecting to 100+ data sources for real-time executive dashboards, used for board-level reporting and operational metrics tracking.",
        "categories": ["BI/Analytics"],
    },
    # Financial Close additions
    {
        "name": "FloQast",
        "url": "https://floqast.com",
        "description": "Close management built around the checklist controllers already use. Centralizes reconciliations, flux analysis, and audit prep on top of the existing close process rather than replacing it.",
        "categories": ["Accounting"],
    },
    {
        "name": "Gappify",
        "url": "https://gappify.com",
        "description": "Account reconciliation and close automation: accruals, reconciliations, and close task management with full audit trails, competing directly with FloQast and BlackLine for controller-owned workflows.",
        "categories": ["Accounting"],
    },
    {
        "name": "Numeral",
        "url": "https://www.numeral.io",
        "description": "Close automation focused on the month-end process — reconciliations, journal entries, and close reporting — for high-growth companies outgrowing spreadsheet-based close.",
        "categories": ["Accounting"],
    },
    # Cloud/IT Spend
    {
        "name": "Apptio (acquired by IBM)",
        "url": "https://www.apptio.com",
        "description": "Technology business management and cloud cost optimization, owned by IBM. Models total IT spend against business value and cost drivers, used by CFOs and CIOs to understand the true cost of technology at scale.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Finout",
        "url": "https://www.finout.io",
        "description": "Cloud cost visibility and FinOps: maps AWS, GCP, and Azure spend to business units, products, and customers to track unit economics like cost-per-customer alongside engineering spend.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "nOps",
        "url": "https://www.nops.io",
        "description": "AWS cost optimization: automates Reserved Instance and Savings Plan purchases, surfaces idle and underutilized resources, and tracks commitment coverage to reduce cloud waste.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Ternary",
        "url": "https://ternary.app",
        "description": "Cloud cost management with particularly deep GCP support, built by ex-Google engineers. Covers multi-cloud cost allocation, anomaly detection, and savings recommendations across AWS, GCP, and Azure.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Vantage",
        "url": "https://www.vantage.sh",
        "description": "Cloud cost observability: unit cost analysis, Kubernetes cost allocation, and savings recommendations across AWS, GCP, and Azure, with a developer-friendly interface finance teams can actually use.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Anodot",
        "url": "https://www.anodot.com",
        "description": "Cloud cost monitoring and anomaly detection using AI to surface unexpected cost spikes and efficiency opportunities across multi-cloud environments before they show up in the monthly bill.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "CloudZero",
        "url": "https://www.cloudzero.com",
        "description": "Cloud cost intelligence focused on unit economics: maps AWS, Azure, and GCP spend to products, features, and customers so engineering and finance can track cost-per-customer and cost-per-feature alongside cloud bills.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Torii",
        "url": "https://www.torii.io",
        "description": "SaaS management: discovers all software in use across the organization, tracks spend and license utilization, and manages renewals and offboarding to reduce shadow IT and wasted seats.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Zluri",
        "url": "https://www.zluri.com",
        "description": "SaaS management and identity governance: tracks software usage and license utilization across the stack, automates provisioning and deprovisioning, and surfaces savings from unused seats.",
        "categories": ["Cloud/IT Spend"],
    },
    # Procurement additions
    {
        "name": "Vertice",
        "url": "https://www.vertice.one",
        "description": "SaaS and cloud procurement: benchmarks software pricing against market rates, manages renewal workflows, and negotiates on your behalf, with pricing intelligence as the core differentiator versus peers.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Zylo",
        "url": "https://zylo.com",
        "description": "Enterprise SaaS management: discovers shadow IT, tracks license utilization and renewal dates, and benchmarks software costs against market data — the enterprise-tier option for companies managing hundreds of contracts.",
        "categories": ["Cloud/IT Spend"],
    },
    # Contract Management additions
    {
        "name": "Dealhub",
        "url": "https://dealhub.io",
        "description": "CPQ and contract lifecycle management: connects guided selling, quoting, eSign, and contract management in one revenue workflow, so finance can govern deal terms and track obligations.",
        "categories": ["Legal and Contracting"],
    },
    {
        "name": "Sirion",
        "url": "https://www.sirionlabs.com",
        "description": "Enterprise contract lifecycle management powered by AI: extracts obligations and key terms from executed contracts, tracks renewal and compliance milestones, and provides a searchable contract intelligence layer for legal and finance.",
        "categories": ["Legal and Contracting"],
    },
    # RevOps
    {
        "name": "Clari",
        "url": "https://www.clari.com",
        "description": "Revenue operations: pipeline inspection, forecast accuracy scoring, and deal intelligence from CRM activity data, used to improve revenue forecast confidence and board reporting on ARR.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Gong",
        "url": "https://www.gong.io",
        "description": "Revenue intelligence: records and analyzes sales calls, surfaces deal risks and coaching opportunities, and tracks pipeline health — often budget-owned by the CFO for revenue visibility and sales productivity measurement.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Weflow",
        "url": "https://www.weflow.io",
        "description": "Salesforce productivity and pipeline management: reps update CRM data faster and managers get real-time pipeline visibility, reducing the gap between what's in Salesforce and what's actually happening in deals.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "BoostUp",
        "url": "https://boostup.ai",
        "description": "Revenue forecasting and pipeline analytics: combines AI-driven deal scoring, rep activity signals, and forecast roll-ups to improve forecast accuracy for finance teams.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Revsure",
        "url": "https://www.revsure.ai",
        "description": "Pipeline analytics and revenue forecasting using historical CRM data to predict deal outcomes and surface at-risk pipeline, for finance and RevOps teams who need a reliable revenue signal.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "GrowBlocks",
        "url": "https://www.growblocks.com",
        "description": "Revenue planning and go-to-market analytics: models pipeline coverage, headcount, and revenue targets in one connected plan, bridging the FP&A model with what's actually happening in the business.",
        "categories": ["Revenue Operations"],
    },
    {
        "name": "Sightfull",
        "url": "https://www.sightfull.com",
        "description": "Revenue analytics for SaaS CFOs and RevOps teams: automates ARR waterfall, NRR, churn, and cohort reporting from CRM and billing data without a data team or custom SQL.",
        "categories": ["Revenue Operations"],
    },
    # Billing additions
    {
        "name": "m3ter",
        "url": "https://www.m3ter.com",
        "description": "Usage-based billing infrastructure for SaaS and cloud companies. Supports seat, consumption, and hybrid pricing from one metering and invoicing engine, aimed at teams running mixed pricing models.",
        "categories": ["Revenue"],
    },
    {
        "name": "Amberflo",
        "url": "https://www.amberflo.io",
        "description": "Cloud metering and usage-based billing. Handles real-time usage ingestion, pricing plan configuration, and customer-facing usage dashboards for teams shifting from seat-based to consumption pricing.",
        "categories": ["Revenue"],
    },
    {
        "name": "Togai (acquired by Zuora)",
        "url": "https://www.togai.com",
        "description": "Usage metering and rating engine with a low-code builder for configuring pricing on raw event data. Now part of Zuora's Billing/Revenue suite as a standalone consumption offering.",
        "categories": ["Revenue"],
    },
    {
        "name": "DigitalRoute",
        "url": "https://www.digitalroute.com",
        "description": "Usage data management and monetization for telco, SaaS, and IoT billing at high volume. Built for complex, high-throughput usage-based revenue models rather than simple per-seat billing.",
        "categories": ["Revenue"],
    },
    {
        "name": "Zuora",
        "url": "https://www.zuora.com",
        "description": "Subscription billing and recurring revenue management: pricing, invoicing, ASC 606/IFRS 15 revenue recognition, and SaaS metrics reporting. One of the most widely deployed billing platforms at mid-market to enterprise scale.",
        "categories": ["Revenue"],
    },
    {
        "name": "Paddle",
        "url": "https://www.paddle.com",
        "description": "Merchant of record for SaaS companies: payments, subscription billing, global sales tax/VAT compliance, and fraud prevention bundled so sellers don't register in every jurisdiction themselves. Strongest for international sellers.",
        "categories": ["Revenue", "Tax Management"],
    },
    # Collections additions
    {
        "name": "Tesorio",
        "url": "https://www.tesorio.com",
        "description": "Cash flow and AR management: automates collections outreach, forecasts cash from open receivables, and gives real-time visibility into what's owed and when, reducing DSO without adding headcount.",
        "categories": ["Revenue"],
    },
    {
        "name": "Kolleno",
        "url": "https://www.kolleno.com",
        "description": "AR and collections automation: dunning sequences, cash application, and dispute management for mid-market businesses — a leaner alternative to HighRadius and Billtrust for teams that don't need enterprise-scale complexity.",
        "categories": ["Revenue"],
    },
    # Spend Management additions
    {
        "name": "Payhawk",
        "url": "https://www.payhawk.com",
        "description": "Corporate cards, expense management, and AP automation with strong European coverage: multi-currency spend, VAT compliance, and ERP integrations across EMEA.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Pleo",
        "url": "https://www.pleo.io",
        "description": "European spend management and corporate cards: smart cards, out-of-pocket reimbursements, and invoice management in one product, built for teams across multiple European countries with multi-currency support.",
        "categories": ["Procurement/Spend"],
    },
    {
        "name": "Mesh Payments",
        "url": "https://www.meshpayments.com",
        "description": "Global corporate payment and spend management: virtual and physical cards, AP automation, and cross-border payment capabilities with multi-currency support for international operations.",
        "categories": ["Procurement/Spend"],
    },
    # Cap Table Management additions
    {
        "name": "Capdesk (acquired by Carta)",
        "url": "https://www.capdesk.com",
        "description": "European equity management: cap tables, employee equity plans, vesting schedules, and secondary transactions, with strong UK/European jurisdiction support. Now part of Carta's product line for the region.",
        "categories": ["Equity Management"],
    },
    {
        "name": "Eqvista",
        "url": "https://eqvista.com",
        "description": "Cap table management and 409A valuation software, positioned as a lower-cost alternative to Carta for early-stage startups needing basic equity tracking and annual valuations.",
        "categories": ["Equity Management"],
    },
    {
        "name": "Semper",
        "url": "https://www.semper.com",
        "description": "Equity management for private companies: cap table, employee equity plans, and 409A valuations, positioned as a newer entrant against Carta and Pulley.",
        "categories": ["Equity Management"],
    },
    # FP&A additions
    {
        "name": "Jirav",
        "url": "https://www.jirav.com",
        "description": "FP&A and workforce planning for mid-market companies, connecting HR and financial data for headcount planning, budgeting, and management reporting with the people-cost model as the anchor.",
        "categories": ["FP&A"],
    },
    {
        "name": "Vena",
        "url": "https://www.venasolutions.com",
        "description": "Excel-native FP&A: a governed data model and ERP integrations sit behind the spreadsheet, consolidating actuals, budgets, and forecasts without asking finance to give up Excel.",
        "categories": ["FP&A"],
    },
    {
        "name": "Board",
        "url": "https://www.board.com",
        "description": "Enterprise planning and business intelligence covering FP&A, sales planning, and supply chain in one model. Stronger on multi-dimensional modeling and consolidation than most single-purpose FP&A tools.",
        "categories": ["FP&A"],
    },
    {
        "name": "Digits",
        "url": "https://www.digits.com",
        "description": "AI-powered financial analytics that connects to accounting systems and automatically surfaces variance analysis, spend anomalies, and trends, cutting the manual work of month-end reporting and QBR prep.",
        "categories": ["FP&A"],
    },
    {
        "name": "Farseer",
        "url": "https://www.farseer.io",
        "description": "FP&A and integrated business planning covering budgeting, forecasting, consolidation, and management reporting, with particular strength in Central/Eastern European markets and multi-entity, multi-currency planning.",
        "categories": ["FP&A"],
    },
    {
        "name": "Phocas",
        "url": "https://www.phocassoftware.com",
        "description": "BI and FP&A built for distribution, manufacturing, and retail: layers analytics on top of ERP data for industry-specific reporting without custom SQL.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Workday Adaptive Planning",
        "url": "https://www.workday.com/en-us/products/adaptive-planning/",
        "description": "Enterprise FP&A and one of the market leaders alongside Anaplan and Planful. Covers budgeting, forecasting, workforce planning, and reporting, with native Workday HCM integration for connected headcount and financial planning.",
        "categories": ["FP&A"],
    },
    # Headcount Planning
    {
        "name": "ChartHop",
        "url": "https://www.charthop.com",
        "description": "Org management and headcount planning: visualizes the org chart, models headcount scenarios, and connects HR and finance data so CFOs and People leaders share one source of truth on open reqs and budget.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Doublefin",
        "url": "https://www.doublefin.com",
        "description": "Headcount planning and workforce intelligence built for the finance-HR handoff: tracks budget vs. actuals on headcount, manages offer approvals, and models hiring scenarios against plan.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Orgvue",
        "url": "https://www.orgvue.com",
        "description": "Organizational design and workforce planning: models org structures, spans of control, and headcount scenarios to support transformation planning and ongoing workforce optimization.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Teamohana",
        "url": "https://www.teamohana.com",
        "description": "Headcount management: a single source of truth for open reqs, planned hires, and budget vs. actuals, connecting HRIS and ATS data to the finance model.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Knoetic",
        "url": "https://www.knoetic.com",
        "description": "People analytics for CHROs and CFOs: combines HRIS data with a peer network for benchmarking headcount, attrition, and compensation, giving finance and people teams shared context for headcount decisions.",
        "categories": ["Headcount Planning"],
    },
    # ERP additions
    {
        "name": "Pennylane",
        "url": "https://www.pennylane.com",
        "description": "French all-in-one accounting and finance platform: ERP-level bookkeeping, invoicing, and cash management in one product, built for European companies with multi-currency support and accountant collaboration.",
        "categories": ["ERP"],
    },
    {
        "name": "Puzzle",
        "url": "https://puzzle.io",
        "description": "Modern accounting software built for startups, with a general ledger designed for accrual accounting from day one — positioned against QuickBooks for early-stage companies that want real accounting rather than bookkeeping.",
        "categories": ["ERP"],
    },
    {
        "name": "Exact",
        "url": "https://www.exact.com",
        "description": "Netherlands-based ERP and accounting platform dominant in Benelux. Covers financials, inventory, project accounting, and payroll, with multi-currency and multi-entity support for European mid-market companies.",
        "categories": ["ERP"],
    },
    {
        "name": "FreshBooks",
        "url": "https://www.freshbooks.com",
        "description": "SMB accounting and invoicing software covering time tracking, invoicing, expenses, and basic reporting for small businesses and freelancers — below the typical CFO-directory audience but widely used at the earliest stage.",
        "categories": ["ERP"],
    },
    # BI & Analytics
    {
        "name": "Mode (acquired by ThoughtSpot)",
        "url": "https://mode.com",
        "description": "SQL-based analytics for ad-hoc analysis, automated reports, and sharing insights across the business, with Python and R notebook support for deeper analysis.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Tableau (acquired by Salesforce)",
        "url": "https://www.tableau.com",
        "description": "Enterprise data visualization and BI, part of Salesforce. Used to build executive dashboards, custom financial reports, and interactive visualizations from virtually any data source.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Looker (acquired by Google)",
        "url": "https://www.looker.com",
        "description": "Enterprise BI with a governed semantic layer (LookML), part of Google Cloud. Built for teams that want consistent, reusable metrics and finance dashboards on top of a data warehouse.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Snowflake",
        "url": "https://www.snowflake.com",
        "description": "Cloud data warehouse — not a CFO tool directly, but a major infrastructure cost line CFOs own and the foundational layer many financial reporting and FP&A tools are built on top of.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Coefficient",
        "url": "https://coefficient.io",
        "description": "Live data connector for Google Sheets and Excel, pulling real-time data from CRMs, ERPs, and billing systems directly into spreadsheets, automating the manual export-import cycle for reporting.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Sisense",
        "url": "https://www.sisense.com",
        "description": "Embedded analytics and BI, primarily used by product teams embedding analytics into SaaS applications, but also deployed by finance teams for custom operational dashboards.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Databricks",
        "url": "https://www.databricks.com",
        "description": "Data lakehouse for large-scale data engineering, ML, and analytics — infrastructure CFOs increasingly own as a significant cost center, underpinning financial data pipelines at scale.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Grow",
        "url": "https://www.grow.com",
        "description": "No-code BI and dashboards for SMBs, connecting to 150+ data sources for executive-level dashboards without SQL — for finance teams that need reporting without a data engineer.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Equals",
        "url": "https://equals.com",
        "description": "Spreadsheet-based analytics with live data connections: the familiarity of a spreadsheet with direct SQL access to the data warehouse, for finance and ops teams who think in spreadsheets but need database-scale data.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Hex",
        "url": "https://hex.tech",
        "description": "Collaborative data notebook combining SQL, Python, and no-code visuals in one shareable workspace, used by finance and data teams for exploratory analysis and automated reporting.",
        "categories": ["BI/Analytics"],
    },
    {
        "name": "Omni",
        "url": "https://omni.co",
        "description": "BI platform combining a governed semantic layer with ad-hoc spreadsheet-style exploration, letting finance teams self-serve on data without waiting on the data team while keeping metrics consistent org-wide.",
        "categories": ["BI/Analytics"],
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed the CFO Toolbox with curated vendors.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    # Safe to run against a brand-new DB (first-time seed), so a missing
    # file at the resolved path isn't treated as a wrong-path error.
    args.db = resolve_db_path(args.db, allow_missing=True)

    lib = Library(args.db)
    added = updated = skipped = 0
    try:
        # Normalized comparison (not exact string) so a trailing-slash/www/http
        # variant of an already-seeded URL is recognized as the same tool rather
        # than tripping Library.add_tool's own duplicate check below.
        existing_by_url = {
            normalize_url(r["url"]): r
            for r in lib.conn.execute("SELECT id, name, description, advisor, url FROM tools").fetchall()
        }
        for t in TOOLS:
            existing = existing_by_url.get(normalize_url(t["url"]))
            if existing:
                if t.get("advisor") and not existing["advisor"]:
                    lib.conn.execute("UPDATE tools SET advisor=1 WHERE id=?", (existing["id"],))
                    lib.conn.commit()
                    print(f"  UPDATED advisor flag: {t['name']}")
                # description is deliberately NOT synced here (2026-08 incident —
                # see CLAUDE.md and webapp/app.py's _seed_toolbox() docstring): a
                # re-run of this script used to silently revert an AI-regenerated
                # or hand-edited description back to this file's seed blurb.
                # name-only from here on, since nothing ever AI-drafts a name.
                if existing["name"] != t["name"]:
                    lib.conn.execute("UPDATE tools SET name=? WHERE id=?", (t["name"], existing["id"]))
                    lib.conn.commit()
                    print(f"  UPDATED name: {t['name']}")
                    updated += 1
                else:
                    print(f"  SKIP  {t['name']}")
                skipped += 1
                continue
            try:
                tool_id = lib.add_tool(
                    name=t["name"],
                    description=t["description"],
                    url=t["url"],
                    categories=t["categories"],
                    approved=1,
                    advisor=int(t.get("advisor", False)),
                    source="script",
                )
            except DuplicateURLError as e:
                # Defense in depth — the existing_by_url lookup above should already
                # catch this, so this only fires if two TOOLS entries themselves
                # share a normalized URL. Skip rather than crash the whole run.
                print(f"  SKIP  {t['name']} — URL collides with existing {e.entry_id} ({e.name!r})")
                continue
            print(f"  ADDED {t['name']} (id={tool_id})")
            added += 1
    finally:
        lib.close()

    print(f"\n{added} added, {updated} updated, {skipped - updated} unchanged.")


if __name__ == "__main__":
    main()
