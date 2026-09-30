"""PR 2a.1: community and software edit page polish.

Covers the shared Program details words (one constant set read by the edit
page, the public Details card, Compare and MCP, with a drift test over every
mapped pair), the Priority tags box on both edit pages, Categories moved up,
the over-max refusal that re-renders from what was submitted, the shared
character-budget script never disabling buttons another form owns, the two
coverage tests for empty states, and the /admin/checks warning row.
"""
import importlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import compare
from linklib.community_profile import PROFILE_LIMITS
from linklib.db import Library


@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


@pytest.fixture
def admin(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    return c


@pytest.fixture
def visitor(app_module):
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, raise_server_exceptions=True)


def _lib():
    return Library(os.environ["LINKLIB_DB"])


def _full_community(needs_review=0, **profile):
    lib = _lib()
    cid = lib.add_community(
        name="Chief", url="https://chief.com", demographic="", cost_band="Paid", categories=["FP&A"], approved=1,
        sponsorship_type="Independent", sponsor_name="Acme", access="Application", format="Hybrid",
        reach="Regional", local_markets="Boston, Denver", featured=1, advisor=1)
    base = dict(ideal_member="Senior operators.", verdict_summary="Solid.",
                cpe_eligible="Yes (NASBA sponsor)", needs_review=needs_review)
    base.update(profile)
    lib.upsert_community_profile(cid, **base)
    slug = lib.get_community(cid)["slug"]
    lib.close()
    return cid, slug


ADMIN_ONLY_WORDS = {compare.LABEL_SPECIFIED_MARKETS, compare.LABEL_SPONSOR_NAME}


# -- one set of words, four surfaces ------------------------------------------

def test_program_details_words_match_on_all_four_surfaces(admin, visitor):
    """Every mapped label appears on the edit page, the public Details card,
    the Compare band and the MCP key_facts list, read from one constant set."""
    cid, slug = _full_community()
    edit = admin.get(f"/tools/communities/{slug}/edit").text
    public = visitor.get(f"/tools/communities/{slug}").text
    cmp_html = visitor.get(f"/tools/communities/compare?ids={cid},{cid}").text if False else ""
    # Edit page: every public label, plus the two admin-only companions.
    for label in (*compare.PROGRAM_DETAILS_LABELS, *ADMIN_ONLY_WORDS,
                  compare.LABEL_FEATURED, compare.LABEL_FORMAL_ADVISOR, compare.PROGRAM_DETAILS_TITLE):
        assert label in edit, f"edit page is missing {label!r}"
    # Public Details card: the heading and each label in the fixed order.
    assert f'<h2 class="tp-card-h">{compare.PROGRAM_DETAILS_TITLE}</h2>' in public
    labels = re.findall(r'class="tp-detail-label">([^<]*)<', public)
    assert labels == list(compare.PROGRAM_DETAILS_LABELS)
    # MCP and Compare share the serializer: same labels, same order.
    lib = _lib()
    community = lib.get_community(cid)
    profile = lib.get_community_profile(cid)
    lib.close()
    entities, _ = compare.build_communities_compare([community], {cid: profile}, {}, {})
    assert [kf.label for kf in entities[0].key_facts] == list(compare.PROGRAM_DETAILS_LABELS)


def test_compare_page_band_is_titled_program_details(admin, visitor):
    cid, slug = _full_community()
    lib = _lib()
    cid2 = lib.add_community(name="Other", url="https://other.example", demographic="", cost_band="Free",
                             categories=["FP&A"], approved=1)
    lib.close()
    html = visitor.get(f"/tools/communities/compare?ids={cid},{cid2}").text
    assert compare.PROGRAM_DETAILS_TITLE in html
    assert 'cmp-sticky-label">Key facts<' not in html


def test_public_reach_row_shows_specified_markets_when_filled(visitor):
    _cid, slug = _full_community()
    html = visitor.get(f"/tools/communities/{slug}").text
    row = re.search(r'Reach</span><span class="tp-detail-value">(.*?)</span></div>', html, re.S).group(1)
    assert "Boston, Denver" in row


def test_public_sponsorship_row_shows_the_sponsor_name(visitor):
    _cid, slug = _full_community()
    html = visitor.get(f"/tools/communities/{slug}").text
    row = re.search(r'Sponsorship</span><span class="tp-detail-value">(.*?)</span></div>', html, re.S).group(1)
    assert "Acme" in row


