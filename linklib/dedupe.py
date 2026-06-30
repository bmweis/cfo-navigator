"""Near-duplicate detection for sources that rerun similar content (e.g. SaaStr).

Exact-URL dedup (db.normalize_url) catches link variants; this catches the same
piece republished under a different title/url within a time window. No ML deps —
combines normalized-title sequence similarity with token-set (Jaccard) overlap on
title + summary, gated by a publish-date window.

Tunable and deliberately a bit aggressive: the goal is to keep even *potential*
dupes out of the library.
"""
from __future__ import annotations

import os
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


# Hiring-advice posts ("When should I hire my first AE?", "When to hire a CFO")
# are only dups when they're about the SAME role/level. A first AE, a first
# finance hire, and a CFO are different hires — distinct posts, not reruns — so a
# differing role signature vetoes a match. Catches "hire a/your [first] X" forms,
# not just "first … hire".
_HIRE_RE = re.compile(r"\bhir(?:e|es|ed|ing)\b", re.IGNORECASE)
_ROLE_WORDS = {
    # functions / departments
    "sales", "marketing", "product", "engineering", "engineer", "finance",
    "revenue", "success", "people", "hr", "operations", "ops", "design",
    "data", "growth", "support", "recruiting", "recruiter", "legal",
    "accounting", "partnerships", "bizops", "analytics", "demand", "brand",
    # individual-contributor / sales roles
    "ae", "sdr", "bdr", "rep", "ic",
    # seniority / titles
    "cfo", "cro", "cmo", "cto", "coo", "cpo", "chro", "ceo", "cso", "cco",
    "vp", "svp", "evp", "head", "director", "chief", "manager", "lead",
    "controller", "founder", "gm",
}

# A trailing "— From <Publication>" / "(From <Publication>)" byline marks a guest
# cross-post: a different author's article, distinct even when the topic matches a
# native column piece. Searched after stripping a "| Publication" suffix.
_GUEST_RE = re.compile(r"[—–\-(]\s*from\s+([a-z0-9][\w .&]*?)\s*\)?$", re.IGNORECASE)


def _hire_role(title: str) -> frozenset[str]:
    """Role/level tokens from a hiring-advice title, else empty. Two hiring posts
    are dups only if their role signatures match, so different roles or seniority
    levels veto a match."""
    t = (title or "").lower()
    if not _HIRE_RE.search(t):
        return frozenset()
    return frozenset(w for w in re.findall(r"[a-z]+", t) if w in _ROLE_WORDS)


def _guest_source(title: str) -> str:
    """The publication in a "— From <X>" guest byline, else "". A differing source
    means different authors' articles, which aren't dups of each other."""
    t = re.sub(r"\s*\|\s*[^|]*$", "", (title or "").lower()).strip()
    m = _GUEST_RE.search(t)
    return m.group(1).strip() if m else ""


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
    similarity: a different revenue milestone, a different role/level in a hiring
    post, a different guest author, or a different object in a 'first X' template."""
    fa, fb = _figures(a_title), _figures(b_title)
    if fa and fb and fa.isdisjoint(fb):
        return True
    ra, rb = _hire_role(a_title), _hire_role(b_title)
    if ra and rb and ra != rb:
        return True
    if _guest_source(a_title) != _guest_source(b_title):
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


def _seqratio(a: str, b: str) -> float:
    """Symmetric SequenceMatcher ratio. difflib's ratio() is order-dependent
    (ratio(a,b) != ratio(b,a)), which made the pairwise check and the cluster
    check disagree on borderline pairs; take the max so the result is stable."""
    if not a or not b:
        return 0.0
    return max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, b, a).ratio())


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
    title_ratio = _seqratio(ta, tb)
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
        tr = _seqratio(norm_titles[i], norm_titles[j])
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


# ---------------------------------------------------------------------------
# Keeper selection + Claude verification
#
# Title similarity is a high-recall *candidate* generator: it reliably surfaces
# possible dupes but can't tell "same talk, different headline" (a real dupe)
# from "VP of Sales vs VP of CS, one phrase different" (not a dupe) — that needs
# the article's meaning, not its characters. So candidates are verified by Claude
# against title + summary. With no API key it degrades to the raw candidates.
# ---------------------------------------------------------------------------

def _is_dear(a: dict) -> bool:
    """A "Dear SaaStr: …" / "Ask SaaStr: …" advice-column post (the rehash), as
    opposed to an original article."""
    return bool(re.match(r"\s*(dear|ask)\s+saastr\s*[:,]", (a.get("title") or ""), re.IGNORECASE))


def _keeper_sorted(cluster: list[dict]) -> list[dict]:
    """Order a cluster so the article to KEEP is first: prefer a non-"Dear SaaStr"
    original over the column rehash, then the newest publish date."""
    def key(a: dict):
        d = _date(a.get("published_at"))
        ts = d.timestamp() if d else float("-inf")
        return (_is_dear(a), -ts)   # non-dear first; newest first
    return sorted(cluster, key=key)


def _rekey_cluster(cluster: list[dict], source: str = "") -> list[dict]:
    """Pick the keeper (non-Dear, then newest), put it first, and annotate every
    other member with `_dup_score` = similarity to that keeper for the UI."""
    ordered = _keeper_sorted(cluster)
    keeper = ordered[0]
    for a in ordered[1:]:
        a["_dup_score"] = round(similarity(a, keeper, source), 2)
    keeper.pop("_dup_score", None)
    return ordered


_VERIFY_MODEL = os.environ.get("LINKLIB_DEDUPE_MODEL",
                               os.environ.get("LINKLIB_CHAT_MODEL", "claude-sonnet-4-6"))

_VERIFY_PROMPT = """A finance leader is de-duplicating a research library. A fast
text filter flagged the articles below as POSSIBLE near-duplicates, but it only
compares titles and over-flags. Decide which are TRULY duplicates.

