"""Phase N build: the /admin/system/scripts inventory page. A static,
hand-maintained registry (see _SCRIPT_REGISTRY in webapp/app.py) covering
the "Recurring & actively useful" and "Reusable diagnostic" buckets from the
Phase N investigation — one-time/job-done scripts live in scripts/archive/
and are deliberately NOT listed here.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    resp = c.get("/admin/system/scripts", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)
    assert "/login" in resp.headers.get("location", "")


def test_page_renders_for_authed_admin(env):
    c = _admin_client(env)
    resp = c.get("/admin/system/scripts")
    assert resp.status_code == 200
    assert "Scripts" in resp.text


def test_every_recurring_and_diagnostic_script_is_listed(env):
    c = _admin_client(env)
    body = c.get("/admin/system/scripts").text
    expected = [
        "backfill_logos.py", "capture_tool_screenshots.py", "seed_tools.py",
        "seed_communities.py", "enrich_community_profiles.py", "enrich_agent_taxonomy.py",
        "mcp_server.py", "generate_brand_docs.py", "report_orphaned_categories.py",
        "dump_communities.py", "diagnose_cookie_banner.py", "verify_screenshot_capture.py",
    ]
    for name in expected:
        assert name in body, name


def test_archived_one_time_scripts_are_not_listed(env):
    """The one-time/job-done bucket lives in scripts/archive/ — this page
    only documents the still-relevant scripts, not the archived ones."""
    c = _admin_client(env)
    body = c.get("/admin/system/scripts").text
    archived = [
        "rename_differentiation_columns.py", "migrate_app_screenshot_from_product_flag.py",
        "migrate_software_tags.py", "migrate_domain_slugs.py", "fix_corpay_category.py",
        "patch_community_profiles_copy.py", "report_field_reviews_summary.py",
        "audit_screenshot_is_product_regression.py", "backfill_community_access_format.py",
        "backfill_community_geo.py", "patch_round3_community_profiles.py",
        "patch_round4_community_profiles.py", "report_flagged_categories.py",
        "report_brandfetch_coverage.py", "import_community_profiles.py",
    ]
    for name in archived:
        assert name not in body, name


def test_both_bucket_headings_present(env):
    c = _admin_client(env)
    body = c.get("/admin/system/scripts").text
    assert "Recurring &amp; actively useful" in body
    assert "Reusable diagnostic" in body


def test_registry_entries_have_no_missing_fields(env):
    """Every _SCRIPT_REGISTRY row has a non-empty purpose, cadence, and at
    least one invocation line — catches a copy-paste row left half-filled."""
    for name, module, bucket, purpose, cadence, env_vars, invocation in env._SCRIPT_REGISTRY:
        assert purpose.strip(), name
        assert cadence.strip(), name
        assert invocation, name
        assert bucket in ("Recurring & actively useful", "Reusable diagnostic"), name


def test_admin_hub_links_to_scripts_page(env):
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    assert "/admin/system/scripts" in resp.text


def test_scripts_registry_files_actually_exist_on_disk(env):
    """Guards against the registry drifting from scripts/ on disk — a
    listed script that got renamed or archived without updating this page
    would otherwise go unnoticed."""
    import pathlib as _pathlib
    scripts_dir = _pathlib.Path(__file__).resolve().parents[1] / "scripts"
    for name, module, bucket, purpose, cadence, env_vars, invocation in env._SCRIPT_REGISTRY:
        assert (scripts_dir / name).exists(), name
