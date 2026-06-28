"""Learn the librarian's tagging style from their existing library.

Brian tagged thousands of articles by hand in Feedly before this tool existed.
That history encodes *how* he tags — granularity, when a tag applies, what he
leaves untagged — not just which words he uses. This module samples that history
and asks Claude to distill it into a short "tagging guide" (soft rules). The guide
is stored in settings and injected into the enrichment prompt so auto-tagging
mimics his judgment.

Like the rest of the Claude-backed code: needs the SDK + ANTHROPIC_API_KEY, and
degrades to None when they're absent.
"""
from __future__ import annotations

import os

from .db import Library

# Reuse the chat-quality model by default — this is a one-time synthesis where
# depth matters more than cost. Overridable.
GUIDE_MODEL = os.environ.get("LINKLIB_TAG_GUIDE_MODEL", "claude-opus-4-8")

TAG_GUIDE_KEY = "tag_guide"

_GUIDE_PROMPT = """You are studying how one finance leader tagged their personal
research library so an automated tagger can imitate their judgment.

Below are their most-used tags, each with how many articles carry it and a few
example articles (title — summary). Infer their tagging *style*, then write a
concise guide (a few hundred words, markdown) that an auto-tagger can follow.

Cover:
- The canonical vocabulary: list the core tags with a short "use when…" for each.
- Granularity: do they prefer broad buckets or specific tags? How many tags per
  article is typical?
- Casing / wording conventions (e.g. lowercase, hyphenation, singular vs plural).
- Patterns: tags that travel together; topic→tag mappings that are clearly habitual.
- What they tend NOT to tag, or distinctions they avoid.

Write it as direct instructions ("Tag X when…"), not analysis of the data. Do not
restate these instructions or mention this prompt. Output only the guide.

LIBRARY TAGGING DATA
{data}
"""


def build_tag_profile(lib: Library, max_tags: int = 45,
                      examples_per_tag: int = 6, summary_chars: int = 180) -> dict:
    """Summarize how the library is tagged: top tags with counts + example articles,
    plus a tags-per-article average. Pure/local — no API call."""
    arts = lib.all_articles(limit=100000)
    counts: dict[str, int] = {}
    examples: dict[str, list[tuple[str, str]]] = {}
    per_counts: list[int] = []
    for a in arts:
        tags = a.get("tags", []) or []
        if tags:
            per_counts.append(len(tags))
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
            ex = examples.setdefault(t, [])
            if len(ex) < examples_per_tag:
                ex.append((a.get("title", "") or "", (a.get("summary") or "")[:summary_chars]))
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:max_tags]
    avg = round(sum(per_counts) / len(per_counts), 1) if per_counts else 0
    return {
        "total": len(arts),
        "tagged": len(per_counts),
        "avg_tags": avg,
        "distinct_tags": len(counts),
        "tags": [{"tag": t, "count": c, "examples": examples.get(t, [])} for t, c in top],
    }


def profile_to_text(profile: dict) -> str:
    lines = [
        f"Library: {profile['total']} articles, {profile['tagged']} tagged, "
        f"{profile['distinct_tags']} distinct tags, ~{profile['avg_tags']} tags per tagged article.",
        "",
        "Most-used tags with example articles:",
    ]
    for e in profile["tags"]:
        lines.append(f'- "{e["tag"]}" ({e["count"]} articles):')
        for title, summ in e["examples"]:
            snippet = (title or "(untitled)")
            if summ:
                snippet += f" — {summ}"
            lines.append(f"    * {snippet}")
    return "\n".join(lines)


def generate_tag_guide(lib: Library, model: str | None = None) -> str | None:
    """Build the profile and ask Claude to distill a tagging guide. Returns the
    guide text, or None if there's nothing to learn from or the API is unavailable."""
    profile = build_tag_profile(lib)
    if not profile["tags"]:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model or GUIDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user",
                       "content": _GUIDE_PROMPT.format(data=profile_to_text(profile))}],
        )
        guide = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return guide or None
    except Exception:
        return None
