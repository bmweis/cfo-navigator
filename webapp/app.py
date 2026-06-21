#!/usr/bin/env python3
"""Going-forward capture + search web app.

Run locally now:
    pip install fastapi "uvicorn[standard]"
    uvicorn webapp.app:app --reload

This same app is what you later deploy so the iOS/iPadOS Share Sheet
shortcut and the desktop bookmarklet can POST to it from anywhere.

Endpoints:
    GET  /                     search + browse UI
    POST /save                 capture a link  {url, tags?, note?}  -> JSON
    GET  /api/search?q=...      JSON search (this is what Claude/MCP hits)
    GET  /bookmarklet           a drag-to-bookmarks-bar one-click saver

Security: set LINKLIB_SAVE_TOKEN and pass it as ?token= (or X-Save-Token
header) on /save so a hosted instance isn't open to the world.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from linklib.db import Library
from linklib.pipeline import ingest_url

DB_PATH = os.environ.get("LINKLIB_DB", "library.db")
SAVE_TOKEN = os.environ.get("LINKLIB_SAVE_TOKEN", "")
PUBLIC_BASE = os.environ.get("LINKLIB_PUBLIC_BASE", "http://localhost:8000")

app = FastAPI(title="CFO Navigator")


def _lib() -> Library:
    return Library(DB_PATH)


def _check_token(token: str | None) -> None:
    if SAVE_TOKEN and token != SAVE_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing save token")


@app.post("/admin/upload-db")
async def upload_db(token: str | None = None, file: UploadFile = File(...)):
    """Restore library.db from an uploaded file. Protected by LINKLIB_SAVE_TOKEN."""
    _check_token(token)
    data = await file.read()
    with open(DB_PATH, "wb") as f:
        f.write(data)
    return JSONResponse({"ok": True, "bytes": len(data)})


@app.get("/api/search")
def api_search(q: str = "", limit: int = 50):
    lib = _lib()
    try:
        return {"query": q, "results": lib.search(q, limit=limit)}
    finally:
        lib.close()


@app.post("/ask")
async def ask(request: Request):
    """FP&A Q&A grounded in the library, with cited sources."""
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
    """Draft a LinkedIn post in Brian's voice from a saved article or topic."""
    from linklib.social import draft_post
    payload = await request.json()
    lib = _lib()
    try:
        d = draft_post(lib, article_id=payload.get("id"), url=payload.get("url"),
                       topic=payload.get("topic"), mode=payload.get("mode", "original"))
        return {"post": d.post}
    finally:
        lib.close()


