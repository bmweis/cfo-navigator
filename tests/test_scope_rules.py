"""The enrichment scope rules (v3) — what gets excluded from the library.

Pins that the prompt instructs the model to drop podcasts/webinars, slide decks,
and annual-predictions roundups, so the sweep skips them and a re-enrich flags
already-saved ones for removal review.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def test_rules_version_bumped():
    assert enrich.ENRICH_RULES_VERSION == "v4"


def test_prompt_excludes_recordings_slides_predictions():
    p = enrich._PROMPT.lower()
    assert "podcast" in p and "webinar" in p
    assert "slide deck" in p or "slides" in p
    assert "predictions" in p
    assert "20vc" in p          # the concrete example the user hit (podcast on SaaStr)


def test_prompt_excludes_fund_and_lp_content():
    # Content for fund managers / GPs / LPs (the Carta case) is excluded...
    p = enrich._PROMPT.lower()
    assert "fund managers" in p or "gps" in p
    assert "lp" in p


def test_prompt_still_keeps_operator_vc_content():
    # ...but operator fundraising content (deal with investors) is kept.
    p = enrich._PROMPT.lower()
    assert "fundraising" in p and "term sheets" in p
    assert "raising a round" in p
