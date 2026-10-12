"""Benchmark digests: the bundle parser, `pipeline.ingest_text`, the explicit
overwrite method, and the admin page at /admin/reader/digests.

Everything here uses an invented publisher ("Example Benchmarks 2099") and
invented figures. No real third-party report text belongs in this repo.

The skip guards (enrich backfill, forced re-enrich, Reader backfill, purge list,
de-dupe) are in tests/test_digest_guards.py. No test here touches the network:
the page fetch and the Claude enrich call are patched to raise, and the OpenAI
embedding call is replaced with a stub.
"""
import importlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import digests as dg
from linklib import embeddings as embed_mod
from linklib import enrich as enrich_mod
from linklib import extract as extract_mod
from linklib import pipeline
from linklib.db import DIGEST_TAG, Article, Library, normalize_url

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
BASE = "https://example-benchmarks.test/report-2099"


def digest_text(slug="growth", title="Example Benchmarks 2099: Growth", published="2099-03",
                tags="benchmark-digest, saas", summary="Median growth was 90 percent at seed.",
                content="Seed companies grew a median 90 percent.\n\n| Stage | Growth |\n|---|---|\n| Seed | 90 |"):
    lines = [f"TITLE: {title}", f"URL: {BASE}?digest={slug}", "SOURCE: Example Benchmarks 2099",
             f"PUBLISHED: {published}"]
    if tags is not None:
        lines.append(f"TAGS: {tags}")
    lines += [f"SUMMARY: {summary}", "CONTENT:", content]
    return "\n".join(lines)


