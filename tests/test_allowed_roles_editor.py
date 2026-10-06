"""PR 3c: the allowed-roles editor on /admin/system/ai. Stored allowed_roles is
the only thing the role check reads; hard blocks stay in code; enrichment for an
unconfirmed model needs the confirm step; every change is logged."""
import importlib
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pricing
from linklib.db import Library
from linklib.models import BUDDY_BLOCK_REASON, ENRICHMENT_TEST_STEPS


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "l.db"))
    monkeypatch.delenv("LINKLIB_CHAT_MODEL", raising=False)
    pricing.reset_price_cache()
    l = Library(str(tmp_path / "l.db"))
    l.seed_model_catalog(); l.seed_model_roles()
    yield l
    l.close()


def _verify(lib, mid):
    row = lib.get_model_pricing(mid)
    rates = {k: (row[k] if row[k] is not None else 1.0) for k in Library._PRICE_FIELDS}
    lib.set_model_pricing(mid, rates, verified=True)


# --- Library -----------------------------------------------------------------

def test_editor_writes_allowed_roles_and_the_role_check_reads_it(lib):
    _verify(lib, "claude-fable-5-1")
    assert lib.model_allowed_roles("claude-fable-5-1") == []
    assert any("not allowed for Enrichment" in p for p in lib.role_problems("enrichment", "claude-fable-5-1"))
    assert lib.set_allowed_role("claude-fable-5-1", "enrichment", True, tested=True) == []
    assert lib.model_allowed_roles("claude-fable-5-1") == ["enrichment"]
    assert lib.role_problems("enrichment", "claude-fable-5-1") == []
    assert lib.set_role_model("enrichment", "claude-fable-5-1") == []


def test_unconfirmed_enrichment_is_refused_without_the_confirm(lib):
    for mid in ("claude-fable-5", "claude-fable-5-1", "claude-sonnet-5-5"):
        problems = lib.set_allowed_role(mid, "enrichment", True)
        assert problems and "confirm" in problems[0], mid
        assert lib.model_allowed_roles(mid) == []
    assert lib.list_model_role_log() == []


def test_hard_blocked_roles_cannot_be_enabled_even_with_tested(lib):
    for mid in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5", "claude-fable-5-1"):
        for role in ("matchmaker", "buddy_quick", "buddy_standard", "buddy_deep"):
            problems = lib.set_allowed_role(mid, role, True, tested=True)
            assert problems and "blocked in code" in problems[0], (mid, role)
            assert role not in lib.model_allowed_roles(mid)
    assert lib.list_model_role_log() == []


def test_role_check_also_refuses_a_hard_block_written_straight_into_the_database(lib):
    _verify(lib, "claude-opus-5-5")
    lib.conn.execute("UPDATE model_catalog SET allowed_roles=? WHERE model_id=?",
                     ('["buddy_deep"]', "claude-opus-5-5"))
    lib.conn.commit()
    problems = lib.role_problems("buddy_deep", "claude-opus-5-5")
    assert any(BUDDY_BLOCK_REASON in p for p in problems)


def test_a_model_in_use_cannot_lose_the_role_it_is_using(lib):
    problems = lib.set_allowed_role("claude-opus-5", "enrichment", False)   # the enrichment default
    assert problems and "assign another model first" in problems[0]
    assert "enrichment" in lib.model_allowed_roles("claude-opus-5")
    assert lib.set_allowed_role("claude-opus-4-8", "matchmaker", False) == []   # not in use there


def test_the_change_is_recorded_with_the_confirmation(lib):
    lib.set_allowed_role("claude-sonnet-5-5", "enrichment", True, tested=True)
    lib.set_allowed_role("claude-opus-4-8", "matchmaker", False)
    log = lib.list_model_role_log()
    assert [(l["model_id"], l["role"], l["action"], l["confirmed_tested"]) for l in log] == [
        ("claude-opus-4-8", "matchmaker", "disallowed", 0),
        ("claude-sonnet-5-5", "enrichment", "allowed", 1)]
    assert log[1]["created_at"] and "test was run" in log[1]["detail"]


def test_a_no_op_change_logs_nothing(lib):
    assert lib.set_allowed_role("claude-opus-4-8", "matchmaker", True) == []
    assert lib.list_model_role_log() == []


