"""Public /tools/software directory card's admin-only Delete affordance.

Regression coverage for a real bug: the Delete <form> baked into
`tools_directory()`'s inline JS (webapp/app.py's renderTools()) posted to
`/admin/tools/{id}/delete` — missing the "software/" path segment the real
route (`admin_tools_delete`, `/admin/tools/software/{tool_id}/delete`)
actually lives at. The form was already a real POST (not a stray GET link,
as first suspected) with a confirm() dialog and a correctly-attributed
tool id; it just posted to a URL FastAPI has no route for, so the browser
navigated straight into a plain 404 instead of completing the delete.

Fixed by correcting the action URL to match the exact path the admin
table's own working Delete button (/admin/tools/software, `_tool_row`)
already uses — same route, same mechanism, no new one invented.

/tools/communities has no equivalent affordance at all: its public-card
renderer (`renderCommunities`) never gates on AUTHED and never renders
edit/delete controls, so there was nothing to fix there. Confirmed here so
that fact doesn't silently go unverified in the future.
"""
import json
import os
import re
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


def test_public_directory_delete_form_posts_to_the_real_route(admin_client):
    """The rendered page's delete <form> must point at the actual route."""
    client, appmod, db = admin_client
    public = client.get("/tools/software")
    assert public.status_code == 200
    body = public.text

    fixed = "action=\"/admin/tools/software/' + t.id + '/delete\""
    broken = "action=\"/admin/tools/' + t.id + '/delete\""
    assert fixed in body, "delete form should post to /admin/tools/software/{id}/delete"
    assert broken not in body, "the old, route-less action pattern must not reappear"


def test_old_broken_url_genuinely_404s(admin_client):
    """Confirms the real root cause: a missing path segment, not GET vs POST."""
    client, appmod, db = admin_client
    lib = appmod._lib()
    try:
        tid = lib.add_tool(
            "Disposable Test Tool", "A disposable test description",
            "https://disposable.example.com", ["FP&A"], approved=1,
        )
    finally:
        lib.close()

    # The pre-fix action, reconstructed exactly as it used to render.
    broken_resp = client.post(f"/admin/tools/{tid}/delete", data={}, follow_redirects=False)
    assert broken_resp.status_code == 404


def test_delete_via_public_directory_affordance_works_end_to_end(admin_client):
    """Real repro: create a disposable tool, delete it via the fixed
    mechanism (the exact route + form field shape the public card now
    renders), confirm it's actually gone — cascade cleanup included."""
    client, appmod, db = admin_client
    lib = appmod._lib()
    try:
        tid = lib.add_tool(
            "Disposable Test Tool 2", "A disposable test description",
            "https://disposable2.example.com", ["FP&A"], approved=1,
        )
    finally:
        lib.close()

    # Sanity: it's really there and shows up on the public page first.
    public_before = client.get("/tools/software")
    tools_before = json.loads(re.search(r"var ALL_TOOLS = (\[.*?\]);\n", public_before.text, re.S).group(1))
    assert any(t["id"] == tid for t in tools_before)

    resp = client.post(
        f"/admin/tools/software/{tid}/delete",
        data={"redirect_to": "/tools/software"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/tools/software"

    lib2 = appmod._lib()
    try:
        assert lib2.get_tool(tid) is None
    finally:
        lib2.close()

    public_after = client.get("/tools/software")
    tools_after = json.loads(re.search(r"var ALL_TOOLS = (\[.*?\]);\n", public_after.text, re.S).group(1))
    assert not any(t["id"] == tid for t in tools_after)


def test_delete_still_requires_auth(admin_client):
    """The fix must not weaken the existing auth check on this route."""
    client, appmod, db = admin_client
    lib = appmod._lib()
    try:
        tid = lib.add_tool(
            "Disposable Test Tool 3", "A disposable test description",
            "https://disposable3.example.com", ["FP&A"], approved=1,
        )
    finally:
        lib.close()

    client.get("/logout")
    resp = client.post(
        f"/admin/tools/software/{tid}/delete",
        data={"redirect_to": "/tools/software"},
        follow_redirects=False,
    )
    assert resp.status_code == 401

    lib2 = appmod._lib()
    try:
        assert lib2.get_tool(tid) is not None
    finally:
        lib2.close()


def test_communities_public_directory_has_no_admin_delete_affordance(admin_client):
    """Communities' public card renderer never gates on AUTHED at all —
    confirmed rather than assumed identical to Software's (now-fixed) bug."""
    client, appmod, db = admin_client
    public = client.get("/tools/communities")
    assert public.status_code == 200
    body = public.text
    assert "tool-admin-del" not in body
    render_fn = body.split("function renderCommunities")[1].split("function commFiltered")[0]
    assert "delete" not in render_fn.lower()
    # AUTHED only picks the wording of the Bottom line "under review" tag
    # (PR 2a); it never gates an edit/delete control.
    assert render_fn.count("AUTHED") == 1
    assert "Edit" not in render_fn
