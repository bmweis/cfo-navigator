"""Queue approval predictions (linklib/suggest.py).

The Claude call is mocked. Pins that predictions are scoped to the source, keyed
by a valid candidate URL, and that we degrade gracefully with no examples / no API.
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import suggest
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _B:
            type = "text"
            text = payload_json
        return types.SimpleNamespace(content=[_B()])
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_none_without_approved_examples(lib, monkeypatch):
    _mock_anthropic(monkeypatch, "[]")
    lib.add_to_queue("https://saastr.com/a", title="A", source="SaaStr")
    # No SaaStr articles in the library yet -> nothing to learn from.
    assert suggest.suggest_approvals(lib, "SaaStr") is None


def test_predicts_pending_scoped_to_source(lib, monkeypatch):
    lib.upsert(Article(url="https://saastr.com/kept1", title="ARR benchmarks",
                       source="SaaStr", summary="net retention", tags=["arr"]))
    lib.add_to_queue("https://saastr.com/keep", title="Good SaaS metrics piece", source="SaaStr")
    lib.add_to_queue("https://saastr.com/skip", title="20VC podcast episode", source="SaaStr")
    lib.add_to_queue("https://other.com/x", title="Unrelated", source="Other")

    _mock_anthropic(monkeypatch, """[
      {"url": "https://saastr.com/keep", "keep": true,  "reason": "matches your SaaS metrics picks"},
      {"url": "https://saastr.com/skip", "keep": false, "reason": "podcast episode"},
      {"url": "https://other.com/x",     "keep": true,  "reason": "should be ignored - wrong source"}
    ]""")

    preds = suggest.suggest_approvals(lib, "SaaStr")
    assert set(preds) == {"https://saastr.com/keep", "https://saastr.com/skip"}  # other source excluded
    assert preds["https://saastr.com/keep"]["keep"] is True
    assert preds["https://saastr.com/skip"]["keep"] is False
    assert "podcast" in preds["https://saastr.com/skip"]["reason"]


def test_empty_when_no_pending(lib, monkeypatch):
    _mock_anthropic(monkeypatch, "[]")
    lib.upsert(Article(url="https://saastr.com/kept1", title="A", source="SaaStr"))
    assert suggest.suggest_approvals(lib, "SaaStr") == {}


def test_none_without_api_key(lib, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lib.upsert(Article(url="https://saastr.com/kept1", title="A", source="SaaStr"))
    lib.add_to_queue("https://saastr.com/keep", title="x", source="SaaStr")
    assert suggest.suggest_approvals(lib, "SaaStr") is None
