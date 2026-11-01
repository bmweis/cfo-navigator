"""Phase B1/B0 (issue #738): the figures query layer and landing URLs.
All data is invented (see tests/figures_fixtures.py)."""
import pytest

from linklib import figures as fg
from linklib.db import Library
from tests import figures_fixtures as fx


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    yield db
    db.close()


@pytest.fixture
def std(lib):
    fx.seed_standard(lib)
    return lib


def _by(rows, **kw):
    return [r for r in rows if all(r[k] == v for k, v in kw.items())]


# --- band parsing and bracketing -------------------------------------------------

@pytest.mark.parametrize("text,lo,hi,kind", [
    ("$20M-$30M", 20e6, 30e6, "money"), ("$20-30M", 20e6, 30e6, "money"),
    ("< $10M", 0, 10e6, "money"), ("$100M+", 100e6, float("inf"), "money"),
    ("Over $100M", 100e6, float("inf"), "money"), ("$1M to $5M", 1e6, 5e6, "money"),
    ("51-200 employees", 51, 200, "count"), ("$25M", 25e6, 25e6, "money"),
    ("$20M–$30M", 20e6, 30e6, "money"),
])
def test_parse_band(text, lo, hi, kind):
    assert fg.parse_band(text) == {"lo": lo, "hi": hi, "kind": kind}


def test_parse_band_unreadable():
    assert fg.parse_band("All companies") is None and fg.parse_band("") is None


def test_band_inside_a_printed_band_is_exact(std):
    r = fg.find_figures(std, "Net revenue retention", band="$15M", basis="ARR", statistic="median")
    mine = _by(r["rows"], report_slug="eb-2099")
    assert [x["band"] for x in mine] == ["$10M-$20M"]
    assert mine[0]["match"] == "exact" and mine[0]["gaps"] == []
    # the other publisher's revenue-basis band is returned too, but flagged
    assert [x["match"] for x in _by(r["rows"], report_slug="ssc-2099")] == ["different_basis"]


def test_band_between_printed_bands_returns_both_sides_flagged(std):
    """$25M ARR sits between $10M-$20M and $30M-$50M: both are returned, both
    flagged adjacent_band, printed text verbatim."""
    r = fg.find_figures(std, "Net revenue retention", band="$25M", basis="ARR", statistic="median")
    got = sorted(x["band"] for x in _by(r["rows"], report_slug="eb-2099"))
    assert got == ["$10M-$20M", "$30M-$50M"]
    assert all(x["match"] == "adjacent_band" for x in _by(r["rows"], report_slug="eb-2099"))
    assert all("you asked for '$25M'" in x["gaps"][0] for x in _by(r["rows"], report_slug="eb-2099"))


def test_unplaceable_band_is_counted_not_silently_dropped(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(band="All"), fx.frow(band="$10M-$20M")])
    r = fg.find_figures(lib, "Net revenue retention", band="$15M")
    assert r["unplaced_bands"] == 1 and any("cannot be placed" in n for n in r["notes"])


# --- basis, statistic, segment ---------------------------------------------------

def test_different_basis_is_flagged(std):
    r = fg.find_figures(std, "NRR", band="$25M", basis="ARR")
    ssc = _by(r["rows"], report_slug="ssc-2099")
    assert ssc and all(x["match"] == "different_basis" for x in ssc)
    assert "basis: report prints 'Revenue', you asked for 'ARR'" in ssc[0]["gaps"]


def test_basis_agreeing_rows_win_inside_a_report(std):
    r = fg.find_figures(std, "Net revenue retention", basis="ARR", statistic="median")
    assert not _by(r["rows"], report_slug="eb-2099", band_basis="Revenue")


def test_statistic_is_returned_as_printed_never_merged(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(statistic="p75", value="112")])
    r = fg.find_figures(lib, "Net revenue retention", statistic="top_quartile")
    assert r["rows"][0]["statistic"] == "p75" and r["rows"][0]["match"] == "different_statistic"
    assert "top_quartile" in r["rows"][0]["gaps"][0]


