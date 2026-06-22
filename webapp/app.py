#!/usr/bin/env python3
"""bmweis.com — public site + private CFO Navigator tools.

Public routes (no auth):
    GET  /                     Bio homepage
    GET  /thought-leadership   Podcasts, writing, interviews
    GET  /contact              Contact form
    POST /contact              Submit contact form

Private routes (library tools):
    GET  /library              Search + browse saved articles
    POST /ask                  FP&A Q&A
    POST /save                 Capture a link
    POST /post                 Draft a LinkedIn post
    GET  /api/search           JSON search API
    GET  /bookmarklet          One-click saver script
    GET  /admin/contacts       View contact form submissions (token-gated)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from linklib.db import Library
from linklib.pipeline import ingest_url

DB_PATH = os.environ.get("LINKLIB_DB", "library.db")
SAVE_TOKEN = os.environ.get("LINKLIB_SAVE_TOKEN", "")
PUBLIC_BASE = os.environ.get("LINKLIB_PUBLIC_BASE", "http://localhost:8000")

app = FastAPI(title="bmweis.com")


def _lib() -> Library:
    return Library(DB_PATH)


def _check_token(token: str | None) -> None:
    if SAVE_TOKEN and token != SAVE_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing save token")


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

def _page(title: str, active: str, body: str) -> str:
    nav_items = [
        ("/", "About"),
        ("/thought-leadership", "Thought Leadership"),
        ("/contact", "Contact"),
        ("/library", "Library"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"active" if active == label else ""}">{label}</a>'
        for href, label in nav_items
    )
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
# Public pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def homepage():
    body = """<div class="page">
<h1>Brian Weisberg</h1>
<p style="color:var(--muted);font-size:15px;margin:0 0 28px;">CFO &middot; Boston, MA</p>

<p>I'm a CFO with 15+ years leading finance, accounting, and business operations for B2B SaaS
and IT infrastructure companies. I'm currently CFO at The Suite, Inc. and GM of
<a href="https://www.fsuite.co" target="_blank" rel="noopener">The F Suite</a>—an invite-only
network of 1,000+ growth and late-stage CFOs. Before that, I spent seven years as CFO of
<a href="https://tidelift.com" target="_blank" rel="noopener">Tidelift</a>, growing the company
from fewer than a dozen employees through $73.5M in funding and an eventual acquisition by Sonar.</p>

<p>Scaling early-stage startups has become my passion. While not a traditional entrepreneur myself,
I'm inspired by the energy and conviction founders bring to disrupting the status quo—and I've
built my career helping them do it with a clear financial picture and sound operational backbone.</p>

<p>What sets me apart is a cross-functional approach to financial leadership. I get out from
behind my desk to mentor, learn from, and build real relationships with peers in product,
engineering, sales, and marketing. Those relationships are how you earn trust, acquire earned
secrets, and develop a genuine pulse on how a business actually operates. That's the foundation
for financial leadership that's actually useful to a leadership team.</p>

<p>I host the <a href="https://www.onlycfo.io/podcast" target="_blank" rel="noopener">OnlyCFO Podcast</a>,
write on startup finance, and advise finance leaders navigating the early-to-growth journey.
Based in Boston, MA.</p>

<div style="display:flex;gap:12px;margin-top:32px;flex-wrap:wrap;">
  <a href="/thought-leadership" class="btn">Thought Leadership</a>
  <a href="/contact" class="btn btn-ghost">Get in Touch</a>
