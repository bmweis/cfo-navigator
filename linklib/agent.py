"""Ask FP&A / finance questions against your own library, RSS feed, and the web.

Retrieval-augmented: pulls the most relevant saved articles (and optionally
current feed items), hands them to Claude as grounded sources, and returns an
answer with numbered citations. Web search is restricted to the user's trusted
domains from preferred_sites.opml.

Source types, model, and effort level are all configurable at call time.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .db import Library

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", "claude-sonnet-4-6")

# Friendly alias → canonical model ID
MODEL_ALIASES: dict[str, str] = {
    "haiku":   "claude-haiku-4-5-20251001",
    "sonnet":  "claude-sonnet-4-6",
    "sonnet5": "claude-sonnet-5",
    "opus":    "claude-opus-4-8",
}

# Controls how many sources are pulled, how long synthesis runs, and which
# model answers. The UI surfaces only this Quick/Standard/Deep tier — the
# underlying model is an internal implementation detail, not a user choice.
# web_max_uses caps the number of web_search tool calls Claude may make.
EFFORT_SETTINGS: dict[str, dict] = {
    # source_chars: per-source grounding budget (summary + archived-text excerpt).
    # global_chars: hard cap on TOTAL grounding text per answer, so a query that
    #   retrieves many long articles can't balloon the prompt (cost guard).
    "quick":    {"model": "claude-haiku-4-5-20251001", "max_library": 4,  "max_feed": 3,  "web_max_uses": 2, "max_tokens": 700,  "source_chars": 900,  "global_chars": 6000},
    "standard": {"model": "claude-sonnet-4-6",         "max_library": 8,  "max_feed": 5,  "web_max_uses": 4, "max_tokens": 1500, "source_chars": 1800, "global_chars": 16000},
    "deep":     {"model": "claude-opus-4-8",           "max_library": 16, "max_feed": 8,  "web_max_uses": 6, "max_tokens": 2500, "source_chars": 3500, "global_chars": 40000},
}

# Conversation cost guards — invisible and server-enforced, so a monetized user
# can't run up the API bill with an endless back-and-forth. Tune here.
MAX_FOLLOWUPS = 6          # extra turns allowed after the first question (7 total)
MAX_HISTORY_CHARS = 8000   # cap on prior-turn text carried back into the prompt


def count_prior_questions(history) -> int:
    """User turns already in the conversation — drives the follow-up cap."""
    return sum(1 for m in (history or [])
               if isinstance(m, dict) and m.get("role") == "user")

# Rough per-query cost estimates (USD) keyed by canonical model ID then effort.
# Based on typical token counts at each effort level; actual billing may differ.
COST_ESTIMATES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"quick": 0.004,  "standard": 0.008,  "deep": 0.013},
    "claude-sonnet-4-6":         {"quick": 0.014,  "standard": 0.028,  "deep": 0.048},
    "claude-sonnet-5":           {"quick": 0.014,  "standard": 0.028,  "deep": 0.048},
    "claude-opus-4-8":           {"quick": 0.069,  "standard": 0.141,  "deep": 0.240},
}

_STOP = {
    "the", "a", "an", "and", "or", "but", "for", "with", "how", "what", "why",
    "when", "should", "would", "could", "about", "into", "from", "that", "this",
    "are", "is", "do", "does", "my", "me", "i", "to", "of", "in", "on", "it",
    "you", "your", "can", "be", "as", "at", "we", "our", "vs",
}


# DB-backed voice, editable live from /admin/voice (settings keys "voice_core"
# and "voice_fpa_buddy") — these are only the fallback used when a field is
# empty. voice_core is written persona-neutrally (mechanics + tone only) so it
# doubles as the "General / site copy" reviewer rubric; voice_fpa_buddy layers
# the analyst-specific register on top for FP&A Buddy generation and its own
# reviewer rubric. See issue #95.
VOICE_CORE_DEFAULT = """Lead with the point, support it with ONE concrete detail, and stop. Direct, low-ceremony, confident — it earns trust by being specific and grounded, not by sounding authoritative.

VOICE:
- Specific over abstract: numbers, names, the actual mechanism — never stacked adjectives.
- State a view plainly when it's supported. When it's not, say so and name the gap — don't guess, and don't pad the gap with generic hedging ("it's worth noting that", "there are many factors to consider").
- Confident, not boastful. No gratitude theater, no apologizing.
- An emdash marks a short pivot or label, not a place to bolt on a longer explanation: if what follows could stand as its own sentence, or carries its own parenthetical, split it into two sentences instead.