def test_segment_gap(std):
    r = fg.find_figures(std, "Net revenue retention", segment="B2B")
    assert r["rows"] and all(x["match"] == "different_segment" for x in r["rows"])


# --- metric resolution -----------------------------------------------------------

def test_exact_canonical_name_beats_alias(lib):
    fx.seed_registry(lib)
    assert fg.resolve_metric(lib, "mrr") == [{"canonical": "MRR", "match": "canonical"}]
    assert fg.resolve_metric(lib, "NRR") == [{"canonical": "Net revenue retention", "match": "alias"}]


@pytest.mark.parametrize("alias,owners", [
    ("net new mrr", {"MRR", "Net New ARR"}),
    ("capital efficiency", {"Cash Conversion Score", "Capital Consumption Ratio"}),
])
def test_ambiguous_alias_returns_every_owner(lib, alias, owners):
    fx.seed_registry(lib)
    res = fg.resolve_metric(lib, alias)
    assert {r["canonical"] for r in res} == owners
    assert all(r["match"] == "ambiguous_alias" for r in res)


def test_edited_alias_is_a_single_owner(lib):
    fx.seed_registry(lib)
    lib.conn.execute("UPDATE metric_aliases SET canonical_metric='Net New ARR', edited=1 WHERE alias='net new mrr'")
    lib.conn.commit()
    assert fg.resolve_metric(lib, "net new mrr") == [{"canonical": "Net New ARR", "match": "alias"}]


def test_ambiguous_alias_figures_are_flagged_not_assumed(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(metric="MRR", value="5"), fx.frow(metric="Net New ARR", value="7")])
    r = fg.find_figures(lib, "net new mrr")
    assert {x["canonical_metric"] for x in r["rows"]} == {"MRR", "Net New ARR"}
    assert all(x["match"] == "different_definition" for x in r["rows"])
    assert any("more than one metric" in n for n in r["notes"])


def test_alias_reaches_rows_stored_under_another_name(std):
    r = fg.find_figures(std, "net dollar retention")
    assert {x["metric"] for x in r["rows"]} <= {"Net revenue retention", "NRR"} and r["rows"]


def test_unrelated_metric_finds_nothing_and_says_so(std):
    r = fg.find_figures(std, "customer happiness index")
    assert r["rows"] == [] and r["total_matching"] == 0


def test_related_metric_by_words_is_flagged(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(metric="Net revenue retention, enterprise cohort")])
    r = fg.find_figures(lib, "revenue retention")
    assert r["rows"] and r["rows"][0]["match"] == "related_metric"


def test_detect_metrics_by_rules(lib):
    fx.seed_registry(lib)
    got = fg.detect_metrics(lib, "How do our NRR and burn multiple compare at $25M ARR?")
    assert [d["canonical"] for d in got] == ["Net revenue retention", "Burn multiple"]
    assert fg.detect_metrics(lib, "How should I structure my sales team?") == []


# --- editions --------------------------------------------------------------------

def test_default_is_newest_edition_with_a_note(std):
    r = fg.find_figures(std, "Net revenue retention", band="$15M", basis="ARR", statistic="median")
    assert {x["report_year"] for x in r["rows"]} == {"2099"}
    assert any("Older editions exist" in n and "2098" in n for n in r["notes"])


def test_all_editions_orders_by_year(std):
    r = fg.find_figures(std, "Net revenue retention", band="$15M", basis="ARR", statistic="median", all_editions=True)
    # Newest first: a cap that cuts rows cuts the oldest (2026-11).
    assert [x["report_year"] for x in _by(r["rows"], source="Example Benchmarks")] == ["2099", "2098"]


def test_year_filter(std):
    r = fg.find_figures(std, "Net revenue retention", year=2098)
    assert {x["report_year"] for x in r["rows"]} == {"2098"}


