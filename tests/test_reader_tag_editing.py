"""Phase 5c — tag editing in the Reader: save-time and inline.

Phase 5's merge deliberately dropped every inline management control from the
Reader. This is the one narrow, tags-only exception: an optional tag input on
the Feed "+ Save" action, and a collapsed-by-default tag editor in the reader
pane for articles that are already saved. Delete/archive stay admin-only.

Both write paths reuse machinery that already existed rather than adding a
parallel one — POST /library/{id}/tags (which survived Phase 5 with no callers
once the old Archive page's "Edit tags" button was removed) and POST /feed/save's
already-supported `tags` field. Library.update_tags writes tags_text, so the
articles_au FTS trigger keeps search in sync with no extra work; that's the same
propagation path /admin/reader/tag-management's rename/delete already rely on.

See CLAUDE.md's "Reader tag editing (Phase 5c)" bullet and ARCHITECTURE.md's
Reader section for the full write-up.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


def _seed(appmod, url="https://example.com/wc", title="Working Capital",
          tags=("working capital", "cash")):
    lib = appmod._lib()
    try:
        lib.conn.execute(
            "INSERT INTO articles (url,title,source,summary,content,tags_json,tags_text,"
            "saved_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (url, title, "CFO Weekly", "A summary.", "Body text. " * 60,
             json.dumps(list(tags)), " ".join(tags), "2026-08-01", "2026-08-01", "2026-08-01"),
        )
        lib.conn.commit()
        return lib.conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()[0]
    finally:
        lib.close()


def _tags(appmod, url="https://example.com/wc"):
    lib = appmod._lib()
    try:
        row = lib.conn.execute("SELECT tags_json FROM articles WHERE url=?", (url,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 1. The reader pane's inline tag editor is present and collapsed by default.
# ---------------------------------------------------------------------------

def test_reader_renders_collapsed_tag_editor(env):
    c = _admin_client(env)
    html = c.get("/read?view=saved").text
    # The panel and the toolbar button that opens it are built client-side in
    # rrRenderArticle, so it's the JS that has to carry them.
    assert "rr-tag-panel" in html
    assert "rr-tag-btn" in html
    assert "rrToggleTagPanel()" in html
    assert "RR_ICON_TAG" in html
    # Collapsed by default: .rr-tag-panel is display:none until .rr-tag-open is
    # added. If this flips to a default-visible panel it clutters the reading
    # view for anyone not managing tags, which the phase explicitly rules out.
    assert ".rr-tag-panel{display:none;" in html
    assert ".rr-tag-panel.rr-tag-open{display:flex;}" in html


def test_reader_tag_editor_posts_to_the_existing_route(env):
    """No second tag-write endpoint — the editor calls the route that already
    existed (and had no callers) rather than a new parallel implementation."""
    c = _admin_client(env)
    html = c.get("/read").text
    assert "'/library/' + rrCurrent.id + '/tags'" in html


# ---------------------------------------------------------------------------
# 2. Save-time tagging on a Feed row — inline, not window.prompt().
# ---------------------------------------------------------------------------

def test_feed_row_has_inline_save_time_tag_form(env, monkeypatch):
    import linklib.feed as feedmod
    monkeypatch.setattr(feedmod, "get_feed_items", lambda *a, **k: ([{
        "url": "https://example.com/item", "title": "An Item", "source": "CFO Dive",
        "category": "Finance", "published_at": "2026-08-10", "summary": "Summary.",
    }], ["Finance"]))
    c = _admin_client(env)
    html = c.get("/read?view=feed").text
    assert "rr-row-tagform" in html
    assert 'onkeydown="rrRowTagKeydown(event,this)"' in html
    assert 'onclick="rrRowSaveGo(this)"' in html
    # Optional, never required: the form ships a Cancel and the Save button
    # commits with an empty input (covered end-to-end below).
    assert 'onclick="rrRowSaveCancel(this)"' in html


def test_no_prompt_dialogs_left_in_the_reader(env):
    """Both save paths used a blocking window.prompt() before this phase. That
    is not an inline input, and it can't offer autocomplete against the
    vocabulary. Comments are stripped first — the code that explains the change
    naturally names the thing it replaced."""
    import re
    c = _admin_client(env)
    html = c.get("/read").text
    code = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "prompt(" not in code


def test_save_time_tags_are_optional_and_persist(env):
    """Empty tags must not block the save; supplied tags must land on the row."""
    c = _admin_client(env)
    import linklib.pipeline as pipeline

    def fake_ingest(lib, url, tags=None, **kw):
        lib.conn.execute(
            "INSERT INTO articles (url,title,tags_json,tags_text,saved_at,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (url, "T", json.dumps(sorted(set(tags or []))), " ".join(sorted(set(tags or []))),
             "2026-08-01", "2026-08-01", "2026-08-01"),
        )
        lib.conn.commit()
        row = lib.conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()
        return {"id": row[0]}

    import webapp.app as appmod
    appmod.ingest_url = fake_ingest

    r = c.post("/feed/save", data={"url": "https://example.com/tagged", "tags": "alpha, beta"})
    assert r.status_code == 200
    assert _tags(env, "https://example.com/tagged") == ["alpha", "beta"]

    r = c.post("/feed/save", data={"url": "https://example.com/untagged", "tags": ""})
    assert r.status_code == 200, "an empty tag input must not block the save"
    assert _tags(env, "https://example.com/untagged") == []


def test_feed_save_returns_the_new_article_id(env):
    """The Reader needs the id back to switch into its saved state (and to know
    where to POST subsequent tag edits) without a reload."""
    import webapp.app as appmod

    def fake_ingest(lib, url, tags=None, **kw):
        lib.conn.execute(
            "INSERT INTO articles (url,title,tags_json,tags_text,saved_at,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (url, "T", json.dumps(sorted(set(tags or []))), " ".join(sorted(set(tags or []))),
             "2026-08-01", "2026-08-01", "2026-08-01"),
        )
        lib.conn.commit()
        return {"id": lib.conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()[0]}

    appmod.ingest_url = fake_ingest
    c = _admin_client(env)
    body = c.post("/feed/save", data={"url": "https://example.com/x", "tags": "z"}).json()
    assert body["ok"] is True
    assert isinstance(body["id"], int) and body["id"] > 0
    assert body["tags"] == ["z"]


# ---------------------------------------------------------------------------
# 3. Same tag data as Admin — one vocabulary, one write path, FTS stays in sync.
# ---------------------------------------------------------------------------

def test_reader_tag_write_round_trips_with_admin(env):
    aid = _seed(env)
    c = _admin_client(env)
    r = c.post(f"/library/{aid}/tags", json={"tags": ["roundtrip", "cash"]})
    assert r.status_code == 200
    assert r.json()["tags"] == ["cash", "roundtrip"]
    # Admin's Tag cleanup reads the same all_tags() vocabulary.
    assert "roundtrip" in c.get("/admin/reader/tag-management").text
    # ...and an Admin-side rename is visible to the Reader's own API.
    c.post("/admin/reader/tag-management/tags/rename", data={"old": "roundtrip", "new": "renamed"},
           follow_redirects=False)
    assert "renamed" in c.get(f"/api/read-article?id={aid}").json()["tags"]


def test_reader_tag_write_keeps_fts_in_sync(env):
    """update_tags writes tags_text, so the articles_au trigger reindexes with
    no extra step — the same propagation Admin's rename/delete rely on. No
    re-embed here either: article_embeddings' content hash means the next
    embed_backfill picks up the changed text, matching what those tools do."""
    aid = _seed(env)
    c = _admin_client(env)
    c.post(f"/library/{aid}/tags", json={"tags": ["ftsprobe"]})
    hits = c.get("/api/search", params={"q": "ftsprobe"}).json()["results"]
    assert any(h["id"] == aid for h in hits)


def test_tag_autocomplete_vocabulary_is_present_in_every_view(env):
    """A Feed item can be tagged at save time, so the vocabulary can't be read
    only in the Saved branch the way the Saved filter bar's copy is."""
    _seed(env, tags=("vocabtag",))
    c = _admin_client(env)
    for view in ("feed", "saved", "readlater"):
        html = c.get(f"/read?view={view}").text
        assert '<datalist id="rr-tag-vocab">' in html, view
        assert '<option value="vocabtag"></option>' in html, view
    # Both tag inputs point at it: the reader pane's (built in JS) and the Feed
    # row's save-time form (server-rendered, so it needs a feed item to exist).
    import linklib.feed as feedmod
    monkeypatch_items = [{
        "url": "https://example.com/i", "title": "I", "source": "S",
        "category": "Finance", "published_at": "2026-08-10", "summary": "s",
    }]
    orig = feedmod.get_feed_items
    feedmod.get_feed_items = lambda *a, **k: (monkeypatch_items, ["Finance"])
    try:
        html = c.get("/read?view=feed").text
    finally:
        feedmod.get_feed_items = orig
    assert html.count('list="rr-tag-vocab"') >= 2


