"""Feature definitions on the public Software profile's Key features card
(2026-09). Each curated feature shows a short definition derived from
category_features.definition by default and expands, via a native
<details>/<summary>, to the full stored text plus the category's
pointer_note. tool_feature_links.note is a curation log with reviewer
caveats, so it is never rendered publicly.
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# A realistic long definition (~1,470 chars, the length of the longest live
# row, "Spend controls & payment approval workflows").
LONG_DEF = (
    "Set spending rules up front and require sign-off before money leaves the business. "
    + " ".join(
        f"Finance teams define limit set {i} across daily, weekly, monthly, quarterly or annual "
        f"cycles, tie cards to specific budgets, accounts or currencies, and route approvals."
        for i in range(1, 9)
    )
)
LONG_DEF = LONG_DEF[:1470].rsplit(" ", 1)[0] + " end."


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


def _seed(definitions: list[tuple[str, str, str]], note: str = "", public_note: str | None = None):
    """definitions: (feature name, definition, pointer_note)."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("Neobanking")
    tool_id = lib.add_tool("Mercury", "d", "https://mercury.com", ["Neobanking"], approved=1, summary="s")
    fids = []
    for name, definition, pointer in definitions:
        fid = lib.add_category_feature(cat_id, name, definition, pointer)
        lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-24", note=note,
                                     public_note=public_note)
        fids.append(fid)
    lib.close()
    return fids


def _placeholder():
    from linklib import gates
    return gates.EMPTY_COPY["feature_definition"]


def _card(html: str) -> str:
    start = html.index('<h2 class="tp-card-h">Key features</h2>')
    return html[start:html.index('</div>\n<div class="tp-footnote"', start)]


# ---------------------------------------------------------------------------
# Short-form derivation (pure)
# ---------------------------------------------------------------------------

def test_short_form_uses_first_sentence_when_short(env):
    f = env._feature_definition_short
    assert f("Deposit checks by photo. Funds credit the account.") == "Deposit checks by photo."


def test_short_form_skips_abbreviation_periods(env):
    f = env._feature_definition_short
    text = "Projects cash (e.g. delayed payments) forward. Second sentence."
    assert f(text) == "Projects cash (e.g. delayed payments) forward."


def test_short_form_cuts_long_first_sentence_at_word_boundary(env):
    f = env._feature_definition_short
    text = ("Move money into and out of the account over standard payment rails, choosing the right one "
            "for each payment: domestic ACH (typically settling in one to three business days, with more")
    out = f(text)
    assert out.endswith("…")
    assert len(out) <= env._FEATURE_DEF_SHORT_TARGET + 1
    assert text.startswith(out[:-1])          # a prefix, cut on a word boundary
    assert "(" not in out                     # no dangling open parenthetical


def test_short_form_empty(env):
    assert env._feature_definition_short("") == ""
    assert env._feature_definition_short("   ") == ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_long_definition_round_trips_in_full_to_expanded_view(env):
    assert len(LONG_DEF) > 1400
    _seed([("Spend Controls", LONG_DEF, "")])
    r = _client(env).get("/tools/software/mercury")
    card = _card(r.text)
    assert '<details class="tp-fd">' in card
    full = card[card.index('class="tp-fd-full"'):]
    assert env._esc(LONG_DEF) in full
    # Short line is the derived first sentence, shown in the summary.
    assert ('<span class="tp-fd-short">Set spending rules up front and require sign-off '
            'before money leaves the business.</span>') in card


def test_stored_definition_is_never_shortened(env):
    fid = _seed([("Spend Controls", LONG_DEF, "")])[0]
    _client(env).get("/tools/software/mercury")
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    stored = lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (fid,)).fetchone()[0]
    lib.close()
    assert stored == LONG_DEF


