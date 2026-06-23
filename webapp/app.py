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
    "Financial Planning",
    "Cap Table Management",
    "Headcount Planning",
    "Spend Management",
    "Financial Close",
    "Billing",
    "Sales Tax",
    "Commission Calculations",
    "Contract Management",
    "Procurement",
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
    """Seed the tools directory on first boot if the table is empty."""
    from scripts.seed_tools import TOOLS
    lib = _lib()
    try:
        if lib.conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0] == 0:
            for t in TOOLS:
                lib.add_tool(t["name"], t["description"], t["url"], t["categories"], approved=1)
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
:root{--ink:#16130f;--muted:#6b6258;--line:#e6e0d6;--bg:#faf7f2;--accent:#1a4d3c;--accent-light:#eef3f0;}
*{box-sizing:border-box;}
body{margin:0;font:16px/1.6 ui-sans-serif,-apple-system,Segoe UI,Inter,sans-serif;color:var(--ink);background:var(--bg);}
a{color:var(--accent);text-decoration:none;}
a:hover{text-decoration:underline;}
.site-header{border-bottom:1px solid var(--line);padding:18px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}
.site-header .logo{font-size:17px;font-weight:700;letter-spacing:-0.02em;color:var(--ink);}
.site-nav{display:flex;gap:24px;font-size:14px;}
.site-nav a{color:var(--muted);}
.site-nav a:hover{color:var(--ink);text-decoration:none;}
.site-nav a.active{color:var(--ink);font-weight:600;}
.page{max-width:780px;margin:0 auto;padding:48px 24px 80px;}
h1{font-size:28px;font-weight:700;letter-spacing:-0.02em;margin:0 0 6px;}
h2{font-size:20px;font-weight:600;letter-spacing:-0.01em;margin:40px 0 14px;}
h3{font-size:15px;font-weight:600;margin:0 0 4px;}
p{margin:0 0 16px;color:#3a352e;}
.btn{display:inline-block;padding:10px 20px;background:var(--accent);color:#fff;border-radius:10px;font-size:15px;font-weight:500;border:0;cursor:pointer;}
.btn:hover{opacity:.9;text-decoration:none;}
.btn-ghost{background:transparent;color:var(--accent);border:1px solid var(--line);padding:9px 18px;}
.btn-ghost:hover{background:var(--accent-light);opacity:1;}
"""

def _page(title: str, active: str, body: str, authed: bool = False) -> str:
    nav_items = [
        ("/", "About"),
        ("/thought-leadership", "Thought Leadership"),
        ("/contact", "Contact"),
        ("/tools", "CFO Toolbox"),
        ("/library", "Library"),
        ("/feed", "Feed"),
        ("/ask", "Ask"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"active" if active == label else ""}">{label}</a>'
        for href, label in nav_items
    )
    if authed:
        nav += '<a href="/logout">Log out</a>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style></head><body>
<header class="site-header">
  <a class="logo" href="/">Brian Weisberg</a>
  <nav class="site-nav">{nav}</nav>
</header>
{body}
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
    return HTMLResponse(_page("Sign in — Brian Weisberg", "", body))


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
                  'object-position:center top;flex-shrink:0;border:3px solid var(--line);">')
    else:
        avatar = ('<div aria-label="Brian Weisberg" '
                  'style="width:140px;height:140px;border-radius:50%;flex-shrink:0;border:3px solid var(--line);'
                  'background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;'
                  'font-size:46px;font-weight:700;letter-spacing:-0.02em;">BW</div>')
    body = f"""<div class="page">
<div style="display:flex;align-items:flex-start;gap:32px;flex-wrap:wrap;margin-bottom:28px;">
  {avatar}
  <div>
    <h1 style="margin:0 0 4px;">Brian Weisberg</h1>
    <p style="color:var(--muted);font-size:15px;margin:0;">CFO &middot; Boston, MA</p>
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
</div>
</div>"""
    return HTMLResponse(_page("Brian Weisberg — CFO", "About", body))


@app.get("/thought-leadership", response_class=HTMLResponse)
def thought_leadership():
    def section(title: str, items: list[tuple[str, str, str]]) -> str:
        # items: (label, url, sort_key) — sort_key is "YYYY-MM" or "" to pin to top
        sorted_items = sorted(items, key=lambda x: x[2], reverse=True)
        links = "".join(
            f'<li style="margin:0 0 10px;"><a href="{url}" target="_blank" rel="noopener">{_esc(label)}</a></li>'
            for label, url, _ in sorted_items
        )
        return f'<h2>{title}</h2><ul style="padding-left:20px;margin:0 0 8px;">{links}</ul>'

    body = '<div class="page"><h1>Thought Leadership</h1>' + \
        '<p style="color:var(--muted);margin:4px 0 28px;">Podcasts, writing, interviews, and appearances.</p>' + \
        """<a href="/growth-engine-ratio" style="display:block;text-decoration:none;background:var(--accent);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:36px;">
  <div style="font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;opacity:.75;margin-bottom:6px;">Featured &mdash; New Framework</div>
  <div style="font-size:20px;font-weight:700;letter-spacing:-.02em;margin-bottom:6px;">The Growth Engine Ratio</div>
  <div style="font-size:14px;opacity:.85;line-height:1.5;">A new metric for measuring how R&amp;D and GTM investments work together to drive growth &mdash; with an interactive calculator to see how you stack up. Published with The F Suite &rarr;</div>
</a>"""

    body += section("Podcast — Host", [
        ("The Cash Flow Show — Conversations about how tech companies make money",
         "https://www.onlycfo.io/podcast", ""),
    ])

    body += section("Podcasts — Guest", [
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

    body += section("Webinar — Host", [
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

    body += "</div>"
    return HTMLResponse(_page("Thought Leadership — Brian Weisberg", "Thought Leadership", body))


@app.get("/growth-engine-ratio", response_class=HTMLResponse)
def growth_engine_ratio():
    body = """<div class="page" style="max-width:820px;">

<p style="font-size:13px;color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em;">Framework</p>
<h1 style="margin:0 0 8px;">The Growth Engine Ratio</h1>
<p style="color:var(--muted);font-size:15px;margin:0 0 32px;">
  By Brian Weisberg &middot; Published with <a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a> &middot; June 2026
</p>

<div style="background:var(--accent-light);border-left:3px solid var(--accent);border-radius:0 10px 10px 0;padding:18px 22px;margin:0 0 36px;">
  <p style="margin:0;font-size:15px;">
    The full guide — including benchmark data from 200+ public and private SaaS companies via OPEXEngine —
    is available as a downloadable whitepaper on The F Suite.
    <strong><a href="https://www.fsuite.co" target="_blank" rel="noopener">Read the full article and download the guide &rarr;</a></strong>
    <em style="display:block;margin-top:6px;font-size:13px;color:var(--muted);">(Link will be live when The F Suite publishes — coming soon.)</em>
  </p>
</div>

<h2 style="margin-top:0;">Why I Built This</h2>
<p>Most SaaS efficiency metrics measure one engine at a time. CAC payback tells you how quickly GTM
investment pays back on new logos. Magic Number tells you how much ARR you're getting per dollar of
sales and marketing spend. Both are useful — I use them all the time — but they share a blind spot:
they leave R&D entirely out of the efficiency equation.</p>

<p>That bothers me. At most companies, R&D is 20–30% of revenue. It's a meaningful investment, and
it directly influences how easy — or hard — it is for GTM to do its job. A great product shortens
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

<p>GTM uses a 4-quarter lookback because enterprise sales cycles run 6–9 months — pipeline built
in Q<sub>n-4</sub> converts across subsequent quarters until it lands in Q<sub>n</sub>.
R&amp;D uses a 2-quarter lookback starting one quarter earlier (n-5, n-4) because features are
built before they're sold. The build-then-sell sequence matters.</p>

<h2>What the Number Tells You</h2>
<p>A ratio of <strong>$1.00</strong> means you're generating exactly $1 of annualized revenue growth for
every $1 of combined R&amp;D + GTM investment. That's the threshold that separates companies
that are profitable on acquisition from those that aren't.</p>

<p>In my analysis of 11 public SaaS companies across 188 company-quarters, only 2 exceeded $1.00
in steady state. The other 9 need to retain customers for 1.2 to 2.8 years just to break even
on acquisition costs. That changes how you think about churn — permanently.</p>

<div style="background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:0 0 32px;">
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <thead><tr style="background:var(--accent-light);">
      <th style="padding:10px 14px;text-align:left;font-weight:600;">Tier</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;">Ratio</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;">Years to Break Even</th>
      <th style="padding:10px 14px;text-align:left;font-weight:600;">What It Means</th>
    </tr></thead>
    <tbody>
      <tr style="border-top:1px solid var(--line);">
        <td style="padding:10px 14px;">&#127942; Elite</td>
        <td style="padding:10px 14px;">&gt; $1.20</td>
        <td style="padding:10px 14px;">&lt; 0.8 years</td>
        <td style="padding:10px 14px;">Profitable on acquisition — invest aggressively</td>
      </tr>
      <tr style="border-top:1px solid var(--line);background:#fdfcfa;">
        <td style="padding:10px 14px;">&#11088; Strong</td>
        <td style="padding:10px 14px;">$0.70 – $1.20</td>
        <td style="padding:10px 14px;">0.8 – 1.4 years</td>
        <td style="padding:10px 14px;">Above median — maintain efficiency as you scale</td>
      </tr>
      <tr style="border-top:1px solid var(--line);">
        <td style="padding:10px 14px;">&#10003; Typical</td>
        <td style="padding:10px 14px;">$0.50 – $0.70</td>
        <td style="padding:10px 14px;">1.4 – 2.0 years</td>
        <td style="padding:10px 14px;">In the pack — retention must be a top priority</td>
      </tr>
      <tr style="border-top:1px solid var(--line);background:#fdfcfa;">
        <td style="padding:10px 14px;">&#9888;&#65039; Below target</td>
        <td style="padding:10px 14px;">&lt; $0.50</td>
        <td style="padding:10px 14px;">&gt; 2.0 years</td>
        <td style="padding:10px 14px;">Urgent review — fix retention before scaling acquisition</td>
      </tr>
    </tbody>
  </table>
</div>

<h2>Calculate Your Ratio</h2>
<p style="color:var(--muted);font-size:15px;margin:-6px 0 24px;">Enter your last 6 quarters of data. All figures in the same currency (millions, thousands — just be consistent).</p>

<div style="background:#fff;border:1px solid var(--line);border-radius:16px;padding:28px 32px;margin:0 0 40px;">
  <div style="display:grid;gap:20px;">

    <div>
      <p style="font-weight:600;font-size:14px;margin:0 0 12px;color:var(--ink);">Revenue</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
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
      <p style="font-weight:600;font-size:14px;margin:0 0 12px;color:var(--ink);">GTM Spend (Sales &amp; Marketing) — last 4 quarters</p>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">
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
      <p style="font-weight:600;font-size:14px;margin:0 0 12px;color:var(--ink);">R&amp;D Spend — 2 quarters (the build window)</p>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;max-width:320px;">
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

  <div id="ger-result" style="display:none;margin-top:28px;padding-top:24px;border-top:1px solid var(--line);">
    <div style="display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap;">
      <div style="flex:0 0 auto;">
        <p style="font-size:13px;color:var(--muted);margin:0 0 4px;text-transform:uppercase;letter-spacing:.06em;">Your Growth Engine Ratio</p>
        <p id="ger-value" style="font-size:48px;font-weight:700;letter-spacing:-0.03em;margin:0;color:var(--accent);"></p>
      </div>
      <div style="flex:1;min-width:200px;">
        <p id="ger-tier" style="font-size:18px;font-weight:600;margin:0 0 6px;"></p>
        <p id="ger-breakeven" style="font-size:14px;color:var(--muted);margin:0 0 10px;"></p>
        <p id="ger-interp" style="font-size:15px;margin:0;"></p>
      </div>
    </div>
    <div id="ger-detail" style="margin-top:16px;font-size:13px;color:var(--muted);line-height:1.8;"></div>
  </div>
</div>

<p style="font-size:13px;color:var(--muted);margin:-20px 0 40px;">
  <strong>Methodology note:</strong> GTM = Sales &amp; Marketing expense (GAAP including SBC).
  R&D = Research &amp; Development expense. Use either GAAP or non-GAAP consistently —
  don't mix. Benchmarks in the full guide use GAAP. Requires at least 6 quarters of history
  for the n-5 R&amp;D lookback.
</p>

<h2>A Note on Retention</h2>
<p>One of the more useful outputs of this framework is a simple break-even calculation:
<strong>Years to Break Even = 1 ÷ Efficiency Ratio</strong>. If your ratio is $0.60, you
need to retain each customer for 1.7 years just to recover acquisition costs — and that
assumes flat renewal with no expansion. Strong NRR (above 110%) compresses that timeline;
contraction can make it indefinitely long.</p>

<p>Companies below $1.00 — which is most of them — need both high gross retention and strong
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
<p style="font-size:13px;color:var(--muted);margin-top:8px;">(Full link coming soon — check back or <a href="/contact">reach out</a> and I'll send it directly.)</p>

</div>

<script>
function v(id) { return parseFloat(document.getElementById(id).value) || 0; }

function loadExample() {
  document.getElementById('rev_n').value = 100;
  document.getElementById('rev_n1').value = 90;
  document.getElementById('gtm4').value = 20;
  document.getElementById('gtm3').value = 22;
  document.getElementById('gtm2').value = 24;
  document.getElementById('gtm1').value = 26;
  document.getElementById('rnd5').value = 16;
  document.getElementById('rnd4').value = 18;
  calcGER();
}

function calcGER() {
  var rev_n = v('rev_n'), rev_n1 = v('rev_n1');
  var gtm4 = v('gtm4'), gtm3 = v('gtm3'), gtm2 = v('gtm2'), gtm1 = v('gtm1');
  var rnd5 = v('rnd5'), rnd4 = v('rnd4');

  var annGrowth = (rev_n - rev_n1) * 4;
  var gtmInv = 0.25 * (gtm4 + gtm3 + gtm2 + gtm1);
  var rndInv = 0.25 * (rnd5 + rnd4);
  var totalInv = gtmInv + rndInv;

  if (totalInv <= 0 || rev_n <= 0) {
    alert('Please fill in all fields with values greater than zero.');
    return;
  }

  var ratio = annGrowth / totalInv;
  var breakeven = ratio > 0 ? (1 / ratio).toFixed(1) : '∞';

  var tier, tierColor, interp;
  if (ratio >= 1.20) {
    tier = '&#127942; Elite (top 10%)';
    tierColor = '#1a4d3c';
    interp = "You've earned the right to invest aggressively. Every new customer is profitable on acquisition — consider TAM expansion, adjacent markets, or accelerating hiring.";
  } else if (ratio >= 0.70) {
    tier = '&#11088; Strong (above median)';
    tierColor = '#2d6a4f';
    interp = "Solid performance. Focus on maintaining efficiency as you scale. You're close to the $1.00 break-even — small improvements in NRR or cost discipline can get you there.";
  } else if (ratio >= 0.50) {
    tier = '&#10003; Typical (near median)';
    tierColor = '#b45309';
    interp = "You're in the pack. Diagnose: is growth too slow, or investment too high? Pick one to improve first. Retention is critical — you need " + breakeven + " years just to break even on acquisition.";
  } else if (ratio > 0) {
    tier = '&#9888;&#65039; Below target (bottom 25%)';
    tierColor = '#b91c1c';
    interp = "Urgent strategic review needed. Growth likely decelerated while spending stayed elevated. Fix retention and expansion economics before scaling acquisition further.";
  } else {
    tier = '&#8212; Negative growth';
    tierColor = '#b91c1c';
    interp = "Revenue declined quarter-over-quarter. Focus on stabilizing the base before evaluating efficiency.";
  }

  document.getElementById('ger-value').textContent = ratio >= 0 ? '$' + ratio.toFixed(2) : '-$' + Math.abs(ratio).toFixed(2);
  document.getElementById('ger-value').style.color = tierColor;
  document.getElementById('ger-tier').innerHTML = tier;
  document.getElementById('ger-tier').style.color = tierColor;
  document.getElementById('ger-breakeven').textContent = ratio > 0 ? 'Break-even: ' + breakeven + ' years at flat renewal' : '';
  document.getElementById('ger-interp').textContent = interp;
  document.getElementById('ger-detail').innerHTML =
    'Annualized growth: <strong>' + annGrowth.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'GTM investment (time-weighted): <strong>' + gtmInv.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'R&amp;D investment (time-weighted): <strong>' + rndInv.toFixed(1) + '</strong> &nbsp;|&nbsp; ' +
    'Total investment: <strong>' + totalInv.toFixed(1) + '</strong>';

  document.getElementById('ger-result').style.display = 'block';
  document.getElementById('ger-result').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
</script>"""
    return HTMLResponse(_page("The Growth Engine Ratio — Brian Weisberg", "Thought Leadership", body))


@app.get("/contact", response_class=HTMLResponse)
def contact_page(submitted: str = ""):
    if submitted == "1":
        body = """<div class="page" style="max-width:560px;">
<h1>Thanks for reaching out.</h1>
<p>I'll get back to you shortly.</p>
<a href="/" class="btn btn-ghost" style="margin-top:8px;">Back to home</a>
</div>"""
        return HTMLResponse(_page("Contact — Brian Weisberg", "Contact", body))

    body = """<div class="page" style="max-width:560px;">
<h1>Get in Touch</h1>
<p style="color:var(--muted);margin:4px 0 32px;">I'm always happy to connect with finance leaders, founders, and operators.</p>
<form method="post" action="/contact" style="display:grid;gap:16px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Name</label>
    <input name="name" required style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;" placeholder="Your name">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Email</label>
    <input name="email" type="email" required style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;" placeholder="you@example.com">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Message</label>
    <textarea name="message" required rows="5" style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;" placeholder="What's on your mind?"></textarea>
  </div>
  <div>
    <button type="submit" class="btn">Send message</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Contact — Brian Weisberg", "Contact", body))


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
    tools_json = _json.dumps([
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "url": t["url"],
            "categories": t["categories"],
            "submitted_by": t.get("submitted_by") or "",
            "created_at": (t.get("created_at") or "")[:10],
            "updated_at": (t.get("updated_at") or "")[:10],
        }
        for t in tools
    ])

    cat_buttons = "".join(
        f'<button class="tcat-btn" data-cat="{_esc(c)}" onclick="filterCat(this)">{_esc(c)}</button>'
        for c in TOOL_CATEGORIES
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
  <button class="tcat-btn tcat-all tcat-active" data-cat="" onclick="filterCat(this)">All</button>
  {cat_buttons}
</div>

<div id="tool-count" style="font-size:13px;color:var(--muted);margin-bottom:16px;"></div>

<div id="tool-grid" style="display:grid;gap:14px;">
</div>

<p id="tool-empty" style="display:none;color:var(--muted);padding:32px 0;">No tools match your search.</p>

<div style="margin-top:48px;padding-top:32px;border-top:1px solid var(--line);">
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
.tool-name{{font-size:16px;font-weight:600;color:var(--ink);text-decoration:none;display:block;margin-bottom:6px;}}
.tool-name:hover{{color:var(--accent);}}
.tool-desc{{font-size:14px;color:#3a352e;margin:0 0 12px;line-height:1.5;}}
.tool-cats{{display:flex;flex-wrap:wrap;gap:6px;}}
.tool-cat{{font-size:11px;color:var(--accent);background:var(--accent-light);border-radius:6px;padding:2px 8px;}}
.tool-admin{{display:flex;gap:6px;flex-shrink:0;}}
.tool-admin-btn{{font-size:12px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:6px;padding:3px 10px;cursor:pointer;text-decoration:none;white-space:nowrap;}}
.tool-admin-btn:hover{{background:var(--accent-light);color:var(--ink);text-decoration:none;}}
.tool-admin-del:hover{{background:#fee2e2;color:#b91c1c;border-color:#fca5a5;}}
.tool-meta{{font-size:12px;color:var(--muted);margin-top:10px;}}
</style>

<script>
var ALL_TOOLS = {tools_json};
var AUTHED = {'true' if authed else 'false'};
var activeCat = '';

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
  grid.innerHTML = tools.map(function(t) {{
    var cats = (t.categories || []).map(function(c) {{
      return '<span class="tool-cat">' + esc(c) + '</span>';
    }}).join('');
    var adminControls = AUTHED
      ? '<div class="tool-admin">'
          + '<a href="/admin/tools/' + t.id + '/edit" class="tool-admin-btn">Edit</a>'
          + '<form method="post" action="/admin/tools/' + t.id + '/delete" style="display:inline;"'
          + ' onsubmit="return confirm(\'Delete \' + ' + JSON.stringify(t.name) + ' + \'?\');">'
          + '<button type="submit" class="tool-admin-btn tool-admin-del">Delete</button>'
          + '</form>'
          + '</div>'
      : '';
    var metaParts = [];
    if (AUTHED) {{
      if (t.submitted_by) metaParts.push('Submitted by ' + esc(t.submitted_by));
      if (t.created_at) metaParts.push('Added ' + t.created_at);
      if (t.updated_at && t.updated_at !== t.created_at) metaParts.push('Edited ' + t.updated_at);
    }}
    var adminMeta = metaParts.length
      ? '<div class="tool-meta">' + metaParts.join(' &middot; ') + '</div>'
      : '';
    return '<article class="tool-card">'
      + '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">'
      + '<a class="tool-name" href="' + esc(t.url) + '" target="_blank" rel="noopener">' + esc(t.name) + '</a>'
      + adminControls
      + '</div>'
      + '<p class="tool-desc">' + esc(t.description) + '</p>'
      + '<div class="tool-cats">' + cats + '</div>'
      + adminMeta
      + '</article>';
  }}).join('');
}}

function esc(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function filtered() {{
  var q = (document.getElementById('tool-search').value || '').toLowerCase();
  return ALL_TOOLS.filter(function(t) {{
    var matchCat = !activeCat || (t.categories || []).indexOf(activeCat) !== -1;
    if (!matchCat) return false;
    if (!q) return true;
    return (t.name + ' ' + t.description + ' ' + (t.categories || []).join(' ')).toLowerCase().indexOf(q) !== -1;
  }});
}}

function filterTools() {{ renderTools(filtered()); }}

function filterCat(btn) {{
  activeCat = btn.dataset.cat;
  document.querySelectorAll('.tcat-btn').forEach(function(b) {{ b.classList.remove('tcat-active'); }});
  btn.classList.add('tcat-active');
  filterTools();
}}

renderTools(ALL_TOOLS);
</script>"""
    return HTMLResponse(_page("CFO Toolbox — Brian Weisberg", "CFO Toolbox", body, authed=authed))


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
<h1>Thanks — submission received.</h1>
<p>Your tool has been submitted for review. If approved, it'll appear in the CFO Toolbox shortly.</p>
<a href="/tools" class="btn btn-ghost" style="margin-top:8px;">Back to CFO Toolbox</a>
</div>"""
        return HTMLResponse(_page("Submission received — CFO Toolbox", "CFO Toolbox", body))

    body = f"""<div class="page" style="max-width:560px;">
<h1>Submit a Tool</h1>
<p style="color:var(--muted);margin:4px 0 32px;">Know a tool that belongs in the CFO Toolbox? Submit it for review.</p>
<form method="post" action="/tools/submit" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="e.g. Mosaic">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="https://…">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"
      placeholder="What does it do? 1–2 sentences."></textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes()}
    </div>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Your email *</label>
    <input name="submitted_by" type="email" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="you@example.com">
  </div>
  <div>
    <button type="submit" class="btn">Submit for review</button>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Submit a Tool — CFO Toolbox", "CFO Toolbox", body))


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
<h1>Contact submissions</h1>
<p style="margin:-2px 0 0;"><a href="/logout" style="font-size:13px;color:var(--muted);">Log out</a></p>
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
    return HTMLResponse(_page("Contacts — Admin", "", body))


@app.get("/admin/tools", response_class=HTMLResponse)
def admin_tools(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        pending = [t for t in lib.list_tools(approved_only=False) if not t["approved"]]
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

    rows = "".join(_tool_row(t) for t in pending) or \
        '<tr><td colspan="7" style="padding:20px;color:var(--muted);">No pending submissions.</td></tr>'

    body = f"""<div class="page" style="max-width:1100px;">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
  <h1>CFO Toolbox — Pending submissions</h1>
  <a href="/admin/tools/new" class="btn" style="font-size:14px;padding:8px 18px;">+ Add tool</a>
</div>
<p style="margin:0 0 24px;"><a href="/tools" style="font-size:13px;color:var(--muted);">View public directory →</a></p>
<div style="overflow-x:auto;">
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
<tbody>{rows}</tbody>
</table>
</div>
</div>"""
    return HTMLResponse(_page("Tools Admin — CFO Toolbox", "", body, authed=True))


@app.get("/admin/tools/new", response_class=HTMLResponse)
def admin_tools_new(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    body = f"""<div class="page" style="max-width:560px;">
<h1>Add a tool</h1>
<p style="color:var(--muted);margin:4px 0 32px;">Manually add a tool directly to the public directory.</p>
<form method="post" action="/admin/tools/new" style="display:grid;gap:20px;">
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;"
      placeholder="https://…">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;"
      placeholder="What does it do? 1–2 sentences."></textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes()}
    </div>
  </div>
  <div>
    <button type="submit" class="btn">Add to directory</button>
    <a href="/admin/tools" class="btn btn-ghost" style="margin-left:10px;">Cancel</a>
  </div>
</form>
</div>"""
    return HTMLResponse(_page("Add Tool — CFO Toolbox", "", body, authed=True))


@app.post("/admin/tools/new")
async def admin_tools_new_submit(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    categories = [v.strip() for v in form.getlist("categories") if v.strip()]
    if not (name and url and description and categories):
        raise HTTPException(status_code=400, detail="Name, URL, description, and at least one category are required.")
    lib = _lib()
    try:
        lib.add_tool(name, description, url, categories, approved=1)
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
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Tool name *</label>
    <input name="name" required maxlength="200" value="{_esc(tool['name'])}"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">URL *</label>
    <input name="url" type="url" required maxlength="500" value="{_esc(tool['url'])}"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;">
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:6px;">Short description *</label>
    <textarea name="description" required maxlength="400" rows="3"
      style="width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;font:inherit;font-size:15px;background:#fff;resize:vertical;">{_esc(tool['description'])}</textarea>
  </div>
  <div>
    <label style="display:block;font-size:14px;font-weight:500;margin-bottom:10px;">Categories * <span style="font-weight:400;color:var(--muted);">(select all that apply)</span></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
      {_tool_category_checkboxes(tool['categories'])}
    </div>
  </div>
  <div>
    <button type="submit" class="btn">Save changes</button>
    <a href="/tools" class="btn btn-ghost" style="margin-left:10px;">Cancel</a>
  </div>
</form>
</div>"""
    return HTMLResponse(_page(f"Edit {_esc(tool['name'])} — CFO Toolbox", "", body, authed=True))


@app.post("/admin/tools/{tool_id}/edit")
async def admin_tools_edit_submit(request: Request, tool_id: int):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    categories = [v.strip() for v in form.getlist("categories") if v.strip()]
    if not (name and url and description and categories):
        raise HTTPException(status_code=400, detail="Name, URL, description, and at least one category are required.")
    lib = _lib()
    try:
        lib.update_tool(tool_id, name, description, url, categories)
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


# ---------------------------------------------------------------------------
# Private library tools
# ---------------------------------------------------------------------------

OPML_PATH = os.environ.get("LINKLIB_SITES_OPML", os.path.join(_APP_DIR, "preferred_sites.opml"))


@app.get("/feed", response_class=HTMLResponse)
def feed_reader(request: Request, cat: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)
    from linklib.feed import get_feed_items

    try:
        items, categories = get_feed_items(OPML_PATH, category=cat, max_total=120)
    except Exception as e:
        return HTMLResponse(_page("CFO Feed — Brian Weisberg", "Feed",
            f'<div class="page"><h2>Feed unavailable</h2><p style="color:var(--muted);">Could not load feeds: {_esc(str(e))}</p></div>',
            authed=True))

    # Category tab bar
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

    # Collect unique sources in the order they first appear
    sources = list(dict.fromkeys(item["source"] for item in items))

    cards = ""
    for item in items:
        save_url = _esc(item["url"])
        paywalled = item.get("paywalled", False)
        paywall_badge = ' <span style="font-size:11px;background:#fef3c7;color:#92400e;padding:2px 7px;border-radius:10px;font-weight:600;vertical-align:middle;">&#128274; Paywalled</span>' if paywalled else ''
        read_btn = '' if paywalled else f'<a href="/read?url={quote(item["url"], safe="")}" class="faction">&#9654; Read</a>'
        src_attr = _esc(item["source"])
        cards += f"""<article class="fcard" data-source="{src_attr}">
  <div class="fcard-meta">{_esc(item['source'])}{ ' &middot; ' + _fmt_date(item['published_at']) if item['published_at'] else ''}{paywall_badge}</div>
  <a class="fcard-title" href="{save_url}" target="_blank" rel="noopener">{_esc(item['title'])}</a>
  { f'<p class="fcard-summary">{_esc(item["summary"])}</p>' if item.get('summary') else '' }
  <div class="fcard-actions">
    {read_btn}
    <button class="faction" onclick="saveItem(this,'{save_url}')">+ Save to Library</button>
  </div>
</article>"""

    if not cards:
        cards = '<p style="color:var(--muted);padding:32px 0;">No items loaded — feeds may be warming up. Try refreshing in a moment.</p>'

    # Source filter checkboxes
    source_checks = "".join(
        f'<label class="fsrc-label"><input type="checkbox" class="fsrc-cb" value="{_esc(s)}" checked onchange="applyFilter()"><span>{_esc(s)}</span></label>'
        for s in sources
    )
    filter_panel = f"""<div id="filter-panel" style="display:none;border-bottom:1px solid var(--line);background:#fff;padding:14px 24px;">
  <div style="max-width:860px;margin:0 auto;">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:10px;">
      <span style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;">Filter by source</span>
      <button onclick="setAll(true)" style="font-size:12px;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;">Select all</button>
      <button onclick="setAll(false)" style="font-size:12px;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;">Clear all</button>
      <span id="filter-count" style="font-size:12px;color:var(--muted);margin-left:auto;"></span>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;">{source_checks}</div>
  </div>
</div>"""

    body = f"""<div style="border-bottom:1px solid var(--line);padding:12px 24px;position:sticky;top:0;z-index:5;background:var(--bg);">
  <div style="max-width:860px;margin:0 auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <div style="display:flex;gap:8px;flex-wrap:wrap;flex:1;">{tabs}</div>
    <button onclick="toggleFilter()" id="filter-btn" style="font-size:13px;font-weight:500;color:var(--accent);background:none;border:1px solid var(--line);border-radius:20px;padding:6px 14px;cursor:pointer;white-space:nowrap;">&#9776; Sources</button>
  </div>
</div>
{filter_panel}
<main id="feed-main" style="max-width:860px;margin:0 auto;padding:24px 24px 80px;display:grid;gap:12px;">
{cards}
</main>
<style>
.ftab{{display:inline-block;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;
  color:var(--muted);text-decoration:none;border:1px solid transparent;}}
.ftab:hover{{color:var(--ink);text-decoration:none;background:var(--accent-light);}}
.ftab-on{{background:var(--accent);color:#fff !important;}}
.fcard{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 20px;}}
.fcard-meta{{font-size:12px;color:var(--muted);margin-bottom:5px;}}
.fcard-title{{font-size:16px;font-weight:600;color:var(--ink);text-decoration:none;display:block;margin-bottom:6px;line-height:1.35;}}
.fcard-title:hover{{color:var(--accent);text-decoration:none;}}
.fcard-summary{{font-size:14px;color:#5a5248;margin:0 0 10px;line-height:1.5;}}
.fcard-actions{{display:flex;gap:10px;margin-top:8px;}}
.faction{{font-size:13px;font-weight:500;color:var(--accent);background:none;border:1px solid var(--line);
  border-radius:8px;padding:5px 12px;cursor:pointer;text-decoration:none;}}
.faction:hover{{background:var(--accent-light);text-decoration:none;}}
.faction.saved{{color:var(--muted);pointer-events:none;}}
.fsrc-label{{display:flex;align-items:center;gap:5px;font-size:13px;cursor:pointer;
  background:var(--bg);border:1px solid var(--line);border-radius:20px;padding:4px 10px;
  user-select:none;transition:background .1s;}}
.fsrc-label:hover{{background:var(--accent-light);}}
.fsrc-label input{{accent-color:var(--accent);cursor:pointer;}}
</style>
<script>
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
function applyFilter() {{
  var selected = new Set();
  document.querySelectorAll('.fsrc-cb:checked').forEach(function(cb) {{ selected.add(cb.value); }});
  var visible = 0;
  document.querySelectorAll('.fcard').forEach(function(card) {{
    var show = selected.has(card.dataset.source);
    card.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  updateCount(visible);
}}
function updateCount(n) {{
  var total = document.querySelectorAll('.fcard').length;
  if (n === undefined) n = total;
  document.getElementById('filter-count').textContent = n + ' of ' + total + ' shown';
}}
function saveItem(btn, url) {{
  btn.textContent = 'Saving…';
  btn.classList.add('saved');
  fetch('/feed/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: 'url=' + encodeURIComponent(url)
  }})
  .then(r => {{ btn.textContent = r.ok ? '✓ Saved' : '✗ Error'; }})
  .catch(() => {{ btn.textContent = '✗ Error'; btn.classList.remove('saved'); }});
}}
</script>"""

    return HTMLResponse(_page("CFO Feed — Brian Weisberg", "Feed", body, authed=True))


_READER_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Inter:wght@400;500&display=swap');
:root{--ink:#1a1714;--muted:#7a7068;--line:#e8e2d8;--bg:#f9f6f0;--surface:#ffffff;--accent:#1a4d3c;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);font:18px/1.75 'Lora',Georgia,serif;}
a{color:var(--accent);text-decoration:underline;text-underline-offset:3px;}
a:hover{opacity:.8;}

.reader-bar{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--line);
  padding:0 24px;height:48px;display:flex;align-items:center;justify-content:space-between;
  font-family:'Inter',sans-serif;font-size:13px;color:var(--muted);}
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
.reader-meta .byline{font-family:'Inter',sans-serif;font-size:14px;color:var(--muted);line-height:1.5;}
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
.reader-body figcaption{font-size:.85em;color:var(--muted);font-family:'Inter',sans-serif;margin-top:.4em;}
.reader-body table{width:100%;border-collapse:collapse;font-size:.9em;margin:1.5em 0;}
.reader-body th,.reader-body td{padding:8px 12px;border:1px solid var(--line);text-align:left;}
.reader-body th{background:#f4f0e8;font-family:'Inter',sans-serif;}
.reader-body pre,.reader-body code{font-family:ui-monospace,monospace;font-size:.85em;
  background:#f0ece4;border-radius:4px;padding:2px 5px;}
.reader-body pre{padding:16px;overflow-x:auto;border-radius:8px;margin:1.5em 0;}
.reader-body pre code{background:none;padding:0;}
.reader-body hr{border:none;border-top:1px solid var(--line);margin:2.5em 0;}

.reader-empty{text-align:center;padding:60px 20px;color:var(--muted);font-family:'Inter',sans-serif;}
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
    <a href="{orig_url}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:13px;">Original &rarr;</a>
  </div>
</div>
<div class="reader-wrap">
  <div class="reader-meta">
    <h1>{title}</h1>
    <div class="byline">{byline}</div>
  </div>
  <div class="reader-body">{body}</div>
</div>
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
        body_html = '<div class="reader-empty"><h2>No URL provided</h2><p>Add ?url=https://... to the address bar.</p></div>'
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

    return HTMLResponse(_READER_TMPL.format(
        title=_esc(title), css=_READER_CSS,
        back_url=back_url, back_label=back_label,
        orig_url=_esc(url), byline=byline,
        body=body_html,
    ))


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, q: str = ""):
    if not _is_authed(request):
        return _login_redirect(request)
    lib = _lib()
    try:
        results = lib.search(q, limit=100)
        total = lib.count()
        tags = lib.all_tags()[:25]
    finally:
        lib.close()

    cards = "".join(
        f"""<article class="card">
          <a class="card-title" href="{r['url']}" target="_blank" rel="noopener">{_esc(r['title'])}</a>
          <div class="meta">{_esc(r.get('source',''))}{' &middot; ' + _esc(r['saved_at'][:10]) if r.get('saved_at') else ''}</div>
          <p class="summary">{_esc(r.get('summary',''))[:280]}</p>
          <div class="tags">{''.join(f'<span>{_esc(t)}</span>' for t in r.get('tags', []))}</div>
          <div style="display:flex;gap:8px;margin-top:8px;">
            <a href="/read?id={r['id']}" class="postbtn" style="text-decoration:none;">Read</a>
            <button class="postbtn" onclick="draftPost('{_esc(r['url'])}')">Draft LinkedIn post</button>
          </div>
        </article>"""
        for r in results
    ) or '<p style="color:var(--muted);">No matches.</p>'

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
.tags span{{font-size:11px;color:var(--accent);background:var(--accent-light);border-radius:6px;padding:2px 8px;}}
.postbtn{{margin-top:12px;padding:6px 12px;font-size:12px;background:transparent;color:var(--accent);border:1px solid var(--line);border-radius:8px;cursor:pointer;}}
.postbtn:hover{{background:var(--accent-light);}}
nav.site-nav a[href="/library"]{{color:var(--ink);font-weight:600;}}
</style>
<script>
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
async function draftPost(url){{
  var box=document.getElementById('answer');
  box.style.display='block';box.scrollIntoView({{behavior:'smooth'}});box.innerHTML='<em>Drafting in your voice…</em>';
  try{{
    var r=await fetch('/post',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{url:url,mode:'original'}})}});
    var d=await r.json();
    box.innerHTML='<div style="white-space:pre-wrap;line-height:1.6;">'+(d.post||'')+'</div><button class="btn btn-ghost" style="margin-top:10px;font-size:13px;" onclick="navigator.clipboard.writeText(this.previousElementSibling.innerText)">Copy</button>';
  }}catch(e){{box.innerHTML='Something went wrong.';}}
}}
</script>"""

    return HTMLResponse(_page("Library — Brian Weisberg", "Library", page_body, authed=True))


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

    return HTMLResponse(_page("Ask — Brian Weisberg", "Ask", body, authed=True))


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


@app.get("/admin/upload-db", response_class=HTMLResponse)
def upload_db_page(request: Request):
    """One-time helper to seed the hosted DB from a local library.db.

    Login-gated. Drag the local file in and submit — the next page load uses
    it (connections are opened per-request, so no restart is needed). Safe to
    leave in place: it's protected by the same secret as the rest of the
    private section.
    """
    if not _is_authed(request):
        return _login_redirect(request)
    try:
        lib = _lib()
        try:
            current = lib.count()
        finally:
            lib.close()
    except Exception:
        current = "unknown"
    body = f"""<div class="page">
  <h1>Upload library database</h1>
  <p class="muted">Current hosted database holds <strong>{current}</strong> articles.
  Uploading replaces it with the file you select. This is meant as a one-time
  seed from your local <code>library.db</code>.</p>
  <form method="post" action="/admin/upload-db" enctype="multipart/form-data"
        style="margin-top:1.5rem;display:flex;flex-direction:column;gap:1rem;max-width:480px;">
    <input type="file" name="file" accept=".db,.sqlite,.sqlite3,application/octet-stream" required
           style="padding:0.5rem;border:1px solid #ccc;border-radius:6px;">
    <button type="submit"
            style="padding:0.6rem 1rem;background:#1a1a2e;color:#fff;border:none;border-radius:6px;cursor:pointer;">
      Upload and replace
    </button>
  </form>
  <p class="muted" style="margin-top:1rem;font-size:0.85rem;">
    Tip: quit your local app first so the file is fully written, then upload
    <code>library.db</code> (the main file only — the <code>-wal</code>/<code>-shm</code>
    sidecars aren't needed).</p>
</div>"""
    return HTMLResponse(_page("Upload database", "", body, authed=True))


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

    body = f"""<div class="page">
  <h1>Upload complete</h1>
  <p>Imported a database with <strong>{n}</strong> articles. It's live now —
  no restart needed.</p>
  <p style="margin-top:1rem;"><a href="/library">Go to the library →</a></p>
</div>"""
    return HTMLResponse(_page("Upload complete", "", body, authed=True))


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
        "javascript:(function(){var u=encodeURIComponent(location.href);"
        f"fetch('{PUBLIC_BASE}/save{token_param}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
        "body:JSON.stringify({url:decodeURIComponent(u)})}).then(function(){alert('Saved to library');});})();"
    )
    return js


@app.post("/post")
async def post_draft(request: Request):
    _require_api(request)
    from linklib.social import draft_post
    payload = await request.json()
    lib = _lib()
    try:
        d = draft_post(lib, article_id=payload.get("id"), url=payload.get("url"),
                       topic=payload.get("topic"), mode=payload.get("mode", "original"))
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
    lib = _lib()
    try:
        ingest_url(lib, url)
        background_tasks.add_task(backup.maybe_backup, DB_PATH)
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        lib.close()


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