def test_newest_edition_without_the_metric_is_disclosed(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2098", [fx.frow(report="Example Benchmarks 2098", year="2098")])
    fx.load_report(lib, "eb-2099", [fx.frow(metric="Burn multiple")])
    r = fg.find_figures(lib, "Net revenue retention")
    assert {x["report_year"] for x in r["rows"]} == {"2098"}
    assert any("2099" in n and "no figure for this" in n for n in r["notes"])


def test_truncation_is_stated(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(band=f"${i}M-${i + 1}M") for i in range(1, 30)])
    r = fg.find_figures(lib, "Net revenue retention", limit=5)
    assert r["truncated"] and r["returned"] == 5 and r["total_matching"] == 29
    assert any("showing the first 5" in n for n in r["notes"])


# --- summary ---------------------------------------------------------------------

def test_summary_refuses_across_denominators(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(denominator="per employee", value="10"),
                                    fx.frow(denominator="per customer", value="12")])
    rows = fg.find_figures(lib, "Net revenue retention")["rows"]
    s = fg.summarize(rows)
    assert s["refused"] and "denominator" in s["reason"]


def test_summary_computes_only_when_everything_agrees_and_labels_it(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(value="100")])
    fx.load_report(lib, "eb-2098", [fx.frow(report="Example Benchmarks 2098", year="2098", value="110")])
    rows = fg.find_figures(lib, "Net revenue retention", all_editions=True)["rows"]
    s = fg.summarize(rows)
    assert not s["refused"] and s["median"] == 105 and s["label"] == "derived, not in report"


def test_summary_refuses_a_non_numeric_value(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [fx.frow(value="N/A"), fx.frow(value="5")])
    assert fg.summarize(fg.find_figures(lib, "Net revenue retention")["rows"])["refused"]


# --- coverage, digests, cards ----------------------------------------------------

def test_coverage_lists_what_can_be_asked_for(std):
    c = fg.coverage(std, "Net revenue retention")
    assert "median" in c["statistics"] and "p25" in c["statistics"]
    assert {"$10M-$20M", "$30M-$50M"} <= set(c["bands"])
    assert {r["report_slug"] for r in c["reports"]} >= {"eb-2099", "eb-2098"}


def test_get_digest_is_full_text_by_id_and_slug(lib):
    fx.load_report(lib, "eb-2099", [fx.frow()])
    big = "x" * 20000
    aid = fx.add_digest(lib, "eb-2099", text=big)
    assert len(fg.get_digest(lib, aid)["text"]) == 20000
    assert fg.get_digest(lib, "eb-2099")["article_id"] == aid
    assert fg.get_digest(lib, "nope") is None


def test_get_digest_refuses_a_non_digest_article(lib):
    from linklib.db import Article
    aid = lib.upsert(Article(url="https://x.example.test/a", title="Plain", content="paid text", tags=["x"], enriched=1))
    assert fg.get_digest(lib, aid) is None


def test_metric_definition_returns_the_workbook_card(lib):
    fx.seed_registry(lib)
    fx.add_digest(lib, "saas-metric-burn-multiple", text="Burn multiple = net burn / net new ARR.")
    d = fg.metric_definition(lib, "cash burn ratio")
    assert d["found"] and d["metrics"][0]["canonical_metric"] == "Burn multiple"
    assert "net burn" in d["metrics"][0]["card"]["text"]
    assert "cash burn ratio" in d["metrics"][0]["aliases"]
    assert fg.metric_definition(lib, "zzz")["found"] is False


# --- B0 landing URLs -------------------------------------------------------------

