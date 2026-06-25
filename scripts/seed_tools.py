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
        "categories": ["FP&A"],
    },
    {
        "name": "Campfire",
        "url": "https://campfire.ai",
        "description": "AI-native ERP built for high-growth finance teams. Combines general ledger, revenue automation, close management, and reporting so lean teams can scale without adding headcount.",
        "categories": ["ERP"],
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
        "description": "AI agents for corporate finance teams. Connects to your ERP, CRM, and data warehouse to generate reports, forecasts, and variance analysis through natural language—no SQL required.",
        "categories": ["AI Agents"],
    },
    {
        "name": "Drivetrain",
        "url": "https://www.drivetrain.ai",
        "description": "AI-native FP&A and business planning platform. Supports multi-dimensional modeling, scenario analysis, and KPI reporting with 800+ integrations and a familiar spreadsheet-like interface.",
        "categories": ["FP&A"],
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
        "categories": ["Revenue Recognition"],
    },
    {
        "name": "HubiFi",
        "url": "https://www.hubifi.com",
        "description": "Automated revenue recognition and reconciliation platform for high-volume businesses. Handles ASC 606 compliance, continuous close, and real-time revenue reporting across complex billing models.",
        "categories": ["Revenue Recognition"],
    },
    {
        "name": "Runway",
        "url": "https://runway.com",
        "description": "FP&A platform built for high-growth teams. Connects 750+ data sources for financial modeling, forecasting, and reporting with human-readable formulas and real-time actuals sync.",
        "categories": ["FP&A"],
    },
    {
        "name": "Lumera",
        "url": "https://www.lumerahq.com",
        "description": "Governed AI infrastructure for finance teams. Lets controllers and FP&A teams build automations, agents, and scripts for close workflows — reconciliations, flux, journal entries — with full audit trails.",
        "categories": ["FP&A"],
    },
    {
        "name": "Planful",
        "url": "https://planful.com",
        "description": "End-to-end financial performance management platform covering planning, budgeting, forecasting, close, consolidation, and reporting. Built for the Office of the CFO at mid-market to enterprise scale.",
        "categories": ["FP&A"],
    },
    {
        "name": "Aleph",
        "url": "https://www.getaleph.com",
        "description": "AI-native FP&A platform that works alongside your existing spreadsheets. Connects 150+ data sources for financial modeling, budget planning, reporting, and close — with a familiar spreadsheet interface.",
        "categories": ["FP&A"],
    },
    {
        "name": "Pigment",
        "url": "https://www.pigment.com",
        "description": "Enterprise AI business planning platform for finance, sales, and HR. Covers FP&A, headcount planning, and sales performance management in a single connected model with real-time scenario analysis.",
        "categories": ["FP&A"],
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
        "categories": ["Contract Management"],
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
        "categories": ["Spend Management", "Billing"],
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
        "categories": ["FP&A"],
    },
    {
        "name": "Rillet",
        "url": "https://www.rillet.com",
        "description": "AI-native ERP and general ledger built for SaaS. Automates month-end close, revenue recognition, and reporting — purpose-built for subscription and usage-based businesses.",
        "categories": ["ERP"],
        "advisor": True,
    },
    {
        "name": "GoClose",
        "url": "https://www.goclose.com",
        "description": "Financial close management platform that centralizes close checklists, reconciliations, and task assignments. Gives controllers real-time visibility into close status and bottlenecks.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Lumos",
        "url": "https://www.lumos.ai",
        "description": "AI-native accounts receivable and collections platform. Automates dunning workflows, payment follow-ups, and dispute resolution to accelerate cash collection and reduce DSO.",
        "categories": ["Collections"],
        "advisor": True,
    },
    {
        "name": "Chargebee",
        "url": "https://www.chargebee.com",
        "description": "Subscription management and recurring billing platform for SaaS and subscription businesses. Handles flexible pricing models, dunning, revenue recognition, and integrates with major payment gateways and ERPs.",
        "categories": ["Billing"],
    },
    {
        "name": "Recurly",
        "url": "https://recurly.com",
        "description": "Subscription billing platform with flexible pricing, dunning management, and revenue recognition automation. Helps reduce churn and optimize recurring revenue for subscription businesses.",
        "categories": ["Billing"],
    },
    {
        "name": "HighRadius",
        "url": "https://www.highradius.com",
        "description": "Enterprise AI-powered order-to-cash automation. Covers cash application, credit risk management, collections, and deductions — purpose-built for mid-market and enterprise finance teams.",
        "categories": ["Collections"],
    },
    {
        "name": "Billtrust",
        "url": "https://www.billtrust.com",
        "description": "B2B accounts receivable automation platform covering invoice delivery, payment portals, cash application, and collections. Accelerates cash flow and reduces manual AR work for mid-market and enterprise businesses.",
        "categories": ["Collections"],
    },
    {
        "name": "Versapay",
        "url": "https://www.versapay.com",
        "description": "Collaborative accounts receivable platform that connects suppliers and buyers on a shared network for invoice resolution, payment, and cash application. Reduces DSO through buyer-seller collaboration.",
        "categories": ["Collections"],
    },
    {
        "name": "BlackLine",
        "url": "https://www.blackline.com",
        "description": "Financial close and accounting automation platform for mid-market to enterprise. Centralizes account reconciliations, transaction matching, journal entries, and intercompany accounting with full audit trails.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Spendesk",
        "url": "https://www.spendesk.com",
        "description": "Spend management platform combining corporate cards, invoice processing, and expense reimbursements with real-time visibility, approval workflows, and accounting integrations.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Sage Intacct",
        "url": "https://www.sageintacct.com",
        "description": "Cloud ERP and accounting platform for mid-market businesses. Provides multi-entity consolidation, project accounting, revenue recognition, and financial reporting with deep integrations across the finance stack.",
        "categories": ["ERP"],
    },
    {
        "name": "NetSuite",
        "url": "https://www.netsuite.com",
        "description": "Oracle's cloud ERP platform and the dominant mid-market to enterprise accounting and operations system. Covers financial management, inventory, CRM, and ecommerce in a unified suite.",
        "categories": ["ERP"],
    },
    {
        "name": "Vareto",
        "url": "https://www.vareto.com",
        "description": "Modern FP&A platform designed for high-growth companies. Focuses on headcount planning, business partnering, and real-time financial reporting — connecting finance to the rest of the business.",
        "categories": ["FP&A"],
    },
    {
        "name": "Brex",
        "url": "https://www.brex.com",
        "description": "Corporate cards, expense management, and bill pay platform for growing companies. Provides high credit limits, automated expense categorization, real-time spend controls, and integrations with major ERPs.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Airwallex",
        "url": "https://www.airwallex.com",
        "description": "Global payments and financial infrastructure platform. Provides multi-currency business accounts, FX at interbank rates, international AP, and card issuing — built for companies operating across borders.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Carta",
        "url": "https://carta.com",
        "description": "Equity management platform for private companies. Manages cap tables, 409A valuations, employee equity grants, and investor reporting — the most widely used cap table tool in venture-backed startups.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "Pulley",
        "url": "https://www.pulley.com",
        "description": "Modern cap table management platform built for startups. Handles equity grants, scenario modeling, and 409A valuations with a cleaner interface than legacy tools—and faster 409A turnaround.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "Ledgy",
        "url": "https://www.ledgy.com",
        "description": "Equity management platform for European and global startups. Covers cap table, employee equity plans, vesting schedules, and investor relations with strong multi-jurisdiction support.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "Fidelity Private Shares",
        "url": "https://www.fidelityprivateshares.com",
        "description": "Fidelity's equity management solution for private companies. Cap table management, 409A valuations, and equity plan administration backed by Fidelity's brokerage infrastructure.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "CaptivateIQ",
        "url": "https://www.captivateiq.com",
        "description": "Sales commission management platform. Automates complex commission calculations, handles plan design, and gives reps real-time earnings visibility — reducing finance time spent on spreadsheet reconciliation.",
        "categories": ["Commission Calculations"],
    },
    {
        "name": "Everstage",
        "url": "https://www.everstage.com",
        "description": "No-code sales commission software. Automates incentive calculations, provides real-time rep dashboards, and integrates with CRMs and ERPs — built for ops teams that don't want to manage commission logic in code.",
        "categories": ["Commission Calculations"],
    },
    {
        "name": "QuotaPath",
        "url": "https://www.quotapath.com",
        "description": "Commission tracking and compensation management platform. Simplifies complex commission plans, automates calculations, and gives sales teams earnings transparency without the back-and-forth with finance.",
        "categories": ["Commission Calculations"],
    },
    {
        "name": "Xactly",
        "url": "https://www.xactlycorp.com",
        "description": "Enterprise incentive compensation management platform. Handles complex commission plan design, automated calculations, and analytics for large sales organizations with multi-tier structures.",
        "categories": ["Commission Calculations"],
    },
    {
        "name": "Cube",
        "url": "https://www.cubesoftware.com",
        "description": "FP&A platform that integrates with Excel and Google Sheets. Centralizes financial data, automates consolidation and reporting, and enables collaborative planning without replacing existing spreadsheet workflows.",
        "categories": ["FP&A"],
    },
    {
        "name": "Pave",
        "url": "https://www.pave.com",
        "description": "Compensation management and benchmarking platform. Real-time salary, equity, and total compensation data to help companies build and communicate competitive comp structures—with direct integrations into HRIS systems.",
        "categories": ["Compensation Data"],
    },
    {
        "name": "Culpepper",
        "url": "https://www.culpeppercomp.com",
        "description": "Compensation survey and benchmarking data provider. Covers base pay, bonuses, and long-term incentives across industries with robust segmentation by company size, geography, and sector.",
        "categories": ["Compensation Data"],
    },
    {
        "name": "Radford (Aon)",
        "url": "https://radford.aon.com",
        "description": "Aon's compensation benchmarking surveys and data. One of the most widely used comp survey providers—covers tech, life sciences, and other sectors with deep cut of the data by role, level, and geography.",
        "categories": ["Compensation Data"],
    },
    # Treasury / Cash Management
    {
        "name": "Agicap",
        "url": "https://agicap.com",
        "description": "European cash flow management platform. Aggregates bank accounts, ERPs, and invoices into a real-time cash position and rolling forecast. Strong multi-currency support for companies operating across Europe.",
        "categories": ["Treasury"],
    },
    {
        "name": "Trovata",
        "url": "https://trovata.io",
        "description": "Treasury and cash management platform that connects directly to bank APIs. Automates cash reporting, balance aggregation across accounts and currencies, and rolling cash forecasts—eliminating manual bank file imports.",
        "categories": ["Treasury"],
    },
    {
        "name": "Tresio",
        "url": "https://www.tresio.io",
        "description": "Cash flow forecasting and liquidity planning platform for SMBs and scale-ups. Pulls from bank accounts and ERPs to deliver rolling forecasts and scenario analysis.",
        "categories": ["Treasury"],
    },
    {
        "name": "Trezy",
        "url": "https://trezy.com",
        "description": "European cash flow management and forecasting platform. Connects to bank accounts across multiple currencies for real-time cash visibility and multi-entity consolidation.",
        "categories": ["Treasury"],
    },
    {
        "name": "Sibill",
        "url": "https://www.sibill.com",
        "description": "European cash flow forecasting platform. Connects to bank accounts and ERPs across multiple currencies to deliver rolling cash forecasts, payment planning, and liquidity alerts.",
        "categories": ["Treasury"],
    },
    {
        "name": "Tidely",
        "url": "https://www.tidely.com",
        "description": "Cash flow planning and liquidity management platform with strong European bank connectivity. Provides rolling cash forecasts, multi-currency support, and scenario modeling for SMBs operating across EMEA.",
        "categories": ["Treasury"],
    },
    {
        "name": "Viably",
        "url": "https://www.viably.com",
        "description": "Working capital and cash flow platform for e-commerce businesses. Combines cash flow forecasting with embedded financing to help companies manage inventory cycles and seasonal cash gaps.",
        "categories": ["Treasury"],
    },
    {
        "name": "Nilus",
        "url": "https://nilus.com",
        "description": "Modern treasury management platform for mid-market companies. Provides real-time cash visibility across bank accounts and entities, liquidity forecasting, and payment operations—a lighter-weight alternative to enterprise TMS platforms.",
        "categories": ["Treasury"],
    },
    # Sales Tax additions
    {
        "name": "Avalara",
        "url": "https://www.avalara.com",
        "description": "The market-leading sales tax compliance platform. Monitors nexus across all US states and 100+ countries, calculates tax in real time, and automates filing and remittance. Integrates with virtually every ERP and billing system.",
        "categories": ["Sales Tax"],
    },
    {
        "name": "Zamp",
        "url": "https://www.zamp.com",
        "description": "Fully managed sales tax service. Handles registration, collection, filing, and remittance on your behalf—so your finance team doesn't touch the workflow. Good fit if you want to hand the whole problem off.",
        "categories": ["Sales Tax"],
    },
    # Financial Reporting
    {
        "name": "Liveflow",
        "url": "https://www.liveflow.io",
        "description": "Financial reporting automation that pulls live data from QuickBooks and Xero directly into Google Sheets. Finance teams use it to automate P&L, cash flow, and board reporting without leaving spreadsheets.",
        "categories": ["Financial Reporting"],
    },
    {
        "name": "EMAsphere",
        "url": "https://www.emasphere.com",
        "description": "Automated financial and management reporting platform. Consolidates data from ERPs to produce board-ready reports, dashboards, and multi-entity consolidations. Widely used across Europe.",
        "categories": ["Financial Reporting"],
    },
    {
        "name": "Klipfolio",
        "url": "https://www.klipfolio.com",
        "description": "KPI dashboard and business metrics platform. Connects to 100+ data sources for real-time executive dashboards. Finance teams use it for board-level reporting and operational metrics tracking.",
        "categories": ["Financial Reporting"],
    },
    # Financial Close additions
    {
        "name": "FloQast",
        "url": "https://floqast.com",
        "description": "Close management and accounting automation platform built for controllers. Centralizes close checklists, reconciliations, flux analysis, and audit prep. One of the most widely deployed in mid-market finance teams.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Silverfin",
        "url": "https://www.silverfin.com",
        "description": "Cloud-based financial reporting and close platform for accountants and finance teams. Automates workpapers, reconciliations, and management reporting—with strong multi-entity consolidation support across European jurisdictions.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Gappify",
        "url": "https://gappify.com",
        "description": "Account reconciliation and close automation platform. Automates accruals, reconciliations, and close task management with full audit trails. Competes with FloQast and BlackLine for controller-owned workflows.",
        "categories": ["Financial Close"],
    },
    {
        "name": "Numeral",
        "url": "https://www.numeral.io",
        "description": "Modern financial close and accounting automation platform for high-growth companies. Focuses on automating the month-end process: reconciliations, journal entries, and close reporting in one system.",
        "categories": ["Financial Close"],
    },
    # Cloud/IT Spend
    {
        "name": "Apptio",
        "url": "https://www.apptio.com",
        "description": "Technology business management and cloud cost optimization platform (IBM). Models total IT spend against business value and cost drivers—used by CFOs and CIOs to understand the true cost of technology at scale.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Finout",
        "url": "https://www.finout.io",
        "description": "Cloud cost visibility and FinOps platform. Maps AWS, GCP, and Azure spend to business units, products, and customers to track unit economics like cost-per-customer alongside engineering spend.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "nOps",
        "url": "https://www.nops.io",
        "description": "AWS cost optimization platform. Automates Reserved Instance and Savings Plan purchases, surfaces idle and underutilized resources, and continuously tracks commitment coverage to reduce cloud waste.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Ternary",
        "url": "https://ternary.app",
        "description": "Cloud cost management platform with particularly deep GCP support, built by ex-Google engineers. Covers multi-cloud cost allocation, anomaly detection, and savings recommendations across AWS, GCP, and Azure.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Vantage",
        "url": "https://www.vantage.sh",
        "description": "Cloud cost observability platform. Provides unit cost analysis, Kubernetes cost allocation, and savings recommendations across AWS, GCP, and Azure—with a developer-friendly interface finance teams can actually use.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Anodot",
        "url": "https://www.anodot.com",
        "description": "Cloud cost monitoring and anomaly detection platform. Uses AI to surface unexpected cost spikes and efficiency opportunities across multi-cloud environments before they show up in the monthly bill.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "CloudZero",
        "url": "https://www.cloudzero.com",
        "description": "Cloud cost intelligence platform focused on unit economics. Maps AWS, Azure, and GCP spend to products, features, and customers so engineering and finance teams can track cost-per-customer and cost-per-feature alongside cloud bills.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Torii",
        "url": "https://www.torii.io",
        "description": "SaaS management platform. Discovers all software in use across the organization, tracks spend and license utilization, and manages renewals and offboarding workflows to reduce shadow IT and wasted seats.",
        "categories": ["Cloud/IT Spend"],
    },
    {
        "name": "Zluri",
        "url": "https://www.zluri.com",
        "description": "SaaS management and identity governance platform. Tracks software usage and license utilization across the stack, automates provisioning and deprovisioning, and surfaces savings opportunities from unused seats.",
        "categories": ["Cloud/IT Spend"],
    },
    # Procurement additions
    {
        "name": "Vertice",
        "url": "https://www.vertice.one",
        "description": "SaaS and cloud procurement platform. Benchmarks software pricing against market rates, manages renewal workflows, and negotiates on your behalf—similar to Vendr but with strong pricing intelligence.",
        "categories": ["Procurement"],
    },
    {
        "name": "Zylo",
        "url": "https://zylo.com",
        "description": "Enterprise SaaS management platform. Discovers shadow IT, tracks license utilization and renewal dates, and benchmarks software costs against market data. The enterprise-tier option for companies managing hundreds of software contracts.",
        "categories": ["Cloud/IT Spend"],
    },
    # Contract Management additions
    {
        "name": "Dealhub",
        "url": "https://dealhub.io",
        "description": "CPQ and contract lifecycle management platform. Connects guided selling, quoting, eSign, and contract management in one revenue workflow—finance teams use it to govern deal terms and track contract obligations.",
        "categories": ["Contract Management"],
    },
    {
        "name": "Sirion",
        "url": "https://www.sirionlabs.com",
        "description": "Enterprise contract lifecycle management powered by AI. Extracts obligations and key terms from executed contracts, tracks renewal and compliance milestones, and provides a searchable contract intelligence layer for legal and finance.",
        "categories": ["Contract Management"],
    },
    # RevOps
    {
        "name": "Clari",
        "url": "https://www.clari.com",
        "description": "Revenue operations platform. Provides pipeline inspection, forecast accuracy scoring, and deal intelligence from CRM activity data. CFOs use it to improve revenue forecast confidence and board reporting on ARR.",
        "categories": ["RevOps"],
    },
    {
        "name": "Gong",
        "url": "https://www.gong.io",
        "description": "Revenue intelligence platform. Records and analyzes sales calls, surfaces deal risks and coaching opportunities, and tracks pipeline health. CFO owns the budget and uses it for revenue visibility and sales productivity measurement.",
        "categories": ["RevOps"],
    },
    {
        "name": "Weflow",
        "url": "https://www.weflow.io",
        "description": "Salesforce productivity and pipeline management tool. Helps reps update CRM data faster and gives managers real-time pipeline visibility—reducing the noise between what's in Salesforce and what's actually happening in deals.",
        "categories": ["RevOps"],
    },
    {
        "name": "BoostUp",
        "url": "https://boostup.ai",
        "description": "Revenue forecasting and pipeline analytics platform. Combines AI-driven deal scoring, rep activity signals, and forecast roll-ups to improve forecast accuracy and give finance teams a more reliable revenue number.",
        "categories": ["RevOps"],
    },
    {
        "name": "Revsure",
        "url": "https://www.revsure.ai",
        "description": "Pipeline analytics and revenue forecasting AI. Uses historical CRM data to predict deal outcomes, surface at-risk pipeline, and improve forecast accuracy—built for finance and RevOps teams who need a reliable revenue signal.",
        "categories": ["RevOps"],
    },
    {
        "name": "GrowBlocks",
        "url": "https://www.growblocks.com",
        "description": "Revenue planning and go-to-market analytics platform. Models pipeline coverage, headcount, and revenue targets together in a connected plan—bridging the gap between the FP&A model and what's actually happening in the business.",
        "categories": ["RevOps"],
    },
    {
        "name": "Sightfull",
        "url": "https://www.sightfull.com",
        "description": "Revenue analytics platform for SaaS CFOs and RevOps teams. Automates ARR waterfall, NRR, churn, and cohort reporting from CRM and billing data—no SQL or data team required.",
        "categories": ["RevOps"],
    },
    # Billing additions
    {
        "name": "m3ter",
        "url": "https://www.m3ter.com",
        "description": "Usage-based billing infrastructure for SaaS and cloud companies. Ingests product usage events and powers any pricing model—seat, consumption, hybrid—with real-time metering and invoice generation at scale.",
        "categories": ["Billing"],
    },
    {
        "name": "Amberflo",
        "url": "https://www.amberflo.io",
        "description": "Cloud metering and usage-based billing platform. Handles real-time usage ingestion, flexible pricing plan configuration, and customer-facing usage dashboards—built for teams moving to consumption pricing.",
        "categories": ["Billing"],
    },
    {
        "name": "Togai",
        "url": "https://www.togai.com",
        "description": "Flexible pricing and billing infrastructure. Handles usage metering, pricing experiments, and invoice generation for SaaS companies moving toward consumption or hybrid pricing models.",
        "categories": ["Billing"],
    },
    {
        "name": "DigitalRoute",
        "url": "https://www.digitalroute.com",
        "description": "Enterprise usage data management and monetization platform. Processes high-volume usage events for telco, SaaS, and IoT billing at scale. Strong choice for complex, high-throughput usage-based revenue models.",
        "categories": ["Billing"],
    },
    {
        "name": "Zuora",
        "url": "https://www.zuora.com",
        "description": "Subscription management and recurring billing platform. Handles flexible pricing, recurring invoicing, revenue recognition (ASC 606/IFRS 15), and SaaS metrics reporting. One of the most widely deployed billing platforms at mid-market to enterprise.",
        "categories": ["Billing"],
    },
    {
        "name": "Paddle",
        "url": "https://www.paddle.com",
        "description": "Merchant of record for SaaS companies. Handles payments, subscription billing, global sales tax and VAT compliance, and fraud prevention—so you don't have to register in every jurisdiction. Strong for SaaS companies selling internationally.",
        "categories": ["Billing", "Sales Tax"],
    },
    # Collections additions
    {
        "name": "Tesorio",
        "url": "https://www.tesorio.com",
        "description": "Cash flow and AR management platform. Automates collections outreach, forecasts cash from open receivables, and gives finance teams real-time visibility into what's owed and when—reducing DSO without adding headcount.",
        "categories": ["Collections"],
    },
    {
        "name": "Kolleno",
        "url": "https://www.kolleno.com",
        "description": "AR and collections automation platform. Automates dunning sequences, cash application, and dispute management for mid-market businesses. A leaner alternative to HighRadius and Billtrust for teams that don't need enterprise-scale complexity.",
        "categories": ["Collections"],
    },
    # Spend Management additions
    {
        "name": "Payhawk",
        "url": "https://www.payhawk.com",
        "description": "Corporate cards, expense management, and AP automation platform with strong European coverage. Handles multi-currency spend, VAT compliance, and ERP integrations across EMEA—a leading option for European finance teams.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Pleo",
        "url": "https://www.pleo.io",
        "description": "European spend management and corporate cards platform. Smart cards, out-of-pocket reimbursements, and invoice management in one product—built for teams across multiple European countries with multi-currency support.",
        "categories": ["Spend Management"],
    },
    {
        "name": "Mesh Payments",
        "url": "https://www.meshpayments.com",
        "description": "Global corporate payment and spend management platform. Provides virtual and physical cards, AP automation, and cross-border payment capabilities with multi-currency support for companies operating internationally.",
        "categories": ["Spend Management"],
    },
    {
        "name": "PayEm",
        "url": "https://www.payem.co",
        "description": "Global spend and procurement management platform. Handles purchase requests, multi-currency vendor payments, and corporate cards in a single workflow—built for finance teams managing international operations.",
        "categories": ["Spend Management"],
    },
    # Cap Table Management additions
    {
        "name": "Capdesk",
        "url": "https://www.capdesk.com",
        "description": "European equity management platform. Manages cap tables, employee equity plans, vesting schedules, and secondary transactions—with strong support for UK and European jurisdictions and multi-currency equity modeling.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "Eqvista",
        "url": "https://eqvista.com",
        "description": "Cap table management and 409A valuation software. A lower-cost alternative to Carta aimed at early-stage startups that need basic equity tracking and annual valuations without the enterprise price tag.",
        "categories": ["Cap Table Management"],
    },
    {
        "name": "Semper",
        "url": "https://www.semper.com",
        "description": "Equity management platform for private companies. Covers cap table management, employee equity plans, and 409A valuations—a newer entrant competing with Carta and Pulley.",
        "categories": ["Cap Table Management"],
    },
    # FP&A additions
    {
        "name": "Jirav",
        "url": "https://www.jirav.com",
        "description": "FP&A and workforce planning platform for mid-market companies. Connects HR and financial data for headcount planning, budgeting, and management reporting—with a strong focus on the people-cost model.",
        "categories": ["FP&A"],
    },
    {
        "name": "Vena",
        "url": "https://www.venasolutions.com",
        "description": "Excel-native FP&A platform. Works inside Excel with a governed data model and ERP integrations behind it—consolidating actuals, budgets, and forecasts without replacing the spreadsheet workflows finance teams already know.",
        "categories": ["FP&A"],
    },
    {
        "name": "Board",
        "url": "https://www.board.com",
        "description": "Enterprise planning and business intelligence platform. Covers FP&A, sales planning, and supply chain in a unified model. Stronger on multi-dimensional modeling and consolidation than most pure FP&A tools—built for complex, multi-entity organizations.",
        "categories": ["FP&A"],
    },
    {
        "name": "Digits",
        "url": "https://www.digits.com",
        "description": "AI-powered financial analytics platform. Connects to accounting systems and automatically surfaces variance analysis, spend anomalies, and trends—reducing the manual work of month-end reporting and QBR prep.",
        "categories": ["FP&A"],
    },
    {
        "name": "Farseer",
        "url": "https://www.farseer.io",
        "description": "FP&A and integrated business planning platform. Covers budgeting, forecasting, consolidation, and management reporting—with particular strength in Central and Eastern European markets and multi-entity, multi-currency planning.",
        "categories": ["FP&A"],
    },
    {
        "name": "Phocas",
        "url": "https://www.phocassoftware.com",
        "description": "Business intelligence and FP&A platform built for distribution, manufacturing, and retail. Layers analytics on top of ERP data to give operations and finance teams industry-specific reporting without custom SQL.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Workday Adaptive Planning",
        "url": "https://www.workday.com/en-us/products/adaptive-planning/",
        "description": "Enterprise FP&A platform and one of the market leaders alongside Anaplan and Planful. Covers budgeting, forecasting, workforce planning, and reporting with strong Workday HCM integration for connected headcount and financial planning.",
        "categories": ["FP&A"],
    },
    # Headcount Planning
    {
        "name": "ChartHop",
        "url": "https://www.charthop.com",
        "description": "Org management and headcount planning platform. Visualizes the org chart, models headcount scenarios, and connects HR and finance data so CFOs and People leaders share a single source of truth on open reqs and budget.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Doublefin",
        "url": "https://www.doublefin.com",
        "description": "Headcount planning and workforce intelligence platform. Purpose-built for the finance–HR handoff: tracks budget vs. actuals on headcount, manages offer approvals, and models hiring scenarios against plan.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Orgvue",
        "url": "https://www.orgvue.com",
        "description": "Organizational design and workforce planning platform. Models org structures, spans of control, and headcount scenarios to support transformation planning and ongoing workforce optimization.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Teamohana",
        "url": "https://www.teamohana.com",
        "description": "Headcount management platform. Provides a single source of truth for open reqs, planned hires, and budget vs. actuals—connecting HRIS and ATS data to the finance model so headcount plans stay current.",
        "categories": ["Headcount Planning"],
    },
    {
        "name": "Knoetic",
        "url": "https://www.knoetic.com",
        "description": "People analytics platform for CHROs and CFOs. Combines HRIS data with a peer network for benchmarking headcount, attrition, and compensation—giving finance and people teams data to make headcount decisions with context.",
        "categories": ["Headcount Planning"],
    },
    # ERP additions
    {
        "name": "Pennylane",
        "url": "https://www.pennylane.com",
        "description": "French all-in-one accounting and finance platform. Combines ERP-level bookkeeping, invoicing, and cash management in one product—built for European companies with strong multi-currency support and accountant collaboration.",
        "categories": ["ERP"],
    },
    {
        "name": "Puzzle",
        "url": "https://puzzle.io",
        "description": "Modern accounting software built for startups. A clean general ledger designed for accrual accounting from day one—positioned as the alternative to QuickBooks for early-stage companies that want real accounting, not bookkeeping.",
        "categories": ["ERP"],
    },
    {
        "name": "Exact",
        "url": "https://www.exact.com",
        "description": "Netherlands-based ERP and accounting platform dominant in Benelux. Covers financials, inventory, project accounting, and payroll—with strong multi-currency and multi-entity support for European mid-market companies.",
        "categories": ["ERP"],
    },
    {
        "name": "FreshBooks",
        "url": "https://www.freshbooks.com",
        "description": "SMB accounting and invoicing software. Handles time tracking, invoicing, expenses, and basic reporting for small businesses and freelancers. Below the typical CFO audience but widely used at the early stage.",
        "categories": ["ERP"],
    },
    # BI & Analytics
    {
        "name": "Mode",
        "url": "https://mode.com",
        "description": "SQL-based analytics and business intelligence platform. Finance and data teams use it for ad-hoc analysis, automated reports, and sharing insights across the business—with Python and R notebook support for more complex analysis.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Tableau",
        "url": "https://www.tableau.com",
        "description": "The most widely used enterprise data visualization and BI platform (Salesforce). Finance teams use it to build executive dashboards, custom financial reports, and interactive visualizations from virtually any data source.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Looker",
        "url": "https://www.looker.com",
        "description": "Enterprise BI platform with a governed semantic layer (LookML). Strong with data teams building consistent, reusable metrics and finance dashboards on top of data warehouses—now part of Google Cloud.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Snowflake",
        "url": "https://www.snowflake.com",
        "description": "Cloud data platform and data warehouse. Not a CFO tool directly, but a major infrastructure cost center CFOs own and a foundational layer that financial reporting and FP&A tools are increasingly built on top of.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Coefficient",
        "url": "https://coefficient.io",
        "description": "Live data connector for Google Sheets and Excel. Finance teams use it to pull real-time data from CRMs, ERPs, and billing systems directly into spreadsheets—automating the manual export-import cycle for reporting.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Sisense",
        "url": "https://www.sisense.com",
        "description": "Embedded analytics and BI platform. Primarily used by product teams to embed analytics into SaaS applications, but also deployed by finance teams for custom operational dashboards.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Databricks",
        "url": "https://www.databricks.com",
        "description": "Data lakehouse platform for large-scale data engineering, ML, and analytics. Infrastructure-level technology that CFOs increasingly own as a significant cost center and that underpins financial data pipelines at scale.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Grow",
        "url": "https://www.grow.com",
        "description": "No-code BI and dashboard platform for SMBs. Connects to 150+ data sources for executive-level dashboards without requiring SQL—a practical option for finance teams that need reporting without a data engineer.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Equals",
        "url": "https://equals.com",
        "description": "Spreadsheet-based analytics platform with live data connections. Combines the familiarity of a spreadsheet with direct SQL access to your data warehouse—built for finance and ops teams who think in spreadsheets but need database-scale data.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Hex",
        "url": "https://hex.tech",
        "description": "Collaborative data notebook and analytics platform. Combines SQL, Python, and no-code visuals in a single shareable workspace—used by finance and data teams for exploratory analysis and automated reporting.",
        "categories": ["BI & Analytics"],
    },
    {
        "name": "Omni",
        "url": "https://omni.co",
        "description": "Modern BI platform that combines a governed semantic layer with ad-hoc spreadsheet-style exploration. Lets finance teams self-serve on data without waiting for the data team, while keeping metrics consistent across the organization.",
        "categories": ["BI & Analytics"],
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
