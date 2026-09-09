"""Tests for forced re-enrichment (linklib/pipeline.enrich_library force path).

The API call is mocked — we pin the selection logic (force re-runs every row,
not just unenriched ones) and that the chosen model is threaded through.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pipeline
from linklib.db import Article, Library
from linklib.enrich import Enrichment


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _fake_enricher(calls):
    def fake(title, text, known_tags=None, model="?", tag_guide=""):
        calls.append(model)
        return Enrichment(summary=f"NEW::{title}", tags=["opus-tag"])
    return fake


def test_without_force_skips_already_enriched(lib, monkeypatch):
    lib.upsert(Article(url="u1", title="A", content="body one", summary="old", enriched=True))
    lib.upsert(Article(url="u2", title="B", content="body two", enriched=False))
    calls = []
    monkeypatch.setattr(pipeline.enrich_mod, "enrich", _fake_enricher(calls))

    n = pipeline.enrich_library(lib, fetch=False, force=False)
    assert n == 1                      # only the unenriched row
    rows = {r["url"]: r for r in lib.search("")}
    assert rows["u1"]["summary"] == "old"          # untouched
    assert rows["u2"]["summary"] == "NEW::B"


def test_force_reenriches_every_row_with_given_model(lib, monkeypatch):
    lib.upsert(Article(url="u1", title="A", content="body one", summary="old", enriched=True))
    lib.upsert(Article(url="u2", title="B", content="body two", summary="old2", enriched=True))
    calls = []
    monkeypatch.setattr(pipeline.enrich_mod, "enrich", _fake_enricher(calls))

    n = pipeline.enrich_library(lib, fetch=False, force=True, model="claude-opus-4-8")
    assert n == 2                      # both rows, despite being already enriched
    assert calls == ["claude-opus-4-8", "claude-opus-4-8"]
    rows = {r["url"]: r for r in lib.search("")}
    assert rows["u1"]["summary"] == "NEW::A"       # summary overwritten
    assert rows["u2"]["summary"] == "NEW::B"
    assert "opus-tag" in rows["u1"]["tags"]         # tags unioned in


def test_force_preserves_existing_tags(lib, monkeypatch):
    lib.upsert(Article(url="u1", title="A", content="body", tags=["board:fpa"], enriched=True))
    monkeypatch.setattr(pipeline.enrich_mod, "enrich", _fake_enricher([]))

    pipeline.enrich_library(lib, fetch=False, force=True, model="claude-opus-4-8")
    tags = lib.search("")[0]["tags"]
    assert "board:fpa" in tags          # curated tag survives
    assert "opus-tag" in tags           # new tag added


def test_enrichment_provenance_is_recorded(lib, monkeypatch):
    """apply_enrichment (via the pipeline) records which model + rules ran."""
    from linklib.enrich import Enrichment
    lib.upsert(Article(url="u1", title="A", content="body", enriched=False))

    def fake(title, text, known_tags=None, model="?", tag_guide=""):
        return Enrichment(summary="s", tags=["t"], model=model, rules_version="v9")
    monkeypatch.setattr(pipeline.enrich_mod, "enrich", fake)

    pipeline.enrich_library(lib, fetch=False, force=False, model="claude-opus-4-8")
    row = lib.search("")[0]
    assert row["enrich_model"] == "claude-opus-4-8"
    assert row["enrich_rules"] == "v9"


def test_queue_provenance_survives_promotion(lib):
    """A queued candidate's model/rules carry into the library on approval."""
    lib.add_to_queue("https://ex.com/a", title="A", summary="s", enriched=True,
                     enrich_model="claude-opus-4-8", enrich_rules="v1")
    queued = lib.list_queue()[0]
    assert queued["enrich_model"] == "claude-opus-4-8"
    lib.promote_queue_item("https://ex.com/a")
    art = lib.search("")[0]
    assert art["enrich_model"] == "claude-opus-4-8"
    assert art["enrich_rules"] == "v1"


def test_apply_enrichment_leaves_in_scope_at_its_frozen_default(lib, monkeypatch):
    """The audience-scope judgment is retired (PR 4, "Remove content" —
    see linklib/db.py's articles.in_scope column comment): enrich() no
    longer computes in_scope/scope_reason at all, so every article lands
    with in_scope=1 regardless of content, and no review queue exists to
    flag anything into."""
    from linklib.enrich import Enrichment
    lib.upsert(Article(url="https://ex.com/vc", title="How to land a job in VC",
                       content="career advice for aspiring investors", enriched=False))

    def fake(title, text, known_tags=None, model="?", tag_guide=""):
        return Enrichment(summary="s", tags=["t"], model=model, rules_version="v2")
    monkeypatch.setattr(pipeline.enrich_mod, "enrich", fake)

    pipeline.enrich_library(lib, fetch=False, force=True, model="claude-opus-4-8")
    row = lib.search("")[0]
    assert row["in_scope"] == 1
    assert row["scope_reason"] == ""
