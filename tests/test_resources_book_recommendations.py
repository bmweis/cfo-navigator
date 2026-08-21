"""Resources page split — "Benchmarking" and "Book recommendations" sections
(linklib.db's benchmarks.section column, /tools/resources + /admin/tools/
resources rendering, the /contact suggest-a-resource link, and the one-off
scripts/seed_book_recommendations.py migration).
"""
import os
import pathlib
import sys
import tempfile

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


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


# -- linklib.db: section column ------------------------------------------------

def test_existing_benchmarks_default_to_benchmarking_section(lib):
    bid = lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc")
    b = lib.get_benchmark(bid)
    assert b["section"] == "benchmarking"


def test_add_benchmark_with_books_section(lib):
    bid = lib.add_benchmark("Venture Deals", "https://venturedeals.com/",
                            "Brad Feld & Jason Mendelson.", section="books")
    b = lib.get_benchmark(bid)
    assert b["section"] == "books"


def test_list_benchmarks_filters_by_section(lib):
    lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc", section="benchmarking")
    lib.add_benchmark("Venture Deals", "https://venturedeals.com/", "desc", section="books")
    lib.add_benchmark("The Advantage", "https://www.tablegroup.com/product/the-advantage/", "desc", section="books")

    bench_only = lib.list_benchmarks(section="benchmarking")
    books_only = lib.list_benchmarks(section="books")
    everything = lib.list_benchmarks()

    assert [b["name"] for b in bench_only] == ["ICONIQ Growth"]
    assert sorted(b["name"] for b in books_only) == ["The Advantage", "Venture Deals"]
    assert len(everything) == 3


def test_update_benchmark_can_move_section(lib):
    bid = lib.add_benchmark("Misfiled", "https://example.com", "desc", section="books")
    lib.update_benchmark(bid, "Misfiled", "https://example.com", "desc", "Private", "free", section="benchmarking")
    b = lib.get_benchmark(bid)
    assert b["section"] == "benchmarking"


def test_sections_have_independent_sort_order(lib):
    # Adding to one section shouldn't be affected by how many rows exist
    # in the other section.
    for i in range(3):
        lib.add_benchmark(f"Bench {i}", f"https://bench{i}.example.com", "desc", section="benchmarking")
    bid = lib.add_benchmark("First Book", "https://book0.example.com", "desc", section="books")
    b = lib.get_benchmark(bid)
    assert b["sort_order"] == 0   # not 3 — scoped to its own section


# -- /tools/resources (public) -------------------------------------------------

