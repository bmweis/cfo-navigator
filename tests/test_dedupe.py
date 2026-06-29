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


def test_interesting_learnings_same_company_milestone_flagged():
    a = _a("5 Interesting Learnings about Snowflake at $1B in ARR | SaaStr", date="2025-03-01")
    b = _a("Interesting Learnings from Snowflake at $1 Billion in ARR", date="2025-02-20")
    assert dd.is_near_dup(a, b, source="SaaStr")


def test_interesting_learnings_different_milestone_not_flagged():
    # Same company, DIFFERENT revenue milestone -> not a duplicate.
    a = _a("5 Interesting Learnings about Snowflake at $100M ARR | SaaStr", date="2025-03-01")
    b = _a("5 Interesting Learnings about Snowflake at $1B ARR | SaaStr", date="2025-02-20")
    assert not dd.is_near_dup(a, b, source="SaaStr")


def test_first_hire_same_role_flagged():
    a = _a("Hiring Your First VP of Sales | SaaStr", date="2025-03-01")
    b = _a("Your First VP of Sales Hire | SaaStr", date="2025-02-15")
    assert dd.is_near_dup(a, b, source="SaaStr")


def test_first_hire_different_role_not_flagged():
    a = _a("Your First VP of Sales Hire | SaaStr", date="2025-03-01")
    b = _a("Your First VP of Marketing Hire | SaaStr", date="2025-02-15")
    assert not dd.is_near_dup(a, b, source="SaaStr")


def test_first_hire_different_level_not_flagged():
    a = _a("Your First VP of Sales Hire | SaaStr", date="2025-03-01")
    b = _a("Your First Head of Sales Hire | SaaStr", date="2025-02-15")
    assert not dd.is_near_dup(a, b, source="SaaStr")


def test_first_template_different_object_not_flagged():
    # The screenshot case: same "build your first sales ___" template, different
    # object — a comp plan vs a team — must NOT be flagged as duplicates.
    a = _a("8 Top Tips to Building Your Very First Sales Comp Plan | SaaStr", date="2025-06-25")
    b = _a("Dear SaaStr: What Are Your Top Tips to Building Your First Sales Team? | SaaStr",
           date="2025-04-14")
    assert not dd.is_near_dup(a, b, source="SaaStr")


def test_first_template_same_object_flagged():
    # Same object (comp plan), reworded -> still a duplicate.
    a = _a("8 Top Tips to Building Your Very First Sales Comp Plan | SaaStr", date="2025-06-25")
    b = _a("Dear SaaStr: How Should I Build Our First Sales Comp Plan? | SaaStr", date="2025-04-14")
    assert dd.is_near_dup(a, b, source="SaaStr")


def test_screenshot_cluster_splits_correctly():
    # The full 3-item cluster from the screenshot: the comp-plan pair should
    # cluster, the sales-team post should fall out as its own.
    arts = [
        _a("8 Top Tips to Building Your Very First Sales Comp Plan | SaaStr", date="2025-06-25"),
        _a("Dear SaaStr: How Should I Build Our First Sales Comp Plan? | SaaStr", date="2025-04-14"),
        _a("Dear SaaStr: What Are Your Top Tips to Building Your First Sales Team? | SaaStr",
           date="2025-04-14"),
    ]
    clusters = dd.find_clusters(arts, source="SaaStr")
    # only the two comp-plan posts cluster; the sales-team post stands alone
    assert len(clusters) == 1
    assert len(clusters[0]) == 2
    titles = {x["title"] for x in clusters[0]}
    assert all("Comp Plan" in t for t in titles)
