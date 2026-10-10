"""Compare-page data serializer — Compare Redesign Phase 1 (2026-09).

Pure-data layer for the Software and Communities Compare pages
(`/tools/software/compare`, `/tools/communities/compare`), following the
`linklib/gates.py` precedent exactly: no HTML, no FastAPI, no Library/DB
calls in this module. `webapp/app.py` fetches tool/community/profile/
citation/competitor dicts via the existing `Library` methods, then hands
them to `build_software_compare`/`build_communities_compare` here to get
back a curated, already-gated structure; the HTML layer (`webapp/app.py`)
is the only thing that turns it into markup.

This is the shared contract for three consumers, not just the HTML page
this PR ships:
  1. This page's own rendering (Compare Redesign Phase 1).
  2. Compare Phase 2's AI-summary generation prompt (a later, separate PR)
     — it needs the identical curated field set this module selects, not a
     re-derived approximation of it.
  3. MCP Phase 3's Toolbox/Communities compare tools (a later, separate
     PR) — so "compare HubiFi and RightRev" answered via Claude returns the
     same curated shape as the web page.

Compare shows full field text, never clamped (2026-10). `CompareField.text`
is always the full, untruncated field text, and the HTML layer renders every
character of it. This replaced the Compare Redesign Phase 1 Step 0 decision
(a 4-line CSS `-webkit-line-clamp` and its `EXCERPT_LINE_CLAMP` constant,
both removed): Brian's standing rule is that a profile, a comparison and an
MCP response never cut a field off. Keeping the full text here also means
citations, accessibility, copy/paste, the MCP tools and the Phase 2
summarizer all see the real content.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field

from . import gates
from . import tool_labels
from .enrich import NEEDS_VERIFICATION
from .community_profile import NOT_ASSESSED, cpe_note, cpe_state

# The live Community profile fields, grouped by theme. The profile page's card
# grouping and Compare's section grouping both read this list, so they can
# never drift apart (it moved here from webapp/app.py in Compare Redesign
# Phase 1). No gating logic lives here: every field goes through
# `_narrative_field`/`gates.field_state` off the whole-profile `needs_review`
# flag, exactly like every other entry.
#
# PR 2a (2026-09) — the five group names below are the ONE place these words
# live. The admin edit page, the public profile page, Compare and the MCP
# tools all read them from here, so a rename is a one-line change. They were
# four titles here ("Who it's for", "What you get", "How it works", "Cost and
# structure") and five separate literals on the admin page before this pass.
GROUP_TARGET_AUDIENCE = "Target audience"
GROUP_MEMBER_EXPERIENCE = "Member experience"
GROUP_ECONOMICS = "Economics"
GROUP_KEY_POINTS = "Key points"
GROUP_ADDITIONAL_BENEFITS = "Additional benefits"

# Bottom line is its own leading section on the public page, Compare and MCP
# (a callout, then the groups). The admin edit page lists it inside Key points
# instead; `community_admin_groups()` below builds that view from this list.
BOTTOM_LINE = ("Bottom line", "verdict_summary")

COMMUNITY_PROFILE_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (GROUP_TARGET_AUDIENCE, [
        ("Ideal member", "ideal_member"),
        ("Who should skip it", "anti_fit"),
        ("Value proposition", "value_prop"),
    ]),
    (GROUP_MEMBER_EXPERIENCE, [
        ("Programming", "format_reality"),
        ("Engagement level", "engagement_level"),
        ("Application friction", "application_friction"),
    ]),
    (GROUP_ECONOMICS, [
        ("Business model", "business_model"),
        ("Sponsor relationship", "sponsor_relationship_note"),
        ("Cost vs. value", "cost_value_verdict"),
    ]),
    (GROUP_KEY_POINTS, [
        ("Notable members", "notable_members"),
        ("Trade-offs to weigh", "public_criticism"),
    ]),
    (GROUP_ADDITIONAL_BENEFITS, [
        ("Resources included", "resources_included"),
        ("Jobs program", "jobs_program"),
    ]),
]

# PR 2a.1 (2026-09) — the Program details words, in ONE place. The admin edit
# page's Program details grid, the public Details card, Compare's Program
# details band and the MCP `key_facts` list all read these names, so Brian
# never sees a name in the edit view that differs from what a visitor sees.
# tests/test_edit_polish_2a1.py walks every mapped pair; tests/test_mcp_field_parity.py checks them against the page and the MCP.
PROGRAM_DETAILS_TITLE = "Program details"
LABEL_REACH = "Reach"
LABEL_COST_BAND = "Cost band"
LABEL_SPONSORSHIP = "Sponsorship"
LABEL_ACCESS = "Access"
LABEL_CPE = "CPE eligible"
LABEL_FORMAT = "Format"
# Admin-only companions that feed a combined public row: Reach shows the
# Specified markets, Sponsorship shows the Sponsor name in parentheses.
LABEL_SPECIFIED_MARKETS = "Specified markets"
LABEL_SPONSOR_NAME = "Sponsor name"
# Public order, top to bottom (Details card, Compare band, MCP key_facts).
PROGRAM_DETAILS_LABELS = (
    LABEL_REACH, LABEL_COST_BAND, LABEL_SPONSORSHIP, LABEL_ACCESS, LABEL_CPE, LABEL_FORMAT,
)
# Admin priority-tag names; the public sticker/badge they produce is named the same.
LABEL_FEATURED = "Featured"
LABEL_FORMAL_ADVISOR = "Formal advisor"


def community_admin_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """The admin edit page's view of COMMUNITY_PROFILE_GROUPS: the same five
    groups in the same order, with Bottom line added as the last field of Key
    points, so admin and public use the same words for every group and field."""
    return [
        (title, fields + [BOTTOM_LINE] if title == GROUP_KEY_POINTS else list(fields))
        for title, fields in COMMUNITY_PROFILE_GROUPS
    ]


def community_geo_line(c: dict) -> str:
    """Moved here (Compare Redesign Phase 1) from webapp/app.py's
    `_community_geo_line` — a pure function over a community dict, no
    reason for it to be webapp-private, and Compare's Key facts band needs
    the exact same reach/local_markets logic the profile page already
    uses. webapp/app.py re-exports this name for its existing call site
    rather than keeping a second, driftable copy.

    An unresearched reach must return the verification flag rather than
    falling through to the "no local_markets" branch below, which would
    otherwise render a guessed "National · online" as if it were
    confirmed."""
    if c.get("reach") == NEEDS_VERIFICATION:
        return NEEDS_VERIFICATION
    local_markets = (c.get("local_markets") or "").strip()
    reach = c.get("reach") or "National"
    if reach == "Regional":
        return local_markets if local_markets else "Regional"
    if not local_markets:
        return "Global" if reach == "Global" else "National · online"
    return f"{reach} · {local_markets}"


@dataclass
class CompareField:
    """One narrative field for one compared entity — Description, Agent
    taxonomy, Bottom line (tools' competitive_differentiation, communities'
    verdict_summary), or one of a Community profile group's sub-fields.
    `state` is always computed (EMPTY included) — every section renders
    for every entity, mirroring the profile pages' own "nothing ever
    disappears" radical-transparency standard, not omitted when blank."""
    key: str
    label: str
    text: str
    state: gates.GateState
    citations: list = _dc_field(default_factory=list)


