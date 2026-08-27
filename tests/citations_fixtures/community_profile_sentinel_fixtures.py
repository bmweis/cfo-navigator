"""Frozen fixtures for the Community profile citation fix (2026-08, see
CLAUDE.md). Written BEFORE generate_community_profile was wired to this
contract — these encode the spec the implementation has to satisfy.

Community profile's response is 23 fields (12 narrative + confidence-
tracked, founded_year, 10 short factual/categorical) plus a nested
CONFIDENCE: block with 12 sub-keys — a materially different parsing shape
from the two-field Description/Agent taxonomy fix, since several fields
are genuinely multi-sentence prose that can't be forced onto one line.

_parse_labeled_blocks(text, keys, terminal_key=None) is the new generic
helper: forward scan, order-tolerant, a recognized "KEY:" header opens a
new block and everything until the next recognized header (or end of
text) is that block's body. `terminal_key`, when given, is a header that
— once matched — ends header recognition entirely: everything from there
to the end of text becomes that key's raw body, verbatim, with no further
scanning. This is NOT a convenience — it's the only thing that prevents a
line like "IDEAL_MEMBER: true" inside CONFIDENCE:'s own body (12 lines,
one per confidence sub-key, sharing names with 12 of the 23 top-level
fields) from being mistaken for a fresh top-level `ideal_member` header
and silently corrupting the real narrative text parsed earlier. Community
profile calls this with `terminal_key="confidence"` — CONFIDENCE: is
always the prompt's last requested section.
"""

# -- _parse_labeled_blocks -----------------------------------------------

PARSE_LABELED_BLOCKS_CASES = [
    {
        "name": "two_fields_forward_order",
        "text": (
            "IDEAL_MEMBER:\nVP Finance and above at growth-stage SaaS companies.\n\n"
            "ANTI_FIT:\nEarly-career finance managers."
        ),
        "keys": ["ideal_member", "anti_fit"],
        "terminal_key": None,
        "expected": {
            "ideal_member": "VP Finance and above at growth-stage SaaS companies.",
            "anti_fit": "Early-career finance managers.",
        },
    },
    {
        "name": "multi_sentence_body_preserved_including_internal_newlines",
        "text": (
            "VALUE_PROP:\nPeer benchmarking data updated quarterly.\n"
            "Direct introductions to vetted vendors.\nA private, curated Slack.\n\n"
            "FOUNDED_YEAR:\n2019"
        ),
        "keys": ["value_prop", "founded_year"],
        "terminal_key": None,
        "expected": {
            "value_prop": (
                "Peer benchmarking data updated quarterly.\n"
                "Direct introductions to vetted vendors.\nA private, curated Slack."
            ),
            "founded_year": "2019",
        },
    },
    {
        "name": "reversed_order_still_parses",
        "text": "ANTI_FIT:\nSolo founders.\n\nIDEAL_MEMBER:\nSeries B+ CFOs.",
        "keys": ["ideal_member", "anti_fit"],
        "terminal_key": None,
        "expected": {"ideal_member": "Series B+ CFOs.", "anti_fit": "Solo founders."},
    },
    {
        "name": "missing_key_absent_not_raised",
        "text": "IDEAL_MEMBER:\nSeries B+ CFOs.",
        "keys": ["ideal_member", "anti_fit"],
        "terminal_key": None,
        "expected": {"ideal_member": "Series B+ CFOs."},   # "anti_fit" simply absent
    },
    {
        "name": "prose_line_with_a_colon_not_mistaken_for_a_header",
        # A real sentence naming a feature ("Format: Slack-based") must not
        # be mistaken for a recognized header just because it has a colon —
        # only a line that IS one of the given keys counts.
        "text": "PLATFORM_TYPE:\nFormat: Slack-based, with quarterly in-person meetups.",
        "keys": ["platform_type"],
        "terminal_key": None,
        "expected": {"platform_type": "Format: Slack-based, with quarterly in-person meetups."},
    },
    {
        "name": "terminal_key_stops_header_scanning_entirely",
        # The actual data-integrity case Brian asked to see proven, not just
        # present: CONFIDENCE:'s own body contains "IDEAL_MEMBER: true" — a
        # line that, if header-scanning continued, would be mistaken for a
        # fresh ideal_member header and would corrupt/reopen it. With
        # terminal_key="confidence", scanning stops the moment CONFIDENCE:
        # is matched, so everything after it (including that
        # IDEAL_MEMBER-shaped line) is preserved verbatim as confidence's
        # own body — never re-parsed as a header.
        "text": (
            "IDEAL_MEMBER:\nSeries B+ CFOs at SaaS companies.\n\n"
            "ANTI_FIT:\nSolo founders.\n\n"
            "CONFIDENCE:\nIDEAL_MEMBER: true\nANTI_FIT: false"
        ),
        "keys": ["ideal_member", "anti_fit", "confidence"],
        "terminal_key": "confidence",
        "expected": {
            "ideal_member": "Series B+ CFOs at SaaS companies.",   # UNCORRUPTED — proven, not assumed
            "anti_fit": "Solo founders.",                          # UNCORRUPTED
            "confidence": "IDEAL_MEMBER: true\nANTI_FIT: false",   # verbatim, not re-parsed
        },
    },
    {
        "name": "terminal_key_with_nothing_before_it",
        "text": "CONFIDENCE:\nIDEAL_MEMBER: true\nVALUE_PROP: false",
        "keys": ["ideal_member", "value_prop", "confidence"],
        "terminal_key": "confidence",
        "expected": {"confidence": "IDEAL_MEMBER: true\nVALUE_PROP: false"},
    },
    {
        "name": "empty_text",
        "text": "",
        "keys": ["ideal_member"],
        "terminal_key": None,
        "expected": {},
    },
]

