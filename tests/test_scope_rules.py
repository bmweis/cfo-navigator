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
    assert enrich.ENRICH_RULES_VERSION == "v3"


def test_prompt_excludes_recordings_slides_predictions():
    p = enrich._PROMPT.lower()
    assert "podcast" in p and "webinar" in p
    assert "slide deck" in p or "slides" in p
    assert "predictions" in p
    assert "20vc" in p          # the concrete example the user hit (podcast on SaaStr)


def test_prompt_still_keeps_operator_vc_content():
    # We exclude career-in-VC and recordings, but keep operator fundraising content.
    p = enrich._PROMPT.lower()
    assert "fundraising" in p and "term sheets" in p
