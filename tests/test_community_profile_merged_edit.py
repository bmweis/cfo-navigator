"""PR 2a (2026-09): the merged Community edit page and everything it changed.

One page (`/tools/communities/{slug}/edit`) now holds the listing and the
profile; eight profile columns and three listing columns are RETIRED (frozen in
the schema, never rendered, collected, generated or read). Covered here:

  * the frozen-column guarantee (a save never blanks a retired column),
  * the citation invariant (the shared set is never cleared while any `[n]`
    marker remains in any profile field),
  * confidence NULL on a hand edit,
  * per-field limits (amber target, refused over the max, max above production),
  * the CPE dropdown and its qualifier handling,
  * retired fields gone from the render and from generation output,
  * the directory card's Bottom line (one query, no-profile community safe),
  * the matchmaker still gets Ideal member, seeds stop syncing `notes`,
  * "Restore previous" (real headless Chromium, skipped when none is available).
"""
import importlib
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import compare
from linklib.community_profile import (
    PRODUCTION_LONGEST, PROFILE_LIMITS, RETIRED_COMMUNITY_FIELDS, RETIRED_PROFILE_FIELDS,
    CPE_NOTE_LIMITS, assemble_cpe, coerce_cpe_eligible, cpe_note, resolve_cpe_submission,
)
from linklib.db import Library

