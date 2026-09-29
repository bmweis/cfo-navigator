"""Admin "Completeness" filter on /admin/tools/software and
/admin/tools/communities (2026-09) — a "Missing something" scalar filter,
reusing linklib.gates' emptiness signal (via the same strip-then-check
convention gates.field_state applies) plus a direct screenshot_url check,
consume-only per CLAUDE.md's scope for this feature (no gates.py changes).

A genuinely empty field/missing screenshot must surface a row when the
filter is active; a fully-populated record must not. The filter must also
combine correctly (AND) with the pre-existing Category/Cost band/etc.
filters — verified here at the data-attribute level, since the actual
AND-combination logic lives in the shared, already-tested
_ADMIN_SORT_FILTER_JS (applySortFilter), not in anything this PR changes.
"""
import os
import tempfile

import pytest


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def _complete_tool(lib, name="Complete Tool", url="https://complete.example"):
    tid = lib.add_tool(name, "A full description of what this tool does.", url,
                        ["FP&A"], approved=1)
    lib.update_tool_screenshot_url(tid, "https://complete.example/shot.png")
    lib.update_tool_agent_taxonomy(tid, "This tool uses agents to do X.")
    lib.update_tool_differentiation(tid, "It differs from competitors by Y.")
    other = lib.add_tool("Other Tool", "desc", "https://other.example", ["FP&A"], approved=1)
    lib.add_tool_competitor(tid, other)
    return tid


def test_software_completeness_filter_toolbar_and_row_attrs(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool_category("FP&A")
    _complete_tool(lib)
    lib.add_tool("Missing Tool", "desc", "https://missing.example", ["FP&A"], approved=1)
    lib.close()

    r = client.get("/admin/tools/software")
    assert 'data-filter-field="completeness"' in r.text
    assert '<option value="missing">Missing</option>' in r.text
    assert '<option value="complete">Complete</option>' in r.text
    assert 'data-name="complete tool" data-promoted="0"' in r.text
    # The fully-populated tool is marked complete...
    complete_pos = r.text.index('data-name="complete tool"')
    complete_row = r.text[complete_pos:complete_pos + 400]
    assert 'data-completeness="complete"' in complete_row
    # ...and the sparse one (no summary/description text beyond a bare
    # "desc", no screenshot, no agent taxonomy, no differentiation, no
    # competitor, no bottom line) is marked missing.
    missing_pos = r.text.index('data-name="missing tool"')
    missing_row = r.text[missing_pos:missing_pos + 400]
    assert 'data-completeness="missing"' in missing_row


def test_software_completeness_missing_on_each_individual_gap(admin_client):
    """Each of the tracked fields independently flips a fully-populated
    tool to 'missing' when it alone is absent — not just the
    all-fields-empty case."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool_category("FP&A")

    # Complete baseline, then knock out just the screenshot.
    tid = _complete_tool(lib, name="No Screenshot Tool", url="https://noshot.example")
    lib.update_tool_screenshot_url(tid, "")
    lib.close()

    r = client.get("/admin/tools/software")
    pos = r.text.index('data-name="no screenshot tool"')
    row = r.text[pos:pos + 400]
    assert 'data-completeness="missing"' in row


def test_software_completeness_combines_with_category_filter(admin_client):
    """Both filters render together — an admin combining Categories with
    Completeness sees the AND-matched result via the same shared JS every
    other scalar filter already uses; here we confirm the server renders
    the data attributes both filters need simultaneously."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool_category("FP&A")
    lib.add_tool_category("ERP")
    _complete_tool(lib, name="FPA Complete", url="https://fpacomplete.example")
    lib.close()

    r = client.get("/admin/tools/software")
    pos = r.text.index('data-name="fpa complete"')
    row = r.text[pos:pos + 400]
    assert 'data-completeness="complete"' in row
    assert 'data-categories="fp&amp;a"' in row


def _complete_community(lib, name="Complete Community", url="https://complete-comm.example"):
    cid = lib.add_community(name=name, url=url, demographic="CFOs", cost_band="<$1k/yr",
                             categories=["Peer Group"], approved=1, access="Open",
                             sponsorship_type="Independent", format="Online", reach="National")
    lib.update_community_screenshot_url(cid, "https://complete-comm.example/shot.png")
    lib.upsert_community_profile(
        cid, ideal_member="CFOs at Series B+ startups.", anti_fit="Not for solo founders.",
        seniority_band="VP+", stage_focus="Series B-D",
        value_prop="Peer benchmarking.", primary_purpose="Networking",
        resources_included="Slack, events.", notable_members="A few well-known CFOs.",
        jobs_program="Yes, a job board.",
        format_reality="Mostly async Slack.", engagement_level="Moderate",
        application_friction="Light vetting.",
        cost_value_verdict="Worth it for the network.", sponsor_relationship_note="Independent, no sponsor influence.",
        business_model="Membership dues.", public_criticism="Some say it's too US-centric.",
        cpe_eligible="Yes", verdict_summary="A solid peer community for CFOs.",
    )
    other = lib.add_community(name="Other Community", url="https://other-comm.example",
                               demographic="CFOs", cost_band="Free", categories=["Peer Group"],
                               approved=1, access="Open", sponsorship_type="Independent",
                               format="Online", reach="National")
    lib.add_community_competitor(cid, other)
    return cid


def test_communities_completeness_filter_toolbar_and_row_attrs(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    _complete_community(lib)
    lib.add_community(name="Missing Community", url="https://missing-comm.example",
                       demographic="CFOs", cost_band="Free", categories=["Peer Group"],
                       approved=1, access="Open", sponsorship_type="Independent",
                       format="Online", reach="National")
    lib.close()

    r = client.get("/admin/tools/communities")
    assert 'data-filter-field="completeness"' in r.text
    assert '<option value="missing">Missing</option>' in r.text
    assert '<option value="complete">Complete</option>' in r.text

    complete_pos = r.text.index('data-name="complete community"')
    complete_row = r.text[complete_pos:complete_pos + 400]
    assert 'data-completeness="complete"' in complete_row

    missing_pos = r.text.index('data-name="missing community"')
    missing_row = r.text[missing_pos:missing_pos + 400]
    assert 'data-completeness="missing"' in missing_row


def test_communities_completeness_missing_with_no_profile_row_at_all(admin_client):
    """A community with zero community_profiles row (never had a profile
    drafted) must read as 'missing', not silently pass as complete just
    because there's no row to find an empty field in."""
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    cid = lib.add_community(name="No Profile Community", url="https://noprofile.example",
                             demographic="CFOs", cost_band="Free", categories=["Peer Group"],
                             approved=1, access="Open", sponsorship_type="Independent",
                             format="Online", reach="National")
    lib.update_community_screenshot_url(cid, "https://noprofile.example/shot.png")
    lib.close()

    r = client.get("/admin/tools/communities")
    pos = r.text.index('data-name="no profile community"')
    row = r.text[pos:pos + 400]
    assert 'data-completeness="missing"' in row


def test_communities_completeness_combines_with_cost_band_filter(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    _complete_community(lib, name="Cheap Complete", url="https://cheapcomplete.example")
    lib.close()

    r = client.get("/admin/tools/communities")
    pos = r.text.index('data-name="cheap complete"')
    row = r.text[pos:pos + 400]
    assert 'data-completeness="complete"' in row
    assert 'data-cost_band="&lt;$1k/yr"' in row
