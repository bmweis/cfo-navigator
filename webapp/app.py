#!/usr/bin/env python3
"""bmweis.com — public site + private CFO Navigator tools.

Public routes (no auth):
    GET  /                     Bio homepage
    GET  /thought-leadership   Podcasts, writing, interviews
    GET  /growth-engine-ratio  GER framework + calculator
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
    POST /feed/save            Save a feed item to the library
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
BENCHMARKS = [
    {
        "name": "ICONIQ Growth",
        "url": "https://iconiqcapital.com/growth/",
        "description": "ICONIQ's annual State of SaaS report. Top-tier portfolio, so keep that in mind when comparing—but the data and analysis are excellent.",
        "coverage": "Private",
    },
    {
        "name": "ICONIQ Compass",
        "url": "https://iconiqcapital.com/growth/compass/",
        "description": "Their interactive benchmarking tool. Lets you slice the data by ARR range, growth rate, and other filters so you're actually comparing against something relevant.",
        "coverage": "Private",
    },
    {
        "name": "HighAlpha (formerly OpenView)",
        "url": "https://highalpha.com/resources/",
        "description": "Took over OpenView's annual SaaS benchmarks report. NRR, GRR, CAC payback, and the usual suspects for private SaaS companies.",
        "coverage": "Private",
    },
    {
        "name": "Benchmarkit",
        "url": "https://benchmarkit.solutions/",
        "description": "Ray Rike's interactive benchmarking tool. Better segmentation than most—you can control who you're comparing against, which is the whole point.",
        "coverage": "Private",
    },
    {
        "name": "OpexEngine",
        "url": "https://www.opexengine.com/saas-financial-benchmarks/",
        "description": "Private and public SaaS benchmarks across Rule of 40, unit economics, and operating metrics. One of the more comprehensive data sets out there.",
        "coverage": "Both",
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
        "name": "Meritech Capital",
        "url": "https://www.meritechcapital.com/benchmarking",
        "description": "Interactive public cloud benchmarks—growth, efficiency, and valuation multiples, updated in real time. Great for understanding where public comps are trading.",
        "coverage": "Public",
    },
    {
        "name": "PublicComps",
        "url": "https://www.publiccomps.com",
        "description": "Public SaaS comps and operating metrics. Good filters by category and scale.",
        "coverage": "Public",
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


# --- Session cookie helpers (stdlib HMAC — no extra dependency) --------------

def _sign(value: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _make_session() -> str:
    """A signed cookie value that expires SESSION_TTL seconds from now."""
    return _sign(str(int(time.time()) + SESSION_TTL))


def _valid_session(cookie: str | None) -> bool:
    if not cookie or "." not in cookie:
        return False
    value, _, sig = cookie.rpartition(".")
    expected = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(value) > int(time.time())
    except ValueError:
        return False


def _is_authed(request: Request) -> bool:
    """True if the request carries a valid login session (or no password set)."""
    if not AUTH_PASSWORD:
        return True
    return _valid_session(request.cookies.get(COOKIE_NAME))


def _login_redirect(request: Request) -> RedirectResponse:
    nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)


def _check_token(token: str | None) -> None:
    """Constant-time token check for the bookmarklet / programmatic save."""
    if SAVE_TOKEN and not (token and hmac.compare_digest(token, SAVE_TOKEN)):
        raise HTTPException(status_code=401, detail="bad or missing save token")


def _require_api(request: Request, token: str | None = None) -> None:
    """Allow API access via a valid login cookie OR a valid token header/param."""
    if _is_authed(request):
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

def _page(title: str, active: str, body: str, authed: bool = False) -> str:
    public = [("/", "About"), ("/thought-leadership", "Thought Leadership"),
              ("/tools", "CFO Toolbox"), ("/contact", "Contact")]
    private = [("/library", "Library"), ("/feed", "Feed"), ("/ask", "Ask")]

    def links(items):
        return "".join(
            f'<a href="{href}" class="{"active" if active == label else ""}">{label}</a>'
            for href, label in items
        )

    nav = links(public) + '<span class="sep"></span>' + links(private)
    if authed:
        nav += f'<a href="/admin" class="{"active" if active == "Admin" else ""}">Admin</a>'
        nav += '<a href="/logout">Log out</a>'

    star = ('<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">'
            '<path d="M8 0 L9.4 6.6 L16 8 L9.4 9.4 L8 16 L6.6 9.4 L0 8 L6.6 6.6 Z" fill="#002975"/></svg>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
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
</footer>
</body></html>"""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/library", error: str = ""):
    if _is_authed(request):
        return RedirectResponse(next or "/library", status_code=303)
    err = ('<p style="color:#b91c1c;font-size:14px;margin:0 0 16px;">Incorrect password — try again.</p>'
           if error else "")
    body = f"""<div class="page" style="max-width:420px;">
<h1>Sign in</h1>
<p style="color:var(--muted);margin:4px 0 28px;">This area is private. Enter the password to continue.</p>
{err}
<form method="post" action="/login" style="display:grid;gap:16px;">
  <input type="hidden" name="next" value="{_esc(next or '/library')}">
  <input name="password" type="password" required autofocus placeholder="Password"
         style="width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  <button type="submit" class="btn">Sign in</button>
</form>
</div>"""
    return HTMLResponse(_page("Sign in—Brian Weisberg", "", body))


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = form.get("password") or ""
    nxt = form.get("next") or "/library"
    if not nxt.startswith("/"):  # never redirect off-site
        nxt = "/library"
    if AUTH_PASSWORD and hmac.compare_digest(password, AUTH_PASSWORD):
        resp = RedirectResponse(nxt, status_code=303)
        secure = (request.url.scheme == "https"
                  or request.headers.get("x-forwarded-proto") == "https")
        resp.set_cookie(COOKIE_NAME, _make_session(), max_age=SESSION_TTL,
                        httponly=True, samesite="lax", secure=secure, path="/")
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

