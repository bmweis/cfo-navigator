"""parse_band reads "MM", "million" and "Greater than" labels (invented figures only; the
label list is the set of distinct band labels stored in production, labels only)."""
import json
import os

import pytest

from linklib import figures as fg
from linklib.db import Library
from tests import figures_fixtures as fx

INF = float("inf")
M = 1e6


@pytest.mark.parametrize("text,lo,hi,kind", [
    ("$5MM", 5 * M, 5 * M, "money"), ("$10MM", 10 * M, 10 * M, "money"),
    ("$0-10MM", 0, 10 * M, "money"), ("$5MM-$10MM", 5 * M, 10 * M, "money"),
    ("$1 - $3 Million", 1 * M, 3 * M, "money"),
    ("Greater than $40MM", 40 * M, INF, "money"), ("Greater than $50M", 50 * M, INF, "money"),
    ("Greater than 90%", 90, INF, "pct"),
    ("Over $50M", 50 * M, INF, "money"), ("Less than $1M", 0, 1 * M, "money"),
    ("<$1M", 0, 1 * M, "money"), ("<$1m", 0, 1 * M, "money"), ("Under $1M", 0, 1 * M, "money"),
    ("$25M-$50M", 25 * M, 50 * M, "money"), ("$25-50M", 25 * M, 50 * M, "money"),
    ("$50M+", 50 * M, INF, "money"), (">$100M", 100 * M, INF, "money"),
])
def test_parse_band_forms(text, lo, hi, kind):
    assert fg.parse_band(text) == {"lo": lo, "hi": hi, "kind": kind}


@pytest.mark.parametrize("text", ["All", "BOX", "DOCU", "AYX"])
def test_non_size_labels_stay_unplaced(text):
    assert fg.parse_band(text) is None


def test_multiple_label_is_not_mapped_to_dollars():
    p = fg.parse_band("2-4X")
    assert p is None or p["kind"] != "money"


def _golden():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "band_labels.json")
    d = json.load(open(path))
    dec = lambda v: None if v is None else {"lo": v["lo"], "hi": INF if v["hi"] == "inf" else v["hi"], "kind": v["kind"]}
    return ({k: dec(v) for k, v in d["unchanged"].items()}, {k: dec(v) for k, v in d["changed"].items()})


def test_every_label_whose_parse_was_already_right_is_unchanged():
    """Passes before and after the fix: the expected values are the parses from before it."""
    unchanged, _ = _golden()
    assert len(unchanged) > 1500
    bad = {k: fg.parse_band(k) for k, v in unchanged.items() if fg.parse_band(k) != v}
    assert not bad, bad


def test_every_label_the_fix_changes_parses_as_listed():
    _, changed = _golden()
    assert len(changed) == 122
    bad = {k: fg.parse_band(k) for k, v in changed.items() if fg.parse_band(k) != v}
    assert not bad, bad


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    yield db
    db.close()


def test_find_figures_places_mm_and_greater_than_bands(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "mm-2099", [
        fx.frow(band="$5MM-$10MM", value="101"),
        fx.frow(band="Greater than $50M", value="111", report="Example Benchmarks 2099"),
    ])
    r7 = {x["band"]: x["match"] for x in fg.find_figures(lib, "Net revenue retention", band="$7M", basis="ARR")["rows"]}
    assert r7.get("$5MM-$10MM") == "exact"
    r60 = {x["band"]: x["match"] for x in fg.find_figures(lib, "Net revenue retention", band="$60M", basis="ARR")["rows"]}
    assert r60.get("Greater than $50M") == "exact"
