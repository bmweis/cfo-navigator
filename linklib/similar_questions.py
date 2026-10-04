"""Text-only similar-question matching for FP&A Buddy (no embeddings, no model
call). Token-set Dice coefficient over lowercased words with a small stopword
list; a suggestion needs a score of at least THRESHOLD. Dice 0.7 was chosen over
0.4 (more false suggestions) and Jaccard 0.7 (misses almost every paraphrase);
it still misses most paraphrases, which is why the UI always offers "Ask anyway"."""
from __future__ import annotations

import re

THRESHOLD = 0.7
MAX_SUGGESTIONS = 3
_STOP = frozenset("""a an the of for to in on at by with and or is are be do does did how what
should we our us you your i my it its this that these those can could would will""".split())
_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall((text or "").lower()) if w not in _STOP)


def dice(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def rank_similar(question: str, candidates: list[dict]) -> list[dict]:
    """Candidates scoring >= THRESHOLD, thumbs-up-rated first, then by score,
    then newest (candidates arrive newest first). At most MAX_SUGGESTIONS.
    Each result carries its `score`."""
    q = tokens(question)
    scored = []
    for i, c in enumerate(candidates):
        s = dice(q, tokens(c.get("question") or ""))
        if s >= THRESHOLD:
            scored.append((0 if int(c.get("helpful_count") or 0) else 1, -s, i, c, s))
    scored.sort(key=lambda t: t[:3])
    return [dict(c, score=round(s, 3)) for _, _, _, c, s in scored[:MAX_SUGGESTIONS]]