# ---------------------------------------------------------------------------
# 4. A Feed item already in the library opens as saved, not as "+ Save" again.
# ---------------------------------------------------------------------------

def test_url_already_in_library_resolves_to_its_saved_row(env, monkeypatch):
    aid = _seed(env, url="https://example.com/known", tags=("kept",))
    import linklib.extract as ex

    class _P:
        title = "Known"
        content = "Body. " * 50
        raw_html = "<article><p>Body.</p></article>"

    monkeypatch.setattr(ex, "fetch_page", lambda url, **k: _P())
    c = _admin_client(env)
    # Opened by url (a Feed row carries no id), it still comes back as saved so
    # the inline tag editor is available instead of a duplicate "+ Save".
    d = c.get("/api/read-article", params={"url": "https://example.com/known"}).json()
    assert d["id"] == aid
    assert d["tags"] == ["kept"]


def test_url_matched_article_prefers_backfilled_content_html(env, monkeypatch):
    """The seam between Phase 5b and 5c, reconciled at merge.

    5c skips the cached `content` on a url match because it's flattened plain
    text. 5b's `content_html` is the opposite: real structured HTML, a strict
    upgrade over both the plain-text cache and a live re-fetch. So it must be
    used even on a url match — skipping it would re-fetch over the network to
    rebuild something already on disk, and lose 5b's quality-checked output.
    """
    _seed(env, url="https://example.com/backfilled", tags=())
    lib = env._lib()
    try:
        lib.conn.execute(
            "UPDATE articles SET content_html=? WHERE url=?",
            ("<p>Backfilled <em>structured</em> body.</p>", "https://example.com/backfilled"),
        )
        lib.conn.commit()
    finally:
        lib.close()

    import linklib.extract as ex
    calls = []

    def _boom(url, **k):
        calls.append(url)
        raise AssertionError("must not live-fetch when content_html is on file")

    monkeypatch.setattr(ex, "fetch_page", _boom)
    c = _admin_client(env)
    d = c.get("/api/read-article", params={"url": "https://example.com/backfilled"}).json()
    assert not calls, "a backfilled row should never trigger a live fetch"
    assert "<em>structured</em>" in d["body_html"]
    assert d["id"], "still resolves as saved, so the tag editor stays available"


