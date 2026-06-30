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

# Controls how many sources are pulled and how long synthesis runs.
# web_max_uses caps the number of web_search tool calls Claude may make.
EFFORT_SETTINGS: dict[str, dict] = {
    # source_chars: per-source grounding budget (summary + archived-text excerpt).
    # global_chars: hard cap on TOTAL grounding text per answer, so a query that
    #   retrieves many long articles can't balloon the prompt (cost guard).
    "quick":    {"max_library": 4,  "max_feed": 3,  "web_max_uses": 2, "max_tokens": 700,  "source_chars": 900,  "global_chars": 6000},
    "standard": {"max_library": 8,  "max_feed": 5,  "web_max_uses": 4, "max_tokens": 1500, "source_chars": 1800, "global_chars": 16000},
    "deep":     {"max_library": 16, "max_feed": 8,  "web_max_uses": 6, "max_tokens": 2500, "source_chars": 3500, "global_chars": 40000},
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


def _build_system(use_library: bool, use_feed: bool, use_web: bool) -> str:
    """Build the advisor system prompt, describing only the active source types."""
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

    cite_parts = []
    if use_library or use_feed:
        cite_parts.append("Cite the numbered sources you actually used with [n].")
    if use_web:
        cite_parts.append("Name web results inline with their title and URL.")
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
        "best-effort answer.\n"
        "- End with a one-line \"Worth reading:\" pointer to the 1–2 most useful sources."
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


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)       # saved-library hits
    feed_sources: list[dict] = field(default_factory=list)  # RSS feed hits
    web_sources: list[dict] = field(default_factory=list)   # fresh web results


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


def retrieve(lib: Library, question: str, max_sources: int = 8) -> list[dict]:
    return lib.search(_safe_fts_query(question), limit=max_sources)


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


def _format_all_sources(lib_hits: list[dict], feed_items: list[dict],
                        source_chars: int = 1800, global_chars: int = 16000) -> str:
    """Format library and feed items as a unified numbered block for the prompt.

    Each source gets up to `source_chars` of grounding text; the running total
    is capped at `global_chars` so a query that hits many long articles can't
    balloon the prompt (a cost guard).
    """
    blocks: list[str] = []
    idx = 1
    used = 0

    if lib_hits:
        blocks.append("=== SAVED LIBRARY ===")
        for h in lib_hits:
            if used >= global_chars:
                break
            body = _ground_body(h, min(source_chars, global_chars - used))
            used += len(body)
            tags = ", ".join(h.get("tags", []))
            blocks.append(
                f"[{idx}] {h['title']}\n"
                f"    URL: {h['url']}\n"
                f"    Tags: {tags}\n"
                f"    {body}"
            )
            idx += 1

    if feed_items:
        blocks.append("=== CURRENT RSS FEED ===")
        for item in feed_items:
            if used >= global_chars:
                break
            body = (item.get("summary") or "")[: min(source_chars, global_chars - used)]
            used += len(body)
            blocks.append(
                f"[{idx}] {item['title']}\n"
                f"    URL: {item['url']}\n"
                f"    Source: {item.get('source', '')}\n"
                f"    {body}"
            )
            idx += 1

    return "\n\n".join(blocks) or "(no local articles matched)"


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
    model: str = DEFAULT_MODEL,
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
        model: canonical model ID or alias from MODEL_ALIASES.
        effort: "quick" | "standard" | "deep" — controls source depth and token budget.
        use_library: search the SQLite FTS5 library.
        use_feed: include recent RSS feed items (requires opml_path).
        use_web: enable web_search tool against trusted domains.
        opml_path: path to preferred_sites.opml; required when use_feed or use_web is True.
        history: prior [{role, content}] turns for a follow-up; bounded by
            MAX_HISTORY_CHARS. Retrieval still runs on the current question.
    """
    model = MODEL_ALIASES.get(model, model) or DEFAULT_MODEL
    settings = EFFORT_SETTINGS.get(effort, EFFORT_SETTINGS["standard"])

    lib_hits: list[dict] = []
    feed_items: list[dict] = []

    if use_library:
        lib_hits = retrieve(lib, question, max_sources=settings["max_library"])
    if use_feed and opml_path:
        feed_items = retrieve_feed(question, opml_path, max_items=settings["max_feed"])

    import importlib.util
    if importlib.util.find_spec("anthropic") is None:
        return Answer(text="(Install `anthropic` to enable answers.)",
                      sources=lib_hits, feed_sources=feed_items)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Answer(text="(Set ANTHROPIC_API_KEY to enable answers.)",
                      sources=lib_hits, feed_sources=feed_items)

    sources_block = _format_all_sources(
        lib_hits, feed_items,
        source_chars=settings.get("source_chars", 1800),
        global_chars=settings.get("global_chars", 16000),
    )
    prompt = f"LOCAL SOURCES:\n{sources_block}\n\nQUESTION: {question}"

    system = _build_system(use_library, use_feed, use_web)

    messages = _trim_history(history) + [{"role": "user", "content": prompt}]
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
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        web = _collect_web_sources(resp.content) if use_web else []
        return Answer(text=text, sources=lib_hits, feed_sources=feed_items, web_sources=web)
    except Exception as e:
        return Answer(text=f"(Answer call failed: {e})",
                      sources=lib_hits, feed_sources=feed_items)