@app.get("/", response_class=HTMLResponse)
def homepage():
    # Show the photo if it's present; otherwise a clean monogram. This way the
    # headshot displays automatically the moment headshot.jpg lands in static/.
    if os.path.isfile(os.path.join(_STATIC_DIR, "headshot.jpg")):
        avatar = ('<img src="/static/headshot.jpg" alt="Brian Weisberg" '
                  'style="width:140px;height:140px;border-radius:50%;object-fit:cover;'
                  'object-position:center top;flex-shrink:0;border:3px solid var(--navy);">')
    else:
        avatar = ('<div aria-label="Brian Weisberg" '
                  'style="width:140px;height:140px;border-radius:50%;flex-shrink:0;border:3px solid var(--navy);'
                  'background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;'
                  'font-size:46px;font-weight:700;letter-spacing:-0.02em;">BW</div>')
    body = f"""<div class="page">
<div style="display:flex;align-items:flex-start;gap:32px;flex-wrap:wrap;margin-bottom:28px;">
  {avatar}
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

<div style="display:flex;gap:12px;margin-top:32px;flex-wrap:wrap;">
  <a href="/thought-leadership" class="btn">Thought Leadership</a>
  <a href="/contact" class="btn btn-ghost">Get in Touch</a>
  <a href="https://linkedin.com/in/bmw-cfo" target="_blank" rel="noopener" class="btn btn-ghost">LinkedIn</a>
  <a href="/community" class="btn btn-ghost">CFO Community &rarr;</a>
</div>
</div>"""
    return HTMLResponse(_page("Brian Weisberg—CFO", "About", body))


