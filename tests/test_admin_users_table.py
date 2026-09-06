"""Admin Users page: table redesign, direct-edit rows, and MCP user setup docs.

/admin/users moves from a one-card-per-user layout to the same standard
admin-table convention already used by /admin/tools/software and
/admin/tools/communities (checkbox select, a column picker, client-side
sort/filter, a "Delete selected" bulk action) — see CLAUDE.md's admin-table
note. This is a layout change, not a feature change: every existing
per-user action (profile edit, Ask/Matchmaker cap override, password reset,
role toggle, active toggle, single-row delete, the pending-password-reset
notice) has to keep working identically.

Direct-edit follow-up: the separate "Manage {user}" click-through panel is
retired entirely. Full name/Email are inline fields saved directly in the
row (one shared form, since posting only one of the two would blank the
other — admin_users_edit writes whatever it's handed); FP&A Buddy/Matchmaker
caps get an inline input + Set button each; password reset is an inline
field + button in the Actions column; Username stays read-only (it's the
login identifier); Access level/Status render as their own badge columns,
with the actual mutating actions (Make admin/member, Disable/Enable,
password reset, Delete) bundled into the Actions column instead of living
beside each badge. "Add a member" also moves to the top of the page, beside
the two dollar-cap default forms (2/3 + 1/3 width). The one deliberately new
mechanism (unchanged by the direct-edit follow-up) is bulk delete (approved
scope: "Delete selected" only, no bulk "Edit selected" — role/active toggles
carry a last-active-admin guard that's inherently per-row, and a bulk version
of it risks a silent partial-failure mode, per Brian's own call).

Mobile-tidiness follow-up: FP&A Buddy cap and Matchmaker cap merged into one
"Usage limits" column/mobile-card section — matching how Software/
Communities' own mobile cards group related info under a single label
(e.g. Review status: one label, a badge and its action button together)
rather than a full separate section per field.

Also: a collapsible "How to set up a new MCP user" disclosure block (reusing
the exact <details>/<summary> markup already established on
/admin/tools/communities), documenting the full account-creation ->
cap-raise -> `scripts/mint_api_token.py` -> connector-setup flow.
"""
import json
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
    for col in ("users:realname", "users:email", "users:password", "users:last_login",
                "users:access_level", "users:status", "users:ask", "users:matchmaker"):
        assert f'data-col="{col}"' in body


def test_optional_columns_default_visible(env):
    """The 'expand the table' ask reads as show-by-default, not hidden
    behind the picker — every optional column starts checked."""
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    for col in ("realname", "email", "password", "last_login", "access_level", "status", "ask", "matchmaker"):
        assert f'id="colpick-users-{col}" checked' in body


def test_initcolpicker_call_passes_the_real_default_visible_list(env):
    """Real bug, caught by a live mobile screenshot check, not by the
    server-rendered-checkbox test above: initColPicker()'s own fallback (used
    whenever nothing's saved in localStorage yet — i.e. every first visit)
    unconditionally overwrote every checkbox AND every column's actual
    display to the single shared ADMIN_DEFAULT_VISIBLE_COLS ('review_status')
    regardless of what the server rendered as checked — Users has no
    'review_status' column, so every optional column silently rendered
    hidden on load despite this page's own checkboxes showing checked. Fixed
    by passing this page's real default-visible list as initColPicker's new
    third argument; this test pins the actual JS call, not just the
    server-rendered (and, pre-fix, misleading) checkbox markup."""
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    m = re.search(r"initColPicker\('users',\s*(\[.*?\]),\s*(\[.*?\])\);", body)
    assert m, "initColPicker('users', ...) call not found with a third argument"
    cols = json.loads(m.group(1))
    default_visible = json.loads(m.group(2))
    for col in ("realname", "email", "password", "last_login", "access_level", "status", "ask", "matchmaker"):
        assert col in cols
        assert col in default_visible


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

def test_manage_panel_is_retired(env):
    """The separate "Manage {user}" click-through panel is gone — every
    field it used to hold is edited directly in the row now."""
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "toggleManage(" not in body
    assert 'id="manage-' not in body
    assert "Manage jane" not in body
    assert 'class="user-manage-panel"' not in body


