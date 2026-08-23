"""scripts/deny_pending_scan_proposals.py — the bulk-deny cleanup tool for
a botched origination run (built for the real Neobanking incident: 364
bad singleton proposals from the pre-fix whole-batch clustering bug).
Denies rather than deletes, per the standing no-dead-data /
always-leave-a-trace discipline.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
import scripts.deny_pending_scan_proposals as deny_mod


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    try:
        yield library
    finally:
        library.close()
        if os.path.exists(db):
            os.remove(db)


def _seed(lib, category_name="Neobanking", n=3, source="scan"):
    cat_id = lib.get_tool_category_id(category_name) or lib.add_tool_category(category_name, "desc")
    ids = []
    for i in range(n):
        ids.append(lib.add_feature_review_queue_item(
            source=source, proposal_type="new_feature+link",
            payload={"category_id": cat_id, "feature": {"name": f"Bad singleton {i}"}, "links": []},
            category_id=cat_id,
        ))
    return cat_id, ids


def _run(monkeypatch, db_path, category, reason, apply=False):
    argv = ["deny_pending_scan_proposals.py", "--db", db_path, "--category", category, "--reason", reason]
    if apply:
        argv.append("--apply")
    monkeypatch.setattr(sys, "argv", argv)
    return deny_mod.main()


def test_preview_denies_nothing(monkeypatch, lib):
    cat_id, ids = _seed(lib)
    lib.close()  # release the connection so the script's own Library() can open it

    rc = _run(monkeypatch, lib.path, "Neobanking", "test cleanup", apply=False)
    assert rc == 0

    lib2 = Library(lib.path)
    try:
        pending = lib2.list_feature_review_queue(status="pending")
        assert len(pending) == 3
    finally:
        lib2.close()


def test_apply_denies_with_reason_and_preserves_rows(monkeypatch, lib):
    cat_id, ids = _seed(lib)
    lib.close()

    rc = _run(monkeypatch, lib.path, "Neobanking", "Superseded by corrected re-run", apply=True)
    assert rc == 0

    lib2 = Library(lib.path)
    try:
        all_items = lib2.list_feature_review_queue(status=None)
        assert len(all_items) == 3   # rows preserved, not deleted
        assert all(i["status"] == "denied" for i in all_items)
        assert all(i["resolution_note"] == "Superseded by corrected re-run" for i in all_items)
        assert lib2.list_feature_review_queue(status="pending") == []
    finally:
        lib2.close()


def test_only_denies_matching_source(monkeypatch, lib):
    cat_id, scan_ids = _seed(lib, source="scan", n=2)
    admin_id = lib.add_feature_review_queue_item(
        source="admin", proposal_type="new_feature+link",
        payload={"category_id": cat_id, "feature": {"name": "Admin's own entry"}, "links": []},
        category_id=cat_id,
    )
    lib.close()

    _run(monkeypatch, lib.path, "Neobanking", "cleanup", apply=True)

    lib2 = Library(lib.path)
    try:
        admin_item = lib2.get_feature_review_queue_item(admin_id)
        assert admin_item["status"] == "pending"   # untouched — different source
        pending = lib2.list_feature_review_queue(status="pending")
        assert len(pending) == 1
        assert pending[0]["id"] == admin_id
    finally:
        lib2.close()


def test_only_denies_matching_category(monkeypatch, lib):
    cat_a, ids_a = _seed(lib, category_name="Neobanking", n=1)
    cat_b, ids_b = _seed(lib, category_name="FP&A", n=1)
    lib.close()

    _run(monkeypatch, lib.path, "Neobanking", "cleanup", apply=True)

    lib2 = Library(lib.path)
    try:
        assert lib2.get_feature_review_queue_item(ids_a[0])["status"] == "denied"
        assert lib2.get_feature_review_queue_item(ids_b[0])["status"] == "pending"
    finally:
        lib2.close()


def test_unknown_category_errors_without_writing(monkeypatch, lib):
    _seed(lib)
    lib.close()

    rc = _run(monkeypatch, lib.path, "NotARealCategory", "cleanup", apply=True)
    assert rc == 1

    lib2 = Library(lib.path)
    try:
        assert len(lib2.list_feature_review_queue(status="pending")) == 3
    finally:
        lib2.close()


def test_no_matching_items_is_a_clean_noop(monkeypatch, lib):
    lib.add_tool_category("Neobanking", "desc")
    lib.close()
    rc = _run(monkeypatch, lib.path, "Neobanking", "cleanup", apply=True)
    assert rc == 0