HARD MECHANICAL RULES (never violate):
- Emdashes have NO surrounding spaces, and are used sparingly—one well-placed, never peppered.
- Sentence case for any heading/title; proper nouns and acronyms stay capped (Mux, NetSuite, FP&A, AI, Ramp).
- Spell out "and"; never "&" except in terms like FP&A.
- No performative openers or closers ("I'm excited to share", "thrilled to", "Onward!", "Excited for what's next").
- No filler ("at the end of the day", "it's worth noting that", "needless to say", "in order to" → "to").
- Avoid: genuinely, honestly, actually (as filler), leverage (as a verb), delve, robust, seamless, synergy, transformative, game-changer."""

VOICE_FPA_BUDDY_DEFAULT = """You are FP&A Buddy: a trusted senior FP&A / strategic-finance analyst answering a colleague's question, in third person / neutral register — not narrating personal experience.

- Every confident claim traces to a cited source. Never invent personal experience or borrow authority beyond what's cited — you have an archive and the web, not a career.
- Never claim first-person experience ("I've done this myself", "when I ran finance at...") — you have no career history to invoke.
- When sources don't cover the question well, name the gap plainly rather than hedge around it with generic filler.
- No personal-interest metaphors (sports, music, skateboarding, etc.) — those are Brian's own references, not this assistant's.
- No LinkedIn-shape devices — no hook lines, no emoji, no single closing aphorism. This is a direct answer, not a post."""


def _build_system(use_library: bool, use_feed: bool, use_web: bool, lib: Library) -> str:
    """Build the advisor system prompt, describing only the active source types.

    Voice: appends the DB-backed voice_core + voice_fpa_buddy settings (each
    falling back to its code-constant default when empty) so answers sound
    like a calibrated analyst persona, not a generic assistant.
    """
    voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT
    voice_fpa_buddy = lib.get_setting("voice_fpa_buddy") or VOICE_FPA_BUDDY_DEFAULT
    voice = f"{voice_core}\n\n{voice_fpa_buddy}"

    sources = []
    if use_library:
        sources.append("SAVED LIBRARY: the user's hand-curated archive of qualified "
                       "sources — highest authority. Lead with it.")
    if use_feed:
        sources.append("RSS FEED: recent items from the user's subscribed publications.")
    if use_web:
        sources.append("WEB SEARCH (restricted to the user's trusted domains): live "
                       "results, to supplement the library — not replace it.")
    source_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sources))

    # Citations happen at the API level now (documents are sent with
    # citations enabled, and web search cites automatically) — the prompt no
    # longer asks for [n] markers or a "Worth reading:" line; markers are
    # injected server-side from the verified citation metadata.
    cite_parts = []
    if use_library or use_feed:
        cite_parts.append("The saved articles and feed items are provided as "
                          "documents — cite them for every claim you draw from them.")
    if use_web:
        cite_parts.append("Don't paste raw URLs into the answer text; web "
                          "results are cited automatically.")
    cite_note = " ".join(cite_parts)

    return (
        "You are a strategic-finance advisor for finance leaders at high-growth "
        "technology companies — the person running FP&A or strategic finance at a "
        "startup or scaleup, or growing into that seat. You are fluent in SaaS and "
        "startup finance: ARR and usage-based revenue, NRR/GRR, CAC payback and "
        "sales efficiency, burn multiple and runway, gross margin and COGS, the "
        "Rule of 40, and headcount and GTM-investment benchmarks.\n\n"
        "Answer like a trusted advisor: concrete, practical, and grounded — "
        "accessible to a sharp operator who isn't necessarily a finance specialist.\n\n"
        f"Sources, in priority order:\n{source_list}\n\n"
        "Rules:\n"
        f"- {cite_note}\n"
        "- NEVER reproduce source text verbatim. Synthesize and paraphrase in your "
        "own words, then cite and link; quote at most a short phrase.\n"
        "- Ground every claim in the provided sources. If they don't cover the "
        "question well, say so plainly and note what's missing — don't guess.\n"
        "- If the question is missing key inputs (company stage, business model, the "
        "relevant numbers), ask one focused clarifying question alongside your "
        "best-effort answer.\n\n"
        "Voice — write every answer this way:\n"
        f"{voice}"
    )


def _safe_fts_query(question: str) -> str:
    """Turn a natural question into a safe FTS5 query (no operator surprises)."""
    tokens = [w for w in re.findall(r"[a-z0-9]+", question.lower())
              if len(w) > 2 and w not in _STOP]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _question_tokens(question: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", question.lower())
            if len(w) > 2 and w not in _STOP}


# --- Follow-up query rewrite -------------------------------------------------
# On follow-up turns, a raw question like "what about for a Series A stage
# company?" carries none of the conversation's subject, so FTS5/feed retrieval
# grounds the answer in the wrong articles. One small, cheap Haiku call turns
# the follow-up into a standalone search question first. Retrieval-only: the
# answering prompt always carries the user's verbatim question (it already has
# the raw history for conversational context). Best-effort by design — any
# failure falls back silently to retrieving on the raw question, so the
# rewrite can never block or fail an answer.
REWRITE_MODEL = MODEL_ALIASES["haiku"]
REWRITE_MAX_TOKENS = 120
REWRITE_TIMEOUT_SECONDS = 10.0   # a slow rewrite isn't worth stalling the answer for
REWRITE_MAX_CHARS = 300          # longer output = the model rambled; treat as malformed

REWRITE_SYSTEM = (
    "You rewrite the latest follow-up question from a conversation into ONE "
    "standalone, self-contained search question. Resolve pronouns, ellipsis, "
    "and implicit references using the conversation — e.g. after a discussion "
    "of SaaS pricing benchmarks, \"what about for a Series A stage company?\" "
    "becomes \"SaaS pricing benchmarks for Series A stage companies\". Keep "
    "every concrete term that matters for search. Output ONLY the rewritten "
    "question — no preamble, no quotes, no explanation, under 40 words."
)


@dataclass
class RewriteResult:
    """Outcome of one follow-up rewrite call. `text` is "" when the call ran
    but produced nothing usable — the token usage is still real spend and gets
    recorded either way; only retrieval falls back to the raw question."""
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _clean_rewrite_output(raw: str) -> str:
    """Reduce the rewrite model's output to one usable search question, or ""
    if it's malformed: first non-empty line only, wrapping quotes stripped,
    rejected outright when empty or suspiciously long."""
    line = next((ln.strip() for ln in (raw or "").splitlines() if ln.strip()), "")
    line = line.strip('"“”').strip("'").strip()
    if not line or len(line) > REWRITE_MAX_CHARS:
        return ""
    return line


def _rewrite_followup(trimmed_history: list[dict], question: str) -> RewriteResult | None:
    """Rewrite a follow-up into a standalone search question via one Haiku call.

    `trimmed_history` must already be bounded by _trim_history (the same cap
    the answering prompt uses). Returns None when the call never ran — SDK or
    key missing, or the API call raised/timed out — i.e. nothing was spent.
    Returns a RewriteResult (possibly with text="") when the call completed,
    so the caller can record the spend even if the output was unusable.
    """
    import importlib.util
    if importlib.util.find_spec("anthropic") is None:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    transcript = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in trimmed_history
    )
    try:
        resp = _get_client().messages.create(
            model=REWRITE_MODEL,
            max_tokens=REWRITE_MAX_TOKENS,
            system=REWRITE_SYSTEM,
            messages=[{
                "role": "user",
                "content": (f"Conversation so far:\n{transcript}\n\n"
                            f"Follow-up question: {question}"),
            }],
            timeout=REWRITE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None

    from .pricing import compute_cost
    usage = resp.usage
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = compute_cost(REWRITE_MODEL, in_tok, out_tok, cache_w, cache_r)

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    return RewriteResult(text=_clean_rewrite_output(text),
                         input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)       # saved-library hits
    feed_sources: list[dict] = field(default_factory=list)  # RSS feed hits
    web_sources: list[dict] = field(default_factory=list)   # fresh web results
    # API-verified citations: [{n, title, url, type, article_id?}] for the
    # sources the answer ACTUALLY cited (type: library|feed|web; article_id =
    # articles.id, present on library entries only), numbered to match the
    # [n] markers injected into `text`. Empty when the model cited nothing or
    # citation metadata was unusable — never blocks an answer.
    citations: list[dict] = field(default_factory=list)
    # The resolved canonical model ID actually used (after MODEL_ALIASES /
    # DEFAULT_MODEL resolution) — callers that log/record this answer should
    # use this, not the raw `model` argument they passed in, which may have
    # been an alias or empty (e.g. a caller that never sends a model field).
    model: str = ""
    # Real usage from the API response (0 when the call never ran, e.g. no key).
    # cost_usd is the authoritative per-TURN dollar figure — the answer call
    # plus the follow-up rewrite call (when one ran) — as opposed to the
    # pre-call COST_ESTIMATES. The token fields below cover the answer call
    # only; the rewrite call's share is broken out in the rewrite_* fields.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    # Follow-up query-rewrite call (zero on turn one, or when it never ran).
    # rewrite_cost_usd is already included in cost_usd above.
    rewrite_input_tokens: int = 0
    rewrite_output_tokens: int = 0
    rewrite_cost_usd: float = 0.0
    # Query-time embedding call for hybrid library retrieval (#93) — zero
    # when vector search is unavailable or the embedding call failed (both
    # fall back to FTS5-only silently). This is a USER-CAP cost (embedding
    # the question), unlike article_embeddings.cost_usd, which is embed-ON-
    # SAVE overhead and never appears here. embed_cost_usd is already
    # included in cost_usd above.
    embed_input_tokens: int = 0
    embed_cost_usd: float = 0.0


# Reuse one client across requests so its httpx connection pool stays warm —
# sequential questions skip the TLS handshake to the API. The SDK client is
# thread-safe, so sharing it across FastAPI's request threads is fine.
_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


# Reciprocal Rank Fusion constant (standard default from the TREC literature).
# Chosen over blending bm25 scores with cosine distances: the two live on
# incomparable scales with no corpus-scale signal on a ~1,500-article library
# to calibrate a blend weight against, whereas RRF only uses rank position —
# scale-free and deterministic. See #93 for the fuller writeup.
RRF_K = 60


def _rrf_merge(ranked_lists: list[list[dict]], limit: int, k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion: merge multiple best-first result lists into
    one by summing 1/(k + rank) per item across lists, then sorting
    descending by that summed score. An item near the top of either list
    (or both) outranks one that's merely present.

    Items are deduped by `id`. When an item appears in more than one list,
    the copy from whichever list it was first seen in wins (search() and
    vector_search() both return the same _row_to_dict shape, so this is
    just picking one representation, not a data quality concern).
    """
    scores: dict[int, float] = {}
    items: dict[int, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            item_id = item.get("id")
            if item_id is None:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            items.setdefault(item_id, item)
    ordered = sorted(scores, key=lambda i: -scores[i])
    return [items[i] for i in ordered[:limit]]


def retrieve(lib: Library, question: str, max_sources: int = 8
            ) -> tuple[list[dict], int, float]:
    """Hybrid library retrieval: FTS5 keyword search plus vector semantic
    search (sqlite-vec), merged by reciprocal rank fusion. Each path fetches
    2x max_sources candidates so the merge has room to actually blend rank
    signal, rather than RRF-ing two already-truncated top-N lists.

    Returns (hits, embed_input_tokens, embed_cost_usd). The token/cost
    figures are 0 unless a query-embedding call actually ran — vector search
    being unavailable, or the embedding call failing, both degrade silently
    to FTS5-only, the same best-effort contract as the follow-up rewrite.
    """
    fts_hits = lib.search(_safe_fts_query(question), limit=max_sources * 2)

    if not lib.vector_search_available():
        return fts_hits[:max_sources], 0, 0.0

    from .embeddings import embed_text
    embedded = embed_text(question)
    if embedded is None or not embedded.vectors:
        return fts_hits[:max_sources], 0, 0.0

    vec_hits = lib.vector_search(embedded.vectors[0], limit=max_sources * 2)
    merged = _rrf_merge([fts_hits, vec_hits], limit=max_sources)
    return merged, embedded.input_tokens, embedded.cost_usd


def retrieve_feed(question: str, opml_path: str, max_items: int = 5) -> list[dict]:
    """Return feed items most relevant to the question via keyword overlap.

    Relies on feed.get_feed_items which is already 30-min cached, so this adds
    negligible latency.
    """
    try:
        from .feed import get_feed_items
        items, _ = get_feed_items(opml_path, max_total=300)
    except Exception:
        return []

    tokens = _question_tokens(question)
    if not tokens:
        return items[:max_items]

    def score(item: dict) -> int:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        return sum(1 for t in tokens if t in text)

    return sorted(items, key=score, reverse=True)[:max_items]


def _ground_body(h: dict, source_chars: int) -> str:
    """Grounding text for one library hit: the distilled summary plus an excerpt
    of the archived article body, up to the per-source budget. The full text is
    an internal grounding input only — the model is instructed to synthesize and
    cite, never reproduce it."""
    summary = (h.get("summary") or "").strip()
    content = (h.get("content") or "").strip()
    if summary and content:
        # Summary orients; the body excerpt (minus what the summary already
        # covers in length) supplies detail to answer specifics from.
        excerpt = content[: max(0, source_chars - len(summary))]
        return (summary + ("\n" + excerpt if excerpt else ""))[:source_chars]
    return (summary or content)[:source_chars]


def _build_source_documents(lib_hits: list[dict], feed_items: list[dict],
                            source_chars: int = 1800, global_chars: int = 16000
                            ) -> tuple[list[dict], list[dict]]:
    """Build Citations-API `document` content blocks for the retrieved sources.

    Returns (doc_blocks, sent_docs). doc_blocks are plain-text document blocks
    with citations enabled, in retrieval order — library first, then feed —
    which keeps numbering deterministic. sent_docs[i] describes doc_blocks[i]
    as {title, url, type}; a response citation's `document_index` indexes into
    it, so it must describe what was actually SENT, not everything retrieved.

    The same cost guards as the old flattened prompt apply, and only document
    text counts against them (titles ride in the block's `title` field, like
    the old header lines rode outside the budget): each source gets up to
    `source_chars` of grounding text and the running total is capped at
    `global_chars`. A source whose budget is exhausted (or that has no text at
    all) is skipped entirely — an empty document block is worse than none.
    """
    doc_blocks: list[dict] = []
    sent_docs: list[dict] = []
    used = 0

    def _add(title: str, url: str, kind: str, body: str,
             article_id: int | None = None) -> None:
        nonlocal used
        body = (body or "").strip()
        if not body:
            return
        used += len(body)
        doc_blocks.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": body},
            "title": (title or url)[:250],
            "citations": {"enabled": True},
        })
        doc = {"title": title or url, "url": url, "type": kind}
        if article_id is not None:
            # Library sources keep their articles.id so a persisted citation
            # can be traced back to the archive row (feed/web are transient —
            # their title/url snapshot is the whole record).
            doc["article_id"] = article_id
        sent_docs.append(doc)

    for h in lib_hits:
        if used >= global_chars:
            break
        _add(h.get("title", ""), h.get("url", ""), "library",
             _ground_body(h, min(source_chars, global_chars - used)),
             article_id=h.get("id"))

    for item in feed_items:
        if used >= global_chars:
            break
        _add(item.get("title", ""), item.get("url", ""), "feed",
             (item.get("summary") or "")[: min(source_chars, global_chars - used)])

    return doc_blocks, sent_docs


