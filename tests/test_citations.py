"""linklib.citations — the shared Citations-API document-block/citation-
extraction helper factored out of agent.py and reused by enrich.py's
grounded generation calls (2026-08 Agent taxonomy grounding fix, Phase 1).
Pure unit tests against fake SDK-shaped content blocks; no real API calls.
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.citations import extract_citations, make_document_block


def _text_block(text, citations=None):
    return types.SimpleNamespace(type="text", text=text, citations=citations)


def test_make_document_block_shape():
    block = make_document_block("My Title", "body text")
    assert block == {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "body text"},
        "title": "My Title",
        "citations": {"enabled": True},
    }


def test_make_document_block_truncates_long_title():
    block = make_document_block("x" * 500, "body")
    assert len(block["title"]) == 250


def test_extract_citations_injects_markers_and_dedupes(monkeypatch=None):
    sent_docs = [{"title": "Page A", "url": "https://a.example", "type": "page"}]
    blocks = [
        _text_block("Fact one.", citations=[{"document_index": 0}]),
        _text_block(" Fact two, same source.", citations=[{"document_index": 0}]),
        _text_block(" Uncited tail."),
    ]
    text, cited = extract_citations(blocks, sent_docs, inject_markers=True)
    assert text == "Fact one.[1] Fact two, same source.[1] Uncited tail."
    assert cited == [{"n": 1, "title": "Page A", "url": "https://a.example", "type": "page"}]


def test_extract_citations_no_markers_for_json_safety():
    """inject_markers=False (enrich.py's strict-JSON use case): the returned
    text is a plain concatenation, safe to json.loads(), even though the
    citation list is still collected."""
    sent_docs = [{"title": "Page A", "url": "https://a.example", "type": "page"}]
    raw_json = '{"summary": "Fact one.", "confident": true}'
    blocks = [_text_block(raw_json, citations=[{"document_index": 0}])]
    text, cited = extract_citations(blocks, sent_docs, inject_markers=False)
    assert text == raw_json   # no [1] spliced into the JSON string
    assert cited == [{"n": 1, "title": "Page A", "url": "https://a.example", "type": "page"}]
    import json
    assert json.loads(text)["summary"] == "Fact one."


def test_extract_citations_multiple_documents_numbered_in_first_use_order():
    sent_docs = [
        {"title": "Page A", "url": "https://a.example", "type": "page"},
        {"title": "Page B", "url": "https://b.example", "type": "page"},
    ]
    blocks = [
        _text_block("From B.", citations=[{"document_index": 1}]),
        _text_block(" From A.", citations=[{"document_index": 0}]),
    ]
    text, cited = extract_citations(blocks, sent_docs, inject_markers=True)
    assert text == "From B.[1] From A.[2]"
    assert [c["url"] for c in cited] == ["https://b.example", "https://a.example"]


def test_extract_citations_automatic_url_citation_tagged_native():
    blocks = [_text_block("Live web fact.",
                          citations=[{"url": "https://news.example/story", "title": "A story"}])]
    text, cited = extract_citations(blocks, sent_docs=[], inject_markers=True)
    assert text == "Live web fact.[1]"
    assert cited == [{"n": 1, "title": "A story", "url": "https://news.example/story",
                       "type": "web", "provider": "native"}]


def test_extract_citations_no_citations_returns_plain_text():
    blocks = [_text_block("Just an answer, nothing cited.")]
    text, cited = extract_citations(blocks, sent_docs=[])
    assert text == "Just an answer, nothing cited."
    assert cited == []


def test_extract_citations_ignores_unrecognized_citation_shape():
    blocks = [_text_block("Some text.", citations=[{"weird": "shape"}])]
    text, cited = extract_citations(blocks, sent_docs=[])
    assert text == "Some text."
    assert cited == []


def test_extract_citations_out_of_range_document_index_skipped():
    blocks = [_text_block("Some text.", citations=[{"document_index": 5}])]
    text, cited = extract_citations(blocks, sent_docs=[{"title": "Only one", "url": "u", "type": "page"}])
    assert text == "Some text."
    assert cited == []


def test_extract_citations_never_raises_on_malformed_blocks():
    """Best-effort by design: a block with no usable shape degrades to plain
    text extraction rather than raising."""
    class _Weird:
        type = "text"
        # no .text attribute at all
    text, cited = extract_citations([_Weird()], sent_docs=[])
    assert isinstance(text, str)
    assert cited == []
