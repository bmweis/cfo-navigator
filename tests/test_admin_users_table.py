"""Admin Users page: table redesign + MCP user setup docs.

/admin/users moves from a one-card-per-user layout to the same standard
admin-table convention already used by /admin/tools/software and
/admin/tools/communities (checkbox select, a column picker, client-side
sort/filter, a "Delete selected" bulk action) — see CLAUDE.md's admin-table
note. This is a layout change, not a feature change: every existing
per-user action (profile edit, Ask/Matchmaker cap override, password reset,
role toggle, active toggle, single-row delete, the pending-password-reset
notice) has to keep working identically, reachable now via a per-row
"Manage" button instead of a card's own header button. The one deliberately
new mechanism is bulk delete (approved scope: "Delete selected" only, no
bulk "Edit selected" — role/active toggles carry a last-active-admin guard
that's inherently per-row, and a bulk version of it risks a silent
partial-failure mode, per Brian's own call).

Also new: a collapsible "How to set up a new MCP user" disclosure block
(reusing the exact <details>/<summary> markup already established on
/admin/tools/communities), documenting the full account-creation ->
cap-raise -> `scripts/mint_api_token.py` -> connector-setup flow.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

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


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _seed_users(appmod):
    lib = appmod._lib()
    try:
        lib.create_user("jane", "supersecretpw", role="user", name="Jane Doe", email="jane@example.com")
        lib.create_user("bob", "supersecretpw", role="admin")
        jane_id = lib.get_user("jane")["id"]
        bob_id = lib.get_user("bob")["id"]
    finally:
        lib.close()
    return jane_id, bob_id


# ---------------------------------------------------------------------------
# 1. Table shell — same convention as Software/Communities.
# ---------------------------------------------------------------------------

def test_page_uses_standard_admin_table_shell(env):
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    # Column picker + client-side sort/filter toolbar (shared helpers also
    # used by /admin/tools/software and /admin/tools/communities).
    assert "initColPicker('users'" in body
    assert "applySortFilter('users')" in body
    assert 'id="users-sort-field"' in body
    assert 'id="users-filter-search"' in body
    # Checkbox select + the required tbody id the shared sort/filter JS
    # hardcodes ({tableKey}-approved-tbody).
    assert 'class="users-row-cb"' in body
    assert 'id="users-approved-tbody"' in body
    assert "selectAllRows('users'" in body


def test_delete_selected_present_but_no_edit_selected(env):
    """Approved scope: bulk delete only. No bulk "Edit selected" button/panel
    for Users — unlike Software/Communities, which do offer one."""
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert 'id="users-bulk-delete-btn"' in body
    assert "Delete selected (0)" in body
    # No "Edit selected" trigger button for Users — the generic id is absent
    # entirely, even though the shared _ADMIN_BULK_EDIT_JS (which defines
    # updateBulkButton's own "Edit selected" text for whichever table does
    # have one) is still included on every admin-table page regardless.
    assert 'id="users-bulk-btn"' not in body
    assert 'onclick="openBulkPanel(\'users\')"' not in body


def test_column_picker_lists_optional_columns(env):
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    for col in ("users:realname", "users:email", "users:last_login", "users:ask", "users:matchmaker"):
        assert f'data-col="{col}"' in body


def test_rows_carry_username_role_and_status(env):
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "jane" in body and "bob" in body
    assert 'data-role="admin"' in body      # bob
    assert 'data-role="member"' in body     # jane
    assert 'data-status="active"' in body


def test_empty_state_when_no_users(env):
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "No accounts yet. Create one below." in body


# ---------------------------------------------------------------------------
# 2. Per-user actions still work identically post-redesign.
# ---------------------------------------------------------------------------

def test_manage_button_and_panel_present_per_row(env):
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert f"toggleManage({jane_id})" in body
    assert f'id="manage-{jane_id}"' in body
    assert "Manage jane" in body
    # The panel still posts to the same routes as before the redesign.
    assert f'action="/admin/users/{jane_id}/edit"' in body
    assert f'action="/admin/users/{jane_id}/ask-cap"' in body
    assert f'action="/admin/users/{jane_id}/matchmaker-cap"' in body
    assert f'action="/admin/users/{jane_id}/password"' in body
    assert f'action="/admin/users/{jane_id}/role"' in body
    assert f'action="/admin/users/{jane_id}/toggle"' in body
    assert f'action="/admin/users/{jane_id}/delete"' in body


def test_ask_cap_override_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/ask-cap", data={"cap": "12.50"})
    lib = env._lib()
    try:
        assert lib.get_user("jane")["ask_cap_usd"] == 12.5
    finally:
        lib.close()
    body = admin.get("/admin/users").text
    assert "$12.50" in body and "override" in body


def test_matchmaker_cap_override_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/matchmaker-cap", data={"cap": "3.00"})
    lib = env._lib()
    try:
        assert lib.get_user("jane")["matchmaker_cap_usd"] == 3.0
    finally:
        lib.close()


def test_active_toggle_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/toggle")
    lib = env._lib()
    try:
        assert lib.get_user("jane")["active"] == 0
    finally:
        lib.close()
    body = admin.get("/admin/users").text
    assert 'data-status="disabled"' in body


def test_role_toggle_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/role")
    lib = env._lib()
    try:
        assert lib.get_user("jane")["role"] == "admin"
    finally:
        lib.close()


def test_password_reset_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/password", data={"password": "brandnewpw123"})
    lib = env._lib()
    try:
        assert lib.authenticate("jane", "brandnewpw123") is not None
    finally:
        lib.close()


def test_single_row_delete_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/delete")
    lib = env._lib()
    try:
        assert lib.get_user("jane") is None
    finally:
        lib.close()


def test_pending_password_reset_notice_renders_in_row(env):
    jane_id, _ = _seed_users(env)
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "Requested a password reset" in body
    assert f'action="/admin/users/{jane_id}/password-reset/dismiss"' in body


def test_edit_profile_form_still_works(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/edit",
               data={"username": "jane", "name": "Jane R. Doe", "email": "jane2@example.com"})
    lib = env._lib()
    try:
        u = lib.get_user("jane")
        assert u["name"] == "Jane R. Doe"
        assert u["email"] == "jane2@example.com"
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 3. Bulk delete — the one new mechanism, with the last-active-admin guard
#    generalized to a batch (never a silent partial failure: a blocked row
#    is reported back explicitly, not dropped or allowed).
# ---------------------------------------------------------------------------

def test_bulk_delete_check_allows_deleting_a_member(env):
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete-check", json={"ids": [jane_id]})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["users"] == [{"id": jane_id, "username": "jane"}]
    assert data["blocked"] == []


def test_bulk_delete_check_blocks_the_last_active_admin(env):
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete-check", json={"ids": [bob_id]})
    data = r.json()
    assert data["users"] == []
    assert len(data["blocked"]) == 1
    assert data["blocked"][0]["username"] == "bob"
    assert "admin" in data["blocked"][0]["reason"]


def test_bulk_delete_check_allows_admin_when_another_admin_remains(env):
    lib = env._lib()
    try:
        lib.create_user("carl", "supersecretpw", role="admin")
    finally:
        lib.close()
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete-check", json={"ids": [bob_id]})
    data = r.json()
    # carl (unselected) is still an active admin, so bob is deletable.
    assert {"id": bob_id, "username": "bob"} in data["users"]
    assert data["blocked"] == []


def test_bulk_delete_actually_deletes_selected_members(env):
    jane_id, bob_id = _seed_users(env)
    lib = env._lib()
    try:
        lib.create_user("amy", "supersecretpw", role="user")
        amy_id = lib.get_user("amy")["id"]
    finally:
        lib.close()
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete", json={"ids": [jane_id, amy_id]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 2}
    lib = env._lib()
    try:
        assert lib.get_user("jane") is None
        assert lib.get_user("amy") is None
        assert lib.get_user("bob") is not None
    finally:
        lib.close()


def test_bulk_delete_skips_the_last_active_admin_without_erroring(env):
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete", json={"ids": [jane_id, bob_id]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 1}   # jane deleted, bob skipped
    lib = env._lib()
    try:
        assert lib.get_user("jane") is None
        assert lib.get_user("bob") is not None
    finally:
        lib.close()


def test_bulk_delete_check_requires_auth(env):
    _seed_users(env)
    c = _client(env)
    r = c.post("/admin/users/bulk-delete-check", json={"ids": [1]})
    assert r.status_code == 401


def test_bulk_delete_requires_auth(env):
    _seed_users(env)
    c = _client(env)
    r = c.post("/admin/users/bulk-delete", json={"ids": [1]})
    assert r.status_code == 401


def test_bulk_delete_check_rejects_empty_ids(env):
    admin = _admin_client(env)
    r = admin.post("/admin/users/bulk-delete-check", json={"ids": []})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 4. The reused Communities disclosure component + the MCP setup copy.
# ---------------------------------------------------------------------------

def test_disclosure_block_reuses_communities_markup_pattern(env):
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert '<span class="disclosure-caret">&#9654;</span>How to set up a new MCP user' in body
    # Same wrapping <details> style as /admin/tools/communities' own block.
    assert re.search(
        r'<details style="margin:0 0 24px;border:1px solid var\(--line\);'
        r'border-radius:12px;padding:14px 18px;background:var\(--bg\);">',
        body,
    )


def test_mcp_setup_copy_covers_all_four_steps(env):
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "Create the account." in body
    assert "Raise their Ask/Matchmaker cap" in body
    assert "no other format" in body  # Bearer <token> format callout
    assert "unlimited" in body
    assert "python -m scripts.mint_api_token --db /data/library.db --username" in body
    assert "https://mcp.bmweis.com/mcp" in body
    assert "authorization" in body
    assert "Bearer &lt;token&gt;" in body
    assert "railway ssh" in body
    # Brian's approved addition: how the temp password actually reaches
    # the new user (automatic welcome email, or shared directly).
    assert "welcome email" in body
    assert "share it with them directly" in body


def test_mcp_setup_copy_does_not_hardcode_default_cap_amounts(env):
    """Default caps are admin-editable (/admin/users' own "Save default"
    forms) — the instructional copy must describe them generically rather
    than embedding a number that could drift out of sync (Step 0 item 5)."""
    setup_html = webapp_mcp_setup_html(env)
    assert "$5" not in setup_html and "$2" not in setup_html
    assert "5.00" not in setup_html and "2.00" not in setup_html


def webapp_mcp_setup_html(appmod):
    return appmod._MCP_USER_SETUP_HTML


def test_mint_api_token_invocation_matches_the_real_script(env):
    """scripts/mint_api_token.py is a module, invoked without a .py suffix —
    confirmed against the script's own module-invocation docstring."""
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "python -m scripts.mint_api_token.py" not in body
    assert "python -m scripts.mint_api_token " in body


# ---------------------------------------------------------------------------
# 5. Rendered <script> blocks must be valid JS — never approximated from
#    app.py's own source text (CLAUDE.md's standing lesson). This page's
#    bespoke bulk-delete/manage-toggle script is inline, not a module-level
#    *_JS constant, so it isn't covered by webapp.checks.script_syntax_problems.
# ---------------------------------------------------------------------------

def test_rendered_admin_users_script_parses_as_valid_js(env):
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    scripts = re.findall(r"<script>(.*?)</script>", body, re.S)
    assert scripts, "admin/users page should carry at least one inline script block"
    for i, js in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            p = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert p.returncode == 0, f"admin/users script block {i} failed to parse:\n{p.stderr}"
        finally:
            os.unlink(path)


def test_admin_users_requires_auth(env):
    c = _client(env)
    r = c.get("/admin/users", follow_redirects=False)
    assert r.status_code == 303
