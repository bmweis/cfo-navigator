"""Item 4 of Brian's PR #595 review feedback — the bulk-action bar rendered
above each rule group on /admin/voice/review-queue must offer ONLY the
actions valid for that group's actual status, never a mismatched action
(e.g. "Accept selected" — meant for an already-`auto_corrected` row — run
against a still-`open` finding, which writes nothing back and records no
exception, so the same violation reopens on the very next reconciler
pass; see webapp.app._voice_review_group_bulk_actions_html's own
docstring for the reopen-loop incident this scoping closes).

This file asserts the exact rendered button set for each of the three
group types by reading the real HTML response, not just the click-handler
logic — a button that renders but is rejected server-side is still a real
UX bug (it invites exactly the click that caused the incident)."""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


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


def _login_admin(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


# --- Group type 1: a plain `open` group (not bare-ampersand, not --------
# --- seed-disagreement) — only "Allow selected here" -------------------

def test_open_findings_group_shows_allow_here_only(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid1 = lib.add_tool("Buzz Tool One", "This is seamless.", "https://buzz-one.example", [], approved=1)
        lib.add_voice_review_item("tools", tid1, "description", "buzzword", "seamless")
        tid2 = lib.add_tool("Buzz Tool Two", "This is robust.", "https://buzz-two.example", [], approved=1)
        lib.add_voice_review_item("tools", tid2, "description", "buzzword", "robust")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    assert "Allow selected here" in r.text
    assert "Accept selected" not in r.text
    assert "Revert selected" not in r.text
    assert "Use seed selected" not in r.text
    assert "Keep mine selected" not in r.text
    # Not the ampersand rule, so no replace-ampersand bulk button either.
    assert "Replace ampersands with and" not in r.text


# --- Group type 2: an `auto_corrected` group — "Accept selected" / -----
# --- "Revert selected" only, never "Allow selected here" ---------------

def test_auto_corrected_group_shows_accept_and_revert_only(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid1 = lib.add_tool("Corrected Tool One", "Clean copy one.", "https://corrected-one.example", [], approved=1)
        lib.log_voice_correction(
            "tools", tid1, "description",
            before="Copy with a spaced dash -- like this.",
            after="Corrected copy with a fixed dash—like this.",
            source="admin-edit", rule="spaced-em-dash",
        )
        tid2 = lib.add_tool("Corrected Tool Two", "Clean copy two.", "https://corrected-two.example", [], approved=1)
        lib.log_voice_correction(
            "tools", tid2, "description",
            before="Another spaced dash -- example.",
            after="Another corrected dash—example.",
            source="admin-edit", rule="spaced-em-dash",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    assert "Accept selected" in r.text
    assert "Revert selected" in r.text
    assert "Allow selected here" not in r.text
    assert "Use seed selected" not in r.text
    assert "Keep mine selected" not in r.text


# --- Group type 3: a `seed-disagreement` group — "Use seed selected" / -
# --- "Keep mine selected" only ------------------------------------------

def test_seed_disagreement_group_shows_use_seed_and_keep_mine_only(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid1 = lib.add_tool("Seed Tool One", "Live description one.", "https://seed-one.example", [], approved=1)
        lib.add_seed_disagreement_item("tools", tid1, "name", "Seed Tool One (live)", "Seed Tool One (seed)")
        tid2 = lib.add_tool("Seed Tool Two", "Live description two.", "https://seed-two.example", [], approved=1)
        lib.add_seed_disagreement_item("tools", tid2, "name", "Seed Tool Two (live)", "Seed Tool Two (seed)")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    assert "Use seed selected" in r.text
    assert "Keep mine selected" in r.text
    assert "Allow selected here" not in r.text
    assert "Accept selected" not in r.text
    assert "Revert selected" not in r.text


# --- Single-item groups get no bulk bar at all (unchanged threshold) ----

def test_a_single_open_finding_shows_no_bulk_bar(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid = lib.add_tool("Lone Buzz Tool", "This is seamless.", "https://lone-buzz.example", [], approved=1)
        lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "Select all" not in r.text
    assert "Allow selected here" not in r.text


# --- Part C item 1 (column widths), re-verified against the review's -----
# --- own bar: table.column+id on one line, and a correct floor bucket ----

def test_review_queue_table_uses_named_column_widths_and_xwide_floor(env):
    """Field/Source use the sitewide _COL_WIDTH_NAME/_COL_WIDTH_STATUS
    constants (280px/110px), Actions is 280px, and the table's own
    min-width is the documented Extra-wide (960px) exception — not the
    plain 640px a 5-column count would otherwise assign — since Field and
    Actions alone already claim 560px of that count before Detail gets
    anything (see webapp.app._VOICE_TABLE_FLOOR's own comment)."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        tid = lib.add_tool("Width Check Tool", "This is seamless.", "https://width-check.example", [], approved=1)
        lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "width:280px" in r.text
    assert "width:110px" in r.text
    assert "min-width:960px;table-layout:fixed" in r.text
    # A finding's table.column path and its row id are one combined
    # secondary line ("tools.description · id N"), not split across two
    # separate lines the way an earlier round rendered them.
    assert f"tools.description · id {tid}" in r.text


def test_bulk_bar_gating_module_constants_match_app_module(env):
    """Sanity check that _VOICE_TABLE_FLOOR really resolves to
    _TABLE_FLOOR_XWIDE at the module level, not just in the rendered
    string above — guards against the two drifting if either constant is
    ever redefined."""
    assert env._VOICE_TABLE_FLOOR == env._TABLE_FLOOR_XWIDE == 960
    assert env._VOICE_COL_WIDTH_FIELD == env._COL_WIDTH_NAME == 280
    assert env._VOICE_COL_WIDTH_SOURCE == env._COL_WIDTH_STATUS == 110


# --- Part C item 2 (page layout): lede matches the sitewide "descriptive --
# --- lede under an h1" pattern, capped at a readable width -----------------

def test_lede_uses_the_standard_admin_lede_treatment(env):
    """The lede's font-size/color/margin already matched the sitewide
    pattern (e.g. /admin/ai-surfaces' own lede) before this fix; what it was
    missing was max-width:900px, so it stretched the full page-standard
    container width instead of wrapping at a readable measure like every
    other lede of this shape."""
    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert ('<p style="color:var(--muted);margin:4px 0 22px;max-width:900px;'
            'font-size:14px;line-height:1.5;">Every voice-rule finding and '
            "automatic fix lands") in r.text
    assert '<a href="/admin/voice">/admin/voice</a>' in r.text


# --- Part C item 3 (button style): every outline action button is ---------
# --- uniform — no dashed border, no grey text, anywhere on the page --------

def test_no_button_on_the_page_has_a_dashed_border_or_grey_text(env):
    """Confirms the fix is complete, not just applied to "Allow here"/
    "Allow selected here" — three more instances (the row-level "Keep
    mine" button, and the Cancel button inside both the Allow-everywhere
    and Edit reveal panels) still carried `color:var(--muted)` before this
    fix; none of the three were named explicitly in the brief's own bullet
    list, but the brief's own opening sentence ("Every action button,
    row-level and bulk, uses the same outlined style...") covers them."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid1 = lib.add_community("Style Check Co", "https://style-check-1.example",
                                  "Finance & Operations leaders", "", [])
        lib.add_voice_review_item("communities", cid1, "demographic", "bare-ampersand",
                                   "Finance & Operations leaders")
        tid1 = lib.add_tool("Style Check Tool One", "Live description one.",
                             "https://style-check-seed-1.example", [], approved=1)
        lib.add_seed_disagreement_item("tools", tid1, "name", "Live name", "Seed name")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "border:1px dashed var(--line);color:var(--muted)" not in r.text
    # The row-level style resolves to exactly this string once interpolated
    # (see webapp.app's `btn_style = "font-size:12px;padding:5px 10px;"`) —
    # checking the REAL rendered CSS, not the unresolved `{btn_style}`
    # template placeholder, which never appears in server output at all and
    # so can never actually catch this bug.
    assert 'style="font-size:12px;padding:5px 10px;color:var(--muted);"' not in r.text
    # Every remaining Cancel/Keep mine button on the page shares the plain
    # outlined style, not a muted variant.
    assert ">Keep mine</button>" in r.text
    assert '<button type="submit" class="btn btn-ghost" style="font-size:12px;padding:5px 10px;">Keep mine</button>' in r.text
    assert r.text.count(">Cancel</button>") >= 1


def test_only_approve_and_save_edit_are_filled_buttons(env):
    """"The only filled buttons are the confirm steps inside a panel
    (Approve, Save edit)." — before this fix, "Save edit" was still an
    outlined btn-ghost button, not filled, so this asserts both are
    filled (plain class="btn", no "btn-ghost") and nothing else is."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Filled Button Co", "https://filled-button.example",
                                 "Finance & Operations leaders", "", [])
        lib.add_voice_review_item("communities", cid, "demographic", "bare-ampersand",
                                   "Finance & Operations leaders")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert '<button type="submit" class="btn" style="font-size:12px;padding:5px 10px;">Approve</button>' in r.text
    assert '<button type="submit" class="btn" style="font-size:12px;padding:5px 10px;">Save edit</button>' in r.text
    # No other class="btn" (filled, non-ghost) button appears anywhere —
    # match `class="btn"` NOT immediately followed by another class token
    # (so `class="btn btn-ghost"` is correctly excluded).
    import re
    filled = re.findall(r'class="btn" style="[^"]*">([^<]*)</button>', r.text)
    assert set(filled) <= {"Approve", "Save edit"}
    assert len(filled) == 2


# --- Part C item 4 (labels): the exact wording pair and panel caption -----

def test_allow_everywhere_button_and_caption_use_brians_exact_wording(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Label Check Co", "https://label-check.example",
                                 "Finance & Operations leaders", "", [])
        lib.add_voice_review_item("communities", cid, "demographic", "bare-ampersand",
                                   "Finance & Operations leaders")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert ">Allow here</button>" in r.text
    assert ">Allow everywhere</button>" in r.text
    assert ">Approve term</button>" not in r.text
    assert ("Keep only the exact term, like Dun &amp; Bradstreet. It'll be "
            'allowed everywhere. Remove it anytime on <a href="/admin/voice">'
            "/admin/voice</a>.") in r.text
