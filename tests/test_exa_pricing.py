"""Phase 1 cost-ledger wiring for a future Exa retrieval source (linklib/pricing.py).

No retrieval code calls Exa yet — this only covers compute_exa_cost() so the
cost-capture plumbing is verified before any caller depends on it.
"""
import pytest

from linklib import pricing


def test_compute_exa_cost_within_included_results():
    # A 10-result Search call stays within the $7/1k base tier: no overage.
    cost = pricing.compute_exa_cost("search", num_results=10)
    assert cost == pytest.approx(0.007)


def test_compute_exa_cost_zero_results_still_charges_base():
    # Exa bills the base request fee even when a search returns nothing.
    cost = pricing.compute_exa_cost("search", num_results=0)
    assert cost == pytest.approx(0.007)


def test_compute_exa_cost_overage_above_included_results():
    # 15 results = 10 included + 5 over, at $1/1k per extra result.
    cost = pricing.compute_exa_cost("search", num_results=15)
    assert cost == pytest.approx(0.007 + 5 * 0.001)


def test_compute_exa_cost_unknown_endpoint_falls_back_not_zero():
    cost = pricing.compute_exa_cost("some-future-endpoint", num_results=10)
    assert cost > 0