def test_public_page_shows_both_section_headings(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc", section="benchmarking")
    lib.add_benchmark("Venture Deals", "https://venturedeals.com/",
                      "Brad Feld & Jason Mendelson.", section="books")
    lib.close()

    r = _client(env).get("/tools/resources")
    assert r.status_code == 200
    assert "Benchmarking" in r.text
    assert "Book recommendations" in r.text
    assert "ICONIQ Growth" in r.text
    assert "Venture Deals" in r.text


def test_book_cards_have_no_pricing_or_coverage_badges(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_benchmark("Venture Deals", "https://venturedeals.com/",
                      "Brad Feld & Jason Mendelson.", coverage="Both", pricing="paid", section="books")
    lib.close()

    r = _client(env).get("/tools/resources")
    # The benchmarking badge classes/labels shouldn't appear near the book card
    # at all, since no benchmarking-section row exists in this test.
    assert "$ Paid" not in r.text
    assert ">Both<" not in r.text


def test_empty_books_section_shows_coming_soon(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc", section="benchmarking")
    lib.close()

    r = _client(env).get("/tools/resources")
    assert "Book recommendations" in r.text
    assert "Coming soon" in r.text


def test_public_page_has_suggest_a_resource_link(env):
    r = _client(env).get("/tools/resources")
    assert '/contact?context=resource-suggestion' in r.text
    assert "Suggest a resource" in r.text


def test_old_benchmarks_url_still_redirects(env):
    r = _client(env).get("/tools/benchmarks", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/tools/resources"


# -- /admin/tools/resources -----------------------------------------------------

def test_admin_page_groups_by_section(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc", section="benchmarking")
    lib.add_benchmark("Venture Deals", "https://venturedeals.com/", "desc", section="books")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/resources")
    assert r.status_code == 200
    assert "Benchmarking" in r.text
    assert "Book recommendations" in r.text
    assert "ICONIQ Growth" in r.text
    assert "Venture Deals" in r.text


def test_admin_add_resource_with_books_section(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/resources/new", data={
        "section": "books", "name": "The Advantage", "url": "https://www.tablegroup.com/product/the-advantage/",
        "description": "Patrick Lencioni.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    books = lib.list_benchmarks(section="books")
    lib.close()
    assert len(books) == 1
    assert books[0]["name"] == "The Advantage"


def test_admin_edit_resource_preserves_and_can_change_section(env):
    lib = Library(os.environ["LINKLIB_DB"])
    bid = lib.add_benchmark("ICONIQ Growth", "https://iconiqcapital.com/growth/", "desc", section="benchmarking")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/resources/{bid}/edit", data={
        "section": "books", "name": "ICONIQ Growth", "url": "https://iconiqcapital.com/growth/",
        "description": "desc", "coverage": "Private", "pricing": "free",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    b = lib.get_benchmark(bid)
    lib.close()
    assert b["section"] == "books"


def test_admin_add_rejects_unknown_section_defaults_to_benchmarking(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/resources/new", data={
        "section": "not-a-real-section", "name": "Test", "url": "https://example.com", "description": "desc",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    b = lib.list_benchmarks()[0]
    lib.close()
    assert b["section"] == "benchmarking"


# -- /contact context param ----------------------------------------------------

def test_contact_context_prefills_resource_suggestion_message(env):
    r = _client(env).get("/contact?context=resource-suggestion")
    assert "Resource suggestion:" in r.text


def test_contact_no_context_leaves_message_blank(env):
    r = _client(env).get("/contact")
    # The textarea's own content shouldn't contain the resource-suggestion prefix.
    assert "Resource suggestion:" not in r.text


def test_contact_explicit_message_wins_over_context(env):
    r = _client(env).get("/contact?context=resource-suggestion&message=Something+else")
    assert "Something else" in r.text
    assert "Resource suggestion:" not in r.text


# -- scripts/seed_book_recommendations.py --------------------------------------

def test_seed_book_recommendations_script(tmp_path):
    db_path = str(tmp_path / "seed_test.db")
    lib = Library(db_path)
    lib.close()

    from scripts import seed_book_recommendations as seed_mod
    assert len(seed_mod.BOOKS) == 10

    argv = sys.argv
    try:
        sys.argv = ["seed_book_recommendations", "--db", db_path]
        assert seed_mod.main() == 0
        lib = Library(db_path)
        try:
            assert lib.list_benchmarks(section="books") == []   # preview writes nothing
        finally:
            lib.close()

        sys.argv = ["seed_book_recommendations", "--db", db_path, "--apply"]
        assert seed_mod.main() == 0
    finally:
        sys.argv = argv

    lib = Library(db_path)
    try:
        books = lib.list_benchmarks(section="books")
        assert len(books) == 10
        assert {b["name"] for b in books} == {b["name"] for b in seed_mod.BOOKS}
    finally:
        lib.close()


def test_seed_book_recommendations_idempotent_by_url(tmp_path):
    db_path = str(tmp_path / "seed_test2.db")
    lib = Library(db_path)
    lib.close()

    from scripts import seed_book_recommendations as seed_mod
    argv = sys.argv
    try:
        sys.argv = ["seed_book_recommendations", "--db", db_path, "--apply"]
        assert seed_mod.main() == 0
        assert seed_mod.main() == 0   # second run: nothing to insert, no crash
    finally:
        sys.argv = argv

    lib = Library(db_path)
    try:
        assert len(lib.list_benchmarks(section="books")) == 10   # not duplicated
    finally:
        lib.close()