@dataclass
class CompareSection:
    """One grouped card-equivalent — Description/Agent taxonomy/Bottom
    line/Competitors for a tool, or one of Communities' 4 themed profile
    groups. `fields` is a single-item list for every tool section and for
    a community's Bottom line; it's the group's 3-4 sub-fields for a
    Community profile group, mirroring `COMMUNITY_PROFILE_GROUPS` exactly."""
    title: str
    fields: list[CompareField]


@dataclass
class CompareChipItem:
    name: str
    url: str


@dataclass
class CompareChipList:
    """A curated cross-link list — Competitors (tools) / Similar
    communities. Empty `items` means "not yet curated," not "there are
    none" — same convention as every other empty state here."""
    title: str
    empty_copy_key: str
    items: list[CompareChipItem] = _dc_field(default_factory=list)


@dataclass
class CompareKeyFact:
    """One Key-facts-band line — Region/Access/Sponsor/Cost/etc. for a
    Community (tools have none of these beyond tags, which are handled
    separately via `CompareEntity.tags`/`CompareTagDiff`, not this class).
    `needs_verification` mirrors the pre-existing `_NEEDS_VERIFICATION`
    data-completeness sentinel (linklib.enrich.NEEDS_VERIFICATION) — a
    different concept from the review-state gate (`gates.GateState`):
    this is "we never researched this," not "an AI draft awaits human
    review." `value` is blank whenever this is true."""
    label: str
    value: str
    needs_verification: bool = False