@app.get("/", response_class=HTMLResponse)
def home(q: str = ""):
    lib = _lib()
    try:
        results = lib.search(q, limit=100)
        total = lib.count()
        tags = lib.all_tags()[:25]
    finally:
        lib.close()

    cards = "".join(
        f"""<article class="card">
          <a class="title" href="{r['url']}" target="_blank" rel="noopener">{_esc(r['title'])}</a>
          <div class="meta">{_esc(r.get('source',''))}{' · ' + _esc(r['saved_at'][:10]) if r.get('saved_at') else ''}</div>
          <p class="summary">{_esc(r.get('summary',''))[:280]}</p>
          <div class="tags">{''.join(f'<span>{_esc(t)}</span>' for t in r.get('tags', []))}</div>
          <button class="postbtn" onclick="draftPost('{_esc(r['url'])}')">Draft LinkedIn post</button>
        </article>""" for r in results
    ) or '<p class="empty">No matches.</p>'

    tagbar = "".join(f'<a href="/?q={_esc(t)}">{_esc(t)} <em>{c}</em></a>' for t, c in tags)

    return HTMLResponse(_PAGE.format(q=_esc(q), total=total, cards=cards, tagbar=tagbar))


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CFO Navigator</title>
<style>
  :root {{ --ink:#16130f; --muted:#6b6258; --line:#e6e0d6; --bg:#faf7f2; --accent:#1a4d3c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.5 ui-sans-serif,-apple-system,Segoe UI,Inter,sans-serif; color:var(--ink); background:var(--bg); }}
  header {{ padding:28px 20px 16px; border-bottom:1px solid var(--line); }}
  h1 {{ margin:0 0 12px; font-size:20px; letter-spacing:-0.01em; }}
  h1 em {{ color:var(--muted); font-style:normal; font-weight:400; font-size:14px; }}
  form {{ display:flex; gap:8px; max-width:680px; }}
  input[type=search] {{ flex:1; padding:11px 14px; border:1px solid var(--line); border-radius:10px; font-size:16px; background:#fff; }}
  button {{ padding:11px 18px; border:0; border-radius:10px; background:var(--accent); color:#fff; font-size:15px; cursor:pointer; }}
  .tagbar {{ display:flex; flex-wrap:wrap; gap:6px; max-width:920px; margin:14px 0 0; }}
  .tagbar a {{ font-size:12px; color:var(--muted); text-decoration:none; border:1px solid var(--line); border-radius:999px; padding:3px 10px; background:#fff; }}
  .tagbar a em {{ color:#b8aE9e; font-style:normal; }}
  .ask {{ display:flex; gap:8px; max-width:680px; margin:14px 0 0; }}
  .ask textarea {{ flex:1; padding:10px 14px; border:1px solid var(--line); border-radius:10px; font:inherit; font-size:15px; background:#fff; resize:vertical; }}
  .answer {{ max-width:920px; margin:14px 0 0; background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; font-size:15px; }}
  .answer .srcs {{ margin:10px 0 0; padding-left:18px; font-size:13px; }}
  .answer .srcs a {{ color:var(--accent); text-decoration:none; }}
  main {{ max-width:920px; margin:0 auto; padding:20px; display:grid; gap:14px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:16px 18px; }}
  .title {{ font-size:17px; font-weight:600; color:var(--ink); text-decoration:none; }}
  .title:hover {{ color:var(--accent); }}
  .meta {{ color:var(--muted); font-size:13px; margin:3px 0 8px; }}
  .summary {{ margin:0 0 10px; color:#3a352e; font-size:14px; }}
  .tags {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .tags span {{ font-size:11px; color:var(--accent); background:#eef3f0; border-radius:6px; padding:2px 8px; }}
  .postbtn {{ margin-top:12px; padding:6px 12px; font-size:12px; background:transparent; color:var(--accent); border:1px solid var(--line); border-radius:8px; cursor:pointer; }}
  .postbtn:hover {{ background:#eef3f0; }}
  .answer .draft {{ white-space:normal; line-height:1.6; }}
  .answer button {{ margin-top:10px; padding:6px 14px; font-size:13px; }}
  .empty {{ color:var(--muted); }}
</style></head><body>
<header>
  <h1>CFO Navigator <em>{total} saved &middot; find your way back to anything</em></h1>
  <form method="get" action="/">
    <input type="search" name="q" value="{q}" placeholder="Search titles, summaries, notes, tags…" autofocus>
    <button type="submit">Search</button>
  </form>
  <nav class="tagbar">{tagbar}</nav>
  <div class="ask">
    <textarea id="q" rows="2" placeholder="Ask your library an FP&amp;A question… (e.g. how should I frame CAC payback for usage-based pricing?)"></textarea>
    <button onclick="ask()">Ask</button>
  </div>
  <div id="answer" class="answer" hidden></div>
</header>
<script>
async function ask() {{
  var q = document.getElementById('q').value.trim();
  if (!q) return;
  var box = document.getElementById('answer');
  box.hidden = false; box.innerHTML = '<em>Thinking…</em>';
  try {{
    var r = await fetch('/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{question:q}})}});
    var d = await r.json();
    var lib = (d.sources||[]).map(function(s,i){{return '<li><a href="'+s.url+'" target="_blank" rel="noopener">['+(i+1)+'] '+s.title+'</a></li>';}}).join('');
    var web = (d.web_sources||[]).map(function(s){{return '<li><a href="'+s.url+'" target="_blank" rel="noopener">🌐 '+s.title+'</a></li>';}}).join('');
    box.innerHTML = '<p>'+(d.answer||'').replace(/\\n/g,'<br>')+'</p>'+((lib||web)?'<ul class="srcs">'+lib+web+'</ul>':'');
  }} catch(e) {{ box.innerHTML = 'Something went wrong.'; }}
}}
async function draftPost(url) {{
  var box = document.getElementById('answer');
  box.hidden = false; box.scrollIntoView({{behavior:'smooth'}}); box.innerHTML = '<em>Drafting in your voice…</em>';
  try {{
    var r = await fetch('/post', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{url:url, mode:'original'}})}});
    var d = await r.json();
    box.innerHTML = '<div class="draft">'+(d.post||'').replace(/\\n/g,'<br>')+'</div><button onclick="navigator.clipboard.writeText(this.previousElementSibling.innerText)">Copy</button>';
  }} catch(e) {{ box.innerHTML = 'Something went wrong.'; }}
}}
</script>
<main>{cards}</main>
</body></html>"""
