"""Tag vocabulary cleanup (linklib.db rename_tag / delete_tag).

Auto-tagging fragments over time ("saas metrics" vs "saas-metrics"); these pin
that rename merges, delete removes, and full-text search stays in sync.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_rename_tag(lib):
    lib.upsert(Article(url="u1", title="A", tags=["saas metrics", "arr"]))
    n = lib.rename_tag("saas metrics", "saas-metrics")
    assert n == 1
    tags = lib.search("")[0]["tags"]
    assert "saas-metrics" in tags and "saas metrics" not in tags


def test_rename_merges_into_existing(lib):
    # Article already carries both the source and target tag — merge must dedupe.
    lib.upsert(Article(url="u1", title="A", tags=["saas metrics", "saas-metrics", "arr"]))
    lib.upsert(Article(url="u2", title="B", tags=["saas metrics"]))
    changed = lib.rename_tag("saas metrics", "saas-metrics")
    assert changed == 2
    t1 = {r["url"]: r["tags"] for r in lib.search("")}
    assert t1["u1"].count("saas-metrics") == 1            # no duplicate after merge
    assert "saas metrics" not in t1["u1"]
    assert "saas-metrics" in t1["u2"]


def test_delete_tag(lib):
    lib.upsert(Article(url="u1", title="A", tags=["junk", "arr"]))
    lib.upsert(Article(url="u2", title="B", tags=["arr"]))
    n = lib.delete_tag("junk")
    assert n == 1
    assert "junk" not in lib.search("")[0]["tags"]


def test_rename_updates_search_index(lib):
    lib.upsert(Article(url="u1", title="A", content="body", tags=["oldtag"]))
    assert any(r["url"] == "u1" for r in lib.search("oldtag"))   # findable before
    lib.rename_tag("oldtag", "newtag")
    assert not any(r["url"] == "u1" for r in lib.search("oldtag"))  # old token gone from FTS
    assert any(r["url"] == "u1" for r in lib.search("newtag"))      # new token indexed


def test_noop_rename(lib):
    lib.upsert(Article(url="u1", title="A", tags=["arr"]))
    assert lib.rename_tag("missing", "x") == 0
    assert lib.rename_tag("arr", "arr") == 0
    assert lib.rename_tag("arr", "") == 0
