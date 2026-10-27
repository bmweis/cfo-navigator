"""Benchmark figures (issue #738, Phase A): tables, zip loader, registry seed,
and the /admin/checks invariant.

Every fixture is invented: the publisher is "Example Benchmarks 2099" and the
figures are made up. No third-party report data is ever committed here."""
import csv
import hashlib
import importlib
import io
import json
import os
import zipfile

import pytest

from linklib import figures as fg
from linklib.db import Article, DIGEST_TAG, Library

HEADER = list(fg.COLUMNS)


def _row(metric="Net revenue retention", value="101%", segment="All", band="$10M-$50M ARR",
         statistic="median", note=""):
    return ["Example Benchmarks", "Example Benchmarks 2099", "2099", "FY2098", "ARR", band,
            segment, metric, "", statistic, value, "percent", "120", "14", "table 3", note]


def _csv(rows, header=HEADER) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _zip(files: dict[str, bytes], manifest: list[tuple] | None = None, extra: dict | None = None) -> bytes:
    """files: filename -> csv bytes. manifest defaults to the true values."""
    if manifest is None:
        manifest = []
        for name, data in files.items():
            n = data.decode("utf-8").count("\n") - 1
            manifest.append((name[:-4], name, n, len(data), hashlib.sha256(data).hexdigest()))
    lines = ["report_slug\tfile\trows\tbytes\tsha256"]
    lines += ["\t".join(str(c) for c in m) for m in manifest]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("MANIFEST.tsv", "\n".join(lines) + "\n")
        for name, data in {**files, **(extra or {})}.items():
            zf.writestr(name, data)
    return out.getvalue()


GOOD_A = _csv([_row(), _row(metric="Gross margin", value="78.5%"), _row(metric="CAC payback", value="N/A")])
GOOD_B = _csv([_row(metric="Burn multiple", value="1.4x")])


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


# --- value_num ----------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("101", 101.0), ("78.5", 78.5), ("78.5%", 78.5), ("(15%)", -15.0), ("(15)", -15.0),
    ("$1,200", 1200.0), ("$1,200,000.50", 1200000.5), ("0.7x", 0.7), ("$12M", 12.0),
    ("-3.2", -3.2), (".5", 0.5), ("  42  ", 42.0),
    ("N/A", None), ("—", None), ("", None), ("10-20", None), ("10–20%", None),
    ("1,2", None), ("12,34", None), ("1.2.3", None), ("~5", None), ("<1%", None),
    ("$-", None), ("(-5)", None), ("−5", None), ("5%%", None), ("$ 5", None),
    ("1.4X", None), ("5k", None), ("n/a", None), ("12 months", None),
])
def test_value_num_parses_only_unambiguous_plain_numbers(value, expected):
    assert fg.parse_value_num(value) == expected


# --- zip validation -------------------------------------------------------------

def test_a_clean_zip_parses_every_row_verbatim():
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A, "eb-2099-b.csv": GOOD_B}))
    assert not z.errors and [f.ok for f in z.files] == [True, True]
    a = z.files[0]
    assert a.parsed_rows == a.manifest_rows == 3
    assert a.rows[0]["value"] == "101%" and a.rows[0]["value_num"] == 101.0
    assert a.rows[2]["value"] == "N/A" and a.rows[2]["value_num"] is None
    assert a.null_value_num == 1
    assert [r["row_no"] for r in a.rows] == [1, 2, 3]


def test_a_short_row_is_rejected():
    bad = _csv([_row(), _row()[:15]])
    z = fg.parse_zip(_zip({"eb-2099.csv": bad, "eb-2099-b.csv": GOOD_B}))
    assert not z.files[0].ok and "Row 2 has 15 fields, not 16" in " ".join(z.files[0].errors)
    assert z.files[1].ok                        # one bad file does not sink the rest


def test_a_long_row_is_rejected():
    bad = _csv([_row() + ["extra"]])
    z = fg.parse_zip(_zip({"eb-2099.csv": bad}))
    assert "17 fields" in " ".join(z.files[0].errors)