def test_row_posts_to_all_the_same_action_routes_inline(env):
    """2026-09 cap-consolidation follow-up: the two cap fields no longer
    have their own <form action="...ask-cap"/"...matchmaker-cap">
    — they're part of the shared profile-form now (form= attribute), so
    those two actions are gone from this list; every other action still
    posts to its own unchanged route."""
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert f'action="/admin/users/{jane_id}/edit"' in body
    assert f'action="/admin/users/{jane_id}/ask-cap"' not in body
    assert f'action="/admin/users/{jane_id}/matchmaker-cap"' not in body
    assert f'action="/admin/users/{jane_id}/password"' in body
    assert f'action="/admin/users/{jane_id}/role"' in body
    assert f'action="/admin/users/{jane_id}/toggle"' in body
    assert f'action="/admin/users/{jane_id}/delete"' in body


def test_username_is_read_only_no_input(env):
    """Decision: username stays display-only — an accidental inline edit
    of the login identifier is a bigger footgun than the convenience is
    worth. Full name/Email get real <input>s; username does not."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    # A hidden username field still rides along with the Full name/Email
    # save (the edit route requires it) — but no *editable* username input.
    assert 'type="text" name="username"' not in body
    assert 'type="hidden" name="username" value="jane">' in body
    assert '<span class="user-name">jane</span>' in body


def test_full_name_and_email_share_one_form_via_form_attribute(env):
    """Full name and Email post together in one request (a shared <form>,
    referenced from the Full name cell's <input> via the HTML `form=`
    attribute) — admin_users_edit writes whatever name/email it's given, so
    two independent per-field forms would silently blank whichever field
    wasn't included in a given save."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    form_id = f"profile-form-{jane_id}"
    assert f'<form id="{form_id}"' in body
    assert f'form="{form_id}" name="name"' in body
    assert f'<input type="hidden" name="username" value="jane">' in body


def test_access_level_and_status_badges_carry_their_own_action_form(env):
    """View/edit-mode redesign (2026-09), a real reversal of the original
    judgment call: Make admin/member and Disable/Enable used to live only in
    the Actions column, separate from the Access/Status badges. Brian asked
    for them moved directly under their own badge instead — they're hidden
    (revealed only once "Edit" is clicked) but structurally part of the
    SAME <td> as the badge now, not the Actions cell."""
    jane_id, bob_id = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert 'data-col="users:access_level"' in body
    assert 'data-col="users:status"' in body
    # jane (role=user) gets a "Make admin" action; bob (role=admin) doesn't.
    assert body.count(">Make admin<") == 1
    # Both seeded accounts start active, so both rows offer "Disable".
    assert body.count(">Disable<") == 2
    # The role/toggle forms are hidden by default (view mode) — id-scoped
    # per user, per-field, and both start with the `hidden` attribute.
    assert f'id="role-form-{jane_id}"' in body
    assert f'id="toggle-form-{jane_id}"' in body
    role_form_tag = re.search(rf'<form id="role-form-{jane_id}"[^>]*>', body).group(0)
    toggle_form_tag = re.search(rf'<form id="toggle-form-{jane_id}"[^>]*>', body).group(0)
    assert "hidden" in role_form_tag
    assert "hidden" in toggle_form_tag