def _cit_get(c, name):
    """Read a field off a citation that may be an SDK object or a raw dict."""
    v = getattr(c, name, None)
    if v is None and isinstance(c, dict):
        v = c.get(name)
    return v


def _assemble_cited_answer(content_blocks, sent_docs: list[dict]
                           ) -> tuple[str, list[dict]]:
    """Reassemble the answer text with verified citation markers.

    With citations enabled, the answer arrives as multiple text blocks and
    cited spans carry a `citations` list. This appends [n] after each cited
    span (the API splits text exactly at citation boundaries), where n indexes
    one continuous, deduplicated, first-use-ordered list covering documents
    (via `document_index` into sent_docs) and web results (via URL citations)
    alike — the post-call renumbering that unifies all three source types.

    Returns (text, citations) where citations is [{n, title, url, type,
    article_id?}] for the sources actually cited. Best-effort by design: any surprise in the
    citation metadata degrades to the plain flattened text and an empty list —
    citation handling must never fail an answer.
    """
    try:
        parts: list[str] = []
        cited: list[dict] = []
        seen: dict = {}   # dedupe key -> assigned 1-based n

        for block in content_blocks:
            if getattr(block, "type", None) != "text":
                continue
            text = getattr(block, "text", "") or ""
            nums: list[int] = []
            for c in getattr(block, "citations", None) or []:
                doc_idx = _cit_get(c, "document_index")
                url = _cit_get(c, "url")
                if isinstance(doc_idx, int) and 0 <= doc_idx < len(sent_docs):
                    key = ("doc", doc_idx)
                    info = sent_docs[doc_idx]
                elif url:
                    key = ("web", url)
                    info = {"title": _cit_get(c, "title") or url,
                            "url": url, "type": "web"}
                else:
                    continue   # unrecognized citation shape — skip silently
                n = seen.get(key)
                if n is None:
                    n = len(cited) + 1
                    seen[key] = n
                    entry = {"n": n, "title": info["title"],
                             "url": info["url"], "type": info["type"]}
                    if info.get("article_id") is not None:
                        entry["article_id"] = info["article_id"]
                    cited.append(entry)
                if n not in nums:
                    nums.append(n)
            if nums:
                # Attach markers to the span itself, before trailing whitespace,
                # so they hug the sentence they cite.
                stripped = text.rstrip()
                trail = text[len(stripped):]
                text = stripped + "".join(f"[{n}]" for n in sorted(nums)) + trail
            parts.append(text)

        return "".join(parts).strip(), cited
    except Exception:
        text = "".join(
            getattr(b, "text", "") or "" for b in content_blocks
            if getattr(b, "type", None) == "text"
        ).strip()
        return text, []


