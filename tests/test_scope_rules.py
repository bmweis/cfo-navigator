"""The enrichment scope rules (v3) — what gets excluded from the library.

Pins that the prompt instructs the model to drop podcasts/webinars, slide decks,
and annual-predictions roundups, so the sweep skips them and a re-enrich flags
already-saved ones for removal review.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def test_rules_version_bumped():
    assert enrich.ENRICH_RULES_VERSION == "v4"


def test_base_prompt_is_permanent_audience_only():
    # The base prompt always carries the audience rule + career-in-VC exclusion,
    # but NOT the toggleable cleanup exclusions.
    p = enrich._PROMPT.lower()
    assert "career in venture capital" in p
    assert "podcast" not in p and "predictions" not in p   # those live in the cleanup block


def test_cleanup_block_has_the_temporary_exclusions():
    c = enrich._CLEANUP_EXCLUSIONS.lower()
    assert "podcast" in c and "webinar" in c
    assert "slide deck" in c or "slides" in c
    assert "predictions" in c
    assert "20vc" in c
    assert "fund managers" in c or "gps" in c   # the Carta/fund case
    assert "lp" in c
    # operator content is explicitly kept even within the cleanup block
    assert "raising a round" in c


def test_cleanup_block_injected_only_in_cleanup_mode(monkeypatch):
    """enrich() includes the cleanup exclusions only when cleanup_mode=True."""
    captured = {}
    import types
    def _make(**kw):
        captured["prompt"] = kw["messages"][0]["content"]
        class _B:
            type = "text"
            text = '{"summary":"s","tags":["t"],"in_scope":true,"scope_reason":"k"}'
        return types.SimpleNamespace(content=[_B()])
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _make(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    enrich.enrich("T", "body", cleanup_mode=False)
    assert "podcast" not in captured["prompt"].lower()
    enrich.enrich("T", "body", cleanup_mode=True)
    assert "podcast" in captured["prompt"].lower() and "fund managers" in captured["prompt"].lower()
