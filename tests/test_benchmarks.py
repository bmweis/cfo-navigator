"""Benchmarking Resources section (linklib.db list/add/update/delete_benchmark).

Backs the /admin/tools/resources CRUD page, which lets Brian wordsmith text,
add/remove resources, and change URLs/coverage/pricing without touching code.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_add_and_list_benchmarks(lib):
    lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/",
                      "Annual State of SaaS report.", "Private")
    lib.add_benchmark("Clouded Judgement", "https://cloudedjudgement.substack.com/",
                      "Weekly newsletter.", "Public", "free")
    benches = lib.list_benchmarks()
    assert [b["name"] for b in benches] == ["ICONIQ Growth", "Clouded Judgement"]
    assert benches[0]["coverage"] == "Private"
    assert benches[0]["pricing"] == "free"   # default when not specified


def test_get_benchmark(lib):
    bid = lib.add_benchmark("OpexEngine", "https://www.opexengine.com/",
                            "Benchmarks across Rule of 40.", "Both", "paid")
    b = lib.get_benchmark(bid)
    assert b["name"] == "OpexEngine"
    assert b["pricing"] == "paid"


def test_get_missing_benchmark_returns_none(lib):
    assert lib.get_benchmark(999) is None


def test_update_benchmark_wordsmiths_in_place(lib):
    bid = lib.add_benchmark("PublicComps", "https://www.publiccomps.com/",
                            "Original description.", "Public", "free")
    lib.update_benchmark(bid, "PublicComps", "https://www.publiccomps.com/",
                         "Rewritten, punchier description.", "Public", "freemium")
    b = lib.get_benchmark(bid)
    assert b["description"] == "Rewritten, punchier description."
    assert b["pricing"] == "freemium"
    assert len(lib.list_benchmarks()) == 1   # edit, not a duplicate insert


def test_update_can_change_url_and_coverage(lib):
    bid = lib.add_benchmark("Baremetrics", "https://old-url.example.com",
                            "desc", "Private", "free")
    lib.update_benchmark(bid, "Baremetrics", "https://baremetrics.com/open-benchmarks",
                         "desc", "Both", "free")
    b = lib.get_benchmark(bid)
    assert b["url"] == "https://baremetrics.com/open-benchmarks"
    assert b["coverage"] == "Both"


def test_delete_benchmark(lib):
    bid = lib.add_benchmark("Temp", "https://temp.example.com", "desc")
    lib.add_benchmark("Keep", "https://keep.example.com", "desc")
    lib.delete_benchmark(bid)
    names = [b["name"] for b in lib.list_benchmarks()]
    assert names == ["Keep"]


def test_delete_missing_benchmark_is_noop(lib):
    lib.add_benchmark("Keep", "https://keep.example.com", "desc")
    lib.delete_benchmark(999)
    assert len(lib.list_benchmarks()) == 1


def test_new_benchmarks_append_after_existing_order(lib):
    lib.add_benchmark("First", "https://a.example.com", "desc")
    lib.add_benchmark("Second", "https://b.example.com", "desc")
    names = [b["name"] for b in lib.list_benchmarks()]
    assert names == ["First", "Second"]
