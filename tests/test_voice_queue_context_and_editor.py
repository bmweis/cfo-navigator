"""Voice review queue, the four items PR 595 deferred (checks-page
follow-ups, 2026-09):

1. Detail shows the finding (or the fix) in context, centered on what
   matters, with invisible characters named in words.
2. Edit happens in place in the Detail cell, on the FULL stored value, with
   a stale-value check on save; an original_content.body_md save still
   fires the mirror sync.
3. Resolved rows say how they ended.
4. "Resolved and exceptions" loads collapsed with its count, every row kept.
"""
import os
import pathlib
import re
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
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    lib = Library(db)
    yield appmod, c, lib
    lib.close()
    if os.path.exists(db):
        os.remove(db)


LONG_PREFIX = ("Month-end close for finance teams who want a clear audit trail. " * 20)


# --- 1. Context ---------------------------------------------------------------

def test_open_finding_shows_surrounding_context_with_match_marked(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", LONG_PREFIX + "It makes reconciliation seamless for the controller.",
                       "https://acme.example", [])
    iid = lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    html = c.get("/admin/voice/review-queue").text
    detail = html[html.index(f'id="voice-edit-field-{iid}-detail"'):]
    detail = detail[:detail.index("</div>") + 6]
    assert "reconciliation " in detail and " for the controller" in detail
    assert re.search(r"<mark[^>]*>seamless</mark>", detail)
    assert "&hellip;" in detail  # windowed, not the whole 1,300-char field
    assert "Flagged text: seamless" not in html


