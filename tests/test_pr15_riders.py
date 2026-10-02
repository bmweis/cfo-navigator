"""PR 15 riders: Software/Communities admin table structural unification
(Rider 1), the three PR-11 hand-picked-width tables joining the standard
column-count buckets (Rider 2), and the /admin/emails fresh-DB 500 fix
(Rider 3).

See CLAUDE.md's PR 15 entry and BRAND.md §5 for the full write-up.
"""
import os
import tempfile

import pytest

from webapp.app import _TABLE_FLOOR_NARROW, _TABLE_FLOOR_MEDIUM, _TABLE_FLOOR_XWIDE


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
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


# --- Rider 1: Software and Communities admin tables are structurally --------
# identical now (same floor, same sticky Name-column width), not merely
# similar-looking exceptions computed independently.

def test_software_and_communities_tables_share_the_xwide_floor(env):
    admin = _admin_client(env)
    software = admin.get("/admin/tools/software").text
    communities = admin.get("/admin/tools/communities").text
    assert _TABLE_FLOOR_XWIDE == 960
    needle = f'min-width:{_TABLE_FLOOR_XWIDE}px;border-collapse:collapse;">'
    assert needle in software
    assert needle in communities
    # Neither table's old, individually hand-computed floor survives.
    assert "min-width:820px" not in software
    assert "min-width:880px" not in communities


def test_software_and_communities_tables_share_the_same_name_column_width(env):
    """Both lists pin the same single sticky Name column, sized by one named
    constant (the checkbox sits inside it since Refs 655, B1), so a width change
    can't reach one table and miss the other."""
    admin = _admin_client(env)
    software = admin.get("/admin/tools/software").text
    communities = admin.get("/admin/tools/communities").text
    for html in (software, communities):
        assert 'class="admin-sticky-col"' in html
        assert "width:230px;min-width:230px;max-width:230px" in html


def test_no_other_column_min_width_shrank_at_the_new_shared_floor(env):
    """The Name column widened (Software: 220->280px) while the shared
    floor widened even more (Software: 820->960px, +140; Communities:
    880->960px, +80) — every other column's own declared min-width, on
    the row a real tool/community actually renders, is unchanged, so
    nothing that fit before is squeezed now."""
    lib = env._lib()
    try:
        lib.add_tool("Real Tool", "A description.", "https://real-tool.example.com", categories=[], approved=1)
        lib.add_community("Real Community", "https://real-community.example.com", "CFOs",
                           "Free", categories=[], approved=1)
    finally:
        lib.close()
    admin = _admin_client(env)
    software = admin.get("/admin/tools/software").text
    communities = admin.get("/admin/tools/communities").text
    # Software's Short description column kept its own 260px floor.
    assert "min-width:260px" in software
    # Communities' Format column kept its own declared floor (the Short
    # description column, 320px, was retired in PR 2a).
    assert "min-width:220px" in communities


# --- Rider 2: the three PR-11 hand-picked-620px tables join the real -------
# column-count buckets instead of a one-off value.

def test_software_categories_table_uses_the_narrow_bucket(env):
    admin = _admin_client(env)
    body = admin.get("/admin/tools/software/categories").text
    assert f"min-width:{_TABLE_FLOOR_NARROW}px" in body
    assert "min-width:620px" not in body


def test_community_categories_table_uses_the_medium_bucket(env):
    admin = _admin_client(env)
    body = admin.get("/admin/tools/communities/categories").text
    assert f"min-width:{_TABLE_FLOOR_MEDIUM}px" in body
    assert "min-width:620px" not in body


def test_resources_table_uses_the_medium_bucket(env):
    admin = _admin_client(env)
    body = admin.get("/admin/tools/resources").text
    assert f"min-width:{_TABLE_FLOOR_MEDIUM}px" in body
    assert "min-width:620px" not in body


# --- Rider 3: /admin/emails no longer 500s on a fresh, empty database ------

def test_admin_emails_returns_200_on_a_fresh_database(env):
    admin = _admin_client(env)
    r = admin.get("/admin/emails")
    assert r.status_code == 200
    assert "Library Submission" in r.text


def test_every_internal_email_row_notification_type_has_a_label(env):
    from linklib.email_utils import NOTIFICATION_TYPE_LABELS
    for row in env._INTERNAL_EMAIL_ROWS:
        assert row["notification_type"] in NOTIFICATION_TYPE_LABELS, (
            f"{row['notification_type']!r} has no NOTIFICATION_TYPE_LABELS entry — "
            "/admin/emails will 500 rendering this row."
        )
