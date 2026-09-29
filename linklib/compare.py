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

Narrative-field truncation is deliberately NOT done here. `CompareField.text`
is always the full, untruncated field text — clamping to ~4 lines is a pure
CSS `-webkit-line-clamp` presentation concern the HTML layer applies
(`EXCERPT_LINE_CLAMP` below is the agreed line count, exposed so the HTML
layer and any future consumer stay in sync on the number). Keeping the full
text here means citations, accessibility, and copy/paste all see the real
content, and a future MCP tool or the Phase 2 summarizer — neither of which
wants a *visual* clamp — get the whole field for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field

from . import gates
from .enrich import NEEDS_VERIFICATION

# Approved in Compare Redesign Phase 1 Step 0: CSS line-clamp over a fixed
# character count, so the clamp adapts to each table's actual column width
# instead of guessing a char count that's wrong at some width.
EXCERPT_LINE_CLAMP = 4

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
        ("CPE eligible", "cpe_eligible"),
    ]),
]


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
            "description", "Description", t.get("summary") or t.get("description"),
            bool(t.get("description_needs_verification")))
        if desc_field.text:
            desc_field.citations = citations.get((tid, "description"), [])

        agent_field = _narrative_field(
            "agent_taxonomy", "How autonomous is it?", t.get("agent_taxonomy_note"),
            bool(t.get("agent_taxonomy_needs_verification")))
        if agent_field.text:
            agent_field.citations = citations.get((tid, "agent_taxonomy"), [])

        diff_field = _narrative_field(
            "competitive_differentiation", "Bottom line", t.get("competitive_differentiation"),
            bool(t.get("competitive_differentiation_needs_verification")))

        comp_list = competitors.get(tid, [])
        entities.append(CompareEntity(
            id=tid, slug=t["slug"], name=t["name"],
            profile_url=f"/tools/software/{t['slug']}",
            promoted=bool(t.get("promoted")), advisor=bool(t.get("advisor")),
            tags=tags_by_id[tid],
            key_facts=[],
            sections=[
                CompareSection("Description", [desc_field]),
                CompareSection("AI / Agent involvement", [agent_field]),
                CompareSection("Bottom line", [diff_field]),
            ],
            chip_lists=[CompareChipList(
                "Competitors", "tool_competitors",
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
            ("Region", community_geo_line(c)),
            ("Access", c.get("access")),
            ("Sponsor", sponsorship),
            ("Cost", c.get("cost_band")),
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
            sections=sections,
            chip_lists=[CompareChipList(
                "Similar communities", "community_similar_communities",
                [CompareChipItem(s["name"], f"/tools/communities/{s['slug']}") for s in similar_list],
            )],
        ))
    return entities, tag_diff(tags_by_id)