def test_multiple_spots_are_called_out(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", "A seamless start. Then a seamless finish.", "https://acme.example", [])
    lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    html = c.get("/admin/voice/review-queue").text
    assert "Match 1 of 2 in this field" in html


def test_match_gone_is_said_plainly(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", "A clean close.", "https://acme.example", [])
    lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    html = c.get("/admin/voice/review-queue").text
    assert "is no longer in this field" in html


def test_auto_fix_deep_in_the_field_centers_before_and_after(env):
    appmod = env[0]
    before = LONG_PREFIX + "Close fast — then reconcile." + " Tail sentence here." * 5
    after = before.replace("fast — then", "fast—then")
    assert before[:200] == after[:200]  # the old first-200 view showed no change
    html = appmod._voice_centered_diff_html(before, after)
    b, a = html.split("<strong>After:</strong>")
    assert "fast" in b and "then reconcile" in b and "<del" in b
    assert "fast" in a and "<mark" in a
    assert re.search(r"<del[^>]*> — </del>", b)
    assert "character " in b  # position named for a long field


def test_auto_fix_row_in_queue_uses_the_centered_diff(env):
    appmod, c, lib = env
    before = LONG_PREFIX + "Close fast — then reconcile."
    lib.log_voice_correction("settings", None, "voice_core", before, before.replace(" — ", "—"))
    html = c.get("/admin/voice/review-queue").text
    assert "fast" in html and "then reconcile" in html


def test_invisible_character_fix_is_named_in_words(env):
    appmod = env[0]
    before = "x" * 837 + "​"
    html = appmod._voice_centered_diff_html(before, "x" * 837)
    assert html == ('<div style="color:var(--ink-soft);">Removed a zero-width space (U+200B) '
                    'at the end of the field.</div>')


def test_invisible_character_open_finding_is_visible(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", "Clean text", "https://acme.example", [])
    lib.conn.execute("UPDATE tools SET description=? WHERE id=?", ("Clean text​", tid))
    lib.conn.commit()
    lib.add_voice_review_item("tools", tid, "description", "invisible-character", "U+200B (zero-width space)")
    html = c.get("/admin/voice/review-queue").text
    assert "[U+200B zero-width space]" in html


# --- 2. Editor ----------------------------------------------------------------

def test_editor_holds_the_full_value_and_save_never_writes_the_excerpt(env):
    appmod, c, lib = env
    full = LONG_PREFIX + "It is seamless."
    assert len(full) > 1000
    tid = lib.add_tool("Acme", full, "https://acme.example", [])
    iid = lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    html = c.get("/admin/voice/review-queue").text
    m = re.search(r'<textarea name="edited_text"[^>]*data-match-start="(\d+)"[^>]*>(.*?)</textarea>', html, re.S)
    assert m and m.group(2) == appmod._esc(full)
    assert int(m.group(1)) == full.index("seamless")
    # Editor sits in the Detail cell, hidden, with the required caption.
    assert f'id="voice-edit-field-{iid}-expanded" style="display:none;"' in html
    assert "Saving replaces the whole field. To keep the text as it is, cancel and use Allow once." in html

    fixed = full.replace("seamless", "smooth")
    r = c.post(f"/admin/voice/review-queue/{iid}/resolve",
               data={"action": "edit", "edited_text": fixed, "original_value": full},
               follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    assert lib.get_tool(tid)["description"] == fixed
    assert lib.get_voice_review_item(iid)["resolution_note"] == "Edited."


def test_stale_value_is_refused(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", "It is seamless.", "https://acme.example", [])
    iid = lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    lib.conn.execute("UPDATE tools SET description=? WHERE id=?", ("Changed elsewhere, seamless.", tid))
    lib.conn.commit()
    r = c.post(f"/admin/voice/review-queue/{iid}/resolve",
               data={"action": "edit", "edited_text": "It is smooth.", "original_value": "It is seamless."},
               follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert lib.get_tool(tid)["description"] == "Changed elsewhere, seamless."
    assert lib.get_voice_review_item(iid)["status"] == "open"


def test_original_content_body_md_edit_keeps_the_mirror_in_sync(env):
    appmod, c, lib = env
    from linklib.original_content_sync import sync_original_content_article
    oc = lib.add_original_content("sync-test", "Sync test", teaser="t", tag_label="Guide",
                                  link_label="Read the guide", body_md="A seamless close.")
    sync_original_content_article(lib, oc)
    assert lib.list_drifted_original_content_mirrors() == []
    iid = lib.add_voice_review_item("original_content", oc, "body_md", "buzzword", "seamless")
    r = c.post(f"/admin/voice/review-queue/{iid}/resolve",
               data={"action": "edit", "edited_text": "A smooth close.", "original_value": "A seamless close."},
               follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        assert lib2.get_original_content(oc)["body_md"] == "A smooth close."
        assert lib2.list_drifted_original_content_mirrors() == []
        art = lib2.conn.execute("SELECT content FROM articles WHERE id=?",
                                (lib2.get_original_content(oc)["mirrored_article_id"],)).fetchone()
        assert "smooth" in art[0]
    finally:
        lib2.close()


# --- 3/4. History ---------------------------------------------------------------

def test_resolved_rows_show_their_outcome_and_section_is_collapsed(env):
    appmod, c, lib = env
    tid = lib.add_tool("Acme", "It is seamless and robust.", "https://acme.example", [])
    a = lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    lib.resolve_voice_review_item(a, "accept_exception")
    lib.log_voice_correction("tools", tid, "summary", "a — b", "a—b")
    b = lib.list_voice_review_queue(status="auto_corrected")[0]["id"]
    lib.resolve_voice_review_item(b, "accept")
    lib.approve_voice_term("Smith & Jones")
    for _ in range(60):  # history keeps every row, duplicates included
        lib.log_voice_correction("tools", tid, "summary", "c — d", "c—d")
    for item in lib.list_voice_review_queue(status="auto_corrected"):
        lib.resolve_voice_review_item(item["id"], "accept")

    html = c.get("/admin/voice/review-queue").text
    sec = html[html.index('id="voice-resolved"'):]
    assert re.match(r'id="voice-resolved"[^>]*><details class="admin-group" ', sec)
    assert "<details class=\"admin-group\" open" not in sec[:400]
    assert "62 rows" in sec[:2000]
    assert sec.count('<strong>Outcome:</strong>') == 62
    assert "<strong>Outcome:</strong> Allowed once." in sec
    assert "<strong>Outcome:</strong> Auto-fix accepted." in sec


def test_every_action_records_an_outcome(env):
    _, _, lib = env
    assert set(lib._VOICE_RESOLUTION_NOTES) == {"accept", "revert", "edit", "accept_exception",
                                               "use_seed", "keep_mine"}


def test_legacy_row_without_a_note_reads_honestly(env):
    appmod = env[0]
    assert appmod._voice_outcome_text({"resolution_note": "", "status": "resolved", "rule": "buzzword"}) == \
        "Resolved (logged before outcomes were recorded)."
    assert appmod._voice_outcome_text({"resolution_note": None, "status": "exception", "rule": "buzzword"}) == \
        "Allowed once."
