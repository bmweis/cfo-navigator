"""scripts/probe_digest_rank.py: the read-only ranking probe.

Runs against a temp database built with synthetic data (invented publisher,
invented vectors). The vector half is exercised for real through sqlite-vec when
it is installed; the OpenAI embedding of the question is replaced with a stub, so
no test touches the network.
"""
import hashlib
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import embeddings as embed_mod
from linklib.agent import EFFORT_SETTINGS
from linklib.db import Article, Library
from scripts import probe_digest_rank as probe

QUESTION = "median net revenue retention benchmarks"


def _vec(i):
    v = [0.0] * embed_mod.EMBED_DIM
    v[i] = 1.0
    return v


def _seed(path, n_matching=40, content_chars=400, digest_vec_index=3):
    lib = Library(path)
    ids = {}
    try:
        for i in range(n_matching):
            body = ("retention benchmarks median net revenue " * 200)[:content_chars]
            ids[f"plain{i}"] = lib.upsert(Article(
                url=f"https://blog.test/{i}", title=f"Blog post {i}", source="Some Blog",
                summary="retention benchmarks", content=body, enriched=True,
                published_at=f"2024-01-{i % 28 + 1:02d}T00:00:00+00:00"))
        ids["digest"] = lib.upsert(Article(
            url="https://example-benchmarks.test/report-2099?digest=retention",
            title="Example Benchmarks 2099: Retention", source="Example Benchmarks 2099",
            summary="Median net revenue retention was 101 percent.",
            content="Retention digest body. " * 20, tags=["benchmark-digest"], enriched=True,
            published_at="2099-03-01T00:00:00+00:00"))
        if lib.vector_search_available():
            for name, aid in ids.items():
                idx = digest_vec_index if name == "digest" else (10 + aid) % embed_mod.EMBED_DIM
                lib.upsert_article_embedding(aid, _vec(idx), "h", "m")
    finally:
        lib.close()
    return ids


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "probe.db")
    ids = _seed(path)
    return path, ids


def _stub_embed(monkeypatch, index=3):
    monkeypatch.setattr(embed_mod, "embed_text",
                        lambda text, model=embed_mod.DEFAULT_MODEL: embed_mod.EmbedResult(
                            vectors=[_vec(index)], input_tokens=7, cost_usd=0.000001))


def _run(capsys, *argv):
    rc = probe.main(list(argv))
    return rc, capsys.readouterr().out


def _depth_block(out, depth):
    start = out.index(f"== {depth}:")
    nxt = [out.index(f"== {d}:") for d in probe.DEPTHS if d != depth and out.index(f"== {d}:") > start]
    return out[start:min(nxt) if nxt else len(out)]


def test_requires_an_absolute_db_path(capsys):
    with pytest.raises(SystemExit) as e:
        probe.main(["--db", "library.db", "--question", "x"])
    assert "absolute" in str(e.value)
    with pytest.raises(SystemExit) as e:
        probe.main(["--db", "/nope/library.db", "--question", "x"])
    assert "not found" in str(e.value)


def test_full_text_half_with_no_vector(db, capsys, monkeypatch):
    path, ids = db
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    rc, out = _run(capsys, "--db", path, "--question", QUESTION, "--watch", str(ids["digest"]))
    assert rc == 0
    assert "Vector half skipped" in out
    for d in probe.DEPTHS:
        assert f"== {d}:" in out
    row = next(l for l in _depth_block(out, "quick").splitlines() if "Example Benchmarks 2099: Retention" in l)
    assert row.startswith(">>>")


