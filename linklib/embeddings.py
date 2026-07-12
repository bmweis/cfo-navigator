"""Text embeddings for FP&A Buddy's semantic retrieval path (issue #93).

Mirrors the fail-fast-and-fall-back contract used elsewhere in this codebase
(see `agent._rewrite_followup`): every call here is best-effort. A missing
SDK/key, a raised exception, or an empty input all degrade to `None` (or an
empty result) rather than raising — callers (embed-on-save, query-time
retrieval) fall back to their non-semantic behavior, never blocked.

No retries here on purpose: the live request path (embed-on-save, per-query
embedding) should fail fast like the rest of the codebase's real-time calls.
The one place that needs to tolerate transient errors is the bulk backfill
(`scripts/embed_backfill.py`), which wraps calls to `embed_texts` with its
own retry/backoff — an operational concern of that one-off job, not this
module.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

DEFAULT_MODEL = os.environ.get("LINKLIB_EMBED_MODEL", "text-embedding-3-small")

# text-embedding-3-small's native output size — the vec0 virtual table is
# created with this many dimensions (see linklib/db.py).
EMBED_DIM = 1536

# Generous relative to agent.py's `deep`-tier 3,500-char grounding budget:
# embedding quality benefits from more context and the marginal per-article
# cost is negligible (well under a cent per 1,000 articles). Stays safely
# under text-embedding-3-small's 8,191-token input ceiling at ~4 chars/token.
DOCUMENT_MAX_CHARS = 8000


@dataclass
class EmbedResult:
    """Outcome of one embeddings API call (one or many texts). `vectors[i]`
    corresponds to the i-th input text. `input_tokens`/`cost_usd` are the
    call's real total usage — the caller attributes it to whichever texts it
    sent (embed-on-save vs. a retrieval query) since the API doesn't break
    usage out per-input."""
    vectors: list[list[float]] = field(default_factory=list)
    input_tokens: int = 0
    cost_usd: float = 0.0


def document_text(article: dict) -> str:
    """Build the text embedded for one library article: title, tags, then a
    summary+content excerpt, capped at DOCUMENT_MAX_CHARS.

    Deliberately mirrors `agent._ground_body`'s summary-then-content shape —
    the same material that grounds an answer is what gets embedded, so
    semantic search surfaces what the model would actually cite.
    """
    title = (article.get("title") or "").strip()
    tags = article.get("tags") or []
    summary = (article.get("summary") or "").strip()
    content = (article.get("content") or "").strip()

    header = title
    if tags:
        header += "\nTags: " + ", ".join(tags)
    body = (summary + "\n" + content) if (summary and content) else (summary or content)

    remaining = max(0, DOCUMENT_MAX_CHARS - len(header) - 1)
    text = header + ("\n" + body[:remaining] if body else "")
    return text.strip()


def content_hash(text: str) -> str:
    """SHA-256 hex digest of the exact text that was (or would be) embedded —
    stored alongside the vector so a later edit is detected as stale without
    re-embedding every unchanged row."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Reuse one client across calls, same rationale as agent._get_client (skips
# the TLS handshake on every request).
_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
    return _client


def embed_texts(texts: list[str], model: str = DEFAULT_MODEL) -> EmbedResult | None:
    """Embed a batch of texts in one API call.

    Returns None when the call never ran (SDK or key missing) or raised —
    best-effort, so every caller degrades gracefully rather than failing.
    Returns an empty EmbedResult for an empty `texts` list without touching
    the network.
    """
    import importlib.util
    if importlib.util.find_spec("openai") is None:
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    if not texts:
        return EmbedResult()

    try:
        resp = _get_client().embeddings.create(model=model, input=texts)
    except Exception:
        return None

    from .pricing import compute_embedding_cost
    tokens = getattr(resp.usage, "total_tokens", 0) or 0
    cost = compute_embedding_cost(model, tokens)
    vectors = [d.embedding for d in resp.data]
    return EmbedResult(vectors=vectors, input_tokens=tokens, cost_usd=cost)


def embed_text(text: str, model: str = DEFAULT_MODEL) -> EmbedResult | None:
    """Embed a single text (e.g. one retrieval query) — a thin wrapper over
    embed_texts for the common one-at-a-time case."""
    if not text or not text.strip():
        return None
    return embed_texts([text], model=model)
