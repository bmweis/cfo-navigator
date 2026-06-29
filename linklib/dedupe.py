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
    "saastr", "saastrai", "dear", "interesting", "learnings", "learning", "about",
}

# Revenue milestones in a title ("$100M ARR", "$1B", "$1 billion"). Two posts that
# cite DIFFERENT milestones aren't duplicates even if the rest reads the same
# (e.g. "5 Interesting Learnings about Snowflake at $100M" vs "...at $1B").
_FIGURE_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(m|b|k|million|billion|thousand)\b", re.IGNORECASE)


def _figures(text: str) -> set[str]:
    out = set()
    for num, unit in _FIGURE_RE.findall(text or ""):
        out.add(f"{num}{unit[0].lower()}")   # "100m", "1b", "1.5b"
    return out


# "Your first VP of Sales hire" posts are only dups when they're about the SAME
# role AND level — "first VP of Sales" ≠ "first VP of Marketing" ≠ "first Head of
# Sales". We pull the role/level words out of a "first … hire" title and veto a
# match when two such posts don't share an identical signature.
_FIRST_HIRE_RE = re.compile(r"\bfirst\s+(.+?)\s+hires?\b", re.IGNORECASE)
_ROLE_WORDS = {
    # functions / departments
    "sales", "marketing", "product", "engineering", "engineer", "finance",
    "revenue", "success", "people", "hr", "operations", "ops", "design",
    "data", "growth", "support", "recruiting", "recruiter", "legal",
    "accounting", "partnerships", "bizops", "analytics", "demand", "brand",
    # seniority / titles
    "cfo", "cro", "cmo", "cto", "coo", "cpo", "chro", "ceo", "cso", "cco",
    "vp", "svp", "evp", "head", "director", "chief", "manager", "lead",
    "controller", "founder", "rep", "gm",
}


def _role_sig(title: str) -> frozenset[str]:
    """Role/level tokens from a 'your first X hire' post, else empty. Two such
    posts are dups only if their signatures are identical, so a differing
    signature (different role and/or level) vetoes a match."""
    m = _FIRST_HIRE_RE.search(title or "")
    if not m:
        return frozenset()
    return frozenset(w for w in re.findall(r"[a-z]+", m.group(1).lower())
                     if w in _ROLE_WORDS)


def _first_topic(title: str) -> frozenset[str]:
    """For a 'your first X' title, the significant tokens after 'first' — the
    object the post is actually about. SaaStr reuses the "build your first sales
    ___" template across unrelated topics (a *comp plan* vs a *team/hire*); two
    such posts about different objects aren't dups even though the lead-in matches."""
    m = re.search(r"\bfirst\b(.*)$", (title or "").lower())
    if not m:
        return frozenset()
    return frozenset(_tokens(m.group(1)))


def _veto(a_title: str, b_title: str) -> bool:
    """A discriminator that says two posts CAN'T be dups regardless of overall
    similarity: a different revenue milestone, a different role/level in a 'first
    hire' post, or a different object in a 'your first X' template post."""
    fa, fb = _figures(a_title), _figures(b_title)
    if fa and fb and fa.isdisjoint(fb):
        return True
    ra, rb = _role_sig(a_title), _role_sig(b_title)
    if ra and rb and ra != rb:
        return True
    oa, ob = _first_topic(a_title), _first_topic(b_title)
    if oa and ob and _jaccard(set(oa), set(ob)) < 0.5:
        return True
    return False