@app.get("/thought-leadership", response_class=HTMLResponse)
def thought_leadership():
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

    body = '<div class="page"><h1>Thought Leadership</h1>' + \
        '<p style="color:var(--muted);margin:4px 0 28px;">Podcasts, writing, interviews, and appearances.</p>' + \
        """<a href="/growth-engine-ratio" style="display:block;text-decoration:none;background:var(--accent);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:36px;">
  <div style="display:flex;align-items:center;gap:9px;margin-bottom:9px;">
    <span style="background:var(--coral);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;border-radius:5px;padding:2px 8px;">New</span>
    <span style="font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--seafoam);">Featured Framework</span>
  </div>
  <div style="font-size:20px;font-weight:700;letter-spacing:-.02em;margin-bottom:6px;">The Growth Engine Ratio</div>
  <div style="font-size:14px;opacity:.85;line-height:1.5;">A new metric for measuring how R&amp;D and GTM investments work together to drive growth&mdash;with an interactive calculator to see how you stack up. Published with The F Suite &rarr;</div>
</a>"""

    body += section("Events Hosted", [
        ("Abacum AI Summit · Abacum · Apr 2026",
         "https://www.abacum.ai/summit-post", "2026-04"),
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
        ("The F Suite Boston — Private Dinner & Guided Discussion · The F Suite · Apr 2024",
         "", "2024-04"),
        ("The F Suite Boston — CFO Dinner · The F Suite · Dec 2023",
         "", "2023-12"),
        ("The F Suite — NC Launch Dinner · The F Suite · Jun 2023",
         "", "2023-06"),
        ("Teampay Agile Finance Summit · Teampay · Oct 2021",
         "https://www.accelevents.com/e/agile-finance-summit-2021", "2021-10"),
    ])

    body += section("Podcast—Host: The Cash Flow Show", [
        ("The Cash Flow Show — Conversations about how tech companies make money (full episode feed) · OnlyCFO",
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
    ])

    body += section("Podcasts—Guest", [
        ("Code to Cash, Ep. 9 — Monetizing Thoughtfully: Architecting Financial Stacks · Monetizely · Sep 2023",
         "https://creators.spotify.com/pod/profile/codetocash/episodes/Episode-9-Monetizing-Thoughtfully--Architecting-Financial-Stacks-with-Brian-Weisberg--CFO-of-Tidelift-e28unsn",
         "2023-09"),
        ("OpexEngine — SaaS Conversations: Dynamic Planning for SaaS Finance Leaders · OpexEngine · May 2023",
         "https://www.opexengine.com/webinar/opexengine-saas-conversations-dynamic-planning-for-saas-finance-leaders",
         "2023-05"),
        ("Role Forward Podcast — The Heuristics of Forecasting · Mosaic Tech · Dec 2022",
         "https://www.youtube.com/watch?v=mqVvcVVTSrk", "2022-12"),
        ("Role Forward Podcast — Collaborative Budgeting · Mosaic Tech · Apr 2022",
         "https://www.youtube.com/watch?v=GPdRstJ_sKw", "2022-04"),
    ])

    body += section("Webinar—Host", [
        ("Numeric — Lean Accounting Team · Numeric · Mar 2024",
         "https://numeric.lpages.co/lean-accounting-team-webinar/", "2024-03"),
    ])

    body += section("Interview", [
        ("Sequence — From $1M to $100M: 6 Finance Lessons from the Frontline · Sequence · Jul 2025",
         "https://www.sequencehq.com/blog/from-1m-to-100m-6-finance-lessons-from-the-frontline", "2025-07"),
    ])

    body += section("Authored", [
        ("The Growth Engine Ratio: Accounting for the Missing Half of Your Efficiency Equation · The F Suite · Jun 2026",
         "/growth-engine-ratio", "2026-06"),
        ("The F Suite — Exit Readiness for CFOs · The F Suite · Mar 2026",
         "https://www.fsuite.co/blog/exit-readiness-cfos", "2026-03"),
        ("OnlyCFO — Building Dashboards That Matter · OnlyCFO · Apr 2024",
         "https://www.onlycfo.io/p/building-dashboards-that-matter", "2024-04"),
    ])

    body += section("Cited & Quoted", [
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

    body += """<div style="margin-top:48px;padding:24px 28px;background:var(--navy);border-radius:16px;">
  <div style="font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--seafoam);margin-bottom:10px;">CFO Community</div>
  <p style="font-size:17px;font-weight:600;color:#fff;margin:0 0 8px;letter-spacing:-0.01em;line-height:1.35;">Building something better for CFO peers.</p>
  <p style="font-size:14px;color:rgba(255,255,255,.78);margin:0 0 18px;line-height:1.6;">I&rsquo;ve spent years inside finance communities&mdash;as a founding member and GM of The F Suite. I know what they get right and where even the best ones fall short. I&rsquo;m working on something new. If you have thoughts on what a well-designed community for CFO peers would look like, I&rsquo;d love to hear from you.</p>
  <a href="/community" class="btn" style="background:#fff;color:var(--navy);border-color:#fff;font-size:14px;padding:10px 22px;">Share your experience &rarr;</a>
</div>"""
    body += "</div>"
    return HTMLResponse(_page("Thought Leadership—Brian Weisberg", "Thought Leadership", body))


@app.get("/growth-engine-ratio", response_class=HTMLResponse)
def growth_engine_ratio():
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

  var s = '<svg class="ger-contrib" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';
  s += '<defs>' +
    '<linearGradient id="cgB" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + BLUE + '" stop-opacity="0.30"/><stop offset="1" stop-color="' + BLUE + '" stop-opacity="0.08"/></linearGradient>' +
    '<linearGradient id="cgG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + GREEN + '"/><stop offset="1" stop-color="' + GREEN + '" stop-opacity="0.6"/></linearGradient>' +
    '<linearGradient id="cgR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + RED + '" stop-opacity="0.30"/><stop offset="1" stop-color="' + RED + '" stop-opacity="0.08"/></linearGradient>' +
    '</defs>';

  s += '<text x="' + (W / 2) + '" y="30" text-anchor="middle" font-size="17" font-weight="700" fill="#1a1a1a">How ' + labels[cur] + ' is built &#8212; Time-Distributed Contribution</text>';
  s += '<text x="' + (W / 2) + '" y="52" text-anchor="middle" font-size="13" fill="' + MUT + '">25% of every quarter of spend feeds the window &#183; Efficiency Ratio = $' + ratio.toFixed(2) + '</text>';

  s += '<text x="20" y="186" font-size="13" font-weight="700" fill="' + BLUE + '">GTM</text>';
  s += '<text x="20" y="320" font-size="13" font-weight="700" fill="' + GREEN + '">Revenue</text>';
  s += '<text x="20" y="498" font-size="13" font-weight="700" fill="' + RED + '">R&amp;D</text>';

  // One shared scale across spend AND revenue so every box is comparable by $;
  // each is bottom-aligned on its lane baseline with a 25% "pull" cap on top.
  var allMax = Math.max(g[0], g[1], g[2], g[3], r[0], r[1], revC, revP, 1), BARMAX = 142;
  function barH(val) { return Math.max(val / allMax * BARMAX, 3); }
  function spendBar(c, baseY, val, col, grad) {
    var h = barH(val), x = cx(c) - bw / 2, y = baseY - h, capH = h * 0.25;
    var o = '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="6" fill="url(#' + grad + ')" stroke="' + col + '" stroke-opacity="0.45"/>';
    o += '<path d="M' + x.toFixed(1) + ' ' + (y + capH).toFixed(1) + ' L' + x.toFixed(1) + ' ' + (y + 6).toFixed(1) + ' Q' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + 6).toFixed(1) + ' ' + y.toFixed(1) + ' L' + (x + bw - 6).toFixed(1) + ' ' + y.toFixed(1) + ' Q' + (x + bw).toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + bw).toFixed(1) + ' ' + (y + 6).toFixed(1) + ' L' + (x + bw).toFixed(1) + ' ' + (y + capH).toFixed(1) + ' Z" fill="' + col + '"/>';
    if (h >= 8) o += '<text x="' + cx(c).toFixed(1) + '" y="' + (y - 5).toFixed(1) + '" text-anchor="middle" font-size="10" font-weight="700" fill="' + col + '">25%</text>';
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
  for (var c = 1; c <= 4; c++) s += spendBar(c, gB, g[c - 1], BLUE, 'cgB');

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
  s += '<text x="' + (gx + cw / 2).toFixed(1) + '" y="' + (gy + 18).toFixed(1) + '" text-anchor="middle" font-size="14" font-weight="700" fill="' + GREEN + '">' + (ann >= 0 ? '+' : '') + fmtM(ann) + '</text>';
  s += '<text x="' + (gx + cw / 2).toFixed(1) + '" y="' + (gy + 32).toFixed(1) + '" text-anchor="middle" font-size="9.5" fill="' + GREEN + '">Annualized Growth</text>';

  // R&D lane bottom-aligned at dB (same scale)
  var dB = 494, dTop = dB - barH(Math.max(r[0], r[1]));
  s += bracket(0, 1, dTop - 22, RED, 'R&amp;D Investment = ' + fmtM(rndInv));
  for (var c = 0; c <= 1; c++) s += spendBar(c, dB, r[c], RED, 'cgR');

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
  var pill = '<tspan fill="' + GREEN + '" font-weight="700">' + (ann >= 0 ? '+' : '') + fmtM(ann) + '</tspan> &#247; ( ' +
    '<tspan fill="' + BLUE + '" font-weight="700">' + fmtM(gtmInv) + '</tspan> + ' +
    '<tspan fill="' + RED + '" font-weight="700">' + fmtM(rndInv) + '</tspan> ) = ' +
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
    return HTMLResponse(_page("The Growth Engine Ratio—Brian Weisberg", "Thought Leadership", body))


@app.get("/contact", response_class=HTMLResponse)
def contact_page(submitted: str = ""):
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks for reaching out.</h1>
<p>I'll get back to you shortly.</p>
<a href="/" class="btn btn-ghost" style="margin-top:8px;">Back to home</a>
</div>"""
        return HTMLResponse(_page("Contact—Brian Weisberg", "Contact", body))

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
    return HTMLResponse(_page("Contact—Brian Weisberg", "Contact", body))


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
# Community
# ---------------------------------------------------------------------------

# TODO: Replace this placeholder with the real Google Form URL once created in
# Google Workspace. Create the form with: Name, Email, Current/past communities,
# Gaps in community experiences, What you'd look for in an ideal community (multi-select).
_COMMUNITY_FORM_URL = "#community-form-coming-soon"
_COMMUNITY_FORM_CONFIGURED = _COMMUNITY_FORM_URL != "#community-form-coming-soon"


@app.get("/community", response_class=HTMLResponse)
def community_page():
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
    return HTMLResponse(_page("CFO Community—Brian Weisberg", "", body))


@app.get("/tools", response_class=HTMLResponse)
def tools_directory(request: Request):
    authed = _is_authed(request)
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

    bench_cards = "".join(
        f'<a class="bench-card" href="{_esc(b["url"])}" target="_blank" rel="noopener">'
        f'<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px;">'
        f'<span class="bench-name">{_esc(b["name"])}</span>'
        f'<span class="bench-badge" style="{_bench_badge_style(b["coverage"])}">{_esc(b["coverage"])}</span>'
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
<a href="/tools/submit" style="margin-left:12px;font-size:14px;font-weight:500;">+ Submit a tool</a></p>

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

<div style="margin-top:56px;padding-top:40px;border-top:1px solid var(--line);">
  <h2 style="font-size:20px;font-weight:700;margin:0 0 6px;">Benchmarking Resources</h2>
  <p style="color:var(--muted);font-size:14px;margin:0 0 12px;">The benchmarking sources I actually use.</p>
  <p style="font-size:13px;color:var(--muted);margin:0 0 24px;">Worth reading first: <a href="https://www.onlycfo.io/p/benchmarking-is-bad" target="_blank" rel="noopener" style="color:var(--accent);font-weight:500;">Benchmarking is Bad</a>&mdash;it&rsquo;s not always what you think it is.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;">
    {bench_cards}
  </div>
</div>

<div style="margin-top:40px;padding-top:28px;border-top:1px solid var(--line);">
  <p style="font-size:13px;color:var(--muted);margin-bottom:16px;">&#9733; Formal advisor to these companies.</p>
  <p style="font-size:15px;color:var(--muted);">Know a tool that belongs here?
    <a href="/tools/submit" style="font-weight:500;">Submit it for review →</a></p>
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
    var introBtn = '<button class="tool-intro-btn" onclick="openIntroModal(' + t.id + ',\'' + esc(t.name).replace(/'/g,"\\'") + '\')">'
      + '&#10024; Warm Intro</button>';
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
    return HTMLResponse(_page("CFO Toolbox—Brian Weisberg", "CFO Toolbox", body, authed=authed))


def _tool_category_checkboxes(selected: list[str] | None = None) -> str:
    selected = selected or []
    return "".join(
        f'<label style="display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;">'
        f'<input type="checkbox" name="categories" value="{_esc(c)}"'
        f'{" checked" if c in selected else ""}> {_esc(c)}</label>'
        for c in TOOL_CATEGORIES
    )


@app.get("/tools/submit", response_class=HTMLResponse)
def tools_submit_page(submitted: str = ""):
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks—submission received.</h1>
<p>Your tool has been submitted for review. If approved, it'll appear in the CFO Toolbox shortly.</p>
<a href="/tools" class="btn btn-ghost" style="margin-top:8px;">Back to CFO Toolbox</a>
</div>"""
        return HTMLResponse(_page("Submission received—CFO Toolbox", "CFO Toolbox", body))

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
    return HTMLResponse(_page("Submit a Tool—CFO Toolbox", "CFO Toolbox", body))


@app.post("/tools/submit")
async def tools_submit(request: Request):
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
    if not _is_authed(request):
        return _login_redirect(request)

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
            return HTMLResponse(_page("CFO Feed — Brian Weisberg", "Feed",
                f'<div class="page"><h2>Feed unavailable</h2><p style="color:var(--muted);">Could not load feeds: {_esc(str(e))}</p></div>',
                authed=True))

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
        read_btn = ('' if paywalled
                    else f'<a href="/read?url={quote(url, safe="")}" class="faction">&#9654; Read</a>')
        is_rl_mode = item.get("_rl_mode", False)
        is_rl = is_rl_mode or (url in rl_urls)
        if is_rl_mode:
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

    return HTMLResponse(_page("CFO Feed—Brian Weisberg", "Feed", body, authed=True))


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
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib.extract import fetch_page
    import html as html_mod

    back_url = "/library"
    back_label = "Library"

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
    <a href="/library">Library</a> or <a href="/feed">Feed</a>.</p>
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
  if (!confirm('Permanently delete this article from your library?')) return;
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


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, q: str = ""):
    authed = _is_authed(request)
    if not authed:
        return _login_redirect(request)
    lib = _lib()
    try:
        results = lib.search(q, limit=100)
        total = lib.count()
        tags = lib.all_tags()[:25]
    finally:
        lib.close()

    def _card(r):
        tags_csv = _esc(",".join(r.get("tags", [])))
        tag_spans = "".join(
            f'<span class="tag-chip" data-tag="{_esc(t)}">{_esc(t)}'
            f' <button class="tag-x-btn" data-article-id="{r["id"]}" data-tag="{_esc(t)}"'
            f' onclick="quickRemoveTagBtn(this)">&times;</button></span>'
            for t in r.get("tags", [])
        )
        return f"""<article class="card" id="card-{r['id']}">
          <a class="card-title" href="{r['url']}" target="_blank" rel="noopener">{_esc(r['title'])}</a>
          <div class="meta">{_esc(r.get('source',''))}{' &middot; ' + _esc(r['saved_at'][:10]) if r.get('saved_at') else ''}</div>
          <p class="summary">{_esc(r.get('summary',''))[:280]}</p>
          <div class="tags" id="tags-{r['id']}" data-tags="{tags_csv}">{tag_spans}</div>
          <div id="tag-editor-{r['id']}" style="display:none;margin-top:8px;">
            <input type="text" id="tag-input-{r['id']}" value="{tags_csv}"
              placeholder="comma-separated tags"
              style="width:100%;padding:6px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:13px;background:#fff;">
            <div style="display:flex;gap:6px;margin-top:6px;">
              <button class="postbtn" onclick="saveTags({r['id']})">Save</button>
              <button class="postbtn" onclick="cancelTags({r['id']})">Cancel</button>
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
            <a href="/read?id={r['id']}" class="postbtn" style="text-decoration:none;">Read</a>
            <button class="postbtn" onclick="openTagEditor({r['id']})">Edit tags</button>
            <form method="post" action="/library/{r['id']}/delete" style="display:inline;"
                  onsubmit="return confirm('Permanently delete this article?');">
              <button type="submit" class="postbtn" style="color:#b91c1c;">Delete</button>
            </form>
          </div>
        </article>"""

    cards = "".join(_card(r) for r in results) or '<p style="color:var(--muted);">No matches.</p>'

    tagbar = "".join(
        f'<a href="/library?q={_esc(t)}">{_esc(t)} <em>{c}</em></a>' for t, c in tags
    )

    page_body = f"""<div style="border-bottom:1px solid var(--line);padding:20px 24px;">
  <div style="max-width:780px;margin:0 auto;">
    <div style="font-size:13px;color:var(--muted);margin-bottom:10px;display:flex;align-items:center;gap:16px;">
      <span>{total} saved</span>
      <a href="/read" style="color:var(--accent);font-weight:500;">&#9654; Article Reader</a>
    </div>
    <form method="get" action="/library" style="display:flex;gap:8px;max-width:680px;">
      <input type="search" name="q" value="{_esc(q)}" placeholder="Search titles, summaries, notes, tags…"
             style="flex:1;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font-size:15px;background:#fff;" autofocus>
      <button type="submit" class="btn">Search</button>
    </form>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;">{tagbar}</div>
    <div style="display:flex;gap:8px;margin-top:14px;max-width:680px;">
      <textarea id="askq" rows="2" placeholder="Ask your library an FP&amp;A question…"
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
nav.site-nav a[href="/library"]{{color:var(--ink);font-weight:600;}}
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

    return HTMLResponse(_page("Library—Brian Weisberg", "Library", page_body, authed=authed))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/search")
def api_search(request: Request, q: str = "", limit: int = 50, token: str | None = None):
    _require_api(request, token)
    lib = _lib()
    try:
        return {"query": q, "results": lib.search(q, limit=limit)}
    finally:
        lib.close()


@app.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request, q: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)

    from linklib.agent import EFFORT_SETTINGS, COST_ESTIMATES, MODEL_ALIASES

    # Build model radio rows
    models = [
        ("claude-haiku-4-5-20251001", "Fast &middot; cost-effective"),
        ("claude-sonnet-4-6",         "Balanced &middot; default"),
        ("claude-opus-4-8",           "Best quality"),
    ]
    default_model = "claude-sonnet-4-6"

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
        ("quick",    "Quick",    "4 library &middot; 2 web searches &middot; ~700 tokens out"),
        ("standard", "Standard", "8 library &middot; 4 web searches &middot; ~1,500 tokens out"),
        ("deep",     "Deep",     "16 library &middot; 6 web searches &middot; ~2,500 tokens out"),
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

    effort_rows = "".join(effort_row(v, l, d, v == "standard") for v, l, d in effort_details)

    # Bake cost table into JS as a JSON-like literal
    import json as _json
    cost_js = _json.dumps(COST_ESTIMATES)

    pre_q = _esc(q)

    body = f"""<div class="page" style="max-width:820px;">
<h1 style="margin-bottom:6px;">Ask a question</h1>
<p style="color:var(--muted);margin:0 0 28px;">Query your saved library, RSS feed, and trusted web sources. Tune cost vs. depth before each query.</p>

<div class="ask-card">
  <label style="display:block;font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">Question</label>
  <textarea id="ask-q" rows="3" autofocus placeholder="e.g. What frameworks do CFOs use for headcount planning in uncertain environments?"
    style="width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:var(--bg);resize:vertical;">{pre_q}</textarea>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin:16px 0;">

  <div class="ask-card">
    <div class="ask-section-label">Sources</div>
    <div style="display:flex;flex-direction:column;gap:8px;">
      <label class="ask-check-label"><input type="checkbox" id="src-library" checked onchange="updateEstimate()"> My saved library</label>
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
  <span id="cost-est" style="font-size:13px;color:var(--muted);"></span>
</div>

<div id="ask-result" style="display:none;"></div>
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

async function doAsk() {{
  var q = document.getElementById('ask-q').value.trim();
  if (!q) {{ document.getElementById('ask-q').focus(); return; }}

  var model = document.querySelector('input[name="model"]:checked')?.value || 'claude-sonnet-4-6';
  var effort = document.querySelector('input[name="effort"]:checked')?.value || 'standard';
  var sources = [];
  if (document.getElementById('src-library').checked) sources.push('library');
  if (document.getElementById('src-feed').checked) sources.push('feed');
  if (document.getElementById('src-web').checked) sources.push('web');
  if (!sources.length) {{ alert('Select at least one source.'); return; }}

  var btn = document.getElementById('ask-btn');
  var box = document.getElementById('ask-result');
  btn.disabled = true; btn.textContent = 'Thinking…';
  box.style.display = 'block';
  box.innerHTML = '<div class="ask-answer"><em style="color:var(--muted);">Querying sources…</em></div>';

  try {{
    var resp = await fetch('/ask', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ question: q, model: model, effort: effort, sources: sources }})
    }});
    var d = await resp.json();
    if (!resp.ok) {{ box.innerHTML = '<div class="ask-answer" style="color:#b91c1c;">' + (d.detail || 'Error') + '</div>'; return; }}

    var answerHtml = '<p>' + (d.answer || '').replace(/\\n\\n/g, '</p><p>').replace(/\\n/g, '<br>') + '</p>';

    var srcItems = [];
    (d.sources || []).forEach(function(s, i) {{
      srcItems.push('<li>&#128218; <a href="' + s.url + '" target="_blank" rel="noopener">[' + (i+1) + '] ' + s.title + '</a></li>');
    }});
    var feedOffset = (d.sources || []).length;
    (d.feed_sources || []).forEach(function(s, i) {{
      srcItems.push('<li>&#128240; <a href="' + s.url + '" target="_blank" rel="noopener">[' + (feedOffset+i+1) + '] ' + s.title + '</a></li>');
    }});
    (d.web_sources || []).forEach(function(s) {{
      srcItems.push('<li>&#127760; <a href="' + s.url + '" target="_blank" rel="noopener">' + s.title + '</a></li>');
    }});

    box.innerHTML = '<div class="ask-answer">' + answerHtml +
      (srcItems.length ? '<ul class="ask-src-list">' + srcItems.join('') + '</ul>' : '') +
      '</div>';
  }} catch(e) {{
    box.innerHTML = '<div class="ask-answer" style="color:#b91c1c;">Something went wrong: ' + e + '</div>';
  }} finally {{
    btn.disabled = false; btn.textContent = 'Ask';
  }}
}}

