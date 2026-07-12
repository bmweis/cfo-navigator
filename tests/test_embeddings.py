"""Tests for linklib/embeddings.py (#93): the document-text builder, content
hashing, and embed_texts/embed_text's best-effort contract around a faked
OpenAI client (no real network calls).
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import embeddings as embed_mod
from linklib import pricing


# --- document_text -----------------------------------------------------------

def test_document_text_combines_title_tags_summary_content():
    article = {"title": "SaaS NRR benchmarks", "tags": ["saas", "nrr"],
               "summary": "A summary.", "content": "Full body text."}
    text = embed_mod.document_text(article)
    assert text.startswith("SaaS NRR benchmarks")
    assert "Tags: saas, nrr" in text
    assert "A summary." in text
    assert "Full body text." in text


def test_document_text_respects_max_chars_cap():
    article = {"title": "T", "tags": [], "summary": "S" * 100, "content": "C" * 20000}
    text = embed_mod.document_text(article)
    assert len(text) <= embed_mod.DOCUMENT_MAX_CHARS


def test_document_text_summary_only_when_no_content():
    article = {"title": "T", "tags": [], "summary": "just a summary", "content": ""}
    assert "just a summary" in embed_mod.document_text(article)


def test_document_text_empty_article_yields_empty_string():
    assert embed_mod.document_text({"title": "", "tags": [], "summary": "", "content": ""}) == ""


# --- content_hash --------------------------------------------------------------

def test_content_hash_deterministic():
    assert embed_mod.content_hash("hello") == embed_mod.content_hash("hello")


def test_content_hash_changes_with_text():
    assert embed_mod.content_hash("hello") != embed_mod.content_hash("goodbye")


# --- embed_texts / embed_text: best-effort contract ---------------------------

class _FakeUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class _FakeItem:
    def __init__(self, embedding):
        self.embedding = embedding


class _FakeResp:
    def __init__(self, vectors, total_tokens):
        self.data = [_FakeItem(v) for v in vectors]
        self.usage = _FakeUsage(total_tokens)


class _FakeEmbeddingsAPI:
    def __init__(self, vectors, total_tokens, raise_exc=None):
        self._vectors = vectors
        self._tokens = total_tokens
        self._raise = raise_exc

    def create(self, model, input):
        if self._raise:
            raise self._raise
        return _FakeResp(self._vectors, self._tokens)


class _FakeClient:
    def __init__(self, vectors, total_tokens, raise_exc=None):
        self.embeddings = _FakeEmbeddingsAPI(vectors, total_tokens, raise_exc)


def test_embed_texts_returns_vectors_and_real_cost(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    fake = _FakeClient(vectors=[[0.1, 0.2], [0.3, 0.4]], total_tokens=1000)
    monkeypatch.setattr(embed_mod, "_get_client", lambda: fake)

    result = embed_mod.embed_texts(["a", "b"], model="text-embedding-3-small")
    assert result is not None
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.input_tokens == 1000
    assert result.cost_usd == pytest.approx(
        pricing.compute_embedding_cost("text-embedding-3-small", 1000))


def test_embed_texts_no_key_is_a_noop(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert embed_mod.embed_texts(["a"]) is None


def test_embed_texts_empty_input_skips_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    def boom():
        raise AssertionError("must not touch the network for empty input")
    monkeypatch.setattr(embed_mod, "_get_client", boom)

    result = embed_mod.embed_texts([])
    assert result is not None
    assert result.vectors == []


def test_embed_texts_api_failure_returns_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    fake = _FakeClient(vectors=[], total_tokens=0, raise_exc=RuntimeError("rate limited"))
    monkeypatch.setattr(embed_mod, "_get_client", lambda: fake)

    assert embed_mod.embed_texts(["a"]) is None


def test_embed_text_single_wrapper(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    fake = _FakeClient(vectors=[[0.5, 0.6]], total_tokens=42)
    monkeypatch.setattr(embed_mod, "_get_client", lambda: fake)

    result = embed_mod.embed_text("a question")
    assert result is not None
    assert result.vectors == [[0.5, 0.6]]


def test_embed_text_blank_input_is_a_noop(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    assert embed_mod.embed_text("   ") is None


# --- pricing -------------------------------------------------------------------

def test_compute_embedding_cost_known_model():
    cost = pricing.compute_embedding_cost("text-embedding-3-small", 1_000_000)
    assert cost == pytest.approx(0.02)


def test_compute_embedding_cost_unknown_model_falls_back_not_zero():
    cost = pricing.compute_embedding_cost("some-future-embedding-model", 1_000_000)
    assert cost > 0