CITES = [{"n": 1, "title": "Chief", "url": "https://chief.com", "type": "tool_page"}]
FRESH_JSON = '[{"n": 1, "title": "Fresh", "url": "https://fresh.example", "type": "tool_page"}]'


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
def client(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _lib():
    return Library(os.environ["LINKLIB_DB"])


def _community(name="Chief", url="https://chief.com", profile=True, cites=True,
               retired=True, **fields):
    lib = _lib()
    kw = {}
    if retired:
        kw = dict(demographic="OLD demographic", cost_note="OLD cost note", notes="OLD notes")
    cid = lib.add_community(name=name, url=url, cost_band="Paid", categories=["FP&A"],
                            approved=1, **({"demographic": "", **kw} if not retired else kw))
    if profile:
        base = dict(ideal_member="Senior women.", value_prop="Peer network.",
                    verdict_summary="Solid.", cpe_eligible="Yes (NASBA sponsor)")
        base.update(fields)
        retired_kw = {}
        if retired:
            retired_kw = dict(founded_year=1999, event_style="OLD event", seniority_band="OLD band",
                              platform_type="OLD platform", team_or_individual="OLD team",
                              primary_purpose="OLD purpose", meeting_format="OLD meeting",
                              stage_focus="OLD stage")
        lib.upsert_community_profile(cid, **base, **retired_kw)
        if cites:
            lib.set_entity_citations("community", cid, "community_profile", CITES, model="m")
    slug = lib.get_community(cid)["slug"]
    lib.close()
    return cid, slug


def _post(client, slug, cid=None, **over):
    """Save the merged page, carrying the stored profile unchanged unless overridden."""
    lib = _lib()
    c = lib.get_community_by_slug(slug)
    prof = lib.get_community_profile(c["id"]) or {}
    lib.close()
    data = {"name": c["name"], "url": c["url"], "cost_band": c["cost_band"], "categories": ["FP&A"]}
    for f in PROFILE_LIMITS:
        data[f] = prof.get(f, "") or ""
    data["cpe_eligible"] = compare_token(prof.get("cpe_eligible"))
    data["cpe_note"] = cpe_note(prof.get("cpe_eligible"))
    data.update(over)
    return client.post(f"/tools/communities/{slug}/edit", data=data, follow_redirects=False)


def compare_token(v):
    from linklib.community_profile import cpe_token
    return cpe_token(v)


def _cites(cid):
    lib = _lib()
    try:
        return lib.get_entity_citations("community", cid, "community_profile")
    finally:
        lib.close()


# -- frozen columns ----------------------------------------------------------

def test_saving_the_merged_page_never_touches_retired_columns(client):
    cid, slug = _community()
    r = _post(client, slug, value_prop="A hand-edited value prop.")
    assert r.status_code == 303, r.text[:300]
    lib = _lib()
    c = lib.get_community(cid)
    p = lib.get_community_profile(cid)
    lib.close()
    assert c["demographic"] == "OLD demographic"
    assert c["cost_note"] == "OLD cost note"
    assert c["notes"] == "OLD notes"
    assert p["founded_year"] == 1999
    for f in ("event_style", "seniority_band", "platform_type", "team_or_individual",
              "primary_purpose", "meeting_format", "stage_focus"):
        assert p[f] == f"OLD {f.split('_')[0]}" or p[f].startswith("OLD"), (f, p[f])
    assert p["value_prop"] == "A hand-edited value prop."


def test_retired_lists_are_the_documented_eleven():
    assert set(RETIRED_PROFILE_FIELDS) == {
        "founded_year", "event_style", "seniority_band", "platform_type",
        "team_or_individual", "primary_purpose", "meeting_format", "stage_focus"}
    assert set(RETIRED_COMMUNITY_FIELDS) == {"demographic", "cost_note", "notes"}


def test_naive_full_replace_would_blank_them_so_the_guard_is_real():
    """The frozen-column test above only means something if a naive full
    replace really does blank a retired column. Prove it against a bare SQL
    replace of the shape the old upsert used."""
    lib = Library(tempfile.mktemp(suffix=".db"))
    cid = lib.add_community("X", "https://x.example", "d", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="a", event_style="KEEP ME")
    lib.conn.execute("UPDATE community_profiles SET ideal_member='b', event_style='' WHERE community_id=?", (cid,))
    assert lib.get_community_profile(cid)["event_style"] == ""
    lib.upsert_community_profile(cid, ideal_member="c", event_style="KEEP ME")
    lib.upsert_community_profile(cid, ideal_member="d")  # what the merged page does
    assert lib.get_community_profile(cid)["event_style"] == "KEEP ME"
    lib.close()


# -- citation invariant ------------------------------------------------------

def test_editing_one_cited_field_leaves_the_set_while_markers_remain(client):
    cid, slug = _community(value_prop="Peer network [1].", ideal_member="Senior women [1].")
    _post(client, slug, value_prop="Peer network, reworded [1].")
    assert _cites(cid) == CITES


def test_removing_the_last_marker_clears_the_set(client):
    cid, slug = _community(value_prop="Peer network [1].", ideal_member="Senior women.")
    _post(client, slug, value_prop="Peer network, no marker.")
    assert _cites(cid) == []


def test_markers_in_two_groups_edit_one_keeps_both(client):
    cid, slug = _community(ideal_member="Audience [1].", cost_value_verdict="Economics [1].")
    _post(client, slug, ideal_member="Audience, reworded [1].")
    assert _cites(cid) == CITES
    lib = _lib()
    assert "[1]" in lib.get_community_profile(cid)["cost_value_verdict"]
    lib.close()


def test_full_generate_then_save_writes_the_fresh_set(client):
    cid, slug = _community(ideal_member="Audience [1].")
    _post(client, slug, ideal_member="Freshly generated [1].", value_prop="Also fresh [1].",
          ai_drafted_fields="ideal_member,value_prop", ai_drafted_citations=FRESH_JSON,
          ai_drafted_citations_model="claude-opus-5")
    got = _cites(cid)
    assert [c["url"] for c in got] == ["https://fresh.example"]


def test_markers_with_no_stored_and_no_fresh_set_change_nothing(client):
    cid, slug = _community(ideal_member="Audience [1].", cites=False)
    _post(client, slug, ideal_member="Audience, reworded [1].")
    assert _cites(cid) == []


def test_invariant_helper_branches(app_module):
    act = app_module._community_citation_action
    assert act(["a [1]"], [], CITES) == "keep"
    assert act(["a [1]"], CITES, CITES) == "write"
    assert act(["a"], CITES, CITES) == "write"
    assert act(["a"], [], CITES) == "clear"
    assert act(["a"], [], []) == "none"
    assert act(["a [1]"], [], []) == "keep"


# -- confidence --------------------------------------------------------------

def test_hand_edited_tracked_field_goes_to_not_yet_assessed(client):
    cid, slug = _community(ideal_member="Senior women.", value_prop="Peer network.")
    lib = _lib()
    lib.upsert_community_profile(cid, ideal_member="Senior women.", value_prop="Peer network.",
                                 confidence={"ideal_member": 1, "value_prop": 1})
    lib.close()
    _post(client, slug, ideal_member="Rewritten by hand.")
    lib = _lib()
    p = lib.get_community_profile(cid)
    lib.close()
    assert p["ideal_member_ai_confident"] is None
    assert p["value_prop_ai_confident"] == 1  # untouched field keeps its own value


def test_field_drafted_this_session_keeps_the_fresh_confidence(client):
    cid, slug = _community()
    _post(client, slug, ideal_member="Drafted text.", ai_drafted_fields="ideal_member",
          ai_drafted_confidence="ideal_member:1")
    lib = _lib()
    p = lib.get_community_profile(cid)
    lib.close()
    assert p["ideal_member_ai_confident"] == 1


# -- limits ------------------------------------------------------------------

def test_limits_are_the_2a1_budget():
    """2a.1 sets the maxes below some stored text on purpose (Brian trims by
    hand; /admin/checks lists the over-limit fields), so this pins the values
    instead of the old "above every stored value" rule."""
    narrative = ("ideal_member", "anti_fit", "value_prop", "format_reality",
                 "engagement_level", "application_friction", "business_model",
                 "sponsor_relationship_note", "cost_value_verdict",
                 "notable_members", "public_criticism")
    for f in narrative:
        assert PROFILE_LIMITS[f] == (600, 800), f
    assert PROFILE_LIMITS["verdict_summary"] == (250, 400)
    assert PROFILE_LIMITS["resources_included"] == (300, 600)
    assert PROFILE_LIMITS["jobs_program"] == (300, 600)
    for f, (target, mx) in PROFILE_LIMITS.items():
        assert target < mx, f


def test_over_max_is_refused_whole_naming_the_limit(client):
    cid, slug = _community()
    too_long = "x" * (PROFILE_LIMITS["verdict_summary"][1] + 1)
    r = _post(client, slug, verdict_summary=too_long, value_prop="Should not be saved.")
    assert r.status_code == 400
    assert "400" in r.text
    lib = _lib()
    assert lib.get_community_profile(cid)["value_prop"] == "Peer network."
    lib.close()


def test_under_the_max_but_over_target_saves(client):
    cid, slug = _community()
    target, mx = PROFILE_LIMITS["verdict_summary"]
    text = "y" * (target + 10)
    assert _post(client, slug, verdict_summary=text).status_code == 303
    lib = _lib()
    assert lib.get_community_profile(cid)["verdict_summary"] == text
    lib.close()


def test_edit_page_renders_target_and_max_counters(client):
    _cid, slug = _community()
    html = client.get(f"/tools/communities/{slug}/edit").text
    for f, (target, _mx) in PROFILE_LIMITS.items():
        assert f'id="cp-{f}"' in html, f
    assert "Aim for 250" in html or "250" in html


# -- CPE ---------------------------------------------------------------------

def test_cpe_coercion_vocabulary():
    assert coerce_cpe_eligible("Yes") == "Yes"
    assert coerce_cpe_eligible("Yes (NASBA sponsor)") == "Yes (NASBA sponsor)"
    assert coerce_cpe_eligible("No evidence of CPE credit offered; assume no") == "No"
    assert coerce_cpe_eligible("maybe") == ""
    assert coerce_cpe_eligible("") == ""


def test_cpe_note_parse_and_assemble_round_trip():
    """No column: the stored string is "Word (note)", parsed on load and
    assembled on save. Every shape must survive the round trip."""
    for stored in ("Yes", "No", "Unclear", "Yes (NASBA sponsor)", "No (a (b))",
                   "Yes ((a) (b))", "Yes (x, y; z)"):
        assert assemble_cpe(cpe_token_of(stored), cpe_note(stored)) == stored, stored
    # A legacy value with prose after the word keeps its text as the note.
    assert cpe_note("Yes - NASBA sponsor") == "NASBA sponsor"
    assert resolve_cpe_submission("Yes", "NASBA sponsor") == "Yes (NASBA sponsor)"
    assert resolve_cpe_submission("No", "") == "No"
    assert resolve_cpe_submission("", "orphan note") == ""
    assert resolve_cpe_submission("Bogus", "x") == ""
    assert CPE_NOTE_LIMITS == (40, 60)


def cpe_token_of(v):
    return compare_token(v)


def test_cpe_save_round_trip_keeps_stored_qualifier(client):
    cid, slug = _community()
    _post(client, slug)
    lib = _lib()
    assert lib.get_community_profile(cid)["cpe_eligible"] == "Yes (NASBA sponsor)"
    lib.close()


def test_cpe_renders_as_a_dropdown_with_four_options(client):
    _cid, slug = _community()
    html = client.get(f"/tools/communities/{slug}/edit").text
    m = re.search(r'<select[^>]*id="cp-cpe_eligible".*?</select>', html, re.S)
    assert m
    opts = re.findall(r"<option[^>]*>([^<]*)</option>", m.group(0))
    assert opts == ["Not assessed", "Yes", "No", "Unclear"]


# -- the merged page's shape -------------------------------------------------

def test_edit_page_has_five_named_groups_and_no_retired_fields(client):
    _cid, slug = _community()
    html = client.get(f"/tools/communities/{slug}/edit").text
    for g in ("Target audience", "Member experience", "Economics", "Key points", "Additional benefits"):
        assert g in html, g
    for label in ("Trade-offs to weigh", "Programming", "Specified markets", "Program details", "Bottom line",
                  "Generate full profile"):
        assert label in html, label
    for retired in (*RETIRED_PROFILE_FIELDS, "demographic", "cost_note"):
        assert f'id="cp-{retired}"' not in html and f'name="{retired}"' not in html, retired
    assert 'name="notes"' not in html
    assert f"/admin/tools/communities/{_cid}/profile" not in html
    assert "Mark reviewed" in html or "Flag for review" in html  # verification status stays


def test_old_profile_routes_are_gone(client):
    cid, _slug = _community()
    assert client.get(f"/admin/tools/communities/{cid}/profile").status_code == 404
    assert client.post(f"/admin/tools/communities/{cid}/profile", data={}).status_code in (404, 405)


def test_group_names_come_from_one_place():
    titles = [g[0] for g in compare.community_admin_groups()]
    assert titles == [compare.GROUP_TARGET_AUDIENCE, compare.GROUP_MEMBER_EXPERIENCE,
                      compare.GROUP_ECONOMICS, compare.GROUP_KEY_POINTS,
                      compare.GROUP_ADDITIONAL_BENEFITS]
    sizes = {t: len(rows) for t, rows in compare.community_admin_groups()}
    # CPE eligible moved to Program details (2a.1), so Additional benefits has two.
    assert sizes[compare.GROUP_ADDITIONAL_BENEFITS] == 2
    assert all(n >= 2 for n in sizes.values())


def test_generated_output_carries_no_retired_field(app_module):
    from linklib.enrich import COMMUNITY_PROFILE_FIELDS, CommunityProfileDraft
    for f in RETIRED_PROFILE_FIELDS:
        assert f not in COMMUNITY_PROFILE_FIELDS
        assert not hasattr(CommunityProfileDraft, f) or f not in CommunityProfileDraft.__dataclass_fields__
    assert len(COMMUNITY_PROFILE_FIELDS) == 15


# -- directory card, public page, search ------------------------------------

def test_directory_blurb_is_the_bottom_line_and_no_profile_community_renders(client):
    _community(name="Has Profile", url="https://has.example", verdict_summary="Bottom line blurb here.")
    _community(name="No Profile", url="https://none.example", profile=False)
    r = client.get("/tools/communities")
    assert r.status_code == 200
    assert "Bottom line blurb here." in r.text
    assert "OLD demographic" not in r.text and "OLD notes" not in r.text


def test_directory_list_is_a_single_query(app_module):
    lib = _lib()
    for i in range(6):
        cid = lib.add_community(f"C{i}", f"https://c{i}.example", "", "Free", [], approved=1)
        if i % 2:
            lib.upsert_community_profile(cid, verdict_summary=f"BL {i}")
    seen = []
    lib.conn.set_trace_callback(lambda q: seen.append(q))
    rows = lib.list_communities_for_directory()
    lib.conn.set_trace_callback(None)
    lib.close()
    assert len(rows) == 6
    selects = [q for q in seen if q.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects
    assert sum(1 for r in rows if r["bottom_line"]) == 3
    assert all(r["bottom_line"] is None for r in rows if not r["bottom_line"])


def test_public_page_has_no_retired_content_and_new_structure(client):
    _cid, slug = _community(verdict_summary="The bottom line text.",
                            public_criticism="A real trade-off.", notable_members="Notable folks.",
                            resources_included="Templates.", jobs_program="Job board.")
    html = client.get(f"/tools/communities/{slug}").text
    assert "The bottom line text." in html
    for g in ("Target audience", "Member experience", "Economics", "Key points", "Additional benefits"):
        assert g in html, g
    assert "Trade-offs to weigh" in html
    for gone in ("OLD demographic", "OLD notes", "OLD cost note", "OLD event", "OLD platform"):
        assert gone not in html, gone


# -- boot, matchmaker --------------------------------------------------------

def test_boot_opens_no_seed_disagreement_for_frozen_columns(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    from scripts.seed_communities import COMMUNITIES
    seed = COMMUNITIES[0]
    lib = Library(db)
    cid = lib.add_community(seed["name"], seed["url"], "frozen demographic", "Free", [],
                            approved=1, notes="a divergent frozen note", cost_note="frozen cost")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app):
        pass
    lib = Library(db)
    items = [i for i in lib.list_voice_review_queue()
             if i["table_name"] == "communities" and i["row_id"] == str(cid)
             and i["rule"] == "seed-disagreement"
             and i["column_name"] in ("notes", "demographic", "cost_note")]
    row = lib.get_community(cid)
    lib.close()
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)
    assert items == []
    assert row["notes"] == "a divergent frozen note"


def test_matchmaker_context_gives_ideal_member_and_no_retired_lines(app_module):
    from linklib.matchmaker import _build_communities_context
    _community(ideal_member="Controllers at biotech startups.", cpe_eligible="Yes")
    lib = _lib()
    ctx, _unverified = _build_communities_context(lib)
    lib.close()
    assert "Ideal member: Controllers at biotech startups." in ctx
    for gone in ("OLD demographic", "OLD cost note", "OLD notes", "Who it's for:", "Cost detail:",
                 "Notes:", "Founded:"):
        assert gone not in ctx, gone


# -- Compare -----------------------------------------------------------------

def test_compare_sections_and_no_orphan_bands(app_module):
    cid1, _ = _community(name="A", url="https://a.example", verdict_summary="BL A.",
                         public_criticism="TO A.", resources_included="R A.", jobs_program="J A.")
    cid2, _ = _community(name="B", url="https://b.example", profile=False)
    lib = _lib()
    comms = [lib.get_community(cid1), lib.get_community(cid2)]
    profs = {cid1: lib.get_community_profile(cid1) or {}, cid2: {}}
    lib.close()
    ents, _tags = compare.build_communities_compare(comms, profs, {}, {})
    titles = [s["title"] for s in ents[0]["sections"]] if isinstance(ents[0], dict) else \
        [s.title for s in ents[0].sections]
    assert titles[0] == "Bottom line"
    assert compare.GROUP_KEY_POINTS in titles and compare.GROUP_ADDITIONAL_BENEFITS in titles
    keys = ents[0]["key_facts"] if isinstance(ents[0], dict) else ents[0].key_facts
    labels = [k["label"] if isinstance(k, dict) else k.label for k in keys]
    assert "Founded" not in labels and "Cost detail" not in labels


# -- Restore previous (real Chromium) ----------------------------------------

def _chromium_path():
    for p in ("/opt/pw-browsers/chromium", *sorted(
            str(x) for x in pathlib.Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))):
        if os.path.exists(p) and os.path.isfile(p):
            return p
    return None


