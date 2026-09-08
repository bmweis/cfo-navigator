"""Freshness-Banner & Reviewed-Toggle Consolidation (2026-09) — direct
coverage for the two shared helpers this refactor introduced:
`webapp.app._reviewed_freshness_banner` (the mechanical wrapper the three
/admin/checks freshness banners share) and
`webapp.app._reviewed_toggle_html` (the badge+action pair shared by
community-gaps, ask-feedback, and compare-summary-feedback).

Every pre-existing test for the three banners and the three toggle call
sites (tests/test_pricing_freshness.py, tests/test_models_freshness.py,
tests/test_exa_pricing_freshness.py, tests/test_ask_feedback.py,
tests/test_community_profiles.py, tests/test_compare_summary.py) passes
unmodified against this refactor — proof the consolidation is
behavior-identical, not just that these two helpers work in isolation.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import webapp.app as appmod


def test_reviewed_freshness_banner_amber_when_stale():
    html = appmod._reviewed_freshness_banner(True, "some message", "/admin/checks/mark-x")
    assert "#fef3c7" in html  # amber wash
    assert "some message" in html
    assert 'action="/admin/checks/mark-x"' in html
    assert "Mark reviewed" in html


def test_reviewed_freshness_banner_seafoam_when_fresh():
    html = appmod._reviewed_freshness_banner(False, "all good", "/admin/checks/mark-y")
    assert "var(--seafoam-wash)" in html
    assert "#fef3c7" not in html
    assert "all good" in html


def test_pricing_banner_no_longer_claims_openai_coverage():
    """The corrected-copy half of this PR: MODEL_PRICING is Claude-only, so
    the pricing banner must never claim "(and OpenAI's)" coverage."""
    stale_html = appmod._pricing_freshness_banner("")
    assert "OpenAI" not in stale_html
    fresh_html = appmod._pricing_freshness_banner("2026-01-01T00:00:00+00:00")
    assert "OpenAI" not in fresh_html


def test_reviewed_toggle_html_two_way_reviewed_state():
    badge, action = appmod._reviewed_toggle_html(True, "/toggle/1")
    assert "Reviewed" in badge
    assert "New" not in badge
    assert 'action="/toggle/1"' in action
    assert "Mark unreviewed" in action


def test_reviewed_toggle_html_two_way_unreviewed_state():
    badge, action = appmod._reviewed_toggle_html(False, "/toggle/1")
    assert "New" in badge
    assert 'action="/toggle/1"' in action
    assert "Mark reviewed" in action
    assert "Mark unreviewed" not in action


def test_reviewed_toggle_html_two_way_form_style_applied():
    _, action = appmod._reviewed_toggle_html(False, "/toggle/1", form_style="margin-left:auto;")
    assert 'style="margin-left:auto;"' in action


def test_reviewed_toggle_html_one_way_unreviewed_shows_button_no_badge():
    badge, action = appmod._reviewed_toggle_html(False, "/mark/1", one_way=True)
    assert badge == ""
    assert "Mark reviewed" in action
    assert "<form" in action


def test_reviewed_toggle_html_one_way_reviewed_shows_plain_text_no_button():
    badge, action = appmod._reviewed_toggle_html(
        True, "/mark/1", one_way=True, reviewed_at="2026-09-01T12:00:00",
    )
    assert badge == ""
    assert "<form" not in action
    assert "Reviewed" in action
    assert "2026-09-01" in action


def test_reviewed_toggle_html_one_way_reviewed_without_date_still_renders():
    badge, action = appmod._reviewed_toggle_html(True, "/mark/1", one_way=True)
    assert badge == ""
    assert "Reviewed" in action
    assert "<form" not in action
