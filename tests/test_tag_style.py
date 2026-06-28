"""Tag-style learning (linklib/tagstyle.py) + guide injection into enrichment.

The API call is mocked. We pin the profile builder, that a learned guide persists,
and that enrich() threads the guide into the prompt.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import tagstyle, enrich as enrich_mod
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_build_profile_counts_and_examples(lib):
    lib.upsert(Article(url="u1", title="ARR benchmarks", summary="net retention by stage",
                       tags=["saas-metrics", "arr"]))
    lib.upsert(Article(url="u2", title="Headcount planning", summary="when to hire",
                       tags=["headcount", "saas-metrics"]))
    prof = tagstyle.build_tag_profile(lib)
    assert prof["total"] == 2
    assert prof["distinct_tags"] == 3
    by = {t["tag"]: t for t in prof["tags"]}
    assert by["saas-metrics"]["count"] == 2
    assert by["arr"]["examples"][0][0] == "ARR benchmarks"
    text = tagstyle.profile_to_text(prof)
    assert "saas-metrics" in text and "ARR benchmarks" in text


def test_generate_guide_persists(lib, monkeypatch):
    lib.upsert(Article(url="u1", title="A", tags=["arr"]))

    def fake_generate(_lib, model=None):
        return "Tag arr when the piece discusses recurring revenue."
    monkeypatch.setattr(tagstyle, "generate_tag_guide", fake_generate)

    # Mirror what the web background task does.
    guide = tagstyle.generate_tag_guide(lib)
    lib.set_setting("tag_guide", guide)
    assert "recurring revenue" in lib.get_setting("tag_guide")


def test_generate_guide_none_when_no_tags(lib, monkeypatch):
    # No tags -> nothing to learn -> None (before any API call).
    assert tagstyle.build_tag_profile(lib)["tags"] == []
    assert tagstyle.generate_tag_guide(lib) is None


def test_enrich_injects_tag_guide_into_prompt(monkeypatch):
    captured = {}

    class _Block:
        type = "text"
        text = '{"summary":"s","tags":["arr"],"in_scope":true,"scope_reason":"keep"}'

    class _Resp:
        content = [_Block()]

    class _Client:
        def __init__(self, *a, **k): pass
        class messages:  # noqa
            pass

    def fake_create(self, *, model, max_tokens, messages):
        captured["prompt"] = messages[0]["content"]
        return _Resp()

    import types
    fake_anthropic = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: (captured.update(prompt=kw["messages"][0]["content"]) or _Resp()))
    ))
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    out = enrich_mod.enrich("Title", "body", known_tags=["arr"],
                            tag_guide="Tag arr when discussing recurring revenue.")
    assert out is not None and out.tags == ["arr"]
    assert "recurring revenue" in captured["prompt"]
    assert "How this librarian tags" in captured["prompt"]
