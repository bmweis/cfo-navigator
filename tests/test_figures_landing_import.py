"""Landing-URL import on /admin/reader/figures (turn-20 follow-up, Part D).
Invented slugs and example.test URLs only; the real TSV is not in the repo."""
import importlib
import json
import os
import re
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402
from linklib import figures as fg  # noqa: E402

HEADER = "report_slug\tlanding_url\n"


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
    lib = appmod._lib()
    try:
        for slug in ("alpha-2099", "beta-2099", "gamma-2099"):
            load_report(lib, slug, [frow(report=f"Report {slug}")])
        lib.set_report_landing_manual("beta-2099", "https://example.test/typed-by-hand")
    finally:
        lib.close()
    yield appmod, tmp_path, db


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    if login:
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _manual(appmod):
    lib = appmod._lib()
    try:
        return {r["report_slug"]: r["landing_url_manual"] for r in lib.list_benchmark_reports()}
    finally:
        lib.close()


def _post_preview(c, text, replace=False):
    data = {"replace_existing": "1"} if replace else {}
    return c.post("/admin/reader/figures/landing-preview", data=data,
                  files={"tsv": ("l.tsv", text.encode(), "text/tab-separated-values")})


TSV = (HEADER + "alpha-2099\thttps://example.test/alpha\n"
       "beta-2099\thttps://example.test/from-file\n"
       "gamma-2099\thttps://bit.ly/abc123\n"
       "nope-2099\thttps://example.test/nope\n")


def test_parser_rejects_bad_urls_fields_and_repeats():
    rows, errs = fg.parse_landing_tsv("a\tftp://x.test/a\nb\thttps://x.test/b c\nc\n"
                                      "d\thttps://x.test/d\nd\thttps://x.test/e\n")
    assert len(errs) == 4 and [r[1] for r in rows] == ["d"]
    assert fg.parse_landing_tsv("")[1] == ["The file has no rows."]
    assert fg.parse_landing_tsv(TSV)[1] == []


def test_short_link_detection():
    assert fg.is_short_link("https://bit.ly/x") and not fg.is_short_link("https://example.test/x")


def test_preview_writes_nothing_and_shows_each_status_and_the_short_link_flag(env):
    appmod, _, _ = env
    before = _manual(appmod)
    with _client(appmod) as c:
        r = _post_preview(c, TSV)
    assert r.status_code == 200
    t = r.text
    for status in ("Set", "Will replace (kept, not ticked)", "Unknown report"):
        assert status in t
    assert "short link" in t and "https://example.test/typed-by-hand" in t
    assert "Set 2 landing URLs" in t        # alpha and gamma; beta kept
    assert _manual(appmod) == before


def test_load_sets_empty_keeps_hand_typed_value_unless_ticked(env):
    appmod, tmp, _ = env
    with _client(appmod) as c:
        r = _post_preview(c, TSV)
        text = re.search(r'<textarea name="tsv_text" hidden>(.*?)</textarea>', r.text, re.S).group(1)
        import html
        r = c.post("/admin/reader/figures/landing-load", data={"tsv_text": html.unescape(text), "replace_existing": "0"})
    assert r.status_code == 200 and "2 set" in r.text and "1 kept" in r.text and "1 unknown report" in r.text
    m = _manual(appmod)
    assert m["alpha-2099"] == "https://example.test/alpha" and m["gamma-2099"] == "https://bit.ly/abc123"
    assert m["beta-2099"] == "https://example.test/typed-by-hand"
    assert "3 of 3 reports now have a landing URL" in r.text
    with _client(appmod) as c:
        r = c.post("/admin/reader/figures/landing-load", data={"tsv_text": TSV, "replace_existing": "1"})
    assert "1 replaced" in r.text and _manual(appmod)["beta-2099"] == "https://example.test/from-file"


def test_load_is_all_or_nothing_and_audited(env, monkeypatch):
    appmod, tmp, db = env
    lib = appmod._lib()
    real = lib.conn
    rows, _ = fg.parse_landing_tsv(TSV)
    # A failure partway through must leave every row as it was.
    calls = {"n": 0}
    orig = lib.conn.execute

    class Wrap:
        def __getattr__(self, k):
            return getattr(real, k)

        def execute(self, sql, *a):
            if sql.startswith("UPDATE benchmark_reports"):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("boom")
            return orig(sql, *a)
    lib.conn = Wrap()
    with pytest.raises(RuntimeError):
        lib.apply_landing_import(rows, replace_existing=False)
    lib.conn = real
    lib.close()
    assert _manual(appmod)["alpha-2099"] == ""
    with _client(appmod) as c:
        c.post("/admin/reader/figures/landing-load", data={"tsv_text": TSV, "replace_existing": "0"})
    audit = [json.loads(l) for l in (tmp / "figures-audit.jsonl").read_text().splitlines()]
    assert audit[-1]["event"] == "landing_import" and audit[-1]["set"] == 2


def test_bad_file_writes_nothing_and_routes_are_admin_session_only(env):
    appmod, _, _ = env
    before = _manual(appmod)
    with _client(appmod) as c:
        r = _post_preview(c, "alpha-2099\tnot-a-url\n")
        assert r.status_code == 400 and "must start with http" in r.text
        r = c.post("/admin/reader/figures/landing-load", data={"tsv_text": "alpha-2099\tnot-a-url\n"})
        assert r.status_code == 400
    assert _manual(appmod) == before
    with _client(appmod, login=False) as c:
        for path in ("landing-preview", "landing-load"):
            assert c.post(f"/admin/reader/figures/{path}", headers={"X-Save-Token": "savetok"},
                          follow_redirects=False).status_code == 401
