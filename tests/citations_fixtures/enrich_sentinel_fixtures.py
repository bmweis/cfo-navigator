"""Frozen fixtures for the citation-tag-investigation generation-path fix
(2026-08, see CLAUDE.md). Written BEFORE generate_tool_description/
generate_tool_agent_taxonomy were wired to the new plain-prose +
trailing-sentinel response contract — these encode the SPEC the
implementation has to satisfy, not a snapshot of what the implementation
happens to produce. tests/test_enrich_sentinel_parsing.py asserts against
these; nothing here imports linklib.enrich.

Each SPLIT_SENTINEL_CASES entry is (raw_text, keys, expected_remaining,
expected_found) for linklib.enrich._split_trailing_sentinels — the
deterministic parsing boundary that replaces json.loads() for these two
fields.

Each AGENT_TAXONOMY_RESPONSES / DESCRIPTION_RESPONSES entry describes one
mocked raw Claude response (a list of text blocks, each optionally citing
one or more sent-document indexes) plus the expected parsed result —
mirroring the (payload_text, cited_document_indexes) shape the existing
_mock_anthropic_citing test helpers already use in
tests/test_agent_taxonomy_enrichment.py / tests/test_description_citations.py,
so the same mocking helper works unmodified against the new contract.
"""

# -- _split_trailing_sentinels ------------------------------------------------

SPLIT_SENTINEL_CASES = [
    {
        "name": "single_key_clean",
        "text": "This is the note.\n\nCONFIDENT: true",
        "keys": ["CONFIDENT"],
        "expected_remaining": "This is the note.",
        "expected_found": {"CONFIDENT": "true"},
    },
    {
        "name": "single_key_no_blank_line",
        # The model is asked for a blank line before the sentinel but the
        # parser must not depend on getting one.
        "text": "This is the note.\nCONFIDENT: false",
        "keys": ["CONFIDENT"],
        "expected_remaining": "This is the note.",
        "expected_found": {"CONFIDENT": "false"},
    },
    {
        "name": "two_keys_expected_order",
        "text": (
            "The full write-up goes here, several sentences.\n\n"
            "SUMMARY: A condensed one-line version.\n\n"
            "CONFIDENT: true"
        ),
        "keys": ["SUMMARY", "CONFIDENT"],
        "expected_remaining": "The full write-up goes here, several sentences.",
        "expected_found": {"SUMMARY": "A condensed one-line version.", "CONFIDENT": "true"},
    },
    {
        "name": "two_keys_reversed_order_still_parses",
        # The model doesn't reliably follow instruction order — the parser
        # must not assume SUMMARY always precedes CONFIDENT.
        "text": (
            "The write-up.\n\n"
            "CONFIDENT: true\n\n"
            "SUMMARY: A condensed version."
        ),
        "keys": ["SUMMARY", "CONFIDENT"],
        "expected_remaining": "The write-up.",
        "expected_found": {"SUMMARY": "A condensed version.", "CONFIDENT": "true"},
    },
    {
        "name": "missing_key_degrades_not_raises",
        # The model forgot CONFIDENT entirely — no exception, key just absent.
        "text": "This is the note with no sentinel at all.",
        "keys": ["CONFIDENT"],
        "expected_remaining": "This is the note with no sentinel at all.",
        "expected_found": {},
    },
    {
        "name": "malformed_value_not_dropped",
        # An unexpected value ("unsure") still gets captured verbatim — the
        # CALLER decides what counts as true (only the literal string
        # "true"), not this generic string-splitting helper.
        "text": "The note.\n\nCONFIDENT: unsure, hard to say",
        "keys": ["CONFIDENT"],
        "expected_remaining": "The note.",
        "expected_found": {"CONFIDENT": "unsure, hard to say"},
    },
    {
        "name": "prose_line_containing_a_colon_is_not_mistaken_for_a_sentinel",
        # A real prose sentence can contain "Word: something" (e.g. a
        # product feature named "Workflow: Approvals") — only a KEY drawn
        # from `keys` may end the scan; an unrecognized "Key: value"-shaped
        # line must NOT be swallowed as if it were a sentinel.
        "text": "It offers a feature called Workflow: Approvals for finance teams.\n\nCONFIDENT: true",
        "keys": ["CONFIDENT"],
        "expected_remaining": "It offers a feature called Workflow: Approvals for finance teams.",
        "expected_found": {"CONFIDENT": "true"},
    },
    {
        "name": "empty_text",
        "text": "",
        "keys": ["CONFIDENT"],
        "expected_remaining": "",
        "expected_found": {},
    },
    {
        "name": "duplicate_key_keeps_first_occurrence_scanning_backward",
        # last (bottom-most) occurrence of a key wins, since the scan is
        # backward from the end of the text and stops re-capturing a key
        # it's already found.
        "text": "Note.\n\nCONFIDENT: false\n\nCONFIDENT: true",
        "keys": ["CONFIDENT"],
        "expected_remaining": "Note.",
        "expected_found": {"CONFIDENT": "true"},
    },
]

# -- generate_tool_agent_taxonomy: full pipeline ------------------------------
# Each case describes the response as the SDK would actually deliver it for
# a document-cited answer: a list of (block_text, cited_document_indexes)
# pairs — the real Citations API splits a response into separate text
# blocks at citation boundaries, so a cited claim and an uncited trailing
# sentinel line arrive as DIFFERENT blocks, never one block carrying both.
# ("blocks": [(body, [0]), (sentinel_tail, [])] — matched against the
# expected parsed AgentTaxonomyResult fields.)

