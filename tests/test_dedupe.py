"""Near-duplicate detection (linklib/dedupe.py).

Catches reworded reruns within a date window — what exact-URL dedup misses.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import dedupe as dd


def _a(title, summary="", date="2025-03-01"):
    return {"id": id(title), "title": title, "summary": summary, "published_at": date, "url": title}


def _ai(aid, title, summary="", date="2025-03-01"):
    return {"id": aid, "title": title, "summary": summary, "published_at": date, "url": f"u{aid}"}


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


def test_hiring_advice_distinct_roles_not_clustered():
    # All four are distinct hires: an AE, a first finance hire, and two CFO posts
    # (one a guest cross-post). None should be flagged as duplicates.
    arts = [
        _a("Dear SaaStr: When Should I Hire My First AE? | SaaStrAI", date="2025-03-21"),
        _a("Dear SaaStr: When Should You Make Your First Finance Hire? | SaaStrAI", date="2025-03-11"),
        _a("When to Hire Your First CFO — From OnlyCFO | SaaStrAI", date="2025-03-07"),
        _a("Dear SaaStr: When Should I Hire a CFO? | SaaStrAI", date="2025-01-02"),
    ]
    clusters = dd.find_clusters(arts, source="SaaStr")
    assert clusters == []   # keep all four


def test_hiring_finance_hire_not_dup_of_cfo():
    a = _a("Dear SaaStr: When Should You Make Your First Finance Hire? | SaaStrAI", date="2025-03-11")
    b = _a("Dear SaaStr: When Should I Hire a CFO? | SaaStrAI", date="2025-01-02")
    assert not dd.is_near_dup(a, b, source="SaaStr")


def test_hiring_guest_crosspost_not_dup_of_native_column():
    # Same role (CFO) but one is a "— From OnlyCFO" guest cross-post -> distinct.
    a = _a("When to Hire Your First CFO — From OnlyCFO | SaaStrAI", date="2025-03-07")
    b = _a("Dear SaaStr: When Should I Hire a CFO? | SaaStrAI", date="2025-01-02")
    assert not dd.is_near_dup(a, b, source="SaaStr")


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


# --- keeper selection -------------------------------------------------------

def test_keeper_prefers_non_dear_over_newer_dear():
    # Even though the "Dear SaaStr" post is newer, the original article is kept.
    dear = _a("Dear SaaStr: How Should I Build Our First Sales Comp Plan? | SaaStrAI", date="2025-06-25")
    orig = _a("8 Top Tips to Building Your Very First Sales Comp Plan | SaaStrAI", date="2025-04-14")
    ordered = dd._rekey_cluster([dear, orig], source="SaaStr")
    assert ordered[0] is orig                     # non-Dear kept
    assert "_dup_score" in ordered[1]             # the Dear post is the dup
    assert "_dup_score" not in ordered[0]


def test_keeper_falls_back_to_newest_when_all_dear():
    older = _a("Dear SaaStr: How Do I Raise a Series A? | SaaStrAI", date="2025-01-01")
    newer = _a("Dear SaaStr: How Should I Raise My Series A? | SaaStrAI", date="2025-03-01")
    ordered = dd._rekey_cluster([older, newer], source="SaaStr")
    assert ordered[0] is newer                    # newest among all-Dear


# --- Claude verification pass ----------------------------------------------

class _FakeResp:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]


def _fake_anthropic(groups_json, monkeypatch):
    """Install a fake anthropic.Anthropic whose messages.create returns groups_json."""
    import sys, types
    mod = types.ModuleType("anthropic")

    class _Client:
        def __init__(self, *a, **k):
            self.messages = self
        def create(self, *a, **k):
            return _FakeResp(groups_json)

    mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


def test_verify_drops_false_positive_cluster(monkeypatch):
    # The filter grouped a real dupe pair with a look-alike; Claude keeps only the pair.
    cluster = [
        _ai(1, "Dear SaaStr: How Can I Become a Better VP of Sales?", date="2025-03-06"),
        _ai(2, "Dear SaaStr: How Do I Become a Great Sales Rep?", date="2025-02-01"),
    ]
    _fake_anthropic('{"groups": []}', monkeypatch)   # Claude says: not dupes
    out, status = dd.verify_clusters([cluster], source="SaaStr")
    assert out == [] and status == "verified"


def test_verify_confirms_and_rekeys(monkeypatch):
    # Same talk retitled: Claude confirms; keeper is the non-Dear original.
    a = _ai(10, "How to Build a World-Class CS Machine: Lessons from the CRO of Notion", date="2025-08-18")
    b = _ai(11, "Dear SaaStr: The 10% Rule — Invest in CS, With the CRO of Notion", date="2025-08-21")
    _fake_anthropic('{"groups": [["10", "11"]]}', monkeypatch)
    out, status = dd.verify_clusters([[b, a]], source="SaaStr")
    assert len(out) == 1 and len(out[0]) == 2
    assert out[0][0] is a                          # non-Dear original kept
    assert status == "verified"


def test_verify_degrades_without_api(monkeypatch):
    # No API key -> candidates returned unchanged, keeper still re-selected, and
    # the status reports the reason so the UI can stop claiming "verified".
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dear = _ai(20, "Dear SaaStr: How Should I Build Our First Sales Comp Plan?", date="2025-06-25")
    orig = _ai(21, "8 Top Tips to Building Your Very First Sales Comp Plan", date="2025-04-14")
    out, status = dd.verify_clusters([[dear, orig]], source="SaaStr")
    assert len(out) == 1
    assert out[0][0] is orig                       # keeper rule applied offline
    assert status == "no_key"


def test_verify_reports_error_status_on_api_failure(monkeypatch):
    # If the Claude call throws, the batch is kept raw and the status says so.
    import sys, types
    mod = types.ModuleType("anthropic")

    class _Client:
        def __init__(self, *a, **k):
            self.messages = self
        def create(self, *a, **k):
            raise RuntimeError("boom")

    mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cluster = [_ai(1, "A reworded title", date="2025-03-01"),
               _ai(2, "A reworded title v2", date="2025-02-01")]
    out, status = dd.verify_clusters([cluster], source="SaaStr")
    assert len(out) == 1                            # kept raw, not silently dropped
    assert status.startswith("error")