def test_landing_resolution_order(lib):
    fx.load_report(lib, "r-manual", [fx.frow()])
    fx.load_report(lib, "r-landing", [fx.frow()])
    fx.load_report(lib, "r-digest", [fx.frow()])
    fx.load_report(lib, "r-none", [fx.frow()])
    lib.set_report_landing_manual("r-manual", "https://m.example.test/r")
    lib.conn.execute("UPDATE benchmark_reports SET landing_url='https://l.example.test/r?digest=zzz&x=1' WHERE report_slug='r-landing'")
    lib.conn.execute("UPDATE benchmark_reports SET landing_url='https://ignored.example.test' WHERE report_slug='r-manual'")
    lib.conn.commit()
    fx.add_digest(lib, "r-digest", url="https://d.example.test/page?digest=r-digest")
    got = fg.resolve_landings(lib)
    assert got["r-manual"] == {"url": "https://m.example.test/r", "how": "manual"}
    assert got["r-landing"] == {"url": "https://l.example.test/r?x=1", "how": "landing_url"}
    assert got["r-digest"] == {"url": "https://d.example.test/page", "how": "digest_slug"}
    assert got["r-none"] == {"url": "", "how": "none"}


def test_two_digests_for_one_slug_is_unresolved_never_a_guess(lib):
    fx.load_report(lib, "r1", [fx.frow()])
    fx.add_digest(lib, "r1", url="https://a.example.test/p?digest=r1")
    fx.add_digest(lib, "r1", url="https://b.example.test/p?digest=r1")
    assert fg.resolve_landings(lib)["r1"]["how"] == "none"


def test_reload_never_clobbers_the_manual_landing_url(lib):
    fx.load_report(lib, "r1", [fx.frow()])
    lib.set_report_landing_manual("r1", "https://m.example.test/r")
    fx.load_report(lib, "r1", [fx.frow(), fx.frow(band="$30M-$50M")])
    assert fg.resolve_landings(lib)["r1"]["url"] == "https://m.example.test/r"


def test_manual_landing_url_must_be_http(lib):
    fx.load_report(lib, "r1", [fx.frow()])
    with pytest.raises(ValueError):
        lib.set_report_landing_manual("r1", "javascript:alert(1)")
    assert lib.set_report_landing_manual("r1", "") is True
    assert lib.set_report_landing_manual("nope", "https://x.example.test") is False


def test_row_without_a_link_says_so(std):
    r = fg.find_figures(std, "Net revenue retention")
    assert r["rows"][0]["link"] == "" and r["rows"][0]["link_note"] == "no link on file"


# --- B0 admin cell ---------------------------------------------------------------

def test_admin_page_shows_landing_cell_and_saves_it(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "pw")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "s.opml"))
    import webapp.app as appmod
    importlib.reload(appmod)
    db = Library(str(tmp_path / "t.db"))
    fx.load_report(db, "eb-2099", [fx.frow()])
    db.close()
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "admin", "password": "pw"}, follow_redirects=False).status_code in (200, 302, 303)
    html = c.get("/admin/reader/figures").text
    assert "Landing URL" in html and "Not set" in html and "0 of 1 reports" in html
    r = c.post("/admin/reader/figures/landing", data={"report_slug": "eb-2099", "landing_url": "https://r.example.test/x"})
    assert r.status_code == 200 and "https://r.example.test/x" in r.text and "1 of 1 reports" in r.text
    bad = c.post("/admin/reader/figures/landing", data={"report_slug": "eb-2099", "landing_url": "ftp://x"})
    assert bad.status_code == 400 and "http" in bad.text


def test_landing_report_script_is_read_only_and_names_unresolved(tmp_path, capsys):
    from scripts import report_figure_landings as rep
    path = str(tmp_path / "t.db")
    db = Library(path)
    fx.load_report(db, "r-a", [fx.frow()])
    fx.load_report(db, "r-b", [fx.frow()])
    db.set_report_landing_manual("r-a", "https://m.example.test/r")
    db.close()
    before = open(path, "rb").read()
    assert rep.main(["--db", path]) == 0
    out = capsys.readouterr().out
    assert "1 of 2 reports resolve" in out and "r-b" in out and "r-a  " not in out.split("Unresolved")[1]
    assert open(path, "rb").read() == before