AGENT_TAXONOMY_RESPONSES = [
    {
        "name": "clean_grounded_no_real_citations",
        "blocks": [(
            "Runway uses an AI-assisted scenario modeling feature; no named "
            "agent is described anywhere in the pages provided.\n\n"
            "CONFIDENT: true",
            [],
        )],
        "expected_note": (
            "Runway uses an AI-assisted scenario modeling feature; no named "
            "agent is described anywhere in the pages provided."
        ),
        "expected_confident": True,
        "expected_citation_count": 0,
    },
    {
        "name": "real_citation_fires_and_marker_lands_in_note",
        "blocks": [
            ("The homepage describes a Contract Review Agent that runs "
             "end-to-end without human review.", [0]),
            ("\n\nCONFIDENT: true", []),
        ],
        # inject_markers=True appends "[1]" to the end of the cited span —
        # its own block, distinct from the uncited sentinel tail — matching
        # how the real API actually splits blocks at citation boundaries.
        "expected_note_contains": "[1]",
        "expected_confident": True,
        "expected_citation_count": 1,
    },
    {
        "name": "not_confident_no_signal",
        "blocks": [(
            "The pages provided use generic \"AI-powered\" marketing language "
            "with no concrete agent behavior described.\n\nCONFIDENT: false",
            [],
        )],
        "expected_note": (
            "The pages provided use generic \"AI-powered\" marketing language "
            "with no concrete agent behavior described."
        ),
        "expected_confident": False,
        "expected_citation_count": 0,
    },
    {
        "name": "missing_sentinel_defaults_to_not_confident_but_note_survives",
        # The old json.loads() contract would have raised and returned None
        # here (malformed JSON). The new contract must not: the note is
        # still usable, confident just defaults to False.
        "blocks": [("A thorough note with no CONFIDENT line at the end at all.", [])],
        "expected_note": "A thorough note with no CONFIDENT line at the end at all.",
        "expected_confident": False,
        "expected_citation_count": 0,
    },
    {
        "name": "no_cite_tags_anywhere_in_a_clean_response",
        # The actual regression this whole fix exists for: a clean response
        # under the new contract must never contain the literal pseudo-tag
        # text observed in production (GoClose/Klarity/Expensify/Concourse/
        # Datarails, 2026-08 investigation).
        "blocks": [
            ("Multiple named agents are described: Aura handles reconciliation "
             "end-to-end, and Ember assists close review with a human in the "
             "loop.", [0]),
            ("\n\nCONFIDENT: true", []),
        ],
        "expected_confident": True,
        "expected_citation_count": 1,
        "forbid_substrings": ['cite index=', '<cite', '"confident"', '"summary"'],
    },
]

# -- generate_tool_description: full pipeline (Option A, one call,
# sentinel-delimited SUMMARY + CONFIDENT). Same multi-block shape as above —
# the cited write-up and the uncited SUMMARY/CONFIDENT tail are separate
# blocks, matching real API citation-boundary splitting.

DESCRIPTION_RESPONSES = [
    {
        "name": "clean_grounded",
        "blocks": [
            ("Runway is a financial planning platform built for finance teams "
             "at growth-stage technology companies. It centralizes scenario "
             "modeling, headcount planning, and board reporting in one "
             "workspace.", [0]),
            (
                "\n\nSUMMARY: Runway is an FP&A platform for growth-stage finance teams.\n\n"
                "CONFIDENT: true",
                [],
            ),
        ],
        "expected_description_contains": "financial planning platform",
        "expected_summary": "Runway is an FP&A platform for growth-stage finance teams.",
        "expected_confident": True,
        "expected_citation_count": 1,
    },
    {
        "name": "not_confident_thin_page",
        "blocks": [(
            "Based on general knowledge, this appears to be a spend "
            "management tool for mid-market companies.\n\n"
            "SUMMARY: A spend management tool for mid-market companies.\n\n"
            "CONFIDENT: false",
            [],
        )],
        "expected_description_contains": "spend management",
        "expected_summary": "A spend management tool for mid-market companies.",
        "expected_confident": False,
        "expected_citation_count": 0,
    },
    {
        "name": "missing_summary_and_confident_degrades_gracefully",
        "blocks": [("Just a write-up with no sentinel lines at all, several sentences long.", [])],
        "expected_description_contains": "Just a write-up",
        "expected_summary": "",
        "expected_confident": False,
        "expected_citation_count": 0,
    },
    {
        "name": "no_cite_tags_or_json_braces_in_a_clean_response",
        # generate_tool_description is single-page grounding — only ever one
        # document block (index 0), unlike Agent taxonomy's multi-page crawl.
        "blocks": [
            ("Concourse is an accounts-payable automation platform. It "
             "handles invoice capture, approval routing, and payment "
             "execution for mid-market finance teams.", [0]),
            (
                "\n\nSUMMARY: Concourse automates AP workflows for mid-market finance teams.\n\n"
                "CONFIDENT: true",
                [],
            ),
        ],
        "expected_description_contains": "accounts-payable automation platform",
        "expected_summary": "Concourse automates AP workflows for mid-market finance teams.",
        "expected_confident": True,
        "expected_citation_count": 1,
        "forbid_substrings": ['cite index=', '<cite', '{"description"', '"summary":'],
    },
]