# -- generate_community_profile: full pipeline ----------------------------
# Each case describes the response as separate SDK text blocks (matching
# real citation-boundary splitting — a cited claim and uncited surrounding
# structure/headers arrive as different blocks), matched against the
# expected parsed CommunityProfileDraft fields. Only a subset of the 23
# fields is exercised per case — deliberately: the parser has to work
# whether or not every field is populated the same way production drafts
# won't always name every field identically.

_CLEAN_RESPONSE_BLOCKS = [
    ("IDEAL_MEMBER:\nVP Finance and above at growth-stage SaaS companies.", [0]),
    (
        "\n\nANTI_FIT:\nEarly-career finance managers — the discussion assumes a seat "
        "at the table already.\n\n"
        "VALUE_PROP:\nPeer benchmarking data and direct vendor introductions.\n\n"
        "FORMAT_REALITY:\nMonthly virtual roundtables, one annual in-person summit.\n\n"
        "ENGAGEMENT_LEVEL:\nHigh in the first quarter, tapers to occasional after.\n\n"
        "SPONSOR_RELATIONSHIP_NOTE:\nSponsors present but clearly value-add, not salesy.\n\n"
        "BUSINESS_MODEL:\nDues-funded, gated peer group.\n\n"
        "APPLICATION_FRICTION:\nLight vetting, most qualified applicants get in within a week.\n\n"
        "COST_VALUE_VERDICT:\nWorth it for the network alone.\n\n"
        "NOTABLE_MEMBERS:\nNone publicly reported.\n\n"
        "FOUNDED_YEAR:\n2019\n\n"
        "PUBLIC_CRITICISM:\nNone reported.\n\n"
        "VERDICT_SUMMARY:\nBest for growth-stage operator CFOs, not for public-company controllers.\n\n"
        "STAGE_FOCUS:\nGrowth-stage\n\n"
        "JOBS_PROGRAM:\nNo\n\n"
        "TEAM_OR_INDIVIDUAL:\nIndividual\n\n"
        "SENIORITY_BAND:\nCFO and VP Finance only\n\n"
        "PRIMARY_PURPOSE:\nPeer learning\n\n"
        "RESOURCES_INCLUDED:\nTemplates, benchmarking data\n\n"
        "PLATFORM_TYPE:\nSlack\n\n"
        "MEETING_FORMAT:\nVirtual\n\n"
        "EVENT_STYLE:\nIntimate small-group\n\n"
        "CPE_ELIGIBLE:\nNo\n\n"
        "CONFIDENCE:\n"
        "IDEAL_MEMBER: true\nANTI_FIT: true\nVALUE_PROP: true\nBUSINESS_MODEL: true\n"
        "FORMAT_REALITY: true\nENGAGEMENT_LEVEL: false\nSPONSOR_RELATIONSHIP_NOTE: true\n"
        "APPLICATION_FRICTION: true\nCOST_VALUE_VERDICT: true\nNOTABLE_MEMBERS: false\n"
        "PUBLIC_CRITICISM: false\nVERDICT_SUMMARY: true",
        [],
    ),
]