def bundle(*parts):
    return "\n=====\n".join(parts) + "\n"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Digests are loaded from text. A fetch or a Claude call is a bug."""
    def boom(*a, **k):
        raise AssertionError("network call during a digest load")
    monkeypatch.setattr(extract_mod, "fetch_page", boom)
    monkeypatch.setattr(enrich_mod, "enrich", boom)


@pytest.fixture
def embed_calls(monkeypatch):
    calls = []

    def fake(texts, model=embed_mod.DEFAULT_MODEL):
        calls.append(list(texts))
        vecs = [[(i % 7 + 1) / 10.0] + [0.0] * (embed_mod.EMBED_DIM - 1) for i, _ in enumerate(texts)]
        return embed_mod.EmbedResult(vectors=vecs, input_tokens=5, cost_usd=0.0)

    monkeypatch.setattr(embed_mod, "embed_texts", fake)
    return calls


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_every_field():
    rows, errs = dg.parse_bundle(bundle(digest_text("growth"), digest_text("retention", title="Example Benchmarks 2099: Retention")))
    assert errs == []
    assert [r.index for r in rows] == [1, 2]
    r = rows[0]
    assert r.ok and r.errors == []
    assert r.title == "Example Benchmarks 2099: Growth"
    assert r.url == f"{BASE}?digest=growth"
    assert r.source == "Example Benchmarks 2099"
    assert r.published == "2099-03" and r.published_at == "2099-03-01T00:00:00+00:00"
    assert r.tags == ["benchmark-digest", "saas"]
    assert r.summary == "Median growth was 90 percent at seed."
    assert r.content.startswith("Seed companies grew") and r.content.endswith("| Seed | 90 |")


def test_tags_default_and_always_include_the_digest_tag():
    rows, _ = dg.parse_bundle(digest_text(tags=None))
    assert rows[0].tags == ["benchmark-digest"]
    rows, _ = dg.parse_bundle(digest_text(tags="saas, Growth"))
    assert "benchmark-digest" in rows[0].tags and {"saas", "Growth"} <= set(rows[0].tags)
    rows, _ = dg.parse_bundle(digest_text(tags="Benchmark-Digest, benchmark-digest"))
    assert rows[0].tags.count("benchmark-digest") + rows[0].tags.count("Benchmark-Digest") == 1


def test_windows_line_endings_and_trailing_blank_lines():
    text = bundle(digest_text("a"), digest_text("b", title="Example Benchmarks 2099: B")).replace("\n", "\r\n") + "\r\n\r\n"
    rows, errs = dg.parse_bundle(text)
    assert errs == [] and len(rows) == 2
    assert all(r.ok for r in rows)
    assert "\r" not in rows[0].content and "\r" not in rows[0].summary


@pytest.mark.parametrize("given,expected", [
    ("2099", "2099-01-01T00:00:00+00:00"),
    ("2099-03", "2099-03-01T00:00:00+00:00"),
    ("2099-03-17", "2099-03-17T00:00:00+00:00"),
])
def test_partial_published_dates_expand_to_the_first_day_at_utc_midnight(given, expected):
    assert dg.expand_published(given) == expected


@pytest.mark.parametrize("bad", ["2099-13", "2099-02-30", "March 2099", "99", "2099/03", ""])
def test_published_that_is_not_a_real_date_is_an_error(bad):
    rows, _ = dg.parse_bundle(digest_text(published=bad or "x"))
    assert any("PUBLISHED" in e for e in rows[0].errors)


@pytest.mark.parametrize("key", ["TITLE", "URL", "SOURCE", "PUBLISHED", "SUMMARY"])
def test_a_missing_required_field_names_the_field(key):
    text = "\n".join(l for l in digest_text().split("\n") if not l.startswith(key + ":"))
    rows, _ = dg.parse_bundle(text)
    assert f"Missing {key}." in rows[0].errors


def test_missing_or_empty_content_is_an_error():
    rows, _ = dg.parse_bundle("\n".join(digest_text().split("\n")[:-4]).replace("CONTENT:", ""))
    assert "Missing CONTENT: line." in rows[0].errors
    rows, _ = dg.parse_bundle(digest_text(content="   "))
    assert "CONTENT is empty." in rows[0].errors


def test_a_multi_line_summary_or_unknown_header_is_an_error_not_silently_dropped():
    text = digest_text().replace("SUMMARY: Median growth was 90 percent at seed.",
                                 "SUMMARY: Median growth was 90 percent.\nand a second line")
    rows, _ = dg.parse_bundle(text)
    assert any("Unrecognized header line" in e for e in rows[0].errors)
    rows, _ = dg.parse_bundle(digest_text().replace("SOURCE:", "PUBLISHER:"))
    assert any("Unrecognized header line" in e for e in rows[0].errors)


def test_a_repeated_header_is_an_error():
    rows, _ = dg.parse_bundle("TITLE: Example Benchmarks 2099: Again\n" + digest_text())
    assert "TITLE appears twice." in rows[0].errors


def test_duplicate_normalized_urls_in_one_bundle_name_both_titles():
    a = digest_text("growth", title="Example Benchmarks 2099: First")
    b = (digest_text("x", title="Example Benchmarks 2099: Second")
         .replace(f"{BASE}?digest=x", "http://www.example-benchmarks.test/report-2099?digest=growth#frag"))
    rows, _ = dg.parse_bundle(bundle(a, b))
    assert rows[0].ok
    err = " ".join(rows[1].errors)
    assert "Example Benchmarks 2099: First" in err and "Example Benchmarks 2099: Second" in err


def test_empty_and_oversized_bundles_are_bundle_errors():
    assert dg.parse_bundle("")[1] == ["The bundle is empty."]
    assert dg.parse_bundle("\n=====\n  \n")[1]
    rows, errs = dg.parse_bundle("x" * (dg.MAX_BUNDLE_CHARS + 1))
    assert rows == [] and errs


def test_summary_and_content_limits_refuse_and_targets_warn():
    over_s = "s" * (dg.SUMMARY_MAX + 1)
    rows, _ = dg.parse_bundle(digest_text(summary=over_s))
    assert any("Summary is 1,001 characters. The limit is 1,000." in e for e in rows[0].errors)
    rows, _ = dg.parse_bundle(digest_text(summary="s" * dg.SUMMARY_MAX))
    assert rows[0].ok and rows[0].warnings and "Aim for 700." in " ".join(rows[0].warnings)
    rows, _ = dg.parse_bundle(digest_text(summary="s" * dg.SUMMARY_TARGET))
    assert not [w for w in rows[0].warnings if w.startswith("Summary")]

    rows, _ = dg.parse_bundle(digest_text(content="c" * (dg.CONTENT_MAX + 1)))
    assert any("Content is 12,001 characters. The limit is 12,000." in e for e in rows[0].errors)
    rows, _ = dg.parse_bundle(digest_text(content="c" * (dg.CONTENT_TARGET + 1)))
    assert rows[0].ok and any(w == "Content: 6,001 characters. Aim for 6,000." for w in rows[0].warnings)
    rows, _ = dg.parse_bundle(digest_text(content="c" * dg.CONTENT_TARGET))
    assert not [w for w in rows[0].warnings if w.startswith("Content:")]


def test_the_embedding_cap_is_named_in_an_amber_note():
    rows, _ = dg.parse_bundle(digest_text(content="c" * 9000))
    assert rows[0].ok
    assert any("8,000" in w and "embeds only" in w for w in rows[0].warnings)
    rows, _ = dg.parse_bundle(digest_text(content="c" * 1000))
    assert not any("embeds only" in w for w in rows[0].warnings)


def test_a_title_without_a_four_digit_year_warns_but_does_not_block():
    rows, _ = dg.parse_bundle(digest_text(title="Example Benchmarks: Growth"))
    assert rows[0].ok
    assert any("no 4-digit year" in w for w in rows[0].warnings)
    rows, _ = dg.parse_bundle(digest_text(title="Example Benchmarks 2099: Growth"))
    assert not any("no 4-digit year" in w for w in rows[0].warnings)


def test_a_url_without_the_digest_slug_warns():
    rows, _ = dg.parse_bundle(digest_text().replace("?digest=growth", ""))
    assert rows[0].ok and any("?digest=" in w for w in rows[0].warnings)


# ---------------------------------------------------------------------------
# URL identity
# ---------------------------------------------------------------------------

def test_digest_param_survives_normalization_but_fragment_and_tracking_do_not():
    assert normalize_url(f"{BASE}?digest=growth") != normalize_url(f"{BASE}?digest=retention")
    assert normalize_url(f"{BASE}?digest=growth#digest-growth") == normalize_url(f"{BASE}?digest=growth")
    assert (normalize_url("http://www.example-benchmarks.test/report-2099/?utm_source=x&source=y&ref=z&amp=1&digest=growth")
            == "https://example-benchmarks.test/report-2099?digest=growth")


def test_two_digests_of_one_report_stay_two_rows(lib, embed_calls):
    a = pipeline.ingest_text(lib, url=f"{BASE}?digest=growth", title="Example Benchmarks 2099: Growth",
                             content="Growth body.", summary="Growth summary.", source="Example Benchmarks 2099")
    b = pipeline.ingest_text(lib, url=f"{BASE}?digest=retention", title="Example Benchmarks 2099: Retention",
                             content="Retention body.", summary="Retention summary.", source="Example Benchmarks 2099")
    assert a["id"] != b["id"] and a["created"] and b["created"]
    assert lib.count() == 2


# ---------------------------------------------------------------------------
# ingest_text and the overwrite method
# ---------------------------------------------------------------------------

def _load(lib, **over):
    args = dict(url=f"{BASE}?digest=growth", title="Example Benchmarks 2099: Growth",
                content="Seed companies grew a median 90 percent.", summary="Median growth was 90 percent.",
                source="Example Benchmarks 2099", tags=["saas"], published_at="2099-03-01T00:00:00+00:00")
    args.update(over)
    return pipeline.ingest_text(lib, **args)


def test_ingest_text_stores_the_row_as_written_with_no_fetch_and_no_claude(lib, embed_calls):
    out = _load(lib)
    assert out["created"] and out["embedded"]
    a = lib.get_article(out["id"])
    assert (a["enriched"], a["in_scope"], a["is_own_content"], a["needs_content_check"]) == (1, 1, 0, 0)
    assert a["tags"] == ["benchmark-digest", "saas"]
    assert a["published_at"] == "2099-03-01T00:00:00+00:00"
    assert a["source"] == "Example Benchmarks 2099" and a["saved_at"]
    assert a["summary"] == "Median growth was 90 percent." and a["content"].startswith("Seed companies")
    assert a["url"] == "https://example-benchmarks.test/report-2099?digest=growth"
    row = lib.conn.execute("SELECT tags_json, tags_text FROM articles WHERE id=?", (out["id"],)).fetchone()
    assert json.loads(row["tags_json"]) == ["benchmark-digest", "saas"]
    assert row["tags_text"] == "benchmark-digest saas"


def test_text_is_stored_exactly_with_no_voice_normalization(lib, embed_calls):
    spaced = "Median growth — 90 percent — at seed & beyond."
    out = _load(lib, summary=spaced)
    assert lib.get_article(out["id"])["summary"] == spaced


def test_a_new_digest_is_searchable_by_keyword_through_the_fts_triggers(lib, embed_calls):
    out = _load(lib, content="Zanzibar cohort retention reached 101 percent.")
    hits = lib.search("zanzibar")
    assert [h["id"] for h in hits] == [out["id"]]


def test_a_new_digest_is_in_the_vector_index_and_the_ledger(lib, embed_calls):
    out = _load(lib)
    assert len(embed_calls) == 1
    assert lib.embedding_content_hash(out["id"]) == embed_mod.content_hash(embed_mod.document_text(lib.get_article(out["id"])))
    if lib.vector_search_available():
        hit = lib.vector_search([0.1] + [0.0] * (embed_mod.EMBED_DIM - 1), limit=3)
        assert out["id"] in [h["id"] for h in hit]


def test_upsert_alone_cannot_correct_a_digest(lib, embed_calls):
    """Why overwrite_article_text exists: upsert keeps the old content."""
    out = _load(lib, content="Old body.", summary="Old summary.")
    lib.upsert(Article(url=f"{BASE}?digest=growth", title="x", content="New body.", summary="New summary."))
    a = lib.get_article(out["id"])
    assert a["content"] == "Old body." and a["summary"] == "Old summary."


def test_loading_the_same_url_again_overwrites_and_re_embeds(lib, embed_calls):
    first = _load(lib, content="Old body.", summary="Old summary.", tags=["saas"])
    lib.conn.execute("UPDATE articles SET content_html='<p>stale</p>', needs_content_check=1 WHERE id=?", (first["id"],))
    lib.conn.commit()
    old_hash = lib.embedding_content_hash(first["id"])
    second = _load(lib, title="Example Benchmarks 2099: Growth (corrected)", content="New body.",
                   summary="New summary.", tags=["growth"], published_at="2099-04-01T00:00:00+00:00")
    assert second["id"] == first["id"] and not second["created"] and second["embedded"]
    a = lib.get_article(first["id"])
    assert a["title"].endswith("(corrected)")
    assert (a["summary"], a["content"]) == ("New summary.", "New body.")
    assert a["tags"] == ["benchmark-digest", "growth"]
    assert a["published_at"] == "2099-04-01T00:00:00+00:00"
    assert (a["content_html"], a["needs_content_check"], a["enriched"]) == ("", 0, 1)
    assert lib.count() == 1
    assert len(embed_calls) == 2 and lib.embedding_content_hash(first["id"]) != old_hash
    assert [h["id"] for h in lib.search("corrected")] == [first["id"]]
    assert lib.search("stale") == []


def test_overwriting_an_unchanged_digest_does_not_pay_for_a_second_embedding(lib, embed_calls):
    _load(lib)
    out = _load(lib)
    assert out["embedded"] and not out["created"]
    assert len(embed_calls) == 1


def test_an_existing_article_that_is_not_a_digest_is_never_overwritten(lib, embed_calls):
    aid = lib.upsert(Article(url=f"{BASE}?digest=growth", title="A real saved article", content="Mine.", summary="Mine."))
    with pytest.raises(ValueError, match="not a digest"):
        _load(lib)
    a = lib.get_article(aid)
    assert (a["title"], a["content"]) == ("A real saved article", "Mine.")
    assert embed_calls == []


def test_overwrite_method_raises_for_a_missing_article(lib):
    with pytest.raises(KeyError):
        lib.overwrite_article_text(999, title="t", summary="s", content="c", source="x", tags=[], published_at=None)


def test_saved_not_embedded_when_the_embedding_call_fails(lib, monkeypatch):
    monkeypatch.setattr(embed_mod, "embed_texts", lambda texts, model=embed_mod.DEFAULT_MODEL: None)
    out = _load(lib)
    assert out["created"] and out["embedded"] is False
    assert lib.get_article(out["id"]) is not None
    assert lib.embedding_content_hash(out["id"]) is None
    assert [h["id"] for h in lib.search("growth")] == [out["id"]]    # keyword search still works


def test_a_failed_re_embed_after_an_overwrite_reports_not_embedded(lib, embed_calls, monkeypatch):
    first = _load(lib)
    monkeypatch.setattr(embed_mod, "embed_texts", lambda texts, model=embed_mod.DEFAULT_MODEL: None)
    out = _load(lib, content="Changed body.")
    assert out["id"] == first["id"] and out["embedded"] is False


def test_ingest_text_always_carries_the_digest_tag(lib, embed_calls):
    out = _load(lib, tags=[])
    assert DIGEST_TAG in lib.get_article(out["id"])["tags"]


# ---------------------------------------------------------------------------
# The admin page
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path, embed_calls):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetok")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    if login:
        client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return client


def _count(appmod):
    lib = appmod._lib()
    try:
        return lib.count()
    finally:
        lib.close()


def test_signed_out_get_redirects_and_the_save_token_gets_401(env):
    with _client(env, login=False) as c:
        r = c.get("/admin/reader/digests", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/admin/reader/digests", headers={"X-Save-Token": "savetok"},
                     follow_redirects=False).status_code == 401
        for path in ("preview", "load"):
            assert c.post(f"/admin/reader/digests/{path}", data={"bundle": digest_text()},
                          follow_redirects=False).status_code == 401
            assert c.post(f"/admin/reader/digests/{path}", data={"bundle": digest_text()},
                          headers={"X-Save-Token": "savetok"}, follow_redirects=False).status_code == 401
    assert _count(env) == 0


def test_form_page_has_the_format_reference_below_the_form_and_uses_the_standard_width(env):
    with _client(env) as c:
        r = c.get("/admin/reader/digests")
    assert r.status_code == 200
    html = r.text
    assert 'class="page page-standard"' in html
    assert "<h1>Benchmark digests</h1>" in html
    assert html.index("<form") < html.index("Bundle format")
    assert "max-width:900px;margin:0 auto" in html
    assert "Example Benchmarks 2099" in html and "=====" in html
    assert f"{dg.SUMMARY_TARGET:,}" in html and f"{dg.CONTENT_MAX:,}" in html


def test_preview_writes_nothing_and_shows_new_overwrite_error_and_amber_rows(env):
    lib = env._lib()
    try:
        pipeline.ingest_text(lib, url=f"{BASE}?digest=retention", title="Example Benchmarks 2099: Retention",
                             content="x", summary="y", source="Example Benchmarks 2099")
        lib.upsert(Article(url=f"{BASE}?digest=sales", title="Saved", content="mine"))
        before = lib.count()
    finally:
        lib.close()
    text = bundle(
        digest_text("growth", title="Example Benchmarks: Growth", summary="s" * 800),     # new, amber
        digest_text("retention", title="Example Benchmarks 2099: Retention"),              # overwrite
        digest_text("sales", title="Example Benchmarks 2099: Sales"),                       # not a digest
        digest_text("bad", title="Example Benchmarks 2099: Bad", published="soon"),         # parse error
    )
    with _client(env) as c:
        r = c.post("/admin/reader/digests/preview", data={"bundle": text})
    assert r.status_code == 200
    html = r.text
    assert _count(env) == before
    assert "Will overwrite article #1" in html
    assert "already uses this URL and is not a digest" in html
    assert "PUBLISHED must be" in html
    assert "no 4-digit year" in html and "Aim for 700." in html
    for col in ("Title", "URL", "Published", "Summary chars", "Content chars", "Status"):
        assert f">{col}</th>" in html
    assert "Load 4 digests" not in html and "/admin/reader/digests/load" not in html   # errors: no load button
    assert "Nothing loads until every row is clean" in html


def test_preview_of_a_clean_bundle_offers_the_load_button_with_the_count(env):
    text = bundle(digest_text("growth"), digest_text("retention", title="Example Benchmarks 2099: Retention"))
    with _client(env) as c:
        r = c.post("/admin/reader/digests/preview", data={"bundle": text})
    assert "Load 2 digests" in r.text
    assert 'action="/admin/reader/digests/load"' in r.text
    assert _count(env) == 0


def test_preview_accepts_a_file_and_refuses_file_plus_text(env):
    text = bundle(digest_text("growth"))
    with _client(env) as c:
        r = c.post("/admin/reader/digests/preview",
                   files={"file": ("digests.md", text.encode("utf-8"), "text/markdown")})
        assert r.status_code == 200 and "Load 1 digest" in r.text
        r = c.post("/admin/reader/digests/preview", data={"bundle": text},
                   files={"file": ("digests.md", text.encode("utf-8"), "text/markdown")})
        assert r.status_code == 400 and "not both" in r.text
        r = c.post("/admin/reader/digests/preview", data={"bundle": ""})
        assert r.status_code == 400 and "empty" in r.text
    assert _count(env) == 0


def test_load_saves_every_digest_audits_it_and_overwrites_on_a_second_load(env):
    text = bundle(digest_text("growth"), digest_text("retention", title="Example Benchmarks 2099: Retention"))
    with _client(env) as c:
        r = c.post("/admin/reader/digests/load", data={"bundle": text})
        assert r.status_code == 200
        assert r.text.count(">Loaded") == 2 and "2 of 2 digests saved" in r.text
        assert _count(env) == 2
        r2 = c.post("/admin/reader/digests/load", data={"bundle": text})
        assert r2.text.count("Overwrote #") == 2 and _count(env) == 2
    lib = env._lib()
    try:
        rows = lib.list_archive_audit_log()
        assert [x["action"] for x in rows] == ["add", "add"]
        newest = rows[0]["detail"]
        assert newest.startswith("digest load: 0 new, 2 overwritten;")
        assert f"{BASE}?digest=growth" in newest and "digest=retention" in newest
        oldest = rows[1]["detail"]
        assert oldest.startswith("digest load: 2 new, 0 overwritten;")
        assert rows[0]["admin_id"] is not None and rows[0]["created_at"]
    finally:
        lib.close()


def test_load_refuses_the_whole_bundle_when_any_row_has_an_error(env):
    text = bundle(digest_text("growth"), digest_text("bad", title="Example Benchmarks 2099: Bad", published="soon"))
    with _client(env) as c:
        r = c.post("/admin/reader/digests/load", data={"bundle": text})
    assert r.status_code == 400
    assert "Nothing was loaded" in r.text
    assert _count(env) == 0
    lib = env._lib()
    try:
        assert lib.list_archive_audit_log() == []
    finally:
        lib.close()


def test_load_reports_saved_not_embedded_and_still_saves(env, monkeypatch):
    monkeypatch.setattr(embed_mod, "embed_texts", lambda texts, model=embed_mod.DEFAULT_MODEL: None)
    with _client(env) as c:
        r = c.post("/admin/reader/digests/load", data={"bundle": bundle(digest_text("growth"))})
    assert r.status_code == 200
    assert "Saved, not embedded" in r.text and "scripts.embed_backfill --db /data/library.db" in r.text
    assert _count(env) == 1


def test_a_failure_mid_load_stops_and_says_so(env, monkeypatch):
    real = pipeline.ingest_text
    seen = []

    def flaky(lib, **kw):
        seen.append(kw["url"])
        if len(seen) == 2:
            raise RuntimeError("disk full")
        return real(lib, **kw)

    monkeypatch.setattr(pipeline, "ingest_text", flaky)
    text = bundle(digest_text("a", title="Example Benchmarks 2099: A"), digest_text("b", title="Example Benchmarks 2099: B"),
                  digest_text("c", title="Example Benchmarks 2099: C"))
    with _client(env) as c:
        r = c.post("/admin/reader/digests/load", data={"bundle": text})
    assert "Failed" in r.text and "disk full" in r.text and "Not loaded" in r.text
    assert "1 of 3 digests saved" in r.text and len(seen) == 2


def test_the_hub_card_is_in_the_reader_group_and_the_menu_loads_collapsed(env):
    assert "/admin/reader/digests" in {h for h, _, _ in env._LIBRARY_TOOLS}
    assert env.hub_nav_orphans() == []
    with _client(env) as c:
        html = c.get("/admin").text
    assert 'href="/admin/reader/digests"' in html
    assert "<details open" not in html


def test_the_forced_enrich_job_in_the_app_skips_digests(env, monkeypatch):
    lib = env._lib()
    try:
        lib.upsert(Article(url="https://blog.test/ordinary", title="Ordinary", content="body body", enriched=False))
        pipeline.ingest_text(lib, url=f"{BASE}?digest=growth", title="Example Benchmarks 2099: Growth",
                             content="x", summary="Digest summary.", source="Example Benchmarks 2099")
    finally:
        lib.close()
    seen = []
    monkeypatch.setattr(enrich_mod, "enrich", lambda title, text, **kw: seen.append(title))
    env._enrich_job(True, "claude-test", 1000)
    assert seen == ["Ordinary"]
    assert env._job_get("enrich")["total"] == 1
    lib = env._lib()
    try:
        assert lib.get_article_by_url(f"{BASE}?digest=growth")["summary"] == "Digest summary."
    finally:
        lib.close()


def test_the_reader_opens_a_digest_from_its_stored_text_without_fetching(env):
    lib = env._lib()
    try:
        out = pipeline.ingest_text(lib, url=f"{BASE}?digest=growth", title="Example Benchmarks 2099: Growth",
                                   content="Stored digest text. " * 30, summary="s", source="Example Benchmarks 2099",
                                   published_at="2099-03-01T00:00:00+00:00")
    finally:
        lib.close()
    resolved = env._resolve_reader_content(id=out["id"])   # fetch_page raises (autouse), so a live fetch would fail the test
    assert resolved is not None
    assert "Stored digest text." in resolved["body_html"] and resolved["content_via"] == "cache"
    assert resolved["published_at"].startswith("2099-03-01")
    assert env._reader_fmt_date(resolved["published_at"]) == "1 Mar 2099"