</div>
</div>"""
    return HTMLResponse(_page("Brian Weisberg — CFO", "About", body))


@app.get("/thought-leadership", response_class=HTMLResponse)
def thought_leadership():
    def section(title: str, items: list[tuple[str, str]]) -> str:
        links = "".join(
            f'<li style="margin:0 0 10px;"><a href="{url}" target="_blank" rel="noopener">{_esc(label)}</a></li>'
            for label, url in items
        )
        return f'<h2>{title}</h2><ul style="padding-left:20px;margin:0 0 8px;">{links}</ul>'

    body = '<div class="page"><h1>Thought Leadership</h1>' + \
        '<p style="color:var(--muted);margin:4px 0 32px;">Podcasts, writing, interviews, and appearances.</p>'

    body += section("Podcast — Host", [
        ("OnlyCFO Podcast", "https://www.onlycfo.io/podcast"),
    ])

    body += section("Podcasts — Guest", [
        ("Code to Cash, Ep. 9 — Monetizing Thoughtfully: Architecting Financial Stacks",
         "https://creators.spotify.com/pod/profile/codetocash/episodes/Episode-9-Monetizing-Thoughtfully--Architecting-Financial-Stacks-with-Brian-Weisberg--CFO-of-Tidelift-e28unsn"),
        ("OpexEngine — SaaS Conversations: Dynamic Planning for SaaS Finance Leaders",
         "https://www.opexengine.com/webinar/opexengine-saas-conversations-dynamic-planning-for-saas-finance-leaders"),
        ("YouTube appearance", "https://www.youtube.com/watch?v=mqVvcVVTSrk"),
        ("YouTube appearance", "https://www.youtube.com/watch?v=GPdRstJ_sKw"),
    ])

    body += section("Webinar — Host", [
        ("Numeric — Lean Accounting Team", "https://numeric.lpages.co/lean-accounting-team-webinar/"),
    ])

    body += section("Interview", [
        ("Sequence — From $1M to $100M: 6 Finance Lessons from the Frontline",
         "https://www.sequencehq.com/blog/from-1m-to-100m-6-finance-lessons-from-the-frontline"),
    ])

    body += section("Authored", [
        ("The F Suite — Exit Readiness for CFOs", "https://www.fsuite.co/blog/exit-readiness-cfos"),
        ("OnlyCFO — Building Dashboards That Matter", "https://www.onlycfo.io/p/building-dashboards-that-matter"),
    ])

    body += section("Cited & Quoted", [
        ("LegalDive — GC/CFO collaboration (SVB, TechGC, The F Suite)",
         "https://www.legaldive.com/news/gc-cfo-collaboration-svb-techgc-the-f-suite-silicon-valley-bank/646561/"),
        ("Numeric — When and How to Scale Your Accounting Department",
         "https://www.numeric.io/blog/when-and-how-to-scale-your-accounting-department"),
        ("Numeric — Startup CFO Primer", "https://www.numeric.io/blog/startup-cfo-primer"),
        ("CFO Drive — Innovative Cost-Saving Measures Q&A",
         "https://cfodrive.com/qa/what-innovative-cost-saving-measures-can-significantly-impact-a-companys-bottom-line/"),
    ])

    body += "</div>"
    return HTMLResponse(_page("Thought Leadership — Brian Weisberg", "Thought Leadership", body))


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


@app.get("/admin/contacts", response_class=HTMLResponse)
def admin_contacts(token: str | None = None):
    _check_token(token)
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


# ---------------------------------------------------------------------------
# Private library tools
# ---------------------------------------------------------------------------

@app.get("/library", response_class=HTMLResponse)
def library(q: str = ""):
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
          <button class="postbtn" onclick="draftPost('{_esc(r['url'])}')">Draft LinkedIn post</button>
        </article>"""
        for r in results
    ) or '<p style="color:var(--muted);">No matches.</p>'

    tagbar = "".join(
        f'<a href="/library?q={_esc(t)}">{_esc(t)} <em>{c}</em></a>' for t, c in tags
    )

    page_body = f"""<div style="border-bottom:1px solid var(--line);padding:20px 24px;">
  <div style="max-width:780px;margin:0 auto;">
    <div style="font-size:13px;color:var(--muted);margin-bottom:10px;">{total} saved</div>
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
  var box=document.getElementById('answer');
  box.style.display='block';box.innerHTML='<em>Thinking…</em>';
  try{{
    var r=await fetch('/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q}})}});
    var d=await r.json();
    var lib=(d.sources||[]).map(function(s,i){{return '<li><a href="'+s.url+'" target="_blank">['+(i+1)+'] '+s.title+'</a></li>';}}).join('');
    var web=(d.web_sources||[]).map(function(s){{return '<li><a href="'+s.url+'" target="_blank">🌐 '+s.title+'</a></li>';}}).join('');
    box.innerHTML='<p>'+(d.answer||'').replace(/\\n/g,'<br>')+'</p>'+((lib||web)?'<ul style="padding-left:18px;font-size:13px;">'+lib+web+'</ul>':'');
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

    return HTMLResponse(_page(f"Library — Brian Weisberg", "Library", page_body))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/search")
def api_search(q: str = "", limit: int = 50):
    lib = _lib()
    try:
        return {"query": q, "results": lib.search(q, limit=limit)}
    finally:
        lib.close()


@app.post("/ask")
async def ask(request: Request):
    from linklib.agent import answer_question
    payload = await request.json()
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question required")
    lib = _lib()
    try:
        ans = answer_question(lib, question)
        return {"answer": ans.text,
                "sources": [{"title": s["title"], "url": s["url"]} for s in ans.sources],
                "web_sources": ans.web_sources}
    finally:
        lib.close()


@app.post("/save")
async def save(request: Request, token: str | None = None):
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
        return JSONResponse({"ok": True, "id": row["id"], "title": row.get("title"), "tags": row.get("tags", [])})
    finally:
        lib.close()


@app.get("/bookmarklet", response_class=PlainTextResponse)
def bookmarklet():
    token_param = f"?token={SAVE_TOKEN}" if SAVE_TOKEN else ""
    js = (
        "javascript:(function(){var u=encodeURIComponent(location.href);"
        f"fetch('{PUBLIC_BASE}/save{token_param}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
        "body:JSON.stringify({url:decodeURIComponent(u)})}).then(function(){alert('Saved to library');});})();"
    )
    return js


@app.post("/post")
async def post_draft(request: Request):
    from linklib.social import draft_post
    payload = await request.json()
    lib = _lib()
    try:
        d = draft_post(lib, article_id=payload.get("id"), url=payload.get("url"),
                       topic=payload.get("topic"), mode=payload.get("mode", "original"))
        return {"post": d.post}
    finally:
        lib.close()
