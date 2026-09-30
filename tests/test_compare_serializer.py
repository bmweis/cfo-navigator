"""linklib/compare.py — the pure-data Compare-page serializer (Compare
Redesign Phase 1). Exercised directly, independent of any HTML rendering,
per the module's own charter as the shared contract for the HTML page,
Compare Phase 2's AI-summary prompt, and MCP Phase 3's compare tools.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import compare, gates


# ---------------------------------------------------------------------------
# tag_diff
# ---------------------------------------------------------------------------

def test_tag_diff_computes_shared_and_unique():
    diff = compare.tag_diff({1: ["FP&A", "ERP"], 2: ["FP&A", "Close Management"]})
    assert diff.shared == ["FP&A"]
    assert diff.unique[1] == ["ERP"]
    assert diff.unique[2] == ["Close Management"]


def test_tag_diff_no_overlap_means_everything_unique():
    diff = compare.tag_diff({1: ["ERP"], 2: ["FP&A"]})
    assert diff.shared == []
    assert diff.unique[1] == ["ERP"]
    assert diff.unique[2] == ["FP&A"]


def test_tag_diff_handles_none_and_empty_lists():
    diff = compare.tag_diff({1: None, 2: []})
    assert diff.shared == []
    assert diff.unique == {1: [], 2: []}


def test_tag_diff_single_entity_has_no_shared_concept():
    """Fewer than 2 entities: nothing to share against, so everything is
    "unique" (there's no meaningful shared/unique distinction with one
    entity) rather than crashing on set.intersection with zero sets."""
    diff = compare.tag_diff({1: ["FP&A"]})
    assert diff.shared == []
    assert diff.unique == {1: ["FP&A"]}


# ---------------------------------------------------------------------------
# build_software_compare
# ---------------------------------------------------------------------------

def _tool(id_, **kw):
    base = {
        "id": id_, "slug": f"tool-{id_}", "name": f"Tool {id_}",
        "categories": [], "promoted": 0, "advisor": 0,
        "summary": "", "description": "", "description_needs_verification": 0,
        "agent_taxonomy_note": "", "agent_taxonomy_needs_verification": 0,
        "competitive_differentiation": "", "competitive_differentiation_needs_verification": 0,
    }
    base.update(kw)
    return base


def test_software_compare_every_section_present_even_empty():
    tools = [_tool(1), _tool(2)]
    entities, diff = compare.build_software_compare(tools, {}, {})
    assert len(entities) == 2
    e = entities[0]
    titles = [s.title for s in e.sections]
    assert titles == ["Description", "AI / Agent involvement", "Bottom line"]
    for s in e.sections:
        assert s.fields[0].state == gates.GateState.EMPTY
        assert s.fields[0].text == ""


def test_software_compare_prefers_summary_over_description():
    tools = [_tool(1, summary="Short summary.", description="Longer description."), _tool(2)]
    entities, _ = compare.build_software_compare(tools, {}, {})
    desc_field = entities[0].sections[0].fields[0]
    assert desc_field.text == "Short summary."
    assert desc_field.state == gates.GateState.VERIFIED


def test_software_compare_falls_back_to_description_when_no_summary():
    tools = [_tool(1, description="Longer description.")]
    entities, _ = compare.build_software_compare(tools, {}, {})
    assert entities[0].sections[0].fields[0].text == "Longer description."


def test_software_compare_pending_state_from_needs_verification_flag():
    tools = [_tool(1, agent_taxonomy_note="Draft note.", agent_taxonomy_needs_verification=1)]
    entities, _ = compare.build_software_compare(tools, {}, {})
    agent_field = entities[0].sections[1].fields[0]
    assert agent_field.text == "Draft note."
    assert agent_field.state == gates.GateState.PENDING


def test_software_compare_attaches_citations_only_to_populated_fields():
    tools = [_tool(1, description="A description.")]
    citations = {
        (1, "description"): [{"n": 1, "title": "Source", "url": "https://example.com"}],
        (1, "agent_taxonomy"): [{"n": 1, "title": "Unused", "url": "https://unused.example"}],
    }
    entities, _ = compare.build_software_compare(tools, citations, {})
    desc_field = entities[0].sections[0].fields[0]
    assert desc_field.citations == citations[(1, "description")]
    # Agent taxonomy has no text -> citations are never attached to an
    # empty field, even if the caller's dict has an entry for it.
    agent_field = entities[0].sections[1].fields[0]
    assert agent_field.citations == []


def test_software_compare_differentiation_never_carries_citations():
    """competitive_differentiation has no Citations-API grounding mechanism
    at all (per CLAUDE.md) — confirm the serializer never attaches any,
    even if a caller mistakenly passed some in."""
    tools = [_tool(1, competitive_differentiation="Bottom line text.")]
    entities, _ = compare.build_software_compare(tools, {}, {})
    diff_field = entities[0].sections[2].fields[0]
    assert diff_field.text == "Bottom line text."
    assert diff_field.citations == []


def test_software_compare_competitors_chip_list():
    tools = [_tool(1)]
    competitors = {1: [{"name": "Rival Co", "slug": "rival-co"}]}
    entities, _ = compare.build_software_compare(tools, {}, competitors)
    chips = entities[0].chip_lists[0]
    assert chips.title == "Competitors"
    assert chips.empty_copy_key == "tool_competitors"
    assert chips.items[0].name == "Rival Co"
    assert chips.items[0].url == "/tools/software/rival-co"


def test_software_compare_tag_diff_uses_categories():
    tools = [_tool(1, categories=["FP&A", "ERP"]), _tool(2, categories=["FP&A"])]
    _, diff = compare.build_software_compare(tools, {}, {})
    assert diff.shared == ["FP&A"]
    assert diff.unique[1] == ["ERP"]
    assert diff.unique[2] == []


# ---------------------------------------------------------------------------
# build_communities_compare
# ---------------------------------------------------------------------------

def _community(id_, **kw):
    base = {
        "id": id_, "slug": f"community-{id_}", "name": f"Community {id_}",
        "categories": [], "featured": 0, "advisor": 0,
        "access": "", "sponsorship_type": "", "sponsor_name": "",
        "cost_band": "", "cost_note": "", "reach": "", "local_markets": "",
    }
    base.update(kw)
    return base


def test_communities_compare_every_section_present_even_empty():
    communities = [_community(1), _community(2)]
    entities, diff = compare.build_communities_compare(communities, {}, {}, {})
    e = entities[0]
    titles = [s.title for s in e.sections]
    assert titles == ["Bottom line", compare.GROUP_TARGET_AUDIENCE, compare.GROUP_MEMBER_EXPERIENCE,
                      compare.GROUP_ECONOMICS, compare.GROUP_KEY_POINTS,
                      compare.GROUP_ADDITIONAL_BENEFITS]
    for s in e.sections:
        for f in s.fields:
            assert f.state == gates.GateState.EMPTY


def test_communities_compare_groups_match_profile_page_grouping():
    """Confirms the serializer's grouping is literally
    compare.COMMUNITY_PROFILE_GROUPS — the same constant the profile page
    imports — not a second, independently-typed copy."""
    communities = [_community(1)]
    entities, _ = compare.build_communities_compare(communities, {}, {}, {})
    group_sections = entities[0].sections[1:]
    assert len(group_sections) == len(compare.COMMUNITY_PROFILE_GROUPS)
    for section, (title, fields) in zip(group_sections, compare.COMMUNITY_PROFILE_GROUPS):
        assert section.title == title
        assert [f.key for f in section.fields] == [key for _, key in fields]


def test_communities_compare_whole_profile_flag_drives_every_field_pending():
    communities = [_community(1)]
    profiles = {1: {"needs_review": 1, "ideal_member": "Solo CFOs.", "verdict_summary": "Great fit."}}
    entities, _ = compare.build_communities_compare(communities, profiles, {}, {})
    bottom = entities[0].sections[0].fields[0]
    assert bottom.state == gates.GateState.PENDING
    who_group = entities[0].sections[1]
    ideal_member_field = next(f for f in who_group.fields if f.key == "ideal_member")
    assert ideal_member_field.state == gates.GateState.PENDING


def test_communities_compare_verified_when_not_needs_review():
    communities = [_community(1)]
    profiles = {1: {"needs_review": 0, "verdict_summary": "Great fit."}}
    entities, _ = compare.build_communities_compare(communities, profiles, {}, {})
    assert entities[0].sections[0].fields[0].state == gates.GateState.VERIFIED


def test_communities_compare_citations_attach_only_when_bottom_line_populated():
    communities = [_community(1)]
    profiles = {1: {"verdict_summary": "Great fit."}}
    citations = {1: [{"n": 1, "title": "Source", "url": "https://example.com"}]}
    entities, _ = compare.build_communities_compare(communities, profiles, citations, {})
    assert entities[0].sections[0].fields[0].citations == citations[1]


def test_communities_compare_key_facts_include_region_access_sponsor_cost():
    c = _community(1, access="Invite-only", sponsorship_type="Vendor-backed", sponsor_name="Acme",
                    cost_band="<$1k/yr", cost_note="Annual dues", reach="Regional", local_markets="NYC")
    entities, _ = compare.build_communities_compare([c], {1: {}}, {}, {})
    facts = {kf.label: kf.value for kf in entities[0].key_facts}
    assert facts[compare.LABEL_REACH] == "NYC"
    assert facts[compare.LABEL_ACCESS] == "Invite-only"
    assert facts[compare.LABEL_SPONSORSHIP] == "Vendor-backed (Acme)"
    assert facts[compare.LABEL_COST_BAND] == "<$1k/yr"
    assert "Cost detail" not in facts and "Founded" not in facts   # retired in PR 2a


def test_communities_compare_needs_verification_sentinel_on_key_fact():
    from linklib.enrich import NEEDS_VERIFICATION
    c = _community(1, cost_band=NEEDS_VERIFICATION)
    entities, _ = compare.build_communities_compare([c], {}, {}, {})
    cost_fact = next(kf for kf in entities[0].key_facts if kf.label == compare.LABEL_COST_BAND)
    assert cost_fact.needs_verification is True
    assert cost_fact.value == ""


def test_communities_compare_similar_communities_chip_list():
    communities = [_community(1)]
    similar = {1: [{"name": "Peer Group", "slug": "peer-group"}]}
    entities, _ = compare.build_communities_compare(communities, {}, {}, similar)
    chips = entities[0].chip_lists[0]
    assert chips.title == "Similar communities"
    assert chips.items[0].url == "/tools/communities/peer-group"


def test_communities_compare_no_profile_row_degrades_gracefully():
    """A community with no community_profiles row at all (profiles.get
    returns the {} default) still produces every section, all EMPTY."""
    communities = [_community(1), _community(2)]
    entities, _ = compare.build_communities_compare(communities, {1: {"verdict_summary": "Has one."}}, {}, {})
    assert entities[0].sections[0].fields[0].state == gates.GateState.VERIFIED
    assert entities[1].sections[0].fields[0].state == gates.GateState.EMPTY


# ---------------------------------------------------------------------------
# community_geo_line
# ---------------------------------------------------------------------------

def test_community_geo_line_needs_verification_sentinel():
    from linklib.enrich import NEEDS_VERIFICATION
    assert compare.community_geo_line({"reach": NEEDS_VERIFICATION}) == NEEDS_VERIFICATION


def test_community_geo_line_regional_with_local_markets():
    assert compare.community_geo_line({"reach": "Regional", "local_markets": "NYC, SF"}) == "NYC, SF"


def test_community_geo_line_national_default():
    assert compare.community_geo_line({}) == "National · online"


def test_community_geo_line_global():
    assert compare.community_geo_line({"reach": "Global"}) == "Global"