def test_ask_cap_override_still_works(env):
    """The standalone /ask-cap route is no longer reachable from the UI
    (2026-09 cap-consolidation follow-up — see the new tests below for the
    consolidated path) but it's still a real, directly-tested route,
    deliberately left in place rather than deleted."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/ask-cap", data={"cap": "12.50"})
    lib = env._lib()
    try:
        assert lib.get_user("jane")["ask_cap_usd"] == 12.5
    finally:
        lib.close()
    body = admin.get("/admin/users").text
    # View/edit-mode redesign (2026-09): no more (default)/(override) note —
    # the cap <input> just always holds the current effective value.
    assert 'value="12.50"' in body


def test_matchmaker_cap_override_still_works(env):
    """Same standalone-route note as test_ask_cap_override_still_works
    above — /matchmaker-cap is unreachable from the UI now but still a
    real, directly-tested route."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/matchmaker-cap", data={"cap": "3.00"})
    lib = env._lib()
    try:
        assert lib.get_user("jane")["matchmaker_cap_usd"] == 3.0
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 2a. Cap-consolidation follow-up (2026-09) — the per-field "Set" button is
#     gone; both cap inputs ride along in the shared profile-form (via
#     `form=`, same trick Name/Email already use), so the row's one Save
#     covers name/email/both caps together. A hidden `{field}_original`
#     sibling guards against an ordinary Name/Email save silently pinning
#     a user's currently-effective (default) cap as an explicit override —
#     see _apply_user_cap_override_from_form in webapp/app.py.
# ---------------------------------------------------------------------------

def test_cap_save_buttons_are_gone(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert f'id="ask-save-{jane_id}"' not in body
    assert f'id="mm-save-{jane_id}"' not in body
    ask_input_tag = re.search(rf'<input[^>]*id="ask-input-{jane_id}"[^>]*>', body).group(0)
    mm_input_tag = re.search(rf'<input[^>]*id="mm-input-{jane_id}"[^>]*>', body).group(0)
    profile_form_id = f"profile-form-{jane_id}"
    assert f'form="{profile_form_id}"' in ask_input_tag
    assert f'form="{profile_form_id}"' in mm_input_tag
    assert f'name="ask_cap"' in ask_input_tag
    assert f'name="matchmaker_cap"' in mm_input_tag


def test_cap_inputs_carry_a_hidden_original_value_in_the_same_form(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    profile_form_id = f"profile-form-{jane_id}"
    assert re.search(
        rf'<input type="hidden" name="ask_cap_original" value="5\.00" form="{profile_form_id}">', body)
    assert re.search(
        rf'<input type="hidden" name="matchmaker_cap_original" value="2\.00" form="{profile_form_id}">', body)


def test_saving_the_row_with_a_changed_cap_applies_the_override(env):
    """Editing the cap input and clicking the row's one Save (the same
    /edit route Name/Email already use) sets a real override — no separate
    Set button needed."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/edit", data={
        "username": "jane", "name": "Jane Doe", "email": "jane@example.com",
        "ask_cap": "12.50", "ask_cap_original": "5.00",
        "matchmaker_cap": "3.00", "matchmaker_cap_original": "2.00",
    })
    lib = env._lib()
    try:
        u = lib.get_user("jane")
        assert u["ask_cap_usd"] == 12.5
        assert u["matchmaker_cap_usd"] == 3.0
    finally:
        lib.close()


def test_saving_the_row_with_an_unchanged_cap_does_not_create_an_override(env):
    """The real bug this guard prevents: a plain Name/Email save always
    resubmits the cap inputs' current (default) value too, since they're
    part of the same form — that must NOT silently convert "follows the
    site default" into a pinned override."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/edit", data={
        "username": "jane", "name": "Jane Updated", "email": "jane@example.com",
        "ask_cap": "5.00", "ask_cap_original": "5.00",
        "matchmaker_cap": "2.00", "matchmaker_cap_original": "2.00",
    })
    lib = env._lib()
    try:
        u = lib.get_user("jane")
        assert u["name"] == "Jane Updated"
        assert u["ask_cap_usd"] is None
        assert u["matchmaker_cap_usd"] is None
    finally:
        lib.close()


