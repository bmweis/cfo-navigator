"""scripts/remap_queue_to_framework.py + its two linklib.feature_scan support
functions (match_items_to_framework, synthesize_bucket_definition) and
linklib.db.Library.update_feature_review_queue_payload.

Mocks the Anthropic SDK (same idiom as tests/test_feature_scan.py) so this
suite runs with no real API keys or network access.
"""
import json
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from linklib import feature_scan
from linklib.db import Library
from scripts import remap_queue_to_framework as remap


def _mock_anthropic_sequence(monkeypatch, payloads):
    calls = {"n": 0}

    def _create(**kw):
        i = calls["n"]
        calls["n"] += 1
        payload = payloads[min(i, len(payloads) - 1)]

        class _Block:
            type = "text"
            text = payload
        usage = types.SimpleNamespace(
            input_tokens=200, output_tokens=100,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return calls


@pytest.fixture
def temp_lib():
    db_path = tempfile.mktemp(suffix=".db")
    lib = Library(db_path)
    yield lib
    lib.close()
    if os.path.exists(db_path):
        os.remove(db_path)


def _buckets():
    return [
        feature_scan.FrameworkBucket(index=0, group="Core banking", name="Business bank accounts"),
        feature_scan.FrameworkBucket(index=1, group="Cards", name="Charge cards"),
    ]


def _candidate(item_id, name, definition=""):
    return feature_scan.QueueItemCandidate(item_id=item_id, name=name, definition=definition)


# --- linklib.feature_scan.match_items_to_framework --------------------------

def test_match_items_to_framework_maps_and_leaves_none(monkeypatch):
    _mock_anthropic_sequence(monkeypatch, ['{"matches": [0, null, 1]}'])
    items = [_candidate(1, "Checking account"), _candidate(2, "Retail cashback perk"),
              _candidate(3, "Corporate charge card")]
    matches, cost = feature_scan.match_items_to_framework(items, _buckets(), "Neobanking")
    assert matches == [0, None, 1]
    assert cost > 0


def test_match_items_to_framework_batches_and_sends_full_bucket_list_each_time(monkeypatch):
    calls = _mock_anthropic_sequence(monkeypatch, ['{"matches": [0]}', '{"matches": [1]}'])
    items = [_candidate(1, "A"), _candidate(2, "B")]
    matches, _cost = feature_scan.match_items_to_framework(items, _buckets(), "Neobanking", batch_size=1)
    assert calls["n"] == 2
    assert matches == [0, 1]


def test_match_items_to_framework_out_of_range_and_malformed_degrade_to_none(monkeypatch):
    # index 99 is out of range for a 2-bucket list; a bool must not be
    # misread as index 0/1 (bool is an int subclass in Python).
    _mock_anthropic_sequence(monkeypatch, ['{"matches": [99, true, "nope"]}'])
    items = [_candidate(1, "A"), _candidate(2, "B"), _candidate(3, "C")]
    matches, _cost = feature_scan.match_items_to_framework(items, _buckets(), "Neobanking")
    assert matches == [None, None, None]


def test_match_items_to_framework_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    items = [_candidate(1, "A")]
    matches, cost = feature_scan.match_items_to_framework(items, _buckets(), "Neobanking")
    assert matches is None
    assert cost == 0.0


def test_match_items_to_framework_empty_items_short_circuits(monkeypatch):
    matches, cost = feature_scan.match_items_to_framework([], _buckets(), "Neobanking")
    assert matches == []
    assert cost == 0.0


def test_match_items_to_framework_call_failure_returns_none(monkeypatch):
    def _create(**kw):
        raise RuntimeError("simulated API failure")
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    matches, _cost = feature_scan.match_items_to_framework([_candidate(1, "A")], _buckets(), "Neobanking")
    assert matches is None


# --- linklib.feature_scan.synthesize_bucket_definition ----------------------

def test_synthesize_bucket_definition_returns_model_text(monkeypatch):
    _mock_anthropic_sequence(monkeypatch, ['{"definition": "Synthesized outcome-oriented text."}'])
    definition, cost = feature_scan.synthesize_bucket_definition(
        "Business bank accounts", ["Def A", "Def B"], "Neobanking",
    )
    assert definition == "Synthesized outcome-oriented text."
    assert cost > 0


def test_synthesize_bucket_definition_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    definition, cost = feature_scan.synthesize_bucket_definition(
        "Business bank accounts", ["short", "a much longer definition here"], "Neobanking",
    )
    assert definition == "a much longer definition here"
    assert cost == 0.0


def test_synthesize_bucket_definition_falls_back_on_call_failure(monkeypatch):
    def _create(**kw):
        raise RuntimeError("simulated failure")
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    definition, cost = feature_scan.synthesize_bucket_definition(
        "Business bank accounts", ["only one"], "Neobanking",
    )
    assert definition == "only one"
    assert cost == 0.0


# --- linklib.db.Library.update_feature_review_queue_payload -----------------

def test_update_feature_review_queue_payload_rewrites_in_place_and_stays_pending(temp_lib):
    item_id = temp_lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_feature+link",
        payload={"category_id": 1, "feature": {"name": "Old name", "definition": "old"}, "links": []},
        category_id=1,
    )
    temp_lib.update_feature_review_queue_payload(
        item_id, {"category_id": 1, "feature": {"name": "New name", "definition": "new"}, "links": []},
        proposal_type="new_feature+2 links",
    )
    item = temp_lib.get_feature_review_queue_item(item_id)
    assert item["status"] == "pending"
    assert item["payload"]["feature"]["name"] == "New name"
    assert item["proposal_type"] == "new_feature+2 links"


