"""linklib.enrich.generate_compare_summary — Compare Redesign Phase 2's AI
overlap/contrast summary generator. Covers the plain-dict input contract
(no linklib.compare import, avoiding the circular-import trap), the
empty-guards (missing SDK/key, <2 entities, missing voice_core), and that a
real (mocked) call round-trips model/token/cost accounting the same way
every other generate_* function in this module does.
"""
import sys
import pathlib
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def _entities():
    return [
        {"name": "RightRev", "tags": ["Revenue Recognition"],
         "sections": [("Description", "RightRev automates ASC 606 revenue recognition.", False)]},
        {"name": "NetSuite", "tags": ["ERP", "Revenue Recognition"],
         "sections": [("Description", "NetSuite is a full ERP suite including revenue recognition.", False)]},
    ]


def test_returns_none_without_anthropic_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    # Simulate ImportError by removing any real anthropic module first.
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "anthropic":
            raise ImportError("no anthropic")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="Be direct.") is None


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="Be direct.") is None


def test_returns_none_with_fewer_than_two_entities(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert enrich.generate_compare_summary("tool", _entities()[:1], voice_core="Be direct.") is None


def test_returns_none_without_voice_core(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="") is None
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="   ") is None


def _install_fake_anthropic(monkeypatch, response_text: str, tokens=(100, 20)):
    class _Block:
        type = "text"
        text = response_text

    def _create(**kw):
        usage = types.SimpleNamespace(
            input_tokens=tokens[0], output_tokens=tokens[1],
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_generates_and_returns_draft_with_cost_accounting(monkeypatch):
    _install_fake_anthropic(monkeypatch, "RightRev specializes in revenue recognition; NetSuite bundles the same capability inside a broader ERP suite.")
    draft = enrich.generate_compare_summary("tool", _entities(), model="claude-sonnet-5", voice_core="Be direct, no fluff.")
    assert draft is not None
    assert "RightRev" in draft.summary
    assert draft.model == "claude-sonnet-5"
    assert draft.input_tokens == 100
    assert draft.output_tokens == 20
    assert draft.cost_usd >= 0


def test_returns_none_on_empty_response(monkeypatch):
    _install_fake_anthropic(monkeypatch, "   ")
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="Be direct.") is None


def test_returns_none_on_api_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("rate limited")
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=_boom)))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert enrich.generate_compare_summary("tool", _entities(), voice_core="Be direct.") is None


def test_prompt_input_includes_unverified_marker(monkeypatch):
    """A field flagged unverified in the caller's plain-dict input should be
    marked in the prompt text sent to the model, so it can hedge on it."""
    captured = {}

    class _Block:
        type = "text"
        text = "A short summary."

    def _create(**kw):
        captured["messages"] = kw["messages"]
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1,
                                       cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    entities = _entities()
    entities[0]["sections"] = [("Description", "Drafted but not yet reviewed.", True)]
    enrich.generate_compare_summary("tool", entities, voice_core="Be direct.")
    prompt_text = captured["messages"][0]["content"]
    assert "unverified" in prompt_text.lower()