@dataclass
class CompareProgramDetail:
    """One Program details row for a Community (MCP field parity, 2026-10):
    the structured twin of a `CompareKeyFact`, labelled from
    `PROGRAM_DETAILS_LABELS` so the page, Compare and MCP use the same words.
    `value` is blank when `needs_verification` (the never-researched
    sentinel). Only CPE eligible carries `note` and `state`: it lives on the
    profile, so it is gated by the whole-profile `needs_review` flag exactly
    like the page's CPE row; every other row comes from the listing and has
    no review state (`state` is None)."""
    label: str
    value: str
    needs_verification: bool = False
    note: str = ""
    state: gates.GateState | None = None


@dataclass
class CompareEntity:
    id: int
    slug: str
    name: str
    profile_url: str
    promoted: bool
    advisor: bool
    tags: list[str]
    key_facts: list[CompareKeyFact]
    sections: list[CompareSection]
    chip_lists: list[CompareChipList]
    program_details: list[CompareProgramDetail] = _dc_field(default_factory=list)
    # Software only (issue #624): the vendor's Primary use. "" for a
    # community, or for a vendor with no primary yet.
    primary_category: str = ""


@dataclass
class CompareTagDiff:
    """Shared-vs-unique tag split across every compared entity. `shared`
    is the intersection across ALL entities (empty when fewer than 2
    entities, or when nothing overlaps); `unique[entity_id]` is that
    entity's own tags minus the shared set."""
    shared: list[str]
    unique: dict[int, list[str]]


def tag_diff(entities_tags: dict[int, list[str] | None]) -> CompareTagDiff:
    sets = {eid: set(tags or []) for eid, tags in entities_tags.items()}
    if len(sets) < 2:
        return CompareTagDiff(shared=[], unique={eid: sorted(s) for eid, s in sets.items()})
    shared_set = set.intersection(*sets.values()) if sets else set()
    return CompareTagDiff(
        shared=sorted(shared_set),
        unique={eid: sorted(s - shared_set) for eid, s in sets.items()},
    )


def _narrative_field(key: str, label: str, raw_text: str | None, unverified: bool) -> CompareField:
    text = (raw_text or "").strip()
    return CompareField(key=key, label=label, text=text, state=gates.field_state(text, unverified))


def _key_fact(label: str, raw_value: str | None) -> CompareKeyFact | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if value == NEEDS_VERIFICATION:
        return CompareKeyFact(label=label, value="", needs_verification=True)
    return CompareKeyFact(label=label, value=value)