def test_restore_previous_restores_text_citations_and_drafted_state(client):
    sync_api = pytest.importorskip("playwright.sync_api")
    exe = _chromium_path()
    if not exe:
        pytest.skip("no Chromium available for the Restore previous check")
    cid, slug = _community(ideal_member="ORIGINAL ideal member.", value_prop="ORIGINAL value prop.")
    html = client.get(f"/tools/communities/{slug}/edit").text
    gen = {"ok": True, "model": "claude-opus-5", "low_confidence": False,
           "citations": [{"n": 1, "title": "Fresh", "url": "https://fresh.example", "type": "tool_page"}],
           "confidence": {"ideal_member": True}, "cpe_eligible": "No",
           **{f: f"GENERATED {f} [1]" for f in PROFILE_LIMITS}}
    import json
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=exe)
        except Exception as e:  # pragma: no cover
            pytest.skip(f"Chromium would not launch: {e}")
        page = browser.new_page()
        base = "http://test.local"
        page.route("**/*", lambda route: route.abort())
        page.route(f"{base}/edit", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.route(f"{base}/admin/tools/communities/generate-profile",
                   lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(gen)))
        page.goto(f"{base}/edit")
        page.evaluate("generateCommunityProfile('Chief','https://chief.com','cp-gen-status','cp-gen-err',null)")
        page.wait_for_function("document.getElementById('cp-ideal_member').value.indexOf('GENERATED')===0")
        assert page.eval_on_selector("#ai-drafted-citations", "e=>e.value").find("fresh.example") != -1
        assert "ideal_member" in page.eval_on_selector("#ai-drafted-fields", "e=>e.value")
        assert page.is_visible("#cp-restore-btn")
        # keyboard reachable, with a visible label
        assert page.eval_on_selector("#cp-restore-btn", "e=>e.textContent.trim().length>0 && e.tabIndex>=0")
        page.focus("#cp-restore-btn")
        page.keyboard.press("Enter")
        assert page.input_value("#cp-ideal_member") == "ORIGINAL ideal member."
        assert page.input_value("#cp-value_prop") == "ORIGINAL value prop."
        assert page.eval_on_selector("#ai-drafted-citations", "e=>e.value") == ""
        assert page.eval_on_selector("#ai-drafted-fields", "e=>e.value") == ""
        assert not page.is_visible("#cp-restore-btn")
        browser.close()


def test_cpe_note_input_lives_inside_the_cpe_control_and_saves(client):
    cid, slug = _community()
    html = client.get(f"/tools/communities/{slug}/edit").text
    assert 'id="cp-cpe_note"' in html and 'value="NASBA sponsor"' in html
    assert 'data-char-limit="60"' in html and 'data-char-target="40"' in html
    _post(client, slug, cpe_eligible="No", cpe_note="Not offered")
    lib = _lib()
    assert lib.get_community_profile(cid)["cpe_eligible"] == "No (Not offered)"
    lib.close()


def test_cpe_note_over_max_is_refused_and_named_in_the_banner(client):
    cid, slug = _community()
    r = _post(client, slug, cpe_note="n" * 61)
    assert r.status_code == 400
    assert "CPE eligible note" in r.text and "61 characters, limit 60" in r.text
    lib = _lib()
    assert lib.get_community_profile(cid)["cpe_eligible"] == "Yes (NASBA sponsor)"
    lib.close()