def test_update_feature_review_queue_payload_refuses_resolved_item(temp_lib):
    item_id = temp_lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_feature+link",
        payload={"category_id": 1, "feature": {"name": "X"}, "links": []}, category_id=1,
    )
    temp_lib.deny_feature_review_queue_item(item_id, "denied for test")
    with pytest.raises(ValueError):
        temp_lib.update_feature_review_queue_payload(item_id, {"category_id": 1})


# --- scripts/remap_queue_to_framework.py end-to-end --------------------------

def _seed_pending(lib, category_id, name, definition, tool_id, source_url="", note=""):
    return lib.add_feature_review_queue_item(
        source="scan", proposal_type="new_feature+link",
        payload={"category_id": category_id,
                 "feature": {"name": name, "definition": definition, "pointer_note": ""},
                 "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0,
                            "verified_as_of": "2026-08-23", "note": note, "source_url": source_url}]},
        category_id=category_id,
    )


@pytest.fixture
def framework_path(tmp_path):
    path = tmp_path / "framework.json"
    path.write_text(json.dumps({
        "category": "Neobanking",
        "buckets": [
            {"group": "Core banking", "name": "Business bank accounts", "hint": ""},
            {"group": "Cards", "name": "Charge cards", "hint": ""},
        ],
    }))
    return str(path)


def test_remap_preview_writes_nothing(monkeypatch, temp_lib, framework_path, capsys):
    category_id = temp_lib.add_tool_category("Neobanking")
    _seed_pending(temp_lib, category_id, "Checking account access", "def A", tool_id=1)
    _seed_pending(temp_lib, category_id, "Business checking", "def B", tool_id=2)
    _seed_pending(temp_lib, category_id, "Retail cashback perk", "def C", tool_id=3)

    _mock_anthropic_sequence(monkeypatch, ['{"matches": [0, 0, null]}',
                                            '{"definition": "Merged definition."}'])
    monkeypatch.setattr(sys, "argv", [
        "remap_queue_to_framework.py", "--db", temp_lib.path, "--category", "Neobanking",
        "--framework", framework_path,
    ])
    rc = remap.main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "PREVIEW ONLY" in out
    pending = temp_lib.list_feature_review_queue(status="pending")
    assert len(pending) == 3   # nothing changed
    assert temp_lib.list_feature_review_queue(status="denied") == []


