"""Route-level coverage for issue #592 item 4 — the Ampersands group's bulk
"Replace & with and" preview/apply routes on /admin/voice/review-queue.
Library-level coverage for the underlying replace_spaced_ampersands /
preview_ampersand_replacement / apply_ampersand_replacement lives in
tests/test_voice_review_queue.py; this file exercises the two HTTP routes
themselves (preview shows a diff and writes nothing, apply commits and
resolves, an empty selection redirects with an error, and a stale/removed
selection at apply time is a safe no-op rather than a crash).

Also covers issue #592's review-round UI feedback (item 7): the Approve
term input must not render inside the default Actions cell, must appear
only inside its own reveal panel, and every row-action button on the page
must share the one `.btn`/`.btn-ghost` class pair (the earlier design had
a filled Approve-term button with `border:none`, which broke height parity
with the outlined buttons next to it)."""
import os
import re
import tempfile

import pytest

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


def test_preview_route_shows_diff_and_writes_nothing(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Preview Co", "https://amp-route-preview.com",
                                 "", "", [], local_markets="Finance & Operations leaders")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "Finance & Operations leaders",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={"item_ids": [str(item_id)]})
    assert r.status_code == 200
    assert "Finance" in r.text
    assert "Operations" in r.text
    assert f'value="{item_id}"' in r.text  # carried forward as a hidden field to the apply form

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["local_markets"] == "Finance & Operations leaders", "preview must never write"
    assert item["status"] == "open"


def test_preview_route_shows_left_as_is_section_for_unspaced_field(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Unspaced Co", "https://amp-route-unspaced.com",
                                 "", "", [], local_markets="A firm doing S&M consulting")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "A firm doing S&M consulting",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={"item_ids": [str(item_id)]})
    assert r.status_code == 200
    assert "Left as-is" in r.text
    assert "Nothing to replace" in r.text  # the confirm button is disabled with nothing eligible


def test_preview_route_with_no_selection_redirects_with_error(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_apply_route_writes_and_resolves(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Apply Co", "https://amp-route-apply.com",
                                 "", "", [], local_markets="Finance & Operations leaders")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "Finance & Operations leaders",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
               data={"item_ids": [str(item_id)]}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/voice/review-queue"

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["local_markets"] == "Finance and Operations leaders"
    assert item["status"] == "resolved"


def test_apply_route_leaves_unspaced_row_open(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Apply Unspaced Co", "https://amp-route-apply-unspaced.com",
                                 "", "", [], local_markets="A firm doing S&M consulting")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "A firm doing S&M consulting",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
           data={"item_ids": [str(item_id)]}, follow_redirects=False)

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["local_markets"] == "A firm doing S&M consulting"
    assert item["status"] == "open"


def test_apply_route_with_bogus_item_id_is_a_safe_noop(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
               data={"item_ids": ["999999", "not-an-int"]}, follow_redirects=False)
    assert r.status_code == 303  # no crash


def test_preview_and_apply_routes_require_auth(env):
    c = _client(env)  # no login
    r1 = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
                data={"item_ids": ["1"]})
    assert r1.status_code == 401
    r2 = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
                data={"item_ids": ["1"]})
    assert r2.status_code == 401


def test_ampersand_group_shows_replace_button_only_for_bare_ampersand(env):
    # The group-level bulk-actions bar (and this new button with it) only
    # renders once a group has more than one row — see
    # _voice_review_group_bulk_actions_html's own call site — so this
    # needs 2+ ampersand rows and 2+ buzzword rows to exercise both.
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid1 = lib.add_community("Amp Button Co", "https://amp-button.com",
                                  "", "", [], local_markets="Finance & Operations leaders")
        lib.add_voice_review_item(
            "communities", cid1, "local_markets", "bare-ampersand", "Finance & Operations leaders",
        )
        cid2 = lib.add_community("Amp Button Co 2", "https://amp-button-2.com",
                                  "", "", [], local_markets="Widgets & Gadgets only")
        lib.add_voice_review_item(
            "communities", cid2, "local_markets", "bare-ampersand", "Widgets & Gadgets only",
        )
        tid1 = lib.add_tool("Buzzword Tool", "This is seamless.", "https://buzzword-tool.example", [], approved=1)
        lib.add_voice_review_item("tools", tid1, "description", "buzzword", "seamless")
        tid2 = lib.add_tool("Buzzword Tool 2", "This is robust.", "https://buzzword-tool-2.example", [], approved=1)
        lib.add_voice_review_item("tools", tid2, "description", "buzzword", "robust")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "Replace ampersands with and" in r.text
    assert r.text.count("Replace ampersands with and") == 1  # only on the Ampersands group, not the buzzword one


