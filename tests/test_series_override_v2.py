"""Series override v2: the override is the WHOLE family key (no source), and same-year editions order by
month. Real stored source strings and Mostly Metrics titles; invented figures and invented KeyBanc titles."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report, seed_registry  # noqa: E402
from linklib import figures as fg  # noqa: E402
from linklib.db import Library  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    lb = Library(str(tmp_path / "lib.db"))
    seed_registry(lb)
    yield lb
    lb.close()


def _set(lib, slug, series):
    cur = lib.get_benchmark_report(slug)
    lib.save_benchmark_report_edit(slug, source=cur["source"], report=cur["report"], report_year=cur["report_year"],
                                   landing_url="", cited_source="", cited_source_metric="", series_override=series)


def _titles(r):
    return sorted({x["report"] for x in r["rows"]})


KEYBANC = [  # (slug, source string as stored, invented title, year)
    ("kb-2020", "KeyBanc Capital Markets (summary by David Skok, forEntrepreneurs)", "SaaS Survey 2020", "2020"),
    ("kb-2021", "KeyBanc Capital Markets", "Private SaaS Company Survey 2021", "2021"),
    ("kb-2022", "KeyBanc Capital Markets", "Private SaaS Company Survey 2022", "2022"),
    ("kb-2023", "KeyBanc Capital Markets and Sapphire Ventures", "Annual SaaS Survey 2023", "2023"),
    ("kb-2025", "KeyBanc Capital Markets and Sapphire Ventures", "Annual SaaS Survey 2025", "2025"),
    ("kb-2018", "KeyBanc Capital Markets (KBCM Technology Group) (summary by David Skok, forEntrepreneurs)", "SaaS Survey 2018", "2018"),
    ("kb-2011", "Pacific Crest Securities (summary by David Skok, forEntrepreneurs)", "SaaS Survey 2011", "2011"),
]


def _load_keybanc(lib):
    for slug, src, title, yr in KEYBANC:
        load_report(lib, slug, [frow(report=title, year=yr, source=src, value="100")])


def test_override_alone_joins_changed_source_strings(lib):
    _load_keybanc(lib)
    assert len(_titles(fg.find_figures(lib, "Net revenue retention"))) > 3  # source spellings keep them apart
    for slug, *_ in KEYBANC:
        _set(lib, slug, "KeyBanc SaaS survey")
    r = fg.find_figures(lib, "Net revenue retention")
    assert _titles(r) == ["Annual SaaS Survey 2025"]
    assert any("Older editions exist and were left out" in n and "SaaS Survey 2011" in n for n in r["notes"])
    allr = fg.find_figures(lib, "Net revenue retention", all_editions=True)
    assert len(_titles(allr)) == len(KEYBANC)


def test_benchmarkit_and_drivetrain_join(lib):
    load_report(lib, "bk-2024", [frow(report="Benchmarkit SaaS Metrics 2024", year="2024", source="Benchmarkit", value="90")])
    load_report(lib, "bk-2025", [frow(report="Drivetrain Benchmarkit SaaS 2025", year="2025",
                                      source="Benchmarkit and Drivetrain", value="95")])
    _set(lib, "bk-2024", "Benchmarkit SaaS")
    _set(lib, "bk-2025", "Benchmarkit SaaS")
    assert _titles(fg.find_figures(lib, "Net revenue retention")) == ["Drivetrain Benchmarkit SaaS 2025"]


MM = [  # real stored titles
    ("mm-apr25", "Benchmarking Club April 2025 edition", "2025", "Q1 2025"),
    ("mm-jul25", "Benchmarking Club July 2025 edition", "2025", "Q2 2025"),
    ("mm-nov25", "Benchmarking Club November 2025 edition", "2025", "Q3 2025"),
    ("mm-feb26", "Benchmarking Club February 2026 edition", "2026", "Q4 2025"),
    ("mm-may26", "Mostly Metrics Benchmarking Club May 2026 edition", "2026", "Q1 2026"),
]


def _load_mm(lib, override=True):
    for slug, title, yr, dp in MM:
        load_report(lib, slug, [frow(report=title, year=yr, source="Mostly Metrics", data_period=dp, value="50"),
                                frow(report=title, year=yr, source="Mostly Metrics", data_period="Q4 2024", value="49")])
        if override:
            _set(lib, slug, "Mostly Metrics Benchmarking Club")


def test_same_year_editions_default_is_the_newest_month(lib):
    _load_mm(lib)
    r = fg.find_figures(lib, "Net revenue retention")
    assert _titles(r) == ["Mostly Metrics Benchmarking Club May 2026 edition"]
    note = " ".join(r["notes"])
    for t in ("April 2025", "July 2025", "November 2025", "February 2026"):
        assert t in note


def test_same_year_all_editions_window_orders_by_month(lib):
    _load_mm(lib)
    r = fg.find_figures(lib, "Net revenue retention", all_editions=True, recent_window=True)
    titles = _titles(r)
    assert len(titles) == fg.EDITION_WINDOW_COUNT  # four newest editions; April 2025 is the one left out
    assert not any("April 2025" in t for t in titles)
    assert any("April 2025" in n for n in r["notes"])


def test_no_override_family_key_is_unchanged():
    row = {"source": "Example  Benchmarks", "report": "Alpha SaaS Benchmarks 2098"}
    assert fg._family(row) == ("example benchmarks", "alpha saas benchmarks")


def test_override_never_collides_with_a_derived_family(lib):
    # A derived family whose source and title equal the override text must stay a separate family.
    load_report(lib, "a-2098", [frow(report="Alpha 2098", year="2098", source="series", value="1")])
    load_report(lib, "b-2099", [frow(report="Beta 2099", year="2099", source="Example Benchmarks", value="2")])
    _set(lib, "b-2099", "Alpha")
    assert fg._family({"source": "series", "report": "Alpha 2098"}) != fg._family(
        {"source": "Example Benchmarks", "report": "Beta 2099", "series_override": "Alpha"})
    assert len(_titles(fg.find_figures(lib, "Net revenue retention"))) == 2