def test_public_cpe_row_bold_word_muted_note_and_review_badge(visitor):
    _cid, slug = _full_community(needs_review=1)
    html = visitor.get(f"/tools/communities/{slug}").text
    row = re.search(r'CPE eligible</span><span class="tp-detail-value">(.*?)</span></div>', html, re.S).group(1)
    assert "<strong>Yes</strong>" in row
    assert "color:var(--muted)" in row and "(NASBA sponsor)" in row
    assert "tp-verify" in row  # the same "under review" label the group cards carry


def test_public_cpe_row_has_no_badge_once_reviewed(visitor):
    _cid, slug = _full_community(needs_review=0)
    html = visitor.get(f"/tools/communities/{slug}").text
    row = re.search(r'CPE eligible</span><span class="tp-detail-value">(.*?)</span></div>', html, re.S).group(1)
    assert "tp-verify" not in row


def test_cpe_is_no_longer_in_additional_benefits():
    fields = dict(f for _t, fs in compare.COMMUNITY_PROFILE_GROUPS for f in [(k, l) for l, k in fs])
    assert "cpe_eligible" not in fields


def test_review_pill_says_under_review_like_the_visitor_label(admin, visitor):
    _cid, slug = _full_community(needs_review=1)
    edit = admin.get(f"/tools/communities/{slug}/edit").text
    assert ">Under review" in edit and ">Needs review" not in edit
    assert "under review" in visitor.get(f"/tools/communities/{slug}").text.lower()


def test_advisor_badge_tooltip_uses_the_formal_advisor_word(visitor):
    _cid, slug = _full_community()
    html = visitor.get("/tools/communities").text
    assert 'title="Formal advisor (Brian Weisberg)"' in html
    assert compare.LABEL_FORMAL_ADVISOR in html


# -- edit page layout ------------------------------------------------------------

def test_community_edit_layout_priority_tags_and_categories_move_up(admin):
    _cid, slug = _full_community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    assert html.index("Categories") < html.index("Verification status") < html.index("Profile draft") \
        < html.index("Priority tags") < html.index("Program details")
    box = html[html.index("Priority tags"):]
    assert box.index("Featured") < box.index("Formal advisor")
    assert "Adds a &quot;Featured&quot; sticker to this community" in html


def test_software_edit_layout_priority_tags_and_categories_move_up(admin):
    lib = _lib()
    tid = lib.add_tool("Acme", "Does things.", "https://acme.example", ["FP&A"], approved=1)
    slug = lib.get_tool(tid)["slug"]
    lib.close()
    html = admin.get(f"/tools/software/{slug}/edit").text
    assert html.index("Categories") < html.index("Verification status") < html.index("Priority tags") \
        < html.index(">Warm intro<")
    box = html[html.index("Priority tags"):]
    assert box.index("Featured") < box.index("Formal advisor")
    assert 'name="promoted" value="1"' in html and 'name="advisor" value="1"' in html
    assert "software vendor" in html


def test_program_details_grid_uses_one_named_control_height(admin):
    _cid, slug = _full_community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    grid = html[html.index('class="program-grid"'):html.index('class="program-grid"') + 6000]
    assert "--program-ctl-h:47px" in grid
    assert grid.count("height:var(--program-ctl-h)") >= 7
    for gone in (">Cost<", ">Sponsor<", ">Approach<"):
        assert gone not in grid


def test_verification_and_profile_draft_forms_do_not_nest(admin):
    _cid, slug = _full_community()
    html = admin.get(f"/tools/communities/{slug}/edit").text
    main = html[html.index('<form id="comm-edit-form"'):]
    inner = main[main.index(">") + 1:main.index("</form>")]
    assert "<form" not in inner
    assert 'id="review-status-form-communities-' in html


# -- refusal re-render -------------------------------------------------------------

def _post(admin, slug, **over):
    data = {"name": "Chief", "url": "https://chief.com", "cost_band": "Paid", "categories": ["FP&A"],
            "cpe_eligible": "Yes", "cpe_note": "NASBA sponsor"}
    for f in PROFILE_LIMITS:
        data[f] = ""
    data["ideal_member"] = "Senior operators."
    data["verdict_summary"] = "Solid."
    data.update(over)
    return admin.post(f"/tools/communities/{slug}/edit", data=data, follow_redirects=False)


