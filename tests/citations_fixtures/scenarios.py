"""Shared fixture inputs for the Phase 1a extraction-refactor parity check.

Both `scripts/capture_citations_golden_fixtures.py` (run once, against
pre-refactor `linklib/agent.py`, to produce the golden reference committed
at `tests/citations_fixtures/golden_output.json`) and
`tests/test_citations_refactor_parity.py` (run every test session, against
whatever `linklib/agent.py`/`linklib/citations.py` currently contains)
import these exact same scenario builders — so both sides of the parity
check are guaranteed to run against byte-identical inputs. This module
itself is never "refactored": it only builds inputs and hands them to
whatever `_build_source_documents`/`_assemble_cited_answer` (or their
post-refactor equivalents) the caller passes in.

Two scenario sets:
- BUILD_SOURCE_DOCUMENTS_SCENARIOS: kwargs for the doc-block-building
  function (library/feed/web hits, budget-cutoff cases).
- ASSEMBLE_CITED_ANSWER_SCENARIOS: (content_blocks, sent_docs) pairs for
  the citation-extraction function, covering document-index citations,
  automatic URL (native web_search) citations, dedup/first-use ordering,
  an uncited answer, an unrecognized citation shape, and a genuinely
  malformed block that trips the function's own except-and-degrade path.
"""
from __future__ import annotations

import types


def _block(text: str, citations=None, block_type: str = "text"):
    """One SDK-shaped response content block."""
    return types.SimpleNamespace(type=block_type, text=text, citations=citations)


def _doc_citation(document_index: int):
    return types.SimpleNamespace(document_index=document_index, url=None, title=None)


def _url_citation(url: str, title: str | None = None):
    return types.SimpleNamespace(document_index=None, url=url, title=title)


# --- _build_source_documents scenarios --------------------------------------

BUILD_SOURCE_DOCUMENTS_SCENARIOS = [
    {
        "name": "empty_everything",
        "kwargs": {"lib_hits": [], "feed_items": [], "exa_hits": []},
    },
    {
        "name": "library_only_summary_and_content",
        "kwargs": {
            "lib_hits": [
                {"id": 101, "title": "GRR vs NRR Benchmarks", "url": "https://library.example/grr-nrr",
                 "summary": "A short distilled summary of the retention piece.",
                 "content": "The full archived article body goes on at much greater length here, "
                            "covering churn cohorts, expansion revenue, and net dollar retention math."},
                {"id": 102, "title": "Burn Multiple 101", "url": "https://library.example/burn-multiple",
                 "summary": "", "content": "Only a body, no summary was ever generated for this one."},
            ],
            "feed_items": [], "exa_hits": [],
        },
    },
    {
        "name": "feed_only",
        "kwargs": {
            "lib_hits": [],
            "feed_items": [
                {"title": "Board Reporting Cadence", "url": "https://feed.example/board-cadence",
                 "summary": "Recent feed item about how often to report to the board."},
            ],
            "exa_hits": [],
        },
    },
    {
        "name": "exa_web_only",
        "kwargs": {
            "lib_hits": [], "feed_items": [],
            "exa_hits": [
                {"title": "SaaS Pricing Trends 2026", "url": "https://web.example/pricing-trends",
                 "summary": "A fresh web result about usage-based pricing adoption."},
            ],
        },
    },
    {
        "name": "mixed_all_three_sources",
        "kwargs": {
            "lib_hits": [
                {"id": 201, "title": "CAC Payback Benchmarks", "url": "https://library.example/cac-payback",
                 "summary": "Distilled CAC payback summary.", "content": "Full CAC payback article body."},
            ],
            "feed_items": [
                {"title": "Rule of 40 Revisited", "url": "https://feed.example/rule-of-40",
                 "summary": "Feed item revisiting the Rule of 40."},
            ],
            "exa_hits": [
                {"title": "Headcount Planning at Series B", "url": "https://web.example/headcount-series-b",
                 "summary": "Fresh web result on headcount planning."},
            ],
        },
    },
    {
        "name": "global_budget_cuts_off_later_sources",
        "kwargs": {
            "lib_hits": [
                {"id": 301, "title": "Long Source A", "url": "https://library.example/long-a",
                 "summary": "x" * 40, "content": "y" * 40},
                {"id": 302, "title": "Long Source B", "url": "https://library.example/long-b",
                 "summary": "x" * 40, "content": "y" * 40},
            ],
            "feed_items": [
                {"title": "Feed Source C", "url": "https://feed.example/c", "summary": "z" * 40},
            ],
            "exa_hits": [
                {"title": "Web Source D", "url": "https://web.example/d", "summary": "w" * 40},
            ],
            "source_chars": 30, "global_chars": 50,
        },
    },
    {
        "name": "source_with_empty_body_is_skipped",
        "kwargs": {
            "lib_hits": [
                {"id": 401, "title": "No Content At All", "url": "https://library.example/empty",
                 "summary": "", "content": ""},
                {"id": 402, "title": "Has Content", "url": "https://library.example/has-content",
                 "summary": "Real summary here.", "content": "Real body here."},
            ],
            "feed_items": [], "exa_hits": [],
        },
    },
]