document.addEventListener('keydown', function(e) {{
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') doAsk();
}});

updateEstimate();
</script>"""

    return HTMLResponse(_page("Ask—Brian Weisberg", "Ask", body, authed=True))


@app.post("/ask")
async def ask(request: Request):
    _require_api(request)
    from linklib.agent import answer_question, MODEL_ALIASES
    payload = await request.json()
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question required")

    model = (payload.get("model") or "")
    effort = (payload.get("effort") or "standard")

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
        )
        return {
            "answer": ans.text,
            "sources":      [{"title": s["title"], "url": s["url"]} for s in ans.sources],
            "feed_sources": [{"title": s["title"], "url": s["url"]} for s in ans.feed_sources],
            "web_sources":  ans.web_sources,
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


# Admin sections — the hub lists these; each links to its own page.
_ADMIN_SECTIONS = [
    ("/admin/social",       "Social",              "Draft LinkedIn posts in your voice."),
    ("/admin/backup",       "Library backup",      "Download a snapshot or upload a replacement database."),
    ("/admin/brand",        "Brand standards",     "Visual standards, color system, and your writing voice."),
    ("/admin/contacts",     "Contact submissions", "Messages from the public contact form."),
    ("/admin/tools",        "Tool submissions",    "Review the CFO Toolbox approval queue and manage featured/vendor settings."),
    ("/admin/tools/leads",  "Tool leads",          "Warm Intro requests — name, email, company, and size for each tool."),
]


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    cards = "".join(
        f'<a href="{href}" style="display:block;background:var(--surface);border:1px solid var(--line);'
        f'border-radius:14px;padding:20px 22px;text-decoration:none;">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
        f'<span style="font-family:var(--font-head);font-weight:600;font-size:17px;color:var(--navy);letter-spacing:-0.01em;">{title}</span>'
        f'<span style="color:var(--navy);font-size:18px;line-height:1;">&rarr;</span></div>'
        f'<p style="margin:6px 0 0;font-size:14px;color:var(--muted);line-height:1.5;">{desc}</p></a>'
        for href, title, desc in _ADMIN_SECTIONS
    )
    body = f"""<div class="page" style="max-width:720px;">