Two articles are duplicates ONLY if they cover the same underlying piece: the same
specific question with the same answer, the same talk/interview retitled, or the
same article republished under a different headline. They are NOT duplicates if
they differ in any material way, even when the wording looks similar — for example:
- a different role or seniority level (VP of Sales vs VP of Customer Success; a CFO
  vs a first finance hire; an SDR vs an SDR manager; an AE vs a sales rep),
- a different focus (building/managing a team vs being effective in the role;
  quota vs ramp time; paying on monthly deals vs on renewals; a comp plan vs a
  hiring plan),
- a different company, revenue milestone, or stage,
- a genuinely different question, even if it shares a template ("Dear SaaStr…",
  "The #1 mistake…", "5 Interesting Learnings…").

Conversely, DO group two articles that are clearly the same content retitled (e.g.
a talk summarized two ways, naming the same speakers/companies).

The filter's candidate groupings are shown only as hints — regroup freely. It is
fine to return no groups at all.
{learned}
Return STRICT JSON only: {{"groups": [["<id>", "<id>", ...], ...]}} where each inner
array lists the ids of 2+ articles that genuinely duplicate one another. Omit any
article with no duplicate. No prose, no markdown.

CANDIDATE GROUPS (the filter's guesses — verify each):
{blocks}
"""


def _learned_block(decisions: list[dict] | None) -> str:
    """Few-shot guidance distilled from the curator's own past calls, so the
    verifier matches their judgment on similar pairs over time."""
    if not decisions:
        return ""
    dup = [d for d in decisions if d.get("verdict") == "dup"]
    distinct = [d for d in decisions if d.get("verdict") == "distinct"]
    lines = ["\nHOW THIS CURATOR HAS JUDGED PAST PAIRS (match this judgment):"]
    if distinct:
        lines.append("Pairs they said are NOT duplicates (keep both):")
        for d in distinct[:25]:
            lines.append(f'  - "{d.get("title_a","")}"  VS  "{d.get("title_b","")}"')
    if dup:
        lines.append("Pairs they confirmed ARE duplicates (same piece):")
        for d in dup[:15]:
            lines.append(f'  - "{d.get("title_a","")}"  ==  "{d.get("title_b","")}"')
    return "\n".join(lines) + "\n"


def _pair_key(a: dict, b: dict) -> str:
    # Must match Library._pair_key so distinct_pairs lookups line up.
    return "\n".join(sorted([a.get("url", "") or "", b.get("url", "") or ""]))


def _prune_distinct(group: list[dict], distinct_pairs: set | None) -> list[list[dict]]:
    """Remove edges the curator marked 'distinct'. The keeper anchors the group;
    any member the curator said is NOT a dupe of the keeper is split out. Returns
    the surviving group(s) of 2+ (the pruned member could still dup another, so it
    is re-offered on its own — but in practice it just drops out)."""
    if not distinct_pairs:
        return [group]
    ordered = _keeper_sorted(group)
    keeper = ordered[0]
    kept = [keeper]
    for m in ordered[1:]:
        if _pair_key(keeper, m) not in distinct_pairs:
            kept.append(m)
    return [kept] if len(kept) >= 2 else []


def _verify_blocks(clusters: list[list[dict]]) -> tuple[str, dict]:
    """Render candidate clusters for the prompt and return (text, id->article)."""
    by_id: dict[str, dict] = {}
    lines = []
    for gi, c in enumerate(clusters, 1):
        lines.append(f"[Group {gi}]")
        for a in c:
            aid = str(a.get("id") or a.get("url"))
            by_id[aid] = a
            title = (a.get("title") or a.get("url") or "").strip()
            date = (a.get("published_at") or "")[:10]
            summ = (a.get("summary") or "").strip().replace("\n", " ")[:240]
            line = f"- id={aid} — {date} — {title}"
            if summ:
                line += f" — {summ}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines), by_id


_VERIFY_BATCH = 10   # clusters per Claude call, so the JSON reply never truncates


def _verify_one_batch(client, batch: list[list[dict]], model: str | None,
                      decisions: list[dict] | None) -> list[list[dict]]:
    """Ask Claude to confirm one batch of candidate clusters. Returns the member
    lists it judged to be true duplicate groups. Raises on API/parse failure."""
    import json
    blocks, by_id = _verify_blocks(batch)
    resp = client.messages.create(
        model=model or _VERIFY_MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": _VERIFY_PROMPT.format(
            blocks=blocks, learned=_learned_block(decisions))}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Tolerate any prose/markdown around the JSON: decode the first {...} object
    # and ignore trailing text (Claude sometimes appends a sentence of reasoning,
    # which made strict json.loads raise "Extra data").
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in verification reply")
    data, _end = json.JSONDecoder().raw_decode(raw[start:])
    groups = []
    for grp in data.get("groups", []):
        members, seen = [], set()
        for gid in grp:
            a = by_id.get(str(gid))
            if a is not None and id(a) not in seen:
                members.append(a)
                seen.add(id(a))
        if len(members) >= 2:
            groups.append(members)
    return groups


def verify_clusters(clusters: list[list[dict]], *, source: str = "",
                    model: str | None = None, distinct_pairs: set | None = None,
                    decisions: list[dict] | None = None) -> tuple[list[list[dict]], str]:
    """Confirm which candidate near-dup clusters are real, using Claude's judgment
    over title + summary. Splits/drops over-flagged groups and re-picks the keeper
    (non-"Dear SaaStr", then newest) for each surviving group.

    Learns from the curator's feedback: `distinct_pairs` (pairs already marked
    "not a duplicate") are pruned so they're never re-shown, and `decisions` (their
    past dup/distinct calls) are given to Claude as few-shot guidance.

    Returns (clusters, status) where status is "verified" when Claude actually
    judged every batch, or "no_sdk" / "no_key" / "error: …" when it couldn't —
    in which case the *raw* title-match clusters are returned (keeper re-selected,
    known-distinct pairs pruned) and the caller should label them as unverified.

    Clusters are verified in small batches so a big scan's reply can't truncate
    (the bug that made everything silently fall back)."""
    clusters = [c for c in clusters if len(c) >= 2]
    if not clusters:
        return [], "verified"

    def _finish(groups: list[list[dict]]) -> list[list[dict]]:
        out = []
        for g in groups:
            for pruned in _prune_distinct(g, distinct_pairs):
                out.append(_rekey_cluster(pruned, source))
        out.sort(key=len, reverse=True)
        return out

    try:
        from anthropic import Anthropic
    except ImportError:
        return _finish(clusters), "no_sdk"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _finish(clusters), "no_key"

    client = Anthropic()
    all_groups: list[list[dict]] = []
    err = None
    for i in range(0, len(clusters), _VERIFY_BATCH):
        batch = clusters[i:i + _VERIFY_BATCH]
        try:
            all_groups.extend(_verify_one_batch(client, batch, model, decisions))
        except Exception as e:                       # keep this batch raw, note why
            err = err or f"error: {type(e).__name__}: {str(e)[:140]}"
            all_groups.extend(batch)
    return _finish(all_groups), (err or "verified")
