"""Invented benchmark data for the Phase B tests (issue #738). The publishers
are fictional ("Example Benchmarks", "Sample Survey Co") and every figure is
made up. No third-party report data is ever committed here."""
from linklib import figures as fg
from linklib.db import Article, DIGEST_TAG

COLS = fg.COLUMNS


def frow(report="Example Benchmarks 2099", year="2099", source="Example Benchmarks",
         basis="ARR", band="$10M-$20M", segment="All", metric="Net revenue retention",
         denominator="", statistic="median", value="101", unit="percent", n="120",
         page="14", note="", data_period="FY2098"):
    return {"row_no": 0, "source": source, "report": report, "report_year": year,
            "data_period": data_period, "band_basis": basis, "band": band, "segment": segment,
            "metric": metric, "denominator": denominator, "statistic": statistic,
            "value": value, "unit": unit, "n": n, "page": page, "read_from": "table",
            "note": note, "value_num": fg.parse_value_num(value)}


def load_report(lib, slug, rows, report=None, year=None, source=None):
    for i, r in enumerate(rows, start=1):
        r["row_no"] = i
    first = rows[0]
    lib.replace_benchmark_report({
        "report_slug": slug, "source": source or first["source"], "report": report or first["report"],
        "report_year": year or first["report_year"], "row_count": len(rows), "sha256": "0" * 64}, rows)


REGISTRY = (
    "canonical_metric\tcard_url\tcontains\n"
    "Net revenue retention\thttps://cards.example.test/nrr\tContains: NRR, net dollar retention. sources: Example Benchmarks\n"
    "Burn multiple\thttps://cards.example.test/burn\tContains: burn multiple, cash burn ratio\n"
    "MRR\thttps://cards.example.test/mrr\tContains: monthly recurring revenue, net new mrr\n"
    "Net New ARR\thttps://cards.example.test/nnarr\tContains: net new arr, net new mrr\n"
    "Cash Conversion Score\thttps://cards.example.test/ccs\tContains: capital efficiency\n"
    "Capital Consumption Ratio\thttps://cards.example.test/ccr\tContains: capital efficiency, capital consumption\n"
)


def seed_registry(lib):
    rows, errors = fg.parse_registry_tsv(REGISTRY)
    assert not errors
    lib.seed_metric_registry(rows)


def seed_standard(lib):
    seed_registry(lib)
    # Report A (2099): ARR bands $10M-$20M and $30M-$50M, two statistics.
    load_report(lib, "eb-2099", [
        frow(band="$10M-$20M", value="104"),
        frow(band="$10M-$20M", statistic="p25", value="96"),
        frow(band="$30M-$50M", value="108"),
        frow(band="$20M-$30M", basis="Revenue", value="99"),
        frow(metric="Burn multiple", band="$10M-$20M", value="1.4", unit="x"),
    ])
    # Report A, previous edition (2098).
    load_report(lib, "eb-2098", [
        frow(report="Example Benchmarks 2098", year="2098", band="$10M-$20M", value="100"),
    ])
    # A different publisher that only prints revenue bands.
    load_report(lib, "ssc-2099", [
        frow(source="Sample Survey Co", report="Sample Survey 2099", basis="Revenue",
             band="$20M-$30M", value="97", metric="NRR"),
    ])


def add_digest(lib, slug, text="digest body", title="A digest", url=None):
    url = url or f"https://reports.example.test/{slug}?digest={slug}"
    return lib.upsert(Article(url=url, title=title, source="Example Benchmarks",
                              content=text, summary="s", tags=[DIGEST_TAG], enriched=1))
