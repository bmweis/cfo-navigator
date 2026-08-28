"""Frozen fixtures for generate_tool_differentiation's content-exclusion
rules follow-up (2026-08, see CLAUDE.md and generate_tool_differentiation's
own docstring). Written to encode the spec the prompt rules have to
satisfy, mirroring the citation-tag investigation's own two-sided fixture
pattern (tests/citations_fixtures/enrich_sentinel_fixtures.py):

1. A "clean" case — the mocked response is what a model COMPLYING with the
   new rules would produce, even when the input `description` fed into the
   prompt itself carries a vendor-reported stat (a legacy, not-yet-
   regenerated description predating the Description-side fix). Confirms
   the pipeline stores exactly what a compliant model returns, with no
   forbidden marketing substrings.
2. An "old bug reproduction" case — the mocked response is the ACTUAL
   observed production shape (a vendor-reported stat inside the JSON
   `competitive_differentiation` value), fed through the CURRENT (post-fix)
   generate_tool_differentiation. Unlike Description/Agent taxonomy, this
   field's output is still parsed via plain json.loads (no citations, no
   sentinel-line contract) — there is no code-level filter anywhere in
   that path that recognizes or strips a vendor stat the model decided to
   include anyway. This case exists specifically to keep that limitation
   visible and tested, not silently assumed away: the fix is preventative
   (the new prompt rules), not corrective.
"""

# The actual observed production shape (2026-08 blast-radius spot-check):
# a sampled Differentiation output for Scale AI included a vendor-reported
# result attributed to a named customer role.
VENDOR_STAT_TEXT = (
    "Scale AI's CAO reports closing two to three days faster at over "
    "98% automation"
)

DIFFERENTIATION_RESPONSES = [
    {
        "name": "clean_compliant_response_despite_legacy_description_carrying_a_stat",
        # The description passed INTO the prompt still carries the vendor
        # stat (a legacy, not-yet-regenerated field) — rule 5 explicitly
        # tells the model not to repeat it even when it's right there in
        # the description below. This fixture models a model that complied.
        "description": (
            f"Scale AI provides data labeling and evaluation infrastructure "
            f"for AI teams. {VENDOR_STAT_TEXT}, according to the vendor."
        ),
        "competitor_names": ["Labelbox", "Surge AI"],
        "raw_response": (
            '{"competitive_differentiation": '
            '"Best for AI teams that need large-scale human-in-the-loop '
            'labeling; the trade-off is less depth on pure model evaluation '
            'than a narrower specialist like Surge AI.", '
            '"confident": true}'
        ),
        "expected_differentiation": (
            "Best for AI teams that need large-scale human-in-the-loop "
            "labeling; the trade-off is less depth on pure model evaluation "
            "than a narrower specialist like Surge AI."
        ),
        "expected_confident": True,
        "forbid_substrings": ["98%", "two to three days faster", "CAO reports"],
    },
    {
        "name": "old_bug_shape_still_leaks_if_the_model_reverts",
        # Reproduction of the actual observed bug: the model ignores rule 5
        # and bakes the vendor stat straight into the JSON value anyway.
        # json.loads() has no way to tell this apart from a legitimate
        # comparison claim, so it leaks through unfiltered — the same
        # disclosed, tested limitation
        # test_old_bug_shape_still_leaks_if_the_model_reverts_to_it
        # documents for Description/Agent taxonomy.
        "description": "Scale AI provides data labeling and evaluation infrastructure for AI teams.",
        "competitor_names": ["Labelbox", "Surge AI"],
        "raw_response": (
            '{"competitive_differentiation": '
            f'"Best for AI teams at scale; {VENDOR_STAT_TEXT}, so the '
            'trade-off is mostly about cost.", '
            '"confident": true}'
        ),
        "expected_leaked_substrings": ["98%", "two to three days faster", "CAO reports"],
    },
]
