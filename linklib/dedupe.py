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

DEFAULT_THRESHOLD = 0.62      # applies to TITLE similarity (the strictness lever)
DEFAULT_WINDOW_DAYS = 90
# Body/summary overlap must be strong to link two items on its own, so generic
# shared finance words can't chain unrelated posts into one mega-cluster.
_BODY_BAR = 0.72
_SUMMARY_BAR = 0.74

# Generic words that don't help tell two posts apart in this domain.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "your",
    "you", "how", "why", "what", "is", "are", "be", "this", "that", "it", "at",
    "as", "from", "by", "vs", "via", "saas", "b2b", "startup", "startups",
    "company", "companies", "guide", "post", "blog", "2021", "2022", "2023",
    "2024", "2025", "2026", "part", "ways", "things", "tips",
    # generic finance/business terms that recur across most posts
    "cfo", "ceo", "revenue", "growth", "business", "team", "market", "year",
    "best", "new", "great", "way", "every", "should", "must", "need", "get",
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


def _components(a: dict, b: dict, source: str) -> tuple[float, float, float]:
    """(title_sim, body_jac, summary_jac) — the raw signals for two articles."""
    ta, tb = _norm_title(a.get("title", ""), source), _norm_title(b.get("title", ""), source)
    title_ratio = SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0
    title_jac = _jaccard(_tokens(ta), _tokens(tb))
    body_jac = _jaccard(
        _tokens(f'{a.get("title","")} {a.get("summary","") or ""}'),
        _tokens(f'{b.get("title","")} {b.get("summary","") or ""}'),
    )
    summary_jac = _jaccard(_tokens(a.get("summary", "") or ""), _tokens(b.get("summary", "") or ""))
    return max(title_ratio, title_jac), body_jac, summary_jac


def similarity(a: dict, b: dict, source: str = "") -> float:
    """Best single similarity signal (title-led), for debugging/transparency."""
    ts, bj, sj = _components(a, b, source)
    return max(ts, bj, sj)


def _within_window(a: dict, b: dict, days: int) -> bool:
    da, db = _date(a.get("published_at")), _date(b.get("published_at"))
    if da and db:
        return abs((da - db).days) <= days
    return True   # a missing date can't rule out a dup — compare on text


def _is_dup(title_sim: float, body_jac: float, summary_jac: float, threshold: float) -> bool:
    # Title similarity is the strictness-controlled signal; body/summary overlap
    # only links on its own when strong (fixed bars), to avoid over-merging.
    return title_sim >= threshold or body_jac >= _BODY_BAR or summary_jac >= _SUMMARY_BAR


def is_near_dup(a: dict, b: dict, *, days: int = DEFAULT_WINDOW_DAYS,
                threshold: float = DEFAULT_THRESHOLD, source: str = "") -> bool:
    if not _within_window(a, b, days):
        return False
    ts, bj, sj = _components(a, b, source)
    return _is_dup(ts, bj, sj, threshold)


def is_dup_of_any(candidate: dict, existing: list[dict], *,
                  days: int = DEFAULT_WINDOW_DAYS, threshold: float = DEFAULT_THRESHOLD,
                  source: str = "") -> bool:
    """True if `candidate` near-duplicates any item in `existing` (same source)."""
    return any(is_near_dup(candidate, e, days=days, threshold=threshold, source=source)
               for e in existing)


def find_clusters(articles: list[dict], *, days: int = DEFAULT_WINDOW_DAYS,
                  threshold: float = DEFAULT_THRESHOLD, source: str = "") -> list[list[dict]]:
    """Group near-duplicate articles into clusters of 2+ (union-find). Each cluster
    is sorted newest-first, so the first item is the natural one to keep.

    Scales to thousands of articles: tokens/titles are precomputed once, and only
    candidate pairs that share a content token (inverted index) AND fall within the
    date window are compared — instead of all O(n²) pairs.
    """
    n = len(articles)
    _MIN = datetime.min.replace(tzinfo=timezone.utc)

    # Precompute per-article fields once (not per pair).
    norm_titles, title_toks, summ_toks, dates = [], [], [], []
    index: dict[str, list[int]] = {}
    for i, a in enumerate(articles):
        nt = _norm_title(a.get("title", ""), source)
        tt = _tokens(nt)
        st = _tokens(a.get("summary", "") or "")
        norm_titles.append(nt)
        title_toks.append(tt)
        summ_toks.append(st)
        dates.append(_date(a.get("published_at")))
        for tok in (tt | st):
            index.setdefault(tok, []).append(i)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _dup(i, j) -> bool:
        tr = SequenceMatcher(None, norm_titles[i], norm_titles[j]).ratio() if norm_titles[i] and norm_titles[j] else 0.0
        tj = _jaccard(title_toks[i], title_toks[j])
        bj = _jaccard(title_toks[i] | summ_toks[i], title_toks[j] | summ_toks[j])
        sj = _jaccard(summ_toks[i], summ_toks[j])
        return _is_dup(max(tr, tj), bj, sj, threshold)

    def _in_window(i, j) -> bool:
        if dates[i] and dates[j]:
            return abs((dates[i] - dates[j]).days) <= days
        return True

    for i in range(n):
        # Candidate j's: share ≥1 content token with i (and j > i, not already merged).
        cands = set()
        for tok in (title_toks[i] | summ_toks[i]):
            for j in index.get(tok, ()):
                if j > i:
                    cands.add(j)
        for j in cands:
            if find(i) == find(j):
                continue
            if _in_window(i, j) and _dup(i, j):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(articles[i])

    out = [sorted(g, key=lambda a: _date(a.get("published_at")) or _MIN, reverse=True)
           for g in groups.values() if len(g) > 1]
    out.sort(key=len, reverse=True)
    return out