def test_approve_term_input_not_rendered_in_actions_cell_on_first_load(env):
    """issue #592 item 3/7 — the Approve-term text input must not be part
    of the row's default, always-visible Actions cell any more. It's only
    reachable by clicking the "Approve term" button, which reveals a
    separate panel row (rendered `display:none` until then)."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Panel Co", "https://amp-panel.com",
                                 "", "", [], local_markets="Finance & Operations leaders")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "Finance & Operations leaders",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    # The trigger button is on the page, in the default one-line action row.
    # Renamed "Approve term" -> "Allow everywhere" -> "Always allow"
    # (voice-queue-durability fix, Part C item 4; then the label-shortening
    # follow-up).
    assert "Always allow</button>" in r.text

    # But the actual term input lives ONLY inside a panel `<tr>` that starts
    # collapsed. There must be exactly one `name="term"` input on the page
    # for this one row (one ampersand phrase seeded -> one guessed term), and
    # it must sit inside the hidden panel row, not loose in the Actions `<td>`
    # the way the old boxed mini-form did. Panel ids are suffixed by guess
    # index (-0, -1, ...) to support multiple distinct ampersand candidates
    # in the same field (Part A item 4).
    assert r.text.count('name="term"') == 1
    panel_marker = f'<tr id="voice-approve-term-{item_id}-0" style="display:none;">'
    assert panel_marker in r.text
    panel_pos = r.text.index(panel_marker)
    term_input_pos = r.text.index('name="term"')
    assert term_input_pos > panel_pos, "the term input must be inside the (hidden) panel row, not before it"


def test_approve_term_panel_is_full_width_and_only_appears_there(env):
    """issue #592 item 3 — the reveal panel spans the whole table (a
    `colspan` row), holds the prefilled input, the shortened caption, then
    Approve/Cancel — and it is the ONLY place the term input appears."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Panel Co 2", "https://amp-panel-2.com",
                                 "", "", [], local_markets="Sales & Marketing team")
        item_id = lib.add_voice_review_item(
            "communities", cid, "local_markets", "bare-ampersand", "Sales & Marketing team",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    panel_re = re.search(
        r'<tr id="voice-approve-term-%s-0" style="display:none;">(.*?)</tr>' % item_id,
        r.text, re.DOTALL,
    )
    assert panel_re, "expected a dedicated panel <tr> for this item"
    panel_html = panel_re.group(1)
    assert 'colspan="5"' in panel_html
    assert 'name="term"' in panel_html
    assert "Sales" in panel_html  # the prefilled guess
    # Part C item 4 fix (voice-queue-durability PR, PR #595 review round) —
    # "Approve term" renamed to "Allow everywhere" (later "Always allow"),
    # and this caption replaced with Brian's own exact wording, verbatim
    # (linking /admin/voice, matching how the page's own lede references it).
    assert ("Keep only the exact term, like Dun &amp; Bradstreet. "
            "It'll be allowed everywhere. Remove it anytime on "
            '<a href="/admin/voice">/admin/voice</a>.') in panel_html
    assert "—" not in panel_html  # no em dash anywhere in the panel
    assert ">Approve</button>" in panel_html
    assert ">Cancel</button>" in panel_html

    # Only one `name="term"` input anywhere on the page for this one row.
    assert r.text.count('name="term"') == 1


def test_every_row_action_button_uses_the_shared_button_class(env):
    """issue #592 item 2 — every action button on the page, filled or
    outlined, must use the shared `.btn`/`.btn-ghost` classes (never a
    bare `<button>` with no class, and never a filled button that
    overrides `.btn`'s own 1px border with `border:none`, which is what
    broke height parity with the outlined buttons next to it)."""
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        # An "open" bare-ampersand row (Edit / Allow once / Approve term
        # + the Approve/Cancel panel).
        cid = lib.add_community("Amp Class Co", "https://amp-class.com",
                                 "", "", [], local_markets="Ops & Finance")
        lib.add_voice_review_item("communities", cid, "local_markets", "bare-ampersand", "Ops & Finance")

        # An auto_corrected row (Accept / Revert).
        tid = lib.add_tool("Class Tool", "A tool.", "https://class-tool.example", [], approved=1)
        lib.log_voice_correction("tools", tid, "description", "A tool — with a dash.", "A tool—with a dash.")

        # A seed-disagreement row (Use seed version / Keep mine).
        cid2 = lib.add_community("Class Seed Co", "https://class-seed.com", "", "", [], local_markets="Old text")
        lib.add_seed_disagreement_item("communities", cid2, "local_markets", "Old text", "New seed text")

        # A second bare-ampersand row so the group bulk bar (Select all /
        # Accept selected / Allow selected once / Replace & with and)
        # actually renders.
        cid3 = lib.add_community("Amp Class Co 2", "https://amp-class-2.com", "", "", [], local_markets="R&D team")
        lib.add_voice_review_item("communities", cid3, "local_markets", "bare-ampersand", "R&D team")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200

    # Scope to the review-queue content itself — the shared page chrome
    # (e.g. the mobile nav hamburger) has its own, unrelated button that
    # was never in scope for this row-action convention.
    body = r.text.split('id="nav"', 1)[-1]
    buttons = re.findall(r'<button[^>]*>', body)
    assert buttons, "expected at least one button on a populated review queue page"
    for b in buttons:
        assert 'class="btn' in b, f"button missing shared .btn class: {b}"

    # The specific regression this whole item exists to prevent: a filled
    # button that drops .btn's own 1px border, breaking height parity with
    # every outlined button beside it.
    assert "border:none" not in r.text
