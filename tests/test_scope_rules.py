"""The enrichment scope rules — what's excluded from the library.

The permanent rule is audience-only: keep written articles useful to operators
(finance leaders, founders, execs) and drop content about pursuing a personal
career in venture capital. The stricter first-time-cleanup exclusions (podcasts,
slide decks, predictions, fund/LP content) have been retired now that the initial
cleanup is done — ongoing queue review is the gate from here on.
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def test_rules_version():
    assert enrich.ENRICH_RULES_VERSION == "v4"


def test_prompt_is_audience_only():
    p = enrich._PROMPT.lower()
    assert "career in venture capital" in p
    # The retired cleanup exclusions are gone from the prompt entirely.
    assert "podcast" not in p and "predictions" not in p


def test_cleanup_mechanism_removed():
    # The first-time cleanup toggle is gone: no exclusions constant, and enrich()
    # no longer takes a cleanup_mode flag.
    assert not hasattr(enrich, "_CLEANUP_EXCLUSIONS")
    assert "cleanup_mode" not in inspect.signature(enrich.enrich).parameters
