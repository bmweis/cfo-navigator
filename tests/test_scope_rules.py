"""The enrichment scope rules — retired (PR 4, "Remove content" retirement,
2026-09).

Historically this file asserted a permanent audience-only exclusion rule
(drop content about pursuing a personal career in venture capital) baked
into the enrichment prompt, on top of an already-retired first-time-cleanup
toggle. The audience-scope judgment itself is now gone too: enrich() no
longer asks Claude to judge in/out of scope at all, `articles.in_scope` is
frozen at 1 for every future article, and `/admin/library/review-removals`
(the page that showed flagged articles for a human to keep or remove) was
removed outright — see linklib/db.py's articles.in_scope column comment and
CLAUDE.md's "'Remove content' retired" bullet for the full write-up.
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def test_rules_version():
    assert enrich.ENRICH_RULES_VERSION == "v5"


def test_prompt_has_no_audience_scope_judgment():
    p = enrich._PROMPT.lower()
    # The retired audience-scope exclusion rule and the even-earlier retired
    # cleanup exclusions are both gone from the prompt entirely.
    assert "career in venture capital" not in p
    assert "in_scope" not in p
    assert "scope_reason" not in p
    assert "podcast" not in p and "predictions" not in p


def test_enrichment_dataclass_has_no_scope_fields():
    # in_scope/scope_reason were dropped from the Enrichment dataclass
    # entirely, not just left unpopulated.
    fields = {f.name for f in enrich.Enrichment.__dataclass_fields__.values()}
    assert "in_scope" not in fields
    assert "scope_reason" not in fields


def test_cleanup_mechanism_removed():
    # The first-time cleanup toggle is gone: no exclusions constant, and enrich()
    # no longer takes a cleanup_mode flag.
    assert not hasattr(enrich, "_CLEANUP_EXCLUSIONS")
    assert "cleanup_mode" not in inspect.signature(enrich.enrich).parameters