# --- _assemble_cited_answer scenarios ----------------------------------------

def _sent_docs_library_and_feed():
    return [
        {"title": "GRR vs NRR Benchmarks", "url": "https://library.example/grr-nrr", "type": "library", "article_id": 101},
        {"title": "Board Reporting Cadence", "url": "https://feed.example/board-cadence", "type": "feed"},
    ]


def _sent_docs_with_exa():
    return [
        {"title": "GRR vs NRR Benchmarks", "url": "https://library.example/grr-nrr", "type": "library", "article_id": 101},
        {"title": "SaaS Pricing Trends 2026", "url": "https://web.example/pricing-trends", "type": "web", "provider": "exa"},
    ]


ASSEMBLE_CITED_ANSWER_SCENARIOS = [
    {
        "name": "no_citations_plain_text",
        "content_blocks": [_block("Just a plain answer with nothing cited.")],
        "sent_docs": [],
    },
    {
        "name": "single_document_citation",
        "content_blocks": [
            _block("Median GRR runs 90-95% for enterprise SaaS.", citations=[_doc_citation(0)]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "same_document_cited_across_multiple_spans_dedupes",
        "content_blocks": [
            _block("First claim from the library source.", citations=[_doc_citation(0)]),
            _block(" Second claim, same source again.", citations=[_doc_citation(0)]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "two_documents_first_use_order",
        "content_blocks": [
            _block("Claim grounded in the feed item.", citations=[_doc_citation(1)]),
            _block(" Claim grounded in the library item.", citations=[_doc_citation(0)]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "exa_web_document_citation_tagged_exa",
        "content_blocks": [
            _block("Pricing trend claim from a fresh web result.", citations=[_doc_citation(1)]),
        ],
        "sent_docs": _sent_docs_with_exa(),
    },
    {
        "name": "native_web_search_automatic_url_citation",
        "content_blocks": [
            _block("A live web fact with no document block behind it.",
                   citations=[_url_citation("https://news.example/story", "A Story")]),
        ],
        "sent_docs": [],
    },
    {
        "name": "mixed_document_and_native_citations",
        "content_blocks": [
            _block("Library-grounded claim.", citations=[_doc_citation(0)]),
            _block(" Live-web-grounded claim.", citations=[_url_citation("https://news.example/live", "Live Story")]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "out_of_range_document_index_skipped",
        "content_blocks": [
            _block("Claim citing a document index that doesn't exist.", citations=[_doc_citation(9)]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "unrecognized_citation_shape_skipped",
        "content_blocks": [
            _block("Claim with a citation carrying neither a document_index nor a url.",
                   citations=[types.SimpleNamespace(document_index=None, url=None, title=None)]),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "non_text_block_ignored",
        "content_blocks": [
            _block("Some text.", citations=[_doc_citation(0)]),
            types.SimpleNamespace(type="thinking", thinking="internal reasoning, not a text block"),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
    {
        "name": "malformed_citations_field_degrades_to_plain_text",
        # A citations field that's truthy but not iterable (a real SDK
        # response would never do this — this exercises the function's own
        # defensive except-and-degrade path deliberately).
        "content_blocks": [
            types.SimpleNamespace(type="text", text="Text whose citations field is malformed.", citations=42),
        ],
        "sent_docs": _sent_docs_library_and_feed(),
    },
]