def test_url_matched_article_still_uses_live_fetched_structure(env, monkeypatch):
    """A url-matched row with no `content_html` must not fall back to the DB's
    cached plain text — that would silently strip images/links from a Feed item
    that currently reads with them intact (the cached-content gap from the
    Phase 5 follow-up pass, for rows Phase 5b hasn't reached yet)."""
    _seed(env, url="https://example.com/known2", tags=())
    import linklib.extract as ex

    class _P:
        title = "Known"
        content = "Fallback."
        raw_html = '<article><p>Live <a href="/x">link</a></p></article>'

    monkeypatch.setattr(ex, "fetch_page", lambda url, **k: _P())
    c = _admin_client(env)
    d = c.get("/api/read-article", params={"url": "https://example.com/known2"}).json()
    assert "<a href=" in d["body_html"], "should keep live-fetched structure, not cached text"


# ---------------------------------------------------------------------------
# 5. Layout + syntax regression guards for the two live-verified bugs.
# ---------------------------------------------------------------------------

def test_save_time_form_gets_its_own_row_line(env):
    """Live-verification catch: as a plain third flex child of .rr-row the form
    became a third column and squeezed the row's title/meta into a sliver. It
    needs the row to wrap and the form to claim a full line."""
    c = _admin_client(env)
    html = c.get("/read").text
    assert ".rr-row{display:flex;flex-wrap:wrap;" in html
    assert ".rr-row-tagform{display:none;flex:0 0 100%;" in html


def test_tag_input_keeps_a_real_typing_width(env):
    """The input started at flex-basis 130px / min-width 110px, which let a few
    chips squeeze it down to something too narrow to type in. A 220px floor
    makes flex-wrap drop it onto its own line instead, where flex-grow gives it
    the panel's full width. min() keeps that floor from overflowing a container
    narrower than 220px (the reader pane in mobile landscape is ~219px)."""
    c = _admin_client(env)
    html = c.get("/read").text
    assert ".rr-tag-input{flex:1 1 220px;min-width:min(220px,100%);" in html


def test_opening_the_tag_panel_does_not_scroll_it_off_screen(env):
    """Live-verification catch (mobile portrait): focusing the input let the
    browser scroll the document far enough to clip the panel's first chip row
    off the top. preventScroll + an explicit pane anchor is the fix."""
    c = _admin_client(env)
    html = c.get("/read").text
    assert "preventScroll" in html
    assert "pane.scrollIntoView" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_rendered_reader_script_parses_as_valid_js(env):
    """The Reader's <script> is built inline in the route, not as a module-level
    *_JS constant, so webapp.checks.script_syntax_problems() doesn't cover it.
    Check what the browser actually receives — per CLAUDE.md's standing lesson,
    never an approximation reconstructed from app.py's source text."""
    import re
    c = _admin_client(env)
    scripts = re.findall(r"<script>(.*?)</script>", c.get("/read").text, re.S)
    assert scripts, "reader page should carry an inline script block"
    for i, js in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            p = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert p.returncode == 0, f"reader script block {i} failed to parse:\n{p.stderr}"
        finally:
            os.unlink(path)
