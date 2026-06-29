"""Near-duplicate detection (linklib/dedupe.py).

Catches reworded reruns within a date window — what exact-URL dedup misses.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import dedupe as dd


def _a(title, summary="", date="2025-03-01"):
    return {"id": id(title), "title": title, "summary": summary, "published_at": date, "url": title}


def test_similar_titles_flagged():
    a = _a("The 5 Metrics Every SaaS CFO Must Track | SaaStr")
    b = _a("5 Metrics Every SaaS CFO Should Track")
    assert dd.is_near_dup(a, b, source="SaaStr")


def test_different_topics_not_flagged():
    a = _a("How to forecast SaaS revenue")
    b = _a("Hiring your first VP of Sales")
    assert not dd.is_near_dup(a, b)


def test_date_window_excludes_far_apart():
    a = _a("The 5 Metrics Every SaaS CFO Must Track", date="2025-01-01")
    b = _a("The 5 Metrics Every SaaS CFO Must Track", date="2025-09-01")  # 8 months apart
    assert not dd.is_near_dup(a, b, days=90)
    assert dd.is_near_dup(a, b, days=365)   # widen the window -> caught


def test_summary_overlap_catches_reworded_title():
    a = _a("Why NRR is the metric that matters",
           summary="net revenue retention drives valuation; benchmarks by stage; expansion vs churn")
    b = _a("The one number investors care about",
           summary="net revenue retention drives valuation benchmarks by stage expansion versus churn")
    assert dd.is_near_dup(a, b, source="SaaStr")


def test_find_clusters_groups_dupes():
    arts = [
        _a("5 SaaS metrics every CFO must track", date="2025-03-01"),
        _a("The 5 SaaS metrics every CFO should track", date="2025-02-15"),
        _a("Five metrics every SaaS CFO tracks", date="2025-01-20"),
        _a("How to hire a great CFO", date="2025-03-02"),   # unrelated, stands alone
    ]
    clusters = dd.find_clusters(arts, source="SaaStr")
    assert len(clusters) == 1
    assert len(clusters[0]) == 3
    # newest first -> the one to keep
    assert clusters[0][0]["published_at"] == "2025-03-01"


def test_is_dup_of_any():
    existing = [_a("Scaling your finance team", date="2025-03-01")]
    cand = _a("How to scale your finance team", date="2025-03-10")
    assert dd.is_dup_of_any(cand, existing, source="SaaStr")
    assert not dd.is_dup_of_any(_a("Unrelated pricing strategy piece"), existing)