def test_vector_half_ranks_the_digest_first_and_sends_it(db, capsys, monkeypatch):
    path, ids = db
    if not Library(path).vector_search_available():
        pytest.skip("sqlite-vec is not installed here")
    _stub_embed(monkeypatch, index=3)
    rc, out = _run(capsys, "--db", path, "--question", QUESTION, "--watch", str(ids["digest"]))
    assert rc == 0 and "Question embedded once" in out
    for d in ("quick", "standard"):
        rows = [l for l in _depth_block(out, d).splitlines() if l.startswith(">>>") and "Retention" in l]
        assert len(rows) == 1
        cells = rows[0].split()
        # >>> rank id fts vec status chars ...
        # Vector rank 1 is exact. Fused rank can be 2: a row that scores in BOTH lists
        # outranks one that scores in a single list, which is how RRF is meant to work.
        assert cells[4] == "1" and int(cells[1]) <= 2, rows[0]
        assert "SENT" in rows[0]
    # At deep depth the same digest falls below the top 15, because many rows score in
    # both lists. The probe must say so, with the vector rank it found in the deep pool.
    deep = _depth_block(out, "deep")
    assert f"watched #{ids['digest']} (Example Benchmarks 2099: Retention): not in the top 15." in deep
    assert "vector rank 1." in deep


def test_sent_count_matches_each_depths_library_cap(db, capsys, monkeypatch):
    path, _ = db
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    _, out = _run(capsys, "--db", path, "--question", QUESTION)
    for d in probe.DEPTHS:
        block = _depth_block(out, d)
        sent = sum(1 for l in block.splitlines() if " SENT " in l)
        assert sent == min(EFFORT_SETTINGS[d]["max_library"], probe.SHOW), d
        assert "not sent" in block or EFFORT_SETTINGS[d]["max_library"] >= probe.SHOW


def test_a_watched_row_outside_the_top_15_is_located_in_the_deep_pool(db, capsys, monkeypatch):
    path, ids = db
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    _, out = _run(capsys, "--db", path, "--question", QUESTION, "--watch", f"{ids['plain39']},999999")
    assert f"watched #{ids['plain39']} (Blog post 39): not in the top 15. Full-text rank" in out
    assert "watched #999999: no article with that id." in out


def test_watch_text_marks_matching_titles_sources_and_summaries(db, capsys, monkeypatch):
    path, ids = db
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    _, out = _run(capsys, "--db", path, "--question", QUESTION, "--watch-text", "example benchmarks 2099")
    assert f"Watching: #{ids['digest']}" in out


def test_character_budget_and_the_near_zero_warning(tmp_path, capsys, monkeypatch):
    path = str(tmp_path / "big.db")
    _seed(path, n_matching=30, content_chars=5000)
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    _, out = _run(capsys, "--db", path, "--question", QUESTION)
    deep = _depth_block(out, "deep")
    assert "library characters used: 40,000 of 40,000; left for feed and web: 0" in deep
    assert "WARNING: only 0 characters are left for feed and web" in deep
    quick = _depth_block(out, "quick")
    assert "WARNING" not in quick or "quick depth" in quick


def test_the_database_is_opened_read_only_and_left_byte_identical(db, capsys, monkeypatch):
    path, _ = db
    before = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    conn, _vec = probe.open_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_fail (a)")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM articles")
    conn.close()
    _stub_embed(monkeypatch)
    _run(capsys, "--db", path, "--question", QUESTION)
    assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == before


def test_it_never_constructs_the_library_class(db, capsys, monkeypatch):
    path, _ = db

    def boom(self, *a, **k):
        raise AssertionError("Library() writes tables; the probe must not construct it")

    monkeypatch.setattr(Library, "__init__", boom)
    monkeypatch.setattr(embed_mod, "embed_text", lambda text, model=embed_mod.DEFAULT_MODEL: None)
    rc, _ = _run(capsys, "--db", path, "--question", QUESTION)
    assert rc == 0


def test_the_only_network_call_is_one_embedding_of_the_question(db, capsys, monkeypatch):
    path, _ = db
    calls = []

    def one(text, model=embed_mod.DEFAULT_MODEL):
        calls.append(text)
        return embed_mod.EmbedResult(vectors=[_vec(3)], input_tokens=1, cost_usd=0.0)

    monkeypatch.setattr(embed_mod, "embed_text", one)
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    _run(capsys, "--db", path, "--question", QUESTION)
    assert calls in ([QUESTION], [])      # [] when sqlite-vec is not installed