def test_remap_apply_consolidates_denies_and_skips_empty_buckets(monkeypatch, temp_lib, framework_path):
    category_id = temp_lib.add_tool_category("Neobanking")
    id_a = _seed_pending(temp_lib, category_id, "Checking account access", "def A", tool_id=1,
                          source_url="https://a.example")
    id_b = _seed_pending(temp_lib, category_id, "Business checking", "def B", tool_id=2)
    id_c = _seed_pending(temp_lib, category_id, "Retail cashback perk", "def C", tool_id=3)

    _mock_anthropic_sequence(monkeypatch, ['{"matches": [0, 0, null]}',
                                            '{"definition": "Merged canonical definition."}'])
    monkeypatch.setattr(sys, "argv", [
        "remap_queue_to_framework.py", "--db", temp_lib.path, "--category", "Neobanking",
        "--framework", framework_path, "--apply",
    ])
    rc = remap.main()
    assert rc == 0

    pending = temp_lib.list_feature_review_queue(status="pending")
    denied = temp_lib.list_feature_review_queue(status="denied")
    assert len(pending) == 1   # bucket 0's consolidated row; bucket 1 (Charge cards) had no match, skipped
    assert len(denied) == 2    # id_b (consolidated-away) + id_c (out of scope)

    kept = pending[0]
    assert kept["id"] == id_a   # first contributing item's row was rewritten in place
    assert kept["payload"]["feature"]["name"] == "Business bank accounts"   # framework's exact name, verbatim
    assert kept["payload"]["feature"]["definition"] == "Merged canonical definition."
    linked_tool_ids = sorted(l["tool_id"] for l in kept["payload"]["links"])
    assert linked_tool_ids == [1, 2]   # union of both contributing items' links, deduped by tool_id

    denied_by_id = {d["id"]: d for d in denied}
    assert "Consolidated into 'Business bank accounts'" in denied_by_id[id_b]["resolution_note"]
    assert "Out of scope" in denied_by_id[id_c]["resolution_note"]

    # No 'approved' status ever touched, and no direct category_features/
    # tool_feature_links write happened — the hard rule this script enforces.
    assert temp_lib.list_category_features(category_id) == []
    assert all(item["status"] in ("pending", "denied") for item in
               temp_lib.list_feature_review_queue(status=None))


def test_remap_union_links_prefers_more_complete_copy_of_repeated_tool(monkeypatch, temp_lib, framework_path):
    category_id = temp_lib.add_tool_category("Neobanking")
    # Same tool_id (1) proposed under two different queue items — the one
    # with a real source_url should win the union, not whichever came first.
    _seed_pending(temp_lib, category_id, "Checking account access", "def A", tool_id=1, source_url="")
    _seed_pending(temp_lib, category_id, "Business checking", "def B", tool_id=1,
                  source_url="https://real-source.example")

    _mock_anthropic_sequence(monkeypatch, ['{"matches": [0, 0]}'])   # single-item bucket group -> no synthesis call
    monkeypatch.setattr(sys, "argv", [
        "remap_queue_to_framework.py", "--db", temp_lib.path, "--category", "Neobanking",
        "--framework", framework_path, "--apply",
    ])
    rc = remap.main()
    assert rc == 0

    pending = temp_lib.list_feature_review_queue(status="pending")
    assert len(pending) == 1
    links = pending[0]["payload"]["links"]
    assert len(links) == 1   # deduped down to one link for tool_id=1
    assert links[0]["source_url"] == "https://real-source.example"


def test_remap_no_pending_scan_items_is_a_noop(temp_lib, framework_path, monkeypatch, capsys):
    temp_lib.add_tool_category("Neobanking")
    monkeypatch.setattr(sys, "argv", [
        "remap_queue_to_framework.py", "--db", temp_lib.path, "--category", "Neobanking",
        "--framework", framework_path,
    ])
    rc = remap.main()
    assert rc == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_remap_unknown_category_errors(temp_lib, framework_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "remap_queue_to_framework.py", "--db", temp_lib.path, "--category", "Nonexistent Category",
        "--framework", framework_path,
    ])
    rc = remap.main()
    assert rc == 1