def build_program_details(c: dict, profile: dict) -> list[CompareProgramDetail]:
    """Program details rows in `PROGRAM_DETAILS_LABELS` order. Empty rows are
    skipped, as the page's Details card skips them; CPE eligible always shows
    (an empty stored value reads as Not assessed)."""
    sponsorship = ""
    if c.get("sponsorship_type"):
        sponsorship = c["sponsorship_type"]
        if c.get("sponsor_name"):
            sponsorship += f" ({c['sponsor_name']})"
    raw = {
        LABEL_REACH: community_geo_line(c),
        LABEL_COST_BAND: c.get("cost_band"),
        LABEL_SPONSORSHIP: sponsorship,
        LABEL_ACCESS: c.get("access"),
        LABEL_FORMAT: c.get("format"),
    }
    cpe_raw = (profile.get("cpe_eligible") or "").strip()
    out: list[CompareProgramDetail] = []
    for label in PROGRAM_DETAILS_LABELS:
        if label == LABEL_CPE:
            out.append(CompareProgramDetail(
                label=label, value=cpe_state(cpe_raw), note=cpe_note(cpe_raw),
                state=gates.state_for(bool(profile.get("needs_review")))))
            continue
        value = (raw[label] or "").strip()
        if not value:
            continue
        if value == NEEDS_VERIFICATION:
            out.append(CompareProgramDetail(label=label, value="", needs_verification=True))
        else:
            out.append(CompareProgramDetail(label=label, value=value))
    return out


def build_software_compare(
    tools: list[dict],
    citations: dict[tuple[int, str], list[dict]],
    competitors: dict[int, list[dict]],
) -> tuple[list[CompareEntity], CompareTagDiff]:
    """`citations` keys are `(tool_id, field_name)` -> the field's citation
    list from `Library.get_entity_citations("tool", tool_id, field_name)`,
    fetched by the caller for `"description"` and `"agent_taxonomy"` (the
    two tool fields with a real Citations-API grounding mechanism —
    competitive_differentiation has none, per CLAUDE.md). `competitors`
    keys are `tool_id` -> `Library.list_tool_competitors(tool_id)`."""
    entities: list[CompareEntity] = []
    tags_by_id: dict[int, list[str]] = {}
    for t in tools:
        tid = t["id"]
        tags_by_id[tid] = t.get("categories") or []

        desc_field = _narrative_field(
            # The key stays "description": MCP `compare_software` serializes it
            # and clients may read it, and keys are never renamed by a label
            # change. The content and the label are the short summary.
            "description", tool_labels.SHORT_SUMMARY, (t.get("summary") or "").strip(),
            bool(t.get("description_needs_verification")))
        # No fallback to the long description: the row is labelled for the
        # short summary, so an empty one shows the standard placeholder. The
        # citations list belongs to the long Description, which Compare does
        # not show, so none are attached here.

        agent_field = _narrative_field(
            "agent_taxonomy", tool_labels.AGENT, t.get("agent_taxonomy_note"),
            bool(t.get("agent_taxonomy_needs_verification")))
        if agent_field.text:
            agent_field.citations = citations.get((tid, "agent_taxonomy"), [])

        diff_field = _narrative_field(
            "competitive_differentiation", tool_labels.BOTTOM_LINE, t.get("competitive_differentiation"),
            bool(t.get("competitive_differentiation_needs_verification")))

        comp_list = competitors.get(tid, [])
        entities.append(CompareEntity(
            id=tid, slug=t["slug"], name=t["name"],
            profile_url=f"/tools/software/{t['slug']}",
            promoted=bool(t.get("promoted")), advisor=bool(t.get("advisor")),
            tags=tags_by_id[tid],
            primary_category=(t.get("primary_category") or ""),
            key_facts=[],
            sections=[
                CompareSection(tool_labels.SHORT_SUMMARY, [desc_field]),
                CompareSection(tool_labels.SECTION_AGENT, [agent_field]),
                CompareSection(tool_labels.BOTTOM_LINE, [diff_field]),
            ],
            chip_lists=[CompareChipList(
                tool_labels.COMPETITORS, "tool_competitors",
                [CompareChipItem(c["name"], f"/tools/software/{c['slug']}") for c in comp_list],
            )],
        ))
    return entities, tag_diff(tags_by_id)