def _trim_history(history) -> list[dict]:
    """Sanitize and bound prior conversation turns carried into the prompt:
    keep only well-formed user/assistant messages, the most recent ones, and
    stay under MAX_HISTORY_CHARS total (a cost guard)."""
    if not history:
        return []
    clean: list[dict] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content.strip()})
    # Keep the most recent turns within the char budget, oldest-first order.
    kept: list[dict] = []
    total = 0
    for m in reversed(clean[-2 * (MAX_FOLLOWUPS + 1):]):
        total += len(m["content"])
        if total > MAX_HISTORY_CHARS and kept:
            break
        kept.append(m)
    return list(reversed(kept))


def _collect_web_sources(content_blocks) -> list[dict]:
    """Pull title/url out of any web_search_tool_result blocks in the response."""
    out = []
    for block in content_blocks:
        if getattr(block, "type", None) == "web_search_tool_result":
            for r in getattr(block, "content", None) or []:
                url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                title = getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None)
                if url:
                    out.append({"title": title or url, "url": url})
    return out


def answer_question(
    lib: Library,
    question: str,
    model: str = "",
    effort: str = "standard",
    use_library: bool = True,
    use_feed: bool = False,
    use_web: bool = True,
    opml_path: str | None = None,
    history: list[dict] | None = None,
) -> Answer:
    """Answer a question from the configured sources.

    Args:
        lib: open Library connection (caller is responsible for closing it).
        question: the user's question.
        model: canonical model ID or alias from MODEL_ALIASES, to override the
            effort tier's default model. Leave blank (the normal case — the UI
            no longer exposes a model choice) to use the model that tier maps
            to in EFFORT_SETTINGS.
        effort: "quick" | "standard" | "deep" — controls source depth, token
            budget, and (absent an explicit `model`) which model answers.
        use_library: search the SQLite FTS5 library.
        use_feed: include recent RSS feed items (requires opml_path).
        use_web: enable web_search tool against trusted domains.
        opml_path: path to preferred_sites.opml; required when use_feed or use_web is True.
        history: prior [{role, content}] turns for a follow-up; bounded by
            MAX_HISTORY_CHARS. On follow-up turns retrieval runs on a
            history-aware rewrite of the question (falling back to the raw
            question if the rewrite fails); the answering prompt always
            carries the raw question verbatim.
    """
    settings = EFFORT_SETTINGS.get(effort, EFFORT_SETTINGS["standard"])
    model = MODEL_ALIASES.get(model, model) or settings.get("model") or DEFAULT_MODEL

    # Follow-up turns only: resolve pronouns/ellipsis into a standalone
    # question so FTS5 and feed matching see the conversation's subject, not
    # just the raw follow-up. Turn one (no history) skips this entirely —
    # zero added cost or latency for first questions.
    trimmed_history = _trim_history(history)
    rewrite = _rewrite_followup(trimmed_history, question) if trimmed_history else None
    rw_in = rewrite.input_tokens if rewrite else 0
    rw_out = rewrite.output_tokens if rewrite else 0
    rw_cost = rewrite.cost_usd if rewrite else 0.0
    retrieval_question = (rewrite.text if rewrite and rewrite.text else question)

    lib_hits: list[dict] = []
    feed_items: list[dict] = []
    embed_in = 0
    embed_cost = 0.0

    if use_library:
        lib_hits, embed_in, embed_cost = retrieve(
            lib, retrieval_question, max_sources=settings["max_library"])
    if use_feed and opml_path:
        feed_items = retrieve_feed(retrieval_question, opml_path, max_items=settings["max_feed"])

    import importlib.util
    if importlib.util.find_spec("anthropic") is None:
        return Answer(text="(Install `anthropic` to enable answers.)",
                      sources=lib_hits, feed_sources=feed_items, model=model,
                      cost_usd=rw_cost + embed_cost, rewrite_input_tokens=rw_in,
                      rewrite_output_tokens=rw_out, rewrite_cost_usd=rw_cost,
                      embed_input_tokens=embed_in, embed_cost_usd=embed_cost)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Answer(text="(Set ANTHROPIC_API_KEY to enable answers.)",
                      sources=lib_hits, feed_sources=feed_items, model=model,
                      cost_usd=rw_cost + embed_cost, rewrite_input_tokens=rw_in,
                      rewrite_output_tokens=rw_out, rewrite_cost_usd=rw_cost,
                      embed_input_tokens=embed_in, embed_cost_usd=embed_cost)

    # Retrieved sources ride as Citations-API document blocks (library first,
    # then feed — same order as the returned source lists). sent_docs is the
    # document_index -> source manifest used to resolve response citations.
    doc_blocks, sent_docs = _build_source_documents(
        lib_hits, feed_items,
        source_chars=settings.get("source_chars", 1800),
        global_chars=settings.get("global_chars", 16000),
    )

    system = _build_system(use_library, use_feed, use_web, lib)

    # The question rides verbatim as the final text block (never the rewrite —
    # the model already has the raw history for conversational context).
    # History turns stay flat strings; only the current turn uses blocks.
    user_content = doc_blocks + [{"type": "text", "text": f"QUESTION: {question}"}]
    messages = trimmed_history + [{"role": "user", "content": user_content}]
    kwargs: dict = {
        "model": model,
        "max_tokens": settings["max_tokens"],
        "system": system,
        "messages": messages,
    }

    if use_web:
        # preferred_domains() defaults to the configured OPML and returns ()
        # on any parse error, so no extra guarding is needed here.
        from .sources import preferred_domains
        domains = list(preferred_domains(opml_path) if opml_path else preferred_domains())
        tool: dict = {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": settings["web_max_uses"],
        }
        if domains:
            tool["allowed_domains"] = domains
        kwargs["tools"] = [tool]

    try:
        resp = _get_client().messages.create(**kwargs)
        text, citations = _assemble_cited_answer(resp.content, sent_docs)
        web = _collect_web_sources(resp.content) if use_web else []

        from .pricing import compute_cost
        usage = resp.usage
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return Answer(text=text, sources=lib_hits, feed_sources=feed_items, web_sources=web,
                     citations=citations,
                     model=model, input_tokens=in_tok, output_tokens=out_tok,
                     cache_creation_tokens=cache_w, cache_read_tokens=cache_r,
                     cost_usd=cost + rw_cost + embed_cost, rewrite_input_tokens=rw_in,
                     rewrite_output_tokens=rw_out, rewrite_cost_usd=rw_cost,
                     embed_input_tokens=embed_in, embed_cost_usd=embed_cost)
    except Exception as e:
        # The rewrite/embedding calls already spent real money even though
        # the answer call failed — keep their cost on the Answer so it's
        # still recorded.
        return Answer(text=f"(Answer call failed: {e})",
                      sources=lib_hits, feed_sources=feed_items, model=model,
                      cost_usd=rw_cost + embed_cost, rewrite_input_tokens=rw_in,
                      rewrite_output_tokens=rw_out, rewrite_cost_usd=rw_cost,
                      embed_input_tokens=embed_in, embed_cost_usd=embed_cost)