@pytest.mark.parametrize("header", [
    HEADER[:-1],                                   # missing a column
    HEADER[1:] + HEADER[:1],                       # wrong order
    [h.upper() for h in HEADER],                   # wrong case
    HEADER + ["extra"],
])
def test_a_wrong_header_is_rejected(header):
    bad = _csv([], header=header)
    z = fg.parse_zip(_zip({"eb-2099.csv": bad}))
    assert not z.files[0].ok and "Wrong header" in z.files[0].errors[0]


def test_a_checksum_mismatch_is_rejected():
    m = [("eb-2099", "eb-2099.csv", 3, len(GOOD_A), "0" * 64)]
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, manifest=m))
    assert not z.files[0].ok and "sha256 does not match" in " ".join(z.files[0].errors)


def test_a_row_count_mismatch_is_rejected():
    m = [("eb-2099", "eb-2099.csv", 4, len(GOOD_A), hashlib.sha256(GOOD_A).hexdigest())]
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, manifest=m))
    assert "manifest says 4 rows; the file has 3" in " ".join(z.files[0].errors)


def test_a_byte_count_mismatch_is_rejected():
    m = [("eb-2099", "eb-2099.csv", 3, len(GOOD_A) + 1, hashlib.sha256(GOOD_A).hexdigest())]
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, manifest=m))
    assert "bytes" in " ".join(z.files[0].errors)


def test_files_missing_from_the_zip_or_the_manifest_are_rejected():
    m = [("eb-2099", "eb-2099.csv", 3, len(GOOD_A), hashlib.sha256(GOOD_A).hexdigest()),
         ("ghost", "ghost.csv", 1, 10, "a" * 64)]
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, manifest=m, extra={"stray.csv": GOOD_B}))
    by = {f.file: f for f in z.files}
    assert by["eb-2099.csv"].ok
    assert "missing from the zip" in by["ghost.csv"].errors[0]
    assert "not in the manifest" in by["stray.csv"].errors[0]


def test_non_utf8_is_rejected():
    bad = _csv([_row(note="café")]).replace("café".encode(), "café".encode("latin-1"))
    m = [("eb-2099", "eb-2099.csv", 1, len(bad), hashlib.sha256(bad).hexdigest())]
    z = fg.parse_zip(_zip({"eb-2099.csv": bad}, manifest=m))
    assert z.files[0].errors == ["The file is not UTF-8 text."]


def test_slug_must_equal_the_file_stem_and_be_unique():
    sha = hashlib.sha256(GOOD_A).hexdigest()
    m = [("other", "eb-2099.csv", 3, len(GOOD_A), sha)]
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, manifest=m))
    assert "report_slug must equal" in " ".join(z.files[0].errors)


def test_whole_zip_problems():
    assert fg.parse_zip(b"not a zip").errors == ["This is not a zip file."]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("eb-2099.csv", GOOD_A)
    assert "MANIFEST.tsv is missing" in fg.parse_zip(out.getvalue()).errors[0]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("MANIFEST.tsv", "slug\tfile\n")
    assert "header must be exactly" in fg.parse_zip(out.getvalue()).errors[0]


def test_macos_archive_junk_is_ignored_with_a_note():
    z = fg.parse_zip(_zip({"eb-2099.csv": GOOD_A}, extra={"__MACOSX/._eb-2099.csv": b"\x00\x01"}))
    assert not z.errors and all(f.ok for f in z.files) and len(z.files) == 1
    assert any("macOS" in n for n in z.notes)


# --- loading --------------------------------------------------------------------

def _load(lib, data):
    z = fg.parse_zip(data)
    return [lib.replace_benchmark_report(f.meta(), f.rows, loaded_by="admin") for f in z.loadable]