def build_communities_compare(
    communities: list[dict],
    profiles: dict[int, dict],
    citations: dict[int, list[dict]],
    similar: dict[int, list[dict]],
) -> tuple[list[CompareEntity], CompareTagDiff]:
    """`profiles` keys are `community_id` -> `Library.get_community_profile`
    (or `{}` when the community has no profile row yet). `citations` keys
    are `community_id` -> `Library.get_entity_citations("community", id,
    "community_profile")` — ONE shared citation set per community, same as
    the profile page's own single Sources list for the whole draft (see
    CLAUDE.md's Citations-API grounding fix, Phase 3), attached here to the
    Bottom line field since that's the first/leading section for each
    entity. `similar` keys are `community_id` -> curated similar-community
    dicts."""
    entities: list[CompareEntity] = []
    tags_by_id: dict[int, list[str]] = {}
    for c in communities:
        cid = c["id"]
        tags_by_id[cid] = c.get("categories") or []
        profile = profiles.get(cid) or {}
        unverified = bool(profile.get("needs_review"))

        sponsorship = ""
        if c.get("sponsorship_type"):
            sponsorship = c["sponsorship_type"]
            if c.get("sponsor_name"):
                sponsorship += f" ({c['sponsor_name']})"

        key_facts: list[CompareKeyFact] = []
        for label, raw_value in [
            (LABEL_REACH, community_geo_line(c)),
            (LABEL_COST_BAND, c.get("cost_band")),
            (LABEL_SPONSORSHIP, sponsorship),
            (LABEL_ACCESS, c.get("access")),
            (LABEL_CPE, profile.get("cpe_eligible") or NOT_ASSESSED),
            (LABEL_FORMAT, c.get("format")),
        ]:
            kf = _key_fact(label, raw_value)
            if kf:
                key_facts.append(kf)

        bottom_field = _narrative_field(
            "verdict_summary", "Bottom line", profile.get("verdict_summary"), unverified)
        if bottom_field.text:
            bottom_field.citations = citations.get(cid, [])

        sections = [CompareSection("Bottom line", [bottom_field])]
        for group_title, group_fields in COMMUNITY_PROFILE_GROUPS:
            gfields = [
                _narrative_field(key, label, profile.get(key), unverified)
                for label, key in group_fields
            ]
            sections.append(CompareSection(group_title, gfields))

        similar_list = similar.get(cid, [])
        entities.append(CompareEntity(
            id=cid, slug=c["slug"], name=c["name"],
            profile_url=f"/tools/communities/{c['slug']}",
            promoted=bool(c.get("featured")), advisor=bool(c.get("advisor")),
            tags=tags_by_id[cid],
            key_facts=key_facts,
            program_details=build_program_details(c, profile),
            sections=sections,
            chip_lists=[CompareChipList(
                "Similar communities", "community_similar_communities",
                [CompareChipItem(s["name"], f"/tools/communities/{s['slug']}") for s in similar_list],
            )],
        ))
    return entities, tag_diff(tags_by_id)


# ---------------------------------------------------------------------------
# MCP parity registry (2026-10). One entry per stored column on the four
# profile tables, deciding how (or whether) the MCP serves it. A new column
# fails tests/test_mcp_field_parity.py until someone adds a line here, so a
# field can never reach a profile without an MCP decision.
#
# Categories (the text before the first colon):
#   mcp:<tool>.<path>        served to every token. <path> is a dotted key path
#                            in that tool's output, with `field[<key>]` (a
#                            profile section field), `program_details[<Label>]`
#                            or a trailing `[]` (first list item).
#   mcp-admin:<tool>.<path>  served to an admin token only.
#   admin-only:<reason>      never public anywhere (web or MCP); the guard test
#                            proves a member token never sees the key or value.
#   excluded:<reason>        public on the web but deliberately not served over
#                            MCP. Not a leak, so the permission guard does not
#                            apply; a separate test keeps it unserved until
#                            someone changes this line.
#   retired                  frozen in the schema, rendered and collected nowhere.
#   internal:<reason>        row or join key, not a field.
# ---------------------------------------------------------------------------
MCP_PARITY_KINDS = ("mcp", "mcp-admin", "admin-only", "excluded", "retired", "internal")

_EXCLUDED_ASSET = "excluded: presentation asset, Brian's decision"
_ADMIN_TS = "admin-only: audit timestamp"
_ADMIN_FLAG = "admin-only: edit-page review/confidence signal, never public"
_RETIRED = "retired: frozen in the schema, rendered and collected nowhere"