COMMUNITY_PROFILE_RESPONSES = [
    {
        "name": "clean_grounded_full_draft",
        "blocks": _CLEAN_RESPONSE_BLOCKS,
        "expected_fields": {
            "anti_fit": "Early-career finance managers — the discussion assumes a seat at the table already.",
            "value_prop": "Peer benchmarking data and direct vendor introductions.",
            "founded_year": 2019,
            "verdict_summary": "Best for growth-stage operator CFOs, not for public-company controllers.",
            "cpe_eligible": "No",
        },
        # inject_markers=True — the cited span (block 0, "IDEAL_MEMBER:...")
        # gets a real [n] marker; this is the field that citation lands in.
        "expected_ideal_member_contains": "[1]",
        "expected_confidence": {
            "ideal_member": True, "anti_fit": True, "value_prop": True, "business_model": True,
            "format_reality": True, "engagement_level": False, "sponsor_relationship_note": True,
            "application_friction": True, "cost_value_verdict": True, "notable_members": False,
            "public_criticism": False, "verdict_summary": True,
        },
        "expected_citation_count": 1,
    },
    {
        "name": "missing_confidence_block_defaults_all_false",
        "blocks": [(
            "IDEAL_MEMBER:\nSeed-stage operator CFOs.\n\n"
            "VERDICT_SUMMARY:\nBest for seed-stage CFOs.",
            [],
        )],
        "expected_fields": {"verdict_summary": "Best for seed-stage CFOs."},
        "expected_confidence": {f: False for f in [
            "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
            "engagement_level", "sponsor_relationship_note", "application_friction",
            "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary",
        ]},
        "expected_citation_count": 0,
    },
    {
        "name": "founded_year_non_numeric_coerces_to_none",
        "blocks": [(
            "IDEAL_MEMBER:\nSeed-stage operator CFOs.\n\nFOUNDED_YEAR:\nUnclear from the page.",
            [],
        )],
        "expected_fields": {"founded_year": None},
        "expected_confidence": {},   # not asserted field-by-field for this case
        "expected_citation_count": 0,
    },
    {
        "name": "no_cite_tags_or_json_braces_in_a_clean_response",
        "blocks": [
            ("IDEAL_MEMBER:\nSeed-stage operator CFOs at SaaS companies.", [0]),
            (
                "\n\nVERDICT_SUMMARY:\nBest for seed-stage operator CFOs.\n\n"
                "CONFIDENCE:\nIDEAL_MEMBER: true\nVERDICT_SUMMARY: true",
                [],
            ),
        ],
        "expected_fields": {"verdict_summary": "Best for seed-stage operator CFOs."},
        "expected_confidence": {},
        "expected_citation_count": 1,
        "forbid_substrings": ['cite index=', '<cite', '{"ideal_member"', '"confidence":'],
    },
]

# -- reproduction of the vulnerable shape (2026-08 investigation) --------
# Community profile was never actually exercised by the throwaway script's
# production run (communities were untouched), so there's no real captured
# production sample the way Datarails' Agent taxonomy text was. This
# fixture instead mirrors the SAME failure pattern the investigation found
# elsewhere: a JSON blob with a literal pseudo-citation tag embedded in one
# field's string value, run through the CURRENT (post-fix) code — same
# disclosure as the merged fix's OLD_BUG_REPRODUCTION_CASE: this is not a
# corrective filter, so this shape still leaks if the model reverts to it.
COMMUNITY_OLD_BUG_REPRODUCTION_CASE = {
    "name": "old_json_plus_cite_tag_shape_still_leaks_if_model_reverts",
    "raw_text": (
        '{"ideal_member": "Chief (cite index=\\"1-1\\">is a private membership '
        'network for senior executive women</cite>.", "verdict_summary": '
        '"Best for senior operators.", "confidence": {"ideal_member": true}}'
    ),
    "cited_document_indexes": [],
}