def test_load_stores_rows_verbatim_and_the_report_record(lib):
    assert _load(lib, _zip({"eb-2099.csv": GOOD_A})) == [{"replaced": 0, "loaded": 3}]
    rows = [dict(r) for r in lib.conn.execute(
        "SELECT * FROM benchmark_figures ORDER BY row_no")]
    assert [r["value"] for r in rows] == ["101%", "78.5%", "N/A"]
    assert [r["value_num"] for r in rows] == [101.0, 78.5, None]
    assert rows[0]["band"] == "$10M-$50M ARR" and rows[0]["page"] == "14"
    rep = lib.list_benchmark_reports()[0]
    assert (rep["report_slug"], rep["report"], rep["report_year"], rep["row_count"],
            rep["stored_rows"], rep["loaded_by"]) == ("eb-2099", "Example Benchmarks 2099", "2099", 3, 3, "admin")
    assert rep["sha256"] == hashlib.sha256(GOOD_A).hexdigest()


def test_reload_is_idempotent(lib):
    data = _zip({"eb-2099.csv": GOOD_A, "eb-2099-b.csv": GOOD_B})
    _load(lib, data)
    assert _load(lib, data) == [{"replaced": 3, "loaded": 3}, {"replaced": 1, "loaded": 1}]
    assert lib.figures_summary()["figures"] == 4 and lib.figures_summary()["reports"] == 2
    assert lib.figures_manifest_problems() == []


def test_a_reload_with_different_rows_replaces_them(lib):
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    _load(lib, _zip({"eb-2099.csv": _csv([_row(metric="Rule of 40", value="38")])}))
    assert [r[0] for r in lib.conn.execute("SELECT metric FROM benchmark_figures")] == ["Rule of 40"]


def test_a_mid_file_error_rolls_the_whole_report_back(lib):
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    z = fg.parse_zip(_zip({"eb-2099.csv": _csv([_row(metric="New one"), _row(metric="New two")])}))
    f = z.files[0]
    rows = [dict(r) for r in f.rows]
    rows[1]["row_no"] = rows[0]["row_no"]          # duplicate key on the second insert
    meta = {**f.meta(), "row_count": 2}
    with pytest.raises(Exception):
        lib.replace_benchmark_report(meta, rows)
    # Old rows and the old report record are untouched; nothing half loaded.
    assert [r[0] for r in lib.conn.execute("SELECT metric FROM benchmark_figures ORDER BY row_no")] == \
        ["Net revenue retention", "Gross margin", "CAC payback"]
    assert lib.list_benchmark_reports()[0]["row_count"] == 3
    assert lib.figures_manifest_problems() == []


def test_fts_index_follows_inserts_and_deletes(lib):
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    hit = lib.conn.execute("SELECT rowid FROM benchmark_figures_fts WHERE benchmark_figures_fts MATCH 'margin'").fetchall()
    assert len(hit) == 1
    _load(lib, _zip({"eb-2099.csv": GOOD_B}))
    assert lib.conn.execute("SELECT count(*) FROM benchmark_figures_fts WHERE benchmark_figures_fts MATCH 'margin'").fetchone()[0] == 0
    assert lib.conn.execute("SELECT count(*) FROM benchmark_figures_fts WHERE benchmark_figures_fts MATCH 'burn'").fetchone()[0] == 1


def test_digest_article_links_only_when_exactly_one_matches(lib):
    lib.upsert(Article(url="https://example-benchmarks.test/report-2099?digest=growth",
                       title="Example Benchmarks 2099: Growth", source="Example Benchmarks 2099",
                       tags=[DIGEST_TAG], content="x", summary="y"))
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    rep = lib.list_benchmark_reports()[0]
    assert rep["landing_url"] == "https://example-benchmarks.test/report-2099"
    assert rep["article_id"] is not None
    lib.upsert(Article(url="https://example-benchmarks.test/report-2099?digest=retention",
                       title="Example Benchmarks 2099: Retention", source="Example Benchmarks 2099",
                       tags=[DIGEST_TAG], content="x", summary="y"))
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    rep = lib.list_benchmark_reports()[0]
    assert rep["article_id"] is None and rep["landing_url"] is None   # two digests: no guess