<h1>Admin</h1>
<p style="color:var(--muted);margin:4px 0 30px;">Manage the site&rsquo;s private tools.</p>
<div style="display:grid;gap:14px;">{cards}</div>
</div>"""
    return HTMLResponse(_page("Admin — Brian Weisberg", "Admin", body, authed=True))


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
      <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px;">Or topic <span style="font-weight:400;text-transform:none;letter-spacing:0;">(used when URL is blank; pulls from your library)</span></label>
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
<p style="margin:0 0 4px;"><a href="/admin" style="font-size:13px;color:var(--muted);">&larr; Admin</a></p>
<h1>Library backup</h1>
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
    return HTMLResponse(_page("Library backup — Admin", "Admin", body, authed=True))


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
                                detail=f"That doesn't look like a library database: {e}")
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
  <p style="margin-top:1rem;"><a href="/library">Back to the library →</a></p></div>"""
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
        "body:JSON.stringify({url:u,tags:t})}}).then(function(r){alert(r.ok?'Saved to library':'Error saving');});"
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
    return RedirectResponse("/library", status_code=303)


@app.get("/static/{filename}")
def static_file(filename: str):
    # basename strips any path components so "../" can't escape the static dir
    safe = os.path.basename(filename)
    path = os.path.join(_STATIC_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    ext = filename.rsplit(".", 1)[-1].lower()
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media)
