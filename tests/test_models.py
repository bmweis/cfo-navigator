"""The shared model registry and live-reconciliation logic (linklib/models.py).

Covers the pure `_merge` (retire drop + new-model surfacing) and the static
fallback path of `models_for`. The live API call itself is wrapped to never
raise, so it isn't exercised here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import models


def test_registry_well_formed():
    seen = set()
    for m in models._REGISTRY:
        assert {"id", "label", "short", "enrich"} <= set(m), m
        assert m["id"] and m["label"] and m["short"] and m["enrich"]
        assert m["id"] not in seen, f"duplicate id {m['id']}"
        seen.add(m["id"])
    # Sonnet 5 is in the registry (the model that prompted this change).
    assert "claude-sonnet-5" in seen


def _view():
    return [{"id": m["id"], "label": m["label"], "blurb": m["short"]} for m in models._REGISTRY]


def test_merge_no_live_returns_full_registry():
    view = _view()
    assert models._merge(view, None, allow_new=True) == view
    assert models._merge(view, {}, allow_new=True) == view


def test_merge_drops_retired_models():
    view = [{"id": "a", "label": "A", "blurb": "x"}, {"id": "b", "label": "B", "blurb": "y"}]
    live = {"a": {"name": "A", "created": 1}}  # b no longer offered
    out = models._merge(view, live, allow_new=False)
    assert [m["id"] for m in out] == ["a"]


def test_merge_surfaces_newer_models_only_when_allowed():
    view = [{"id": "a", "label": "A", "blurb": "x"}]
    live = {
        "a":   {"name": "A",   "created": 1},
        "new": {"name": "New", "created": 2},   # newer than newest known → surfaced
        "old": {"name": "Old", "created": 0},   # older → never surfaced
    }
    on = models._merge(view, live, allow_new=True)
    assert [m["id"] for m in on] == ["a", "new"]
    assert on[-1]["label"] == "New"

    off = models._merge(view, live, allow_new=False)
    assert [m["id"] for m in off] == ["a"]


def test_merge_unrecognized_live_keeps_curation():
    # API up but returns nothing we know → trust the registry rather than blanking.
    view = [{"id": "a", "label": "A", "blurb": "x"}]
    out = models._merge(view, {"z": {"name": "Z", "created": 9}}, allow_new=True)
    assert [m["id"] for m in out] == ["a"]


def test_models_for_static_fallback(monkeypatch):
    monkeypatch.setattr(models, "_live_models", lambda: None)
    short = models.models_for(blurb="short")
    ids = [m["id"] for m in short]
    assert ids == [m["id"] for m in models._REGISTRY]
    assert "claude-sonnet-5" in ids
    # blurb selection pulls the right registry field.
    by_id = {m["id"]: m["blurb"] for m in short}
    assert by_id["claude-sonnet-5"] == "Balanced · newest"
    enrich = {m["id"]: m["blurb"] for m in models.models_for(blurb="enrich")}
    assert "stronger summaries" in enrich["claude-sonnet-5"]