# --- invariant ------------------------------------------------------------------

def test_invariant_fails_when_a_row_is_deleted(lib):
    _load(lib, _zip({"eb-2099.csv": GOOD_A, "eb-2099-b.csv": GOOD_B}))
    assert lib.figures_manifest_problems() == []
    lib.conn.execute("DELETE FROM benchmark_figures WHERE report_slug='eb-2099' AND row_no=2")
    lib.conn.execute("DELETE FROM benchmark_figures WHERE report_slug='eb-2099-b'")
    lib.conn.commit()
    assert lib.figures_manifest_problems() == [
        "eb-2099: short (2 stored, manifest says 3)",
        "eb-2099-b: missing (manifest says 1 rows, 0 stored)",
    ]


def test_invariant_names_figures_with_no_report_record(lib):
    _load(lib, _zip({"eb-2099.csv": GOOD_A}))
    lib.conn.execute("DELETE FROM benchmark_reports")
    lib.conn.commit()
    assert lib.figures_manifest_problems() == ["eb-2099: 3 figures with no report record"]


# --- registry -------------------------------------------------------------------

REGISTRY = ("canonical_metric\tcard_url\tcontains\n"
            "Net revenue retention\thttps://cards.example.test/nrr\tContains: NRR, net dollar retention, NDR. sources: Example Benchmarks 2099\n"
            "Burn multiple\thttps://cards.example.test/burn\tContains: burn multiple, cash burn ratio\n")


def test_split_aliases_drops_the_sources_tail():
    assert fg.split_aliases("Contains: NRR, Net Dollar Retention, NDR. sources: A, B") == \
        ["nrr", "net dollar retention", "ndr"]


def test_registry_seed_is_idempotent_and_never_touches_an_edited_alias(lib):
    rows, errors = fg.parse_registry_tsv(REGISTRY)
    assert not errors and len(rows) == 2
    first = lib.seed_metric_registry(rows)
    assert first["metrics_added"] == 2 and first["aliases_added"] == 6
    assert lib.seed_metric_registry(rows) == {"metrics_added": 0, "metrics_updated": 0, "aliases_added": 0,
                                              "aliases_updated": 0, "aliases_kept_edited": 0, "conflicts": []}
    lib.conn.execute("UPDATE metric_aliases SET canonical_metric='Burn multiple', edited=1 WHERE alias='ndr'")
    lib.conn.commit()
    out = lib.seed_metric_registry(rows)
    assert out["aliases_kept_edited"] == 1
    assert lib.conn.execute("SELECT canonical_metric FROM metric_aliases WHERE alias='ndr'").fetchone()[0] == "Burn multiple"


def test_registry_conflicts_keep_the_first_owner():
    rows, _ = fg.parse_registry_tsv("A metric\turl\tContains: shared\nB metric\turl\tContains: shared\n")
    lib = Library(":memory:")
    out = lib.seed_metric_registry(rows)
    assert len(out["conflicts"]) == 1
    assert lib.conn.execute("SELECT canonical_metric FROM metric_aliases WHERE alias='shared'").fetchone()[0] == "A metric"


def test_registry_bad_lines_refuse_the_file():
    rows, errors = fg.parse_registry_tsv("Only two\tcolumns\n")
    assert errors == ["Line 1 has 2 fields, not 3."]


def test_metric_name_map_exists_and_starts_empty(lib):
    assert lib.figures_summary()["name_map"] == 0


# --- admin routes -----------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "lib.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetok")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, tmp_path


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    if login:
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _summary(appmod):
    lib = appmod._lib()
    try:
        return lib.figures_summary()
    finally:
        lib.close()


def test_routes_are_admin_session_only(env):
    appmod, _ = env
    with _client(appmod, login=False) as c:
        r = c.get("/admin/reader/figures", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/admin/reader/figures", headers={"X-Save-Token": "savetok"},
                     follow_redirects=False).status_code == 401
        for path in ("preview", "load", "registry"):
            assert c.post(f"/admin/reader/figures/{path}", headers={"X-Save-Token": "savetok"},
                          follow_redirects=False).status_code == 401
            assert c.post(f"/admin/reader/figures/{path}", follow_redirects=False).status_code == 401