def _norm_title(title: str, source: str = "") -> str:
    t = (title or "").lower().strip()
    # Drop a trailing "| Publication" segment ("| saastrai", "| saastr", ...).
    t = re.sub(r"\s*\|\s*[^|]*$", "", t)
    # Drop a leading column marker ("dear saastr:", "ask saastr -", ...).
    t = re.sub(r"^(dear|ask)\s+[\w&]+\s*[:,\-–—]\s*", "", t)
    # Drop the "N interesting learnings about/on/from …" series prefix so what
    # remains is the distinctive company + milestone (the figure veto guards the
    # milestone; this keeps the boilerplate from inflating title similarity).
    t = re.sub(r"^\d*\s*interesting\s+learnings?\s+(?:about|on|from|with|of|at)?\s*", "", t)
    # Drop an explicit source suffix even without a pipe.
    if source:
        t = re.sub(r"\s*[\-–—:]\s*" + re.escape(source.lower()) + r"\s*$", "", t)
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
    if _veto(a.get("title", ""), b.get("title", "")):
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
    """Group near-duplicate articles by LEADER clustering: process newest-first;
    each article either joins an existing group whose *kept* article (the leader,
    the newest) it directly duplicates, or starts its own group. No transitive
    chaining — so unrelated posts can't be linked through an intermediary — and
    every non-leader is, by construction, a dup of the one article that's kept.

    Returns clusters of 2+ (leader first). Each non-leader dict is annotated with
    `_dup_score` (0..1 similarity to its leader) so the UI can show what it dups.

    Scales to thousands: tokens precomputed once; candidates found via an inverted
    index on distinctive (rare) tokens only, then gated by the date window.
    """
    n = len(articles)
    _MIN = datetime.min.replace(tzinfo=timezone.utc)

    norm_titles, title_toks, summ_toks, dates, all_toks = [], [], [], [], []
    df: dict[str, int] = {}
    for a in articles:
        nt = _norm_title(a.get("title", ""), source)
        tt = _tokens(nt)
        st = _tokens(a.get("summary", "") or "")
        norm_titles.append(nt)
        title_toks.append(tt)
        summ_toks.append(st)
        dates.append(_date(a.get("published_at")))
        toks = tt | st
        all_toks.append(toks)
        for tok in toks:
            df[tok] = df.get(tok, 0) + 1

    max_df = max(25, n // 20)   # skip ubiquitous jargon when finding candidates

    def _score(i, j) -> float:
        ts, bj, sj = _components(articles[i], articles[j], source)
        return max(ts, bj, sj)

    def _dup(i, j) -> bool:
        if _veto(articles[i].get("title", ""), articles[j].get("title", "")):
            return False
        tj = _jaccard(title_toks[i], title_toks[j])
        bj = _jaccard(title_toks[i] | summ_toks[i], title_toks[j] | summ_toks[j])
        sj = _jaccard(summ_toks[i], summ_toks[j])
        if _is_dup(tj, bj, sj, threshold):
            return True
        tr = SequenceMatcher(None, norm_titles[i], norm_titles[j]).ratio() if norm_titles[i] and norm_titles[j] else 0.0
        return _is_dup(max(tr, tj), bj, sj, threshold)

    def _in_window(i, j) -> bool:
        if dates[i] and dates[j]:
            return abs((dates[i] - dates[j]).days) <= days
        return True

    order = sorted(range(n), key=lambda i: dates[i] or _MIN, reverse=True)  # newest first
    members: dict[int, list[int]] = {}     # leader idx -> member idxs (excl. leader)
    scores: dict[int, float] = {}          # member idx -> similarity to its leader
    leader_index: dict[str, list[int]] = {}  # rare token -> existing leader idxs

    for idx in order:
        rare = {t for t in all_toks[idx] if df.get(t, 0) <= max_df}
        cands = set()
        for tok in rare:
            cands.update(leader_index.get(tok, ()))
        best, best_s = None, 0.0
        for L in cands:
            if _in_window(idx, L) and _dup(idx, L):
                s = _score(idx, L)
                if s > best_s:
                    best, best_s = L, s
        if best is not None:
            members[best].append(idx)
            scores[idx] = best_s
        else:
            members[idx] = []           # new leader
            for tok in rare:
                leader_index.setdefault(tok, []).append(idx)

    out = []
    for L, mem in members.items():
        if not mem:
            continue
        cluster = [articles[L]]
        for m in mem:
            articles[m]["_dup_score"] = round(scores.get(m, 0.0), 2)
            cluster.append(articles[m])
        out.append(cluster)
    out.sort(key=len, reverse=True)
    return out