MCP_PARITY: dict[str, str] = {
    # --- tools ---------------------------------------------------------
    "tools.id": "mcp:get_software.id",
    "tools.name": "mcp:get_software.name",
    "tools.slug": "mcp:get_software.slug",
    "tools.description": "mcp:get_software.description.text",
    "tools.url": "mcp:get_software.url",
    "tools.categories_json": "mcp:get_software.categories",
    "tools.primary_category": "mcp:get_software.primary_category",
    "tools.approved": "admin-only: approval gate; an unapproved record is refused, the flag is never emitted",
    "tools.advisor": "mcp:get_software.advisor",
    "tools.submitted_by": "admin-only: submitter name",
    "tools.created_at": _ADMIN_TS,
    "tools.updated_at": _ADMIN_TS,
    "tools.vendor_email": "admin-only: vendor contact email, private",
    "tools.promoted": "mcp:get_software.promoted",
    "tools.warm_intro_enabled": "mcp:get_software.warm_intro_available",
    "tools.vendor_name": "admin-only: vendor contact name, private",
    "tools.competitive_differentiation": "mcp:get_software.bottom_line.text",
    "tools.agent_taxonomy_note": "mcp:get_software.agent_taxonomy.text",
    "tools.screenshot_url": _EXCLUDED_ASSET,
    "tools.screenshot_is_product": _RETIRED,
    "tools.screenshot_captured_at": _EXCLUDED_ASSET,
    "tools.agent_taxonomy_needs_verification": "mcp:get_software.agent_taxonomy.state",
    "tools.summary": "mcp:get_software.summary",
    "tools.logo_path": _EXCLUDED_ASSET,
    "tools.app_screenshot_source_url": "admin-only: capture input, not rendered",
    "tools.app_screenshot_url": _EXCLUDED_ASSET,
    "tools.app_screenshot_captured_at": _EXCLUDED_ASSET,
    "tools.description_needs_verification": "mcp:get_software.description.state",
    "tools.competitive_differentiation_needs_verification": "mcp:get_software.bottom_line.state",
    "tools.suite_note": "admin-only: stored, not rendered, decision pending",
    "tools.description_ai_confident": _ADMIN_FLAG,
    "tools.competitive_differentiation_ai_confident": _ADMIN_FLAG,
    "tools.agent_taxonomy_ai_confident": _ADMIN_FLAG,
    "tools.research_refusal": "admin-only: why on-add research drafted nothing (issue #696)",
    "tools.description_low_confidence": _ADMIN_FLAG,
    "tools.competitive_differentiation_low_confidence": _ADMIN_FLAG,
    "tools.agent_taxonomy_low_confidence": _ADMIN_FLAG,
    "tools.logo_manual_override": "admin-only: logo override bookkeeping",
    "tools.logo_override_stale": "admin-only: logo override bookkeeping",
    "tools.needs_review": "mcp-admin:get_software.needs_review",
    # --- communities ---------------------------------------------------
    "communities.id": "mcp:get_community.id",
    "communities.name": "mcp:get_community.name",
    "communities.slug": "mcp:get_community.slug",
    "communities.url": "mcp:get_community.url",
    "communities.demographic": _RETIRED,
    "communities.cost_band": "mcp:get_community.program_details[Cost band]",
    "communities.cost_note": _RETIRED,
    "communities.sponsorship_type": "mcp:get_community.program_details[Sponsorship]",
    "communities.sponsor_name": "mcp:get_community.program_details[Sponsorship]",
    "communities.access": "mcp:get_community.program_details[Access]",
    "communities.format": "mcp:get_community.program_details[Format]",
    "communities.notes": _RETIRED,
    "communities.categories_json": "mcp:get_community.categories",
    "communities.approved": "admin-only: approval gate; an unapproved record is refused, the flag is never emitted",
    "communities.submitted_by": "admin-only: submitter name",
    "communities.created_at": _ADMIN_TS,
    "communities.updated_at": _ADMIN_TS,
    "communities.reach": "mcp:get_community.program_details[Reach]",
    "communities.metros_json": _RETIRED,
    "communities.local_markets": "mcp:get_community.program_details[Reach]",
    "communities.featured": "mcp:get_community.promoted",
    "communities.advisor": "mcp:get_community.advisor",
    "communities.screenshot_url": _EXCLUDED_ASSET,
    "communities.screenshot_is_product": _RETIRED,
    "communities.screenshot_captured_at": _EXCLUDED_ASSET,
    "communities.logo_path": _EXCLUDED_ASSET,
    "communities.app_screenshot_source_url": "admin-only: capture input, not rendered",
    "communities.app_screenshot_url": _EXCLUDED_ASSET,
    "communities.app_screenshot_captured_at": _EXCLUDED_ASSET,
    "communities.logo_manual_override": "admin-only: logo override bookkeeping",
    "communities.logo_override_stale": "admin-only: logo override bookkeeping",
    # --- community_profiles --------------------------------------------
    "community_profiles.community_id": "internal: row key",
    "community_profiles.ideal_member": "mcp:get_community.field[ideal_member]",
    "community_profiles.anti_fit": "mcp:get_community.field[anti_fit]",
    "community_profiles.value_prop": "mcp:get_community.field[value_prop]",
    "community_profiles.format_reality": "mcp:get_community.field[format_reality]",
    "community_profiles.engagement_level": "mcp:get_community.field[engagement_level]",
    "community_profiles.sponsor_relationship_note": "mcp:get_community.field[sponsor_relationship_note]",
    "community_profiles.application_friction": "mcp:get_community.field[application_friction]",
    "community_profiles.cost_value_verdict": "mcp:get_community.field[cost_value_verdict]",
    "community_profiles.notable_members": "mcp:get_community.field[notable_members]",
    "community_profiles.founded_year": _RETIRED,
    "community_profiles.public_criticism": "mcp:get_community.field[public_criticism]",
    "community_profiles.verdict_summary": "mcp:get_community.field[verdict_summary]",
    "community_profiles.low_confidence": _ADMIN_FLAG,
    "community_profiles.updated_at": _ADMIN_TS,
    "community_profiles.business_model": "mcp:get_community.field[business_model]",
    "community_profiles.primary_purpose": _RETIRED,
    "community_profiles.cpe_eligible": "mcp:get_community.program_details[CPE eligible]",
    "community_profiles.platform_type": _RETIRED,
    "community_profiles.meeting_format": _RETIRED,
    "community_profiles.event_style": _RETIRED,
    "community_profiles.seniority_band": _RETIRED,
    "community_profiles.resources_included": "mcp:get_community.field[resources_included]",
    "community_profiles.needs_review": "mcp-admin:get_community.needs_review",
    "community_profiles.stage_focus": _RETIRED,
    "community_profiles.jobs_program": "mcp:get_community.field[jobs_program]",
    "community_profiles.team_or_individual": _RETIRED,
    **{f"community_profiles.{f}_ai_confident": _ADMIN_FLAG for f in (
        "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
        "engagement_level", "sponsor_relationship_note", "application_friction",
        "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary")},
    # --- tool_feature_links --------------------------------------------
    "tool_feature_links.id": "internal: row key",
    "tool_feature_links.tool_id": "internal: join key",
    "tool_feature_links.feature_id": "internal: join key",
    "tool_feature_links.availability": "mcp:get_software.key_features[].availability",
    "tool_feature_links.ai_enabled": "mcp:get_software.key_features[].ai_enabled",
    "tool_feature_links.verified_as_of": "admin-only: curation date, edit page only",
    "tool_feature_links.note": "admin-only: curation log with reviewer caveats",
    "tool_feature_links.source_url": "admin-only: curation source, edit page only",
    "tool_feature_links.created_at": _ADMIN_TS,
    "tool_feature_links.updated_at": _ADMIN_TS,
    "tool_feature_links.public_note": "mcp:get_software.key_features[].public_note",
}
