"""Tag-merge suggestions (linklib.tagstyle.suggest_tag_merges) + apply flow."""
import pathlib, sys, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from linklib import tagstyle
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _mock(monkeypatch, payload):
    def _create(**kw):
        class _B: type="text"; text=payload
        return types.SimpleNamespace(content=[_B()])
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_suggest_filters_to_real_tags(lib, monkeypatch):
    lib.upsert(Article(url="u1", title="A", tags=["saas metrics", "saas-metrics", "arr"]))
    lib.upsert(Article(url="u2", title="B", tags=["recruiting", "hiring"]))
    _mock(monkeypatch, """[
      {"canonical":"saas-metrics","merge":["saas metrics"],"reason":"variant"},
      {"canonical":"hiring","merge":["recruiting"],"reason":"synonym"},
      {"canonical":"x","merge":["does-not-exist"],"reason":"bogus"}
    ]""")
    groups = tagstyle.suggest_tag_merges(lib)
    canon = {g["canonical"] for g in groups}
    assert "saas-metrics" in canon and "hiring" in canon
    assert "x" not in canon          # group with no real tags dropped


def test_apply_merge_folds_tags(lib):
    lib.upsert(Article(url="u1", title="A", tags=["saas metrics", "arr"]))
    lib.upsert(Article(url="u2", title="B", tags=["saas-metrics"]))
    # simulate applying the proposed merge: rename each variant -> canonical
    lib.rename_tag("saas metrics", "saas-metrics")
    t1 = {r["url"]: r["tags"] for r in lib.search("")}
    assert "saas-metrics" in t1["u1"] and "saas metrics" not in t1["u1"]
    assert "saas-metrics" in t1["u2"]


def test_none_without_api(lib, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lib.upsert(Article(url="u1", title="A", tags=["a", "b", "c"]))
    assert tagstyle.suggest_tag_merges(lib) is None
