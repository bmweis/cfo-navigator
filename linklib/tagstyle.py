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
TAG_OBJECTIVE_KEY = "tag_objective"

# The "why" behind the tags — editable. Tags exist to help a finance leader act as
# a strategic thought partner, so they should map to the jobs that role gets pulled
# into, not academic topic labels. This frames both the learning and live tagging.
DEFAULT_TAG_OBJECTIVE = (
    "These tags exist to help a finance leader at a high-growth tech company act as a "
    "strategic thought partner across the business — e.g. GTM efficiency, org-wide team "
    "design and staffing, mentorship and talent development, and the cross-functional "
    "decisions a strategic-finance leader weighs in on. Tag for those jobs-to-be-done: "
    "prefer tags that map to the strategic questions a finance leader gets pulled into, "
    "not just academic topic labels."
)


def get_tag_objective(lib: Library) -> str:
    return (lib.get_setting(TAG_OBJECTIVE_KEY) or "").strip() or DEFAULT_TAG_OBJECTIVE


def effective_tag_guidance(lib: Library) -> str:
    """The full tagging guidance injected into enrichment: the objective (why the
    tags exist) plus the learned guide (how this librarian tags). The objective
    always applies, so tagging is steered even before a guide is generated."""
    parts = [f"Why these tags exist:\n{get_tag_objective(lib)}"]
    guide = (lib.get_setting(TAG_GUIDE_KEY) or "").strip()
    if guide:
        parts.append(f"How this librarian tags (learned from their library):\n{guide}")
    return "\n\n".join(parts)

_GUIDE_PROMPT = """You are studying how one finance leader tagged their personal
research library so an automated tagger can imitate their judgment.

THE PURPOSE OF THESE TAGS (orient every rule around this):
{objective}

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
                       "content": _GUIDE_PROMPT.format(objective=get_tag_objective(lib),
                                                       data=profile_to_text(profile))}],
        )
        guide = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return guide or None
    except Exception:
        return None


_MERGE_PROMPT = """A curated finance-library has accumulated too many tags. Below
are its tags with article counts. Propose consolidations: group tags that are
synonyms, near-synonyms, or minor variants of each other and should be merged
into one.

Rules:
- Only merge tags that mean essentially the SAME thing (e.g. "saas metrics" /
  "saas-metrics" / "metrics-saas"; "hiring" / "recruiting" / "talent"; "arr" /
  "annual recurring revenue"). Do NOT merge tags that are genuinely distinct.
- For each group, pick the cleanest canonical tag (prefer the clearer wording;
  ties go to the higher count). Use lowercase, hyphenated form when sensible.
- It's fine to leave most tags alone. Return only the groups worth merging.

Return STRICT JSON only: an array of
  {{"canonical": "<tag to keep>", "merge": ["<tag>", ...], "reason": "<≤8 words>"}}
where "merge" lists the OTHER tags to fold into canonical (not including canonical
itself). No prose, no markdown.

TAGS (tag — count):
{tags}
"""


def suggest_tag_merges(lib: Library, model: str | None = None) -> list | None:
    """Ask Claude to propose tag-merge groups from the library's vocabulary.
    Returns [{"canonical", "merge":[...], "reason"}], filtered to real existing
    tags, or None if unavailable / nothing to do."""
    tags = lib.all_tags()  # [(tag, count)] desc
    if len(tags) < 3:
        return []
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    listing = "\n".join(f"- {t} — {c}" for t, c in tags)
    valid = {t for t, _ in tags}
    counts = dict(tags)
    try:
        import json
        client = Anthropic()
        resp = client.messages.create(
            model=model or GUIDE_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": _MERGE_PROMPT.format(tags=listing)}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        out = []
        for g in data:
            canon = str(g.get("canonical", "")).strip()
            merge = [str(m).strip() for m in g.get("merge", []) if str(m).strip()]
            # keep only real tags; canonical may be new wording but must differ from merges
            merge = [m for m in merge if m in valid and m != canon]
            if canon and merge:
                out.append({"canonical": canon, "merge": merge,
                            "reason": str(g.get("reason", "")).strip()[:60],
                            "count": sum(counts.get(m, 0) for m in merge) + counts.get(canon, 0)})
        return out
    except Exception:
        return None
