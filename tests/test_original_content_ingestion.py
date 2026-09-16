"""FP&A Buddy published-content ingestion (2026-09): mirroring
original_content pieces into articles for retrieval, the is_own_content
provenance flag, its citation-label-only mechanism, and its non-interference
with RRF ranking. See CLAUDE.md's "FP&A Buddy Published-Content Ingestion"
entry for the full design writeup.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib.db import Article, Library
from linklib.original_content_sync import (
    mirrored_article_url,
    plain_text_from_body_md,
    sync_original_content_article,
)
from linklib.pipeline import ingest_url


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


# --- plain_text_from_body_md: HTML-stripping ---------------------------------

def test_strips_markdown_formatting_to_plain_text():
    text = plain_text_from_body_md("# Heading\n\nSome **bold** and *italic* text.")
    assert "Heading" in text
    assert "Some bold and italic text." in text
    assert "#" not in text and "**" not in text and "<" not in text


def test_strips_raw_html_blocks_the_permissive_renderer_allows():
    # original_content.body_md allows raw HTML passthrough (the ported
    # flagship pieces use it for .ns-table/.ger-pull/.fah-* blocks) — the
    # stripper has to handle whatever HTML that raw pass-through produces,
    # not just what python-markdown itself emits.
    body = (
        "Intro paragraph.\n\n"
        '<div class="ns-table"><table><tr><td>Cell one</td><td>Cell two</td></tr></table></div>\n\n'
        "<style>.ns-table{color:red;}</style>\n"
        "<script>alert('x')</script>\n"
    )
    text = plain_text_from_body_md(body)
    assert "Intro paragraph." in text
    assert "Cell one" in text and "Cell two" in text
    assert "<" not in text and ">" not in text
    # style/script text content must never leak into indexed prose
    assert "color:red" not in text
    assert "alert" not in text


def test_adjacent_block_elements_dont_run_words_together():
    text = plain_text_from_body_md("<table><tr><td>one</td><td>two</td></tr></table>")
    assert "onetwo" not in text


def test_empty_body_md_yields_empty_text():
    assert plain_text_from_body_md("") == ""
    assert plain_text_from_body_md(None) == ""


def test_mirrored_article_url_is_canonical_thought_leadership_path(monkeypatch):
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib
    import linklib.original_content_sync as ocs
    importlib.reload(ocs)
    try:
        assert ocs.mirrored_article_url("my-piece") == "https://bmweis.com/thought-leadership/my-piece"
    finally:
        importlib.reload(ocs)


# --- sync_original_content_article: create / re-sync / clear -----------------

def test_sync_creates_mirrored_article_flagged_as_own_content(lib):
    item_id = lib.add_original_content(
        "my-piece", "My Piece", "A teaser", "Guide", "Read it",
        body_md="# My Piece\n\nSome real content about FP&A.",
        status="live",
    )
    article_id = sync_original_content_article(lib, item_id)
    assert article_id is not None

    article = lib.get_article(article_id)
    assert article["is_own_content"] == 1
    assert article["url"] == mirrored_article_url("my-piece")
    assert article["title"] == "My Piece"
    assert "Some real content about FP&A." in article["content"]

    row = lib.get_original_content(item_id)
    assert row["mirrored_article_id"] == article_id


def test_sync_is_a_noop_for_card_metadata_only_rows(lib):
    # body_md IS NULL (default) — one of the three bespoke routes renders
    # the real piece; nothing to mirror.
    item_id = lib.add_original_content(
        "bespoke-piece", "Bespoke", "Teaser", "Guide", "Read it",
        body_md=None, status="live",
    )
    assert sync_original_content_article(lib, item_id) is None
    assert lib.get_original_content(item_id)["mirrored_article_id"] is None


def test_resync_on_edit_overwrites_never_merges(lib):
    item_id = lib.add_original_content(
        "my-piece", "Original Title", "Teaser", "Guide", "Read it",
        body_md="Original body text.", status="live",
    )
    article_id = sync_original_content_article(lib, item_id)
    original_content = lib.get_article(article_id)["content"]
    assert "Original body text." in original_content

    # Simulate an edit: title AND body_md change.
    lib.update_original_content(
        item_id, "my-piece", "New Title", "Teaser", "Guide", "Read it",
        body_md="Completely new body text.", status="live",
        featured_home=False, date_label="", sort_key="", display_order=0,
    )
    same_article_id = sync_original_content_article(lib, item_id)
    assert same_article_id == article_id   # same mirror, not a new row

    article = lib.get_article(article_id)
    assert article["title"] == "New Title"
    assert "Completely new body text." in article["content"]
    # Library.upsert()'s merge-keeps-existing-content behavior must NOT
    # apply here — a deliberate edit always wins.
    assert "Original body text." not in article["content"]


def test_clearing_body_md_deletes_the_mirror(lib):
    item_id = lib.add_original_content(
        "my-piece", "Title", "Teaser", "Guide", "Read it",
        body_md="Some body.", status="draft",
    )
    article_id = sync_original_content_article(lib, item_id)
    assert lib.get_article(article_id) is not None

    lib.update_original_content(
        item_id, "my-piece", "Title", "Teaser", "Guide", "Read it",
        body_md=None, status="draft",
        featured_home=False, date_label="", sort_key="", display_order=0,
    )
    result = sync_original_content_article(lib, item_id)
    assert result is None
    assert lib.get_article(article_id) is None   # cascaded delete
    assert lib.get_original_content(item_id)["mirrored_article_id"] is None


def test_sync_adopts_a_stray_pre_existing_article_at_the_same_url(lib):
    url = mirrored_article_url("my-piece")
    stray_id = lib.upsert(Article(url=url, title="Stray old save", content="stale stray content"))

    item_id = lib.add_original_content(
        "my-piece", "Real Title", "Teaser", "Guide", "Read it",
        body_md="Real mirrored content.", status="live",
    )
    article_id = sync_original_content_article(lib, item_id)
    assert article_id == stray_id   # adopted, not a duplicate/second row

    article = lib.get_article(article_id)
    assert article["title"] == "Real Title"
    assert "Real mirrored content." in article["content"]
    assert "stale stray content" not in article["content"]
    assert article["is_own_content"] == 1


# --- provenance flag: generic URL-match logic (future bookmarklet saves) ----

def test_is_thought_leadership_url_matches_normalized(lib):
    lib.add_thought_leadership("writing", "External Piece", "https://example.com/post/", "Venue")
    assert lib.is_thought_leadership_url("https://example.com/post") is True   # trailing slash normalized
    assert lib.is_thought_leadership_url("https://example.com/other") is False


def test_ingest_url_flags_own_content_on_thought_leadership_match(lib, monkeypatch):
    lib.add_thought_leadership("writing", "External Piece", "https://example.com/post", "Venue")

    def _fake_fetch_page(url):
        from linklib.extract import PageData
        return PageData(title="External Piece", content="Some real content here.")

    import linklib.extract as extract_mod
    monkeypatch.setattr(extract_mod, "fetch_page", _fake_fetch_page)

    row = ingest_url(lib, "https://example.com/post", do_enrich=False, fetch_fulltext=True)
    article = lib.get_article(row["id"])
    assert article["is_own_content"] == 1


def test_ingest_url_does_not_flag_ordinary_saves(lib, monkeypatch):
    lib.add_thought_leadership("writing", "External Piece", "https://example.com/post", "Venue")

    def _fake_fetch_page(url):
        from linklib.extract import PageData
        return PageData(title="Some Other Article", content="Unrelated content.")

    import linklib.extract as extract_mod
    monkeypatch.setattr(extract_mod, "fetch_page", _fake_fetch_page)

    row = ingest_url(lib, "https://unrelated.example.com/x", do_enrich=False, fetch_fulltext=True)
    article = lib.get_article(row["id"])
    assert article["is_own_content"] == 0


def test_set_article_own_content_never_auto_clears(lib):
    article_id = lib.upsert(Article(url="https://ex.com/a", title="A"))
    lib.set_article_own_content(article_id, True)
    # A later ordinary re-save (upsert merge) must not touch the flag either
    # way — nothing in upsert() reads/writes is_own_content.
    lib.upsert(Article(url="https://ex.com/a", title="A", content="more"))
    assert lib.get_article(article_id)["is_own_content"] == 1


# --- citation labeling: flows through _build_source_documents + extraction --

def test_own_content_hit_gets_labeled_in_sent_docs():
    hits = [{"id": 1, "title": "My Piece", "url": "https://x/piece",
             "summary": "", "content": "real content", "is_own_content": 1}]
    blocks, sent = agent._build_source_documents(hits, [])
    assert sent[0]["own_content"] is True


def test_non_own_content_hit_has_no_own_content_key():
    hits = [{"id": 1, "title": "Someone Else's Piece", "url": "https://x/other",
             "summary": "", "content": "real content"}]
    blocks, sent = agent._build_source_documents(hits, [])
    assert "own_content" not in sent[0]


def test_extract_citations_carries_own_content_through():
    from linklib.citations import extract_citations

    class _Block:
        def __init__(self, text, citations=None):
            self.type = "text"
            self.text = text
            self.citations = citations

    class _DocCit:
        def __init__(self, document_index):
            self.type = "char_location"
            self.document_index = document_index
            self.cited_text = "…"

    sent = [{"title": "My Piece", "url": "https://x/piece", "type": "library",
             "article_id": 1, "own_content": True}]
    blocks = [_Block("As shown.", citations=[_DocCit(0)])]
    text, cites = extract_citations(blocks, sent)
    assert cites == [{"n": 1, "title": "My Piece", "url": "https://x/piece",
                       "type": "library", "article_id": 1, "own_content": True}]


def test_extract_citations_omits_own_content_key_when_false():
    from linklib.citations import extract_citations

    class _Block:
        def __init__(self, text, citations=None):
            self.type = "text"
            self.text = text
            self.citations = citations

    class _DocCit:
        def __init__(self, document_index):
            self.type = "char_location"
            self.document_index = document_index
            self.cited_text = "…"

    sent = [{"title": "Someone Else's", "url": "https://x/other", "type": "library"}]
    blocks = [_Block("As shown.", citations=[_DocCit(0)])]
    text, cites = extract_citations(blocks, sent)
    assert "own_content" not in cites[0]


# --- RRF non-interference: the provenance flag is invisible to ranking ------

def test_rrf_merge_ignores_own_content_flag_entirely():
    # A worse-ranked own-content item must not be promoted over a
    # better-ranked non-own item — _rrf_merge only ever reads rank position.
    own = {"id": 1, "title": "Own piece", "is_own_content": 1}
    better = {"id": 2, "title": "Better match"}
    fts = [better, own]     # better ranks first in both lists
    vec = [better, own]
    merged = agent._rrf_merge([fts, vec], limit=10)
    assert [m["id"] for m in merged] == [2, 1]   # better still wins


def test_retrieve_does_not_boost_own_content_articles(lib, monkeypatch):
    monkeypatch.setattr(lib, "_vec_available", False)
    # "better" matches the query more strongly (title match) than "own"
    # (only a tags/summary match) — is_own_content must not change that.
    lib.upsert(Article(url="https://ex.com/own", title="Unrelated title",
                       summary="budget variance analysis", tags=["fpna"]))
    own_id = [r["id"] for r in lib.search("") if r["url"] == "https://ex.com/own"][0]
    lib.set_article_own_content(own_id, True)
    lib.upsert(Article(url="https://ex.com/better", title="budget variance analysis deep dive",
                       summary="budget variance analysis"))

    hits, _, _ = agent.retrieve(lib, "budget variance analysis", max_sources=5)
    urls = [h["url"] for h in hits]
    assert urls.index("https://ex.com/better") < urls.index("https://ex.com/own")


# --- admin routes: create / edit / delete wire the sync hook ---------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "app.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


VALID_FORM = {
    "title": "A Test Piece",
    "slug": "a-test-piece",
    "teaser": "A teaser",
    "tag_label": "Guide",
    "link_label": "Read it",
    "date_label": "Jan 2027",
    "body_md": "# Hi\n\nReal body content for retrieval.",
    "status": "live",
    "featured_home": "1",
    "display_order": "",
}


def test_admin_create_route_mirrors_into_articles(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
        assert row["mirrored_article_id"] is not None
        article = lib.get_article(row["mirrored_article_id"])
        assert article["is_own_content"] == 1
        assert "Real body content for retrieval." in article["content"]
    finally:
        lib.close()


def test_admin_edit_route_resyncs_mirror(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()

    edited = dict(VALID_FORM, body_md="# Hi\n\nEdited content, brand new text.")
    c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edited, follow_redirects=False)

    lib = env._lib()
    try:
        row = lib.get_original_content(item_id)
        article = lib.get_article(row["mirrored_article_id"])
        assert "Edited content, brand new text." in article["content"]
        assert "Real body content for retrieval." not in article["content"]
    finally:
        lib.close()


def test_admin_delete_route_cascades_the_mirror(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
        item_id, article_id = row["id"], row["mirrored_article_id"]
    finally:
        lib.close()

    c.post(f"/admin/thought-leadership/original/{item_id}/delete", follow_redirects=False)

    lib = env._lib()
    try:
        assert lib.get_original_content(item_id) is None
        assert lib.get_article(article_id) is None   # no dead data
    finally:
        lib.close()


# --- integration: Buddy retrieves and cites a mirrored piece ----------------

def test_ask_retrieves_and_cites_own_content_piece(lib, monkeypatch):
    item_id = lib.add_original_content(
        "growth-engine-ratio", "The Growth Engine Ratio", "Teaser", "Framework", "Read it",
        body_md="# The Growth Engine Ratio\n\nA framework for GTM efficiency and burn multiple analysis.",
        status="live",
    )
    article_id = sync_original_content_article(lib, item_id)
    assert article_id is not None

    lib.set_setting("voice_core", "CUSTOM CORE VOICE")
    lib.set_setting("voice_fpa_buddy", "CUSTOM FPA BUDDY VOICE")
    monkeypatch.setattr("linklib.agent._web_provider", lambda lib: "native")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _Usage:
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class _Cit:
        def __init__(self, document_index):
            self.type = "char_location"
            self.document_index = document_index
            self.cited_text = "…"

    class _RespBlock:
        def __init__(self, text, citations=None):
            self.type = "text"
            self.text = text
            self.citations = citations

    class _Resp:
        content = [_RespBlock("The growth engine ratio measures GTM efficiency.", citations=[_Cit(0)])]
        usage = _Usage()
        stop_reason = "end_turn"

    class _FakeMessages:
        def create(self, **kwargs):
            # Confirm the mirrored article's grounding text actually made it
            # into the document blocks sent to the model.
            docs = [b for b in kwargs["messages"][-1]["content"] if isinstance(b, dict) and b.get("type") == "document"]
            assert any("GTM efficiency" in d["source"]["data"] for d in docs)
            return _Resp()

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(agent, "_get_client", lambda: _FakeClient())

    ans = agent.answer_question(
        lib, "What is the growth engine ratio?",
        use_library=True, use_feed=False, use_web=False,
    )
    assert ans.citations
    assert ans.citations[0]["own_content"] is True
    assert ans.citations[0]["article_id"] == article_id
    assert "[1]" in ans.text


# --- list_unmirrored_original_content: the /admin/checks invariant ----------
# Backs webapp.checks.original_content_mirror_problems() (2026-09) — the
# safety net for the exact gap this incident exposed: a write via any path
# other than the two admin save routes (a migration script, a future bulk
# edit) can leave a live row with real body_md and no working mirror, with
# nothing surfacing it. See CLAUDE.md's Published-Content Ingestion entry
# for the full write-up of why the sync stays in the routes rather than
# moving into Library.update_original_content() itself.

def test_list_unmirrored_finds_nothing_on_a_freshly_synced_row(lib):
    item_id = lib.add_original_content(
        "synced-piece", "Title", "Teaser", "Guide", "Read it",
        body_md="Real body.", status="live",
    )
    sync_original_content_article(lib, item_id)
    assert lib.list_unmirrored_original_content() == []


def test_list_unmirrored_flags_a_row_written_around_the_sync(lib):
    """Simulates exactly what scripts/archive/migrate_hackathon_playbook_content.py
    and scripts/archive/migrate_netsuite_mcp_content.py actually did: a direct
    Library.update_original_content() call with real body_md, never followed
    by sync_original_content_article — the live incident this check exists
    to catch."""
    item_id = lib.add_original_content(
        "bypassed-piece", "Title", "Teaser", "Guide", "Read it",
        body_md=None, status="live",
    )
    row = lib.get_original_content(item_id)
    lib.update_original_content(
        item_id, row["slug"], row["title"], row["teaser"], row["tag_label"],
        row["link_label"], "Body written around the sync.", "live",
        row["featured_home"], row["date_label"], row["sort_key"], row["display_order"],
    )
    # No sync_original_content_article() call — this is the gap.
    unmirrored = lib.list_unmirrored_original_content()
    assert len(unmirrored) == 1
    assert unmirrored[0]["slug"] == "bypassed-piece"
    assert unmirrored[0]["mirrored_article_id"] is None


def test_list_unmirrored_flags_a_dangling_mirrored_article_id(lib):
    """A mirrored_article_id pointing at an articles row that no longer
    exists (e.g. the mirror was deleted out from under it) is exactly as
    broken as a NULL pointer — both mean FP&A Buddy can't retrieve this
    content, so both must flag."""
    item_id = lib.add_original_content(
        "dangling-piece", "Title", "Teaser", "Guide", "Read it",
        body_md="Real body.", status="live",
    )
    article_id = sync_original_content_article(lib, item_id)
    lib.delete_article(article_id)
    # The delete didn't go through the sync's own clear-on-empty-body path,
    # so mirrored_article_id is left dangling — reproducing the shape a bug
    # elsewhere (not this feature) could produce.
    unmirrored = lib.list_unmirrored_original_content()
    assert len(unmirrored) == 1
    assert unmirrored[0]["slug"] == "dangling-piece"


def test_list_unmirrored_ignores_a_body_less_row(lib):
    """A card-metadata-only row (body_md IS NULL, one of the literal bespoke
    routes renders it) is never expected to have a mirror — flagging it
    would be a false positive."""
    lib.add_original_content(
        "metadata-only-piece", "Title", "Teaser", "Guide", "Read it",
        body_md=None, status="live",
    )
    assert lib.list_unmirrored_original_content() == []


def test_admin_checks_surfaces_an_unmirrored_row(monkeypatch, tmp_path):
    """End-to-end: the exact same gap, caught the way an admin would
    actually see it — a red row on /admin/checks, not just a passing
    Library-layer unit test."""
    import importlib
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "checks.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    import webapp.checks as checksmod
    importlib.reload(checksmod)

    db_lib = appmod._lib()
    try:
        item_id = db_lib.add_original_content(
            "bypassed-piece", "Title", "Teaser", "Guide", "Read it",
            body_md=None, status="live",
        )
        row = db_lib.get_original_content(item_id)
        db_lib.update_original_content(
            item_id, row["slug"], row["title"], row["teaser"], row["tag_label"],
            row["link_label"], "Body written around the sync.", "live",
            row["featured_home"], row["date_label"], row["sort_key"], row["display_order"],
        )
    finally:
        db_lib.close()

    results = checksmod.run_all()
    row = next(r for r in results if r["name"] == "Original content mirrored for retrieval")
    assert row["ok"] is False
    assert "bypassed-piece" in row["detail"]
