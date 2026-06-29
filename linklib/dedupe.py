"""Near-duplicate detection for sources that rerun similar content (e.g. SaaStr).

Exact-URL dedup (db.normalize_url) catches link variants; this catches the same
piece republished under a different title/url within a time window. No ML deps —
combines normalized-title sequence similarity with token-set (Jaccard) overlap on
title + summary, gated by a publish-date window.

Tunable and deliberately a bit aggressive: the goal is to keep even *potential*
dupes out of the library.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.62
DEFAULT_WINDOW_DAYS = 90

# Generic words that don't help tell two posts apart in this domain.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "your",
    "you", "how", "why", "what", "is", "are", "be", "this", "that", "it", "at",
    "as", "from", "by", "vs", "via", "saas", "b2b", "startup", "startups",
    "company", "companies", "guide", "post", "blog", "2021", "2022", "2023",
    "2024", "2025", "2026", "part", "ways", "things", "tips",
}


def _norm_title(title: str, source: str = "") -> str:
    t = (title or "").lower()
    if source:
        t = re.sub(r"\s*[|\-–—:]\s*" + re.escape(source.lower()) + r"\s*$", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _date(s) -> datetime | None:
    s = (s or "")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def similarity(a: dict, b: dict, source: str = "") -> float:
    """0..1 similarity between two article dicts ({title, summary}). Takes the
    strongest of several signals so either a near-identical title OR near-identical
    summary (a reworded-title rerun) is enough to flag."""
    ta, tb = _norm_title(a.get("title", ""), source), _norm_title(b.get("title", ""), source)
    title_ratio = SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0
    title_jac = _jaccard(_tokens(ta), _tokens(tb))
    body_jac = _jaccard(
        _tokens(f'{a.get("title","")} {a.get("summary","") or ""}'),
        _tokens(f'{b.get("title","")} {b.get("summary","") or ""}'),
    )
    summary_jac = _jaccard(_tokens(a.get("summary", "") or ""), _tokens(b.get("summary", "") or ""))
    return max(title_ratio, title_jac, body_jac, summary_jac)


def _within_window(a: dict, b: dict, days: int) -> bool:
    da, db = _date(a.get("published_at")), _date(b.get("published_at"))
    if da and db:
        return abs((da - db).days) <= days
    return True   # a missing date can't rule out a dup — compare on text


def is_near_dup(a: dict, b: dict, *, days: int = DEFAULT_WINDOW_DAYS,
                threshold: float = DEFAULT_THRESHOLD, source: str = "") -> bool:
    return _within_window(a, b, days) and similarity(a, b, source) >= threshold


def is_dup_of_any(candidate: dict, existing: list[dict], *,
                  days: int = DEFAULT_WINDOW_DAYS, threshold: float = DEFAULT_THRESHOLD,
                  source: str = "") -> bool:
    """True if `candidate` near-duplicates any item in `existing` (same source)."""
    return any(is_near_dup(candidate, e, days=days, threshold=threshold, source=source)
               for e in existing)


def find_clusters(articles: list[dict], *, days: int = DEFAULT_WINDOW_DAYS,
                  threshold: float = DEFAULT_THRESHOLD, source: str = "") -> list[list[dict]]:
    """Group near-duplicate articles into clusters of 2+ (union-find). Each cluster
    is sorted newest-first, so the first item is the natural one to keep."""
    n = len(articles)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if is_near_dup(articles[i], articles[j], days=days, threshold=threshold, source=source):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(articles[i])

    def _key(a):
        return _date(a.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)

    out = [sorted(g, key=_key, reverse=True) for g in groups.values() if len(g) > 1]
    out.sort(key=len, reverse=True)
    return out