def test_clearing_the_cap_input_clears_an_existing_override(env):
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    admin.post(f"/admin/users/{jane_id}/ask-cap", data={"cap": "12.50"})
    admin.post(f"/admin/users/{jane_id}/edit", data={
        "username": "jane", "name": "", "email": "",
        "ask_cap": "", "ask_cap_original": "12.50",
    })
    lib = env._lib()
    try:
        assert lib.get_user("jane")["ask_cap_usd"] is None
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 2b. View/edit-mode redesign (2026-09) — the row is read-only by default;
#     one "Edit" button per row (Actions column) reveals every editable
#     control at once and becomes "Save"; a second click submits Name/Email.
#     A real bug was caught live here, not just by these assertions: the
#     first version turned the button into a real type="submit" tied to the
#     form via the `form=` attribute, which made Chromium submit the form on
#     the SAME click that was only supposed to reveal the fields — the page
#     navigated away before anything was ever shown. Fixed by never mutating
#     the button's type; the second click calls form.requestSubmit()
#     explicitly instead. See toggleUserEdit() in the page's own <script>.
# ---------------------------------------------------------------------------

def test_edit_button_renders_as_type_button_not_submit(env):
    """Guards against reintroducing the type-mutation footgun: the button
    must render (and stay, in markup) as type="button" — submission is
    handled entirely by JS calling form.requestSubmit(), never by the
    browser's native submit-button activation behavior."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert f'type="button" id="edit-btn-{jane_id}"' in body
    assert f'onclick="toggleUserEdit({jane_id}, this)"' in body
    assert f'type="submit" id="edit-btn-{jane_id}"' not in body


def test_hidden_attribute_override_present(env):
    """Real bug, caught live by screenshotting the page BEFORE ever clicking
    Edit: the sitewide `.btn{{display:inline-block}}` rule is an
    author-origin style, which always beats the browser's own
    `[hidden]{{display:none}}` UA-stylesheet rule regardless of selector
    specificity — so every hidden .btn (the cap Save buttons, at minimum)
    rendered visible from first load, before Edit was ever clicked. This
    pins the fix: an explicit `[hidden]{{display:none!important;}}` rule
    that restores the attribute's actual default."""
    _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert "[hidden]" in body and "display:none!important" in body


def test_editable_controls_hidden_by_default(env):
    """The password edit form (input+Reset) starts with the `hidden`
    attribute, only revealed once Edit is clicked — the role/toggle forms
    are covered by test_access_level_and_status_badges_carry_their_own_
    action_form; the cap fields have no Save button to hide any more
    (2026-09 cap-consolidation follow-up — they're readonly inputs, not a
    hidden button, see test_cap_save_buttons_are_gone and
    test_cap_fields_show_current_effective_value_readonly_by_default)."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    pw_edit_tag = re.search(rf'<form id="pw-edit-{jane_id}"[^>]*>', body).group(0)
    assert "hidden" in pw_edit_tag


def test_password_is_its_own_field_not_folded_into_actions(env):
    """Password moves to its own column/field (was tucked into Actions
    before) — view mode shows a plain bullet placeholder, no real value
    (there's nothing to show — passwords are hashed); edit mode reveals the
    same input+Reset button this always had, just relocated."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    assert 'data-col="users:password"' in body
    assert f'id="pw-view-{jane_id}"' in body
    assert f'action="/admin/users/{jane_id}/password"' in body


def test_cap_fields_show_current_effective_value_readonly_by_default(env):
    """No more (default)/(override) note text — the cap <input> just always
    holds the current effective value (default, absent an override) and is
    readonly until Edit is clicked."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    ask_input_tag = re.search(rf'<input[^>]*id="ask-input-{jane_id}"[^>]*>', body).group(0)
    assert 'value="5.00"' in ask_input_tag  # the site default, no override set
    assert "readonly" in ask_input_tag
    assert "(default)" not in body
    assert "(override)" not in body


def test_name_and_email_inputs_readonly_by_default(env):
    """Name/Email join the same view/edit-mode pattern as everything else —
    readonly (view-mode "plain text" look) until Edit reveals them."""
    jane_id, _ = _seed_users(env)
    admin = _admin_client(env)
    body = admin.get("/admin/users").text
    name_tag = re.search(rf'<input[^>]*id="name-input-{jane_id}"[^>]*>', body).group(0)
    email_tag = re.search(rf'<input[^>]*id="email-input-{jane_id}"[^>]*>', body).group(0)
    assert "readonly" in name_tag
    assert "readonly" in email_tag


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