def test_short_definition_renders_without_expand_control(env):
    _seed([("Mobile Check Deposit", "Deposit paper checks by photo in the app.", "")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    assert "Deposit paper checks by photo in the app." in card
    assert "<details" not in card


def test_missing_definition_renders_placeholder(env):
    _seed([("Basic Card Issuance", "", "")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    copy = _placeholder()
    assert copy.visitor_text == "Definition not available."
    assert copy.visitor_text in card
    assert copy.admin_suffix not in card   # admin suffix only


def test_missing_definition_placeholder_carries_admin_prompt_when_signed_in(env):
    _seed([("Basic Card Issuance", "", "")])
    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    card = _card(c.get("/tools/software/mercury").text)
    copy = _placeholder()
    assert copy.admin_suffix == "Add one from Software features."
    assert f"{copy.visitor_text} {copy.admin_suffix}" in card


def test_pointer_note_is_in_expanded_view(env):
    _seed([("Native Procurement", "",
            "Embedded option—see the Procurement category to compare against standalone products.")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    assert "<details" in card
    assert "Embedded option—see the Procurement category" in card


def test_link_note_curation_log_is_not_rendered_publicly(env):
    note = "UNVERIFIED, could not confirm on primary sources; keep pending"
    _seed([("Charge Cards", "Issues charge cards settled in full each cycle.", "")], note=note)
    r = _client(env).get("/tools/software/mercury")
    assert note not in r.text


def test_definition_text_is_real_dom_text_not_a_tooltip(env):
    _seed([("Spend Controls", LONG_DEF, "")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    for m in re.finditer(r'title="([^"]*)"', card):
        assert "Set spending rules" not in m.group(1)


def test_expand_control_is_native_details_summary(env):
    """Keyboard-reachable with no JavaScript: <summary> is focusable and
    toggles its <details> on Enter/Space natively."""
    _seed([("Spend Controls", LONG_DEF, "")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    assert re.search(r'<details class="tp-fd"><summary class="tp-fd-line">', card)
    assert "onclick" not in card[card.index("<details"):card.index("</details>")]


# ---------------------------------------------------------------------------
# Publishable vendor text: tool_feature_links.public_note (2026-09)
# ---------------------------------------------------------------------------

VENDOR = "Mercury sets limits per card, per team, or per vendor, and routes over-limit spend to an approver."


def test_public_note_renders_after_definition_each_labeled(env):
    _seed([("Spend Controls", "Set spending rules before money moves.", "")], public_note=VENDOR)
    card = _card(_client(env).get("/tools/software/mercury").text)
    full = card[card.index('class="tp-fd-full"'):card.index("</details>")]
    i_label_def = full.index('<p class="tp-fd-label">Definition</p>')
    i_def = full.index("Set spending rules before money moves.")
    i_label_vendor = full.index('<p class="tp-fd-label tp-fd-label-vendor">In Mercury</p>')
    i_vendor = full.index(env._esc(VENDOR))
    assert i_label_def < i_def < i_label_vendor < i_vendor


def test_public_note_makes_a_short_definition_expandable(env):
    _seed([("Mobile Check Deposit", "Deposit paper checks by photo.", "")], public_note=VENDOR)
    card = _card(_client(env).get("/tools/software/mercury").text)
    assert '<details class="tp-fd">' in card
    assert env._esc(VENDOR) in card


def test_public_note_with_empty_definition_keeps_the_placeholder(env):
    _seed([("Basic Card Issuance", "", "")], public_note=VENDOR)
    card = _card(_client(env).get("/tools/software/mercury").text)
    full = card[card.index('class="tp-fd-full"'):]
    assert _placeholder().visitor_text in full
    assert env._esc(VENDOR) in full


def test_no_labels_without_public_note(env):
    _seed([("Spend Controls", LONG_DEF, "")])
    card = _card(_client(env).get("/tools/software/mercury").text)
    assert "tp-fd-label" not in card


def test_public_note_starts_empty_and_is_never_copied_from_note(env):
    _seed([("Charge Cards", "Issues charge cards.", "")], note="Vendor says: settles in full monthly.")
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    rows = lib.conn.execute("SELECT note, public_note FROM tool_feature_links").fetchall()
    lib.close()
    assert [tuple(r) for r in rows] == [("Vendor says: settles in full monthly.", "")]


def test_upsert_without_public_note_keeps_existing_text(env):
    """Queue approval and the seed script never pass public_note; a re-upsert
    from either must not wipe what Brian wrote."""
    fid = _seed([("Spend Controls", "d.", "")], public_note=VENDOR)[0]
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.conn.execute("SELECT tool_id FROM tool_feature_links").fetchone()[0]
    lib.upsert_tool_feature_link(tool_id, fid, "add_on", 1, "2026-09-01", note="new note")
    link = lib.get_tool_feature_link(tool_id, fid)
    lib.close()
    assert link["public_note"] == VENDOR and link["availability"] == "add_on"


def test_public_note_over_limit_is_refused_not_shortened(env):
    fid = _seed([("Spend Controls", "d.", "")], public_note=VENDOR)[0]
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.conn.execute("SELECT tool_id FROM tool_feature_links").fetchone()[0]
    too_long = "x" * (Library.FEATURE_LINK_PUBLIC_NOTE_MAX + 1)
    with pytest.raises(ValueError, match="limit is 1,000"):
        lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "", public_note=too_long)
    exactly = "y" * Library.FEATURE_LINK_PUBLIC_NOTE_MAX
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "", public_note=exactly)
    stored = lib.get_tool_feature_link(tool_id, fid)["public_note"]
    lib.close()
    assert stored == exactly


def _admin(env):
    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    return c


def test_editor_shows_internal_note_beside_public_text(env):
    fid = _seed([("Spend Controls", "d.", "")], note="Vendor copy: per-card limits. UNVERIFIED",
                public_note=VENDOR)[0]
    html = _admin(env).get("/tools/software/mercury/edit").text
    note_i = html.index(f'name="feature_{fid}_note"')
    public_i = html.index(f'name="feature_{fid}_public_note"')
    assert note_i < public_i
    # Adjacent cells: nothing but the closing/opening <td> between them.
    between = html[note_i:public_i]
    assert between.count("<td") == 1 and "Vendor copy: per-card limits. UNVERIFIED" in between
    assert f'maxlength="{env._FEATURE_LINK_PUBLIC_NOTE_MAX}"' in html[public_i:public_i + 300]
    assert env._esc(VENDOR) in html[public_i:public_i + 1200]
    assert ">Internal note</th>" in html and ">Public text</th>" in html


def test_editor_save_writes_public_note_and_refuses_over_limit(env):
    fid = _seed([("Spend Controls", "d.", "")])[0]
    c = _admin(env)
    base = {"feature_ids": str(fid), f"feature_{fid}_enabled": "1",
            f"feature_{fid}_availability": "native", f"feature_{fid}_note": "internal"}
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.conn.execute("SELECT tool_id FROM tool_feature_links").fetchone()[0]
    lib.close()
    url = f"/admin/tools/software/{tool_id}/feature-links/save"
    r = c.post(url, data={**base, f"feature_{fid}_public_note": VENDOR}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tools/software/mercury/edit"
    r = c.post(url, data={**base, f"feature_{fid}_note": "changed",
                          f"feature_{fid}_public_note": "z" * 1001}, follow_redirects=False)
    assert "feature_links_error=" in r.headers["location"]
    lib = Library(os.environ["LINKLIB_DB"])
    link = lib.get_tool_feature_link(tool_id, fid)
    lib.close()
    # Refused as a whole: neither field changed, nothing shortened.
    assert link["public_note"] == VENDOR and link["note"] == "internal"
    page = c.get(r.headers["location"]).text
    assert "the limit is 1,000" in page and '<details class="features-group" open' in page


def test_live_browser_expand_by_keyboard_without_js_errors(env):
    """Real Chromium: Tab to the summary, press Enter, the full text shows,
    no page errors. Skips when no browser binary is available (CI)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    _seed([("Spend Controls", LONG_DEF, "")], public_note=VENDOR)
    html = _client(env).get("/tools/software/mercury").text
    pw = sync_playwright().start()
    browser = None
    # Playwright's bundled path can drift from a sandbox's pre-baked browser
    # cache (see CLAUDE.md's testing-standard note), so fall back to the
    # pre-installed binary before giving up.
    for kwargs in ({}, {"executable_path": "/opt/pw-browsers/chromium"}):
        try:
            browser = pw.chromium.launch(**kwargs)
            break
        except Exception:
            continue
    if browser is None:
        pw.stop()
        pytest.skip("Chromium not available")
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.set_content(html, wait_until="domcontentloaded")
        summary = page.locator("details.tp-fd > summary").first
        full = page.locator("details.tp-fd .tp-fd-full").first
        assert not full.is_visible()
        summary.focus()
        assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
        page.keyboard.press("Enter")
        assert full.is_visible()
        assert "route approvals" in full.inner_text()
        assert "in mercury" in full.inner_text().lower() and "over-limit spend" in full.inner_text()
        assert errors == []
    finally:
        browser.close()
        pw.stop()