def test_seed_does_not_overwrite_an_edited_value(lib):
    lib.set_allowed_role("claude-sonnet-5-5", "enrichment", True, tested=True)
    lib.conn.execute("DELETE FROM settings WHERE key='model_roles_seeded'")
    lib.conn.commit()
    lib.seed_model_roles()
    assert lib.model_allowed_roles("claude-sonnet-5-5") == ["enrichment"]


def test_the_lineup_check_never_changes_allowed_roles(lib, monkeypatch):
    lib.set_allowed_role("claude-sonnet-5-5", "enrichment", True, tested=True)
    before = {r["model_id"]: r["allowed_roles"] for r in lib.list_model_catalog()}
    from linklib.lineup import check_lineup
    result = check_lineup(lib)                  # sees a live model nobody registered
    assert result is not None
    assert {r["model_id"]: r["allowed_roles"] for r in lib.list_model_catalog()} == before


def test_migration_on_a_production_shaped_database(tmp_path, monkeypatch):
    """13 catalog rows, one model_roles row (matchmaker), seed flags set, and no
    model_role_log table yet: opening it adds the table and loses nothing."""
    db = str(tmp_path / "prod.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    pricing.reset_price_cache()
    l = Library(db)
    l.seed_model_catalog(); l.seed_model_roles()
    for i in range(13 - len(l.list_model_catalog())):
        l.set_model_status(f"claude-retired-{i}", "not_using", "retired")
    assert len(l.list_model_catalog()) == 13
    before = {r["model_id"]: r["allowed_roles"] for r in l.list_model_catalog()}
    l.conn.execute("DROP TABLE model_role_log")
    l.conn.commit(); l.close()
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM model_roles").fetchone()[0] == 1
    assert c.execute("SELECT value FROM settings WHERE key='model_roles_seeded'").fetchone()[0] == "1"
    c.close()
    l = Library(db)
    assert {r["model_id"]: r["allowed_roles"] for r in l.list_model_catalog()} == before
    assert l.list_model_role_log() == []
    assert l.set_allowed_role("claude-sonnet-5-5", "enrichment", True, tested=True) == []
    assert len(l.list_model_role_log()) == 1
    l.close()


# --- Admin routes ------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    s = Library(db); s.seed_voice_prompts(); s.seed_model_catalog(); s.seed_model_roles(); s.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _stored(model, db=None):
    l = Library(db or os.environ["LINKLIB_DB"])
    try:
        return l.model_allowed_roles(model)
    finally:
        l.close()


def _card(env, c=None):
    html = (c or _admin(env)).get("/admin/system/ai").text
    i = html.index('id="allowed-roles"')
    return html[i:html.index("</details></div>", i)]


def _switches(card):
    import re
    return re.findall(r'<button type="submit" class="sw"[^>]*>', card)


def _cell(card, model, role_index):
    """The <td> of one role cell in a model's row."""
    import re
    row = re.search(r'<tr><td class="sw-model"><strong>[^<]*</strong><div class="sw-id">%s</div></td>(.*?)</tr>' % re.escape(model), card, re.S).group(1)
    return re.findall(r'<td(?: class="sw-rc")?>(.*?)</td>', row, re.S)[role_index]


def test_editor_section_is_collapsed_and_uses_switches_with_short_words(env):
    html = _admin(env).get("/admin/system/ai").text
    i = html.index('id="allowed-roles"')
    assert html[i:i + 200].count("<details") == 1 and "<details class=\"admin-group\" open" not in html[i:i + 300]
    card = _card(env)
    assert "To do" in card and "Still open before it can be used" not in card
    assert "Ready" in card and "Nothing open" not in card
    assert "Verify pricing: cache write, cache write 1h, date." in card
    assert "Verify its pricing row" not in card
    assert "Allow after testing" not in card and ">Stop allowing<" not in card
    assert "Turning a role on doesn&rsquo;t enable the model. It also needs verified pricing and status available. Changes apply immediately." in card


def test_switch_markup_per_state(env):
    card = _card(env)
    # on (Haiku 4.5 Enrichment is allowed, not in use): enabled, checked, flips to off
    on = _cell(card, "claude-haiku-4-5-20251001", 0)
    assert 'aria-checked="true"' in on and "disabled" not in on and 'name="allow" value="0"' in on and ">On<" in on
    # off but allowed to be on (Opus 4.8 Matchmaker, switched off first)
    # in use: the enrichment default model, checked and disabled, word In use
    inuse = _cell(card, "claude-opus-5", 0)
    assert 'aria-checked="true"' in inuse and " disabled" in inuse and ">In use<" in inuse and "<form" not in inuse
    # locked: Opus 5.5 Buddy Deep, unchecked, disabled, word Locked, no form
    locked = _cell(card, "claude-opus-5-5", 4)
    assert 'aria-checked="false"' in locked and " disabled" in locked and ">Locked<" in locked and "<form" not in locked
    # untested: Fable 5.1 Enrichment, unchecked, disabled, Test first flag links to the confirm page
    untested = _cell(card, "claude-fable-5-1", 0)
    assert 'aria-checked="false"' in untested and " disabled" in untested and "<form" not in untested
    assert 'href="/admin/system/ai/allowed-roles/confirm/claude-fable-5-1/enrichment"' in untested and "Test first" in untested


def test_every_switch_shares_one_class_and_no_inline_style(env):
    card = _card(env)
    sw = _switches(card)
    assert len(sw) >= 40
    assert all('class="sw"' in x and "style=" not in x and 'role="switch"' in x and "aria-checked=" in x for x in sw)
    assert card.count("sw-flag") >= 3
    # no other control stands in for a switch
    assert "Stop allowing" not in card and "btn-ghost" not in card


def test_notes_block_sits_below_the_table_and_log_shows_labels(env):
    c = _admin(env)
    c.post("/admin/system/ai/allowed-roles/set", data={"model_id": "claude-opus-4-8", "role": "matchmaker", "allow": "0"})
    c.post("/admin/system/ai/allowed-roles/set",
           data={"model_id": "claude-sonnet-5-5", "role": "enrichment", "allow": "1", "tested": "1"})
    card = _card(env, c)
    assert card.index("</table>") < card.index('class="sw-notes"') < card.index("Recent changes")
    notes = card[card.index('class="sw-notes"'):card.index("Recent changes")]
    assert BUDDY_BLOCK_REASON in notes and "900 tokens" in notes and "can&rsquo;t be switched off" in notes and "Test first." in notes
    log = card[card.index("Recent changes"):]
    assert "Opus 4.8" in log and "Sonnet 5.5" in log and "claude-opus-4-8" not in log
    assert "Turned off" in log and "Turned on" in log and "Test confirmed" in log
    assert "<th>When</th>" in log and "<th>Change</th>" in log and "<th>Note</th>" in log
    import re
    assert re.search(r"Oct \d+, \d\d:\d\d UTC|[A-Z][a-z]{2} \d+, \d\d:\d\d UTC", log)


def test_a_model_in_use_but_not_in_the_registry_still_gets_a_row(env):
    card = _card(env)
    assert 'class="sw-id">claude-opus-4-8<' in card and "<strong>Opus 4.8</strong>" in card


def test_turning_an_untested_role_off_and_on_again_does_not_ask_twice(lib):
    assert lib.set_allowed_role("claude-sonnet-5-5", "enrichment", True, tested=True) == []
    assert lib.set_allowed_role("claude-sonnet-5-5", "enrichment", False) == []
    assert "enrichment" in lib.confirmed_roles("claude-sonnet-5-5")
    assert lib.set_allowed_role("claude-sonnet-5-5", "enrichment", True) == []
    # a different untested model still asks
    assert lib.set_allowed_role("claude-fable-5", "enrichment", True)


def test_format_of_log_time():
    import webapp.app as a
    assert a._fmt_log_time("2026-10-06T10:54:12+00:00") == "Oct 6, 10:54 UTC"
    assert a._fmt_log_time("garbage") == "garbage"


def test_set_route_allows_and_stops_allowing_a_plain_role(env):
    c = _admin(env)
    r = c.post("/admin/system/ai/allowed-roles/set", data={"model_id": "claude-opus-4-8", "role": "matchmaker", "allow": "0"},
               follow_redirects=False)
    assert r.status_code == 303 and "matchmaker" not in _stored("claude-opus-4-8")
    r = c.post("/admin/system/ai/allowed-roles/set", data={"model_id": "claude-opus-4-8", "role": "matchmaker", "allow": "1"},
               follow_redirects=False)
    assert r.status_code == 303 and "matchmaker" in _stored("claude-opus-4-8")


def test_a_crafted_post_cannot_enable_a_hard_blocked_role(env):
    c = _admin(env)
    for mid in ("claude-opus-5-5", "claude-fable-5-1"):
        r = c.post("/admin/system/ai/allowed-roles/set",
                   data={"model_id": mid, "role": "buddy_deep", "allow": "1", "tested": "1"})
        assert r.status_code == 400 and "blocked in code" in r.text
        assert "buddy_deep" not in _stored(mid)


def test_unconfirmed_enrichment_route_needs_the_tested_box(env):
    c = _admin(env)
    r = c.post("/admin/system/ai/allowed-roles/set", data={"model_id": "claude-fable-5", "role": "enrichment", "allow": "1"})
    assert r.status_code == 400 and _stored("claude-fable-5") == []
    page = c.get("/admin/system/ai/allowed-roles/confirm/claude-fable-5/enrichment").text
    for step in ENRICHMENT_TEST_STEPS:
        assert step in page
    assert 'name="tested"' in page and "required" in page
    r = c.post("/admin/system/ai/allowed-roles/set",
               data={"model_id": "claude-fable-5", "role": "enrichment", "allow": "1", "tested": "1"}, follow_redirects=False)
    assert r.status_code == 303 and _stored("claude-fable-5") == ["enrichment"]
    html = c.get("/admin/system/ai").text
    assert "Recent changes" in html and "Test confirmed" in html


def test_confirm_page_is_404_where_there_is_no_confirm_step(env):
    assert _admin(env).get("/admin/system/ai/allowed-roles/confirm/claude-opus-4-8/matchmaker").status_code == 404


def test_set_and_confirm_routes_need_admin(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.post("/admin/system/ai/allowed-roles/set", data={"model_id": "claude-opus-4-8", "role": "matchmaker", "allow": "0"},
               follow_redirects=False)
    assert r.status_code in (302, 303, 401) and "matchmaker" in _stored("claude-opus-4-8")
    assert c.get("/admin/system/ai/allowed-roles/confirm/claude-fable-5/enrichment", follow_redirects=False).status_code in (302, 303, 401)


def test_role_columns_have_one_equal_width_and_cells_do_not_wrap():
    from webapp import app as appmod
    css = appmod._SW_CARD_CSS
    assert "col.sw-c-role{width:172px;}" in css
    assert "table-layout:fixed" in css
    # switch plus label never wrap inside a cell
    assert ".sw-cell{display:flex;align-items:center;gap:6px;flex-wrap:nowrap;white-space:nowrap;" in css
    info = {"claude-haiku-4-5-20251001": {"allowed": [], "assigned": [], "confirmed": [], "status": "available", "pricing": []}}
    html = appmod._allowed_roles_card_html(info, [])
    assert html.count('<col class="sw-c-role">') == 5
    assert html.count('<th class="sw-rc">') == 5


# --- AI page polish (follow-up to 3c) ----------------------------------------

def test_allowed_roles_uses_the_shared_disclosure_with_a_triangle_and_loads_collapsed(env):
    html = _admin(env).get("/admin/system/ai").text
    i = html.index('id="allowed-roles"')
    head = html[i:i + 900]
    assert '<details class="admin-group"' in head and "<details class=\"admin-group\" open" not in head
    assert '<span class="disclosure-caret">' in html[i:i + 1500]
    assert ">Allowed roles</span>" in head and "Edit allowed roles" not in html
    # the log stays inside the collapsed section
    j = html.index("</details></div>", i)
    assert "Recent changes" in html[i:j] or "No changes recorded yet." in html[i:j]
    # the old card title above it is gone, so the heading appears once
    assert html.count(">Allowed roles</div>") == 0


def test_buddy_tiers_heading_is_escaped_once(env):
    html = _admin(env).get("/admin/system/ai").text
    assert "&amp;amp;" not in html
    assert ">FP&amp;A Buddy tiers</div>" in html


def test_model_label_falls_back_to_a_readable_name_not_the_raw_id(env):
    assert env._enrich_model_label("claude-opus-4-8") == "Opus 4.8"
    assert env._enrich_model_label("claude-sonnet-5") == "Sonnet 5"
    assert env._enrich_model_label("not-a-model") == "not-a-model"
    html = _admin(env).get("/admin/system/ai").text
    assert "Opus 4.8" in html