def test_over_max_rerenders_from_submitted_values_and_writes_nothing(admin):
    cid, slug = _full_community()
    too_long = "y" * 801
    r = _post(admin, slug, name="Renamed", value_prop=too_long, anti_fit="z" * 900,
              ai_drafted_fields="value_prop", ai_drafted_citations='[{"n":1}]',
              ai_drafted_citations_model="m1", featured="1")
    assert r.status_code == 400
    html = r.text
    assert "Nothing was saved" in html
    assert "Value proposition</strong>: 801 characters, limit 800 (1 over)" in html
    assert "Who should skip it</strong>: 900 characters, limit 800 (100 over)" in html
    # Everything submitted is back in the boxes and hidden state.
    assert too_long in html and 'value="Renamed"' in html
    assert 'id="ai-drafted-fields" name="ai_drafted_fields" value="value_prop"' in html
    assert "ai-drafted-citations" in html and "m1" in html
    # And nothing was written.
    lib = _lib()
    assert lib.get_community(cid)["name"] == "Chief"
    assert not (lib.get_community_profile(cid) or {}).get("value_prop")
    lib.close()


def test_under_max_still_saves_and_redirects(admin):
    _cid, slug = _full_community()
    assert _post(admin, slug, value_prop="ok").status_code == 303


# -- shared character-budget script ------------------------------------------------

def _buttons_for_source(app_module):
    js = app_module._CHAR_BUDGET_JS
    m = re.search(r"function buttonsFor\(f\)\{.*?\n  \}\n", js, re.S)
    assert m
    return m.group(0)


def test_buttons_for_only_returns_buttons_the_form_owns(app_module):
    """A button can sit inside the form's markup yet belong to another form via
    its form= attribute (Mark reviewed, the logo controls); a text limit must
    never disable it."""
    src = _buttons_for_source(app_module)
    script = src + """
    var f = {id: 'edit'};
    var save = {form: f}, review = {form: {id: 'other'}};
    f.querySelectorAll = function(){ return [save, review]; };
    var document = {querySelectorAll: function(){ return [save]; }};
    var got = buttonsFor(f);
    if (got.length !== 1 || got[0] !== save) { console.log('FAIL ' + got.length); process.exit(1); }
    console.log('OK');
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr


# -- coverage: empty states -----------------------------------------------------------

def test_empty_field_inside_a_populated_group_says_no_details_available(visitor):
    _cid, slug = _full_community()
    html = visitor.get(f"/tools/communities/{slug}").text
    card = html[html.index("Target audience"):]
    card = card[:card.index("</div>\n", card.index("No details available") - 400) + 6] if "No details available" in card else card
    assert "Senior operators." in html and "No details available." in html


def test_directory_card_for_a_community_with_no_profile_row_shows_the_empty_blurb(visitor):
    lib = _lib()
    lib.add_community(name="Bare", url="https://bare.example", demographic="", cost_band="Free",
                      categories=["FP&A"], approved=1)
    lib.close()
    r = visitor.get("/tools/communities")
    assert r.status_code == 200 and "Bare" in r.text
    from linklib import gates
    assert gates.EMPTY_COPY["community_bottom_line"].visitor_text in r.text


# -- /admin/checks warning row ---------------------------------------------------------

def test_checks_lists_profile_fields_over_their_limit_and_long_cpe_notes(admin):
    _cid, slug = _full_community(cpe_eligible="Yes (" + "n" * 45 + ")")
    # Stored text over the new max is exactly what the row exists for.
    lib = _lib()
    lib.conn.execute("UPDATE community_profiles SET value_prop=? WHERE community_id=?", ("v" * 850, _cid))
    lib.conn.commit()
    lib.close()
    html = admin.get("/admin/checks").text
    assert "Profile fields over their limit" in html
    assert "Value proposition" in html and "850" in html
    assert "CPE eligible note" in html and "over the target" in html


def test_checks_row_is_ok_when_nothing_is_over(admin):
    _full_community()
    html = admin.get("/admin/checks").text
    assert "Profile fields over their limit" in html and "None over" in html
