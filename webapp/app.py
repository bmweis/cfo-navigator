#!/usr/bin/env python3
"""bmweis.com — public site + private CFO Navigator tools.

Public routes (no auth):
    GET  /                     Bio homepage
    GET  /thought-leadership   Podcasts, writing, interviews
    GET  /growth-engine-ratio  GER framework + calculator
    GET  /finops-ai-hackathon  AI hackathon playbook
    GET  /netsuite-mcp         Claude–NetSuite setup guide
    GET  /contact              Contact form
    POST /contact              Submit contact form
    GET  /login / POST /login  Password sign-in (sets a signed session cookie)
    GET  /logout               Clear the session
    GET  /static/{file}        Static assets (e.g. headshot)
    GET  /health               Health check

Private routes (require login cookie; API routes also accept a token):
    GET  /library              Search + browse saved articles
    GET  /feed                 RSS reader over the OPML subscription list
    GET  /read                 Article reader (Instapaper-style clean view)
    POST /ask                  FP&A Q&A
    POST /post                 Draft a LinkedIn post
    POST /feed/save            Save a feed item to the archive
    POST /save                 Capture a link (token auth — used by bookmarklet)
    GET  /api/search           JSON search API
    GET  /bookmarklet          One-click saver script
    GET  /admin/contacts       View contact form submissions
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import threading
import time
from datetime import datetime
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from linklib.db import Library
from linklib.pipeline import ingest_url
from linklib import backup

DB_PATH = os.environ.get("LINKLIB_DB", "library.db")
SAVE_TOKEN = os.environ.get("LINKLIB_SAVE_TOKEN", "")

TOOL_CATEGORIES = [
    "FP&A",
    "Headcount Planning",
    "Treasury",
    "Cash Flow Forecasting",
    "AI Agents",
    "ERP",
    "Cap Table Management",
    "Spend Management",
    "Financial Close",
    "Financial Reporting",
    "Revenue Recognition",
    "Billing",
    "Collections",
    "Sales Tax",
    "Commission Calculations",
    "Compensation Data",
    "Contract Management",
    "Procurement",
    "RevOps",
    "Cloud/IT Spend",
    "BI & Analytics",
]

CATEGORY_DESCRIPTIONS = {
    "FP&A": "Business-wide financial planning, budgeting, forecasting, and management reporting.",
    "Headcount Planning": "Standalone tools for planning and tracking headcount—open reqs, budget vs. actuals on people costs, and the finance–HR handoff.",
    "Treasury": "Treasury management systems, FX risk, global payments infrastructure, and corporate cash investment platforms.",
    "Cash Flow Forecasting": "Tools dedicated to predicting future cash positions and liquidity—connecting to bank feeds and ERPs to model inflows and outflows.",
    "AI Agents": "Finance-native AI agents that operate autonomously on finance workflows, purpose-built for finance teams.",
    "ERP": "Core accounting and enterprise resource planning—general ledger, system of record, and financial management.",
    "Cap Table Management": "Equity management for private companies—cap table tracking, 409A valuations, and employee equity plan administration.",
    "Spend Management": "Corporate cards, expense management, AP automation, and employee spend controls.",
    "Financial Close": "Standalone close management platforms—checklists, reconciliations, journal entries, flux analysis, and audit readiness.",
    "Financial Reporting": "Tools that produce and present the three core financial statements: income statement, balance sheet, and cash flow statement.",
    "Revenue Recognition": "Standalone ASC 606 / IFRS 15 revenue recognition platforms, purchasable independently of the billing system feeding them.",
    "Billing": "Subscription billing, usage-based billing, invoicing, and recurring payments infrastructure.",
    "Collections": "Accounts receivable management and collections automation—dunning, cash application, and DSO reduction.",
    "Sales Tax": "Sales tax, VAT, and GST compliance—nexus monitoring, real-time calculation, and filing.",
    "Commission Calculations": "Incentive compensation management—commission plan design, automated calculations, and rep-facing earnings dashboards.",
    "Compensation Data": "Compensation benchmarking surveys and data used to set and validate salary, equity, and total comp structures.",
    "Contract Management": "Contract lifecycle management—drafting, negotiation, approvals, eSign, obligation tracking, and renewals.",
    "Procurement": "Software and vendor procurement—purchasing workflows, price benchmarking, and renewal management.",
    "RevOps": "Revenue operations—pipeline management, revenue forecasting, and deal intelligence for finance and sales leaders.",
    "Cloud/IT Spend": "Cloud cost management and SaaS management—visibility into and control over cloud infrastructure spend and software license costs.",
    "BI & Analytics": "Business intelligence, data visualization, SQL analytics, and data infrastructure CFOs own or use for reporting.",
}

# coverage: "Private" | "Public" | "Both"
# pricing:  "free" (default) | "paid" | "freemium"  -> shows a $ badge
BENCHMARKS = [
    {
        "name": "ICONIQ Growth",
        "url": "https://iconiqcapital.com/growth/",
        "description": "ICONIQ's annual State of SaaS report. Top-tier portfolio, so keep that in mind when comparing—but the data and analysis are excellent.",
        "coverage": "Private",
    },
    {
        "name": "ICONIQ Compass",
        "url": "https://compass.iconiqgrowth.com/",
        "description": "Their interactive benchmarking tool. Lets you slice the data by ARR range, growth rate, and other filters so you're actually comparing against something relevant.",
        "coverage": "Private",
    },
    {
        "name": "HighAlpha (formerly OpenView)",
        "url": "https://www.highalpha.com/saas-benchmarks",
        "description": "Took over OpenView's annual SaaS benchmarks report. NRR, GRR, CAC payback, and the usual suspects for private SaaS companies.",
        "coverage": "Private",
    },
    {
        "name": "Benchmarkit",
        "url": "https://www.benchmarkit.ai/",
        "description": "Ray Rike's interactive benchmarking tool. Better segmentation than most—you can control who you're comparing against, which is the whole point.",
        "coverage": "Private",
    },
    {
        "name": "SaaStr Benchmarking",
        "url": "https://saastr.ai/startup-benchmarking",
        "description": "Startup benchmarking hub for early-stage SaaS—revenue, growth, and efficiency marks by stage, with Jason Lemkin's take on what 'good' actually looks like.",
        "coverage": "Private",
    },
    {
        "name": "OpexEngine",
        "url": "https://www.opexengine.com/",
        "description": "Private and public SaaS benchmarks across Rule of 40, unit economics, and operating metrics. One of the more comprehensive data sets out there.",
        "coverage": "Both",
        "pricing": "paid",
    },
    {
        "name": "Bessemer Venture Partners",
        "url": "https://www.bvp.com/atlas/state-of-the-cloud",
        "description": "State of the Cloud and the Good-Better-Best SaaS metrics framework. Widely cited—worth knowing what everyone else is measuring against.",
        "coverage": "Both",
    },
    {
        "name": "Clouded Judgement (Jamin Ball)",
        "url": "https://cloudedjudgement.substack.com/",
        "description": "Jamin Ball's weekly newsletter on public SaaS benchmarks and market trends. One of the best signals for tracking what's actually happening across cloud.",
        "coverage": "Public",
    },
    {
        "name": "Meritech Analytics",
        "url": "https://meritechanalytics.com/",
        "description": "Interactive public cloud benchmarks—growth, efficiency, and valuation multiples, updated in real time. Great for understanding where public comps are trading.",
        "coverage": "Public",
    },
    {
        "name": "PublicComps",
        "url": "https://www.publiccomps.com/",
        "description": "Public SaaS comps and operating metrics. Good filters by category and scale.",
        "coverage": "Public",
        "pricing": "freemium",
    },
    {
        "name": "Baremetrics Open Benchmarks",
        "url": "https://baremetrics.com/open-benchmarks",
        "description": "Real, anonymized metrics—MRR growth, churn, ARPU, LTV—aggregated from thousands of Baremetrics-tracked subscription businesses. Skews SMB, but it's actual data, not a survey.",
        "coverage": "Private",
    },
]
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PUBLIC_BASE = os.environ.get("LINKLIB_PUBLIC_BASE", "http://localhost:8000")

# --- Auth -------------------------------------------------------------------
# A single shared secret protects the private tools. LINKLIB_PASSWORD is the
# login password; it falls back to LINKLIB_SAVE_TOKEN so one secret works for
# both the login screen and the bookmarklet/token API. If neither is set, the
# private routes are open (convenient for local-only use).
AUTH_PASSWORD = os.environ.get("LINKLIB_PASSWORD") or SAVE_TOKEN
# Everyone signs in with a username. The host password (AUTH_PASSWORD) is the
# lockout-proof break-glass admin — it works with this reserved username (default
# "admin", overridable) rather than a blank one.
ADMIN_USERNAME = (os.environ.get("LINKLIB_ADMIN_USERNAME") or "admin").strip().lower()
SECRET_KEY = os.environ.get("LINKLIB_SECRET_KEY") or AUTH_PASSWORD or "dev-insecure-key"
COOKIE_NAME = "cfo_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days

app = FastAPI(title="bmweis.com")


@app.on_event("startup")
def _seed_toolbox():
    """Seed tools and keep categories/advisor in sync with the seed list."""
    import json as _j
    from scripts.seed_tools import TOOLS
    lib = _lib()
    try:
        for t in TOOLS:
            row = lib.conn.execute(
                "SELECT id, advisor, categories_json FROM tools WHERE url = ?", (t["url"],)
            ).fetchone()
            if not row:
                lib.add_tool(t["name"], t["description"], t["url"], t["categories"],
                             approved=1, advisor=int(t.get("advisor", False)))
            else:
                new_cats = _j.dumps(t["categories"])
                new_adv = int(t.get("advisor", False))
                if row["categories_json"] != new_cats or row["advisor"] != new_adv:
                    lib.conn.execute(
                        "UPDATE tools SET categories_json=?, advisor=? WHERE id=?",
                        (new_cats, new_adv, row["id"]),
                    )
                    lib.conn.commit()
    finally:
        lib.close()


def _lib() -> Library:
    return Library(DB_PATH)


# Module-level job state for long-running admin tasks (single-process deployment).
# Each key is a job name ("enrich", "backfill"); value is a progress dict.
_JOB_LOCK = threading.Lock()
_JOB_STATE: dict[str, dict] = {}


def _job_set(name: str, **kw) -> None:
    with _JOB_LOCK:
        _JOB_STATE.setdefault(name, {}).update(kw)


def _job_get(name: str) -> dict:
    with _JOB_LOCK:
        return dict(_JOB_STATE.get(name, {}))


# --- Session cookie helpers (stdlib HMAC — no extra dependency) --------------

def _sign(value: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _make_session(role: str = "admin", username: str = "") -> str:
    """A signed cookie value carrying expiry, role, and username. Expires
    SESSION_TTL seconds from now."""
    exp = int(time.time()) + SESSION_TTL
    username = re.sub(r"[^a-z0-9._-]", "", (username or "").lower())[:64]
    return _sign(f"{exp}|{role}|{username}")


def _session_claims(cookie: str | None) -> dict | None:
    """Verify the cookie signature + expiry and return {exp, role, username},
    or None. Old single-number cookies (pre-roles) are treated as admin."""
    if not cookie or "." not in cookie:
        return None
    value, _, sig = cookie.rpartition(".")
    expected = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    parts = value.split("|")
    try:
        exp = int(parts[0])
    except (ValueError, IndexError):
        return None
    if exp <= int(time.time()):
        return None
    role = parts[1] if len(parts) > 1 else "admin"      # legacy cookie = admin
    username = parts[2] if len(parts) > 2 else ""
    return {"exp": exp, "role": role, "username": username}


def _current_claims(request: Request) -> dict | None:
    return _session_claims(request.cookies.get(COOKIE_NAME))


def _is_authed(request: Request) -> bool:
    """True for an admin session (or when no password is configured — local dev).
    Admin is the gate for every currently-private route; user-tier gating is layered
    on top in the re-tier phase."""
    if not AUTH_PASSWORD:
        return True
    claims = _current_claims(request)
    return bool(claims and claims["role"] == "admin")


def _is_member(request: Request) -> bool:
    """True for any valid signed-in session (user OR admin), or local dev."""
    if not AUTH_PASSWORD:
        return True
    return _current_claims(request) is not None


def _role(request: Request) -> str:
    """'admin' | 'user' | 'guest' — drives the nav."""
    if not AUTH_PASSWORD:
        return "admin"
    claims = _current_claims(request)
    if not claims:
        return "guest"
    return "admin" if claims["role"] == "admin" else "user"


def _login_redirect(request: Request) -> RedirectResponse:
    nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)


def _check_token(token: str | None) -> None:
    """Constant-time token check for the bookmarklet / programmatic save."""
    if SAVE_TOKEN and not (token and hmac.compare_digest(token, SAVE_TOKEN)):
        raise HTTPException(status_code=401, detail="bad or missing save token")


def _require_api(request: Request, token: str | None = None) -> None:
    """Admin-only API: a valid admin cookie OR the save token."""
    if _is_authed(request):
        return
    tok = token or request.headers.get("X-Save-Token")
    if SAVE_TOKEN and tok and hmac.compare_digest(tok, SAVE_TOKEN):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def _require_member(request: Request, token: str | None = None) -> None:
    """Member-tier API: any signed-in session (user or admin), OR the save token."""
    if _is_member(request):
        return
    tok = token or request.headers.get("X-Save-Token")
    if SAVE_TOKEN and tok and hmac.compare_digest(tok, SAVE_TOKEN):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def _esc(s) -> str:
    return (str(s) or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ---------------------------------------------------------------------------
# Shared layout helpers
# ---------------------------------------------------------------------------

_CSS = """
:root{
  /* Surfaces */
  --bg:#F5F4EF;            /* warm off-white page */
  --surface:#FFFFFF;       /* cards, inputs */
  --surface-2:#FAF9F4;     /* subtle alt panels, table stripes */
  /* Brand — Navy (primary, cool). deep · base · light · wash */
  --navy-deep:#001B4F;     /* button hover / depth */
  --navy:#002975;          /* primary base */
  --navy-light:#3F5C9A;    /* lighter navy — secondary accents, borders */
  --navy-wash:#EEF1F7;     /* soft navy fill — chip/ghost hovers */
  --accent:#002975;        /* legacy name now = navy (keeps old markup working) */
  --accent-light:#EEF1F7;  /* legacy name now = --navy-wash */
  /* Brand — Seafoam/Green (cool accent). deep · mid · base · wash */
  --seafoam-deep:#1F7A66;  /* deepest teal — text-capable on light (AA) */
  --seafoam-mid:#2E9C86;   /* mid teal — data-viz (legible as a fill/line) */
  --seafoam:#A3E5D4;       /* accent base (light mint) — tags, badges, underline */
  --seafoam-wash:#EAF7F2;  /* soft accent fill — calc readout, table accents */
  /* Brand — Coral (warm accent, rare). deep · base · light · wash */
  --coral-deep:#B14A30;    /* coral that must carry small text (AA on canvas) */
  --coral:#E8704F;         /* warm accent base — display pop, data-viz R&D series */
  --coral-light:#F4A98F;   /* lighter coral — soft highlights */
  --coral-wash:#FBEAE3;    /* soft coral fill — callouts (navy text) */
  /* Text */
  --ink:#1a1a1a;
  --ink-soft:#3a3833;
  --muted:#6F6A60;         /* warm mid-gray */
  /* Lines (warm-toned) */
  --line:#E4E0D6;
  --line-strong:#D6D1C4;
  /* Semantic — GER calculator readout only */
  --good:#002975; --caution:#9A6B12; --alert:#9E3B30;
  /* Type */
  --font-head:'Outfit',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-body:'DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
}
*{box-sizing:border-box;}
body{margin:0;font:16px/1.65 var(--font-body);color:var(--ink-soft);background:var(--bg);-webkit-font-smoothing:antialiased;}
a{color:var(--navy);text-decoration:none;}
a:hover{text-decoration:underline;}

/* Rope rule — the one nautical motif: a double hairline */
.rule{border-top:1px solid var(--line-strong);border-bottom:1px solid var(--line);height:3px;}

/* Header / nav */
.site-header{padding:18px 28px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:relative;}
.site-header .logo{font-family:var(--font-head);font-size:19px;font-weight:600;letter-spacing:-0.01em;color:var(--navy);}
.site-nav{display:flex;align-items:center;gap:22px;font-size:14px;}
.site-nav a{color:var(--muted);}
.site-nav a:hover{color:var(--ink);text-decoration:none;}
.site-nav a.active{color:var(--ink);font-weight:600;border-bottom:2px solid var(--seafoam);padding-bottom:3px;}
.site-nav .sep{width:1px;height:15px;background:var(--line-strong);}
.nav-toggle{display:none;background:none;border:1px solid var(--line-strong);border-radius:9px;width:40px;height:40px;color:var(--navy);font-size:18px;cursor:pointer;align-items:center;justify-content:center;}

/* Headings */
h1{font-family:var(--font-head);font-size:30px;font-weight:600;letter-spacing:-0.02em;color:var(--ink);margin:0 0 6px;}
h2{font-family:var(--font-head);font-size:21px;font-weight:600;letter-spacing:-0.01em;color:var(--ink);margin:38px 0 14px;}
h3{font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin:0 0 4px;}
p{margin:0 0 16px;color:var(--ink-soft);}

.page{max-width:780px;margin:0 auto;padding:48px 24px 72px;}

/* Buttons — primary navy fill, ghost navy outline. Seafoam is NEVER a button. */
.btn{display:inline-block;padding:11px 22px;background:var(--navy);color:#fff;border-radius:10px;font:600 15px var(--font-body);border:1px solid var(--navy);cursor:pointer;}
.btn:hover{background:var(--navy-deep);border-color:var(--navy-deep);text-decoration:none;}
.btn-ghost{background:transparent;color:var(--navy);border:1px solid var(--navy);}
.btn-ghost:hover{background:var(--accent-light);color:var(--navy);}

/* Inputs — navy focus border + soft seafoam ring */
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px rgba(163,229,212,.55);}

/* Footer */
.site-footer{padding:24px 28px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted);}
.site-footer .brand{display:flex;align-items:center;gap:10px;}
.site-footer .brand b{font-family:var(--font-head);font-weight:600;color:var(--navy);font-size:14px;}
.site-footer .links{display:flex;gap:18px;align-items:center;}
.site-footer a{color:var(--muted);}

/* Mobile: nav collapses to a navy hamburger drawer */
@media(max-width:760px){
  .nav-toggle{display:flex;}
  .site-nav{display:none;position:absolute;top:100%;left:0;right:0;flex-direction:column;gap:0;background:var(--navy);padding:6px 0;z-index:20;align-items:stretch;}
  .site-nav.open{display:flex;}
  .site-nav a{color:rgba(255,255,255,.82);padding:13px 24px;border-left:3px solid transparent;}
  .site-nav a:hover{color:#fff;background:rgba(255,255,255,.06);text-decoration:none;}
  .site-nav a.active{color:#fff;font-weight:600;border-bottom:none;border-left:3px solid var(--seafoam);background:rgba(255,255,255,.06);padding-bottom:13px;}
  .site-nav .sep{display:none;}
}
"""

def _page(title: str, active: str, body: str, authed: bool = False,
          role: str | None = None) -> str:
    # role: "admin" | "user" | "guest". Falls back to authed for legacy callers.
    if role is None:
        role = "admin" if authed else "guest"
    public = [("/about", "About"), ("/thought-leadership", "Thought Leadership"),
              ("/tools", "CFO Toolbox"), ("/contact", "Contact")]
    # Account-only section — one nav entry ("Library") that opens a hub linking to
    # Archive, Feed, and Ask. Shown to everyone so the gated area is discoverable;
    # clicking it when signed out lands on the login screen.
    member = [("/library", "Library")]

    def links(items):
        return "".join(
            f'<a href="{href}" class="{"active" if active == label else ""}">{label}</a>'
            for href, label in items
        )

    nav = links(public) + '<span class="sep"></span>' + links(member)
    if role == "admin":
        # Admin sees exactly what a member sees, plus the Admin hub (which holds
        # the admin-only tools like Draft). Keeps the top nav uncluttered.
        nav += f'<a href="/admin" class="{"active" if active == "Admin" else ""}">Admin</a>'
        nav += '<a href="/logout">Log out</a>'
    elif role == "user":
        nav += '<a href="/logout">Log out</a>'
    else:
        nav += f'<a href="/login" class="{"active" if active == "Sign in" else ""}">Sign in</a>'

    star = ('<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">'
            '<path d="M 8.00 0.00 L 8.55 6.66 L 10.76 5.24 L 9.34 7.45 L 16.00 8.00 L 9.34 8.55 '
            'L 10.76 10.76 L 8.55 9.34 L 8.00 16.00 L 7.45 9.34 L 5.24 10.76 L 6.66 8.55 L 0.00 8.00 '
            'L 6.66 7.45 L 5.24 5.24 L 7.45 6.66 Z" fill="#002975"/></svg>')

    # "Built with open-source love" — links to the showcase for admins, plain for visitors.
    _love = 'Built with open-source love <span style="color:var(--coral-deep);">&#9829;</span>'
    oss_love = (f'<a href="/admin/open-source">{_love}</a>' if role == "admin"
                else f'<span>{_love}</span>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&display=swap" rel="stylesheet">
<style>{_CSS}</style></head><body>
<header class="site-header">
  <a class="logo" href="/">Brian Weisberg</a>
  <button class="nav-toggle" aria-label="Menu" onclick="document.getElementById('nav').classList.toggle('open')">&#9776;</button>
  <nav class="site-nav" id="nav">{nav}</nav>
</header>
<div class="rule"></div>
{body}
<div class="rule"></div>
<footer class="site-footer">
  <span class="brand">{star}<b>Brian Weisberg</b> &middot; Strategic finance for companies that are scaling</span>
  <span class="links"><a href="https://linkedin.com/in/bmw-cfo" target="_blank" rel="noopener">LinkedIn</a><a href="/contact">Contact</a><span>&copy; 2026</span></span>
  <span style="flex-basis:100%;text-align:center;font-size:12px;color:var(--muted);">{oss_love}</span>
</footer>
</body></html>"""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/library", error: str = ""):
    if _is_member(request):   # already signed in (member or admin) — go on in
        return RedirectResponse(next or "/library", status_code=303)
    err = ('<p style="color:#b91c1c;font-size:14px;margin:0 0 16px;">That didn&rsquo;t work — check your details and try again.</p>'
           if error else "")
    body = f"""<div class="page" style="max-width:420px;">
<h1>Sign in</h1>
<p style="color:var(--muted);margin:4px 0 28px;">Sign in with your username and password.</p>
{err}
<form method="post" action="/login" style="display:grid;gap:16px;">
  <input type="hidden" name="next" value="{_esc(next or '/library')}">
  <input name="username" type="text" required autofocus autocomplete="username" placeholder="Username"
         style="width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  <input name="password" type="password" required autocomplete="current-password" placeholder="Password"
         style="width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  <button type="submit" class="btn">Sign in</button>
</form>
</div>"""
    return HTMLResponse(_page("Sign in—Brian Weisberg", "", body))


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    nxt = form.get("next") or "/library"
    if not nxt.startswith("/"):  # never redirect off-site
        nxt = "/library"

    role = username_for_cookie = None
    if username:
        lib = _lib()
        try:
            user = lib.authenticate(username, password)
        finally:
            lib.close()
        if user:
            role, username_for_cookie = user["role"], user["username"]
        elif (AUTH_PASSWORD and username.lower() == ADMIN_USERNAME
              and hmac.compare_digest(password, AUTH_PASSWORD)):
            # break-glass: the host password, used with the reserved admin username
            role, username_for_cookie = "admin", ADMIN_USERNAME

    if role:
        resp = RedirectResponse(nxt, status_code=303)
        secure = (request.url.scheme == "https"
                  or request.headers.get("x-forwarded-proto") == "https")
        resp.set_cookie(COOKIE_NAME, _make_session(role, username_for_cookie),
                        max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=secure, path="/")
        return resp
    return RedirectResponse(f"/login?error=1&next={quote(nxt, safe='')}", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

def _avatar(size: int = 140) -> str:
    """Headshot if present, else a clean monogram — shown the moment headshot.jpg lands."""
    if os.path.isfile(os.path.join(_STATIC_DIR, "headshot.jpg")):
        return (f'<img src="/static/headshot.jpg" alt="Brian Weisberg" '
                f'style="width:{size}px;height:{size}px;border-radius:50%;object-fit:cover;'
                f'object-position:center top;flex-shrink:0;border:3px solid var(--navy);">')
    return (f'<div aria-label="Brian Weisberg" '
            f'style="width:{size}px;height:{size}px;border-radius:50%;flex-shrink:0;border:3px solid var(--navy);'
            f'background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;'
            f'font-size:{round(size/3)}px;font-weight:700;letter-spacing:-0.02em;">BW</div>')


@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    def _rcard(href, title, desc, external=False):
        attrs = ' target="_blank" rel="noopener"' if external else ''
        return (
            f'<a href="{href}"{attrs} style="display:block;background:var(--surface);border:1px solid var(--line);'
            f'border-radius:14px;padding:20px 22px;text-decoration:none;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--navy);letter-spacing:-0.01em;">{title}</span>'
            f'<span style="color:var(--navy);font-size:18px;line-height:1;">&rarr;</span></div>'
            f'<p style="margin:6px 0 0;font-size:14px;color:var(--muted);line-height:1.5;">{desc}</p></a>'
        )

    cards = "".join([
        _rcard("/thought-leadership", "Thought Leadership",
               "Frameworks and playbooks worth keeping: the Growth Engine Ratio for pressure-testing GTM "
               "efficiency, a playbook for running an AI hackathon with your finance team, and a guide to "
               "connecting Claude to NetSuite — plus the podcasts, writing, and press."),
        _rcard("/tools", "CFO Toolbox",
               "A curated directory of the tools high-growth finance teams actually use."),
    ])

    # The "suggest a piece" prompt is shown only to signed-in members — submissions
    # are account-only now, to keep public spam out.
    suggest = ('<p style="margin:14px 0 0;font-size:14px;color:var(--muted);">Read something a finance '
               'leader should have in their back pocket? <a href="/library/submit">Suggest a piece for '
               'the archive &rarr;</a></p>') if _is_member(request) else ''

    body = f"""<div class="page">
<div style="max-width:680px;">
  <div style="font:600 12px var(--font-body);letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:14px;">A CFO for CFOs</div>
  <h1 style="margin:0 0 18px;font-size:42px;letter-spacing:-0.025em;line-height:1.08;">Be the strategic partner your leadership team leans on&mdash;not just the scorekeeper.</h1>
  <p style="font-size:18px;line-height:1.6;color:var(--ink-soft);">This is where I share the writing, tools, and hard-won lessons that help finance leaders at high-growth tech companies step into that role: GTM efficiency, headcount and org design, mentorship, and the cross-functional calls finance gets pulled into as a company scales.</p>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:34px 0 8px;">{cards}</div>
{suggest}

<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin-top:26px;">
  {_avatar(64)}
  <div style="flex:1;min-width:240px;">
    <p style="margin:0;font-size:14.5px;color:var(--ink-soft);line-height:1.55;">I'm <strong>Brian Weisberg</strong>, VP of Business Operations and Strategic Finance at <a href="https://www.mux.com" target="_blank" rel="noopener">Mux</a>. Fifteen-plus years scaling B2B SaaS finance, from the founder's corner. <a href="/about">More about me &rarr;</a></p>
  </div>
</div>

<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:26px;">
  <a href="/thought-leadership" class="btn">Thought Leadership</a>
  <a href="/contact" class="btn btn-ghost">Get in Touch</a>
  <a href="https://linkedin.com/in/bmw-cfo" target="_blank" rel="noopener" class="btn btn-ghost">LinkedIn</a>
</div>
</div>"""
    return HTMLResponse(_page("Brian Weisberg — strategic finance for high-growth tech", "Home", body, role=_role(request)))


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    body = f"""<div class="page">
<div style="display:flex;align-items:flex-start;gap:32px;flex-wrap:wrap;margin-bottom:28px;">
  {_avatar(140)}
  <div>
    <div style="font:600 12px var(--font-body);letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">CFO &middot; Boston, MA</div>
    <h1 style="margin:0 0 4px;font-size:42px;letter-spacing:-0.025em;line-height:1.05;">Brian Weisberg</h1>
  </div>
</div>

<p>I'm a tech CFO. That's not my title today—I'm VP of Business Operations and Strategic Finance at
<a href="https://www.mux.com" target="_blank" rel="noopener">Mux</a>—but it's how I think, how I
operate, and the lens I bring to every business I help build. Fifteen-plus years in finance,
accounting, and operations for B2B SaaS and IT infrastructure companies will do that to you. At Mux
I get to do the part I love most: working in the thick of the business, where finance and strategy
actually meet the day-to-day work.</p>

<p>Before Mux, I stepped in as interim CFO of The Suite, Inc. and GM of
<a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a>—the invite-only
network of 1,000+ growth- and late-stage CFOs. I'd been a founding member of that community, so when
they needed someone to run both the parent company and the network, I raised my hand. It was always
meant to be a chapter, not a destination; when the right operating role came along at Mux, we parted
ways as friends. Before all of that, I spent seven years as CFO of
<a href="https://tidelift.com" target="_blank" rel="noopener">Tidelift</a>, helping grow the company
from a handful of people through $73.5M in funding to an acquisition by Sonar.</p>

<p>Scaling startups is the work I care about most. I'm not a founder myself, but I've built my career
in the founder's corner—turning their conviction and momentum into something a business can actually
stand on: a clear financial picture, a sound operational backbone, and decisions that hold up when
the numbers get hard.</p>

<p>I'm also not a behind-the-desk CFO. The best part of the job is getting out into the business—
mentoring and learning from peers in product, engineering, sales, and marketing. That's where trust
gets built, where you pick up the earned secrets of how a company really works, and where finance
stops being a scorecard and becomes something a leadership team genuinely leans on.</p>

<p>In 2025, at the invitation of my friend <a href="https://www.onlycfo.io" target="_blank" rel="noopener">OnlyCFO</a>,
I tried my hand at hosting a podcast—and it turned out to be one of the more fun things I've done
professionally. On <a href="https://www.onlycfo.io/podcast" target="_blank" rel="noopener">The Cash Flow Show</a>
I bring friends and fellow finance leaders on to dig into the topics I care about most: how tech
companies make money, how finance teams earn their seat at the table, and what it really takes to
scale a business with discipline. I also write on startup finance and advise finance leaders making
the early-to-growth leap. Based in Boston.</p>

<div style="display:grid;grid-template-columns:2fr 3fr;gap:10px;margin-top:32px;">
  <img src="/static/speaking-close.jpg" alt="Brian Weisberg speaking on stage"
    style="width:100%;height:200px;object-fit:cover;object-position:center top;border-radius:10px;display:block;">
  <img src="/static/speaking-wide.jpg" alt="Brian Weisberg on stage at the Abacum AI Summit"
    style="width:100%;height:200px;object-fit:cover;object-position:center 30%;border-radius:10px;display:block;">
</div>
<p style="font-size:12px;color:var(--muted);margin:8px 0 24px;font-style:italic;">Abacum AI Summit &middot; New York &middot; April 2026</p>

<div style="display:flex;gap:12px;flex-wrap:wrap;">
  <a href="/thought-leadership" class="btn">Thought Leadership</a>
  <a href="/contact" class="btn btn-ghost">Get in Touch</a>
  <a href="https://linkedin.com/in/bmw-cfo" target="_blank" rel="noopener" class="btn btn-ghost">LinkedIn</a>
</div>
</div>"""
    return HTMLResponse(_page("About — Brian Weisberg", "About", body, role=_role(request)))


@app.get("/thought-leadership", response_class=HTMLResponse)
def thought_leadership(request: Request):
    def section(title: str, items: list[tuple[str, str, str]]) -> str:
        # items: (label, url, sort_key) — sort_key is "YYYY-MM" or "" to pin to top.
        # Editorial rows separated by warm hairlines, under a small-caps navy label.
        # The trailing "· Mon YYYY" is stripped from the label (it isn't shown as a
        # column in this layout). An empty url renders the label as plain (unlinked)
        # text — e.g. invite-only events with no public page.
        sorted_items = sorted(items, key=lambda x: x[2], reverse=True)
        link_style = "font:500 16px var(--font-body);color:var(--ink);line-height:1.4;"
        rows = []
        for label, url, _sort_key in sorted_items:
            clean = re.sub(r"\s*·\s*[A-Za-z]+\s+20\d{2}\s*$", "", label)
            inner = (f'<a href="{url}" target="_blank" rel="noopener" style="{link_style}">{_esc(clean)}</a>'
                     if url else f'<span style="{link_style}">{_esc(clean)}</span>')
            rows.append(f'<div style="border-top:1px solid var(--line);padding:13px 0;">{inner}</div>')
        return (f'<div style="font:600 12px var(--font-body);letter-spacing:.12em;'
                f'text-transform:uppercase;color:var(--navy);margin:30px 0 2px;">{_esc(title)}</div>'
                f'{"".join(rows)}')

    # Featured: three flagship pieces, one consistent card treatment. The only
    # per-card variation is the small category tag colour — no full-colour floods,
    # which is what made the old top read as busy.
    def fcard(href, tag, tag_color, title, desc, cta):
        return (
            f'<a href="{href}" class="tl-card">'
            f'<span class="tl-tag" style="color:{tag_color};">{tag}</span>'
            f'<h3>{title}</h3><p>{desc}</p>'
            f'<span class="tl-go">{cta} &rarr;</span></a>'
        )

    featured = (
        '<div class="tl-featured">'
        + fcard("/growth-engine-ratio", "Framework", "var(--coral-deep)",
                "The Growth Engine Ratio",
                "A metric for how R&amp;D and GTM investments work together to drive growth—with an interactive calculator.",
                "Read the framework")
        + fcard("/finops-ai-hackathon", "Playbook", "var(--seafoam-deep)",
                "Sail, Don&rsquo;t Row",
                "How to run an AI hackathon with your finance team—the full format, facilitation mechanics, and how to make it stick.",
                "Read the playbook")
        + fcard("/netsuite-mcp", "Setup Guide", "var(--navy-light)",
                "Connecting Claude to NetSuite",
                "End-to-end setup for the two-role OAuth architecture—what it is, why it&rsquo;s secure, and how to use it.",
                "Read the guide")
        + '</div>'
    )

    body = (
        '<div class="page">'
        '<style>'
        '.tl-featured{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:6px 0 12px;}'
        '.tl-card{display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line-strong);'
        'border-radius:14px;padding:22px 22px 18px;text-decoration:none;transition:border-color .15s,box-shadow .15s,transform .15s;}'
        '.tl-card:hover{border-color:var(--navy-light);box-shadow:0 6px 20px rgba(0,41,117,.08);transform:translateY(-2px);text-decoration:none;}'
        '.tl-tag{font:700 10px var(--font-body);letter-spacing:.12em;text-transform:uppercase;margin-bottom:12px;}'
        '.tl-card h3{font-family:var(--font-head);font-size:17px;font-weight:700;letter-spacing:-.01em;color:var(--ink);margin:0 0 7px;line-height:1.25;}'
        '.tl-card p{font-size:13px;color:var(--ink-soft);line-height:1.5;margin:0 0 16px;}'
        '.tl-card .tl-go{margin-top:auto;font:600 13px var(--font-body);color:var(--navy);}'
        '.tl-photos{display:grid;grid-template-columns:2fr 3fr;gap:10px;margin:8px 0 6px;}'
        '.tl-photos img{width:100%;height:200px;object-fit:cover;border-radius:10px;display:block;}'
        '@media(max-width:560px){.tl-photos{grid-template-columns:1fr;}.tl-photos img{height:170px;}}'
        '</style>'
        '<h1>Thought Leadership</h1>'
        '<p style="color:var(--muted);margin:4px 0 24px;">Writing, talks, podcasts, and press &mdash; from a tech CFO working in the thick of the business.</p>'
        + featured
    )

    body += section("Writing", [
        ("The Growth Engine Ratio: Accounting for the Missing Half of Your Efficiency Equation · The F Suite · Jun 2026",
         "/growth-engine-ratio", "2026-06"),
        ("Sail, Don't Row: A Playbook for Running an AI Hackathon With Your Finance Team · Jun 2026",
         "/finops-ai-hackathon", "2026-06"),
        ("Connecting Claude to NetSuite: A Setup Guide for Finance Teams · Jun 2026",
         "/netsuite-mcp", "2026-06"),
        ("The F Suite — Exit Readiness for CFOs · The F Suite · Mar 2026",
         "https://www.fsuite.co/blog/exit-readiness-cfos", "2026-03"),
        ("OnlyCFO — Building Dashboards That Matter · OnlyCFO · Apr 2024",
         "https://www.onlycfo.io/p/building-dashboards-that-matter", "2024-04"),
    ])

    # On-stage photos lead the Speaking section, where they have context.
    body += """<div class="tl-photos">
  <img src="/static/speaking-close.jpg" alt="Brian Weisberg speaking at the Abacum AI Summit, April 2026">
  <img src="/static/speaking-wide.jpg" alt="Panel discussion at the Abacum AI Summit, April 2026">
</div>
<p style="font-size:12px;color:var(--muted);margin:0 0 4px;font-style:italic;">Abacum AI Summit &middot; New York &middot; April 2026</p>"""

    body += section("Speaking & Events", [
        ("Abacum AI Summit — Recording · Abacum · Apr 2026",
         "https://www.youtube.com/watch?v=MDBz0OpR1II", "2026-04"),
        ("Abacum — Beyond the Spreadsheet: What FP&A Platforms Need to Deliver in an AI-First Era (Webinar Panel) · Abacum · Jun 2026",
         "https://www.abacum.ai/webinars/beyond-the-spreadsheet-what-fp-a-platforms-need-to-deliver-in-an-ai-first-era", "2026-06"),
        ("Claude in Action for Finance — The F Suite Virtual Panel · The F Suite · Apr 2026",
         "https://fsuitevirtualpanel430.splashthat.com", "2026-04"),
        ("The F Suite Boston — Growth CFO Salon · The F Suite · Nov 2025",
         "", "2025-11"),
        ("The F Suite Cash Cycle Demo Day — Opening & Closing Remarks · The F Suite · Oct 2025",
         "https://cashcycledemoday.splashthat.com/", "2025-10"),
        ("The F Suite Boston — CFO Supper Club · The F Suite · Aug 2025",
         "", "2025-08"),
        ("Fidelity CFO Roundtable — M&A and Managing Uncertainty · Fidelity · Mar 2025",
         "https://luma.com/7fwtr2n8", "2025-03"),
        ("Numeric — Lean Accounting Team (Webinar Host) · Numeric · Mar 2024",
         "https://numeric.lpages.co/lean-accounting-team-webinar/", "2024-03"),
        ("The F Suite Boston — Private Dinner & Guided Discussion · The F Suite · Apr 2024",
         "", "2024-04"),
        ("The F Suite Boston — CFO Dinner · The F Suite · Dec 2023",
         "", "2023-12"),
        ("The F Suite — NC Launch Dinner · The F Suite · Jun 2023",
         "", "2023-06"),
        ("Teampay Agile Finance Summit · Teampay · Oct 2021",
         "https://www.accelevents.com/e/agile-finance-summit-2021", "2021-10"),
    ])

    body += section("Podcasts", [
        ("The Cash Flow Show — Conversations about how tech companies make money (host · full episode feed) · OnlyCFO",
         "https://www.onlycfo.io/podcast", ""),
        ("Adopting AI in Finance & Accounting — with Sowmya Ranganathan (former Controller, OpenAI) · The Cash Flow Show · Aug 2025",
         "https://open.spotify.com/episode/6uXkeypUPX5g5yB8lHGq2V", "2025-08"),
        ("State of Fundraising / Equity Market · The Cash Flow Show · Jun 2025",
         "https://open.spotify.com/episode/0rSm42OSNRzjiG3cYye3tX", "2025-06"),
        ("Is ARR Dead? · The Cash Flow Show · Jun 2025",
         "https://open.spotify.com/episode/5G0GUaRrOeGXxJwFh9WsPw", "2025-06"),
        ("Commission Plan Strategies in 2025 — with Meir Rotenberg & David Ma · The Cash Flow Show · Mar 2025",
         "https://open.spotify.com/episode/64DsKmsDgOshd3LQdM4Cte", "2025-03"),
        ("The M&A Playbook · The Cash Flow Show · Mar 2025",
         "https://open.spotify.com/episode/5kMa3kkutDhsoc4SBoOBZ1", "2025-03"),
        ("Code to Cash, Ep. 9 — Monetizing Thoughtfully: Architecting Financial Stacks (guest) · Monetizely · Sep 2023",
         "https://creators.spotify.com/pod/profile/codetocash/episodes/Episode-9-Monetizing-Thoughtfully--Architecting-Financial-Stacks-with-Brian-Weisberg--CFO-of-Tidelift-e28unsn",
         "2023-09"),
        ("OpexEngine — SaaS Conversations: Dynamic Planning for SaaS Finance Leaders (guest) · OpexEngine · May 2023",
         "https://www.opexengine.com/webinar/opexengine-saas-conversations-dynamic-planning-for-saas-finance-leaders",
         "2023-05"),
        ("Role Forward Podcast — The Heuristics of Forecasting (guest) · Mosaic Tech · Dec 2022",
         "https://www.youtube.com/watch?v=mqVvcVVTSrk", "2022-12"),
        ("Role Forward Podcast — Collaborative Budgeting (guest) · Mosaic Tech · Apr 2022",
         "https://www.youtube.com/watch?v=GPdRstJ_sKw", "2022-04"),
    ])

    body += section("Press", [
        ("Sequence — From $1M to $100M: 6 Finance Lessons from the Frontline · Sequence · Jul 2025",
         "https://www.sequencehq.com/blog/from-1m-to-100m-6-finance-lessons-from-the-frontline", "2025-07"),
        ("LegalDive — GC/CFO Collaboration · LegalDive · Mar 2023",
         "https://www.legaldive.com/news/gc-cfo-collaboration-svb-techgc-the-f-suite-silicon-valley-bank/646561/",
         "2023-03"),
        ("Numeric — When and How to Scale Your Accounting Department · Numeric · Nov 2023",
         "https://www.numeric.io/blog/when-and-how-to-scale-your-accounting-department", "2023-11"),
        ("Numeric — Startup CFO Primer · Numeric · Jun 2024",
         "https://www.numeric.io/blog/startup-cfo-primer", "2024-06"),
        ("CFO Drive — Innovative Cost-Saving Measures Q&A · CFO Drive · Jul 2024",
         "https://cfodrive.com/qa/what-innovative-cost-saving-measures-can-significantly-impact-a-companys-bottom-line/",
         "2024-07"),
    ])

    body += "</div>"
    return HTMLResponse(_page("Thought Leadership—Brian Weisberg", "Thought Leadership", body, role=_role(request)))


@app.get("/growth-engine-ratio", response_class=HTMLResponse)
def growth_engine_ratio(request: Request):
    body = """<div class="page" style="max-width:820px;">
<style>
  .ger-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
  .ger-grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}
  .ger-grid-3{display:grid;grid-template-columns:88px 1fr 1fr 1fr;gap:10px;align-items:center;}
  .ger-table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .ger-modes{display:flex;flex-wrap:wrap;gap:8px;}
  .ger-mode{font:inherit;font-size:14px;font-weight:500;color:var(--muted);background:#fff;border:1px solid var(--line);border-radius:999px;padding:8px 16px;cursor:pointer;}
  .ger-mode:hover{background:var(--accent-light);color:var(--ink);}
  .ger-mode-on{background:var(--accent) !important;color:#fff !important;border-color:var(--accent) !important;}
  .ger-in{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);}
  .qlabel{font-size:13px;color:var(--ink);font-weight:500;}
  .qhead{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
  .qrow-proj .ger-in{background:#fbfaf6;border-style:dashed;}
  .ger-chart{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:12px;background:#fff;font-family:var(--font-body);}
  .ger-contrib-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .ger-contrib{width:100%;height:auto;display:block;font-family:var(--font-body);}
  .tl-step{display:flex;flex-direction:column;gap:6px;}
  .tl-ctrl{display:inline-flex;align-items:center;gap:16px;border:1px solid var(--line);border-radius:10px;padding:6px 12px;background:#fff;width:max-content;}
  .tl-ctrl button{font:inherit;font-size:18px;line-height:1;width:28px;height:28px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--accent);cursor:pointer;}
  .tl-ctrl button:hover{background:var(--accent-light);}
  .tl-ctrl span{font-size:16px;font-weight:700;min-width:16px;text-align:center;color:var(--ink);}
  @media (max-width:640px){
    .ger-grid-4{grid-template-columns:repeat(2,1fr);}
    .ger-grid-2{grid-template-columns:1fr;}
    .ger-grid-3{grid-template-columns:58px 1fr 1fr 1fr;gap:6px;}
    .ger-card{padding:22px 18px !important;}
    .ger-table th,.ger-table td{padding:8px 10px !important;font-size:13px !important;}
    .ger-value-big{font-size:38px !important;}
    .ger-in{font-size:14px;padding:8px 9px;}
  }
</style>

<p style="font-size:13px;color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em;">Framework</p>
<h1 style="margin:0 0 8px;">The Growth Engine Ratio</h1>
<p style="color:var(--muted);font-size:15px;margin:0 0 32px;">
  By Brian Weisberg &middot; Published with <a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a> &middot; June 2026
</p>

<div style="background:var(--accent-light);border-left:3px solid var(--accent);border-radius:0 10px 10px 0;padding:18px 22px;margin:0 0 36px;">
  <p style="margin:0;font-size:15px;">
    The full guide—including benchmark data from 200+ public and private SaaS companies via OPEXEngine—
    is available as a downloadable whitepaper on The F Suite.
    <strong><a href="https://www.fsuite.co" target="_blank" rel="noopener">Read the full article and download the guide &rarr;</a></strong>
    <em style="display:block;margin-top:6px;font-size:13px;color:var(--muted);">(Link will be live when The F Suite publishes—coming soon.)</em>
  </p>
</div>

<h2 style="margin-top:0;">Why I Built This</h2>
<p>Most SaaS efficiency metrics measure one engine at a time. CAC payback tells you how quickly GTM
investment pays back on new logos. Magic Number tells you how much ARR you're getting per dollar of
sales and marketing spend. Both are useful—I use them all the time—but they share a blind spot:
they leave R&D entirely out of the efficiency equation.</p>

<p>That bothers me. At most companies, R&D is 20–30% of revenue. It's a meaningful investment, and
it directly influences how easy—or hard—it is for GTM to do its job. A great product shortens
sales cycles, reduces churn, and drives expansion. A product that's hard to understand or hasn't
kept pace with customer needs makes every dollar of GTM spend work harder just to stay in place.</p>

<p>When product and GTM are evaluated in separate silos, it's almost impossible to answer the
question that actually matters: are these two engines working together efficiently?
I came up with the Growth Engine Ratio to answer that question.</p>

<h2>The Core Idea</h2>
<p>The framework is built on a simple observation: revenue recognized today is the result of
investments made over the past several quarters, not just last quarter. Features ship before
they're sold. Pipeline built in Q1 converts in Q3. A single period's P&amp;L doesn't capture that.</p>

<p>So instead of comparing today's revenue growth to today's spending, the Growth Engine Ratio
distributes investment across the quarters that actually contributed to a given period's growth.
I call this the <strong>time-distributed contribution model</strong>.</p>

<p>The formula:</p>
<div style="background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin:0 0 24px;font-family:ui-monospace,monospace;font-size:14px;line-height:1.8;">
  <strong>Growth Engine Ratio = Annualized Revenue Growth &divide; (GTM Investment + R&amp;D Investment)</strong><br><br>
  Annualized Growth = (Revenue Q<sub>n</sub> &minus; Revenue Q<sub>n-1</sub>) &times; 4<br>
  GTM Investment = 0.25 &times; (GTM<sub>n-4</sub> + GTM<sub>n-3</sub> + GTM<sub>n-2</sub> + GTM<sub>n-1</sub>)<br>
  R&amp;D Investment = 0.25 &times; (R&amp;D<sub>n-5</sub> + R&amp;D<sub>n-4</sub>)
</div>

<p>GTM uses a 4-quarter lookback because enterprise sales cycles run 6–9 months—pipeline built
in Q<sub>n-4</sub> converts across subsequent quarters until it lands in Q<sub>n</sub>.
R&amp;D uses a 2-quarter lookback starting one quarter earlier (n-5, n-4) because features are
built before they're sold. The build-then-sell sequence matters. Each contributing quarter is
weighted at 25%, so GTM enters at a full quarterly run-rate (four quarters &times; 25%) while the
shorter R&amp;D build window enters at half (two quarters &times; 25%).</p>

<h2>What the Number Tells You</h2>
<p>A ratio of <strong>$1.00</strong> means you're generating exactly $1 of annualized revenue growth for
every $1 of combined R&amp;D + GTM investment. That's the threshold that separates companies
that are profitable on acquisition from those that aren't.</p>

<p>In my analysis of 11 public SaaS companies across 188 company-quarters, only 2 exceeded $1.00
in steady state. The other 9 need to retain customers for 1.2 to 2.8 years just to break even
on acquisition costs. That changes how you think about churn—permanently.</p>

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
        <td style="padding:10px 14px;">$0.70 – $1.20</td>
        <td style="padding:10px 14px;">0.8 – 1.4 years</td>
        <td style="padding:10px 14px;">Above median—maintain efficiency as you scale</td>
      </tr>
      <tr style="border-top:1px solid var(--line);">
        <td style="padding:10px 14px;">&#10003; Typical</td>
        <td style="padding:10px 14px;">$0.50 – $0.70</td>
        <td style="padding:10px 14px;">1.4 – 2.0 years</td>
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

<h2>Calculate Your Ratio</h2>
<p style="color:var(--muted);font-size:15px;margin:-6px 0 18px;">
  Pick how much data you have. A single quarter returns your score against the benchmark; a run of
  quarters shows your trend; projected quarters show where you're headed—with an upper/lower band
  if your numbers land 10% better or worse than plan. All figures in the same currency, consistently.
</p>

<div class="ger-modes" role="tablist" style="margin:0 0 18px;">
  <button class="ger-mode ger-mode-on" id="tab-point" onclick="setMode('point')">Point in time</button>
  <button class="ger-mode" id="tab-timeline" onclick="setMode('timeline')">Timeline</button>
</div>

<div class="ger-card" style="background:#fff;border:1px solid var(--line);border-radius:16px;padding:28px 32px;margin:0 0 40px;">
  <div id="panel-point">
  <div style="display:grid;gap:20px;">

    <div>
      <p style="font:600 13px var(--font-body);letter-spacing:.04em;text-transform:uppercase;color:var(--navy);margin:0 0 12px;">Revenue</p>
      <div class="ger-grid-2">
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Current quarter (Q<sub>n</sub>)</label>
          <input id="rev_n" type="number" min="0" step="any" placeholder="e.g. 100"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Prior quarter (Q<sub>n-1</sub>)</label>
          <input id="rev_n1" type="number" min="0" step="any" placeholder="e.g. 90"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
      </div>
    </div>

    <div>
      <p style="font:600 13px var(--font-body);letter-spacing:.04em;text-transform:uppercase;color:var(--navy);margin:0 0 12px;">GTM Spend (Sales &amp; Marketing)—last 4 quarters</p>
      <div class="ger-grid-4">
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-4</sub></label>
          <input id="gtm4" type="number" min="0" step="any" placeholder="e.g. 20"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-3</sub></label>
          <input id="gtm3" type="number" min="0" step="any" placeholder="e.g. 22"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-2</sub></label>
          <input id="gtm2" type="number" min="0" step="any" placeholder="e.g. 24"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-1</sub></label>
          <input id="gtm1" type="number" min="0" step="any" placeholder="e.g. 26"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
      </div>
    </div>

    <div>
      <p style="font:600 13px var(--font-body);letter-spacing:.04em;text-transform:uppercase;color:var(--navy);margin:0 0 12px;">R&amp;D Spend—2 quarters (the build window)</p>
      <div class="ger-grid-2" style="max-width:320px;">
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-5</sub></label>
          <input id="rnd5" type="number" min="0" step="any" placeholder="e.g. 16"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
        <div>
          <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:4px;">Q<sub>n-4</sub></label>
          <input id="rnd4" type="number" min="0" step="any" placeholder="e.g. 18"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:15px;background:var(--bg);">
        </div>
      </div>
    </div>

    <div>
      <button onclick="calcGER()" class="btn" style="padding:12px 28px;font-size:16px;">Calculate my ratio</button>
      <button onclick="loadExample()" class="btn btn-ghost" style="margin-left:12px;">Load worked example</button>
    </div>
  </div>

  <div id="ger-result" style="display:none;margin-top:26px;background:var(--seafoam-wash);border:1px solid #C9EADF;border-radius:14px;padding:24px 26px;">
    <div style="display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap;">
      <div style="flex:0 0 auto;">
        <p style="font-size:13px;color:var(--muted);margin:0 0 4px;text-transform:uppercase;letter-spacing:.06em;">Your Growth Engine Ratio</p>
        <p id="ger-value" class="ger-value-big" style="font-family:var(--font-head);font-size:48px;font-weight:700;letter-spacing:-0.03em;margin:0;color:var(--accent);"></p>
      </div>
      <div style="flex:1;min-width:200px;">
        <p id="ger-tier" style="font-size:18px;font-weight:600;margin:0 0 6px;"></p>
        <p id="ger-breakeven" style="font-size:14px;color:var(--muted);margin:0 0 10px;"></p>
        <p id="ger-interp" style="font-size:15px;margin:0;"></p>
      </div>
    </div>
    <div id="ger-detail" style="margin-top:16px;font-size:13px;color:var(--muted);line-height:1.8;"></div>

    <div style="margin-top:22px;padding-top:18px;border-top:1px dashed var(--line);">
      <p style="font-weight:600;font-size:15px;margin:0 0 4px;color:var(--ink);">How this quarter is built</p>
      <p style="font-size:13px;color:var(--muted);margin:0 0 10px;">Each contributing quarter feeds 25% of its spend into the window—R&amp;D first (build), then GTM (sell), aligned to the revenue it produced.</p>
      <div class="ger-contrib-wrap"><div id="point-contrib" style="min-width:560px;"></div></div>
    </div>

  </div><!-- /ger-result -->
  </div><!-- /panel-point -->

  <div id="panel-timeline" style="display:none;">
    <p style="font-size:14px;color:var(--muted);margin:0 0 16px;">
      Plot your ratio over time. Choose how many quarters to look <strong>back</strong> (actuals) and
      <strong>forward</strong> (projected)—up to 2 each, for up to 5 measured quarters. Forward quarters
      are shaded and drawn dashed, with a best/worst band (revenue &amp; spend 10% better or worse than
      plan). The first five rows are lookback context for the earliest measured quarter.
    </p>
    <div style="display:flex;gap:28px;flex-wrap:wrap;margin:0 0 20px;">
      <div class="tl-step"><span class="qhead">Periods back</span>
        <div class="tl-ctrl"><button onclick="stepTL('back',-1)">&minus;</button><span id="tl-back">2</span><button onclick="stepTL('back',1)">+</button></div></div>
      <div class="tl-step"><span class="qhead">Periods forward</span>
        <div class="tl-ctrl"><button onclick="stepTL('fwd',-1)">&minus;</button><span id="tl-fwd">2</span><button onclick="stepTL('fwd',1)">+</button></div></div>
    </div>
    <div class="ger-grid-3" style="margin-bottom:8px;">
      <span class="qhead">Quarter</span><span class="qhead">Revenue</span><span class="qhead">GTM spend</span><span class="qhead">R&amp;D spend</span>
    </div>
    <div id="tl-rows" style="display:grid;gap:8px;"></div>
    <div style="margin-top:22px;">
      <button onclick="calcTimeline()" class="btn" style="padding:12px 28px;font-size:16px;">Plot timeline</button>
      <button onclick="loadTimelineExample()" class="btn btn-ghost" style="margin-left:12px;">Load example</button>
    </div>
    <div id="tl-result" style="display:none;margin-top:26px;padding-top:24px;border-top:1px solid var(--line);"></div>
  </div>
</div><!-- /ger-card -->

<p style="font-size:13px;color:var(--muted);margin:-20px 0 40px;">
  <strong>Methodology note:</strong> GTM = Sales &amp; Marketing expense (GAAP including SBC).
  R&D = Research &amp; Development expense. Use either GAAP or non-GAAP consistently—don't mix. Benchmarks in the full guide use GAAP. Every ratio needs 6 consecutive quarters for the
  n-5 R&amp;D lookback, so the timeline carries five quarters of lookback before its first measured
  point and adds one measured quarter for each period you look back or forward.
</p>

<h2>A Note on Retention</h2>
<p>One of the more useful outputs of this framework is a simple break-even calculation:
<strong>Years to Break Even = 1 ÷ Efficiency Ratio</strong>. If your ratio is $0.60, you
need to retain each customer for 1.7 years just to recover acquisition costs—and that
assumes flat renewal with no expansion. Strong NRR (above 110%) compresses that timeline;
contraction can make it indefinitely long.</p>

<p>Companies below $1.00—which is most of them—need both high gross retention and strong
net expansion for the economics to work. One without the other isn't sufficient. The ratio
makes that constraint explicit in a way that's hard to argue with in a board room.</p>

<h2>Get the Full Guide</h2>
<p>The whitepaper includes the complete methodology, a worked example using Datadog's public
financials, benchmark data across 200+ companies via OPEXEngine, and a performance tier guide
with specific actions to take based on where your ratio lands. It's published in partnership
with The F Suite.</p>

<a href="https://www.fsuite.co" target="_blank" rel="noopener" class="btn" style="font-size:15px;padding:12px 24px;">
  Download the full guide &rarr;
</a>
<p style="font-size:13px;color:var(--muted);margin-top:8px;">(Full link coming soon—check back or <a href="/contact">reach out</a> and I'll send it directly.)</p>

</div>

<script>
function v(id) { return parseFloat(document.getElementById(id).value) || 0; }
function fmtRatio(r) { return r >= 0 ? '$' + r.toFixed(2) : '-$' + Math.abs(r).toFixed(2); }

// Shared tier lookup so the headline result and the sensitivity panel stay in sync.
function gerTier(ratio) {
  if (ratio >= 1.20) return {tier:'&#127942; Elite (top 10%)', color:'#002975',
    interp:"You've earned the right to invest aggressively. Every new customer is profitable on acquisition—consider TAM expansion, adjacent markets, or accelerating hiring."};
  if (ratio >= 0.70) return {tier:'&#11088; Strong (above median)', color:'#002975',
    interp:"Solid performance. Focus on maintaining efficiency as you scale. You're close to the $1.00 break-even—small improvements in NRR or cost discipline can get you there."};
  if (ratio >= 0.50) return {tier:'&#10003; Typical (near median)', color:'#9A6B12',
    interp:"You're in the pack. Diagnose: is growth too slow, or investment too high? Pick one to improve first. Retention is critical."};
  if (ratio > 0) return {tier:'&#9888;&#65039; Below target (bottom 25%)', color:'#9E3B30',
    interp:"Urgent strategic review needed. Growth likely decelerated while spending stayed elevated. Fix retention and expansion economics before scaling acquisition further."};
  return {tier:'&#8212; Negative growth', color:'#9E3B30',
    interp:"Revenue declined quarter-over-quarter. Focus on stabilizing the base before evaluating efficiency."};
}

/* ---- mode switching ---- */
function setMode(m) {
  ['point', 'timeline'].forEach(function(x) {
    document.getElementById('panel-' + x).style.display = (x === m) ? 'block' : 'none';
    document.getElementById('tab-' + x).className = 'ger-mode' + (x === m ? ' ger-mode-on' : '');
  });
}

/* ---- point-in-time (the published single-quarter formula, unchanged) ---- */
function loadExample() {
  var ex = {rev_n:100, rev_n1:90, gtm4:20, gtm3:22, gtm2:24, gtm1:26, rnd5:16, rnd4:18};
  for (var k in ex) document.getElementById(k).value = ex[k];
  calcGER();
}

function calcGER() {
  var rev_n = v('rev_n'), rev_n1 = v('rev_n1');
  var annGrowth = (rev_n - rev_n1) * 4;
  var gtmInv = 0.25 * (v('gtm4') + v('gtm3') + v('gtm2') + v('gtm1'));
  var rndInv = 0.25 * (v('rnd5') + v('rnd4'));
  var totalInv = gtmInv + rndInv;

  if (totalInv <= 0 || rev_n <= 0) {
    alert('Please fill in all fields with values greater than zero.');
    return;
  }

  var ratio = annGrowth / totalInv;
  var t = gerTier(ratio);
  var breakeven = ratio > 0 ? (1 / ratio).toFixed(1) : '∞';

  document.getElementById('ger-value').textContent = fmtRatio(ratio);
  document.getElementById('ger-value').style.color = t.color;
  document.getElementById('ger-tier').innerHTML = t.tier;
  document.getElementById('ger-tier').style.color = t.color;
  document.getElementById('ger-breakeven').textContent = ratio > 0 ? 'Break-even: ' + breakeven + ' years at flat renewal' : '';
  document.getElementById('ger-interp').textContent = t.interp;
  document.getElementById('ger-detail').innerHTML =
    'Annualized growth: <strong>' + annGrowth.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'GTM investment (time-weighted): <strong>' + gtmInv.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'R&amp;D investment (time-weighted): <strong>' + rndInv.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'Total investment: <strong>' + totalInv.toFixed(1) + '</strong>';

  // Build the contribution diagram from the eight inputs (window n-5 .. n).
  window.pointStrip = [
    {rev: 0,      gtm: 0,         rnd: v('rnd5')},
    {rev: 0,      gtm: v('gtm4'), rnd: v('rnd4')},
    {rev: 0,      gtm: v('gtm3'), rnd: 0},
    {rev: 0,      gtm: v('gtm2'), rnd: 0},
    {rev: rev_n1, gtm: v('gtm1'), rnd: 0},
    {rev: rev_n,  gtm: 0,         rnd: 0}
  ];
  window.pointLabels = ['n-5', 'n-4', 'n-3', 'n-2', 'n-1', 'n'];
  renderContrib('point', 5);

  document.getElementById('ger-result').style.display = 'block';
}

/* ---- rolling ratio over a continuous quarterly strip ---- */
// strip[i] = {rev, gtm, rnd}. Ratio at quarter i uses i plus the prior five.
function gerStripAt(strip, i) {
  if (i < 5) return null;
  var rev_n = strip[i].rev, rev_p = strip[i - 1].rev;
  var gtmInv = 0.25 * (strip[i - 1].gtm + strip[i - 2].gtm + strip[i - 3].gtm + strip[i - 4].gtm);
  var rndInv = 0.25 * (strip[i - 5].rnd + strip[i - 4].rnd);
  var tot = gtmInv + rndInv;
  if (tot <= 0 || rev_n <= 0 || rev_p <= 0) return null;
  return ((rev_n - rev_p) * 4) / tot;
}

/* ---- modern time-distributed contribution diagram ---- */
// Renders the 6-quarter window (cur-5 .. cur) that produces the ratio at `cur`,
// showing the 25%-per-quarter pull from GTM and R&D aligned to the revenue it drove.
function contributionSVG(strip, cur, labels) {
  var g = [strip[cur - 4].gtm, strip[cur - 3].gtm, strip[cur - 2].gtm, strip[cur - 1].gtm];
  var r = [strip[cur - 5].rnd, strip[cur - 4].rnd];
  var revC = strip[cur].rev, revP = strip[cur - 1].rev;
  var ann = (revC - revP) * 4;
  var gtmInv = 0.25 * (g[0] + g[1] + g[2] + g[3]);
  var rndInv = 0.25 * (r[0] + r[1]);
  var tot = gtmInv + rndInv, ratio = tot > 0 ? ann / tot : 0;

  var W = 760, H = 622, x0 = 118, x1 = 612, n = 6;
  var step = (x1 - x0) / n, bw = Math.min(58, step * 0.6);
  function cx(c) { return x0 + step * (c + 0.5); }
  function fmtM(x) { var a = Math.round(x * 10) / 10; return '$' + (a % 1 === 0 ? a.toFixed(0) : a.toFixed(1)) + 'M'; }
  // Brand data palette: GTM = navy, Revenue = seafoam-teal, R&D = coral.
  var BLUE = '#002975', GREEN = '#2E9C86', RED = '#E8704F', INK = '#1a1a1a', MUT = '#6F6A60';
  // Small text must use AA-capable -deep variants (BRAND.md 2.2); base seafoam/coral stay for graphics.
  var GREEN_TX = '#1F7A66', RED_TX = '#B14A30';

  var s = '<svg class="ger-contrib" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';
  s += '<defs>' +
    '<linearGradient id="cgB" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + BLUE + '" stop-opacity="0.30"/><stop offset="1" stop-color="' + BLUE + '" stop-opacity="0.08"/></linearGradient>' +
    '<linearGradient id="cgG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + GREEN + '"/><stop offset="1" stop-color="' + GREEN + '" stop-opacity="0.6"/></linearGradient>' +
    '<linearGradient id="cgR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + RED + '" stop-opacity="0.30"/><stop offset="1" stop-color="' + RED + '" stop-opacity="0.08"/></linearGradient>' +
    '</defs>';

  s += '<text x="' + (W / 2) + '" y="30" text-anchor="middle" font-size="17" font-weight="700" fill="#1a1a1a">How ' + labels[cur] + ' is built &#8212; Time-Distributed Contribution</text>';
  s += '<text x="' + (W / 2) + '" y="52" text-anchor="middle" font-size="13" fill="' + MUT + '">25% of every quarter of spend feeds the window &#183; Efficiency Ratio = $' + ratio.toFixed(2) + '</text>';

  s += '<text x="20" y="186" font-size="13" font-weight="700" fill="' + BLUE + '">GTM</text>';
  s += '<text x="20" y="320" font-size="13" font-weight="700" fill="' + GREEN_TX + '">Revenue</text>';
  s += '<text x="20" y="498" font-size="13" font-weight="700" fill="' + RED_TX + '">R&amp;D</text>';

  // One shared scale across spend AND revenue so every box is comparable by $;
  // each is bottom-aligned on its lane baseline with a 25% "pull" cap on top.
  var allMax = Math.max(g[0], g[1], g[2], g[3], r[0], r[1], revC, revP, 1), BARMAX = 142;
  function barH(val) { return Math.max(val / allMax * BARMAX, 3); }
  function spendBar(c, baseY, val, col, grad, txt) {
    var h = barH(val), x = cx(c) - bw / 2, y = baseY - h, capH = h * 0.25;
    var o = '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="6" fill="url(#' + grad + ')" stroke="' + col + '" stroke-opacity="0.45"/>';
    o += '<path d="M' + x.toFixed(1) + ' ' + (y + capH).toFixed(1) + ' L' + x.toFixed(1) + ' ' + (y + 6).toFixed(1) + ' Q' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + 6).toFixed(1) + ' ' + y.toFixed(1) + ' L' + (x + bw - 6).toFixed(1) + ' ' + y.toFixed(1) + ' Q' + (x + bw).toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + bw).toFixed(1) + ' ' + (y + 6).toFixed(1) + ' L' + (x + bw).toFixed(1) + ' ' + (y + capH).toFixed(1) + ' Z" fill="' + col + '"/>';
    if (h >= 8) o += '<text x="' + cx(c).toFixed(1) + '" y="' + (y - 5).toFixed(1) + '" text-anchor="middle" font-size="10" font-weight="700" fill="' + txt + '">25%</text>';
    o += '<text x="' + cx(c).toFixed(1) + '" y="' + (baseY + 15).toFixed(1) + '" text-anchor="middle" font-size="11" fill="' + MUT + '">' + fmtM(val) + '</text>';
    return o;
  }
  function bracket(cLo, cHi, y, col, label) {
    var xa = cx(cLo) - bw / 2 - 3, xb = cx(cHi) + bw / 2 + 3;
    var o = '<path d="M' + xa.toFixed(1) + ' ' + (y + 8) + ' L' + xa.toFixed(1) + ' ' + (y + 4) + ' Q' + xa.toFixed(1) + ' ' + y + ' ' + (xa + 4).toFixed(1) + ' ' + y + ' L' + (xb - 4).toFixed(1) + ' ' + y + ' Q' + xb.toFixed(1) + ' ' + y + ' ' + xb.toFixed(1) + ' ' + (y + 4) + ' L' + xb.toFixed(1) + ' ' + (y + 8) + '" fill="none" stroke="' + col + '" stroke-width="1.6"/>';
    var mid = (xa + xb) / 2, tw = label.length * 6.7 + 24;
    o += '<rect x="' + (mid - tw / 2).toFixed(1) + '" y="' + (y - 25) + '" width="' + tw.toFixed(1) + '" height="22" rx="11" fill="' + col + '"/>';
    o += '<text x="' + mid.toFixed(1) + '" y="' + (y - 10) + '" text-anchor="middle" font-size="11.5" font-weight="600" fill="#fff">' + label + '</text>';
    return o;
  }

  // GTM lane bottom-aligned at gB
  var gB = 174, gTop = gB - barH(Math.max(g[0], g[1], g[2], g[3]));
  s += bracket(1, 4, Math.max(gTop - 22, 76), BLUE, 'GTM Investment = ' + fmtM(gtmInv));
  for (var c = 1; c <= 4; c++) s += spendBar(c, gB, g[c - 1], BLUE, 'cgB', BLUE);

  // Revenue lane bottom-aligned at rB (same scale as spend)
  var rB = 388;
  function revBar(c, val, isCur) {
    var h = barH(val), x = cx(c) - bw / 2, y = rB - h;
    var o = '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="7" fill="' + (isCur ? 'url(#cgG)' : '#D6EFE8') + '" stroke="' + GREEN + '" stroke-opacity="' + (isCur ? '0.55' : '0.3') + '"/>';
    o += '<text x="' + cx(c).toFixed(1) + '" y="' + (isCur ? (y + 19) : (y - 7)).toFixed(1) + '" text-anchor="middle" font-size="' + (isCur ? '12' : '10.5') + '" font-weight="' + (isCur ? '700' : '500') + '" fill="' + (isCur ? '#fff' : MUT) + '">' + fmtM(val) + '</text>';
    return o;
  }
  s += revBar(4, revP, false);
  s += revBar(5, revC, true);

  // annualized-growth callout beside the current revenue bar (tracks its top)
  var curTop = rB - barH(revC);
  var gx = cx(5) + bw / 2 + 16, gy = Math.max(curTop, 140), cw = 110;
  s += '<line x1="' + (cx(5) + bw / 2).toFixed(1) + '" y1="' + (gy + 19) + '" x2="' + gx.toFixed(1) + '" y2="' + (gy + 19) + '" stroke="' + GREEN + '" stroke-width="1.4" stroke-dasharray="3 2"/>';
  s += '<rect x="' + gx.toFixed(1) + '" y="' + gy.toFixed(1) + '" width="' + cw + '" height="38" rx="9" fill="#E7F5F0" stroke="' + GREEN + '" stroke-width="1.6"/>';
  s += '<text x="' + (gx + cw / 2).toFixed(1) + '" y="' + (gy + 18).toFixed(1) + '" text-anchor="middle" font-size="14" font-weight="700" fill="' + GREEN_TX + '">' + (ann >= 0 ? '+' : '') + fmtM(ann) + '</text>';
  s += '<text x="' + (gx + cw / 2).toFixed(1) + '" y="' + (gy + 32).toFixed(1) + '" text-anchor="middle" font-size="9.5" fill="' + GREEN_TX + '">Annualized Growth</text>';

  // R&D lane bottom-aligned at dB (same scale)
  var dB = 494, dTop = dB - barH(Math.max(r[0], r[1]));
  s += bracket(0, 1, dTop - 22, RED, 'R&amp;D Investment = ' + fmtM(rndInv));
  for (var c = 0; c <= 1; c++) s += spendBar(c, dB, r[c], RED, 'cgR', RED_TX);

  // timeline axis
  var ty = 530;
  s += '<line x1="' + (x0 - 8) + '" y1="' + ty + '" x2="' + (x1 + 8) + '" y2="' + ty + '" stroke="#D6D1C4" stroke-width="2"/>';
  for (var c = 0; c < n; c++) {
    var isCur = (c === 5);
    s += '<line x1="' + cx(c).toFixed(1) + '" y1="' + (ty - 4) + '" x2="' + cx(c).toFixed(1) + '" y2="' + (ty + 4) + '" stroke="#D6D1C4" stroke-width="1.5"/>';
    s += '<text x="' + cx(c).toFixed(1) + '" y="' + (ty + 20) + '" text-anchor="middle" font-size="11.5" font-weight="' + (isCur ? '700' : '400') + '" fill="' + (isCur ? '#1a1a1a' : MUT) + '">' + labels[cur - 5 + c] + '</text>';
  }

  // formula pill
  var label2 = (ann >= 0 ? '+' : '') + fmtM(ann) + ' / ( ' + fmtM(gtmInv) + ' + ' + fmtM(rndInv) + ' ) = $' + ratio.toFixed(2);
  var fw = label2.length * 7.3 + 40, fx = W / 2 - fw / 2, fy = 580;
  var pill = '<tspan fill="' + GREEN_TX + '" font-weight="700">' + (ann >= 0 ? '+' : '') + fmtM(ann) + '</tspan> &#247; ( ' +
    '<tspan fill="' + BLUE + '" font-weight="700">' + fmtM(gtmInv) + '</tspan> + ' +
    '<tspan fill="' + RED_TX + '" font-weight="700">' + fmtM(rndInv) + '</tspan> ) = ' +
    '<tspan fill="' + INK + '" font-weight="700">$' + ratio.toFixed(2) + '</tspan>';
  s += '<rect x="' + fx.toFixed(1) + '" y="' + fy + '" width="' + fw.toFixed(1) + '" height="34" rx="10" fill="#ffffff" stroke="#D6D1C4" stroke-width="1.6"/>';
  s += '<text x="' + (W / 2).toFixed(1) + '" y="' + (fy + 22) + '" text-anchor="middle" font-size="14.5">' + pill + '</text>';

  s += '</svg>';
  return s;
}

function renderContrib(prefix, idx) {
  var strip = window[prefix + 'Strip'], labels = window[prefix + 'Labels'];
  if (!strip || idx < 5) return;
  document.getElementById(prefix + '-contrib').innerHTML = contributionSVG(strip, idx, labels);
}

/* ---- Timeline mode: bounded -2 .. +2 measured periods ---- */
var TL = {back: 2, fwd: 2};
function tlCurIndex() { return 5 + TL.back; }      // index of the current quarter
function tlRows() { return TL.back + TL.fwd + 6; } // 5 lookback + measured periods
function tlLabel(i) {
  var d = i - tlCurIndex();
  return d === 0 ? 'n' : 'n' + (d < 0 ? ('-' + (-d)) : ('+' + d));
}

function renderTL() {
  var n = tlRows(), cur = tlCurIndex(), html = '';
  for (var i = 0; i < n; i++) {
    var proj = i > cur, lookback = i < 5;
    var tag = (i === cur) ? ' <span style="color:var(--accent);font-size:11px;font-weight:600;">current</span>'
            : (proj ? ' <span style="color:var(--muted);font-size:11px;">proj</span>'
            : (lookback ? ' <span style="color:var(--muted);font-size:11px;">lookback</span>' : ''));
    var div = (i === cur + 1) ? ' style="border-top:1px dashed var(--line);padding-top:8px;"' : '';
    html +=
      '<div class="ger-grid-3 ' + (proj ? 'qrow-proj' : '') + '"' + div + '>' +
        '<span class="qlabel">' + tlLabel(i) + tag + '</span>' +
        '<input class="ger-in" id="tl_rev_' + i + '" type="number" min="0" step="any" placeholder="Rev">' +
        '<input class="ger-in" id="tl_gtm_' + i + '" type="number" min="0" step="any" placeholder="GTM">' +
        '<input class="ger-in" id="tl_rnd_' + i + '" type="number" min="0" step="any" placeholder="R&amp;D">' +
      '</div>';
  }
  document.getElementById('tl-rows').innerHTML = html;
}

function tlGather() {
  var n = tlRows(), cur = tlCurIndex(), m = {};
  for (var i = 0; i < n; i++) {
    m[i - cur] = [
      (document.getElementById('tl_rev_' + i) || {}).value || '',
      (document.getElementById('tl_gtm_' + i) || {}).value || '',
      (document.getElementById('tl_rnd_' + i) || {}).value || ''
    ];
  }
  return m;
}
function tlRestore(m) {
  var n = tlRows(), cur = tlCurIndex();
  for (var i = 0; i < n; i++) {
    var k = m[i - cur]; if (!k) continue;
    document.getElementById('tl_rev_' + i).value = k[0];
    document.getElementById('tl_gtm_' + i).value = k[1];
    document.getElementById('tl_rnd_' + i).value = k[2];
  }
}
function stepTL(which, delta) {
  var keep = tlGather();
  if (which === 'back') TL.back = Math.max(0, Math.min(2, TL.back + delta));
  else TL.fwd = Math.max(0, Math.min(2, TL.fwd + delta));
  document.getElementById('tl-back').textContent = TL.back;
  document.getElementById('tl-fwd').textContent = TL.fwd;
  renderTL(); tlRestore(keep);
}

function calcTimeline() {
  var n = tlRows(), cur = tlCurIndex(), strip = [], labels = [];
  for (var i = 0; i < n; i++) {
    strip.push({rev: v('tl_rev_' + i), gtm: v('tl_gtm_' + i), rnd: v('tl_rnd_' + i)});
    labels.push(tlLabel(i));
  }
  var base = [];
  for (var i = 0; i < n; i++) { var r = gerStripAt(strip, i); if (r !== null) base.push({i: i, y: r}); }
  if (base.length === 0) {
    alert('Fill in the quarters (Revenue, GTM and R&D, each greater than zero) so the earliest measured quarter has its six-quarter lookback.');
    return;
  }

  // Start the chart at the first measured quarter — skip the empty lookback columns.
  var startIdx = base[0].i;
  var chartLabels = labels.slice(startIdx);
  var series = [{pts: base.map(function(p) { return {i: p.i - startIdx, y: p.y}; }), color: '#002975', dotsTier: true, role: 'base'}];

  var first = base[0], last = base[base.length - 1];
  var curY = gerStripAt(strip, cur); if (curY === null) curY = last.y;
  var ct = gerTier(curY);
  var dir = last.y > first.y + 0.02 ? 'improving' : (last.y < first.y - 0.02 ? 'declining' : 'holding steady');
  var summary =
    '<div style="display:flex;gap:22px;flex-wrap:wrap;align-items:baseline;margin-top:18px;">' +
      '<div><p class="qhead" style="margin:0 0 2px;">Current quarter (n)</p>' +
      '<p style="font-family:var(--font-head);font-size:32px;font-weight:700;letter-spacing:-0.02em;margin:0;color:' + ct.color + ';">' + fmtRatio(curY) + '</p></div>' +
      '<div style="flex:1;min-width:220px;">' +
      '<p style="font-weight:600;margin:0 0 2px;color:' + ct.color + ';">' + ct.tier + '</p>' +
      '<p style="font-size:14px;color:var(--muted);margin:0;">Across ' + base.length + ' measured quarter' + (base.length > 1 ? 's' : '') +
      ' the ratio is <strong>' + dir + '</strong> (' + fmtRatio(first.y) + ' &rarr; ' + fmtRatio(last.y) + '). ' +
      (TL.fwd > 0 ? 'Quarters past n are your projections. ' : '') +
      'Shaded bands are the benchmark tiers; the dashed grey line is $1.00 break-even.</p></div></div>';

  window.tlStrip = strip; window.tlLabels = labels;
  var pick = '<label class="qhead" style="margin-right:8px;">Explain quarter</label>' +
    '<select id="tl-pick" class="ger-in" style="width:auto;display:inline-block;padding:7px 10px;">';
  base.forEach(function(p) { pick += '<option value="' + p.i + '"' + (p.i === cur ? ' selected' : '') + '>' + labels[p.i] + '</option>'; });
  pick += '</select>';

  var box = document.getElementById('tl-result');
  box.innerHTML = buildChart(chartLabels, series, chartLabels.length) + summary +
    '<div style="margin-top:24px;padding-top:18px;border-top:1px dashed var(--line);">' +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;">' +
        '<p style="font-weight:600;font-size:15px;margin:0;color:var(--ink);">How a quarter is built</p>' + pick +
      '</div>' +
      '<p style="font-size:13px;color:var(--muted);margin:0 0 10px;">Pick any measured quarter to see the 25%-per-quarter pull behind that point.</p>' +
      '<div class="ger-contrib-wrap"><div id="tl-contrib" style="min-width:560px;"></div></div>' +
    '</div>';
  box.style.display = 'block';
  document.getElementById('tl-pick').addEventListener('change', function() { renderContrib('tl', parseInt(this.value, 10)); });
  renderContrib('tl', cur);
}

/* ---- inline SVG line chart with benchmark tier bands ---- */
function buildChart(labels, series, n) {
  var W = 660, H = 330, mL = 44, mR = 64, mT = 16, mB = 42;
  var pw = W - mL - mR, ph = H - mT - mB;

  var ys = [0.5, 0.7, 1.0, 1.2];
  series.forEach(function(s) { s.pts.forEach(function(p) { ys.push(p.y); }); });
  var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
  var pad = (ymax - ymin) * 0.12 || 0.1; ymin -= pad; ymax += pad;

  function X(i) { return mL + (n <= 1 ? pw / 2 : (i / (n - 1)) * pw); }
  function Y(y) { return mT + (ymax - y) / (ymax - ymin) * ph; }
  function band(yTop, yBot, fill) {
    var y0 = Y(Math.min(yTop, ymax)), y1 = Y(Math.max(yBot, ymin));
    if (y1 - y0 < 0.5) return '';
    return '<rect x="' + mL + '" y="' + y0.toFixed(1) + '" width="' + pw + '" height="' + (y1 - y0).toFixed(1) + '" fill="' + fill + '"/>';
  }
  function tlab(yc, txt, col) {
    var yy = Y(yc); if (yy < mT + 7 || yy > mT + ph - 2) return '';
    return '<text x="' + (mL + pw + 6) + '" y="' + (yy + 3).toFixed(1) + '" font-size="10" fill="' + col + '">' + txt + '</text>';
  }
  function polyline(pts, color, dash) {
    if (pts.length < 2) return '';
    var d = pts.map(function(p) { return X(p.i).toFixed(1) + ',' + Y(p.y).toFixed(1); }).join(' ');
    return '<polyline points="' + d + '" fill="none" stroke="' + color + '" stroke-width="2.5"' + (dash ? ' stroke-dasharray="6 4"' : '') + '/>';
  }

  var s = '<svg class="ger-chart" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';
  s += band(ymax, 1.20, '#E3F2EC') + band(1.20, 0.70, '#EDF5F1') + band(0.70, 0.50, '#FAF1E1') + band(0.50, ymin, '#F9E8E3');
  s += tlab((Math.min(ymax, 1.7) + 1.20) / 2, 'Elite', '#002975');
  s += tlab(0.95, 'Strong', '#002975');
  s += tlab(0.60, 'Typical', '#9A6B12');
  s += tlab((0.50 + Math.max(ymin, -0.5)) / 2, 'Below', '#9E3B30');

  // break-even reference + axis baseline
  var yb = Y(1.0);
  s += '<line x1="' + mL + '" y1="' + yb.toFixed(1) + '" x2="' + (mL + pw) + '" y2="' + yb.toFixed(1) + '" stroke="#B8B1A4" stroke-width="1" stroke-dasharray="4 3"/>';
  s += '<text x="' + (mL + 3) + '" y="' + (yb - 4).toFixed(1) + '" font-size="9" fill="#6F6A60">$1.00 break-even</text>';
  s += '<line x1="' + mL + '" y1="' + (mT + ph) + '" x2="' + (mL + pw) + '" y2="' + (mT + ph) + '" stroke="#E4E0D6"/>';
  // y endpoints
  s += '<text x="' + (mL - 6) + '" y="' + (Y(ymax) + 3).toFixed(1) + '" font-size="9" fill="#6F6A60" text-anchor="end">' + fmtRatio(ymax) + '</text>';
  s += '<text x="' + (mL - 6) + '" y="' + (Y(ymin) + 3).toFixed(1) + '" font-size="9" fill="#6F6A60" text-anchor="end">' + fmtRatio(ymin) + '</text>';
  // x labels
  for (var i = 0; i < n; i++) {
    s += '<text x="' + X(i).toFixed(1) + '" y="' + (mT + ph + 16) + '" font-size="10" fill="#6F6A60" text-anchor="middle">' + labels[i] + '</text>';
  }

  // projection uncertainty band (between up and dn series)
  var up = null, dn = null;
  series.forEach(function(ser) { if (ser.role === 'up') up = ser; if (ser.role === 'dn') dn = ser; });
  if (up && dn && up.pts.length && dn.pts.length) {
    var poly = '';
    up.pts.forEach(function(p) { poly += X(p.i).toFixed(1) + ',' + Y(p.y).toFixed(1) + ' '; });
    for (var k = dn.pts.length - 1; k >= 0; k--) { poly += X(dn.pts[k].i).toFixed(1) + ',' + Y(dn.pts[k].y).toFixed(1) + ' '; }
    s += '<polygon points="' + poly.trim() + '" fill="#D8D3C8" opacity="0.45"/>';
  }

  // lines
  series.forEach(function(ser) {
    if (ser.splitAt !== undefined) {
      s += polyline(ser.pts.filter(function(p) { return p.i <= ser.splitAt; }), ser.color, false);
      s += polyline(ser.pts.filter(function(p) { return p.i >= ser.splitAt; }), ser.color, true);
    } else {
      s += polyline(ser.pts, ser.color, ser.dash);
    }
  });

  // dots
  series.forEach(function(ser) {
    if (ser.role === 'up' || ser.role === 'dn') {
      ser.pts.forEach(function(p) { s += '<circle cx="' + X(p.i).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '" r="2.5" fill="' + ser.color + '"/>'; });
    } else {
      ser.pts.forEach(function(p) {
        s += '<circle cx="' + X(p.i).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '" r="4.5" fill="' + gerTier(p.y).color + '" stroke="#fff" stroke-width="1.5"/>';
      });
    }
  });

  s += '</svg>';
  return s;
}

function loadTimelineExample() {
  TL.back = 2; TL.fwd = 2;
  document.getElementById('tl-back').textContent = TL.back;
  document.getElementById('tl-fwd').textContent = TL.fwd;
  renderTL();
  // n-7 .. n+2: revenue compounding ~7%/qtr, spend disciplined; last two quarters projected.
  var rev = [560, 602, 648, 690, 726, 762, 800, 840, 885, 932];
  var gtm = [120, 128, 135, 141, 148, 156, 165, 172, 180, 188];
  var rnd = [150, 150, 162, 168, 176, 184, 196, 205, 214, 224];
  for (var i = 0; i < tlRows(); i++) {
    document.getElementById('tl_rev_' + i).value = rev[i];
    document.getElementById('tl_gtm_' + i).value = gtm[i];
    document.getElementById('tl_rnd_' + i).value = rnd[i];
  }
  calcTimeline();
}

// Build the timeline table up front so its rows exist before the user switches tabs.
renderTL();
</script>"""
    return HTMLResponse(_page("The Growth Engine Ratio—Brian Weisberg", "Thought Leadership", body, role=_role(request)))


@app.get("/finops-ai-hackathon", response_class=HTMLResponse)
def finops_ai_hackathon(request: Request):
    body = """<div class="page" style="max-width:820px;">
<style>
  .fah-pull{background:var(--navy-wash);border-left:3px solid var(--navy);border-radius:0 10px 10px 0;padding:18px 24px;margin:28px 0;}
  .fah-pull p{margin:0;font-size:17px;font-style:italic;line-height:1.55;color:var(--ink);}
  .fah-callout{background:var(--seafoam-wash);border-top:2px solid var(--seafoam-mid);border-radius:0 0 10px 10px;padding:20px 24px;margin:28px 0;}
  .fah-callout-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--seafoam-deep);margin-bottom:10px;}
  .fah-callout p,.fah-callout li{font-size:15px;color:var(--ink-soft);margin-bottom:6px;}
  .fah-callout ul{padding-left:20px;margin:0;}
  .fah-callout li{margin-bottom:5px;}
  .fah-warn{background:var(--coral-wash);border-top:2px solid var(--coral);border-radius:0 0 10px 10px;padding:18px 22px;margin:24px 0;}
  .fah-warn-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--coral-deep);margin-bottom:8px;}
  .fah-warn p{font-size:14px;color:var(--ink-soft);margin:0;}
  /* Phase track */
  .fah-track{display:flex;flex-direction:column;gap:0;margin:28px 0;}
  .fah-step{display:flex;gap:18px;position:relative;}
  .fah-step:not(:last-child)::after{content:"";position:absolute;left:17px;top:40px;width:2px;bottom:-2px;background:var(--line-strong);}
  .fah-num{width:36px;height:36px;border-radius:50%;background:var(--navy);color:#fff;font-family:var(--font-head);font-size:14px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;z-index:1;}
  .fah-body{padding-bottom:26px;flex:1;}
  .fah-body h3{font-family:var(--font-head);font-size:16px;font-weight:600;color:var(--ink);margin:4px 0 6px;}
  .fah-body p{font-size:15px;color:var(--ink-soft);margin-bottom:8px;line-height:1.6;}
  .fah-tag{display:inline-block;font:600 11px var(--font-body);letter-spacing:.05em;color:var(--muted);border:1px solid var(--line-strong);border-radius:5px;padding:2px 8px;margin-top:4px;}
  /* 2x2 Matrix */
  .fah-matrix{margin:28px 0;}
  .fah-matrix-label{text-align:center;font:700 11px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;}
  .fah-matrix-grid{display:grid;grid-template-columns:28px 1fr 1fr;grid-template-rows:1fr 1fr 28px;gap:0;height:280px;border:1px solid var(--line-strong);border-radius:10px;overflow:hidden;}
  .fah-m-y{writing-mode:vertical-rl;transform:rotate(180deg);font:700 10px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);text-align:center;grid-row:1/3;grid-column:1;display:flex;align-items:center;justify-content:center;background:var(--surface-2);}
  .fah-m-x{font:700 10px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);text-align:center;grid-row:3;grid-column:2/4;display:flex;align-items:center;justify-content:center;background:var(--surface-2);}
  .fah-q{padding:16px 18px;font-size:13px;line-height:1.45;display:flex;flex-direction:column;border:1px solid var(--line);}
  .fah-q-label{font:700 10px var(--font-body);letter-spacing:.07em;text-transform:uppercase;margin-bottom:6px;}
  .fah-q-star{background:var(--navy);color:#fff;}.fah-q-star .fah-q-label{color:rgba(255,255,255,.75);}
  .fah-q-b{background:var(--navy-wash);color:var(--ink-soft);}.fah-q-b .fah-q-label{color:var(--muted);}
  .fah-q-c{background:#fff;color:var(--muted);}.fah-q-c .fah-q-label{color:var(--line-strong);}
  /* Verdict chips */
  .fah-verdicts{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0;}
  .fah-verdict{padding:10px 18px;border-radius:8px;font-size:14px;}
  .fah-v-ship{background:var(--navy);color:#fff;}
  .fah-v-iterate{background:var(--coral-wash);color:var(--coral-deep);border:1px solid var(--coral-light);}
  .fah-v-park{background:var(--surface-2);color:var(--muted);border:1px solid var(--line-strong);}
  .fah-v-label{font:700 11px var(--font-body);letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px;}
  /* Resource links */
  .fah-resources{display:flex;flex-direction:column;gap:0;margin:20px 0;border-top:1px solid var(--line-strong);}
  .fah-resource{display:flex;align-items:flex-start;gap:14px;padding:14px 4px;text-decoration:none;color:inherit;border-bottom:1px solid var(--line);}
  .fah-resource:hover{background:var(--navy-wash);}
  .fah-r-icon{font-size:18px;flex-shrink:0;margin-top:1px;}
  .fah-r-title{font:600 15px var(--font-head);color:var(--navy);margin-bottom:2px;}
  .fah-r-desc{font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;}
  .fah-r-src{font:700 10px var(--font-body);letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin-top:3px;}
  /* Notion template box */
  .fah-template{background:#fff;border:1px solid var(--line-strong);border-radius:12px;padding:26px 30px;margin:28px 0;}
  .fah-template-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--navy);margin-bottom:16px;display:flex;align-items:center;gap:8px;}
  .fah-template h3{font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin:18px 0 6px;}
  .fah-template h3:first-of-type{margin-top:0;}
  .fah-template p,.fah-template li{font-size:14px;color:var(--ink-soft);}
  .fah-template ul{padding-left:18px;margin:0 0 8px;}
  .fah-template li{margin-bottom:4px;}
  /* Tier strip */
  .fah-tiers{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin:22px 0;border:1px solid var(--line-strong);border-radius:10px;overflow:hidden;}
  .fah-tier{padding:18px 16px;}
  .fah-tier:not(:last-child){border-right:1px solid var(--line);}
  .fah-tier-title{font-family:var(--font-head);font-size:15px;font-weight:700;margin-bottom:6px;}
  .fah-tier p{font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;}
  .fah-t-ship{background:var(--navy);}.fah-t-ship .fah-tier-title{color:#fff;}.fah-t-ship p{color:rgba(255,255,255,.8);}
  .fah-t-iter{background:var(--coral-wash);}.fah-t-iter .fah-tier-title{color:var(--coral-deep);}
  .fah-t-park{background:var(--surface-2);}.fah-t-park .fah-tier-title{color:var(--muted);}
  /* Sailboat SVG motif */
  .fah-motif{text-align:center;margin:32px 0 24px;}
  @media(max-width:640px){
    .fah-matrix-grid{height:220px;}
    .fah-tiers{grid-template-columns:1fr;}
    .fah-tier:not(:last-child){border-right:none;border-bottom:1px solid var(--line);}
    .fah-verdicts{flex-direction:column;}
  }
</style>

<p style="font-size:13px;color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em;">Playbook</p>
<h1 style="margin:0 0 8px;">Sail, Don't Row</h1>
<p style="font-size:17px;font-style:italic;color:var(--ink-soft);margin:0 0 6px;line-height:1.5;">A playbook for running an AI hackathon with your finance team</p>
<p style="color:var(--muted);font-size:14px;margin:0 0 36px;">By Brian Weisberg &middot; June 2026</p>

<p>There are two ways to approach the AI moment in finance. The first is to row harder: one-off solutions, manual handoffs, each person finding their own tool at their own pace. Exhausting. Doesn't scale. The second is to sail: build the infrastructure deliberately, rig it carefully, and let the conditions do the work. The difference isn't capability. It's intention.</p>

<p>A hackathon is how a finance team learns to sail. It creates protected time and a low-stakes space to learn something hard together—as a team, where nobody has to already know the answer. The builds you ship at the end are real, but they're a byproduct. The point is the skill that stays when everyone goes home.</p>

<p>I've run one of these with my own finance and ops team, and this is the format distilled—what worked, why it worked, and how to run it yourself.</p>

<div class="fah-pull"><p>"Before any piece of work, two questions: Is this worth doing? And am I sailing or rowing—is there a template, an automation, a repeatable version that keeps this from being a one-off someone owns forever?"</p></div>

<h2>Why a hackathon—and why now</h2>
<p>AI adoption in finance doesn't happen on its own. It gets crowded out by the close, the board deck, the forecast update. There's always something more urgent. Left to find the time on their own, most teams never do.</p>

<p>A hackathon fixes that by force. It carves out protected time and makes <em>exploring together</em> the actual assignment. The format works for three reasons:</p>
<ul style="padding-left:22px;margin:0 0 20px;">
  <li style="margin-bottom:10px;"><strong>Psychological safety.</strong> When everyone is learning at the same time, in the same room, there's no expert to defer to and no reason to hide. Half-formed ideas get air.</li>
  <li style="margin-bottom:10px;"><strong>Time-boxing as a feature.</strong> The constraint—ninety minutes to build something shippable—focuses effort better than a two-week sprint with no end in sight. Done is better than perfect.</li>
  <li style="margin-bottom:10px;"><strong>Compounding returns.</strong> A team that has learned something together learns faster next time. The first hackathon is the hardest. Run it annually and it becomes a flywheel.</li>
</ul>

<p>The goal isn't to automate the whole finance function. It's to close the gap between your team's potential and its current velocity—on purpose, together, in a way that compounds.</p>

<h2>The method behind it: design thinking</h2>
<p>Before the mechanics, the philosophy. The prioritization format I use—post-its, dot stickers, a 2×2—isn't a team-building exercise. It's the application of a specific method: design thinking.</p>

<p>Design thinking is a problem-solving approach that starts with the people experiencing the problem, not with the solution. It works in two modes:</p>

<div class="fah-pull"><p>"Diverge first. Everyone generates ideas independently, without talking. Then converge. The separation matters—if you skip the silent step and just go around the room, the first voice anchors every other answer."</p></div>

<p>The other half is the ground rule going in: <strong>no bad ideas.</strong> No judgment, no evaluating while generating. The only requirement is a clear persona and use case: a real person with a real problem, not a vague wish. That's what makes people comfortable putting the half-formed thing on the wall—which is exactly where the good ones tend to start.</p>

<p>This method started in product design but works anywhere you need a group to surface and prioritize ideas without the usual political drag. A few examples of what it looks like at scale:</p>

<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line-strong);border:1px solid var(--line-strong);border-radius:10px;overflow:hidden;margin:20px 0;">
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:700 10px var(--font-body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Airbnb</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">Early growth</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Bookings were flat. They visited hosts, looked at listings, and realized photos were terrible. One non-technical intervention. The insight came from observing the problem directly.</p>
  </div>
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:700 10px var(--font-body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">IBM</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">Enterprise shift</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Flipped the order: start with what the customer needs, then figure out the technology. Built internal design studios. Retrained thousands. Outputs improved. So did relationships.</p>
  </div>
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:700 10px var(--font-body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Google</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">20% rule</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Structured diverge time with real stakes attached. Gmail, Google News, and AdSense all started there. The roadmap never would have produced them.</p>
  </div>
</div>

<p style="font-size:14px;color:var(--muted);margin:0 0 8px;">Three resources worth sending as pre-reading before you run this:</p>
<div class="fah-resources">
  <a href="https://www.nngroup.com/articles/design-thinking/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">📖</span>
    <div><div class="fah-r-title">Design Thinking 101</div><p class="fah-r-desc">Covers the six phases and why this isn't just a brainstorming session with a fancier name.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://www.nngroup.com/articles/diverge-converge/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">🔀</span>
    <div><div class="fah-r-title">The Diverge-and-Converge Technique</div><p class="fah-r-desc">Why you split ideation from prioritization, and what goes wrong when you don't.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://www.nngroup.com/articles/dot-voting/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">🟢</span>
    <div><div class="fah-r-title">Dot Voting</div><p class="fah-r-desc">How many dots to give, when to rerun a vote, and the failure modes to watch for—especially the person who campaigns out loud before the stickers go up.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://designthinking.ideo.com/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">💡</span>
    <div><div class="fah-r-title">IDEO Design Thinking</div><p class="fah-r-desc">The original source—where the method came from, with toolkits and examples across industries.</p><div class="fah-r-src">IDEO</div></div>
  </a>
</div>

<h2>Before you arrive: the setup</h2>
<p>The single biggest mistake in running a hackathon is walking into the room cold. If the first thing you do is ask "so what should we build?"—you'll spend half your time generating half-baked ideas and the other half convincing people to try something. Do the intake work before you're in a room together.</p>

<div class="fah-callout">
  <div class="fah-callout-title">Async intake — 1–2 weeks before the event</div>
  <ul>
    <li>Set up a simple intake form (Notion works well) with these fields: <strong>problem statement</strong> (one sentence), <strong>who it hurts</strong>, <strong>type</strong> (automation / visibility / missing skill / analysis), <strong>impact and feasibility</strong> (a first guess), and <strong>definition of done</strong>.</li>
    <li>Ask specific questions. <em>"What do you do every week that feels like copy-paste?"</em> gets better answers than <em>"What problems do you have?"</em></li>
    <li>Make it frictionless. Let people dump free text if that's easier—you or an AI agent can structure it afterward. The goal is honest input, not a polished pitch.</li>
    <li>Optional: use AI to auto-fill the structured fields from each submission, then have people review and correct. That task itself is a small AI adoption moment.</li>
  </ul>
</div>

<p>Also before the event: get your integrations connected. If you're building with Claude or another AI assistant, make sure it's linked to the systems you actually use—NetSuite, Notion, Google Drive, Slack. Spending build time on setup is demoralizing. Arrive ready to build.</p>

<p>Consider sending the design thinking pre-reads (linked above) a few days out. Not required, but teams that arrive with the method in their heads move faster once they're in the room.</p>

<h2>Day zero: from problems to priorities</h2>
<p>This is the framing session—the day (or half-day) before the build. Its job is to turn a backlog of submitted problems into a ranked shortlist of sprint candidates. Here's the full sequence:</p>

<div class="fah-track">
  <div class="fah-step">
    <div class="fah-num">1</div>
    <div class="fah-body">
      <h3>Transcribe to post-its</h3>
      <p>During a break, the facilitator writes each submitted problem onto a physical sticky note—one problem per note. This forces a human edit pass. You spot duplicates, catch problems that are really the same thing framed twice, and produce something the whole room can see simultaneously. Keep laptops closed for the rest of this session.</p>
      <span class="fah-tag">⏱ 15–20 min · facilitator only · done during a break</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">2</div>
    <div class="fah-body">
      <h3>Live additions + clustering</h3>
      <p>Give the room a few minutes to add anything not submitted async. Then cluster: pull duplicate or overlapping stickies together before voting. Facilitator-led, fast—group what's obviously similar and move on. Don't debate the clusters. Debate costs time and anchors thinking before the vote.</p>
      <span class="fah-tag">⏱ 10–15 min · whole team</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">3</div>
    <div class="fah-body">
      <h3>Silent dot voting</h3>
      <p>Everyone gets five dot stickers. You can stack all five on one idea or spread them across five. <strong>No talking while voting.</strong> Silence prevents the loudest voice from anchoring the group—the most common failure mode in group prioritization. Once votes are tallied, log totals back into your intake database so the prioritization is permanent.</p>
      <span class="fah-tag">⏱ 5–10 min · whole team · silence required</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">4</div>
    <div class="fah-body">
      <h3>The 2×2: value vs. effort</h3>
      <p>With vote tallies as a guide, place stickies on a 2×2 grid together as a team. <strong>Value</strong> on the vertical axis. <strong>Effort</strong> on the horizontal—and effort means all-in effort: time, skill required, data access, dependencies. Facilitator guides, team places. The conversation happens around placement, not around lobbying for ideas.</p>
    </div>
  </div>
</div>

<div class="fah-matrix">
  <div class="fah-matrix-label">Value vs. Effort — how to sort your ideas</div>
  <div class="fah-matrix-grid">
    <div class="fah-m-y">Value &uarr;</div>
    <div class="fah-q fah-q-star">
      <div class="fah-q-label">&#10022; Sprint targets</div>
      <div style="font-size:13px;line-height:1.45;">High value, achievable in 90 min. These are your hackathon finalists. Pick 3–4, pair up, build.</div>
    </div>
    <div class="fah-q fah-q-b">
      <div class="fah-q-label">Scope &amp; own</div>
      <div style="font-size:13px;line-height:1.45;color:var(--ink-soft);">High value, high effort. Real initiatives—not hackathon material. Each gets a named owner and goes on the roadmap.</div>
    </div>
    <div class="fah-q fah-q-c">
      <div class="fah-q-label" style="color:var(--line-strong);">Intentionally skip</div>
      <div style="font-size:13px;line-height:1.45;color:var(--line-strong);">Low value, low effort. Name it explicitly. Agree to leave it alone.</div>
    </div>
    <div class="fah-q fah-q-c">
      <div class="fah-q-label" style="color:var(--line-strong);">Intentionally skip</div>
      <div style="font-size:13px;line-height:1.45;color:var(--line-strong);">Low value, high effort. Clear no.</div>
    </div>
    <div class="fah-m-x">Effort &rarr;</div>
  </div>
</div>

<div class="fah-warn">
  <div class="fah-warn-title">Name what you're skipping</div>
  <p>Things that "fall off the list" have a way of coming back. Things you've explicitly decided to skip don't. For every idea below the line: name it, say it out loud, agree to leave it alone. <em>"Intentionally skipping"</em> and <em>"fell off the list"</em> are not the same thing.</p>
</div>

<div class="fah-track" style="margin-top:24px;">
  <div class="fah-step">
    <div class="fah-num">5</div>
    <div class="fah-body">
      <h3>Two final cuts: when, and whether it's ready</h3>
      <p>Sort the upper-right survivors two ways. First, <strong>timing:</strong> Build (do it at the hackathon), Later (worth doing, not this week), Even Later (needs scoping first). Second, <strong>readiness:</strong> can you touch this right now, or does it have a dependency, a data question, an unknown to resolve? A high-value idea that isn't ready to build isn't a sprint candidate—it's a scoping task. Naming that distinction keeps you honest.</p>
      <span class="fah-tag">⏱ 10–15 min · whole team</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">6</div>
    <div class="fah-body">
      <h3>Assign pairs</h3>
      <p>Match people to Build-ready ideas. Pairs, not solo work—two people per build keeps momentum up when one gets stuck. Assign 1–2 floaters (ideally including yourself if you're the leader) who stay unattached and circulate to unblock teams during the sprint.</p>
      <span class="fah-tag">⏱ 5 min · facilitator-led</span>
    </div>
  </div>
</div>

<h2>Protect the gap: inspire, sleep, build</h2>
<p>Here's the sequencing decision that separates a good hackathon from a great one: <strong>don't build on the framing day.</strong></p>

<p>The framing session is dense with new thinking—a full backlog processed, clustered, voted on, and prioritized. Ending there—inspired rather than rushed—gives that thinking time to settle. People go home with a problem in their head. They think about it in the shower. They wake up with the approach half-formed. That overnight processing is doing real work.</p>

<div class="fah-pull"><p>"Inspire → Sleep → Build. That's the sequence. The gap between the framing day and the build day isn't scheduling slack. It's part of the method."</p></div>

<p>Add one more step on the morning of the build day before anyone opens a laptop: <strong>15–20 minutes of inspiration.</strong> Show examples of what other finance teams have actually built with AI. Real demos, not slides. Actual workflows someone is using. Then—and this is the move worth stealing—clear the votes and run a second idea-generation round from scratch. The second round is almost always better than the first. People arrive with new angles, sharper problem statements, and sometimes a completely different sense of what they want to build.</p>

<h2>The build day</h2>
<p>The build sprint is simple by design. Complexity is the enemy of shipping.</p>

<div class="fah-callout">
  <div class="fah-callout-title">Build day structure</div>
  <ul>
    <li><strong>Morning reboot (15–20 min):</strong> Inspiration videos, second idea-generation round, confirm pairs and targets.</li>
    <li><strong>Sprint #1 (90 min):</strong> Pairs build. Floaters circulate. No whole-group check-ins until time's called—mid-sprint interruptions break flow.</li>
    <li><strong>Optional midpoint (45 min in):</strong> 5-minute pulse check per team. Not a demo—just calibration. Are you stuck? Do you need to scope down?</li>
    <li><strong>Demos + verdicts:</strong> Regroup as a full team. Each pair demos what they built or learned. 10 minutes per team, 2 minutes for the verdict decision. Every demo gets a verdict and a named owner before the next team starts.</li>
  </ul>
</div>

<p>The constraint—90 minutes—is the point. It forces scope decisions early. A team that's trying to build the perfect reconciliation engine will fail. A team that's trying to build a working prototype of one slice of that engine will ship something. "Done enough to demo" is the bar.</p>

<p>What floaters actually do: when a pair is stuck on a tool behavior, a data question, or scope creep, the floater doesn't solve the problem for them. They ask one question: <em>"What's the smallest thing you could build that would prove this works?"</em> That's usually enough to unblock.</p>

<h2>Closing with verdicts and owners</h2>
<p>The close is where most hackathons fail. Teams demo, everyone claps, and then... nothing. The builds sit in a Notion database for three months and quietly become shelf-ware. What prevents that is a discipline: <strong>every demo gets a verdict and a named owner before the room empties.</strong></p>

<div class="fah-tiers">
  <div class="fah-tier fah-t-ship">
    <div class="fah-tier-title">Ship</div>
    <p>Push it to production now. It's working, it's useful, it's ready. Assign an owner whose job is to not let it die.</p>
  </div>
  <div class="fah-tier fah-t-iter">
    <div class="fah-tier-title">Iterate</div>
    <p>Another pass before it's ready. Assign an owner and a target date. <em>Iterate without a date is just Park with extra steps.</em></p>
  </div>
  <div class="fah-tier fah-t-park">
    <div class="fah-tier-title">Park</div>
    <p>Not the right moment—but documented for later. This is a legitimate verdict. Honor it by writing down why.</p>
  </div>
</div>

<p>The Park verdict deserves more credit than it gets. It's not failure—it's intellectual honesty. Naming why something isn't ready (wrong timing, missing data, dependency on something else) is more useful than letting it die quietly. A well-documented Park can become a Ship six months later when the conditions change.</p>

<div class="fah-pull"><p>"The goal is at least one thing in production before anyone gets on a plane. Aim for that. It changes the energy of the room and sets the bar for everything that follows."</p></div>

<h2>The operating system: a Notion setup that compounds</h2>
<p>The post-its get the attention. They're not what makes this work. What makes it work is the underlying system—one intake database, one page per idea, a structured record that outlives the event.</p>

<div class="fah-template">
  <div class="fah-template-title">📋 Hackathon intake form — fields that matter</div>
  <h3>Submission name</h3>
  <p>A 3–6 word label. Forces clarity before anyone has read the full submission.</p>
  <h3>Problem statement</h3>
  <p>One sentence. The constraint is the discipline—a problem that needs "and" is really two problems. Split it.</p>
  <h3>Who it hurts</h3>
  <p>A specific person or role. "The team" is not a persona. "The controller on every close" is.</p>
  <h3>Type</h3>
  <p>Automation / visibility / missing skill / analysis / ops plumbing. Useful for spotting patterns—if half the submissions are "I can't see X," that's a signal.</p>
  <h3>Impact + feasibility</h3>
  <p>A first guess, not a commitment. You'll refine it during the 2×2.</p>
  <h3>Definition of done</h3>
  <p>Describe the two-minute demo. What does success look like when someone watches it work? This field does more work than any other.</p>
</div>

<p>The move worth stealing: <strong>one page per idea.</strong> Not just a row in a table—an actual page that becomes the full record. What the team submitted, live notes from the build, what they learned, what broke, what to do next. The page carries the idea through the event and becomes searchable institutional knowledge.</p>

<p>Without this, the hackathon produces prototypes. With it, it produces compounding assets. The next person who picks up a similar problem starts from the answer, not from scratch.</p>

<p>Two database views worth setting up: an <strong>effort × value matrix</strong> (the digital twin of your sticky-note 2×2, auto-sorted by vote count) and a <strong>groups board</strong> by theme. The groups view is useful for spotting when one area—say, month-end close—quietly dominates the shortlist, which is usually a signal worth paying attention to.</p>

<h2>After: building the AI Lab</h2>
<p>The hackathon is a beginning, not a destination. What makes it compound over time is institutionalizing what you learned: a shared space—call it the AI Lab, call it whatever fits your culture—where builds live and can be forked.</p>

<p>The operating model is simple:</p>
<ul style="padding-left:22px;margin:0 0 20px;">
  <li style="margin-bottom:8px;">Build something useful → document it → drop it in the Lab.</li>
  <li style="margin-bottom:8px;">Find something someone else built → fork it → adapt it to your context.</li>
  <li style="margin-bottom:8px;">Review the Lab quarterly. What's still in use? What needs updating? What opened up new possibilities?</li>
</ul>

<p>Run the hackathon again next year. The format gets easier the second time—the setup is faster, people know what to expect, and the ideas are sharper because everyone has spent a year noticing problems worth solving. The first one is the hardest. The flywheel needs one good push.</p>

<div class="fah-callout">
  <div class="fah-callout-title">What good looks like when you leave</div>
  <ul>
    <li>3–4 working prototypes, each with a verdict and a named owner</li>
    <li>A prioritized backlog in Notion for everything that didn't get built—with owners on anything that moves forward</li>
    <li>At least one thing in production before anyone leaves</li>
    <li>A shared Lab space where builds live and can be forked</li>
    <li>A date on the calendar for the next one</li>
  </ul>
</div>

<p>The teams that get the most out of AI aren't the ones with the best tools. They're the ones that got good at using them—together, on purpose, through deliberate practice. A hackathon is how you start that. Sail, don't row.</p>

<div style="border-top:1px solid var(--line-strong);margin-top:48px;padding-top:24px;">
  <p style="font-size:13px;color:var(--muted);margin:0;">Brian Weisberg is a tech CFO writing about finance leadership, AI adoption, and building finance teams that compound. <a href="/thought-leadership">More writing &rarr;</a></p>
</div>

</div>"""
    return HTMLResponse(_page("Sail, Don't Row: AI Hackathon Playbook—Brian Weisberg", "Thought Leadership", body, role=_role(request)))


@app.get("/netsuite-mcp", response_class=HTMLResponse)
def netsuite_mcp(request: Request):
    body = """<div class="page" style="max-width:820px;">
<style>
  .ns-pull{background:var(--navy-wash);border-left:3px solid var(--navy);border-radius:0 10px 10px 0;padding:18px 24px;margin:28px 0;}
  .ns-pull p{margin:0;font-size:16px;font-style:italic;line-height:1.55;color:var(--ink);}
  .ns-callout{background:var(--seafoam-wash);border-top:2px solid var(--seafoam-mid);border-radius:0 0 10px 10px;padding:20px 24px;margin:24px 0;}
  .ns-callout-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--seafoam-deep);margin-bottom:10px;}
  .ns-callout p,.ns-callout li{font-size:15px;color:var(--ink-soft);margin-bottom:6px;}
  .ns-callout ul{padding-left:20px;margin:0;}
  .ns-callout li{margin-bottom:4px;}
  .ns-warn{background:var(--coral-wash);border-top:2px solid var(--coral);border-radius:0 0 10px 10px;padding:16px 22px;margin:18px 0;}
  .ns-warn-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--coral-deep);margin-bottom:6px;}
  .ns-warn p{font-size:14px;color:var(--ink-soft);margin:0;}
  .ns-note{background:var(--surface-2);border-left:3px solid var(--line-strong);padding:14px 18px;margin:16px 0;border-radius:0 8px 8px 0;}
  .ns-note p{font-size:14px;color:var(--muted);margin:0;}
  /* Phase track */
  .ns-track{display:flex;flex-direction:column;gap:0;margin:24px 0;}
  .ns-step{display:flex;gap:18px;position:relative;}
  .ns-step:not(:last-child)::after{content:"";position:absolute;left:17px;top:40px;width:2px;bottom:-2px;background:var(--line-strong);}
  .ns-num{width:36px;height:36px;border-radius:50%;background:var(--navy);color:#fff;font-family:var(--font-head);font-size:14px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;z-index:1;}
  .ns-body{padding-bottom:24px;flex:1;}
  .ns-body h3{font-family:var(--font-head);font-size:16px;font-weight:600;color:var(--ink);margin:4px 0 6px;}
  .ns-body p{font-size:15px;color:var(--ink-soft);margin-bottom:8px;line-height:1.6;}
  /* Use case cards */
  .ns-cases{display:grid;grid-template-columns:1fr;gap:12px;margin:20px 0;}
  .ns-case{background:#fff;border:1px solid var(--line-strong);border-radius:10px;padding:20px 22px;}
  .ns-case-label{font:700 10px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;}
  .ns-case-title{font-family:var(--font-head);font-size:16px;font-weight:600;color:var(--ink);margin-bottom:8px;}
  .ns-case p{font-size:14px;color:var(--ink-soft);margin-bottom:10px;line-height:1.55;}
  .ns-tip{background:var(--seafoam-wash);border-radius:6px;padding:10px 14px;font-size:13px;color:var(--seafoam-deep);margin-top:8px;}
  .ns-tip strong{font-weight:600;}
  /* Permission table */
  .ns-table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:16px 0;}
  .ns-table{width:100%;border-collapse:collapse;font-size:14px;min-width:360px;}
  .ns-table th{background:var(--navy);color:#fff;padding:9px 14px;text-align:left;font-weight:600;}
  .ns-table td{padding:9px 14px;border-top:1px solid var(--line);}
  .ns-table tr:nth-child(even) td{background:var(--surface-2);}
  /* Troubleshooting table */
  .ns-trouble{width:100%;border-collapse:collapse;font-size:14px;margin:16px 0;}
  .ns-trouble th{background:var(--surface-2);padding:9px 14px;text-align:left;font-weight:600;border-bottom:2px solid var(--line-strong);}
  .ns-trouble td{padding:10px 14px;border-top:1px solid var(--line);vertical-align:top;line-height:1.5;}
  .ns-trouble tr:hover td{background:var(--navy-wash);}
  /* Quick ref */
  .ns-qr{background:#fff;border:1px solid var(--line-strong);border-radius:12px;padding:24px 28px;margin:28px 0;}
  .ns-qr-title{font:700 11px var(--font-body);letter-spacing:.14em;text-transform:uppercase;color:var(--navy);margin-bottom:16px;}
  .ns-qr h3{font-family:var(--font-head);font-size:14px;font-weight:600;color:var(--ink);margin:16px 0 6px;}
  .ns-qr h3:first-of-type{margin-top:0;}
  .ns-qr ul{padding-left:18px;margin:0 0 4px;}
  .ns-qr li{font-size:13px;color:var(--ink-soft);margin-bottom:3px;line-height:1.5;}
  @media(max-width:640px){
    .ns-trouble{font-size:13px;}
    .ns-trouble td,.ns-trouble th{padding:8px 10px;}
  }
</style>

<p style="font-size:13px;color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em;">Setup Guide</p>
<h1 style="margin:0 0 8px;">Connecting Claude to NetSuite</h1>
<p style="font-size:17px;font-style:italic;color:var(--ink-soft);margin:0 0 6px;line-height:1.5;">An end-to-end guide to the two-role OAuth setup for finance teams</p>
<p style="color:var(--muted);font-size:14px;margin:0 0 36px;">By Brian Weisberg &middot; June 2026</p>

<p>This guide walks through connecting Claude to NetSuite so you can ask questions about your financial data and get answers directly—no logging into NetSuite, no writing queries, no manual exports.</p>

<p>Once connected, you can ask things like <em>"how much did we spend with this vendor last year?"</em> or <em>"what's the deferred revenue balance for this customer?"</em> and Claude will query NetSuite and return the answer in plain language, a table, or a formatted report. The connection runs through something called an MCP integration. You don't need to understand the underlying technology to use it—this guide covers everything you need.</p>

<div class="ns-callout">
  <div class="ns-callout-title">Before you start</div>
  <p>Check that the NetSuite AI Connector SuiteApp is installed: <strong>Customization → SuiteCloud → Installed SuiteApps</strong>, look for <code>com.netsuite.mcpstandardtools</code>. It should show <strong>Status: COMPLETE</strong>. If it's not installed, go to the SuiteApp Marketplace and search for it by name before continuing.</p>
</div>

<h2>What you can do with this</h2>
<p>Three examples to get your wheels turning. The right use cases depend on your business, but the pattern is consistent: ask a question in plain language, Claude queries NetSuite, you get something ready to share or act on.</p>

<div class="ns-cases">
  <div class="ns-case">
    <div class="ns-case-label">Use case 01</div>
    <div class="ns-case-title">Revenue flow tracker</div>
    <p>If you work with deferred revenue—annual contracts, prepaid arrangements, usage-based billing—it's hard to get a clear picture of how money is moving at any point in time. Claude can pull a month-by-month view showing how revenue is loading into deferred, releasing into recognized, and what the ending balance looks like. Run it for the whole business or for a specific customer.</p>
    <p>A typical output: a waterfall table (deferred loaded, released, ending balance by month), a transaction-level trace from invoice through recognition, and a findings section flagging anything off—like a balance that should have cleared at contract termination but didn't.</p>
    <div class="ns-tip"><strong>Tip:</strong> Ask Claude to include a math check confirming every ending balance ties back to the underlying arithmetic. Easy to add, catches rounding errors before they make it into something you share.</div>
  </div>
  <div class="ns-case">
    <div class="ns-case-label">Use case 02</div>
    <div class="ns-case-title">Vendor spend analysis</div>
    <p>Vendor spend is deceptively messy in NetSuite. The same vendor might appear under different names across bills. Some vendors route through a spend management platform (Ramp, Navan, Brex), which means they show up as a single vendor with the actual vendor buried in a memo field. Others route through a marketplace, invisible unless you know where to look.</p>
    <p>Claude can learn these patterns. Once you show it how your vendors are recorded—<em>"this vendor always comes through as the platform with the name in the memo"</em>—it applies that logic consistently. The result is a spend picture that reflects reality, not just whatever's in the vendor field.</p>
    <div class="ns-tip"><strong>Tip:</strong> The first time you run a vendor spend query, ask Claude to show you a sample of raw transaction data before it aggregates anything. Easy way to spot non-obvious mappings before they roll up into a wrong total.</div>
  </div>
  <div class="ns-case">
    <div class="ns-case-label">Use case 03</div>
    <div class="ns-case-title">Per-employee benefit and stipend tracking</div>
    <p>If your company offers benefits employees draw on over time—L&amp;D stipends, wellness budgets, home office allowances—and those transactions flow through NetSuite in any form, Claude can extract and organize them by person. A useful output: each employee's YTD usage broken down by category, with transaction-level detail on demand. Useful for answering "who has used their full allocation?" without compiling spreadsheets manually.</p>
    <div class="ns-tip"><strong>Tip:</strong> Employee names in NetSuite memos are often inconsistent—nicknames, initials, misspellings. Ask Claude to show you the distinct name variations it finds before attributing spend, so you can confirm the mapping is right.</div>
  </div>
</div>

<h2>The security architecture</h2>
<p>The setup involves creating a dedicated read-only role in NetSuite for Claude to authenticate as. The reason matters.</p>

<p>Claude's NetSuite integration includes tools that can create and update records—not just read them. If Claude is authenticated with a role that has write permissions, it could theoretically create transactions, edit customer records, or modify other data in your ledger. To prevent that, we create a read-only role and configure Claude to use it. No write permissions on the role means NetSuite blocks any write attempt at the permission level—regardless of what Claude tries to do. The protection is enforced by NetSuite, not by hoping Claude behaves.</p>

<div class="ns-warn">
  <div class="ns-warn-title">One thing that trips people up</div>
  <p>When you connect Claude, you need to be logged into NetSuite under your <strong>normal working role</strong>—not the new read-only role you're about to create. You'll select the read-only role on a screen that appears during the connection flow. More on this in Part 2.</p>
</div>

<h2>Part 1 — NetSuite setup</h2>
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
      <div class="ns-warn" style="margin-top:12px;">
        <div class="ns-warn-title">Known issue</div>
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

<h2>Part 2 — Connecting Claude</h2>
<p style="color:var(--muted);font-size:14px;margin:-8px 0 20px;">NetSuite is set up. This part takes about two minutes per person.</p>

<div class="ns-track">
  <div class="ns-step">
    <div class="ns-num">5</div>
    <div class="ns-body">
      <h3>Make sure you're in the right NetSuite role first</h3>
      <p>Before going to Claude, check which role you're currently in on the NetSuite side. You need to be logged in under your <strong>normal working role</strong>—not the Netsuite MCP role you just created. Netsuite MCP is what you'll select during the connection flow, not what you're already in. Check the role indicator in the top-right corner of NetSuite. If it says Netsuite MCP, switch to your normal role first.</p>
      <div class="ns-warn" style="margin-top:8px;">
        <div class="ns-warn-title">Most common mistake when reconnecting</div>
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

<h2>Tips for getting good results</h2>

<h3 style="font-size:16px;margin:24px 0 8px;">Ask for everything, not just bills</h3>
<p>When you ask Claude to look something up, it may default to querying only vendor bills or invoices. This can miss a lot. Journal entries are a separate transaction type—and many common workflows post through JEs: month-end accruals, prepayment amortizations, corporate card programs. A query limited to vendor bills misses them entirely.</p>
<div class="ns-callout">
  <div class="ns-callout-title">Sanity check for any spend query</div>
  <p>Ask Claude to first pull a grand total with no filters other than the account and date range, then compare against the detailed results. If they don't match, something is being filtered out. The discrepancy tells you what to investigate next.</p>
</div>

<h3 style="font-size:16px;margin:24px 0 8px;">Filter at the line level, not the header</h3>
<p>Revenue recognition journal entries are often posted as a single large entry covering many customers at once. The customer is recorded at the line level inside the entry, not on the entry itself. If Claude filters at the wrong level, it can return results for a completely different customer—or nothing at all.</p>
<p>If results for a customer look wrong—too high, too low, or zero when you know there should be activity—ask Claude: <em>"Are you filtering on the transaction line entity, not the transaction header entity?"</em> That question catches the most common mistake.</p>

<h3 style="font-size:16px;margin:24px 0 8px;">If you get zero results, pull an unfiltered sample first</h3>
<p>Zero results almost always mean a filter is wrong, not that the data is missing. Ask Claude to run a quick sample: <em>"Can you pull 5–10 raw rows with no filters so we can see what's actually there?"</em> This almost always reveals the issue—a filter too narrow, a date range that doesn't match, or a field with data in a slightly different format than expected.</p>

<h3 style="font-size:16px;margin:24px 0 8px;">Claude can run saved searches, not create them</h3>
<p>Claude can run existing saved searches and list available ones. It can't create new ones. If you need a new saved search built, ask Claude what criteria and columns to use, then create it yourself: <strong>Reports → Saved Searches → New → Transaction</strong>.</p>

<h2>Troubleshooting</h2>
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

<h2>When the connection drops</h2>
<p>The connection between Claude and NetSuite drops periodically. This is normal and doesn't mean anything is misconfigured. Try this first: <strong>open a new Claude conversation.</strong> The connection re-establishes on a new session most of the time.</p>

<p>If a new conversation doesn't fix it: go to <strong>Customize</strong> (bottom-left of the chat window) or <strong>Settings → Connectors</strong>. Find NetSuite, click Disconnect, then Connect. On the NetSuite authorization screen, confirm you're in your normal working role, then select Netsuite MCP and authorize. Test with a quick query.</p>

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

</div>"""
    return HTMLResponse(_page("Connecting Claude to NetSuite—Brian Weisberg", "Thought Leadership", body, role=_role(request)))


@app.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request, submitted: str = ""):
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks for reaching out.</h1>
<p>I'll get back to you shortly.</p>
<a href="/" class="btn btn-ghost" style="margin-top:8px;">Back to home</a>
</div>"""
        return HTMLResponse(_page("Contact—Brian Weisberg", "Contact", body, role=_role(request)))

    body = """<div class="page" style="max-width:560px;">
<h1>Get in Touch</h1>
<p style="color:var(--muted);margin:4px 0 32px;">I'm always happy to connect with finance leaders, founders, and operators.</p>
<form method="post" action="/contact" style="display:grid;gap:16px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Name</label>
    <input name="name" required style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;" placeholder="Your name">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Email</label>
    <input name="email" type="email" required style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;" placeholder="you@example.com">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Message</label>
    <textarea name="message" required rows="5" style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;" placeholder="What's on your mind?"></textarea>
  </div>
  <div>
    <button type="submit" class="btn">Send message</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Contact—Brian Weisberg", "Contact", body, role=_role(request)))


@app.post("/contact")
async def contact_submit(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip()
    message = (form.get("message") or "").strip()
    if not (name and email and message):
        raise HTTPException(status_code=400, detail="All fields required")
    lib = _lib()
    try:
        lib.save_contact(name, email, message)
    finally:
        lib.close()
    return RedirectResponse("/contact?submitted=1", status_code=303)


# TODO: Once hello@[domain].com is set up in Google Workspace, wire the contact
# form to also email submissions there. Set LINKLIB_CONTACT_EMAIL in Railway and
# call _send_email() here. The DB record will keep existing as a backup.


# ---------------------------------------------------------------------------
# Library submissions — a public "suggest a piece" form. Submissions land
# UN-ENRICHED in the Archive Queue (no server-side fetch, no Claude call), so a
# public endpoint can't be used to run up cost or fetch arbitrary URLs. Brian
# reviews them in /admin/queue; enrichment happens only on approval.
#
# Public for now; the handler is self-contained, so gating it behind the future
# paid login is a one-line auth check.
# ---------------------------------------------------------------------------

@app.get("/library/submit", response_class=HTMLResponse)
def library_submit_page(request: Request, submitted: str = ""):
    if not _is_member(request):   # account-only, to keep public spam out
        return _login_redirect(request)
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks&mdash;suggestion received.</h1>
<p>I review every suggestion personally. If it's a fit for the archive, it'll join the collection.</p>
<a href="/" class="btn btn-ghost" style="margin-top:8px;">Back to home</a>
</div>"""
        return HTMLResponse(_page("Suggestion received — Brian Weisberg", "", body, role=_role(request)))

    body = """<div class="page" style="max-width:560px;">
<h1>Suggest a piece for the archive</h1>
<p style="color:var(--muted);margin:4px 0 32px;">Read something a finance leader should have in their back pocket? Send it my way. I review every suggestion before it joins the archive.</p>
<form method="post" action="/library/submit" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Article URL *</label>
    <input name="url" type="url" required maxlength="500"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="https://…">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Why it belongs <span style="font-weight:400;color:var(--muted);">(optional)</span></label>
    <textarea name="why" maxlength="600" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"
      placeholder="What makes this worth saving?"></textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Your name <span style="font-weight:400;color:var(--muted);">(optional)</span></label>
    <input name="name" maxlength="120"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="So I know who to thank">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Your email <span style="font-weight:400;color:var(--muted);">(optional)</span></label>
    <input name="email" type="email" maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="you@example.com">
  </div>
  <input type="text" name="website" tabindex="-1" autocomplete="off"
    style="position:absolute;left:-9999px;width:1px;height:1px;" aria-hidden="true">
  <div>
    <button type="submit" class="btn">Suggest for the archive</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Suggest a piece — Brian Weisberg", "", body, role=_role(request)))


@app.post("/library/submit")
async def library_submit(request: Request):
    if not _is_member(request):
        return _login_redirect(request)
    form = await request.form()
    # Honeypot: bots fill the hidden "website" field. Pretend success, drop silently.
    if (form.get("website") or "").strip():
        return RedirectResponse("/library/submit?submitted=1", status_code=303)
    url = (form.get("url") or "").strip()
    if not url or not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="A valid http(s) URL is required")
    why = (form.get("why") or "").strip()[:600]
    name = (form.get("name") or "").strip()[:120]
    email = (form.get("email") or "").strip()[:200]

    who = name or "anonymous"
    note_bits = [f"Reader suggestion from {who}" + (f" ({email})" if email else "") + "."]
    if why:
        note_bits.append(f"Why: {why}")
    note = " ".join(note_bits)

    lib = _lib()
    try:
        # Un-enriched insert; no fetch. add_to_queue dedupes against library + queue.
        lib.add_to_queue(url, source="Reader submissions", summary=note,
                         origin=f"submission:{who}", enriched=False)
    finally:
        lib.close()
    # Always confirm — never reveal whether the URL was already in the archive.
    return RedirectResponse("/library/submit?submitted=1", status_code=303)

# ---------------------------------------------------------------------------
# Community
# ---------------------------------------------------------------------------

# TODO: Replace this placeholder with the real Google Form URL once created in
# Google Workspace. Create the form with: Name, Email, Current/past communities,
# Gaps in community experiences, What you'd look for in an ideal community (multi-select).
_COMMUNITY_FORM_URL = "#community-form-coming-soon"
_COMMUNITY_FORM_CONFIGURED = _COMMUNITY_FORM_URL != "#community-form-coming-soon"


@app.get("/community", response_class=HTMLResponse)
def community_page(request: Request):
    # Parked: kept reachable for admin (so the idea/copy isn't lost) but off the
    # public site — the signed-out site is a clean bio while job-hunting.
    if not _is_authed(request):
        return _login_redirect(request)
    cta_block = (
        f'<a href="{_COMMUNITY_FORM_URL}" target="_blank" rel="noopener" class="btn" '
        f'style="font-size:15px;padding:12px 26px;">Share your experience &rarr;</a>'
        if _COMMUNITY_FORM_CONFIGURED else
        '<div style="background:var(--coral-wash);border:1px solid #F3D3C6;border-radius:12px;'
        'padding:16px 20px;margin-top:8px;">'
        '<p style="margin:0;font-size:14px;color:var(--coral-deep);font-weight:500;">'
        '&#9888; Google Form not yet configured. Create the form in Google Workspace '
        'and update <code>_COMMUNITY_FORM_URL</code> in <code>webapp/app.py</code>.</p></div>'
    )
    body = f"""<div class="page" style="max-width:660px;">
<div style="font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:12px;">CFO Community</div>
<h1 style="margin:0 0 28px;">Building something better<br>for CFO peers.</h1>

<p>I&rsquo;ve spent years inside finance communities&mdash;as a founding member and eventually as GM of
<a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a>, the invite-only network
for CFOs of high-growth tech companies. I&rsquo;ve seen what makes these communities genuinely valuable,
and I&rsquo;ve seen where even the best ones fall short.</p>

<p>I&rsquo;m working on something new in this space. Before I build anything, I want to hear from
peers who&rsquo;ve been in these communities: what they got right, what they missed, and what a
version that actually works would look like for the finance leaders who need it most.</p>

<p>If you have a few minutes, I&rsquo;d love your input. The form takes about two minutes and
your answers will directly shape what I build.</p>

<div style="margin-top:32px;">
{cta_block}
</div>

<div style="margin-top:48px;padding-top:32px;border-top:1px solid var(--line);">
  <p style="font-size:13px;color:var(--muted);margin:0;">Questions? <a href="/contact">Get in touch directly.</a></p>
</div>
</div>"""
    return HTMLResponse(_page("CFO Community—Brian Weisberg", "", body, role=_role(request)))


@app.get("/tools", response_class=HTMLResponse)
def tools_directory(request: Request):
    authed = _is_authed(request)   # admin sees the management controls
    is_member = _is_member(request)  # submit / warm-intro are account-only
    lib = _lib()
    try:
        tools = lib.list_tools(approved_only=True)
    finally:
        lib.close()

    # Serialize to JSON for client-side filtering
    import json as _json
    def _tool_entry(t: dict) -> dict:
        entry = {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "url": t["url"],
            "categories": t["categories"],
            "advisor": bool(t.get("advisor")),
            "promoted": bool(t.get("promoted")),
        }
        if authed:
            entry["submitted_by"] = t.get("submitted_by") or ""
            entry["created_at"] = (t.get("created_at") or "")[:10]
            entry["updated_at"] = (t.get("updated_at") or "")[:10]
        return entry

    tools_json = _json.dumps([_tool_entry(t) for t in tools])

    cat_buttons = "".join(
        f'<button class="tcat-btn" data-cat="{_esc(c)}" onclick="filterCat(this)"'
        f' title="{_esc(CATEGORY_DESCRIPTIONS.get(c, ""))}">{_esc(c)}</button>'
        for c in TOOL_CATEGORIES
    )

    def _bench_badge_style(cov: str) -> str:
        return {
            "Private": "background:#dbeafe;color:#1d4ed8",
            "Public":  "background:#dcfce7;color:#16a34a",
            "Both":    "background:#ede9fe;color:#7c3aed",
        }.get(cov, "background:var(--accent-light);color:var(--accent)")

    def _bench_pricing_badge(b: dict) -> str:
        p = (b.get("pricing") or "free").lower()
        if p == "paid":
            label = "$ Paid"
        elif p == "freemium":
            label = "$ Free + paid"
        else:
            return ""
        return (f'<span class="bench-badge" title="Paid resource" '
                f'style="background:#fef3c7;color:#92400e;">{label}</span>')

    bench_cards = "".join(
        f'<a class="bench-card" href="{_esc(b["url"])}" target="_blank" rel="noopener">'
        f'<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px;">'
        f'<span class="bench-name">{_esc(b["name"])}</span>'
        f'<span style="display:flex;gap:6px;align-items:center;flex-shrink:0;">'
        f'{_bench_pricing_badge(b)}'
        f'<span class="bench-badge" style="{_bench_badge_style(b["coverage"])}">{_esc(b["coverage"])}</span>'
        f'</span>'
        f'</div>'
        f'<p class="bench-desc">{_esc(b["description"])}</p>'
        f'</a>'
        for b in BENCHMARKS
    )

    body = f"""<div class="page" style="max-width:860px;">
<div style="display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:12px;">
  <h1 style="margin:0;">CFO Toolbox</h1>
  {'<a href="/admin/tools/new" class="btn" style="font-size:14px;padding:8px 18px;">+ Add tool</a>' if authed else ''}
</div>
<p style="color:var(--muted);margin:8px 0 28px;">A searchable directory of tools and solutions for the Office of the CFO.
{'<a href="/tools/submit" style="margin-left:12px;font-size:14px;font-weight:500;">+ Submit a tool</a>' if is_member else '<a href="/login" style="margin-left:12px;font-size:14px;font-weight:500;color:var(--muted);">Sign in to submit a tool</a>'}</p>

<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px;">
  <input id="tool-search" type="search" placeholder="Search tools…"
    oninput="filterTools()"
    style="flex:1;min-width:200px;max-width:400px;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  <button id="advisor-btn" class="tcat-btn" onclick="toggleAdvisor()" style="border-color:var(--accent);color:var(--accent);">&#9733; Advisor</button>
  <button class="tcat-btn tcat-all tcat-active" data-cat="" onclick="filterCat(this)">All</button>
  {cat_buttons}
</div>

<div id="tool-count" style="font-size:13px;color:var(--muted);margin-bottom:16px;"></div>

<div id="tool-grid" style="display:grid;gap:14px;">
</div>

<p id="tool-empty" style="display:none;color:var(--muted);padding:32px 0;">No tools match your search.</p>

<div style="margin-top:40px;padding-top:28px;border-top:1px solid var(--line);">
  <p style="font-size:13px;color:var(--muted);margin-bottom:16px;">&#9733; Formal advisor to these companies.</p>
  <p style="font-size:15px;color:var(--muted);">Know a tool that belongs here?
    {'<a href="/tools/submit" style="font-weight:500;">Submit it for review →</a>' if is_member else '<a href="/login" style="font-weight:500;">Sign in to submit a tool →</a>'}</p>
</div>

<div style="margin-top:48px;padding-top:40px;border-top:1px solid var(--line);">
  <h2 style="font-size:20px;font-weight:700;margin:0 0 6px;">Benchmarking Resources</h2>
  <p style="color:var(--muted);font-size:14px;margin:0 0 12px;">The benchmarking sources I actually use.</p>
  <p style="font-size:13px;color:var(--muted);margin:0 0 24px;">Worth reading first: <a href="https://www.onlycfo.io/p/benchmarking-is-bad" target="_blank" rel="noopener" style="color:var(--accent);font-weight:500;">Benchmarking is Bad</a>&mdash;it&rsquo;s not always what you think it is.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;">
    {bench_cards}
  </div>
</div>
</div>

<style>
.tcat-btn{{font-size:13px;font-weight:500;padding:6px 14px;border-radius:20px;border:1px solid var(--line);
  background:none;color:var(--muted);cursor:pointer;white-space:nowrap;}}
.tcat-btn:hover{{background:var(--accent-light);color:var(--ink);}}
.tcat-active{{background:var(--accent)!important;color:#fff!important;border-color:var(--accent)!important;}}
.tool-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;}}
.tool-name{{font-family:var(--font-head);font-size:17px;font-weight:600;color:var(--ink);text-decoration:none;display:block;margin-bottom:6px;letter-spacing:-0.01em;}}
.tool-name:hover{{color:var(--accent);}}
.tool-desc{{font-size:14px;color:#3a352e;margin:0 0 12px;line-height:1.5;}}
.tool-cats{{display:flex;flex-wrap:wrap;gap:6px;}}
.tool-cat{{font-size:11px;font-weight:600;color:var(--navy);background:var(--seafoam);border-radius:6px;padding:3px 9px;}}
.tool-star{{font-size:14px;color:#b8860b;margin-right:4px;flex-shrink:0;}}
.tool-admin{{display:flex;gap:6px;flex-shrink:0;}}
.tool-admin-btn{{font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:6px;padding:3px 10px;cursor:pointer;text-decoration:none;white-space:nowrap;}}
.tool-admin-btn:hover{{background:var(--accent-light);color:var(--ink);text-decoration:none;}}
.tool-admin-del:hover{{background:#fee2e2;color:#b91c1c;border-color:#fca5a5;}}
.tool-meta{{font-size:12px;color:var(--muted);margin-top:10px;}}
.bench-card{{display:block;background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;text-decoration:none;transition:border-color .15s;}}
.bench-card:hover{{border-color:var(--accent);text-decoration:none;}}
.bench-name{{font-size:15px;font-weight:600;color:var(--ink);}}
.bench-card:hover .bench-name{{color:var(--accent);}}
.bench-badge{{font-size:11px;font-weight:500;border-radius:6px;padding:2px 8px;white-space:nowrap;flex-shrink:0;}}
.bench-desc{{font-size:13px;color:#3a352e;margin:0;line-height:1.5;}}
.tool-card-featured{{border-color:var(--coral-light);box-shadow:0 0 0 1px var(--coral-light);}}
.tool-intro-btn{{font-size:13px;font-weight:600;color:var(--navy);background:none;border:1px solid var(--navy);
  border-radius:8px;padding:6px 14px;cursor:pointer;white-space:nowrap;flex-shrink:0;}}
.tool-intro-btn:hover{{background:var(--navy-wash);}}
.tool-intro-btn:disabled{{color:var(--muted);border-color:var(--line);cursor:not-allowed;}}
.tool-intro-btn:disabled:hover{{background:none;}}
/* Intro modal */
.intro-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;
  align-items:center;justify-content:center;padding:20px;}}
.intro-overlay.open{{display:flex;}}
.intro-modal{{background:#fff;border-radius:20px;padding:32px 28px;width:100%;max-width:460px;
  box-shadow:0 20px 60px rgba(0,0,0,.18);position:relative;}}
.intro-modal h2{{font-family:var(--font-head);font-size:20px;font-weight:600;letter-spacing:-0.01em;
  color:var(--ink);margin:0 0 6px;}}
.intro-modal p{{font-size:14px;color:var(--muted);margin:0 0 20px;}}
.intro-field{{display:grid;gap:6px;}}
.intro-field label{{font-size:13px;font-weight:500;color:var(--navy);}}
.intro-field input,.intro-field select{{width:100%;padding:9px 13px;border:1px solid var(--line);
  border-radius:9px;font:inherit;font-size:14px;background:var(--bg);}}
.intro-close{{position:absolute;top:16px;right:20px;background:none;border:none;font-size:20px;
  color:var(--muted);cursor:pointer;line-height:1;padding:4px 8px;border-radius:6px;}}
.intro-close:hover{{background:var(--navy-wash);color:var(--ink);}}
</style>

<script>
var ALL_TOOLS = {tools_json};
var AUTHED = {'true' if authed else 'false'};
var MEMBER = {'true' if is_member else 'false'};
var activeCats = new Set();
var advisorOnly = false;

function esc(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function confirmDelete(form) {{
  return confirm('Delete ' + form.dataset.toolname + '?');
}}

function renderTools(tools) {{
  var grid = document.getElementById('tool-grid');
  var empty = document.getElementById('tool-empty');
  var count = document.getElementById('tool-count');
  if (tools.length === 0) {{
    grid.innerHTML = '';
    empty.style.display = 'block';
    count.textContent = '';
    return;
  }}
  empty.style.display = 'none';
  count.textContent = tools.length + ' tool' + (tools.length === 1 ? '' : 's');
  // Promoted tools first, then alphabetical within each group
  var sorted = tools.slice().sort(function(a, b) {{
    if (a.promoted && !b.promoted) return -1;
    if (!a.promoted && b.promoted) return 1;
    return esc(a.name).localeCompare(esc(b.name));
  }});
  grid.innerHTML = sorted.map(function(t) {{
    var promotedBadge = t.promoted
      ? '<span style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        + 'background:var(--coral);color:#fff;border-radius:5px;padding:2px 8px;flex-shrink:0;">Featured</span>'
      : '';
    var star = t.advisor ? '<span class="tool-star" title="Brian Weisberg is a formal advisor">&#9733;</span>' : '';
    var cats = (t.categories || []).map(function(c) {{
      return '<span class="tool-cat">' + esc(c) + '</span>';
    }}).join('');
    var adminControls = '';
    if (AUTHED) {{
      adminControls = '<div class="tool-admin">'
        + '<a href="/admin/tools/' + t.id + '/edit" class="tool-admin-btn">Edit</a>'
        + '<form method="post" action="/admin/tools/' + t.id + '/delete" style="display:inline;"'
        + ' data-toolname="' + esc(t.name) + '"'
        + ' onsubmit="return confirmDelete(this)">'
        + '<button type="submit" class="tool-admin-btn tool-admin-del">Delete</button>'
        + '</form></div>';
    }}
    var metaParts = [];
    if (AUTHED) {{
      if (t.submitted_by) metaParts.push('Submitted by ' + esc(t.submitted_by));
      if (t.created_at) metaParts.push('Added ' + t.created_at);
      if (t.updated_at && t.updated_at !== t.created_at) metaParts.push('Edited ' + t.updated_at);
    }}
    var adminMeta = metaParts.length ? '<div class="tool-meta">' + metaParts.join(' &middot; ') + '</div>' : '';
    var introBtn = MEMBER
      ? '<button class="tool-intro-btn" onclick="openIntroModal(' + t.id + ',\'' + esc(t.name).replace(/'/g,"\\'") + '\')">'
        + '&#10024; Warm Intro</button>'
      : '<button class="tool-intro-btn" disabled title="Sign in to request a warm intro">&#10024; Warm Intro</button>';
    return '<article class="tool-card' + (t.promoted ? ' tool-card-featured' : '') + '">'
      + '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:2px;">'
      + '<div style="display:flex;align-items:center;gap:6px;min-width:0;flex-wrap:wrap;">'
      + promotedBadge + star
      + '<a class="tool-name" href="' + esc(t.url) + '" target="_blank" rel="noopener">' + esc(t.name) + '</a>'
      + '</div>'
      + adminControls + '</div>'
      + '<p class="tool-desc">' + esc(t.description) + '</p>'
      + '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">'
      + '<div class="tool-cats">' + cats + '</div>'
      + introBtn
      + '</div>'
      + adminMeta + '</article>';
  }}).join('');
}}

function filtered() {{
  var q = (document.getElementById('tool-search').value || '').toLowerCase();
  return ALL_TOOLS.filter(function(t) {{
    if (advisorOnly && !t.advisor) return false;
    if (activeCats.size > 0) {{
      var cats = t.categories || [];
      var hit = false;
      for (var i = 0; i < cats.length; i++) {{ if (activeCats.has(cats[i])) {{ hit = true; break; }} }}
      if (!hit) return false;
    }}
    if (!q) return true;
    return (t.name + ' ' + t.description + ' ' + (t.categories || []).join(' ')).toLowerCase().indexOf(q) !== -1;
  }});
}}

function syncButtons() {{
  var noFilters = activeCats.size === 0 && !advisorOnly;
  document.querySelectorAll('.tcat-btn').forEach(function(b) {{
    var c = b.dataset.cat;
    if (c !== undefined) {{
      b.classList.toggle('tcat-active', c === '' ? noFilters : activeCats.has(c));
    }}
  }});
  var ab = document.getElementById('advisor-btn');
  if (ab) ab.classList.toggle('tcat-active', advisorOnly);
}}

function filterCat(btn) {{
  var cat = btn.dataset.cat;
  if (cat === '') {{
    activeCats.clear();
    advisorOnly = false;
  }} else if (activeCats.has(cat)) {{
    activeCats.delete(cat);
  }} else {{
    activeCats.add(cat);
  }}
  syncButtons();
  renderTools(filtered());
}}

function toggleAdvisor() {{
  advisorOnly = !advisorOnly;
  syncButtons();
  renderTools(filtered());
}}

function filterTools() {{ renderTools(filtered()); }}

renderTools(ALL_TOOLS);
</script>"""

    body += """
<div class="intro-overlay" id="intro-overlay" onclick="if(event.target===this)closeIntroModal()">
  <div class="intro-modal">
    <button class="intro-close" onclick="closeIntroModal()" aria-label="Close">&times;</button>
    <h2>Request a Warm Intro</h2>
    <p>I&rsquo;ll personally connect you with the team at <strong id="intro-tool-name"></strong>.</p>
    <div id="intro-form-body" style="display:grid;gap:16px;margin-top:4px;">
      <div class="intro-field">
        <label for="intro-name">Your name</label>
        <input id="intro-name" type="text" placeholder="Jane Smith" maxlength="200">
      </div>
      <div class="intro-field">
        <label for="intro-email">Work email</label>
        <input id="intro-email" type="email" placeholder="jane@company.com" maxlength="200">
      </div>
      <div class="intro-field">
        <label for="intro-company">Company</label>
        <input id="intro-company" type="text" placeholder="Acme Corp" maxlength="200">
      </div>
      <div class="intro-field">
        <label for="intro-size">Company size</label>
        <select id="intro-size">
          <option value="">Select&hellip;</option>
          <option value="1-10">1&ndash;10 employees</option>
          <option value="11-50">11&ndash;50 employees</option>
          <option value="51-200">51&ndash;200 employees</option>
          <option value="201-500">201&ndash;500 employees</option>
          <option value="500+">500+ employees</option>
        </select>
      </div>
      <div id="intro-error" style="display:none;font-size:13px;color:#b91c1c;"></div>
      <button class="btn" id="intro-submit-btn" onclick="submitIntroForm()" style="justify-content:center;">Send intro request &rarr;</button>
    </div>
    <div id="intro-success" style="display:none;text-align:center;padding:16px 0;">
      <div style="font-size:36px;margin-bottom:12px;">&#10024;</div>
      <p style="font-size:16px;font-weight:600;color:var(--navy);margin:0 0 6px;">Request sent!</p>
      <p style="font-size:14px;color:var(--muted);margin:0 0 20px;line-height:1.5;">Brian will be in touch with an intro shortly.</p>
      <button class="btn btn-ghost" onclick="closeIntroModal()">Close</button>
    </div>
  </div>
</div>
<script>
var _introToolId = null;
function openIntroModal(toolId, toolName) {
  _introToolId = toolId;
  document.getElementById('intro-tool-name').textContent = toolName;
  document.getElementById('intro-name').value = '';
  document.getElementById('intro-email').value = '';
  document.getElementById('intro-company').value = '';
  document.getElementById('intro-size').value = '';
  document.getElementById('intro-form-body').style.display = 'grid';
  document.getElementById('intro-success').style.display = 'none';
  var errEl = document.getElementById('intro-error');
  errEl.style.display = 'none';
  errEl.textContent = '';
  var btn = document.getElementById('intro-submit-btn');
  btn.disabled = false;
  btn.textContent = 'Send intro request →';
  document.getElementById('intro-overlay').classList.add('open');
}
function closeIntroModal() {
  document.getElementById('intro-overlay').classList.remove('open');
  _introToolId = null;
}
function submitIntroForm() {
  var name = (document.getElementById('intro-name').value || '').trim();
  var email = (document.getElementById('intro-email').value || '').trim();
  var company = (document.getElementById('intro-company').value || '').trim();
  var size = document.getElementById('intro-size').value;
  var errEl = document.getElementById('intro-error');
  errEl.style.display = 'none';
  if (!name || !email || !company || !size) {
    errEl.textContent = 'Please fill in all fields.';
    errEl.style.display = 'block';
    return;
  }
  var btn = document.getElementById('intro-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Sending…';
  fetch('/tools/' + _introToolId + '/interest', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, email: email, company: company, company_size: size})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok) {
      document.getElementById('intro-form-body').style.display = 'none';
      document.getElementById('intro-success').style.display = 'block';
    } else {
      errEl.textContent = data.error || 'Something went wrong. Please try again.';
      errEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Send intro request →';
    }
  }).catch(function() {
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Send intro request →';
  });
}
</script>"""
    return HTMLResponse(_page("CFO Toolbox—Brian Weisberg", "CFO Toolbox", body, role=_role(request)))


def _tool_category_checkboxes(selected: list[str] | None = None) -> str:
    selected = selected or []
    return "".join(
        f'<label style="display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;">'
        f'<input type="checkbox" name="categories" value="{_esc(c)}"'
        f'{" checked" if c in selected else ""}> {_esc(c)}</label>'
        for c in TOOL_CATEGORIES
    )


@app.get("/tools/submit", response_class=HTMLResponse)
def tools_submit_page(request: Request, submitted: str = ""):
    if not _is_member(request):
        return _login_redirect(request)
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks—submission received.</h1>
<p>Your tool has been submitted for review. If approved, it'll appear in the CFO Toolbox shortly.</p>
<a href="/tools" class="btn btn-ghost" style="margin-top:8px;">Back to CFO Toolbox</a>
</div>"""
        return HTMLResponse(_page("Submission received—CFO Toolbox", "CFO Toolbox", body, role=_role(request)))

    body = f"""<div class="page" style="max-width:560px;">
<h1>Submit a Tool</h1>
<p style="color:var(--muted);margin:4px 0 32px;">Know a tool that belongs in the CFO Toolbox? Submit it for review.</p>
<form method="post" action="/tools/submit" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="e.g. Mosaic">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="https://…">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"
      placeholder="What does it do? 1–2 sentences."></textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes()}
    </div>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Your email *</label>
    <input name="submitted_by" type="email" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="you@example.com">
  </div>
  <div>
    <button type="submit" class="btn">Submit for review</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Submit a Tool—CFO Toolbox", "CFO Toolbox", body, role=_role(request)))


@app.post("/tools/submit")
async def tools_submit(request: Request):
    if not _is_member(request):
        return _login_redirect(request)
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    categories = [v.strip() for v in form.getlist("categories") if v.strip()]
    submitted_by = (form.get("submitted_by") or "").strip()
    if not (name and url and description and categories and submitted_by):
        raise HTTPException(status_code=400, detail="All fields including email are required.")
    lib = _lib()
    try:
        lib.add_tool(name, description, url, categories, submitted_by=submitted_by, approved=0)
    finally:
        lib.close()
    return RedirectResponse("/tools/submit?submitted=1", status_code=303)


@app.get("/admin/contacts", response_class=HTMLResponse)
def admin_contacts(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        contacts = lib.list_contacts()
    finally:
        lib.close()
    rows = "".join(
        f"""<tr>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;">{_esc(c['created_at'][:10])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);">{_esc(c['name'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);">{_esc(c['email'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:pre-wrap;">{_esc(c['message'])}</td>
        </tr>"""
        for c in contacts
    ) or '<tr><td colspan="4" style="padding:20px;color:var(--muted);">No submissions yet.</td></tr>'
    body = f"""<div class="page" style="max-width:960px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Contact submissions</h1>
<div style="background:var(--coral-wash);border:1px solid var(--coral);border-radius:10px;padding:14px 18px;margin:16px 0;font-size:14px;line-height:1.5;">
  <strong>TODO:</strong> Set up <code>hello@[yourdomain].com</code> in Google Workspace once the domain is purchased,
  then set <code>LINKLIB_SMTP_HOST/USER/PASS</code> + <code>LINKLIB_FROM_EMAIL</code> in Railway so contact
  form submissions are emailed to you automatically. Until then, check this page manually.
</div>
<table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px;border:1px solid var(--line);overflow:hidden;margin-top:24px;">
<thead><tr style="background:var(--accent-light);">
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Date</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Name</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Email</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Message</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""
    return HTMLResponse(_page("Contacts—Admin", "Admin", body, authed=True))


@app.get("/admin/tools", response_class=HTMLResponse)
def admin_tools(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        pending = [t for t in lib.list_tools(approved_only=False) if not t["approved"]]
        approved = [t for t in lib.list_tools(approved_only=True)]
        lead_counts = lib.get_tool_lead_counts()
    finally:
        lib.close()

    def _tool_row(t: dict) -> str:
        cats = ", ".join(t["categories"]) or "—"
        return f"""<tr>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;">{_esc(t['created_at'][:10])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-weight:600;">{_esc(t['name'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);"><a href="{_esc(t['url'])}" target="_blank" rel="noopener" style="word-break:break-all;">{_esc(t['url'][:60])}{'…' if len(t['url']) > 60 else ''}</a></td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-size:14px;">{_esc(t['description'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);">{_esc(cats)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);">{_esc(t['submitted_by'] or '—')}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;">
            <form method="post" action="/admin/tools/{t['id']}/approve" style="display:inline;">
              <button class="btn" style="padding:6px 14px;font-size:13px;">Approve</button>
            </form>
            <form method="post" action="/admin/tools/{t['id']}/reject" style="display:inline;margin-left:6px;"
                  onsubmit="return confirm('Reject and delete this submission?');">
              <button class="btn btn-ghost" style="padding:5px 12px;font-size:13px;color:#b91c1c;border-color:#fca5a5;">Reject</button>
            </form>
          </td>
        </tr>"""

    def _approved_row(t: dict) -> str:
        cats = ", ".join(t["categories"]) or "—"
        n_leads = lead_counts.get(t["id"], 0)
        lead_badge = (f'<a href="/admin/tools/leads?tool_id={t["id"]}" '
                      f'style="display:inline-block;background:var(--coral);color:#fff;border-radius:5px;'
                      f'padding:2px 8px;font-size:11px;font-weight:700;text-decoration:none;white-space:nowrap;">'
                      f'{n_leads} lead{"s" if n_leads != 1 else ""}</a>') if n_leads else \
                     '<span style="font-size:12px;color:var(--muted);">0 leads</span>'
        featured_badge = '<span style="font-size:11px;font-weight:700;background:var(--coral);color:#fff;border-radius:4px;padding:1px 6px;margin-left:6px;">Featured</span>' if t.get("promoted") else ""
        return f"""<tr>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-weight:600;">{_esc(t['name'])}{featured_badge}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);">{_esc(cats)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);">{lead_badge}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;">
            <a href="/admin/tools/{t['id']}/edit" class="btn btn-ghost" style="padding:5px 12px;font-size:13px;">Edit</a>
          </td>
        </tr>"""

    pending_rows = "".join(_tool_row(t) for t in pending) or \
        '<tr><td colspan="7" style="padding:20px;color:var(--muted);">No pending submissions.</td></tr>'
    approved_rows = "".join(_approved_row(t) for t in approved) or \
        '<tr><td colspan="4" style="padding:20px;color:var(--muted);">No approved tools yet.</td></tr>'
    total_leads = sum(lead_counts.values())

    body = f"""<div class="page" style="max-width:1100px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
  <h1>CFO Toolbox—Admin</h1>
  <a href="/admin/tools/new" class="btn" style="font-size:14px;padding:8px 18px;">+ Add tool</a>
</div>
<p style="margin:0 0 24px;">
  <a href="/tools" style="font-size:13px;color:var(--muted);">View public directory →</a>
  &nbsp;&middot;&nbsp;
  <a href="/admin/tools/leads" style="font-size:13px;color:var(--muted);">View all leads ({total_leads}) →</a>
</p>

<h2 style="font-size:16px;font-weight:600;margin:0 0 12px;">Pending submissions</h2>
<div style="overflow-x:auto;margin-bottom:40px;">
<table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px;border:1px solid var(--line);overflow:hidden;">
<thead><tr style="background:var(--accent-light);">
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Date</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Name</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">URL</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Description</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Categories</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Submitted by</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Actions</th>
</tr></thead>
<tbody>{pending_rows}</tbody>
</table>
</div>

<h2 style="font-size:16px;font-weight:600;margin:0 0 12px;">Approved tools</h2>
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px;border:1px solid var(--line);overflow:hidden;">
<thead><tr style="background:var(--accent-light);">
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Name</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Categories</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Leads</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Actions</th>
</tr></thead>
<tbody>{approved_rows}</tbody>
</table>
</div>
</div>"""
    return HTMLResponse(_page("Tools Admin—CFO Toolbox", "", body, authed=True))


@app.get("/admin/tools/leads", response_class=HTMLResponse)
def admin_tools_leads(request: Request, tool_id: int | None = None):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        leads = lib.list_tool_leads(tool_id=tool_id)
        tool_name_filter = ""
        if tool_id:
            t = lib.get_tool(tool_id)
            tool_name_filter = t["name"] if t else f"Tool #{tool_id}"
    finally:
        lib.close()
    rows = "".join(
        f"""<tr>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;">{_esc(ld['created_at'][:10])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-weight:600;">{_esc(ld['tool_name'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);">{_esc(ld['name'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);"><a href="mailto:{_esc(ld['email'])}" style="color:var(--accent);">{_esc(ld['email'])}</a></td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);">{_esc(ld['company'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);">{_esc(ld['company_size'])}</td>
        </tr>"""
        for ld in leads
    ) or '<tr><td colspan="6" style="padding:20px;color:var(--muted);">No leads yet.</td></tr>'
    title_suffix = f" — {_esc(tool_name_filter)}" if tool_name_filter else ""
    body = f"""<div class="page" style="max-width:1000px;">
<p style="margin:0 0 4px;"><a href="/admin/tools" style="font-size:13px;color:var(--muted);">&larr; Tools Admin</a></p>
<h1>Warm Intro Leads{title_suffix}</h1>
<p style="color:var(--muted);margin:4px 0 24px;font-size:14px;">{len(leads)} lead{"s" if len(leads) != 1 else ""} total</p>
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px;border:1px solid var(--line);overflow:hidden;">
<thead><tr style="background:var(--accent-light);">
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Date</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Tool</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Name</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Email</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Company</th>
  <th style="padding:10px 12px;text-align:left;font-size:13px;">Size</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</div>"""
    return HTMLResponse(_page("Tool Leads—Admin", "Admin", body, authed=True))


@app.get("/admin/tools/new", response_class=HTMLResponse)
def admin_tools_new(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    body = f"""<div class="page" style="max-width:560px;">
<h1>Add a tool</h1>
<p style="color:var(--muted);margin:4px 0 32px;">Manually add a tool directly to the public directory.</p>
<form method="post" action="/admin/tools/new" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="https://…">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"
      placeholder="What does it do? 1–2 sentences."></textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes()}
    </div>
  </div>
  <div>
    <label style="display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer;">
      <input type="checkbox" name="advisor" value="1">
      <span>&#9733; Formal advisor — mark this tool with an advisor star</span>
    </label>
  </div>
  <div>
    <label style="display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer;">
      <input type="checkbox" name="promoted" value="1">
      <span>&#10024; Featured — pin to top of directory with coral badge</span>
    </label>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Vendor email <span style="font-weight:400;color:var(--muted);">(for Warm Intro lead notifications)</span></label>
    <input name="vendor_email" type="email" maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="contact@vendor.com">
  </div>
  <div>
    <button type="submit" class="btn">Add to directory</button>
    <a href="/admin/tools" class="btn btn-ghost" style="margin-left:10px;">Cancel</a>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Add Tool—CFO Toolbox", "", body, authed=True))


@app.post("/admin/tools/new")
async def admin_tools_new_submit(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    categories = [v.strip() for v in form.getlist("categories") if v.strip()]
    advisor = 1 if form.get("advisor") == "1" else 0
    promoted = 1 if form.get("promoted") == "1" else 0
    vendor_email = (form.get("vendor_email") or "").strip()
    if not (name and url and description and categories):
        raise HTTPException(status_code=400, detail="Name, URL, description, and at least one category are required.")
    lib = _lib()
    try:
        lib.add_tool(name, description, url, categories, approved=1, advisor=advisor,
                     promoted=promoted, vendor_email=vendor_email)
    finally:
        lib.close()
    return RedirectResponse("/tools", status_code=303)


@app.post("/admin/tools/{tool_id}/approve")
def admin_tools_approve(request: Request, tool_id: int):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    lib = _lib()
    try:
        lib.approve_tool(tool_id)
    finally:
        lib.close()
    return RedirectResponse("/admin/tools", status_code=303)


@app.post("/admin/tools/{tool_id}/reject")
def admin_tools_reject(request: Request, tool_id: int):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    lib = _lib()
    try:
        lib.delete_tool(tool_id)
    finally:
        lib.close()
    return RedirectResponse("/admin/tools", status_code=303)


@app.get("/admin/tools/{tool_id}/edit", response_class=HTMLResponse)
def admin_tools_edit(request: Request, tool_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        tool = lib.get_tool(tool_id)
    finally:
        lib.close()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    meta_parts = []
    if tool.get("submitted_by"):
        meta_parts.append(f"Submitted by {_esc(tool['submitted_by'])}")
    if tool.get("created_at"):
        meta_parts.append(f"Added {tool['created_at'][:10]}")
    if tool.get("updated_at") and tool["updated_at"] != tool["created_at"]:
        meta_parts.append(f"Last edited {tool['updated_at'][:10]}")
    meta_line = (" &middot; ".join(meta_parts)) if meta_parts else ""

    body = f"""<div class="page" style="max-width:560px;">
<h1>Edit tool</h1>
{f'<p style="font-size:13px;color:var(--muted);margin:-4px 0 24px;">{meta_line}</p>' if meta_line else ''}
<form method="post" action="/admin/tools/{tool_id}/edit" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200" value="{_esc(tool['name'])}"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500" value="{_esc(tool['url'])}"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;">{_esc(tool['description'])}</textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes(tool['categories'])}
    </div>
  </div>
  <div>
    <label style="display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer;">
      <input type="checkbox" name="advisor" value="1"{'checked' if tool.get('advisor') else ''}>
      <span>&#9733; Formal advisor — mark this tool with an advisor star</span>
    </label>
  </div>
  <div>
    <label style="display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer;">
      <input type="checkbox" name="promoted" value="1"{'checked' if tool.get('promoted') else ''}>
      <span>&#10024; Featured — pin to top of directory with coral badge</span>
    </label>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;color:var(--navy);margin-bottom:6px;">Vendor email <span style="font-weight:400;color:var(--muted);">(for Warm Intro lead notifications)</span></label>
    <input name="vendor_email" type="email" maxlength="200" value="{_esc(tool.get('vendor_email') or '')}"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="contact@vendor.com">
  </div>
  <div>
    <button type="submit" class="btn">Save changes</button>
    <a href="/tools" class="btn btn-ghost" style="margin-left:10px;">Cancel</a>
  </div>
</form>
</div>"""
    return HTMLResponse(_page(f"Edit {_esc(tool['name'])}—CFO Toolbox", "", body, authed=True))


@app.post("/admin/tools/{tool_id}/edit")
async def admin_tools_edit_submit(request: Request, tool_id: int):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    categories = [v.strip() for v in form.getlist("categories") if v.strip()]
    advisor = 1 if form.get("advisor") == "1" else 0
    promoted = 1 if form.get("promoted") == "1" else 0
    vendor_email = (form.get("vendor_email") or "").strip()
    if not (name and url and description and categories):
        raise HTTPException(status_code=400, detail="Name, URL, description, and at least one category are required.")
    lib = _lib()
    try:
        lib.update_tool(tool_id, name, description, url, categories, advisor=advisor,
                        promoted=promoted, vendor_email=vendor_email)
    finally:
        lib.close()
    return RedirectResponse("/tools", status_code=303)


@app.post("/admin/tools/{tool_id}/delete")
def admin_tools_delete(request: Request, tool_id: int):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    lib = _lib()
    try:
        lib.delete_tool(tool_id)
    finally:
        lib.close()
    return RedirectResponse("/tools", status_code=303)


@app.post("/tools/{tool_id}/interest")
async def tools_interest(tool_id: int, request: Request):
    _require_member(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    company = (body.get("company") or "").strip()
    company_size = (body.get("company_size") or "").strip()
    if not (name and email and company and company_size):
        return JSONResponse({"ok": False, "error": "All fields are required"}, status_code=400)
    lib = _lib()
    try:
        tools = lib.list_tools(approved_only=True)
        tool = next((t for t in tools if t["id"] == tool_id), None)
        if not tool:
            return JSONResponse({"ok": False, "error": "Tool not found"}, status_code=404)
        tool_name = tool["name"]
        vendor_email = tool.get("vendor_email") or ""
        lib.save_tool_lead(tool_id, tool_name, name, email, company, company_size)
    finally:
        lib.close()
    if vendor_email:
        try:
            from linklib.email_utils import send_lead_email
            send_lead_email(
                to=vendor_email,
                tool_name=tool_name,
                name=name,
                email=email,
                company=company,
                company_size=company_size,
            )
        except Exception:
            pass
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Private library tools
# ---------------------------------------------------------------------------

OPML_PATH = os.environ.get("LINKLIB_SITES_OPML", os.path.join(_APP_DIR, "preferred_sites.opml"))


@app.get("/feed", response_class=HTMLResponse)
def feed_reader(request: Request, cat: str = "", rl: str = ""):
    if not _is_member(request):
        return _login_redirect(request)
    is_admin = _is_authed(request)   # admin: in-app reader + save/read-later/curation

    import json as _json
    lib = _lib()
    try:
        lib_tags = [t for t, _ in lib.all_tags()[:20]]
        custom_filters = _json.loads(lib.get_setting("feed_filter_tags") or "[]")
        rl_urls = lib.read_later_urls()
        rl_items_raw = lib.list_read_later() if rl else []
    finally:
        lib.close()

    PINNED_TOPICS = ["S-1"]

    if rl:
        items = [
            {"url": r["url"], "title": r["title"] or "(no title)", "source": r["source"] or "",
             "summary": r["summary"] or "", "published_at": r["published_at"],
             "paywalled": False, "_rl_mode": True}
            for r in rl_items_raw
        ]
        categories = []
    else:
        from linklib.feed import get_feed_items
        try:
            items, categories = get_feed_items(OPML_PATH, category=cat, max_total=120)
        except Exception as e:
            return HTMLResponse(_page("CFO Feed — Brian Weisberg", "Library",
                f'<div class="page"><h2>Feed unavailable</h2><p style="color:var(--muted);">Could not load feeds: {_esc(str(e))}</p></div>',
                role=_role(request)))

    # Tab bar
    if rl:
        tabs = '<a href="/feed" class="ftab" style="margin-right:4px;">&larr; Back</a>'
    else:
        tabs = '<a href="/feed" class="ftab{active}">All</a>'.format(
            active=' ftab-on' if not cat else '',
        )
        for c in categories:
            active = ' ftab-on' if c == cat else ''
            tabs += f'<a href="/feed?cat={quote(c)}" class="ftab{active}">{_esc(c)}</a>'

    def _fmt_date(iso: str) -> str:
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.strftime("%-d %b %Y")
        except Exception:
            return iso[:10]

    sources = list(dict.fromkeys(item.get("source", "") for item in items if item.get("source")))

    # Build cards
    cards = ""
    for item in items:
        url = item["url"]
        paywalled = item.get("paywalled", False)
        paywall_badge = (' <span style="font-size:11px;background:#fef3c7;color:#92400e;padding:2px 7px;border-radius:10px;font-weight:600;vertical-align:middle;">&#128274; Paywalled</span>'
                         if paywalled else '')
        # In-app reader + save/read-later are admin-only (resale-safe); members
        # read on the original source via the card title.
        read_btn = (f'<a href="/read?url={quote(url, safe="")}" class="faction">&#9654; Read</a>'
                    if (is_admin and not paywalled) else '')
        is_rl_mode = item.get("_rl_mode", False)
        is_rl = is_rl_mode or (url in rl_urls)
        if not is_admin:
            rl_btn = save_btn = ''
        elif is_rl_mode:
            rl_btn = '<button class="faction rl-active" onclick="removeReadLater(this)">&#10003; Read later</button>'
            save_btn = ''
        else:
            rl_cls = ' rl-active' if is_rl else ''
            rl_lbl = '&#10003; Read later' if is_rl else '&#128204; Read later'
            rl_btn = f'<button class="faction{rl_cls}" onclick="toggleReadLater(this)">{rl_lbl}</button>'
            save_btn = '<button class="faction" onclick="saveItem(this)">+ Save</button>'

        summary_html = f'  <p class="fcard-summary">{_esc(item["summary"])}</p>\n' if item.get("summary") else ''
        actions = read_btn + (' ' + save_btn if save_btn else '') + ' ' + rl_btn
        cards += (
            f'<article class="fcard"'
            f' data-source="{_esc(item.get("source", ""))}"'
            f' data-text="{_esc((item["title"] + " " + (item.get("summary") or "")).lower())}"'
            f' data-rl="{1 if is_rl else 0}"'
            f' data-url="{_esc(url)}"'
            f' data-title="{_esc(item.get("title", ""))}"'
            f' data-fsrc="{_esc(item.get("source", ""))}"'
            f' data-summary="{_esc((item.get("summary") or "")[:300])}"'
            f' data-pub="{_esc(item.get("published_at") or "")}">\n'
            f'  <div class="fcard-meta">{_esc(item.get("source", ""))}'
            f'{ " &middot; " + _fmt_date(item["published_at"]) if item.get("published_at") else ""}'
            f'{paywall_badge}</div>\n'
            f'  <a class="fcard-title" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(item["title"])}</a>\n'
            f'{summary_html}'
            f'  <div class="fcard-actions">{actions}</div>\n'
            f'</article>'
        )

    if not cards:
        msg = ('No items saved to Read Later yet.' if rl
               else 'No items loaded—feeds may be warming up. Try refreshing in a moment.')
        cards = f'<p style="color:var(--muted);padding:32px 0;">{msg}</p>'

    # Source filter checkboxes
    source_checks = "".join(
        f'<label class="fsrc-label"><input type="checkbox" class="fsrc-cb" value="{_esc(s)}" checked onchange="applyFilter()"><span>{_esc(s)}</span></label>'
        for s in sources
    )

    # Topic chips: pinned + library tags + custom (deduplicated, order preserved)
    seen_t: set[str] = set()
    all_topics: list[str] = []
    for t in PINNED_TOPICS + lib_tags + custom_filters:
        if t not in seen_t:
            seen_t.add(t)
            all_topics.append(t)

    topic_chips = ""
    for t in all_topics:
        is_custom = t not in PINNED_TOPICS and t not in lib_tags
        x_part = (f' <button class="chip-x" data-keyword="{_esc(t)}"'
                  f' onclick="event.stopPropagation();removeCustomFilter(this)">&#xd7;</button>'
                  if is_custom else '')
        topic_chips += (f'<span class="topic-chip" data-keyword="{_esc(t)}"'
                        f' onclick="toggleTopic(this)">{_esc(t)}{x_part}</span>')

    custom_filters_js = _json.dumps(custom_filters)

    # Sources section (hidden in rl mode since items come from DB)
    if not rl:
        src_section = f"""<div style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
        <span style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;">Sources</span>
        <button onclick="setAll(true)" style="font-size:12px;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;">Select all</button>
        <button onclick="setAll(false)" style="font-size:12px;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;">Clear all</button>
        <span id="filter-count" style="font-size:12px;color:var(--muted);margin-left:auto;"></span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;">{source_checks}</div>
    </div>"""
    else:
        src_section = ""

    filter_panel = f"""<div id="filter-panel" style="display:none;border-bottom:1px solid var(--line);background:#fff;padding:14px 24px;">
  <div style="max-width:860px;margin:0 auto;">
    {src_section}
    <div>
      <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">Topics</div>
      <div id="topic-chips" style="display:flex;flex-wrap:wrap;gap:8px;">{topic_chips}</div>
      <div style="margin-top:8px;display:flex;gap:6px;align-items:center;">
        <input id="custom-topic-input" placeholder="Add keyword&#x2026;"
          style="padding:4px 10px;border:1px solid var(--line);border-radius:20px;font-size:12px;background:#fff;width:130px;"
          onkeydown="if(event.key==='Enter')addCustomFilter();">
        <button onclick="addCustomFilter()"
          style="font-size:12px;color:var(--accent);background:none;border:1px solid var(--line);border-radius:20px;padding:4px 10px;cursor:pointer;">+ Add</button>
      </div>
    </div>
  </div>
</div>"""

    rl_count = f' ({len(rl_items_raw)})' if rl_items_raw else ''
    rl_link = '/feed' if rl else '/feed?rl=1'
    rl_extra = ' style="background:var(--accent);color:#fff;border-color:var(--accent);"' if rl else ''

    feed_css = """<style>
.ftab{display:inline-block;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;
  color:var(--muted);text-decoration:none;border:1px solid transparent;}
.ftab:hover{color:var(--ink);text-decoration:none;background:var(--accent-light);}
.ftab-on{background:var(--accent);color:#fff !important;}
.fcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 20px;}
.fcard-meta{font-size:12px;color:var(--muted);margin-bottom:5px;}
.fcard-title{font-size:16px;font-weight:600;color:var(--ink);text-decoration:none;display:block;margin-bottom:6px;line-height:1.35;}
.fcard-title:hover{color:var(--accent);text-decoration:none;}
.fcard-summary{font-size:14px;color:#5a5248;margin:0 0 10px;line-height:1.5;}
.fcard-actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;}
.faction{font-size:13px;font-weight:500;color:var(--accent);background:none;border:1px solid var(--line);
  border-radius:8px;padding:5px 12px;cursor:pointer;text-decoration:none;}
.faction:hover{background:var(--accent-light);text-decoration:none;}
.faction.saved{color:var(--muted);pointer-events:none;}
.faction.rl-active{background:var(--accent-light);border-color:var(--accent);}
.fsrc-label{display:flex;align-items:center;gap:5px;font-size:13px;cursor:pointer;
  background:var(--bg);border:1px solid var(--line);border-radius:20px;padding:4px 10px;
  user-select:none;transition:background .1s;}
.fsrc-label:hover{background:var(--accent-light);}
.fsrc-label input{accent-color:var(--accent);cursor:pointer;}
.filter-btn{font-size:13px;font-weight:500;color:var(--accent);background:none;border:1px solid var(--line);
  border-radius:20px;padding:6px 14px;cursor:pointer;white-space:nowrap;text-decoration:none;display:inline-block;}
.filter-btn:hover{background:var(--accent-light);text-decoration:none;}
.topic-chip{display:inline-flex;align-items:center;gap:3px;padding:4px 10px;border-radius:20px;
  font-size:12px;cursor:pointer;background:var(--accent-light);color:var(--accent);
  border:1px solid transparent;user-select:none;transition:background .1s;}
.topic-chip:hover{border-color:var(--accent);}
.topic-chip.chip-on{background:var(--accent);color:#fff;}
.chip-x{background:none;border:none;cursor:pointer;color:inherit;font-size:11px;
  padding:0;margin-left:1px;opacity:.7;line-height:1;}
.chip-x:hover{opacity:1;}
</style>"""

    feed_js = f"""<script>
var customFilters = {custom_filters_js};
var activeTopics = new Set();
function toggleFilter() {{
  var p = document.getElementById('filter-panel');
  var btn = document.getElementById('filter-btn');
  var open = p.style.display === 'none';
  p.style.display = open ? 'block' : 'none';
  btn.style.background = open ? 'var(--accent-light)' : 'none';
  if (open) updateCount();
}}
function setAll(checked) {{
  document.querySelectorAll('.fsrc-cb').forEach(function(cb) {{ cb.checked = checked; }});
  applyFilter();
}}
function toggleTopic(chip) {{
  var kw = chip.dataset.keyword;
  if (activeTopics.has(kw)) {{ activeTopics.delete(kw); chip.classList.remove('chip-on'); }}
  else {{ activeTopics.add(kw); chip.classList.add('chip-on'); }}
  applyFilter();
}}
function applyFilter() {{
  var hasCbs = document.querySelectorAll('.fsrc-cb').length > 0;
  var selected = new Set();
  document.querySelectorAll('.fsrc-cb:checked').forEach(function(cb) {{ selected.add(cb.value); }});
  var visible = 0;
  document.querySelectorAll('.fcard').forEach(function(card) {{
    var showSrc = !hasCbs || selected.has(card.dataset.source);
    var showTopic = activeTopics.size === 0;
    if (!showTopic) {{
      var txt = card.dataset.text || '';
      activeTopics.forEach(function(kw) {{ if (txt.indexOf(kw.toLowerCase()) !== -1) showTopic = true; }});
    }}
    var show = showSrc && showTopic;
    card.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  updateCount(visible);
}}
function updateCount(n) {{
  var el = document.getElementById('filter-count');
  if (!el) return;
  var total = document.querySelectorAll('.fcard').length;
  if (n === undefined) n = total;
  el.textContent = n + ' of ' + total + ' shown';
}}
function saveItem(btn) {{
  var card = btn.closest('.fcard');
  var url = card.dataset.url;
  var t = prompt('Tags (comma-separated, optional):');
  if (t === null) return;
  btn.textContent = 'Saving…';
  btn.classList.add('saved');
  fetch('/feed/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: 'url=' + encodeURIComponent(url) + '&tags=' + encodeURIComponent(t)
  }})
  .then(function(r) {{ btn.textContent = r.ok ? '✓ Saved' : '✗ Error'; }})
  .catch(function() {{ btn.textContent = '✗ Error'; btn.classList.remove('saved'); }});
}}
async function toggleReadLater(btn) {{
  var card = btn.closest('.fcard');
  var isRl = card.dataset.rl === '1';
  var params = new URLSearchParams({{
    url: card.dataset.url, action: isRl ? 'remove' : 'add',
    title: card.dataset.title || '', source: card.dataset.fsrc || '',
    summary: card.dataset.summary || '', published_at: card.dataset.pub || ''
  }});
  btn.disabled = true;
  try {{
    var r = await fetch('/feed/read-later', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: params
    }});
    if (r.ok) {{
      var newRl = isRl ? '0' : '1';
      card.dataset.rl = newRl;
      btn.innerHTML = newRl === '1' ? '&#10003; Read later' : '&#128204; Read later';
      btn.classList.toggle('rl-active', newRl === '1');
    }}
  }} finally {{ btn.disabled = false; }}
}}
async function removeReadLater(btn) {{
  var card = btn.closest('.fcard');
  var params = new URLSearchParams({{url: card.dataset.url, action: 'remove'}});
  btn.disabled = true;
  try {{
    var r = await fetch('/feed/read-later', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: params
    }});
    if (r.ok) {{
      card.style.transition = 'opacity .25s';
      card.style.opacity = '0';
      setTimeout(function() {{ card.remove(); }}, 260);
    }}
  }} finally {{ btn.disabled = false; }}
}}
function addCustomFilter() {{
  var input = document.getElementById('custom-topic-input');
  var kw = input.value.trim();
  if (!kw || customFilters.indexOf(kw) !== -1) {{ input.value = ''; return; }}
  customFilters.push(kw);
  input.value = '';
  document.getElementById('topic-chips').appendChild(makeTopicChip(kw));
  saveCustomFilters();
}}
function removeCustomFilter(btn) {{
  var kw = btn.dataset.keyword;
  customFilters = customFilters.filter(function(k) {{ return k !== kw; }});
  if (activeTopics.has(kw)) {{ activeTopics.delete(kw); applyFilter(); }}
  btn.closest('.topic-chip').remove();
  saveCustomFilters();
}}
function makeTopicChip(kw) {{
  var span = document.createElement('span');
  span.className = 'topic-chip';
  span.dataset.keyword = kw;
  span.onclick = function() {{ toggleTopic(span); }};
  span.appendChild(document.createTextNode(kw + ' '));
  var x = document.createElement('button');
  x.className = 'chip-x';
  x.dataset.keyword = kw;
  x.innerHTML = '&times;';
  x.onclick = function(e) {{ e.stopPropagation(); removeCustomFilter(x); }};
  span.appendChild(x);
  return span;
}}
function saveCustomFilters() {{
  fetch('/feed/filters', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{filters: customFilters}})
  }});
}}
</script>"""

    body = f"""<div style="border-bottom:1px solid var(--line);padding:12px 24px;position:sticky;top:0;z-index:5;background:var(--bg);">
  <div style="max-width:860px;margin:0 auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <div style="display:flex;gap:8px;flex-wrap:wrap;flex:1;">{tabs}</div>
    <a href="{rl_link}" class="filter-btn"{rl_extra}>&#128204; Read Later{rl_count}</a>
    <button onclick="toggleFilter()" id="filter-btn" class="filter-btn">&#9776; Sources &amp; Topics</button>
  </div>
</div>
{filter_panel}
<main id="feed-main" style="max-width:860px;margin:0 auto;padding:24px 24px 80px;display:grid;gap:12px;">
{cards}
</main>
{feed_css}
{feed_js}"""

    return HTMLResponse(_page("CFO Feed—Brian Weisberg", "Library", body, role=_role(request)))


_READER_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600&family=DM+Sans:opsz,wght@9..40,400;9..40,500&display=swap');
:root{--ink:#1a1a1a;--muted:#6F6A60;--line:#E4E0D6;--bg:#F5F4EF;--surface:#FFFFFF;--accent:#002975;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);font:18px/1.75 'Source Serif 4',Georgia,serif;}
a{color:var(--accent);text-decoration:underline;text-underline-offset:3px;}
a:hover{opacity:.8;}

.reader-bar{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--line);
  padding:0 24px;height:48px;display:flex;align-items:center;justify-content:space-between;
  font-family:'DM Sans',sans-serif;font-size:13px;color:var(--muted);}
.reader-bar .back{color:var(--accent);text-decoration:none;font-weight:500;display:flex;align-items:center;gap:6px;}
.reader-bar .back:hover{opacity:.8;}
.reader-controls{display:flex;align-items:center;gap:16px;}
.reader-controls button{background:none;border:none;cursor:pointer;font:inherit;color:var(--muted);
  padding:4px 8px;border-radius:6px;font-size:13px;}
.reader-controls button:hover{background:var(--line);}

.reader-wrap{max-width:680px;margin:0 auto;padding:56px 24px 100px;}

.reader-meta{margin-bottom:40px;padding-bottom:32px;border-bottom:1px solid var(--line);}
.reader-meta h1{font-size:clamp(22px,4vw,32px);font-weight:600;line-height:1.25;letter-spacing:-.02em;
  margin-bottom:16px;}
.reader-meta .byline{font-family:'DM Sans',sans-serif;font-size:14px;color:var(--muted);line-height:1.5;}
.reader-meta .source-link{color:var(--accent);}

.reader-body{font-size:var(--fs,18px);line-height:1.78;}
.reader-body p{margin-bottom:1.4em;}
.reader-body h1,.reader-body h2,.reader-body h3,.reader-body h4{
  font-weight:600;line-height:1.3;letter-spacing:-.01em;margin:2em 0 .6em;}
.reader-body h1{font-size:1.5em;}
.reader-body h2{font-size:1.25em;}
.reader-body h3{font-size:1.1em;}
.reader-body ul,.reader-body ol{padding-left:1.5em;margin-bottom:1.4em;}
.reader-body li{margin-bottom:.4em;}
.reader-body blockquote{border-left:3px solid var(--line);padding-left:1.2em;color:var(--muted);
  font-style:italic;margin:1.5em 0;}
.reader-body img{max-width:100%;height:auto;border-radius:8px;margin:1.5em 0;}
.reader-body figure{margin:1.5em 0;}
.reader-body figcaption{font-size:.85em;color:var(--muted);font-family:'DM Sans',sans-serif;margin-top:.4em;}
.reader-body table{width:100%;border-collapse:collapse;font-size:.9em;margin:1.5em 0;}
.reader-body th,.reader-body td{padding:8px 12px;border:1px solid var(--line);text-align:left;}
.reader-body th{background:#f4f0e8;font-family:'DM Sans',sans-serif;}
.reader-body pre,.reader-body code{font-family:ui-monospace,monospace;font-size:.85em;
  background:#f0ece4;border-radius:4px;padding:2px 5px;}
.reader-body pre{padding:16px;overflow-x:auto;border-radius:8px;margin:1.5em 0;}
.reader-body pre code{background:none;padding:0;}
.reader-body hr{border:none;border-top:1px solid var(--line);margin:2.5em 0;}

.reader-empty{text-align:center;padding:60px 20px;color:var(--muted);font-family:'DM Sans',sans-serif;}
.reader-empty h2{font-size:18px;margin-bottom:12px;color:var(--ink);}
"""

_READER_TMPL = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<style>{css}</style>
</head><body>
<div class="reader-bar">
  <a class="back" href="{back_url}">&#8592; {back_label}</a>
  <div class="reader-controls">
    <button onclick="adj(-2)">A&minus;</button>
    <button onclick="adj(2)">A+</button>
    {article_controls}
    <a href="{orig_url}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:13px;">Original &rarr;</a>
  </div>
</div>
<div class="reader-wrap">
  <div class="reader-meta">
    <h1>{title}</h1>
    <div class="byline">{byline}</div>
    {tags_block}
  </div>
  <div class="reader-body">{body}</div>
</div>
{article_script}
<script>
var fs = parseInt(localStorage.getItem('reader-fs') || '18');
document.documentElement.style.setProperty('--fs', fs + 'px');
function adj(d) {{
  fs = Math.max(14, Math.min(28, fs + d));
  document.documentElement.style.setProperty('--fs', fs + 'px');
  localStorage.setItem('reader-fs', fs);
}}
</script>
</body></html>"""


@app.get("/read", response_class=HTMLResponse)
def reader(request: Request, url: str = "", id: int = 0):
    # Admin-only by design: the in-app reader renders full article text, which we
    # don't serve to members (resale-safe). Members link out to the source instead.
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib.extract import fetch_page
    import html as html_mod

    back_url = "/archive"
    back_label = "Archive"

    # Try to load from DB first (may have cached content)
    article = None
    if id:
        lib = _lib()
        try:
            row = lib.conn.execute("SELECT * FROM articles WHERE id=?", (id,)).fetchone()
            if row:
                article = dict(row)
                import json as _json
                article["tags"] = _json.loads(article.get("tags_json") or "[]")
        finally:
            lib.close()
        if article:
            url = article["url"]

    if not url:
        body_html = """<div class="reader-empty">
  <h2>Read any article</h2>
  <p style="margin-bottom:1.5rem;">Paste a URL below, or open an article from your
    <a href="/archive">Archive</a> or <a href="/feed">Feed</a>.</p>
  <form method="get" action="/read"
        style="display:flex;gap:8px;max-width:500px;margin:0 auto;">
    <input type="url" name="url" placeholder="https://…" autofocus required
      style="flex:1;padding:10px 14px;border:1px solid #d0cac0;border-radius:10px;
             font-size:16px;background:#fff;font-family:inherit;">
    <button type="submit"
      style="padding:10px 20px;background:#002975;color:#fff;border:none;
             border-radius:10px;font-size:16px;font-family:inherit;cursor:pointer;
             white-space:nowrap;">Read</button>
  </form>
</div>"""
        return HTMLResponse(_READER_TMPL.format(
            title="Reader", css=_READER_CSS, back_url=back_url, back_label=back_label,
            orig_url="#", byline="", body=body_html,
        ))

    # Fetch content — use cached DB content if available and non-empty
    cached_content = (article or {}).get("content", "")
    cached_title = (article or {}).get("title", "")

    if cached_content and len(cached_content) > 200:
        title = cached_title or url
        content = cached_content
    else:
        try:
            page = fetch_page(url)
            title = page.title or cached_title or url
            content = page.content or ""
        except Exception:
            title = cached_title or url
            content = ""

    # Build byline from article metadata if available
    byline_parts = []
    if article:
        if article.get("author"):
            byline_parts.append(_esc(article["author"]))
        if article.get("source"):
            byline_parts.append(_esc(article["source"]))
        if article.get("published_at"):
            byline_parts.append(article["published_at"][:10])
    byline_parts.append(f'<a class="source-link" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url[:60])}{"…" if len(url) > 60 else ""}</a>')
    byline = " &middot; ".join(byline_parts)

    if content:
        # content from extract.py is plain text with newlines — convert to paragraphs
        # but also handle if it looks like it already has HTML tags
        if "<p>" in content or "<div" in content:
            body_html = content
        else:
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            body_html = "".join(f"<p>{html_mod.escape(p)}</p>" for p in paragraphs) if paragraphs else ""
    else:
        body_html = f"""<div class="reader-empty">
          <h2>Content could not be extracted</h2>
          <p>Some sites block automated access. Try reading the original.</p>
          <p style="margin-top:16px;"><a href="{_esc(url)}" target="_blank" rel="noopener">Open original article &rarr;</a></p>
        </div>"""

    # Article management controls — only shown when loaded by id from the DB
    article_controls = ""
    tags_block = ""
    article_script = ""
    if article:
        aid = article["id"]
        current_tags = article.get("tags", [])
        tags_csv = _esc(",".join(current_tags))
        tag_spans = "".join(
            f'<span style="font-size:12px;font-weight:600;color:var(--navy);background:var(--seafoam);'
            f'border-radius:6px;padding:2px 8px;margin-right:4px;">{_esc(t)}</span>'
            for t in current_tags
        )
        tags_block = f"""<div id="reader-tags" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;">
  {tag_spans}
  <button onclick="openReaderTagEditor()" style="font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:6px;padding:2px 8px;cursor:pointer;margin-left:4px;">Edit tags</button>
</div>
<div id="reader-tag-editor" style="display:none;margin-top:10px;">
  <input type="text" id="reader-tag-input" value="{tags_csv}"
    placeholder="comma-separated tags"
    style="width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-family:inherit;font-size:14px;background:#fff;">
  <div style="display:flex;gap:8px;margin-top:6px;">
    <button onclick="saveReaderTags()" style="padding:5px 14px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;">Save</button>
    <button onclick="closeReaderTagEditor()" style="padding:5px 14px;background:none;border:1px solid var(--line);border-radius:8px;cursor:pointer;font-size:13px;color:var(--muted);">Cancel</button>
  </div>
</div>"""
        article_controls = (
            f'<button onclick="deleteArticle({aid})" '
            f'style="font-size:13px;color:#b91c1c;background:none;border:1px solid #fca5a5;'
            f'border-radius:6px;padding:4px 10px;cursor:pointer;">Delete</button>'
        )
        article_script = f"""<script>
var _articleId = {aid};
function openReaderTagEditor() {{
  document.getElementById('reader-tags').style.display = 'none';
  document.getElementById('reader-tag-editor').style.display = 'block';
  document.getElementById('reader-tag-input').focus();
}}
function closeReaderTagEditor() {{
  document.getElementById('reader-tag-editor').style.display = 'none';
  document.getElementById('reader-tags').style.display = 'flex';
}}
async function saveReaderTags() {{
  var val = document.getElementById('reader-tag-input').value;
  var tags = val.split(',').map(function(t) {{ return t.trim(); }}).filter(Boolean);
  try {{
    var r = await fetch('/library/' + _articleId + '/tags', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{tags: tags}})
    }});
    if (!r.ok) throw new Error();
    var d = await r.json();
    var box = document.getElementById('reader-tags');
    var esc = function(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }};
    var spans = (d.tags || []).map(function(t) {{
      return '<span style="font-size:12px;font-weight:600;color:var(--navy);background:var(--seafoam);border-radius:6px;padding:2px 8px;margin-right:4px;">' + esc(t) + '</span>';
    }}).join('');
    var editBtn = '<button onclick="openReaderTagEditor()" style="font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:6px;padding:2px 8px;cursor:pointer;margin-left:4px;">Edit tags</button>';
    box.innerHTML = spans + editBtn;
    closeReaderTagEditor();
  }} catch(e) {{
    alert('Could not save tags — please try again.');
  }}
}}
async function deleteArticle(id) {{
  if (!confirm('Permanently delete this article from your archive?')) return;
  try {{
    var form = new FormData();
    var r = await fetch('/library/' + id + '/delete', {{method: 'POST', body: form}});
    if (r.redirected) {{ window.location.href = r.url; return; }}
    window.location.href = '/library';
  }} catch(e) {{
    alert('Could not delete — please try again.');
  }}
}}
</script>"""

    return HTMLResponse(_READER_TMPL.format(
        title=_esc(title), css=_READER_CSS,
        back_url=back_url, back_label=back_label,
        orig_url=_esc(url), byline=byline,
        body=body_html,
        article_controls=article_controls,
        tags_block=tags_block,
        article_script=article_script,
    ))


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, q: str = ""):
    if not _is_member(request):
        return _login_redirect(request)
    authed = _is_authed(request)   # admin: shows tag-edit / delete controls
    lib = _lib()
    try:
        results = lib.search(q, limit=100)
        total = lib.count()
        tags = lib.all_tags()[:25]
    finally:
        lib.close()

    def _card(r):
        tags_csv = _esc(",".join(r.get("tags", [])))
        # Tags are auto-generated; show them as labels. Editing is behind "Edit tags".
        tag_spans = "".join(
            f'<span class="tag-chip" data-tag="{_esc(t)}">{_esc(t)}</span>'
            for t in r.get("tags", [])
        )
        url = _esc(r['url'])
        if authed:
            # Admin: in-app reader + curation controls.
            editor = f"""<div id="tag-editor-{r['id']}" style="display:none;margin-top:8px;">
            <input type="text" id="tag-input-{r['id']}" value="{tags_csv}"
              placeholder="comma-separated tags"
              style="width:100%;padding:6px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:13px;background:#fff;">
            <div style="display:flex;gap:6px;margin-top:6px;">
              <button class="postbtn" onclick="saveTags({r['id']})">Save</button>
              <button class="postbtn" onclick="cancelTags({r['id']})">Cancel</button>
            </div>
          </div>"""
            actions = (f'<a href="/read?id={r["id"]}" class="postbtn" style="text-decoration:none;">Read</a>'
                       f'<button class="postbtn" onclick="openTagEditor({r["id"]})">Edit tags</button>'
                       f'<form method="post" action="/library/{r["id"]}/delete" style="display:contents;" '
                       f'onsubmit="return confirm(\'Permanently delete this article?\');">'
                       f'<button type="submit" class="postbtn" style="color:#b91c1c;">Delete</button></form>')
        else:
            # Member: read on the original source (no in-app full text).
            editor = ""
            actions = f'<a href="{url}" target="_blank" rel="noopener" class="postbtn" style="text-decoration:none;">Read on source &rarr;</a>'
        return f"""<article class="card" id="card-{r['id']}">
          <a class="card-title" href="{url}" target="_blank" rel="noopener">{_esc(r['title'])}</a>
          <div class="meta">{_esc(r.get('source',''))}{' &middot; ' + _esc(r['saved_at'][:10]) if r.get('saved_at') else ''}</div>
          <p class="summary">{_esc(r.get('summary',''))[:280]}</p>
          <div class="tags" id="tags-{r['id']}" data-tags="{tags_csv}">{tag_spans}</div>
          {editor}
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">{actions}</div>
        </article>"""

    cards = "".join(_card(r) for r in results) or '<p style="color:var(--muted);">No matches.</p>'

    tagbar = "".join(
        f'<a href="/archive?q={_esc(t)}">{_esc(t)} <em>{c}</em></a>' for t, c in tags
    )

    page_body = f"""<div style="border-bottom:1px solid var(--line);padding:20px 24px;">
  <div style="max-width:780px;margin:0 auto;">
    <div style="font-size:13px;color:var(--muted);margin-bottom:10px;display:flex;align-items:center;gap:16px;">
      <span>{total} saved</span>
      <a href="/read" style="color:var(--accent);font-weight:500;">&#9654; Article Reader</a>
    </div>
    <form method="get" action="/archive" style="display:flex;gap:8px;max-width:680px;">
      <input type="search" name="q" value="{_esc(q)}" placeholder="Search titles, summaries, notes, tags…"
             style="flex:1;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font-size:15px;background:#fff;" autofocus>
      <button type="submit" class="btn">Search</button>
    </form>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;">{tagbar}</div>
    <div style="display:flex;gap:8px;margin-top:14px;max-width:680px;">
      <textarea id="askq" rows="2" placeholder="Ask your archive an FP&amp;A question…"
        style="flex:1;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"></textarea>
      <button class="btn" onclick="ask()">Ask</button>
    </div>
    <div style="margin-top:6px;max-width:680px;text-align:right;">
      <a id="more-opts-link" href="/ask" style="font-size:12px;color:var(--muted);">More options (model, effort, sources) &rarr;</a>
    </div>
    <div id="answer" style="display:none;margin-top:14px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;font-size:15px;max-width:680px;"></div>
  </div>
</div>
<main style="max-width:780px;margin:0 auto;padding:20px 24px;display:grid;gap:14px;">
{cards}
</main>
<style>
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px;}}
.card-title{{font-size:16px;font-weight:600;color:var(--ink);}}
.card-title:hover{{color:var(--accent);}}
.meta{{color:var(--muted);font-size:13px;margin:3px 0 8px;}}
.summary{{margin:0 0 10px;color:#3a352e;font-size:14px;}}
.tags{{display:flex;flex-wrap:wrap;gap:6px;}}
.tags span{{font-size:11px;font-weight:600;color:var(--navy);background:var(--seafoam);border-radius:6px;padding:3px 9px;display:inline-flex;align-items:center;gap:2px;}}
.tag-x-btn{{background:none;border:none;cursor:pointer;color:var(--muted);font-size:10px;padding:0;line-height:1;opacity:.7;}}
.tag-x-btn:hover{{color:#b91c1c;opacity:1;}}
.postbtn{{margin-top:12px;padding:6px 12px;font-size:12px;background:transparent;color:var(--accent);border:1px solid var(--line);border-radius:8px;cursor:pointer;}}
.postbtn:hover{{background:var(--accent-light);}}
nav.site-nav a[href="/library"]{{color:var(--ink);font-weight:600;}}  /* bold the Library nav item while in the Archive */
</style>
<script>
function openTagEditor(id) {{
  document.getElementById('tag-editor-' + id).style.display = 'block';
  document.getElementById('tag-input-' + id).focus();
}}
function cancelTags(id) {{
  document.getElementById('tag-editor-' + id).style.display = 'none';
}}
async function _doSaveTags(id, tags) {{
  var r = await fetch('/library/' + id + '/tags', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{tags: tags}})
  }});
  if (!r.ok) throw new Error('failed');
  var d = await r.json();
  var box = document.getElementById('tags-' + id);
  var esc = function(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }};
  box.innerHTML = (d.tags || []).map(function(t) {{
    var et = esc(t);
    var btn = '<button class="tag-x-btn" data-article-id="' + id + '" data-tag="' + et + '" onclick="quickRemoveTagBtn(this)">&times;</button>';
    return '<span class="tag-chip" data-tag="' + et + '">' + et + ' ' + btn + '</span>';
  }}).join('');
  box.dataset.tags = (d.tags || []).join(',');
  var input = document.getElementById('tag-input-' + id);
  if (input) input.value = (d.tags || []).join(', ');
  document.getElementById('tag-editor-' + id).style.display = 'none';
}}
async function saveTags(id) {{
  var input = document.getElementById('tag-input-' + id);
  var tags = input.value.split(',').map(function(t) {{ return t.trim(); }}).filter(Boolean);
  try {{ await _doSaveTags(id, tags); }} catch(e) {{ alert('Could not save tags — please try again.'); }}
}}
async function quickRemoveTag(id, tag) {{
  var box = document.getElementById('tags-' + id);
  var current = (box.dataset.tags || '').split(',').map(function(t) {{ return t.trim(); }}).filter(Boolean);
  try {{ await _doSaveTags(id, current.filter(function(t) {{ return t !== tag; }})); }}
  catch(e) {{ alert('Could not remove tag — please try again.'); }}
}}
function quickRemoveTagBtn(btn) {{
  quickRemoveTag(parseInt(btn.dataset.articleId), btn.dataset.tag);
}}
async function ask(){{
  var q=document.getElementById('askq').value.trim();
  if(!q)return;
  var link=document.getElementById('more-opts-link');
  if(link) link.href='/ask?q='+encodeURIComponent(q);
  var box=document.getElementById('answer');
  box.style.display='block';box.innerHTML='<em>Thinking…</em>';
  try{{
    var r=await fetch('/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q}})}});
    var d=await r.json();
    var idx=1;
    var lib=(d.sources||[]).map(function(s){{return '<li><a href="'+s.url+'" target="_blank">['+(idx++)+'] '+s.title+'</a></li>';}}).join('');
    var feed=(d.feed_sources||[]).map(function(s){{return '<li><a href="'+s.url+'" target="_blank">['+(idx++)+'] '+s.title+'</a></li>';}}).join('');
    var web=(d.web_sources||[]).map(function(s){{return '<li><a href="'+s.url+'" target="_blank">&#127760; '+s.title+'</a></li>';}}).join('');
    box.innerHTML='<p>'+(d.answer||'').replace(/\\n/g,'<br>')+'</p>'+((lib||feed||web)?'<ul style="padding-left:18px;font-size:13px;">'+lib+feed+web+'</ul>':'');
  }}catch(e){{box.innerHTML='Something went wrong.';}}
}}
</script>"""

    return HTMLResponse(_page("Archive—Brian Weisberg", "Library", page_body, role=_role(request)))


@app.get("/library", response_class=HTMLResponse)
def library(request: Request):
    """Account hub: a landing page linking to the Archive, Feed, and Ask."""
    if not _is_member(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        total = lib.count()
    finally:
        lib.close()

    def _hcard(href, title, desc):
        return (
            f'<a href="{href}" style="display:block;border:1px solid var(--line);background:var(--surface);'
            f'border-radius:14px;padding:22px 24px;text-decoration:none;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span style="font-family:var(--font-head);font-weight:600;font-size:19px;color:var(--navy);letter-spacing:-0.01em;">{title}</span>'
            f'<span style="color:var(--navy);font-size:18px;line-height:1;">&rarr;</span></div>'
            f'<p style="margin:7px 0 0;font-size:14.5px;color:var(--muted);line-height:1.5;">{desc}</p></a>'
        )

    cards = "".join([
        _hcard("/archive", "Archive", f"Search {total:,} saved articles by title, summary, or tag &mdash; your curated reading history."),
        _hcard("/feed", "Feed", "The latest from the sources you follow, in one reader. Save anything worth keeping to the Archive."),
        _hcard("/ask", "Ask", "Put an FP&amp;A question to your archive &mdash; a cited answer drawn from the Archive plus trusted web sources."),
    ])

    body = f"""<div class="page" style="max-width:680px;">
<h1 style="margin:0 0 6px;">Library</h1>
<p style="color:var(--muted);margin:0 0 26px;">Your private workspace &mdash; the curated archive, the live feed, and the FP&amp;A assistant.</p>
<div style="display:grid;gap:14px;">{cards}</div>
</div>"""
    return HTMLResponse(_page("Library—Brian Weisberg", "Library", body, role=_role(request)))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/search")
def api_search(request: Request, q: str = "", limit: int = 50, token: str | None = None):
    _require_member(request, token)
    lib = _lib()
    try:
        return {"query": q, "results": lib.search(q, limit=limit)}
    finally:
        lib.close()


@app.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request, q: str = ""):
    # Member-gated: signed-in members and admin. Anonymous visitors go to login.
    if not _is_member(request):
        return _login_redirect(request)
    authed = _is_authed(request)   # admin flag (e.g. for any admin-only affordances)

    from linklib.agent import COST_ESTIMATES

    # Build model radio rows
    models = [
        ("claude-haiku-4-5-20251001", "Fast &middot; cost-effective"),
        ("claude-sonnet-4-6",         "Balanced &middot; default"),
        ("claude-opus-4-8",           "Best quality"),
    ]
    # Logged-in (Brian) gets the balanced default; anonymous users default to
    # the most efficient model.
    default_model = "claude-sonnet-4-6" if authed else "claude-haiku-4-5-20251001"

    def model_row(mid, desc, checked):
        chk = " checked" if checked else ""
        return (
            f'<label class="ask-radio-label">'
            f'<input type="radio" name="model" value="{mid}" onchange="updateEstimate()"{chk}>'
            f'<span class="ask-radio-id">{mid}</span>'
            f'<span class="ask-radio-desc">{desc}</span>'
            f'</label>'
        )

    model_rows = "".join(model_row(mid, desc, mid == default_model) for mid, desc in models)

    effort_details = [
        ("quick",    "Quick",    "4 archive &middot; 2 web searches &middot; ~700 tokens out"),
        ("standard", "Standard", "8 archive &middot; 4 web searches &middot; ~1,500 tokens out"),
        ("deep",     "Deep",     "16 archive &middot; 6 web searches &middot; ~2,500 tokens out"),
    ]

    def effort_row(val, label, detail, checked):
        chk = " checked" if checked else ""
        return (
            f'<label class="ask-radio-label">'
            f'<input type="radio" name="effort" value="{val}" onchange="updateEstimate()"{chk}>'
            f'<strong>{label}</strong>'
            f'<span class="ask-radio-desc">{detail}</span>'
            f'</label>'
        )

    default_effort = "standard" if authed else "quick"
    effort_rows = "".join(effort_row(v, l, d, v == default_effort) for v, l, d in effort_details)

    # Cost estimates are for Brian's eyes only — never exposed to anonymous
    # users. When not authed, the cost table is empty and the estimate line is
    # omitted from the page entirely.
    import json as _json
    cost_js = _json.dumps(COST_ESTIMATES) if authed else "{}"
    cost_span = ('<span id="cost-est" style="font-size:13px;color:var(--muted);"></span>'
                 if authed else "")

    pre_q = _esc(q)

    body = f"""<div class="page" style="max-width:820px;">
<h1 style="margin-bottom:6px;">Ask a question</h1>
<p style="color:var(--muted);margin:0 0 28px;">Query your saved archive, RSS feed, and trusted web sources. Tune cost vs. depth before each query.</p>

<div class="ask-card">
  <label style="display:block;font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">Question</label>
  <textarea id="ask-q" rows="3" autofocus placeholder="e.g. What frameworks do CFOs use for headcount planning in uncertain environments?"
    style="width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--bg);resize:vertical;">{pre_q}</textarea>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin:16px 0;">

  <div class="ask-card">
    <div class="ask-section-label">Sources</div>
    <div style="display:flex;flex-direction:column;gap:8px;">
      <label class="ask-check-label"><input type="checkbox" id="src-library" checked onchange="updateEstimate()"> My saved archive</label>
      <label class="ask-check-label"><input type="checkbox" id="src-feed" onchange="updateEstimate()"> Current RSS feed</label>
      <label class="ask-check-label"><input type="checkbox" id="src-web" checked onchange="updateEstimate()"> Web search (trusted sites)</label>
    </div>
  </div>

  <div class="ask-card">
    <div class="ask-section-label">Model</div>
    <div style="display:flex;flex-direction:column;gap:10px;">
      {model_rows}
    </div>
  </div>

  <div class="ask-card">
    <div class="ask-section-label">Effort</div>
    <div style="display:flex;flex-direction:column;gap:10px;">
      {effort_rows}
    </div>
  </div>

</div>

<div style="display:flex;align-items:center;gap:20px;margin-bottom:20px;">
  <button class="btn" onclick="doAsk()" id="ask-btn" style="padding:11px 28px;font-size:15px;">Ask</button>
  {cost_span}
</div>

<div id="ask-thread"></div>
<div id="ask-capped" style="display:none;margin-top:14px;padding:12px 16px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2);font-size:14px;color:var(--muted);">
  You&rsquo;ve reached the limit for this conversation. <a href="#" onclick="resetConvo();return false;" style="color:var(--navy);font-weight:600;">Start a new question</a>.
</div>
</div>

<style>
.ask-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:0;}}
.ask-section-label{{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;}}
.ask-check-label{{display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;}}
.ask-check-label input{{accent-color:var(--accent);width:15px;height:15px;cursor:pointer;flex-shrink:0;}}
.ask-radio-label{{display:flex;flex-direction:column;gap:2px;cursor:pointer;padding:6px 0;border-top:1px solid var(--line);}}
.ask-radio-label:first-child{{border-top:none;padding-top:0;}}
.ask-radio-label input{{accent-color:var(--accent);width:14px;height:14px;margin-bottom:3px;}}
.ask-radio-id{{font-family:ui-monospace,monospace;font-size:12px;color:var(--ink);font-weight:500;}}
.ask-radio-desc{{font-size:12px;color:var(--muted);}}
.ask-answer{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 24px;font-size:15px;line-height:1.7;}}
.ask-answer p{{margin:0 0 14px;}}
.ask-q-bubble{{background:var(--navy-wash);border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:14px;font-weight:600;color:var(--navy);margin-bottom:8px;}}
.ask-src-list{{margin:16px 0 0;padding-top:14px;border-top:1px solid var(--line);list-style:none;padding-left:0;display:flex;flex-direction:column;gap:6px;}}
.ask-src-list li{{font-size:13px;}}
.ask-src-list a{{color:var(--accent);}}
nav.site-nav a[href="/ask"]{{color:var(--ink);font-weight:600;}}
</style>

<script>
var COST = {cost_js};

function updateEstimate() {{
  var model = document.querySelector('input[name="model"]:checked');
  var effort = document.querySelector('input[name="effort"]:checked');
  var el = document.getElementById('cost-est');
  if (!model || !effort || !el) return;
  var c = (COST[model.value] || {{}})[effort.value];
  el.textContent = c != null ? '~$' + c.toFixed(3) + ' estimated per query' : '';
}}

var convo = [];        // [{{role, content}}] prior turns, sent as history
var asked = false;

function escapeHtml(s) {{
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function answerToHtml(text) {{
  return '<p>' + escapeHtml(text).replace(/\\n\\n/g,'</p><p>').replace(/\\n/g,'<br>') + '</p>';
}}
function srcListHtml(d) {{
  var items = [];
  (d.sources || []).forEach(function(s, i) {{
    items.push('<li>&#128218; <a href="' + encodeURI(s.url) + '" target="_blank" rel="noopener">[' + (i+1) + '] ' + escapeHtml(s.title) + '</a></li>');
  }});
  var feedOffset = (d.sources || []).length;
  (d.feed_sources || []).forEach(function(s, i) {{
    items.push('<li>&#128240; <a href="' + encodeURI(s.url) + '" target="_blank" rel="noopener">[' + (feedOffset+i+1) + '] ' + escapeHtml(s.title) + '</a></li>');
  }});
  (d.web_sources || []).forEach(function(s) {{
    items.push('<li>&#127760; <a href="' + encodeURI(s.url) + '" target="_blank" rel="noopener">' + escapeHtml(s.title) + '</a></li>');
  }});
  return items.length ? '<ul class="ask-src-list">' + items.join('') + '</ul>' : '';
}}
function resetConvo() {{
  convo = []; asked = false;
  document.getElementById('ask-thread').innerHTML = '';
  document.getElementById('ask-capped').style.display = 'none';
  var btn = document.getElementById('ask-btn'); btn.disabled = false; btn.textContent = 'Ask';
  var q = document.getElementById('ask-q'); q.placeholder = 'e.g. What frameworks do CFOs use for headcount planning in uncertain environments?'; q.focus();
}}

async function doAsk() {{
  var qEl = document.getElementById('ask-q');
  var q = qEl.value.trim();
  if (!q) {{ qEl.focus(); return; }}

  var model = document.querySelector('input[name="model"]:checked')?.value || 'claude-sonnet-4-6';
  var effort = document.querySelector('input[name="effort"]:checked')?.value || 'standard';
  var sources = [];
  if (document.getElementById('src-library').checked) sources.push('library');
  if (document.getElementById('src-feed').checked) sources.push('feed');
  if (document.getElementById('src-web').checked) sources.push('web');
  if (!sources.length) {{ alert('Select at least one source.'); return; }}

  var btn = document.getElementById('ask-btn');
  var thread = document.getElementById('ask-thread');
  var turn = document.createElement('div');
  turn.style.marginTop = '18px';
  turn.innerHTML = '<div class="ask-q-bubble">' + escapeHtml(q) + '</div>' +
                   '<div class="ask-answer"><em style="color:var(--muted);">Querying sources…</em></div>';
  thread.appendChild(turn);
  var answerEl = turn.querySelector('.ask-answer');

  btn.disabled = true; btn.textContent = 'Thinking…';
  qEl.value = '';
  turn.scrollIntoView({{behavior:'smooth', block:'nearest'}});

  try {{
    var resp = await fetch('/ask', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ question: q, model: model, effort: effort, sources: sources, history: convo }})
    }});
    var d = await resp.json();
    if (!resp.ok) {{
      answerEl.innerHTML = '<span style="color:var(--alert);">' + escapeHtml(d.detail || 'Error') + '</span>';
      btn.disabled = false; btn.textContent = asked ? 'Ask follow-up' : 'Ask';
      return;
    }}

    answerEl.innerHTML = answerToHtml(d.answer) + srcListHtml(d);

    if (d.capped) {{
      document.getElementById('ask-capped').style.display = 'block';
      btn.disabled = true; btn.textContent = 'Limit reached';
      return;
    }}

    convo.push({{role:'user', content:q}});
    convo.push({{role:'assistant', content:d.answer}});
    asked = true;
    qEl.placeholder = 'Ask a follow-up…';
    btn.disabled = false; btn.textContent = 'Ask follow-up';

    if (d.followups_left === 0) {{
      document.getElementById('ask-capped').style.display = 'block';
      btn.disabled = true; btn.textContent = 'Limit reached';
    }}
  }} catch(e) {{
    answerEl.innerHTML = '<span style="color:var(--alert);">Something went wrong: ' + escapeHtml(String(e)) + '</span>';
    btn.disabled = false; btn.textContent = asked ? 'Ask follow-up' : 'Ask';
  }}
}}

document.addEventListener('keydown', function(e) {{
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') doAsk();
}});

updateEstimate();
</script>"""

    return HTMLResponse(_page("Ask—Brian Weisberg", "Library", body, role=_role(request)))


@app.post("/ask")
async def ask(request: Request):
    _require_member(request)
    from linklib.agent import answer_question, count_prior_questions, MAX_FOLLOWUPS
    payload = await request.json()
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question required")

    model = (payload.get("model") or "")
    effort = (payload.get("effort") or "standard")

    # Conversation history for follow-ups: [{role, content}, ...]. The follow-up
    # cap is enforced here (invisible cost guard) — a capped conversation never
    # reaches the API.
    history = payload.get("history") or []
    if not isinstance(history, list):
        history = []
    prior_questions = count_prior_questions(history)
    if prior_questions >= 1 + MAX_FOLLOWUPS:
        return {
            "capped": True,
            "answer": "We've reached the limit for this conversation. "
                      "Start a new question to keep going.",
            "sources": [], "feed_sources": [], "web_sources": [],
        }

    raw_sources = payload.get("sources") or ["library", "web"]
    if isinstance(raw_sources, str):
        raw_sources = [s.strip() for s in raw_sources.split(",")]
    use_library = "library" in raw_sources
    use_feed    = "feed"    in raw_sources
    use_web     = "web"     in raw_sources

    lib = _lib()
    try:
        ans = answer_question(
            lib, question,
            model=model,
            effort=effort,
            use_library=use_library,
            use_feed=use_feed,
            use_web=use_web,
            opml_path=OPML_PATH if (use_feed or use_web) else None,
            history=history,
        )
        followups_left = max(0, MAX_FOLLOWUPS - prior_questions)
        return {
            "answer": ans.text,
            "sources":      [{"title": s["title"], "url": s["url"]} for s in ans.sources],
            "feed_sources": [{"title": s["title"], "url": s["url"]} for s in ans.feed_sources],
            "web_sources":  ans.web_sources,
            "followups_left": followups_left,
        }
    finally:
        lib.close()


@app.post("/save")
async def save(request: Request, background_tasks: BackgroundTasks, token: str | None = None):
    _check_token(token or request.headers.get("X-Save-Token"))
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        form = await request.form()
        payload = dict(form)
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    lib = _lib()
    try:
        row = ingest_url(lib, url, tags=tags, notes=payload.get("note", ""))
        background_tasks.add_task(backup.maybe_backup, DB_PATH)
        return JSONResponse({"ok": True, "id": row["id"], "title": row.get("title"), "tags": row.get("tags", [])})
    finally:
        lib.close()


# Library management lives on its own page (/admin/library) so the hub stays
# uncluttered. Ordered as the recommended workflow — top to bottom.
_LIBRARY_TOOLS = [
    ("/admin/backup",       "Archive backup",      "Snapshot the database before you start, so you can roll back if needed."),
    ("/admin/backfill",     "Historical sweep",    "Catch up the back catalog: queue older articles from your sources (raise the per-source limit to reach further back)."),
    ("/admin/queue",        "Archive Queue",       "Review proposed saves, fix dates, edit tags, and approve them into the archive."),
    ("/admin/tags",         "Tag cleanup",         "Merge, rename, or remove tags so the vocabulary is tidy before you learn from it."),
    ("/admin/tag-style",    "Tagging style",       "Learn how you tag from your archive and edit the guide, so auto-tagging matches your judgment."),
    ("/admin/enrich",       "Re-enrich archive",   "The big pass: force-refresh summaries + tags on Opus, applying your tag style and the scope rules."),
    ("/admin/review-removals", "Review removals",  "Confirm or keep what the re-enrich flagged as off-audience (podcasts, predictions, fund/LP content)."),
    ("/admin/dedupe",       "Find duplicates",     "Catch near-duplicate articles (similar content within ~3 months) from a source and remove them."),
]

# Admin sections — grouped on the hub; each links to its own page.
_ADMIN_GROUPS = [
    ("Site", "Your voice, your brand, and the public site.", [
        ("/admin/social",       "Social",              "Draft LinkedIn posts in your voice."),
        ("/draft",              "Draft",               "Draft and refine a post in a conversation."),
        ("/admin/brand",        "Brand standards",     "Visual standards, color system, and your writing voice."),
        ("/admin/contacts",     "Contact submissions", "Messages from the public contact form."),
        ("/community",          "CFO Community (parked)", "Your community idea + form — parked off the public site for now."),
        ("/admin/open-source",  "Open source",         "The open-source projects this site is built on — with gratitude."),
    ]),
    ("CFO Toolbox", "The public tools directory and the leads it brings in.", [
        ("/admin/tools",        "Tool submissions",    "Review the CFO Toolbox approval queue and manage featured/vendor settings."),
        ("/admin/tools/leads",  "Tool leads",          "Warm Intro requests — name, email, company, and size for each tool."),
    ]),
    ("Access", "Member accounts and who can see what.", [
        ("/admin/users",        "Users",               "Create and manage member accounts for the gated sections."),
    ]),
]

# Flat view kept for any code/tests that iterate every section.
_ADMIN_SECTIONS = _LIBRARY_TOOLS + [s for _, _, items in _ADMIN_GROUPS for s in items]


# Open-source the site is built on — celebrated on /admin/open-source.
# (name, dist-for-version-lookup or None, license, homepage, what we use it for)
_OPEN_SOURCE = [
    ("Runs the site", "The web stack every page and request is served through.", [
        ("FastAPI", "fastapi", "MIT", "https://fastapi.tiangolo.com",
         "The web framework the whole app is written in — every route, page, and API."),
        ("Starlette", "starlette", "BSD-3-Clause", "https://www.starlette.io",
         "The ASGI toolkit under FastAPI — routing, responses, and the test client."),
        ("Uvicorn", "uvicorn", "BSD-3-Clause", "https://www.uvicorn.org",
         "The fast ASGI server that actually runs the site in production."),
        ("Pydantic", "pydantic", "MIT", "https://docs.pydantic.dev",
         "Parses and validates incoming request data behind FastAPI."),
        ("python-multipart", "python-multipart", "Apache-2.0", "https://github.com/Kludex/python-multipart",
         "Reads the form posts — login, contact, and tool submissions."),
    ]),
    ("Stores & searches", "Where your archive lives and how it's searched.", [
        ("SQLite + FTS5", None, "Public Domain", "https://www.sqlite.org",
         "The entire database is a single SQLite file, with FTS5 powering full-text search across your archive."),
        ("Python", None, "PSF License", "https://www.python.org",
         "The language it's all written in — and its standard library does a lot of the quiet heavy lifting."),
    ]),
    ("Reads the web", "Fetching articles and feeds, and making sense of messy pages.", [
        ("Requests", "requests", "Apache-2.0", "https://requests.readthedocs.io",
         "Fetches article pages and RSS/Atom feeds."),
        ("Beautiful Soup", "beautifulsoup4", "MIT", "https://www.crummy.com/software/BeautifulSoup/",
         "Parses real-world HTML — the fallback full-text extractor."),
        ("trafilatura", "trafilatura", "Apache-2.0", "https://trafilatura.readthedocs.io",
         "The preferred extractor — pulls clean article text out of a noisy page."),
        ("lxml", "lxml", "BSD-3-Clause", "https://lxml.de",
         "The fast C-backed parser the extractors lean on."),
    ]),
    ("Intelligence", "The AI behind enrichment, Ask, drafting, and dedupe verification.", [
        ("Anthropic SDK", "anthropic", "MIT", "https://github.com/anthropics/anthropic-sdk-python",
         "The Python client for Claude — summaries, auto-tags, cited Ask answers, post drafts, and duplicate checks."),
    ]),
    ("Built & kept tidy", "The tools that make and maintain the site — including a couple we leaned on right here.", [
        ("pytest", "pytest", "MIT", "https://pytest.org",
         "Runs the test suite that guards every change."),
        ("pyflakes", "pyflakes", "MIT", "https://github.com/PyCQA/pyflakes",
         "Keeps the wire clean — catches unused imports and dead code on every push (just wired into CI)."),
        ("Pillow", "pillow", "HPND", "https://python-pillow.org",
         "Drew the compass-rose favicons — the PNG and .ico — from a few lines of code."),
        ("httpx", "httpx", "BSD-3-Clause", "https://www.python-httpx.org",
         "The HTTP client powering the test client."),
    ]),
    ("Type & craft", "The look of the site.", [
        ("Outfit", None, "SIL OFL 1.1", "https://fonts.google.com/specimen/Outfit",
         "The headline typeface."),
        ("DM Sans", None, "SIL OFL 1.1", "https://fonts.google.com/specimen/DM+Sans",
         "The body typeface."),
    ]),
]


@app.get("/admin/open-source", response_class=HTMLResponse)
def admin_open_source(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    from importlib.metadata import version as _pkg_version
    import sqlite3 as _sql
    import platform as _platform

    def _ver(name: str, dist: str | None) -> str:
        if dist:
            try:
                return "v" + _pkg_version(dist)
            except Exception:
                return ""
        if name == "Python":
            return "v" + _platform.python_version()
        if name.startswith("SQLite"):
            return "v" + _sql.sqlite_version
        return ""

    groups_html = ""
    for title, blurb, items in _OPEN_SOURCE:
        cards = ""
        for name, dist, lic, url, role in items:
            ver = _ver(name, dist)
            ver_block = (f'<div style="margin:1px 0 6px;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;">{_esc(ver)}</div>'
                         if ver else '<div style="height:6px;"></div>')
            cards += (
                f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
                f'style="display:block;background:var(--bg);border:1px solid var(--line);border-radius:12px;'
                f'padding:14px 16px;text-decoration:none;">'
                f'<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;">'
                f'<span style="font-family:var(--font-head);font-weight:600;font-size:15px;color:var(--navy);">{_esc(name)}</span>'
                f'<span style="font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;'
                f'background:var(--seafoam-wash);color:var(--seafoam-deep);border-radius:5px;padding:2px 7px;white-space:nowrap;flex-shrink:0;">{_esc(lic)}</span>'
                f'</div>'
                f'{ver_block}'
                f'<p style="margin:0;font-size:13px;color:var(--ink-soft);line-height:1.5;">{_esc(role)}</p>'
                f'</a>'
            )
        groups_html += (
            f'<section style="margin-bottom:28px;">'
            f'<h2 style="font-size:16px;font-weight:700;margin:0 0 2px;">{_esc(title)}</h2>'
            f'<p style="font-size:13px;color:var(--muted);margin:0 0 12px;">{_esc(blurb)}</p>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;">{cards}</div>'
            f'</section>'
        )

    # A lighter "built with open-source love" strip. Logos via Simple Icons
    # (CC0 / public-domain, itself an open-source project), in each brand's color.
    _ICONS = [("python", "Python"), ("fastapi", "FastAPI"), ("sqlite", "SQLite"),
              ("pydantic", "Pydantic"), ("pytest", "pytest"), ("anthropic", "Anthropic"),
              ("githubactions", "GitHub Actions"), ("railway", "Railway")]
    icons_html = "".join(
        f'<img src="https://cdn.simpleicons.org/{slug}" alt="{_esc(label)}" title="{_esc(label)}" '
        f'height="30" loading="lazy" style="opacity:.85;">'
        for slug, label in _ICONS)
    love = (f'<div style="margin-top:40px;padding-top:28px;border-top:1px solid var(--line);text-align:center;">'
            f'<div style="display:flex;flex-wrap:wrap;gap:26px;align-items:center;justify-content:center;margin-bottom:14px;">{icons_html}</div>'
            f'<p style="font-size:13px;color:var(--muted);margin:0;">Built with open-source love. '
            f'Icons by <a href="https://simpleicons.org" target="_blank" rel="noopener" style="color:var(--accent);">Simple Icons</a> (CC0).</p>'
            f'</div>')

    total = sum(len(items) for _, _, items in _OPEN_SOURCE)
    body = f"""<div class="page" style="max-width:880px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Built with open source</h1>
<p style="color:var(--ink-soft);margin:-4px 0 6px;font-size:16px;line-height:1.6;">This whole site stands on the shoulders of {total}-plus open-source projects&mdash;maintained by people who gave their work away so the rest of us could build. From the framework that serves every page to the tiny tool that keeps the code tidy and the one that drew the favicon, none of it would exist without them. With gratitude. &#129518;</p>
<p style="color:var(--muted);margin:0 0 28px;font-size:13px;">If you maintain one of these &mdash; thank you. Consider <a href="https://opencollective.com" target="_blank" rel="noopener" style="color:var(--accent);">sponsoring a maintainer</a> you rely on.</p>
{groups_html}
{love}
</div>"""
    return HTMLResponse(_page("Open source — Admin", "Admin", body, authed=True))


def _auth_cookie_banner(request: Request, background_tasks: BackgroundTasks) -> str:
    """Subscriber-cookie status panel for the Admin hub: shows each configured
    paid-newsletter cookie's health (working / expired / untested) with a
    Re-check button, and refresh steps when one is stale. Only rendered when
    LINKLIB_AUTH_COOKIES is set. Kicks a background re-check when the stored
    status is missing or stale."""
    from linklib.extract import _auth_cookies
    cookies = _auth_cookies()
    if not cookies:
        return ""   # feature dormant until cookies are configured

    from linklib import authcheck
    lib = _lib()
    try:
        status = authcheck.get_auth_status(lib)
    finally:
        lib.close()

    age = authcheck.status_age_seconds(status)
    if age is None or age > 12 * 3600:          # stale or never run -> refresh in background
        background_tasks.add_task(_auth_recheck_background)

    stale = authcheck.stale_domains(status)
    any_bad = bool(stale)
    border = "var(--coral)" if any_bad else "var(--seafoam)"
    wash = "var(--coral-wash)" if any_bad else "var(--seafoam-wash)"

    # Per-domain status lines.
    rows = ""
    for dom in cookies:
        s = status.get(dom)
        if not s or s.get("ok") is None:
            dot, label, detail = "&#9679;", "untested", (s or {}).get("detail", "not checked yet")
            color = "var(--muted)"
        elif s.get("ok"):
            dot, label, color = "&#9679;", "working", "var(--seafoam-deep)"
            detail = s.get("detail", "")
        else:
            dot, label, color = "&#9679;", "expired", "var(--coral-deep)"
            detail = s.get("detail", "")
        checked = _esc((s or {}).get("checked_at", "")[:16].replace("T", " ")) if s else ""
        rows += (f'<div style="display:flex;align-items:baseline;gap:8px;font-size:13.5px;margin:2px 0;">'
                 f'<span style="color:{color};">{dot}</span>'
                 f'<strong>{_esc(dom)}</strong>'
                 f'<span style="color:{color};font-weight:600;">{label}</span>'
                 f'<span style="color:var(--muted);">&mdash; {_esc(detail)}{(" &middot; " + checked) if checked else ""}</span></div>')

    refresh_steps = ""
    if any_bad:
        refresh_steps = ("""<p style="font-size:13px;color:var(--ink-soft);margin:10px 0 6px;line-height:1.55;">To refresh an expired cookie: log into the site, open DevTools &rarr; <strong>Network</strong>, reload, click the request to the domain, copy the full <code>Cookie:</code> header, and update <code>LINKLIB_AUTH_COOKIES</code> in Railway &rarr; Variables.</p>""")

    heading = ("Subscriber cookie expired" if any_bad else "Subscriber access")
    return f"""<div style="background:{wash};border:1px solid {border};border-radius:12px;padding:16px 18px;margin:0 0 22px;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
    <div style="font-family:var(--font-head);font-weight:600;font-size:15px;color:var(--navy);">{heading}</div>
    <form method="post" action="/admin/auth/recheck" style="margin:0;"><button type="submit" class="btn btn-ghost" style="font-size:13px;padding:6px 14px;">Re-check now</button></form>
  </div>
  <div style="margin-top:8px;">{rows}</div>
  {refresh_steps}
</div>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)

    auth_banner = _auth_cookie_banner(request, background_tasks)

    def _card(href, title, desc):
        return (
            f'<a href="{href}" style="display:block;background:var(--surface);border:1px solid var(--line);'
            f'border-radius:14px;padding:20px 22px;text-decoration:none;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--navy);letter-spacing:-0.01em;">{title}</span>'
            f'<span style="color:var(--navy);font-size:18px;line-height:1;">&rarr;</span></div>'
            f'<p style="margin:6px 0 0;font-size:14px;color:var(--muted);line-height:1.5;">{desc}</p></a>'
        )

    # Archive gets a single prominent card linking to its own management page,
    # so the hub stays uncluttered.
    library_card = _card("/admin/library", "Archive",
                         f"Build, curate, enrich, and back up your archive &mdash; {len(_LIBRARY_TOOLS)} tools.")

    groups_html = f'<div style="margin-bottom:22px;">{library_card}</div>'
    for i, (gname, gdesc, items) in enumerate(_ADMIN_GROUPS):
        cards = "".join(_card(*s) for s in items)
        open_attr = ""   # all groups start collapsed — click to expand
        groups_html += (
            f'<details class="admin-group"{open_attr} style="margin-bottom:14px;background:transparent;border:1px solid var(--line);border-radius:14px;overflow:hidden;">'
            f'<summary style="list-style:none;cursor:pointer;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">'
            f'<span style="font-size:15px;text-transform:uppercase;letter-spacing:.08em;color:var(--navy);font-weight:600;">{gname}</span>'
            f'<span style="font-size:12px;color:var(--muted);">{len(items)} {"tool" if len(items)==1 else "tools"}</span>'
            f'</span>'
            f'<span class="admin-chevron" style="color:var(--navy);font-size:13px;line-height:1;transition:transform .15s;">&#9660;</span>'
            f'</summary>'
            f'<div style="padding:0 20px 20px;">'
            f'<p style="margin:0 0 14px;font-size:13.5px;color:var(--muted);">{gdesc}</p>'
            f'<div style="display:grid;gap:14px;">{cards}</div>'
            f'</div>'
            f'</details>'
        )

    body = f"""<div class="page" style="max-width:720px;">
<style>
.admin-group summary::-webkit-details-marker{{display:none;}}
.admin-group[open] .admin-chevron{{transform:rotate(180deg);}}
.admin-group summary:hover{{background:var(--surface);}}
</style>
<h1>Admin</h1>
<p style="color:var(--muted);margin:4px 0 26px;">Manage the site&rsquo;s private tools.</p>
{auth_banner}
<div style="background:var(--coral-wash);border:1px solid var(--coral);border-radius:12px;padding:16px 18px;margin:0 0 28px;">
  <div style="font-family:var(--font-head);font-weight:600;font-size:15px;color:var(--coral-deep);margin-bottom:6px;">Before opening the archive to paid subscribers &mdash; read this</div>
  <p style="font-size:13.5px;color:var(--ink-soft);margin:0 0 8px;line-height:1.55;">The archive stores the full text of other people&rsquo;s articles. That&rsquo;s fine for your own research, but charging readers for access to it would mean redistributing content you don&rsquo;t own. Settle licensing with the authors you can, and before any paid access goes live:</p>
  <ul style="font-size:13.5px;color:var(--ink-soft);margin:0;padding-left:18px;line-height:1.6;">
    <li>Make subscriber-facing feed items <strong>link out</strong> to the original source; keep the in-app reader (<code>/read</code>) private to you.</li>
    <li>Serve only <strong>summaries, tags, and citations</strong> &mdash; never the stored full text (the <code>content</code> field).</li>
    <li>Tighten <code>agent.py</code> so an answer can never fall back to raw <code>content</code> when a summary is missing (today it can, at <code>_format_all_sources</code>).</li>
  </ul>
</div>
{groups_html}
</div>"""
    return HTMLResponse(_page("Admin — Brian Weisberg", "Admin", body, authed=True))


@app.get("/admin/library", response_class=HTMLResponse)
def admin_library(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)

    def _step(n, href, title, desc):
        return (
            f'<a href="{href}" style="display:flex;gap:16px;align-items:flex-start;background:var(--surface);'
            f'border:1px solid var(--line);border-radius:14px;padding:18px 20px;text-decoration:none;">'
            f'<span style="flex-shrink:0;width:30px;height:30px;border-radius:50%;background:var(--navy);color:#fff;'
            f'display:flex;align-items:center;justify-content:center;font-family:var(--font-head);font-weight:600;font-size:15px;">{n}</span>'
            f'<span style="flex:1;">'
            f'<span style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--navy);letter-spacing:-0.01em;">{title}</span>'
            f'<span style="color:var(--navy);font-size:18px;line-height:1;">&rarr;</span></span>'
            f'<span style="display:block;margin:6px 0 0;font-size:14px;color:var(--muted);line-height:1.5;">{desc}</span>'
            f'</span></a>'
        )

    cards = "".join(_step(i + 1, href, title, desc)
                    for i, (href, title, desc) in enumerate(_LIBRARY_TOOLS))
    body = f"""<div class="page" style="max-width:720px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Archive</h1>
<p style="color:var(--muted);margin:4px 0 26px;">Build, curate, enrich, and back up your archive. For a first-time cleanup, work top to bottom &mdash; each step sets up the next. You can also jump to any tool directly anytime.</p>
<div style="display:grid;gap:12px;">{cards}</div>
</div>"""
    return HTMLResponse(_page("Archive — Admin", "Admin", body, authed=True))


def _auth_recheck_background() -> None:
    """Probe the configured auth cookies and store their health. Runs off-request."""
    lib = _lib()
    try:
        from linklib import authcheck
        authcheck.check_auth_cookies(lib, OPML_PATH)
    except Exception:
        pass
    finally:
        lib.close()


@app.post("/admin/auth/recheck")
def admin_auth_recheck(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        from linklib import authcheck
        authcheck.check_auth_cookies(lib, OPML_PATH)
    finally:
        lib.close()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/social", response_class=HTMLResponse)
def admin_social(request: Request, url: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)

    from linklib.social import DEFAULT_MODEL

    models = [
        ("claude-haiku-4-5-20251001", "Haiku", "Fast &amp; cheap"),
        ("claude-sonnet-4-6",         "Sonnet", "Balanced &mdash; default"),
        ("claude-opus-4-8",           "Opus",   "Best quality"),
    ]
    modes = [
        ("original",      "Original POV",  "Your take sparked by the article — not a summary"),
        ("amplification", "Amplify",       "Signal genuine resonance, build past the original"),
        ("self_promo",    "Self-promote",  "Promote your own work: lean, single-analogy"),
    ]

    def _radio(name, value, label, detail, checked):
        chk = " checked" if checked else ""
        return (
            f'<label style="display:flex;align-items:flex-start;gap:8px;font-size:14px;cursor:pointer;padding:6px 0;border-top:1px solid var(--line);">'
            f'<input type="radio" name="{name}" value="{value}"{chk} style="margin-top:3px;accent-color:var(--accent);flex-shrink:0;">'
            f'<span><strong>{label}</strong><span style="display:block;font-size:12px;color:var(--muted);">{detail}</span></span>'
            f'</label>'
        )

    model_radios = "".join(
        _radio("model", mid, label, detail, mid == DEFAULT_MODEL)
        for mid, label, detail in models
    )
    mode_radios = "".join(
        _radio("post-mode", val, label, detail, val == "original")
        for val, label, detail in modes
    )

    pre_url = _esc(url)

    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Social</h1>

<h2 style="margin-top:0;">LinkedIn post generator</h2>
<p style="color:var(--muted);margin:-6px 0 20px;">Draft a post in your voice from any URL or topic.</p>

<div style="background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:16px;">
  <div style="display:grid;gap:16px;">
    <div>
      <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">Article URL</label>
      <input id="post-url" type="url" value="{pre_url}" placeholder="https://…"
        style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--bg);">
    </div>
    <div>
      <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px;">Or topic <span style="font-weight:400;text-transform:none;letter-spacing:0;">(used when URL is blank; pulls from your archive)</span></label>
      <input id="post-topic" type="text" placeholder="e.g. headcount planning in uncertain environments"
        style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--bg);">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
      <div>
        <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:0;">Mode</div>
        <div style="display:flex;flex-direction:column;">{mode_radios}</div>
      </div>
      <div>
        <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:0;">Model</div>
        <div style="display:flex;flex-direction:column;">{model_radios}</div>
      </div>
    </div>
    <div>
      <button id="draft-btn" onclick="doDraft()" class="btn" style="padding:11px 28px;font-size:15px;">Draft post</button>
      <span style="font-size:13px;color:var(--muted);margin-left:14px;">&#8984;&#9166; to draft</span>
    </div>
  </div>
</div>

<div id="draft-result" style="display:none;background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:40px;">
  <div id="draft-output" style="white-space:pre-wrap;line-height:1.75;font-size:15px;color:var(--ink);"></div>
  <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line);display:flex;gap:10px;">
    <button class="btn btn-ghost" onclick="navigator.clipboard.writeText(document.getElementById('draft-output').innerText)" style="font-size:13px;">Copy</button>
    <button class="btn btn-ghost" onclick="doDraft()" style="font-size:13px;">Redraft</button>
  </div>
</div>

</div>

<script>
async function doDraft() {{
  var url = document.getElementById('post-url').value.trim();
  var topic = document.getElementById('post-topic').value.trim();
  var mode = document.querySelector('input[name="post-mode"]:checked')?.value || 'original';
  var model = document.querySelector('input[name="model"]:checked')?.value || '{_esc(DEFAULT_MODEL)}';
  if (!url && !topic) {{ document.getElementById('post-url').focus(); return; }}
  var btn = document.getElementById('draft-btn');
  var result = document.getElementById('draft-result');
  var output = document.getElementById('draft-output');
  btn.disabled = true; btn.textContent = 'Drafting…';
  result.style.display = 'block';
  output.textContent = 'Drafting in your voice…';
  result.scrollIntoView({{behavior:'smooth', block:'nearest'}});
  try {{
    var payload = {{mode: mode, model: model}};
    if (url) payload.url = url; else payload.topic = topic;
    var r = await fetch('/post', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
    var d = await r.json();
    output.textContent = d.post || '(no output)';
  }} catch(e) {{
    output.textContent = 'Something went wrong: ' + e;
  }} finally {{
    btn.disabled = false; btn.textContent = 'Draft post';
  }}
}}

document.addEventListener('keydown', function(e) {{
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') doDraft();
}});
</script>"""
    return HTMLResponse(_page("Social — Admin", "Admin", body, authed=True))


# ---------------------------------------------------------------------------
# Archive Queue — staging area for proposed saves
# ---------------------------------------------------------------------------

def _scan_feed_background() -> None:
    """Pull current feed items into the queue (enriched). Runs off-request."""
    lib = _lib()
    try:
        from linklib.queue import scan_feed_into_queue
        scan_feed_into_queue(lib, OPML_PATH)
        backup.maybe_backup(DB_PATH)
    except Exception:
        pass
    finally:
        lib.close()


def _redate_background(source: str) -> None:
    """Re-read true publish dates from article pages for a source. Off-request."""
    lib = _lib()
    try:
        from linklib.queue import redate_from_article_pages
        redate_from_article_pages(lib, source)
        backup.maybe_backup(DB_PATH)
    except Exception:
        pass
    finally:
        lib.close()


def _suggest_background(source: str) -> None:
    """Predict keep/skip for a source's pending queue from past picks. Off-request.
    Records a status so the queue page can show what happened (no silent no-ops)."""
    import json as _json
    from datetime import datetime, timezone
    lib = _lib()
    try:
        from linklib.suggest import suggest_approvals
        preds = suggest_approvals(lib, source)
        now = datetime.now(timezone.utc).isoformat()
        if preds is None:
            note = (f"Couldn’t predict {source}. Approve a few articles first so it has "
                    f"something to learn from — or the AI may be briefly unavailable.")
            n = 0
        elif not preds:
            note = f"No pending candidates for {source}."
            n = 0
        else:
            existing = _json.loads(lib.get_setting("queue_suggestions") or "{}")
            existing.update(preds)
            lib.set_setting("queue_suggestions", _json.dumps(existing))
            n = len(preds)
            note = f"Predicted {n} {source} candidate{'s' if n != 1 else ''} from your past picks."
        lib.set_setting("queue_suggest_status",
                        _json.dumps({"source": source, "n": n, "note": note, "at": now}))
    except Exception as exc:
        try:
            from datetime import datetime, timezone
            lib.set_setting("queue_suggest_status", _json.dumps(
                {"source": source, "n": 0, "note": f"Prediction failed: {exc}",
                 "at": datetime.now(timezone.utc).isoformat()}))
        except Exception:
            pass
    finally:
        lib.close()


@app.get("/admin/queue", response_class=HTMLResponse)
def admin_queue(request: Request, scanning: int = 0, redating: int = 0, suggesting: int = 0):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        pending = lib.list_queue(status="pending")
        dismissed_n = lib.queue_count(status="dismissed")
        import json as _json
        suggestions = _json.loads(lib.get_setting("queue_suggestions") or "{}")
        suggest_status = _json.loads(lib.get_setting("queue_suggest_status") or "{}")
    finally:
        lib.close()

    # Group by source so you can approve a whole publication at once.
    groups: dict[str, list[dict]] = {}
    for c in pending:
        groups.setdefault(c.get("source") or "Other", []).append(c)

    def _short_model(m: str) -> str:
        # claude-opus-4-8 -> opus; claude-haiku-4-5-20251001 -> haiku
        parts = (m or "").split("-")
        return parts[1] if len(parts) > 1 and parts[0] == "claude" else (m or "")

    def _badge(c: dict) -> str:
        if c.get("enriched"):
            label = "enriched"
            sm = _short_model(c.get("enrich_model") or "")
            if sm:
                label = f"enriched &middot; {_esc(sm)}"
            return ('<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;'
                    f'background:var(--seafoam-wash);color:var(--seafoam-deep);">{label}</span>')
        return ('<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;'
                'background:var(--surface-2);color:var(--muted);">needs enrichment</span>')

    def _card(c: dict) -> str:
        url = _esc(c["url"])
        title = _esc(c.get("title") or c["url"])
        date = _esc((c.get("published_at") or "")[:10])
        summary = _esc((c.get("summary") or "")[:340])
        tags_list = c.get("suggested_tags") or []
        tags_val = _esc(", ".join(tags_list))
        chips = "".join(
            f'<span style="font-size:11px;font-weight:600;color:var(--navy);background:var(--seafoam);border-radius:6px;padding:2px 8px;">{_esc(t)}</span>'
            for t in tags_list
        ) or '<span style="font-size:12px;color:var(--muted);">auto-tagged on enrich</span>'
        sub_badge = ('<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;'
                     'background:var(--coral-wash);color:var(--coral-deep);margin-right:6px;">Reader suggestion</span>'
                     if (c.get("origin") or "").startswith("submission:") else "")
        meta = f"{sub_badge}{date}" if date else sub_badge
        # Approval prediction (advisory) from the suggestion engine.
        sug = suggestions.get(c["url"])
        suggest_attr = ""
        suggest_badge = ""
        if sug:
            keep = bool(sug.get("keep"))
            suggest_attr = f' data-suggest="{"keep" if keep else "skip"}"'
            reason = _esc(sug.get("reason", ""))
            if keep:
                suggest_badge = (f'<div style="font-size:12px;color:var(--seafoam-deep);margin:0 0 8px;">'
                                 f'&#10003; <strong>Likely keep</strong>{(" &mdash; " + reason) if reason else ""}</div>')
            else:
                suggest_badge = (f'<div style="font-size:12px;color:var(--coral-deep);margin:0 0 8px;">'
                                 f'&#8855; <strong>Likely skip</strong>{(" &mdash; " + reason) if reason else ""}</div>')
        return f"""<div data-card data-url="{url}"{suggest_attr} style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:12px;">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">
    <a href="{url}" target="_blank" rel="noopener" style="font-family:var(--font-head);font-weight:600;font-size:16px;color:var(--navy);line-height:1.35;">{title}</a>
    {_badge(c)}
  </div>
  <div style="font-size:12px;color:var(--muted);margin:3px 0 8px;">{meta}</div>
  {suggest_badge}
  <p style="font-size:14px;color:var(--ink-soft);margin:0 0 12px;line-height:1.55;">{summary}</p>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
    {chips}
    <button type="button" onclick="editQTags(this)" style="background:none;border:none;color:var(--muted);font-size:12px;cursor:pointer;text-decoration:underline;padding:0;">edit</button>
  </div>
  <input class="qtags" type="text" value="{tags_val}" style="display:none;width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:9px;font:inherit;font-size:14px;background:var(--bg);margin-bottom:12px;">
  <div style="display:flex;gap:9px;">
    <button class="add-btn btn" onclick="addOne(this)" style="font-size:13px;padding:8px 18px;">Add to archive</button>
    <button class="btn btn-ghost" onclick="dismissOne(this)" style="font-size:13px;padding:8px 18px;">Dismiss</button>
  </div>
</div>"""

    group_blocks = ""
    for source, cards in groups.items():
        cards_html = "".join(_card(c) for c in cards)
        s = _esc(source)
        keeps = sum(1 for c in cards if (suggestions.get(c["url"]) or {}).get("keep") is True)
        skips = sum(1 for c in cards if (suggestions.get(c["url"]) or {}).get("keep") is False)
        suggest_bar = ""
        if keeps or skips:
            kb = (f'<button class="btn btn-ghost" onclick="approveKeeps(this)" style="font-size:12px;padding:6px 14px;color:var(--seafoam-deep);">Approve {keeps} likely keep{"s" if keeps != 1 else ""}</button>' if keeps else "")
            sb = (f'<button class="btn btn-ghost" onclick="dismissSkips(this)" style="font-size:12px;padding:6px 14px;color:var(--coral-deep);">Dismiss {skips} likely skip{"s" if skips != 1 else ""}</button>' if skips else "")
            suggest_bar = (f'<div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:0 0 12px;font-size:13px;color:var(--muted);">'
                           f'<span>Predicted from your past picks:</span>{kb}{sb}</div>')
        group_blocks += f"""<details data-group class="q-group" style="margin-bottom:12px;border:1px solid var(--line);border-radius:12px;overflow:hidden;">
  <summary style="list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;">
    <span style="display:flex;align-items:center;gap:10px;min-width:0;">
      <span class="q-chevron" style="color:var(--navy);font-size:12px;line-height:1;transition:transform .15s;flex-shrink:0;">&#9654;</span>
      <h2 style="margin:0;font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{s} <span class="grp-count" style="color:var(--muted);font-weight:500;font-size:14px;">({len(cards)})</span></h2>
    </span>
    <span style="display:flex;gap:9px;flex-shrink:0;">
      <form method="post" action="/admin/queue/suggest" style="margin:0;" onsubmit="event.stopPropagation();"><input type="hidden" name="source" value="{s}"><button type="submit" onclick="event.stopPropagation();" class="btn btn-ghost" style="font-size:12px;padding:6px 14px;">Suggest</button></form>
      <button class="btn btn-ghost" onclick="event.stopPropagation();addAll(this)" style="font-size:12px;padding:6px 14px;">Add all</button>
      <button class="btn btn-ghost" onclick="event.stopPropagation();dismissAll(this)" style="font-size:12px;padding:6px 14px;">Dismiss all</button>
    </span>
  </summary>
  <div style="padding:2px 18px 8px;">
    {suggest_bar}
    {cards_html}
  </div>
</details>"""

    pending_n = len(pending)
    if pending_n == 0:
        group_blocks = ('<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;'
                        'padding:32px;text-align:center;color:var(--muted);">Nothing waiting. Scan the feed to '
                        'find recent articles you haven&rsquo;t saved yet.</div>')

    scan_notice = ""
    if scanning:
        scan_notice = ('<div style="background:var(--seafoam-wash);border:1px solid var(--seafoam);border-radius:10px;'
                       'padding:12px 16px;margin-bottom:20px;font-size:14px;color:var(--seafoam-deep);">'
                       'Scanning the feed in the background &mdash; reload this page in a minute to see new candidates.</div>')
    elif redating:
        scan_notice = ('<div style="background:var(--seafoam-wash);border:1px solid var(--seafoam);border-radius:10px;'
                       'padding:12px 16px;margin-bottom:20px;font-size:14px;color:var(--seafoam-deep);">'
                       'Re-reading publish dates from the article pages in the background &mdash; reload in a minute to see corrected dates.</div>')
    elif suggesting:
        scan_notice = ('<div style="background:var(--seafoam-wash);border:1px solid var(--seafoam);border-radius:10px;'
                       'padding:12px 16px;margin-bottom:20px;font-size:14px;color:var(--seafoam-deep);">'
                       'Predicting which candidates you&rsquo;d keep, from your past picks &mdash; reload in a minute to see &ldquo;Likely keep / skip&rdquo; on each card.</div>')
    elif suggest_status.get("note"):
        # Show the result of the last prediction run so it's never a silent no-op.
        ok = suggest_status.get("n", 0) > 0
        bg = "var(--seafoam-wash)" if ok else "var(--coral-wash)"
        bd = "var(--seafoam)" if ok else "var(--coral)"
        col = "var(--seafoam-deep)" if ok else "var(--coral-deep)"
        scan_notice = (f'<div style="background:{bg};border:1px solid {bd};border-radius:10px;'
                       f'padding:12px 16px;margin-bottom:20px;font-size:14px;color:{col};">'
                       f'{_esc(suggest_status["note"])}</div>')

    dismissed_note = (f'<span style="color:var(--muted);font-size:13px;">{dismissed_n} dismissed</span>'
                      if dismissed_n else "")

    expand_controls = (
        '<span style="font-size:13px;color:var(--muted);">'
        '<a href="#" onclick="setAllGroups(true);return false;" style="color:var(--navy);">Expand all</a>'
        ' &middot; <a href="#" onclick="setAllGroups(false);return false;" style="color:var(--navy);">Collapse all</a>'
        '</span>' if pending_n else ''
    )

    body = f"""<div class="page" style="max-width:820px;">
<style>
.q-group summary::-webkit-details-marker{{display:none;}}
.q-group[open] .q-chevron{{transform:rotate(90deg);}}
.q-group summary:hover{{background:var(--surface);}}
</style>
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Archive Queue</h1>
<p style="color:var(--muted);margin:4px 0 22px;">Proposed saves waiting for your review. Approve them into the archive&nbsp;&mdash;&nbsp;edit the tags first if you like&nbsp;&mdash;&nbsp;or dismiss what you don&rsquo;t want.</p>
{scan_notice}
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;">
  <div><span id="pending-count" style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--ink);">{pending_n}</span> <span style="color:var(--muted);">pending</span> &nbsp; {dismissed_note} &nbsp; {expand_controls}</div>
  <form method="post" action="/admin/queue/refresh-feed" style="margin:0;"><button type="submit" class="btn" style="font-size:14px;padding:9px 20px;">Scan feed</button></form>
</div>
<form method="post" action="/admin/queue/redate" style="margin:0 0 24px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
  <span style="font-size:13px;color:var(--muted);">Dates look wrong? Re-read them from the article pages</span>
  <input type="text" name="source" placeholder="source (blank = all)" style="padding:6px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:13px;background:var(--bg);width:180px;">
  <button type="submit" class="btn btn-ghost" style="font-size:13px;padding:6px 14px;">Fix dates</button>
</form>
{group_blocks}
</div>

<script>
function cardOf(btn){{ return btn.closest('[data-card]'); }}
async function postForm(path, data){{
  try {{
    const r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}}, body:new URLSearchParams(data)}});
    return r.ok;
  }} catch(e) {{ return false; }}
}}
function setPending(delta){{
  const el = document.getElementById('pending-count');
  el.textContent = Math.max(0, parseInt(el.textContent || '0', 10) + delta);
}}
function removeCard(card){{
  const grp = card.closest('[data-group]');
  card.remove();
  setPending(-1);
  if (grp) {{
    const left = grp.querySelectorAll('[data-card]').length;
    const cnt = grp.querySelector('.grp-count');
    if (cnt) cnt.textContent = '(' + left + ')';
    if (left === 0) grp.remove();
  }}
}}
async function addOne(btn){{
  const card = cardOf(btn);
  const addBtn = card.querySelector('.add-btn');
  addBtn.disabled = true; addBtn.textContent = 'Adding…';
  const ok = await postForm('/admin/queue/add', {{url: card.dataset.url, tags: card.querySelector('.qtags').value}});
  if (ok) {{ removeCard(card); }}
  else {{ addBtn.disabled = false; addBtn.textContent = 'Add to archive'; }}
  return ok;
}}
async function dismissOne(btn){{
  const card = cardOf(btn);
  if (await postForm('/admin/queue/dismiss', {{url: card.dataset.url}})) removeCard(card);
}}
function setAllGroups(open){{
  document.querySelectorAll('.q-group').forEach(function(g){{ g.open = open; }});
}}
function editQTags(btn){{
  // Reveal the (otherwise hidden) tag input — tags are auto-set; editing is opt-in.
  const card = btn.closest('[data-card]');
  const input = card.querySelector('.qtags');
  btn.parentElement.style.display = 'none';
  input.style.display = 'block';
  input.focus();
}}
async function addAll(btn){{
  const grp = btn.closest('[data-group]');
  const cards = Array.from(grp.querySelectorAll('[data-card]'));
  for (const c of cards) {{ await addOne(c.querySelector('.add-btn')); }}
}}
async function approveKeeps(btn){{
  const grp = btn.closest('[data-group]');
  const cards = Array.from(grp.querySelectorAll('[data-card][data-suggest="keep"]'));
  for (const c of cards) {{ await addOne(c.querySelector('.add-btn')); }}
}}
async function dismissSkips(btn){{
  const grp = btn.closest('[data-group]');
  const cards = Array.from(grp.querySelectorAll('[data-card][data-suggest="skip"]'));
  for (const c of cards) {{ await dismissOne(c.querySelector('.add-btn')); }}
}}
async function dismissAll(btn){{
  const grp = btn.closest('[data-group]');
  const cards = Array.from(grp.querySelectorAll('[data-card]'));
  for (const c of cards) {{ await dismissOne(c.querySelector('.add-btn')); }}
}}
</script>"""
    return HTMLResponse(_page("Archive Queue — Admin", "Admin", body, authed=True))


@app.post("/admin/queue/refresh-feed")
def admin_queue_refresh(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    background_tasks.add_task(_scan_feed_background)
    return RedirectResponse("/admin/queue?scanning=1", status_code=303)


@app.post("/admin/queue/redate")
async def admin_queue_redate(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    source = (form.get("source") or "").strip()
    background_tasks.add_task(_redate_background, source)
    return RedirectResponse("/admin/queue?redating=1", status_code=303)


@app.post("/admin/queue/suggest")
async def admin_queue_suggest(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    source = (form.get("source") or "").strip()
    background_tasks.add_task(_suggest_background, source)
    return RedirectResponse("/admin/queue?suggesting=1", status_code=303)


def _tag_merge_background() -> None:
    """Ask Claude to propose tag-merge groups; store them. Runs off-request."""
    lib = _lib()
    try:
        import json as _json
        from linklib import tagstyle
        groups = tagstyle.suggest_tag_merges(lib)
        lib.set_setting("tag_merge_suggestions", _json.dumps(groups if groups else []))
        lib.set_setting("tag_merge_status", "" if groups is not None else "unavailable")
    except Exception:
        pass
    finally:
        lib.close()


@app.get("/admin/tags", response_class=HTMLResponse)
def admin_tags(request: Request, msg: str = "", merging: int = 0):
    if not _is_authed(request):
        return _login_redirect(request)
    import json as _json
    lib = _lib()
    try:
        tags = lib.all_tags()   # [(tag, count)] desc by count
        proposals = _json.loads(lib.get_setting("tag_merge_suggestions") or "[]")
        merge_status = lib.get_setting("tag_merge_status")
    finally:
        lib.close()

    banner = (f'<p style="background:#d1fae5;color:#065f46;border-radius:10px;padding:10px 16px;'
              f'font-size:14px;margin:-6px 0 16px;">{_esc(msg)}</p>' if msg else '')

    # Proposed merges (from "Suggest merges").
    live_tags = {t for t, _ in tags}
    merge_html = ""
    if merging:
        merge_html = ('<div style="background:var(--seafoam-wash);border:1px solid var(--seafoam);border-radius:10px;'
                      'padding:12px 16px;margin-bottom:16px;font-size:14px;color:var(--seafoam-deep);">'
                      'Looking for tags to consolidate &mdash; reload in a few seconds to see proposed merges.</div>')
    elif merge_status == "unavailable":
        merge_html = ('<div style="background:var(--coral-wash);border:1px solid var(--coral);border-radius:10px;'
                      'padding:12px 16px;margin-bottom:16px;font-size:14px;color:var(--coral-deep);">'
                      'Couldn&rsquo;t generate merge suggestions (AI unavailable). You can still rename/merge by hand below.</div>')
    else:
        # only show groups whose tags still exist
        groups = [g for g in proposals
                  if all(m in live_tags for m in g.get("merge", [])) and g.get("merge")]
        if groups:
            cards = ""
            for gi, g in enumerate(groups):
                canon = _esc(g["canonical"])
                chips = "".join(f'<span style="font-size:12px;background:var(--surface-2);color:var(--ink-soft);border-radius:6px;padding:2px 8px;">{_esc(m)}</span>' for m in g["merge"])
                reason = _esc(g.get("reason", ""))
                merges_val = _esc("\n".join(g["merge"]))
                cards += f"""<div style="border-top:1px solid var(--line);padding:10px 0;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
  <div style="font-size:13.5px;color:var(--ink-soft);min-width:0;">
    <span style="display:inline-flex;gap:6px;flex-wrap:wrap;align-items:center;">{chips}</span>
    <span style="color:var(--muted);"> &rarr; </span><strong>{canon}</strong>
    {f'<span style="color:var(--muted);font-size:12px;"> &middot; {reason}</span>' if reason else ''}
  </div>
  <form method="post" action="/admin/tags/merge-group" style="margin:0;">
    <input type="hidden" name="canonical" value="{canon}"><textarea name="merge" style="display:none;">{merges_val}</textarea>
    <button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 14px;">Merge</button>
  </form>
</div>"""
            merge_html = f"""<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 18px;margin-bottom:18px;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
    <strong style="font-family:var(--font-head);font-size:15px;color:var(--navy);">Proposed merges ({len(groups)})</strong>
    <form method="post" action="/admin/tags/merge-all" style="margin:0;" onsubmit="return confirm('Apply all {len(groups)} proposed merges?');"><button type="submit" class="btn" style="font-size:13px;padding:6px 16px;">Apply all</button></form>
  </div>
  {cards}
</div>"""

    rows = ""
    for tag, count in tags:
        t = _esc(tag)
        rows += f"""<tr style="border-top:1px solid var(--line);">
  <td style="padding:9px 12px;font-size:14px;font-weight:500;">{t}</td>
  <td style="padding:9px 12px;font-size:13px;color:var(--muted);">{count}</td>
  <td style="padding:9px 12px;">
    <form method="post" action="/admin/tags/rename" style="display:flex;gap:6px;align-items:center;margin:0;">
      <input type="hidden" name="old" value="{t}">
      <input type="text" name="new" value="{t}" style="padding:5px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;font-size:13px;background:var(--bg);width:180px;">
      <button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;">Rename</button>
    </form>
  </td>
  <td style="padding:9px 12px;">
    <form method="post" action="/admin/tags/delete" style="margin:0;" onsubmit="return confirm('Remove the tag &quot;{t}&quot; from every article?');">
      <input type="hidden" name="tag" value="{t}">
      <button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;color:#b91c1c;border-color:#fca5a5;">Delete</button>
    </form>
  </td>
</tr>"""
    if not tags:
        rows = '<tr><td colspan="4" style="padding:24px;text-align:center;color:var(--muted);">No tags yet.</td></tr>'

    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Tag cleanup</h1>
<p style="color:var(--muted);margin:-6px 0 18px;">Tags are generated automatically during enrichment. Use this to tidy the vocabulary &mdash; <strong>renaming a tag to one that already exists merges them</strong>, and deleting removes it from every article. Search and the tag facets update immediately.</p>
{banner}
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px;flex-wrap:wrap;">
  <p style="font-size:13px;color:var(--muted);margin:0;">{len(tags)} tags across the archive</p>
  <form method="post" action="/admin/tags/suggest-merges" style="margin:0;"><button type="submit" class="btn" style="font-size:13px;padding:7px 16px;">Suggest merges</button></form>
</div>
{merge_html}
<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;">
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr style="background:var(--bg);">
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Tag</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Articles</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Rename / merge</th>
      <th style="padding:9px 12px;"></th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</div>"""
    return HTMLResponse(_page("Tag cleanup — Admin", "Admin", body, authed=True))


@app.post("/admin/tags/suggest-merges")
def admin_tags_suggest_merges(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    background_tasks.add_task(_tag_merge_background)
    return RedirectResponse("/admin/tags?merging=1", status_code=303)


@app.post("/admin/tags/merge-group")
async def admin_tags_merge_group(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    canonical = (form.get("canonical") or "").strip()
    merges = [m.strip() for m in (form.get("merge") or "").splitlines() if m.strip()]
    total = 0
    lib = _lib()
    try:
        for m in merges:
            total += lib.rename_tag(m, canonical)
    finally:
        lib.close()
    background_tasks.add_task(backup.maybe_backup, DB_PATH)
    msg = f'Merged {len(merges)} tag{"s" if len(merges) != 1 else ""} into “{canonical}” ({total} article updates).'
    return RedirectResponse(f"/admin/tags?msg={quote(msg)}", status_code=303)


@app.post("/admin/tags/merge-all")
def admin_tags_merge_all(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    import json as _json
    lib = _lib()
    try:
        groups = _json.loads(lib.get_setting("tag_merge_suggestions") or "[]")
        live = {t for t, _ in lib.all_tags()}
        applied = 0
        for g in groups:
            canon = (g.get("canonical") or "").strip()
            for m in g.get("merge", []):
                if m in live and m != canon:
                    lib.rename_tag(m, canon)
                    applied += 1
        lib.set_setting("tag_merge_suggestions", "[]")   # consumed
    finally:
        lib.close()
    background_tasks.add_task(backup.maybe_backup, DB_PATH)
    return RedirectResponse(f"/admin/tags?msg={quote(f'Applied all proposed merges ({applied} tags folded in).')}",
                            status_code=303)


@app.post("/admin/tags/rename")
async def admin_tags_rename(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    old = (form.get("old") or "").strip()
    new = (form.get("new") or "").strip()
    lib = _lib()
    try:
        n = lib.rename_tag(old, new)
    finally:
        lib.close()
    background_tasks.add_task(backup.maybe_backup, DB_PATH)
    msg = f'Renamed “{old}” → “{new}” on {n} article{"s" if n != 1 else ""}.' if n else f'No change — “{old}” not found.'
    return RedirectResponse(f"/admin/tags?msg={quote(msg)}", status_code=303)


@app.post("/admin/tags/delete")
async def admin_tags_delete(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    tag = (form.get("tag") or "").strip()
    lib = _lib()
    try:
        n = lib.delete_tag(tag)
    finally:
        lib.close()
    background_tasks.add_task(backup.maybe_backup, DB_PATH)
    msg = f'Removed “{tag}” from {n} article{"s" if n != 1 else ""}.'
    return RedirectResponse(f"/admin/tags?msg={quote(msg)}", status_code=303)


def _tag_guide_background() -> None:
    """Learn the tagging guide from the archive and save it. Off-request (Claude call)."""
    lib = _lib()
    try:
        from linklib import tagstyle
        guide = tagstyle.generate_tag_guide(lib)
        if guide:
            lib.set_setting("tag_guide", guide)
        lib.set_setting("tag_guide_status", "")
        backup.maybe_backup(DB_PATH)
    except Exception:
        try:
            lib.set_setting("tag_guide_status", "")
        except Exception:
            pass
    finally:
        lib.close()


@app.get("/admin/tag-style", response_class=HTMLResponse)
def admin_tag_style(request: Request, generating: int = 0):
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib import tagstyle
    lib = _lib()
    try:
        guide = lib.get_setting("tag_guide")
        status = lib.get_setting("tag_guide_status")
        objective = tagstyle.get_tag_objective(lib)
        n_tags = len(lib.all_tags())
    finally:
        lib.close()

    is_generating = bool(generating) or status == "generating"
    notice = ('<div style="background:var(--seafoam-wash);border:1px solid var(--seafoam);border-radius:10px;'
              'padding:12px 16px;margin-bottom:18px;font-size:14px;color:var(--seafoam-deep);">'
              'Studying your archive in the background &mdash; reload in about a minute to see the guide.</div>'
              if is_generating else '')

    has_guide = bool(guide and guide.strip())
    state_badge = ('<span style="font-size:12px;font-weight:600;background:#d1fae5;color:#065f46;border-radius:6px;padding:2px 8px;margin-left:10px;vertical-align:middle;">Active</span>'
                   if has_guide else
                   '<span style="font-size:12px;color:var(--muted);margin-left:10px;vertical-align:middle;">Not set &mdash; auto-tagging uses your vocabulary only</span>')

    gen_label = "Re-learn from my archive" if has_guide else "Learn from my archive"

    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Tagging style{state_badge}</h1>
<p style="color:var(--muted);margin:-6px 0 18px;">Auto-tagging already reuses your vocabulary. This goes further: it studies <strong>how</strong> you tagged your {n_tags} tags &mdash; what each one means, how granular you go, what you leave untagged &mdash; and distills soft rules that get injected into enrichment so new tags match your judgment. Review and edit anything below; your edits are what the tagger follows.</p>
{notice}

<form method="post" action="/admin/tag-style/objective" style="margin:0 0 22px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;">
  <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Tagging objective &mdash; why these tags exist</label>
  <p style="font-size:13px;color:var(--muted);margin:0 0 10px;">The north star for tagging. It steers both the learning below and live auto-tagging, even before a guide exists. Frame it around the jobs a strategic finance leader gets pulled into.</p>
  <textarea name="objective" rows="5" style="width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:14px;line-height:1.6;background:var(--bg);resize:vertical;">{_esc(objective)}</textarea>
  <button type="submit" class="btn" style="font-size:14px;padding:8px 18px;margin-top:10px;">Save objective</button>
</form>

<form method="post" action="/admin/tag-style/generate" style="margin:0 0 18px;">
  <button type="submit" class="btn" style="font-size:14px;padding:9px 20px;" {"disabled style='opacity:.5;'" if is_generating else ""}>{gen_label}</button>
  <span style="font-size:13px;color:var(--muted);margin-left:12px;">Reads your tags + example articles and writes the guide. Runs in the background.</span>
</form>

<form method="post" action="/admin/tag-style/save" style="margin:0;">
  <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Tagging guide</label>
  <textarea name="guide" rows="20" placeholder="Click “{gen_label}” to draft this from your archive, or write your own rules here."
    style="width:100%;padding:14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:14px;line-height:1.6;background:var(--bg);resize:vertical;">{_esc(guide)}</textarea>
  <div style="display:flex;gap:10px;margin-top:12px;">
    <button type="submit" class="btn" style="font-size:14px;padding:9px 20px;">Save guide</button>
    <button type="submit" formaction="/admin/tag-style/clear" class="btn btn-ghost" style="font-size:14px;padding:9px 20px;color:#b91c1c;border-color:#fca5a5;"
      onclick="return confirm('Clear the tagging guide? Auto-tagging will fall back to vocabulary only.');">Clear</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Tagging style — Admin", "Admin", body, authed=True))


@app.post("/admin/tag-style/generate")
def admin_tag_style_generate(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        lib.set_setting("tag_guide_status", "generating")
    finally:
        lib.close()
    background_tasks.add_task(_tag_guide_background)
    return RedirectResponse("/admin/tag-style?generating=1", status_code=303)


@app.post("/admin/tag-style/save")
async def admin_tag_style_save(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    guide = (form.get("guide") or "").strip()
    lib = _lib()
    try:
        lib.set_setting("tag_guide", guide)
    finally:
        lib.close()
    return RedirectResponse("/admin/tag-style", status_code=303)


@app.post("/admin/tag-style/objective")
async def admin_tag_style_objective(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    objective = (form.get("objective") or "").strip()
    lib = _lib()
    try:
        lib.set_setting("tag_objective", objective)   # blank falls back to the default
    finally:
        lib.close()
    return RedirectResponse("/admin/tag-style", status_code=303)


@app.post("/admin/tag-style/clear")
async def admin_tag_style_clear(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        lib.set_setting("tag_guide", "")
    finally:
        lib.close()
    return RedirectResponse("/admin/tag-style", status_code=303)


# ---------------------------------------------------------------------------
# Near-duplicate cleanup — catch reworded reruns (e.g. SaaStr) the exact-URL
# dedup misses, within a publish-date window.
# ---------------------------------------------------------------------------

_DEDUPE_PRESETS = {"aggressive": 0.55, "balanced": 0.62, "conservative": 0.72}


@app.get("/admin/dedupe", response_class=HTMLResponse)
def admin_dedupe(request: Request, source: str = "", level: str = "balanced",
                 days: int = 90, msg: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib import dedupe as dd
    threshold = _DEDUPE_PRESETS.get(level, 0.62)
    lib = _lib()
    try:
        sources = lib.article_sources()
        dup_n, distinct_n = lib.dedupe_decision_counts()
        clusters = []
        verify_status = "verified"
        if source:
            arts = lib.articles_by_source(source)
            clusters = dd.find_clusters(arts, days=days, threshold=threshold, source=source)
            clusters, verify_status = dd.verify_clusters(
                clusters, source=source,
                distinct_pairs=lib.distinct_pairs(),
                decisions=lib.dedupe_decisions(limit=40))
    finally:
        lib.close()

    verified = verify_status == "verified"
    if verify_status == "no_key":
        verify_note = ("&#9888;&#65039; <strong>Claude verification is off</strong> — no <code>ANTHROPIC_API_KEY</code> "
                       "is set on the host, so these are raw title matches and look-alikes (different role, "
                       "milestone, or question) may appear. Set the key in Railway to turn on semantic verification. "
                       "Your &ldquo;Not a dupe&rdquo; calls still stick.")
    elif verify_status == "no_sdk":
        verify_note = "&#9888;&#65039; <strong>Claude verification unavailable</strong> (anthropic SDK not installed). Showing raw title matches."
    elif verify_status.startswith("error"):
        verify_note = (f"&#9888;&#65039; <strong>Claude verification failed</strong>, showing raw title matches. "
                       f"<span style=\"color:var(--muted);\">{_esc(verify_status)}</span>")
    else:
        verify_note = ""

    banner = (f'<p style="background:#d1fae5;color:#065f46;border-radius:10px;padding:10px 16px;'
              f'font-size:14px;margin:-6px 0 16px;">{_esc(msg)}</p>' if msg else '')

    # Source picker (default suggestion: SaaStr).
    opts = ""
    for s, n in sources:
        sel = " selected" if s == source else ""
        opts += f'<option value="{_esc(s)}"{sel}>{_esc(s)} ({n})</option>'
    level_opts = "".join(
        f'<option value="{k}"{" selected" if k == level else ""}>{k.capitalize()} ({v})</option>'
        for k, v in _DEDUPE_PRESETS.items())
    days_opts = "".join(
        f'<option value="{d}"{" selected" if d == days else ""}>±{d} days</option>'
        for d in (30, 90, 180, 365))

    controls = f"""<form method="get" action="/admin/dedupe" style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:18px;">
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Source</label>
    <select name="source" style="padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);min-width:180px;"><option value="">Choose a source…</option>{opts}</select></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Strictness</label>
    <select name="level" style="padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);">{level_opts}</select></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Window</label>
    <select name="days" style="padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);">{days_opts}</select></div>
  <button type="submit" class="btn" style="font-size:14px;padding:9px 20px;">Scan</button>
</form>"""

    body_inner = controls
    if dup_n or distinct_n:
        body_inner += (f'<p style="font-size:13px;color:var(--muted);margin:-8px 0 16px;">'
                       f'&#10024; Learning from your calls: <strong>{distinct_n}</strong> marked &ldquo;not a dupe&rdquo;, '
                       f'<strong>{dup_n}</strong> confirmed. Pairs you reject won&rsquo;t be shown again, and Claude '
                       f'uses your past calls to judge new ones.</p>')
    if source:
        if not clusters:
            body_inner += ('<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;'
                           'padding:32px;text-align:center;color:var(--muted);">No near-duplicates found for '
                           f'<strong>{_esc(source)}</strong> at this strictness/window. Try a more aggressive setting if you suspect some.</div>')
        else:
            dupe_total = sum(len(c) - 1 for c in clusters)
            blocks = ""
            for c in clusters:
                keeper = c[0]
                keep_title = _esc((keeper.get("title") or keeper["url"])[:70])
                rows = ""
                for i, a in enumerate(c):
                    keep = i == 0
                    d = _esc((a.get("published_at") or "")[:10])
                    if keep:
                        tag = '<span style="font-size:11px;font-weight:600;color:var(--seafoam-deep);white-space:nowrap;">KEEP</span>'
                        match = ""
                    else:
                        # Shared context so each decision is recorded against the pair.
                        ctx = (f'<input type="hidden" name="keeper_url" value="{_esc(keeper["url"])}">'
                               f'<input type="hidden" name="keeper_title" value="{_esc(keeper.get("title") or "")}">'
                               f'<input type="hidden" name="dup_url" value="{_esc(a["url"])}">'
                               f'<input type="hidden" name="dup_title" value="{_esc(a.get("title") or "")}">'
                               f'<input type="hidden" name="source" value="{_esc(source)}">'
                               f'<input type="hidden" name="back" value="{_esc(source)}|{level}|{days}">')
                        accept = (f'<form method="post" action="/admin/dedupe/remove" style="margin:0;" onsubmit="return confirm(\'Delete this duplicate?\');">'
                                  f'<input type="hidden" name="id" value="{a["id"]}">{ctx}'
                                  f'<button type="submit" class="btn btn-ghost" style="font-size:12px;padding:4px 12px;color:#b91c1c;border-color:#fca5a5;">Remove</button></form>')
                        reject = (f'<form method="post" action="/admin/dedupe/not-dupe" style="margin:0;">{ctx}'
                                  f'<button type="submit" class="btn btn-ghost" style="font-size:12px;padding:4px 12px;color:var(--ink-soft);">Not a dupe</button></form>')
                        tag = f'<div style="display:flex;gap:6px;flex-shrink:0;">{reject}{accept}</div>'
                        pct = round(a.get("_dup_score", 0) * 100)
                        match = (f'<div style="font-size:12px;color:var(--coral-deep);margin-top:2px;">'
                                 f'&#8627; duplicate of &ldquo;{keep_title}&rdquo; &middot; {pct}% match</div>')
                    rows += (f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid var(--line);">'
                             f'<div style="min-width:0;"><a href="{_esc(a["url"])}" target="_blank" rel="noopener" style="font-size:14px;color:var(--navy);font-weight:500;">{_esc(a.get("title") or a["url"])}</a>'
                             f'<div style="font-size:12px;color:var(--muted);">{d}</div>{match}</div>{tag}</div>')
                cluster_label = ("near-duplicates &mdash; verified by Claude" if verified
                                 else "title matches &mdash; <span style=\"color:var(--coral-deep);\">not verified</span>")
                blocks += (f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:6px 18px 14px;margin-bottom:14px;">'
                           f'<div style="font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:600;padding:10px 0 2px;">{len(c)} {cluster_label}</div>{rows}</div>')
            verify_banner = (f'<p style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:10px;'
                             f'padding:10px 14px;font-size:13px;margin:0 0 16px;line-height:1.5;">{verify_note}</p>'
                             if verify_note else '')
            bulk = f"""<form method="post" action="/admin/dedupe/remove-older" style="margin:0 0 18px;" onsubmit="return confirm('Delete {dupe_total} duplicate(s), keeping one per group? A backup is taken first.');">
  <input type="hidden" name="source" value="{_esc(source)}"><input type="hidden" name="level" value="{level}"><input type="hidden" name="days" value="{days}">
  <button type="submit" class="btn" style="font-size:14px;padding:9px 20px;">Remove all {dupe_total} duplicate{'s' if dupe_total != 1 else ''} (keep one each)</button>
  <span style="font-size:13px;color:var(--muted);margin-left:10px;">{len(clusters)} duplicate group{'s' if len(clusters) != 1 else ''} found.</span>
</form>"""
            body_inner += verify_banner + bulk + blocks

    body = f"""<div class="page" style="max-width:760px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Find duplicates</h1>
<p style="color:var(--muted);margin:-6px 0 18px;">Catches the same piece republished under a different title within a date window &mdash; the kind exact-URL dedup misses. A fast title match finds candidates, then Claude verifies each against the summaries so look-alikes (different role, milestone, or question) aren&rsquo;t flagged. The keeper is the original over a &ldquo;Dear SaaStr&rdquo; rehash, otherwise the newest.</p>
{banner}
{body_inner}
</div>"""
    return HTMLResponse(_page("Find duplicates — Admin", "Admin", body, authed=True))


def _dedupe_pair(form) -> tuple[dict, dict, str]:
    """Reconstruct the (keeper, duplicate, source) a dedupe decision is about."""
    keeper = {"url": (form.get("keeper_url") or "").strip(), "title": form.get("keeper_title") or ""}
    dup = {"url": (form.get("dup_url") or "").strip(), "title": form.get("dup_title") or ""}
    return keeper, dup, (form.get("source") or "").strip()


def _dedupe_back(form) -> str:
    back = (form.get("back") or "").split("|")
    src = quote(back[0]) if back and back[0] else ""
    lvl = back[1] if len(back) > 1 else "balanced"
    dys = back[2] if len(back) > 2 else "90"
    return f"/admin/dedupe?source={src}&level={lvl}&days={dys}"


@app.post("/admin/dedupe/remove")
async def admin_dedupe_remove(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    try:
        aid = int(form.get("id") or 0)
    except ValueError:
        aid = 0
    keeper, dup, source = _dedupe_pair(form)
    lib = _lib()
    try:
        if keeper["url"] and dup["url"]:
            lib.record_dedupe_decision(keeper, dup, "dup", source)   # accept = it's a dupe
        if aid:
            lib.delete_article(aid)
    finally:
        lib.close()
    background_tasks.add_task(backup.maybe_backup, DB_PATH)
    return RedirectResponse(_dedupe_back(form), status_code=303)


@app.post("/admin/dedupe/not-dupe")
async def admin_dedupe_not_dupe(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    keeper, dup, source = _dedupe_pair(form)
    lib = _lib()
    try:
        if keeper["url"] and dup["url"]:
            lib.record_dedupe_decision(keeper, dup, "distinct", source)  # reject = keep both
    finally:
        lib.close()
    return RedirectResponse(_dedupe_back(form), status_code=303)


@app.post("/admin/dedupe/remove-older")
async def admin_dedupe_remove_older(request: Request, background_tasks: BackgroundTasks):
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib import dedupe as dd
    form = await request.form()
    source = (form.get("source") or "").strip()
    level = (form.get("level") or "balanced").strip()
    try:
        days = int(form.get("days") or 90)
    except ValueError:
        days = 90
    threshold = _DEDUPE_PRESETS.get(level, 0.62)
    removed = 0
    lib = _lib()
    try:
        backup.maybe_backup(DB_PATH)   # snapshot before a bulk delete
        clusters = dd.find_clusters(lib.articles_by_source(source), days=days,
                                    threshold=threshold, source=source)
        clusters, _ = dd.verify_clusters(clusters, source=source,
                                         distinct_pairs=lib.distinct_pairs(),
                                         decisions=lib.dedupe_decisions(limit=40))
        for c in clusters:
            keeper = c[0]
            for a in c[1:]:            # keep the first (the keeper), remove the rest
                lib.record_dedupe_decision(keeper, a, "dup", source)
                lib.delete_article(a["id"])
                removed += 1
    finally:
        lib.close()
    msg = f"Removed {removed} older duplicate{'s' if removed != 1 else ''} from {source}."
    return RedirectResponse(f"/admin/dedupe?source={quote(source)}&level={level}&days={days}&msg={quote(msg)}",
                            status_code=303)


# ---------------------------------------------------------------------------
# User accounts (admin-provisioned). The gated member tier is layered on these.
# ---------------------------------------------------------------------------

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, msg: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        users = lib.list_users()
    finally:
        lib.close()

    banner = (f'<p style="background:#d1fae5;color:#065f46;border-radius:10px;padding:10px 16px;'
              f'font-size:14px;margin:-6px 0 16px;">{_esc(msg)}</p>' if msg else '')

    rows = ""
    for u in users:
        uid = u["id"]
        active = u["active"]
        status = ('<span style="font-size:12px;font-weight:600;color:#065f46;">active</span>' if active
                  else '<span style="font-size:12px;font-weight:600;color:#b91c1c;">disabled</span>')
        role_badge = (f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;'
                      f'background:{"var(--coral-wash);color:var(--coral-deep)" if u["role"]=="admin" else "var(--seafoam-wash);color:var(--seafoam-deep)"};">{_esc(u["role"])}</span>')
        last = _esc((u["last_login_at"] or "")[:10]) or "—"
        rows += f"""<tr style="border-top:1px solid var(--line);">
  <td style="padding:9px 12px;font-size:14px;font-weight:500;">{_esc(u["username"])}<div style="font-size:12px;color:var(--muted);font-weight:400;">{_esc(u["email"] or "")}</div></td>
  <td style="padding:9px 12px;font-size:14px;">{_esc(u["name"] or "") or '<span style="color:var(--muted);">—</span>'}</td>
  <td style="padding:9px 12px;">{role_badge}</td>
  <td style="padding:9px 12px;">{status}</td>
  <td style="padding:9px 12px;font-size:12px;color:var(--muted);">{last}</td>
  <td style="padding:9px 12px;">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      <form method="post" action="/admin/users/{uid}/edit" style="margin:0;display:flex;gap:4px;align-items:center;flex-wrap:wrap;">
        <input name="username" value="{_esc(u["username"])}" required maxlength="64" pattern="[A-Za-z0-9._-]+" title="username" placeholder="username" style="padding:5px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;font-size:12px;background:var(--bg);width:110px;">
        <input name="name" value="{_esc(u["name"] or "")}" maxlength="120" placeholder="name" title="display name" style="padding:5px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;font-size:12px;background:var(--bg);width:120px;">
        <input name="email" type="email" value="{_esc(u["email"] or "")}" maxlength="200" placeholder="email" title="email" style="padding:5px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;font-size:12px;background:var(--bg);width:160px;">
        <button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;">Save</button>
      </form>
      <form method="post" action="/admin/users/{uid}/role" style="margin:0;"><button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;">{"Make member" if u["role"]=="admin" else "Make admin"}</button></form>
      <form method="post" action="/admin/users/{uid}/toggle" style="margin:0;"><button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;">{"Disable" if active else "Enable"}</button></form>
      <form method="post" action="/admin/users/{uid}/password" style="margin:0;display:flex;gap:4px;align-items:center;">
        <input type="password" name="password" required placeholder="new password" minlength="8" style="padding:5px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;font-size:12px;background:var(--bg);width:130px;">
        <button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;">Reset</button>
      </form>
      <form method="post" action="/admin/users/{uid}/delete" style="margin:0;" onsubmit="return confirm('Delete this account?');"><button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 12px;color:#b91c1c;border-color:#fca5a5;">Delete</button></form>
    </div>
  </td>
</tr>"""
    if not users:
        rows = '<tr><td colspan="6" style="padding:24px;text-align:center;color:var(--muted);">No accounts yet. Create one below.</td></tr>'

    body = f"""<div class="page" style="max-width:880px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Users</h1>
<p style="color:var(--muted);margin:-6px 0 18px;">Member accounts for the gated sections. You create accounts here (no public sign-up yet). You always keep admin access via the host password, so you can&rsquo;t lock yourself out.</p>
{banner}
<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:26px;">
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr style="background:var(--bg);">
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">User</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Name</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Role</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Status</th>
      <th style="padding:9px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Last in</th>
      <th style="padding:9px 12px;"></th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>

<h2 style="font-size:18px;">Add a member</h2>
<form method="post" action="/admin/users/create" style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;display:grid;grid-template-columns:1fr 1fr;gap:14px;">
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Username *</label>
    <input name="username" required maxlength="64" pattern="[A-Za-z0-9._-]+" placeholder="jane.doe" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);"></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Temporary password *</label>
    <input name="password" type="text" required minlength="8" placeholder="at least 8 characters" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);"></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Name</label>
    <input name="name" maxlength="120" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);"></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Email</label>
    <input name="email" type="email" maxlength="200" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);"></div>
  <div><label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Role</label>
    <select name="role" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);">
      <option value="user">Member (user)</option>
      <option value="admin">Admin</option>
    </select></div>
  <div style="display:flex;align-items:flex-end;"><button type="submit" class="btn" style="font-size:14px;padding:9px 22px;">Create account</button></div>
</form>
</div>"""
    return HTMLResponse(_page("Users — Admin", "Admin", body, authed=True))


@app.post("/admin/users/create")
async def admin_users_create(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    role = (form.get("role") or "user").strip()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip()
    if not username or len(password) < 8:
        return RedirectResponse(f"/admin/users?msg={quote('Username and an 8+ char password are required.')}", status_code=303)
    lib = _lib()
    try:
        import sqlite3 as _sql
        try:
            lib.create_user(username, password, role=role, name=name, email=email)
            msg = f'Created account “{username.lower()}” ({role}).'
        except _sql.IntegrityError:
            msg = f'Username “{username.lower()}” already exists.'
    finally:
        lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/users/{user_id}/edit")
async def admin_users_edit(request: Request, user_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    username = (form.get("username") or "").strip()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip()
    if not username:
        return RedirectResponse(f"/admin/users?msg={quote('Username is required.')}", status_code=303)
    lib = _lib()
    try:
        import sqlite3 as _sql
        try:
            lib.update_user(user_id, username=username, name=name, email=email)
            msg = f'Updated “{username.lower()}”.'
        except _sql.IntegrityError:
            msg = f'Username “{username.lower()}” is already taken.'
    finally:
        lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


def _is_last_active_admin(users: list[dict], user_id: int) -> bool:
    """True if user_id is the only active admin account — so demoting, disabling,
    or deleting it would leave the site with no admin. Guards against lockout."""
    active_admins = [u for u in users if u["role"] == "admin" and u["active"]]
    return len(active_admins) == 1 and active_admins[0]["id"] == user_id


@app.post("/admin/users/{user_id}/toggle")
def admin_users_toggle(request: Request, user_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    msg = ""
    try:
        users = lib.list_users()
        u = next((x for x in users if x["id"] == user_id), None)
        if u and u["active"] and _is_last_active_admin(users, user_id):
            msg = "Can’t disable the last admin account."
        elif u:
            lib.set_user_active(user_id, not u["active"])
    finally:
        lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/users/{user_id}/role")
def admin_users_role(request: Request, user_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    msg = ""
    try:
        users = lib.list_users()
        u = next((x for x in users if x["id"] == user_id), None)
        if u and u["role"] == "admin" and _is_last_active_admin(users, user_id):
            msg = "Can’t demote the last admin account."
        elif u:
            new_role = "user" if u["role"] == "admin" else "admin"
            lib.set_user_role(user_id, new_role)
            msg = f'“{u["username"]}” is now {"an admin" if new_role == "admin" else "a member"}.'
    finally:
        lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/users/{user_id}/password")
async def admin_users_password(request: Request, user_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    form = await request.form()
    password = form.get("password") or ""
    msg = "Password too short (8+ characters)." if len(password) < 8 else "Password reset."
    if len(password) >= 8:
        lib = _lib()
        try:
            lib.set_user_password(user_id, password)
        finally:
            lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/users/{user_id}/delete")
def admin_users_delete(request: Request, user_id: int):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    msg = ""
    try:
        users = lib.list_users()
        if _is_last_active_admin(users, user_id):
            msg = "Can’t delete the last admin account."
        else:
            lib.delete_user(user_id)
    finally:
        lib.close()
    return RedirectResponse(f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/queue/add")
async def admin_queue_add(request: Request, background_tasks: BackgroundTasks):
    _require_api(request)
    form = await request.form()
    url = (form.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    tags_raw = form.get("tags")
    tags = ([t.strip() for t in tags_raw.split(",") if t.strip()]
            if tags_raw is not None else None)
    lib = _lib()
    try:
        article_id = lib.promote_queue_item(url, tags=tags)
        if not article_id:
            raise HTTPException(status_code=404, detail="not in queue")
        background_tasks.add_task(backup.maybe_backup, DB_PATH)
        return JSONResponse({"ok": True, "id": article_id})
    finally:
        lib.close()


@app.post("/admin/queue/dismiss")
async def admin_queue_dismiss(request: Request):
    _require_api(request)
    form = await request.form()
    url = (form.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    lib = _lib()
    try:
        lib.dismiss_queue_item(url)
        return JSONResponse({"ok": True})
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# Review removals — articles the enricher flagged as off-audience
# ---------------------------------------------------------------------------

@app.get("/admin/review-removals", response_class=HTMLResponse)
def admin_review_removals(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        flagged = lib.list_flagged()
    finally:
        lib.close()

    def _card(a: dict) -> str:
        aid = a["id"]
        url = _esc(a["url"])
        title = _esc(a.get("title") or a["url"])
        source = _esc(a.get("source") or "")
        reason = _esc(a.get("scope_reason") or "flagged off-audience")
        summary = _esc((a.get("summary") or "")[:300])
        return f"""<div data-card data-id="{aid}" style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:12px;">
  <a href="{url}" target="_blank" rel="noopener" style="font-family:var(--font-head);font-weight:600;font-size:16px;color:var(--navy);line-height:1.35;">{title}</a>
  <div style="font-size:12px;color:var(--muted);margin:3px 0 6px;">{source}</div>
  <div style="font-size:12px;color:var(--muted);font-style:italic;margin-bottom:8px;">Flagged: {reason}</div>
  <p style="font-size:14px;color:var(--ink-soft);margin:0 0 12px;line-height:1.55;">{summary}</p>
  <div style="display:flex;gap:9px;">
    <button class="keep-btn btn btn-ghost" onclick="keepOne(this)" style="font-size:13px;padding:8px 18px;">Keep</button>
    <button class="btn btn-ghost" onclick="removeOne(this)" style="font-size:13px;padding:8px 18px;color:var(--alert);border-color:var(--alert);">Remove</button>
  </div>
</div>"""

    n = len(flagged)
    if n == 0:
        cards = ('<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;'
                 'padding:32px;text-align:center;color:var(--muted);">Nothing flagged for removal. '
                 'After a re-enrichment pass, off-audience articles (e.g. how-to-get-into-VC) show up here.</div>')
    else:
        cards = "".join(_card(a) for a in flagged)

    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Review removals</h1>
<p style="color:var(--muted);margin:4px 0 22px;">Articles the enricher flagged as off-audience for this archive &mdash; most often &ldquo;how to get into VC&rdquo; content. Nothing is deleted until you say so. Keep the false positives; remove the rest.</p>
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:22px;">
  <div><span id="flagged-count" style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--ink);">{n}</span> <span style="color:var(--muted);">flagged</span></div>
  <button class="btn btn-ghost" onclick="removeAll()" style="font-size:12px;padding:6px 14px;color:var(--alert);border-color:var(--alert);">Remove all</button>
</div>
{cards}
</div>

<script>
function cardOf(btn){{ return btn.closest('[data-card]'); }}
async function postForm(path, data){{
  try {{
    const r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}}, body:new URLSearchParams(data)}});
    return r.ok;
  }} catch(e) {{ return false; }}
}}
function dropCard(card){{
  card.remove();
  const el = document.getElementById('flagged-count');
  el.textContent = Math.max(0, parseInt(el.textContent || '0', 10) - 1);
}}
async function keepOne(btn){{
  const card = cardOf(btn);
  if (await postForm('/admin/review-removals/keep', {{id: card.dataset.id}})) dropCard(card);
}}
async function removeOne(btn){{
  const card = cardOf(btn);
  if (await postForm('/admin/review-removals/remove', {{id: card.dataset.id}})) dropCard(card);
}}
async function removeAll(){{
  if (!confirm('Remove all flagged articles? This deletes them from the archive.')) return;
  const cards = Array.from(document.querySelectorAll('[data-card]'));
  for (const c of cards) {{ await removeOne(c.querySelector('button:last-child')); }}
}}
</script>"""
    return HTMLResponse(_page("Review removals — Admin", "Admin", body, authed=True))


@app.post("/admin/review-removals/keep")
async def admin_review_keep(request: Request):
    _require_api(request)
    form = await request.form()
    try:
        article_id = int(form.get("id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad id")
    lib = _lib()
    try:
        lib.keep_article(article_id)
        return JSONResponse({"ok": True})
    finally:
        lib.close()


@app.post("/admin/review-removals/remove")
async def admin_review_remove(request: Request, background_tasks: BackgroundTasks):
    _require_api(request)
    form = await request.form()
    try:
        article_id = int(form.get("id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad id")
    lib = _lib()
    try:
        lib.delete_article(article_id)
        background_tasks.add_task(backup.maybe_backup, DB_PATH)
        return JSONResponse({"ok": True})
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# Re-enrich archive — force-refresh Claude summaries + tags server-side
# ---------------------------------------------------------------------------

def _enrich_job(force: bool, model: str, limit: int) -> None:
    """Background thread: run enrich_library, updating _JOB_STATE["enrich"]."""
    _job_set("enrich", running=True, done=0, total=0, error="", model=model)
    lib = _lib()
    try:
        from linklib import pipeline as _pl

        rows = lib.all_articles(limit=limit) if force else lib.unenriched(limit=limit)
        total = len(rows)
        _job_set("enrich", total=total)

        def _progress(done, _total, _title):
            _job_set("enrich", done=done)

        _pl.enrich_library(lib, limit=limit, fetch=False, force=force,
                           model=model, progress=_progress)
        backup.maybe_backup(DB_PATH)
        _job_set("enrich", running=False, done=total)
    except Exception as exc:
        _job_set("enrich", running=False, error=str(exc))
    finally:
        lib.close()


@app.get("/admin/enrich", response_class=HTMLResponse)
def admin_enrich(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        total = lib.count()
        unenriched = len(lib.unenriched(limit=100000))
        cleanup_on = lib.get_setting("scope_cleanup", "on") != "off"
    finally:
        lib.close()

    cleanup_state = ("on" if cleanup_on else "off")
    cleanup_toggle = f"""<div style="background:{'var(--seafoam-wash)' if cleanup_on else 'var(--surface)'};border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
  <div style="font-size:13.5px;color:var(--ink-soft);max-width:520px;line-height:1.5;">
    <strong>First-time cleanup exclusions: {cleanup_state.upper()}.</strong>
    {'Podcasts/webinars, slide decks, annual predictions, and fund/LP content are flagged out of scope. Turn this off once the cleanup is done — ongoing queue review is the gate from then on.' if cleanup_on else 'Only the standard audience rules apply (off-audience + career-in-VC). Turn back on for another cleanup pass.'}
  </div>
  <form method="post" action="/admin/enrich/cleanup-toggle" style="margin:0;"><button type="submit" class="btn btn-ghost" style="font-size:13px;padding:6px 14px;white-space:nowrap;">Turn {'off' if cleanup_on else 'on'}</button></form>
</div>"""

    from linklib.enrich import DEFAULT_MODEL, ENRICH_RULES_VERSION

    job = _job_get("enrich")
    running = job.get("running", False)
    job_done = job.get("done", 0)
    job_total = job.get("total", 0)
    job_error = job.get("error", "")
    job_model = job.get("model", "")

    enriched = total - unenriched
    pct = round(enriched / total * 100) if total else 0

    status_html = ""
    if running:
        prog_pct = round(job_done / job_total * 100) if job_total else 0
        status_html = f"""
<div id="job-status" style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;margin-bottom:20px;">
  <div style="font-weight:600;font-size:14px;color:#1d4ed8;margin-bottom:6px;">Re-enrichment in progress&hellip;</div>
  <div style="font-size:13px;color:var(--muted);">Model: <strong>{_esc(job_model)}</strong> &middot; {job_done} / {job_total} done</div>
  <div style="background:#dbeafe;border-radius:6px;height:8px;margin-top:10px;overflow:hidden;">
    <div style="background:#2563eb;height:8px;width:{prog_pct}%;transition:width .3s;"></div>
  </div>
</div>"""
    elif job_error:
        status_html = f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#b91c1c;">Error: {_esc(job_error)}</div>'
    elif job_done and not running:
        status_html = f'<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#065f46;">Done — {job_done} articles enriched with {_esc(job_model)}.</div>'

    models = [
        ("claude-opus-4-8",           "Opus 4.8",   "Deepest summaries. The one to standardize the archive on."),
        ("claude-sonnet-4-6",         "Sonnet 4.6",  "Solid summaries at a lower cost."),
        ("claude-haiku-4-5-20251001", "Haiku 4.5",   "Fast and cheap. Good for clearing a big unenriched backlog."),
    ]

    def _mrow(mid, label, detail):
        chk = " checked" if mid == DEFAULT_MODEL else ""
        return (
            f'<label style="display:flex;align-items:flex-start;gap:8px;font-size:14px;cursor:pointer;padding:7px 0;border-top:1px solid var(--line);">'
            f'<input type="radio" name="model" value="{mid}"{chk} style="margin-top:3px;accent-color:var(--accent);flex-shrink:0;">'
            f'<span><strong>{label}</strong><span style="display:block;font-size:12px;color:var(--muted);">{detail}</span></span></label>'
        )

    model_radios = "".join(_mrow(m, l, d) for m, l, d in models)
    disable = 'disabled style="opacity:.5;cursor:not-allowed;"' if running else ""

    body = f"""<div class="page" style="max-width:720px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Re-enrich archive</h1>
<p style="color:var(--muted);margin:-6px 0 22px;">Generate Claude summaries and tags across your saved articles, server-side. The summary is what the Ask feature reasons from, so depth here pays off there.</p>

<div id="poll-container">{status_html}</div>

<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:20px;">
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:18px;">
    <div style="text-align:center;padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px;">
      <div style="font-size:26px;font-weight:700;color:var(--navy);font-family:var(--font-head);">{total:,}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px;">Total articles</div>
    </div>
    <div style="text-align:center;padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px;">
      <div style="font-size:26px;font-weight:700;color:#16a34a;font-family:var(--font-head);">{enriched:,}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px;">Enriched ({pct}%)</div>
    </div>
    <div style="text-align:center;padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px;">
      <div style="font-size:26px;font-weight:700;color:#d97706;font-family:var(--font-head);">{unenriched:,}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px;">Need enrichment</div>
    </div>
  </div>
  <p style="font-size:13px;color:var(--muted);margin:0 0 14px;">Current rules version: <strong>{ENRICH_RULES_VERSION}</strong></p>
  {cleanup_toggle}

  <form id="enrich-form" method="post" action="/admin/enrich/start" style="display:grid;gap:18px;">
    <div>
      <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:2px;">Model</div>
      <div style="display:flex;flex-direction:column;">{model_radios}</div>
    </div>
    <div>
      <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">Scope</div>
      <label style="display:flex;align-items:flex-start;gap:8px;font-size:14px;cursor:pointer;">
        <input type="checkbox" name="force" value="1" style="margin-top:3px;accent-color:var(--accent);">
        <span><strong>Force re-enrich all articles</strong>
        <span style="display:block;font-size:12px;color:var(--muted);">Re-run every article, not just unenriched ones. Use this to standardize the archive on a new model or rules version. Summary is overwritten; existing tags are merged.</span></span>
      </label>
    </div>
    <div>
      <button type="submit" class="btn" style="font-size:15px;padding:11px 28px;" {disable}>Start enrichment</button>
      <span style="font-size:13px;color:var(--muted);margin-left:14px;">Runs in the background — you can leave this page.</span>
    </div>
  </form>
</div>

<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 20px;">
  <p style="font-size:13.5px;color:var(--muted);margin:0;line-height:1.6;">
    <strong>After finishing:</strong> visit <a href="/admin/review-removals">Review removals</a> to confirm any articles the enricher flagged as off-audience,
    and check the enriched summaries in the <a href="/archive">Archive</a>.
  </p>
</div>

</div>
<script>
(function() {{
  var reloadOnDone = false;
  function poll() {{
    fetch('/admin/enrich/status').then(r => r.json()).then(function(s) {{
      var container = document.getElementById('poll-container');
      if (!container) return;
      var progPct = s.total > 0 ? Math.round(s.done / s.total * 100) : 0;
      if (s.running) {{
        reloadOnDone = true;
        container.innerHTML = '<div id="job-status" style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;margin-bottom:20px;">'
          + '<div style="font-weight:600;font-size:14px;color:#1d4ed8;margin-bottom:6px;">Re-enrichment in progress&hellip;</div>'
          + '<div style="font-size:13px;color:var(--muted);">Model: <strong>' + s.model + '</strong> &middot; ' + s.done + ' / ' + s.total + ' done</div>'
          + '<div style="background:#dbeafe;border-radius:6px;height:8px;margin-top:10px;overflow:hidden;">'
          + '<div style="background:#2563eb;height:8px;width:' + progPct + '%;transition:width .3s;"></div></div></div>';
        setTimeout(poll, 2000);
      }} else if (reloadOnDone) {{
        // Job finished while we were watching — reload so the stat counters refresh.
        window.location.reload();
      }} else if (s.done > 0 && !s.error) {{
        container.innerHTML = '<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#065f46;">Done — ' + s.done + ' articles enriched with ' + s.model + '.</div>';
      }} else if (s.error) {{
        container.innerHTML = '<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#b91c1c;">Error: ' + s.error + '</div>';
      }}
    }}).catch(function() {{ setTimeout(poll, 3000); }});
  }}
  if ({str(running).lower()}) {{ reloadOnDone = true; setTimeout(poll, 2000); }}
  document.getElementById('enrich-form').addEventListener('submit', function() {{
    setTimeout(function() {{ poll(); }}, 1500);
  }});
}})();
</script>"""
    return HTMLResponse(_page("Re-enrich archive — Admin", "Admin", body, authed=True))


@app.post("/admin/enrich/cleanup-toggle")
def admin_enrich_cleanup_toggle(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        on = lib.get_setting("scope_cleanup", "on") != "off"
        lib.set_setting("scope_cleanup", "off" if on else "on")
    finally:
        lib.close()
    return RedirectResponse("/admin/enrich", status_code=303)


@app.post("/admin/enrich/start")
async def admin_enrich_start(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    if _job_get("enrich").get("running"):
        return RedirectResponse("/admin/enrich?running=1", status_code=303)
    form = await request.form()
    force = bool(form.get("force"))
    model = (form.get("model") or "").strip()
    if not model:
        from linklib.enrich import DEFAULT_MODEL
        model = DEFAULT_MODEL
    t = threading.Thread(target=_enrich_job, args=(force, model, 100000), daemon=True)
    t.start()
    return RedirectResponse("/admin/enrich", status_code=303)


@app.get("/admin/enrich/status")
def admin_enrich_status(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401)
    return JSONResponse(_job_get("enrich"))


# ---------------------------------------------------------------------------
# Historical sitemap backfill — queue articles going back to the saves cutoff
# ---------------------------------------------------------------------------

def _backfill_job(since_str: str, per_source: int, model: str, dry_run: bool,
                  only_sources_raw: str = "") -> None:
    """Background thread: run scan_sitemaps_into_queue, updating _JOB_STATE["backfill"].

    `only_sources_raw` (comma/newline separated) restricts the sweep to matching
    sources — case-insensitive substring match against the OPML name, so
    "Stratechery" matches "Ben Thompson (Stratechery)". Empty = all sources.
    """
    _job_set("backfill", running=True, report=[], error="", done=0, total=0)
    lib = _lib()
    try:
        from linklib.feed import parse_opml
        from linklib.queue import scan_sitemaps_into_queue

        feeds = parse_opml(OPML_PATH)
        tokens = [t.strip().lower() for t in only_sources_raw.replace("\n", ",").split(",") if t.strip()]
        if tokens:
            feeds = [f for f in feeds if any(tok in f.name.lower() for tok in tokens)]
        total = len(feeds)
        _job_set("backfill", total=total)
        sources_done = [0]

        def _progress(source_name, i, n):
            if i == n:  # last item in this source
                sources_done[0] += 1
                _job_set("backfill", done=sources_done[0])

        report = scan_sitemaps_into_queue(
            lib, feeds, since_str,
            enrich=True, model=model,
            per_source_limit=per_source,
            dry_run=dry_run,
            progress=_progress,
        )
        _job_set("backfill", running=False, report=report, done=total)
        if not dry_run:
            backup.maybe_backup(DB_PATH)
    except Exception as exc:
        _job_set("backfill", running=False, error=str(exc))
    finally:
        lib.close()


@app.get("/admin/backfill", response_class=HTMLResponse)
def admin_backfill(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        last_saved = lib.last_saved_at()
    finally:
        lib.close()

    from linklib.queue import QUEUE_ENRICH_MODEL

    default_since = (last_saved or "2024-06-01")[:10]
    job = _job_get("backfill")
    running = job.get("running", False)
    job_error = job.get("error", "")
    report = job.get("report", [])
    job_done = job.get("done", 0)
    job_total = job.get("total", 0)

    status_html = ""
    if running:
        prog_pct = round(job_done / job_total * 100) if job_total else 0
        status_html = f"""
<div id="job-status" style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;margin-bottom:20px;">
  <div style="font-weight:600;font-size:14px;color:#1d4ed8;margin-bottom:4px;">Sitemap sweep in progress&hellip;</div>
  <div style="font-size:13px;color:var(--muted);">{job_done} / {job_total} sources scanned</div>
  <div style="background:#dbeafe;border-radius:6px;height:8px;margin-top:10px;overflow:hidden;">
    <div style="background:#2563eb;height:8px;width:{prog_pct}%;transition:width .3s;"></div>
  </div>
</div>"""
    elif job_error:
        status_html = f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#b91c1c;">Error: {_esc(job_error)}</div>'
    elif report:
        total_added = sum(r.get("added", 0) for r in report)
        total_cands = sum(r.get("candidates", 0) for r in report)
        total_scope = sum(r.get("skipped_scope", 0) for r in report)
        status_html = f'<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#065f46;">Sweep complete &mdash; {total_added} articles queued from {total_cands} candidates ({total_scope} skipped as off-audience). <a href="/admin/queue">Review in Archive Queue &rarr;</a></div>'

    def _report_row(r):
        added = r.get("added", 0)
        cands = r.get("candidates", 0)
        scope = r.get("skipped_scope", 0)
        note = r.get("note", "")
        sitemap = r.get("sitemap") or ""
        sm_link = f'<a href="{_esc(sitemap)}" style="font-size:11px;color:var(--muted);" target="_blank">{_esc(sitemap[:60])}{"…" if len(sitemap)>60 else ""}</a>' if sitemap else '<span style="font-size:11px;color:var(--muted);">—</span>'
        extra = (f" / {scope} off-audience" if scope else "")
        status = note if note else f'{added} added / {cands} candidates{extra}'
        status_color = "#b91c1c" if note else ("#16a34a" if added else "#92400e")
        return (f'<tr><td style="padding:8px 12px;font-size:13px;font-weight:500;">{_esc(r.get("source",""))}</td>'
                f'<td style="padding:8px 12px;">{sm_link}</td>'
                f'<td style="padding:8px 12px;font-size:13px;color:{status_color};">{_esc(status)}</td></tr>')

    report_html = ""
    if report:
        rows_html = "".join(_report_row(r) for r in report)
        report_html = f"""
<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:20px;">
  <div style="padding:14px 18px;border-bottom:1px solid var(--line);font-weight:600;font-size:14px;">Coverage report</div>
  <div style="overflow-x:auto;">
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr style="background:var(--bg);">
      <th style="padding:8px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Source</th>
      <th style="padding:8px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Sitemap</th>
      <th style="padding:8px 12px;text-align:left;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;">Result</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
</div>"""

    disable = 'disabled style="opacity:.5;cursor:not-allowed;"' if running else ""

    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Historical sweep</h1>
<p style="color:var(--muted);margin:-6px 0 20px;">Walks each source&rsquo;s sitemap and queues anything you haven&rsquo;t saved yet, for your review. A one-time catch-up on your back catalog.</p>

<div style="background:#fefce8;border:1px solid #fde68a;border-radius:10px;padding:14px 18px;margin-bottom:22px;font-size:13.5px;color:#92400e;line-height:1.6;">
  <strong>Run this once.</strong> It catches up your back catalog; after that, the <a href="/admin/queue">Archive Queue</a> feed scan keeps you current.
  Start with a <strong>dry run</strong> to see the reach before any sweep spends API calls.
</div>

<div id="poll-container">{status_html}</div>

<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:20px;">
  <form id="backfill-form" method="post" action="/admin/backfill/start" style="display:grid;gap:18px;">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div>
        <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Articles published since</label>
        <input type="date" name="since" value="{default_since}" max="{datetime.now().strftime('%Y-%m-%d')}"
          style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);" required>
        <p style="font-size:12px;color:var(--muted);margin:4px 0 0;">Auto-detected from your oldest save: <strong>{default_since}</strong></p>
      </div>
      <div>
        <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Max articles per source</label>
        <input type="number" name="per_source" value="150" min="10" max="2000"
          style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);">
        <p style="font-size:12px;color:var(--muted);margin:4px 0 0;">150 is a safe starting point. Raise it to reach further back — the sweep takes the most recent N, so a low cap stops early on prolific sources.</p>
      </div>
    </div>
    <div>
      <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Limit to sources <span style="font-weight:400;text-transform:none;letter-spacing:0;">(optional)</span></label>
      <input type="text" name="only_sources" placeholder="e.g. Kellblog, Stratechery, SaaStr"
        style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);">
      <p style="font-size:12px;color:var(--muted);margin:4px 0 0;">Comma-separated. Leave blank to sweep everything. Re-running is safe — already-queued and saved URLs are skipped, so a bigger limit only adds the older articles you haven&rsquo;t seen yet.</p>
    </div>
    <div>
      <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Enrichment model</label>
      <select name="model" style="padding:9px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:14px;background:var(--bg);min-width:240px;">
        <option value="claude-opus-4-8" {"selected" if QUEUE_ENRICH_MODEL=="claude-opus-4-8" else ""}>Opus 4.8 — best quality (recommended)</option>
        <option value="claude-sonnet-4-6" {"selected" if QUEUE_ENRICH_MODEL=="claude-sonnet-4-6" else ""}>Sonnet 4.6 — balanced</option>
        <option value="claude-haiku-4-5-20251001" {"selected" if QUEUE_ENRICH_MODEL=="claude-haiku-4-5-20251001" else ""}>Haiku 4.5 — fast and cheap</option>
      </select>
    </div>
    <div>
      <label style="display:flex;align-items:flex-start;gap:8px;font-size:14px;cursor:pointer;">
        <input type="checkbox" name="dry_run" value="1" checked style="margin-top:3px;accent-color:var(--accent);">
        <span><strong>Dry run</strong>
        <span style="display:block;font-size:12px;color:var(--muted);">Count candidates without fetching or enriching anything. Uncheck to do the real sweep.</span></span>
      </label>
    </div>
    <div>
      <button type="submit" class="btn" style="font-size:15px;padding:11px 28px;" {disable}>Run sweep</button>
      <span style="font-size:13px;color:var(--muted);margin-left:14px;">Runs server-side &mdash; you can leave this page. Results appear in the <a href="/admin/queue">Archive Queue</a>.</span>
    </div>
  </form>
</div>

{report_html}
</div>
<script>
(function() {{
  var reloadOnDone = false;
  function poll() {{
    fetch('/admin/backfill/status').then(r => r.json()).then(function(s) {{
      var container = document.getElementById('poll-container');
      if (!container) return;
      var progPct = s.total > 0 ? Math.round(s.done / s.total * 100) : 0;
      if (s.running) {{
        reloadOnDone = true;
        container.innerHTML = '<div id="job-status" style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;margin-bottom:20px;">'
          + '<div style="font-weight:600;font-size:14px;color:#1d4ed8;margin-bottom:4px;">Sitemap sweep in progress&hellip;</div>'
          + '<div style="font-size:13px;color:var(--muted);">' + s.done + ' / ' + s.total + ' sources scanned</div>'
          + '<div style="background:#dbeafe;border-radius:6px;height:8px;margin-top:10px;overflow:hidden;">'
          + '<div style="background:#2563eb;height:8px;width:' + progPct + '%;transition:width .3s;"></div></div></div>';
        setTimeout(poll, 3000);
      }} else if (reloadOnDone) {{
        // Job finished while we were watching — reload so the full coverage table renders.
        window.location.reload();
      }} else if (s.report && s.report.length) {{
        var totalAdded = s.report.reduce((a, r) => a + (r.added || 0), 0);
        var totalCands = s.report.reduce((a, r) => a + (r.candidates || 0), 0);
        var totalScope = s.report.reduce((a, r) => a + (r.skipped_scope || 0), 0);
        container.innerHTML = '<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#065f46;">Sweep complete &mdash; ' + totalAdded + ' articles queued from ' + totalCands + ' candidates (' + totalScope + ' skipped as off-audience). <a href=\\"/admin/queue\\">Review in Archive Queue &rarr;</a></div>';
      }} else if (s.error) {{
        container.innerHTML = '<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#b91c1c;">Error: ' + s.error + '</div>';
      }}
    }}).catch(function() {{ setTimeout(poll, 4000); }});
  }}
  if ({str(running).lower()}) {{ reloadOnDone = true; setTimeout(poll, 3000); }}
  document.getElementById('backfill-form').addEventListener('submit', function() {{
    setTimeout(function() {{ poll(); }}, 2000);
  }});
}})();
</script>"""
    return HTMLResponse(_page("Historical sweep — Admin", "Admin", body, authed=True))


@app.post("/admin/backfill/start")
async def admin_backfill_start(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    if _job_get("backfill").get("running"):
        return RedirectResponse("/admin/backfill?running=1", status_code=303)
    form = await request.form()
    since = (form.get("since") or "2024-06-01").strip()
    try:
        per_source = int(form.get("per_source") or 150)
    except ValueError:
        per_source = 150
    model = (form.get("model") or "claude-opus-4-8").strip()
    dry_run = bool(form.get("dry_run"))
    only_sources = (form.get("only_sources") or "").strip()
    t = threading.Thread(target=_backfill_job,
                         args=(since, per_source, model, dry_run, only_sources), daemon=True)
    t.start()
    return RedirectResponse("/admin/backfill", status_code=303)


@app.get("/admin/backfill/status")
def admin_backfill_status(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401)
    return JSONResponse(_job_get("backfill"))


@app.get("/admin/backup", response_class=HTMLResponse)
def admin_backup(request: Request, uploaded: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        count = lib.count()
    finally:
        lib.close()
    uploaded_banner = (
        f'<p style="background:#d1fae5;color:#065f46;border-radius:10px;padding:10px 16px;'
        f'font-size:14px;margin:-6px 0 16px;">Database replaced — {_esc(uploaded)} articles now live.</p>'
        if uploaded else ''
    )
    body = f"""<div class="page" style="max-width:820px;">
<p style="margin:0 0 4px;"><a href="/admin/library" style="font-size:13px;color:var(--muted);">&larr; Archive</a></p>
<h1>Archive backup</h1>
{uploaded_banner}
<p style="color:var(--muted);margin:-6px 0 24px;">Currently <strong>{count:,}</strong> articles in the live database.</p>

<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:40px;">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start;">
    <div>
      <p style="font-weight:600;font-size:15px;margin:0 0 6px;">Download backup</p>
      <p style="font-size:13px;color:var(--muted);margin:0 0 14px;">Download a consistent snapshot of the live database. Do this before uploading a replacement so you can recover if something goes wrong.</p>
      <a href="/admin/download-db" class="btn" style="font-size:14px;padding:9px 20px;display:inline-block;text-decoration:none;">Download library.db</a>
    </div>
    <div style="border-left:1px solid var(--line);padding-left:24px;">
      <p style="font-weight:600;font-size:15px;margin:0 0 6px;">Upload replacement database</p>
      <p style="font-size:13px;color:var(--muted);margin:0 0 14px;">Quit your local app first so the file is fully written, then upload <code>library.db</code>. Takes effect immediately — no restart needed.</p>
      <form method="post" action="/admin/upload-db" enctype="multipart/form-data" style="display:flex;flex-direction:column;gap:10px;">
        <input type="file" name="file" accept=".db,.sqlite,.sqlite3,application/octet-stream" required
          style="font-size:13px;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--bg);">
        <button type="submit" class="btn" style="font-size:14px;padding:9px 20px;">Upload and replace</button>
      </form>
    </div>
  </div>
</div>
</div>"""
    return HTMLResponse(_page("Archive backup — Admin", "Admin", body, authed=True))


@app.get("/admin/brand", response_class=HTMLResponse)
def admin_brand(request: Request):
    """A living style guide — the brand standards rendered with the real tokens.
    The written reference lives in BRAND.md; this page is the visual companion."""
    if not _is_authed(request):
        return _login_redirect(request)

    # Verbal identity: the voice guide (editable) lives here too — it's part of the brand.
    lib = _lib()
    try:
        custom_voice = lib.get_setting("voice_prompt")
    finally:
        lib.close()
    from linklib.social import BRIAN_VOICE
    current_voice = custom_voice or BRIAN_VOICE
    is_customized = bool(custom_voice)
    if is_customized:
        voice_badge = ('<span id="voice-badge" style="font-size:12px;font-weight:600;background:#d1fae5;'
                       'color:#065f46;border-radius:6px;padding:2px 8px;margin-left:10px;vertical-align:middle;">Customized</span>')
    else:
        voice_badge = ('<span id="voice-badge" style="font-size:12px;color:var(--muted);'
                       'margin-left:10px;vertical-align:middle;">Built-in default</span>')
    reset_btn = (
        '<button id="reset-btn" onclick="resetVoice()" class="btn btn-ghost" '
        'style="font-size:13px;color:#b91c1c;border-color:#fca5a5;'
        f'{"" if is_customized else "display:none;"}">Reset to default</button>'
    )

    # Brand palette (literal hexes mirror the _CSS :root tokens; see BRAND.md §7).
    CORAL, CORAL_WASH, CORAL_DEEP = "#E8704F", "#FBEAE3", "#B14A30"

    def swatch(hexv: str, name: str, role: str, border: bool = False, tag: str = "") -> str:
        bd = ";border-bottom:1px solid var(--line-strong)" if border else ""
        badge = (f'<span style="background:{CORAL};color:#fff;font:600 9px var(--font-body);'
                 f'letter-spacing:.08em;text-transform:uppercase;border-radius:5px;padding:1px 6px;'
                 f'margin-left:6px;vertical-align:middle;">{tag}</span>') if tag else ""
        return (
            f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;">'
            f'<div style="height:60px;background:{hexv}{bd};"></div>'
            f'<div style="padding:10px 12px;">'
            f'<div style="font:600 13px var(--font-body);color:var(--ink);">{name}{badge}</div>'
            f'<div style="font:500 12px ui-monospace,monospace;color:var(--muted);margin-top:2px;">{hexv}</div>'
            f'<div style="font:400 12px var(--font-body);color:var(--muted);margin-top:5px;line-height:1.45;">{role}</div>'
            f'</div></div>'
        )

    def grid(cards: str) -> str:
        return (f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));'
                f'gap:14px;margin:0 0 20px;">{cards}</div>')

    star = lambda c: ('<svg width="22" height="22" viewBox="0 0 16 16" aria-hidden="true">'
                      f'<path d="M8 0 L9.4 6.6 L16 8 L9.4 9.4 L8 16 L6.6 9.4 L0 8 L6.6 6.6 Z" fill="{c}"/></svg>')

    def ramp_label(text: str) -> str:
        return (f'<div style="font:600 12px var(--font-body);letter-spacing:.1em;'
                f'text-transform:uppercase;color:var(--navy);margin:0 0 10px;">{text}</div>')

    navy_ramp = ramp_label("Navy — primary (cool)") + grid(
        swatch("#001B4F", "Navy-deep", "Button hover, depth.")
        + swatch("#002975", "Navy", "Base — wordmark, links, buttons, headings accents.")
        + swatch("#3F5C9A", "Navy-light", "Lighter navy — secondary accents, borders. Text-capable (5.9:1).")
        + swatch("#EEF1F7", "Navy-wash", "Soft navy fill — chip & ghost-button hovers.", border=True)
    )
    green_ramp = ramp_label("Seafoam / Green — cool accent") + grid(
        swatch("#1F7A66", "Seafoam-deep", "Deepest teal — text-capable on light (4.7:1).")
        + swatch("#2E9C86", "Seafoam-mid", "Mid teal — data-viz; legible as a fill/line. ≥18px text only.")
        + swatch("#A3E5D4", "Seafoam", "Base accent (light mint) — tags, badges, active-nav underline.", border=True)
        + swatch("#EAF7F2", "Seafoam-wash", "Soft fill — readout panels, table accents.", border=True)
    )
    coral_ramp = ramp_label("Coral — warm accent (rare)") + grid(
        swatch(CORAL_DEEP, "Coral-deep", "Text-capable coral (4.9:1) — only when coral must carry small text.")
        + swatch(CORAL, "Coral", "Base — display pop, badges, data-viz R&D series. Graphics & ≥24px only.")
        + swatch("#F4A98F", "Coral-light", "Lighter coral — soft highlights, fills only (never text).", border=True)
        + swatch(CORAL_WASH, "Coral-wash", "Soft fill — callout blocks (put navy text on it).", border=True)
    )

    dataviz_note = (
        '<div style="background:var(--seafoam-wash);border:1px solid #C9EADF;border-radius:12px;padding:14px 18px;margin:0 0 20px;">'
        '<p style="margin:0;font-size:14px;color:var(--navy);"><strong>Data-viz palette:</strong> charts use the three families '
        'as categories — <strong>GTM&nbsp;=&nbsp;navy</strong>, <strong>Revenue&nbsp;=&nbsp;seafoam-mid teal</strong>, '
        '<strong>R&amp;D&nbsp;=&nbsp;coral</strong> (coral marks the series to notice). See the Growth Engine Ratio charts. '
        'Chart text is DM&nbsp;Sans; big readouts are Outfit.</p></div>'
    )

    neutral_row = grid(
        swatch("#F5F4EF", "bg", "Page canvas (warm off-white).", border=True)
        + swatch("#FFFFFF", "surface", "Cards, inputs.", border=True)
        + swatch("#FAF9F4", "surface-2", "Alt panels, table stripes.", border=True)
        + swatch("#1a1a1a", "ink", "Headings, primary text.")
        + swatch("#3a3833", "ink-soft", "Body copy.")
        + swatch("#6F6A60", "muted", "Meta, captions, kickers.")
        + swatch("#E4E0D6", "line", "Warm hairline.", border=True)
        + swatch("#D6D1C4", "line-strong", "Heavier divider / top of the rope rule.", border=True)
    )

    semantic_row = grid(
        swatch("#002975", "good", "GER 'Elite/Strong' tiers.")
        + swatch("#9A6B12", "caution", "GER 'Typical' tier.")
        + swatch("#9E3B30", "alert", "Errors, GER 'Below target'. Status only — never decorative.")
    )

    callout = (lambda bg, bd, body_html:
               f'<div style="background:{bg};border:1px solid {bd};border-radius:12px;padding:16px 20px;margin:0 0 18px;">{body_html}</div>')

    type_specimens = (
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px 26px;margin:0 0 18px;">'
        '<div style="font:600 12px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:var(--navy);margin-bottom:6px;">Outfit — headings &amp; display</div>'
        '<div style="font-family:var(--font-head);font-weight:600;font-size:42px;letter-spacing:-0.025em;line-height:1.05;color:var(--ink);">Brian Weisberg</div>'
        '<div style="font-family:var(--font-head);font-weight:600;font-size:21px;letter-spacing:-0.01em;color:var(--ink);margin-top:10px;">Strategic finance for companies that are scaling</div>'
        '<div style="height:18px;"></div>'
        '<div style="font:600 12px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:var(--navy);margin-bottom:6px;">DM Sans — body &amp; UI</div>'
        '<p style="margin:0;color:var(--ink-soft);">The quick brown fox jumps over the lazy dog. Body copy is DM Sans at 16px / 1.65 — warm, readable, and quiet enough to disappear behind the content. Eyebrows and labels use the same family, uppercase, with wide tracking.</p>'
        '<div style="height:18px;"></div>'
        '<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600&display=swap" rel="stylesheet">'
        '<div style="font:600 12px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:var(--navy);margin-bottom:6px;">Source Serif 4 — long-form reading only (/read)</div>'
        '<p style="margin:0;font-family:\'Source Serif 4\',Georgia,serif;font-size:18px;line-height:1.75;color:var(--ink);">Revenue recognized today is the result of investments made over the past several quarters, not just last quarter. Features ship before they\'re sold; pipeline built in Q1 converts in Q3. The serif appears nowhere else in the system.</p>'
        '</div>'
    )

    motif = (
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px 26px;margin:0 0 18px;">'
        '<div style="font:500 13px var(--font-body);color:var(--muted);margin-bottom:10px;">Rope rule — the double hairline. Frames the header/footer or separates major sections. Never repeated decoratively.</div>'
        '<div class="rule"></div>'
        '<div style="height:26px;"></div>'
        '<div style="font:500 13px var(--font-body);color:var(--muted);margin-bottom:10px;">Compass star — one per page, in the footer. Navy by default; a coral variant is reserved for special headers.</div>'
        f'<div style="display:flex;align-items:center;gap:20px;">{star("#002975")}{star(CORAL)}</div>'
        '<div style="margin-top:14px;font:500 13px var(--font-body);color:var(--alert);">No anchors, ropes-everywhere, boats, waves, knots, or clip-art. These two marks are the entire nautical vocabulary.</div>'
        '</div>'
    )

    components = (
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px 26px;margin:0 0 18px;display:grid;gap:22px;">'
        # buttons
        '<div><div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Buttons — navy fill or ghost outline (never a color fill)</div>'
        '<a class="btn" href="#" onclick="return false;">Primary</a> '
        '<a class="btn btn-ghost" href="#" onclick="return false;" style="margin-left:8px;">Ghost</a></div>'
        # tags
        '<div><div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Tags — seafoam fill, navy text</div>'
        '<span style="font:600 11px var(--font-body);color:var(--navy);background:var(--seafoam);border-radius:6px;padding:3px 9px;">FP&amp;A</span> '
        '<span style="font:600 11px var(--font-body);color:var(--navy);background:var(--seafoam);border-radius:6px;padding:3px 9px;margin-left:4px;">Treasury</span></div>'
        # input
        '<div><div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Input — click to see the seafoam focus ring</div>'
        '<input type="text" placeholder="Search…" style="width:100%;max-width:320px;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--surface);"></div>'
        # table
        '<div><div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Table — navy header, white text</div>'
        '<table style="width:100%;max-width:380px;border-collapse:collapse;font-size:14px;border:1px solid var(--line);border-radius:10px;overflow:hidden;">'
        '<thead><tr style="background:var(--navy);"><th style="padding:8px 12px;text-align:left;color:#fff;">Tier</th><th style="padding:8px 12px;text-align:left;color:#fff;">Ratio</th></tr></thead>'
        '<tbody><tr style="border-top:1px solid var(--line);"><td style="padding:8px 12px;">Elite</td><td style="padding:8px 12px;">&gt; $1.20</td></tr>'
        '<tr style="border-top:1px solid var(--line);background:var(--surface-2);"><td style="padding:8px 12px;">Strong</td><td style="padding:8px 12px;">$0.70–1.20</td></tr></tbody></table></div>'
        # coral in action
        '<div><div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;">Coral in action — rare, decorative, never status</div>'
        f'<span style="font:600 11px var(--font-body);letter-spacing:.06em;text-transform:uppercase;color:#fff;background:{CORAL};border-radius:6px;padding:3px 10px;">New</span>'
        f'<div style="background:{CORAL_WASH};border:1px solid #F3D3C6;border-radius:12px;padding:14px 18px;margin-top:12px;">'
        f'<span style="font:600 12px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:{CORAL_DEEP};">Highlight</span>'
        '<p style="margin:6px 0 0;color:var(--navy);">A coral-wash callout carries navy text at 11:1 contrast — the accessible way to make coral carry a block of copy.</p></div></div>'
        '</div>'
    )

    mono = ("width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;"
            "font:13px/1.6 ui-monospace,monospace;background:var(--bg);resize:vertical;")
    verbal = (
        '<div style="display:flex;align-items:center;gap:10px;margin:0 0 6px;">'
        '<span style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);">Voice guide</span>'
        f'{voice_badge}</div>'
        '<p style="color:var(--muted);margin:0 0 14px;font-size:14px;">The guide Claude uses to draft in your voice, and the rubric the voice check holds new writing to.</p>'
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 18px;">'
        f'<textarea id="voice-prompt" rows="16" style="{mono}">{_esc(current_voice)}</textarea>'
        '<div style="display:flex;gap:10px;margin-top:12px;align-items:center;">'
        '<button id="voice-save-btn" onclick="saveVoice()" class="btn" style="font-size:14px;padding:9px 22px;">Save voice</button>'
        f'{reset_btn}'
        '<span id="voice-status" style="font-size:13px;color:var(--muted);"></span></div></div>'
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 18px;">'
        '<div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;">Check content against your voice</div>'
        '<p style="font-size:13px;color:var(--muted);margin:0 0 12px;">Paste any draft or page copy. Mechanical rules (banned words, filler, performative phrases) flag instantly; Review adds Claude&rsquo;s read on tone.</p>'
        f'<textarea id="vr-input" rows="8" placeholder="Paste content to check against your voice…" style="{mono}"></textarea>'
        '<div style="display:flex;gap:10px;margin-top:12px;align-items:center;">'
        '<button id="vr-btn" onclick="reviewVoice()" class="btn" style="font-size:14px;padding:9px 22px;">Review against my voice</button>'
        '<span id="vr-status" style="font-size:13px;color:var(--muted);"></span></div>'
        '<div id="vr-result" style="display:none;margin-top:16px;border-top:1px solid var(--line);padding-top:14px;font-size:14px;line-height:1.6;"></div>'
        '</div>'
    )

    rules = callout(
        "var(--surface)", "var(--line)",
        '<div style="font:600 12px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--navy);margin-bottom:10px;">Usage rules</div>'
        '<ul style="margin:0;padding-left:20px;color:var(--ink-soft);line-height:1.7;">'
        '<li><strong>Balance ~70 / 20 / 10</strong> — navy + neutrals, then seafoam, then a sliver of coral. One coral element per screen, max.</li>'
        '<li><strong>Coral is decorative, never status.</strong> Alert red means error; coral means highlight. They\'re 96 RGB-units apart — keep it that way.</li>'
        '<li><strong>Coral is display-only.</strong> It\'s too light for body text (2.8:1); use coral-deep, or navy-on-coral-wash, when text is involved.</li>'
        '<li><strong>Buttons are navy or ghost</strong> — never a seafoam or coral fill.</li>'
        '<li><strong>One rope rule, one compass star</strong> per page. Outfit for headings, DM Sans for everything, Source Serif 4 for reading only.</li>'
        '</ul>'
    )

    checks_doc = (
        '<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 18px;">'
        '<div style="overflow-x:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:14px;min-width:560px;">'
        '<thead><tr style="background:var(--navy);">'
        '<th style="padding:9px 12px;text-align:left;color:#fff;">Check</th>'
        '<th style="padding:9px 12px;text-align:left;color:#fff;">What it looks at</th>'
        '<th style="padding:9px 12px;text-align:left;color:#fff;">When &amp; where</th>'
        '<th style="padding:9px 12px;text-align:left;color:#fff;">Cost</th>'
        '</tr></thead><tbody>'
        '<tr style="border-top:1px solid var(--line);">'
        '<td style="padding:10px 12px;font-weight:600;color:var(--navy);">Brand check</td>'
        '<td style="padding:10px 12px;">Colors, fonts, and the voice <em>mechanics</em> &mdash; banned buzzwords, filler, performative phrases.</td>'
        '<td style="padding:10px 12px;"><strong>Automatic.</strong> Every push &amp; pull request via GitHub Actions (<code>.github/workflows/qa.yml</code>); blocks merge on failure. Locally: <code>pytest -q</code>.</td>'
        '<td style="padding:10px 12px;white-space:nowrap;">Free &middot; deterministic</td>'
        '</tr>'
        '<tr style="border-top:1px solid var(--line);background:var(--surface-2);">'
        '<td style="padding:10px 12px;font-weight:600;color:var(--navy);">Tone review</td>'
        '<td style="padding:10px 12px;">The holistic read &mdash; &ldquo;does this sound like me&rdquo; &mdash; judged by Claude against the voice guide.</td>'
        '<td style="padding:10px 12px;"><strong>On demand only.</strong> The <em>Check content against your voice</em> box below, or the CLI <code>python -m scripts.voice_review</code>. <strong>Never in CI.</strong></td>'
        '<td style="padding:10px 12px;white-space:nowrap;">API key, per run</td>'
        '</tr>'
        '</tbody></table></div>'
        '<p style="font-size:13px;color:var(--muted);margin:14px 0 0;">Both share one source of truth: the voice guide below and <code>linklib/voice_review.py</code>. '
        'Tone review stays out of CI on purpose &mdash; running Claude on every commit would be slow, non-deterministic, and spend the pay-per-use API key.</p>'
        '</div>'
    )

    body = f"""<div class="page" style="max-width:900px;">
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Brand standards</h1>
<p style="color:var(--muted);margin:4px 0 30px;">The living style guide for bmweis.com — New England nautical, restrained.
The full written reference is <code>BRAND.md</code> in the repo; an automated check
(<code>tests/test_brand_standards.py</code>) keeps new content on-palette.</p>

<h2 style="margin-top:0;">Brand colors</h2>
<p style="color:var(--muted);margin:-6px 0 18px;font-size:14px;">Three families, each with a working ramp.
Deep shades are text-capable; base/mid are for graphics and large display; light/wash are fills only.</p>
{navy_ramp}
{green_ramp}
{coral_ramp}
{dataviz_note}
<h2>Neutrals</h2>
{neutral_row}
<h2>Semantic — status only</h2>
<p style="color:var(--muted);margin:-6px 0 16px;font-size:14px;">Reserved for state. Never used decoratively, and never confused with coral.</p>
{semantic_row}

<h2>Typography</h2>
{type_specimens}

<h2>The nautical motif</h2>
{motif}

<h2>Components</h2>
{components}

<h2>Usage</h2>
{rules}

<h2>How the checks run</h2>
{checks_doc}

<h2>Verbal identity — your voice</h2>
{verbal}
</div>

<script>
async function saveVoice() {{
  var prompt = document.getElementById('voice-prompt').value;
  var btn = document.getElementById('voice-save-btn');
  var status = document.getElementById('voice-status');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {{
    var r = await fetch('/admin/voice', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{voice_prompt: prompt.trim()}})}});
    if (!r.ok) throw new Error();
    var d = await r.json();
    status.textContent = 'Saved.'; status.style.color = '#065f46';
    setTimeout(function() {{ status.textContent = ''; }}, 3000);
    var badge = document.getElementById('voice-badge'), resetBtn = document.getElementById('reset-btn');
    if (d.custom) {{
      badge.textContent = 'Customized';
      badge.style.cssText = 'font-size:12px;font-weight:600;background:#d1fae5;color:#065f46;border-radius:6px;padding:2px 8px;margin-left:10px;vertical-align:middle;';
      resetBtn.style.display = '';
    }} else {{
      badge.textContent = 'Built-in default';
      badge.style.cssText = 'font-size:12px;color:var(--muted);margin-left:10px;vertical-align:middle;';
      resetBtn.style.display = 'none';
    }}
  }} catch(e) {{
    status.textContent = 'Save failed — try again.'; status.style.color = '#b91c1c';
  }} finally {{ btn.disabled = false; btn.textContent = 'Save voice'; }}
}}

async function resetVoice() {{
  if (!confirm('Reset to the built-in default voice prompt? Your edits will be lost.')) return;
  try {{
    var r = await fetch('/admin/voice', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{voice_prompt: ''}})}});
    if (!r.ok) throw new Error();
    window.location.reload();
  }} catch(e) {{ alert('Reset failed — try again.'); }}
}}

async function reviewVoice() {{
  var text = document.getElementById('vr-input').value.trim();
  if (!text) {{ document.getElementById('vr-input').focus(); return; }}
  var btn = document.getElementById('vr-btn'), box = document.getElementById('vr-result');
  btn.disabled = true; btn.textContent = 'Reviewing…';
  box.style.display = 'block'; box.innerHTML = '<em>Checking…</em>';
  try {{
    var r = await fetch('/admin/voice/review', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{text: text}})}});
    var d = await r.json();
    var mech = d.mechanical || [];
    var mechHtml = mech.length
      ? '<div style="margin-bottom:12px;"><strong style="color:#9E3B30;">Mechanical flags (' + mech.length + ')</strong>'
        + '<ul style="margin:6px 0 0;padding-left:18px;">' + mech.map(function(m) {{ return '<li><code>' + m[1] + '</code> — ' + m[0] + '</li>'; }}).join('') + '</ul></div>'
      : '<div style="margin-bottom:12px;color:#065f46;"><strong>No mechanical violations.</strong></div>';
    var rev = (d.review || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\\n/g, '<br>');
    box.innerHTML = mechHtml + '<div>' + rev + '</div>';
  }} catch(e) {{ box.innerHTML = 'Review failed — try again.'; }}
  finally {{ btn.disabled = false; btn.textContent = 'Review against my voice'; }}
}}
</script>"""
    return HTMLResponse(_page("Brand standards — Admin", "Admin", body, authed=True))


@app.post("/admin/voice")
async def admin_voice_save(request: Request):
    """Save (or reset) the custom voice prompt."""
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    prompt = (payload.get("voice_prompt") or "").strip()
    lib = _lib()
    try:
        lib.set_setting("voice_prompt", prompt)
    finally:
        lib.close()
    return JSONResponse({"ok": True, "custom": bool(prompt)})


@app.post("/admin/voice/review")
async def admin_voice_review(request: Request):
    """Review pasted content against the voice guide (mechanical lint + Claude tone read)."""
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    from linklib.voice_review import review_text
    lib = _lib()
    try:
        custom_voice = lib.get_setting("voice_prompt")
    finally:
        lib.close()
    return JSONResponse(review_text(text, voice_prompt=custom_voice or None))


@app.post("/admin/upload-db", response_class=HTMLResponse)
async def upload_db(request: Request, file: UploadFile = File(...), token: str | None = None):
    _require_api(request, token)
    import sqlite3
    import tempfile

    dest = os.path.abspath(DB_PATH)
    dest_dir = os.path.dirname(dest) or "."
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".upload")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        # Validate it's a real library DB before swapping anything in.
        try:
            check = sqlite3.connect(tmp)
            n = check.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            check.close()
        except Exception as e:
            raise HTTPException(status_code=400,
                                detail=f"That doesn't look like a valid database: {e}")
        # Atomic swap, then clear any stale WAL sidecars from the old file.
        os.replace(tmp, dest)
        tmp = None
        for sidecar in ("-wal", "-shm"):
            try:
                os.remove(dest + sidecar)
            except FileNotFoundError:
                pass
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    return RedirectResponse(f"/admin/backup?uploaded={n}", status_code=303)


@app.get("/admin/download-db")
def download_db(request: Request):
    """Download a consistent snapshot of the live database (manual backup)."""
    if not _is_authed(request):
        return _login_redirect(request)
    from starlette.background import BackgroundTask

    tmp = backup.snapshot_to_file(DB_PATH)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=f"library-{stamp}.db",
        background=BackgroundTask(lambda: os.path.exists(tmp) and os.remove(tmp)),
    )


@app.post("/admin/backup-now", response_class=HTMLResponse)
def backup_now_route(request: Request, token: str | None = None):
    _require_api(request, token)
    if not backup.is_configured():
        body = """<div class="page"><h1>Backup not configured</h1>
  <p class="muted">Set <code>DROPBOX_APP_KEY</code>, <code>DROPBOX_APP_SECRET</code>,
  and <code>DROPBOX_REFRESH_TOKEN</code> to enable Dropbox backups.</p></div>"""
        return HTMLResponse(_page("Backup", "", body, authed=True))
    try:
        result = backup.backup_now(DB_PATH)
        msg = f"Uploaded <strong>{result['name']}</strong> ({result['bytes']:,} bytes) to Dropbox."
    except Exception as e:
        msg = f"Backup failed: {e}"
    body = f"""<div class="page"><h1>Backup</h1><p>{msg}</p>
  <p style="margin-top:1rem;"><a href="/archive">Back to the archive →</a></p></div>"""
    return HTMLResponse(_page("Backup", "", body, authed=True))


@app.get("/bookmarklet", response_class=PlainTextResponse)
def bookmarklet(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    token_param = f"?token={SAVE_TOKEN}" if SAVE_TOKEN else ""
    js = (
        "javascript:(function(){"
        "var t=prompt('Tags (comma-separated, optional):');"
        "if(t===null)return;"
        "var u=location.href;"
        f"fetch('{PUBLIC_BASE}/save{token_param}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
        "body:JSON.stringify({url:u,tags:t})}}).then(function(r){alert(r.ok?'Saved to archive':'Error saving');});"
        "})();"
    )
    return js


@app.post("/post")
async def post_draft(request: Request):
    _require_api(request)
    from linklib.social import draft_post, DEFAULT_MODEL
    payload = await request.json()
    model = (payload.get("model") or "").strip() or DEFAULT_MODEL
    lib = _lib()
    try:
        custom_voice = lib.get_setting("voice_prompt")
        d = draft_post(lib, article_id=payload.get("id"), url=payload.get("url"),
                       topic=payload.get("topic"), mode=payload.get("mode", "original"),
                       model=model,
                       system_prompt=custom_voice or None)
        return {"post": d.post}
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# LinkedIn ghostwriter — multi-turn chat with screenshot + URL context
# ---------------------------------------------------------------------------

_DRAFT_BODY = """<div class="chat-wrap">
  <div class="chat-head">
    <div>
      <h1 style="margin:0 0 4px;">LinkedIn ghostwriter</h1>
      <p style="color:var(--muted);margin:0;font-size:15px;">Hand me a topic, a link, or a screenshot of a post. I'll take a swing in your voice, then we iterate.</p>
    </div>
    <button id="clear-btn" class="btn btn-ghost" onclick="clearChat()" style="white-space:nowrap;">New draft</button>
  </div>

  <div id="chat" class="chat-log"></div>

  <div id="dropzone" class="composer">
    <div id="thumbs" class="thumbs"></div>
    <input id="url-in" type="url" class="url-in" placeholder="Optional: paste a URL for context (best-effort — LinkedIn usually can't be fetched)…">
    <div class="compose-row">
      <textarea id="msg-in" rows="2" placeholder="Topic, notes, or feedback…  (Enter to send · Shift+Enter for a newline)"></textarea>
      <div class="compose-actions">
        <label class="attach-btn" title="Attach screenshot">&#128206;<input id="file-in" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden></label>
        <button id="send-btn" class="btn" onclick="send()">Send</button>
      </div>
    </div>
    <div class="hint">Drag, paste, or attach a screenshot of a LinkedIn post to reshare it.</div>
  </div>
</div>

<style>
.chat-wrap{max-width:780px;margin:0 auto;padding:32px 24px 48px;}
.chat-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px;}
.chat-log{display:flex;flex-direction:column;gap:14px;min-height:180px;margin-bottom:20px;}
.empty{color:var(--muted);font-size:15px;text-align:center;padding:40px 0;}
.msg{max-width:88%;border-radius:14px;padding:13px 16px;font-size:15px;line-height:1.6;}
.msg-user{align-self:flex-end;background:var(--accent-light);border:1px solid var(--line);}
.msg-assistant{align-self:flex-start;background:#fff;border:1px solid var(--line);position:relative;}
.msg-text{white-space:pre-wrap;word-wrap:break-word;}
.msg-text + .msg-text{margin-top:8px;}
.msg-img{max-width:220px;border-radius:10px;border:1px solid var(--line);margin:4px 0;display:block;}
.copy{margin-top:10px;font-size:12px;color:var(--accent);background:transparent;border:1px solid var(--line);border-radius:8px;padding:4px 10px;cursor:pointer;}
.copy:hover{background:var(--accent-light);}
.composer{border:1px solid var(--line);border-radius:16px;background:#fff;padding:14px 16px;transition:border-color .12s,background .12s;}
.composer.drag{border-color:var(--accent);background:var(--accent-light);}
.thumbs{display:flex;flex-wrap:wrap;gap:8px;}
.thumbs:not(:empty){margin-bottom:10px;}
.thumb-wrap{position:relative;}
.thumb-wrap img{width:60px;height:60px;object-fit:cover;border-radius:8px;border:1px solid var(--line);}
.thumb-wrap button{position:absolute;top:-7px;right:-7px;width:20px;height:20px;border-radius:50%;border:none;background:var(--ink);color:#fff;font-size:13px;line-height:1;cursor:pointer;}
.url-in{width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:14px;background:var(--bg);margin-bottom:10px;}
.compose-row{display:flex;gap:10px;align-items:flex-end;}
.compose-row textarea{flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--bg);resize:vertical;min-height:46px;}
.compose-actions{display:flex;gap:8px;align-items:center;}
.attach-btn{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;border:1px solid var(--line);border-radius:10px;cursor:pointer;font-size:18px;background:var(--bg);}
.attach-btn:hover{background:var(--accent-light);}
.hint{font-size:12px;color:var(--muted);margin-top:8px;}
nav.site-nav a[href="/draft"]{color:var(--ink);font-weight:600;}
</style>

<script>
var KEY='cfo_draft_history_v1';
var ALLOWED=['image/png','image/jpeg','image/webp','image/gif'];
var history=loadHistory();
var pending=[];   // {media_type, data(base64), dataUrl}

function loadHistory(){try{return JSON.parse(localStorage.getItem(KEY))||[];}catch(e){return [];}}
function persist(){try{localStorage.setItem(KEY,JSON.stringify(history));}catch(e){/* quota — session memory still holds it */}}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function render(){
  var c=document.getElementById('chat');
  if(!history.length){c.innerHTML='<div class="empty">No draft yet. Give me something to work with.</div>';return;}
  c.innerHTML=history.map(function(m){
    var inner='';
    if(typeof m.content==='string'){inner=textBlock(m.content);}
    else{m.content.forEach(function(b){
      if(b.type==='text'){inner+=textBlock(b.text);}
      else if(b.type==='image'){inner+='<img class="msg-img" src="data:'+b.source.media_type+';base64,'+b.source.data+'">';}
    });}
    var copy=m.role==='assistant'?'<button class="copy" onclick="copyText(this)">Copy</button>':'';
    return '<div class="msg msg-'+m.role+'">'+inner+copy+'</div>';
  }).join('');
  c.scrollTop=c.scrollHeight;
}
function textBlock(t){return '<div class="msg-text">'+esc(t).replace(/\\n/g,'<br>')+'</div>';}

function copyText(btn){
  var node=btn.parentNode.querySelector('.msg-text');
  navigator.clipboard.writeText(node?node.innerText:'');
  btn.textContent='Copied';setTimeout(function(){btn.textContent='Copy';},1500);
}

function renderThumbs(){
  var t=document.getElementById('thumbs');
  t.innerHTML=pending.map(function(p,i){
    return '<div class="thumb-wrap"><img src="'+p.dataUrl+'"><button onclick="rmThumb('+i+')" title="Remove">&times;</button></div>';
  }).join('');
}
function rmThumb(i){pending.splice(i,1);renderThumbs();}

function addFile(file){
  if(ALLOWED.indexOf(file.type)<0){alert('Only PNG, JPEG, WebP, or GIF images.');return;}
  if(file.size>5*1024*1024){alert('That image is over 5MB — please shrink it first.');return;}
  var reader=new FileReader();
  reader.onload=function(){
    var dataUrl=reader.result;
    pending.push({media_type:file.type,data:dataUrl.split(',')[1],dataUrl:dataUrl});
    renderThumbs();
  };
  reader.readAsDataURL(file);
}

function showThinking(){
  var c=document.getElementById('chat');
  if(!history.length){c.innerHTML='';}
  var d=document.createElement('div');
  d.id='pending';d.className='msg msg-assistant';
  d.innerHTML='<div class="msg-text"><em style="color:var(--muted);">Drafting in your voice…</em></div>';
  c.appendChild(d);c.scrollTop=c.scrollHeight;
}
function clearThinking(){var p=document.getElementById('pending');if(p)p.remove();}

async function send(){
  var ta=document.getElementById('msg-in');
  var urlEl=document.getElementById('url-in');
  var text=ta.value.trim();
  var url=urlEl.value.trim();
  if(!text && !pending.length && !url){return;}
  if(!text){text='Draft a LinkedIn post based on this.';}
  if(url){text=text+'\\n\\n[URL: '+url+']';}

  var content=[];
  pending.forEach(function(p){content.push({type:'image',source:{type:'base64',media_type:p.media_type,data:p.data}});});
  content.push({type:'text',text:text});

  history.push({role:'user',content:content});
  persist();
  pending=[];renderThumbs();ta.value='';urlEl.value='';
  render();

  var btn=document.getElementById('send-btn');
  btn.disabled=true;btn.textContent='…';
  showThinking();

  try{
    var r=await fetch('/draft/message',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({messages:history,url:url})});
    var d=await r.json();
    clearThinking();
    if(!r.ok){history.push({role:'assistant',content:'\\u26a0\\ufe0f '+(d.detail||'Something went wrong.')});}
    else{
      var reply=d.reply||'';
      if(d.fetch_note){reply='('+d.fetch_note+')\\n\\n'+reply;}
      history.push({role:'assistant',content:reply});
    }
    persist();render();
  }catch(e){
    clearThinking();
    history.push({role:'assistant',content:'\\u26a0\\ufe0f Network error — try again.'});
    render();
  }finally{btn.disabled=false;btn.textContent='Send';}
}

function clearChat(){
  if(history.length && !confirm('Clear this conversation and start a new draft?')){return;}
  history=[];pending=[];persist();renderThumbs();render();
}

document.getElementById('file-in').addEventListener('change',function(e){
  Array.prototype.forEach.call(e.target.files,addFile);e.target.value='';
});
document.getElementById('msg-in').addEventListener('keydown',function(e){
  if(e.key==='Enter' && !e.shiftKey){e.preventDefault();send();}
});
var dz=document.getElementById('dropzone');
['dragover','dragenter'].forEach(function(ev){dz.addEventListener(ev,function(e){e.preventDefault();dz.classList.add('drag');});});
['dragleave','dragend'].forEach(function(ev){dz.addEventListener(ev,function(e){e.preventDefault();dz.classList.remove('drag');});});
dz.addEventListener('drop',function(e){e.preventDefault();dz.classList.remove('drag');if(e.dataTransfer&&e.dataTransfer.files){Array.prototype.forEach.call(e.dataTransfer.files,addFile);}});
document.addEventListener('paste',function(e){
  if(!e.clipboardData)return;
  Array.prototype.forEach.call(e.clipboardData.items,function(it){
    if(it.type.indexOf('image')===0){var f=it.getAsFile();if(f)addFile(f);}
  });
});

render();renderThumbs();
</script>"""


@app.get("/draft", response_class=HTMLResponse)
def draft_page(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    return HTMLResponse(_page("LinkedIn ghostwriter — Brian Weisberg", "Draft", _DRAFT_BODY, authed=True))


def _append_context_to_last_user(messages: list, note: str) -> None:
    """Append a text block carrying fetched URL context to the latest user turn."""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                m["content"] = content + note
            elif isinstance(content, list):
                content.append({"type": "text", "text": note})
            return


@app.post("/draft/message")
async def draft_message(request: Request):
    _require_api(request)
    from linklib.social import chat_draft, CHAT_IMAGE_MEDIA_TYPES
    payload = await request.json()
    messages = payload.get("messages") or []
    url = (payload.get("url") or "").strip()
    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    # Defensively validate any image blocks before forwarding to the API.
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    mt = (block.get("source") or {}).get("media_type")
                    if mt not in CHAT_IMAGE_MEDIA_TYPES:
                        raise HTTPException(status_code=400, detail=f"unsupported image type: {mt}")

    # Best-effort URL fetch — inject as context, or tell the model to ask for a
    # screenshot rather than invent the contents.
    fetch_note = ""
    if url:
        from linklib.extract import fetch_page
        page = fetch_page(url)
        if page.content:
            _append_context_to_last_user(
                messages,
                f"\n\n[Fetched context from {url}"
                + (f" — title: {page.title}" if page.title else "")
                + f"]\n{page.content[:6000]}",
            )
            fetch_note = f"Fetched “{page.title or url}” for context."
        else:
            _append_context_to_last_user(
                messages,
                f"\n\n[Note: the URL {url} could not be fetched server-side "
                "(likely blocked or login-walled). Do not invent its contents — "
                "ask for a screenshot instead.]",
            )
            fetch_note = "Couldn't fetch that URL — paste a screenshot and I'll read it."

    reply = chat_draft(messages)
    return {"reply": reply, "fetch_note": fetch_note}


@app.post("/feed/save")
async def feed_save(request: Request, background_tasks: BackgroundTasks):
    """Server-side save proxy — authed via login cookie, no token in client HTML."""
    _require_api(request)
    form = await request.form()
    url = (form.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    tags_raw = form.get("tags") or ""
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    lib = _lib()
    try:
        ingest_url(lib, url, tags=tags)
        background_tasks.add_task(backup.maybe_backup, DB_PATH)
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        lib.close()


@app.post("/feed/read-later")
async def feed_toggle_read_later(request: Request):
    """Add or remove a feed item from the read-later list."""
    _require_api(request)
    form = await request.form()
    url = (form.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    action = (form.get("action") or "add").strip()
    lib = _lib()
    try:
        if action == "remove":
            lib.remove_read_later(url)
        else:
            lib.add_read_later(
                url=url,
                title=(form.get("title") or "").strip(),
                source=(form.get("source") or "").strip(),
                summary=(form.get("summary") or "").strip(),
                published_at=(form.get("published_at") or None),
            )
        return JSONResponse({"ok": True, "action": action})
    finally:
        lib.close()


@app.post("/feed/filters")
async def feed_update_filters(request: Request):
    """Persist custom topic keyword filters (stored in settings table)."""
    _require_api(request)
    import json as _json
    payload = await request.json()
    filters = [str(f).strip() for f in payload.get("filters", []) if str(f).strip()]
    lib = _lib()
    try:
        lib.set_setting("feed_filter_tags", _json.dumps(filters))
        return JSONResponse({"ok": True, "filters": filters})
    finally:
        lib.close()


@app.post("/library/{article_id}/tags")
async def library_update_tags(request: Request, article_id: int):
    """Update tags on a saved article. Accepts JSON or form body."""
    _require_api(request)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        tags_raw = payload.get("tags", [])
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        form = await request.form()
        tags_str = form.get("tags") or ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    lib = _lib()
    try:
        lib.update_tags(article_id, tags)
        return JSONResponse({"ok": True, "tags": sorted(set(tags))})
    finally:
        lib.close()


@app.post("/library/{article_id}/delete")
def library_delete(request: Request, article_id: int):
    """Permanently delete a saved article."""
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    lib = _lib()
    try:
        lib.delete_article(article_id)
    finally:
        lib.close()
    return RedirectResponse("/archive", status_code=303)


@app.get("/static/{filename}")
def static_file(filename: str):
    # basename strips any path components so "../" can't escape the static dir
    safe = os.path.basename(filename)
    path = os.path.join(_STATIC_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    ext = filename.rsplit(".", 1)[-1].lower()
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
             "ico": "image/x-icon"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/favicon.ico")
def favicon():
    # Browsers auto-request /favicon.ico; serve the brand icon so no tab shows
    # the default globe even when <link> tags are ignored.
    path = os.path.join(_STATIC_DIR, "favicon.ico")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/x-icon")
