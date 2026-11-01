"""Invented fixture shaped like ask_questions id 35 (org structure and spend profile at $20-$30M ARR).
Publishers are fictional; every figure is made up."""
import json
from types import SimpleNamespace

from figures_fixtures import frow, load_report

from linklib import figures as fg
from linklib.db import Article, Library

REG = (
    "canonical_metric\tcard_url\tcontains\n"
    "Total Employees\thttps://cards.example.test/h\tContains: total employees, fte count\n"
    "Gross Margin\thttps://cards.example.test/gm\tContains: gross margin, gross profit margin\n"
)
SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}
QUESTION = ("I run a $20-$30M ARR company selling to enterprise customers with a sales-led motion. "
            "What does the org structure and spend profile look like for companies like mine?")

# The wording that did reach the lookup in id 35 (it named headcount and expenses by function).
QUESTION_35 = ("I run a $20-$30M ARR company selling to enterprise customers with a sales-led motion. What is the total "
               "headcount, and what are operating expenses by function, and what does the organizational structure "
               "and spend profile look like for companies like mine?")

BAND = "$20M-$30M"


def _eb(yr, metric, value, unit="count", denominator="", **kw):
    return frow(source="Example Benchmarks", report=f"Example Benchmarks {yr}", year=str(yr), data_period=f"CY-{yr-1}",
                basis="ARR", band=BAND, metric=metric, value=value, unit=unit, denominator=denominator, **kw)


def _ss(yr, metric, value, unit="percent", denominator="", **kw):
    return frow(source="Sample Survey Co", report=f"Sample Survey Co {yr}", year=str(yr), data_period=f"CY-{yr-1}",
                basis="ARR", band=BAND, metric=metric, value=value, unit=unit, denominator=denominator, **kw)


def load(db, cogs_printed=True, gm_printed=True):
    rows_eb = []
    for yr in (2023, 2024, 2025):
        rows_eb += [
            _eb(yr, "Headcount: Total", "150"),
            _eb(yr, "Headcount: Engineering", "45"), _eb(yr, "Headcount: Sales", "30"),
            _eb(yr, "Headcount: G&A", "15"), _eb(yr, "Headcount: Customer Success", "20"),
            _eb(yr, "S&M % of Revenue", "38", denominator="percent of revenue"),
            _eb(yr, "R&D % of Revenue", "30", denominator="percent of revenue"),
            _eb(yr, "G&A % of Revenue", "14", denominator="percent of revenue"),
        ]
        if gm_printed:
            rows_eb.append(_eb(yr, "Gross Margin %", "74", unit="percent"))
    for yr in (2023, 2024, 2025):
        load_report(db, f"eb-{yr}", [r for r in rows_eb if r["report_year"] == str(yr)])
    rows_ss = []
    for yr in (2024, 2025):
        rows_ss += [_ss(yr, "Total employees", "140", unit="count")]
        rows_ss += [_ss(yr, "S&M % of ARR", "30", denominator="percent of ARR"),
                    _ss(yr, "R&D % of ARR", "25", denominator="percent of ARR")]
        if cogs_printed:
            rows_ss += [_ss(yr, "Cost of revenue % of revenue", "26", denominator="percent of revenue"),
                        _ss(yr, "Subscription COGS % of revenue", "17", denominator="percent of revenue"),
                        _ss(yr, "Hosting % of revenue", "6", denominator="percent of revenue")]
    # Revenue-basis rows far outnumber ARR-basis rows for opex: add many revenue rows in other bands/stats
    for yr in (2024, 2025):
        for stat in ("p25", "median", "p75", "mean"):
            rows_ss.append(_ss(yr, "S&M % of Revenue", "37", denominator="percent of revenue", statistic=stat))
    for yr in (2024, 2025):
        load_report(db, f"ss-{yr}", [r for r in rows_ss if r["report_year"] == str(yr)])


def make_lib(path, **kw):
    db = Library(str(path))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    load(db, **kw)
    return db


TOPICS = ("total employees headcount fte", "operating expenses by function", "cost of revenue gross margin",
          "organizational structure spend profile")


def add_articles(db, n=60, chars=6000):
    """n invented articles split across the four topics, so each part of the split finds its own."""
    for i in range(n):
        t = TOPICS[i % 4]
        db.upsert(Article(url=f"https://articles.example.test/{i}", title=f"{t.title()} digest {i}",
                          summary=f"{t} at $20-$30M ARR. " * 3,
                          content=((t + " ") * 400)[:chars] + f" article {i}",
                          tags=["benchmark-digest"] if i % 3 == 0 else []))


def _part(intent, metric, text, band="$20-$30M ARR", segment="Enterprise, sales-led", year="all"):
    return {"intent": intent, "metric": metric, "band": band, "segment": segment, "year": year, "text": text}


# The id 35 shape: parts 1 and 3 the same headcount lookup under two names, part 2 opex, part 4 the rest.
SPLIT_35 = {"subqueries": [
    _part("benchmark", "total employees", "Total employees at $20-$30M ARR"),
    _part("benchmark", "Operating expenses by function", "Operating expenses by function at $20-$30M ARR"),
    _part("benchmark", "Headcount: Total", "Headcount: Total at $20-$30M ARR, how has it changed"),
    _part("other", "", "organizational structure and spend profile", band="", segment="", year=""),
], "rest_of_question": "organizational structure and spend profile"}


def fake_client(payload):
    def factory():
        def create(**kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                                   usage=SimpleNamespace(input_tokens=300, output_tokens=200))
        return SimpleNamespace(messages=SimpleNamespace(create=create))
    return factory