def test_preview_writes_nothing_and_load_loads_the_clean_files(env):
    appmod, tmp = env
    bad = _csv([_row()[:15]])
    data = _zip({"eb-2099.csv": GOOD_A, "eb-2099-bad.csv": bad})
    with _client(appmod) as c:
        page = c.get("/admin/reader/figures")
        assert page.status_code == 200 and "Benchmark figures" in page.text
        r = c.post("/admin/reader/figures/preview", files={"zip": ("f.zip", data, "application/zip")})
        assert r.status_code == 200
        assert "New" in r.text and "Error: Row 1 has 15 fields" in r.text and ">Error</span>" in r.text and "Load 1 report" in r.text
        assert _summary(appmod)["figures"] == 0
        import re
        b64 = re.search(r'name="zip_b64" value="([^"]+)"', r.text).group(1)
        r = c.post("/admin/reader/figures/load", data={"zip_b64": b64})
        assert r.status_code == 200 and "Loaded 3" in r.text and "Error" in r.text
        assert _summary(appmod)["figures"] == 3
        r = c.post("/admin/reader/figures/preview", files={"zip": ("f.zip", data, "application/zip")})
        assert "Will replace 3 rows" in r.text
        r = c.post("/admin/reader/figures/load", data={"zip_b64": b64})
        assert "Replaced 3" in r.text
    assert _summary(appmod)["figures"] == 3
    lines = [json.loads(ln) for ln in (tmp / fg.AUDIT_FILE).read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["user"] == "admin"
    assert lines[0]["zip_sha256"] == hashlib.sha256(data).hexdigest()
    assert {r["report_slug"]: r["result"] for r in lines[0]["reports"]} == \
        {"eb-2099": "Loaded 3", "eb-2099-bad": "Error"}
    lib = appmod._lib()
    try:
        audit = lib.conn.execute("SELECT detail FROM archive_audit_log").fetchall()
    finally:
        lib.close()
    assert len(audit) == 2 and "figures load" in audit[0][0]


def test_a_whole_zip_error_offers_no_load(env):
    appmod, _ = env
    with _client(appmod) as c:
        r = c.post("/admin/reader/figures/preview", files={"zip": ("f.zip", b"nope", "application/zip")})
        assert r.status_code == 400 and "not a zip file" in r.text and "zip_b64" not in r.text


def test_registry_route_seeds_and_refuses_bad_files(env):
    appmod, _ = env
    with _client(appmod) as c:
        r = c.post("/admin/reader/figures/registry", files={"tsv": ("r.tsv", REGISTRY.encode(), "text/plain")})
        assert r.status_code == 200 and "2 metrics added" in r.text
        r = c.post("/admin/reader/figures/registry", files={"tsv": ("r.tsv", b"a\tb\n", "text/plain")})
        assert r.status_code == 400 and "Nothing was written" in r.text
    assert _summary(appmod)["registry"] == 2


def test_check_row_reports_a_short_report(env):
    appmod, _ = env
    from webapp import checks
    lib = appmod._lib()
    try:
        _load(lib, _zip({"eb-2099.csv": GOOD_A}))
        assert checks.figures_manifest_problems() == []
        lib.conn.execute("DELETE FROM benchmark_figures WHERE row_no=1")
        lib.conn.commit()
    finally:
        lib.close()
    probs = checks.figures_manifest_problems()
    assert probs[0] == "eb-2099: short (2 stored, manifest says 3)"
    assert "/admin/reader/figures" in probs[-1]


def test_hub_card_is_present(env):
    appmod, _ = env
    assert "/admin/reader/figures" in {h for h, _, _ in appmod._LIBRARY_TOOLS}
    assert appmod.hub_nav_orphans() == []
