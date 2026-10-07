"""/admin/library-backup: one history table, a restore marker on the Drive list,
a result panel that expires, and the phone layout of both tables.

The history merges backup_log (inside the database) with restore-audit.jsonl
(on the volume, so it survives a restore). Chromium tests skip where no
Chromium exists, as in CI."""
import glob
import importlib
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup, backup_history, restore_status as rs
from linklib.db import Library

FILES = [{"id": "f1", "name": "library-20261007-090000.db", "size": 266_000_000, "created": "2026-10-07T09:00:00Z"},
         {"id": "f2", "name": "library-20261006-090000.db", "size": 265_000_000, "created": "2026-10-06T09:00:00Z"}]


def _iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


@pytest.fixture
def world(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SITES_OPML", db + ".opml")
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.close()
    monkeypatch.setattr(backup, "list_for_display",
                        lambda db_path, timeout=10.0: {"ok": True, "message": "", "files": [dict(f) for f in FILES]})
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    admin = TestClient(appmod.app)
    admin.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield {"app": appmod, "admin": admin, "db": db}
    for ext in ("", "-shm", "-wal", "-journal", ".opml", ".restore-audit.jsonl"):
        if os.path.exists(db + ext):
            os.remove(db + ext)
    for f in (rs.STATUS_FILE, rs.LOG_FILE, rs.AUDIT_FILE):
        p = os.path.join(os.path.dirname(db), f)
        if os.path.exists(p):
            os.remove(p)


def _page(w):
    return w["admin"].get("/admin/library-backup").text


# --- A: the panel is for what is current ------------------------------------------

def test_finished_panel_is_gone_after_thirty_minutes(world):
    db = world["db"]
    rs.write_status(db, state="checked", mode="check", message="ok", started_at=_iso(50), finished_at=_iso(40))
    assert 'id="restore-panel"' not in _page(world)


def test_finished_panel_shows_within_thirty_minutes(world):
    db = world["db"]
    rs.write_status(db, state="checked", mode="check", message="ok", started_at=_iso(20), finished_at=_iso(10))
    assert 'id="restore-panel"' in _page(world)


def test_failed_panel_expires_too_and_the_failure_stays_in_the_table(world):
    db = world["db"]
    rs.write_status(db, state="failed", mode="check", message="Boom.", started_at=_iso(200), finished_at=_iso(190))
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="failed",
             message="The check failed: md5 does not match Drive's checksum.")
    html = _page(world)
    assert 'id="restore-panel"' not in html
    assert "md5 does not match Drive" in html and ">Failed<" in html


def test_a_running_job_always_shows(world):
    db = world["db"]
    rs.write_status(db, state="running", mode="restore", pid=os.getpid(), started_at=_iso(200), stage="downloading")
    assert 'data-state="running"' in _page(world)


def test_the_window_is_one_named_constant(world):
    assert world["app"]._RESULT_PANEL_MINUTES == 30


# --- B: the history table ---------------------------------------------------------

def test_history_merges_backups_checks_and_restores_newest_first(world):
    db = world["db"]
    lib = Library(db)
    lib.record_backup_attempt("success", filename="library-20261005-090000.db", drive_file_id="d", size_bytes=1, row_count=4435)
    lib.record_backup_attempt("failure", error="HTTP 404")
    lib.close()
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="checked",
             message="Dry run finished: x is a valid backup with 4,435 articles. Nothing was changed.",
             started_at=_iso(1), articles=4435)
    rs.audit(db, user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore", result="refused",
             message="Another restore or check is already running. Nothing was changed.")
    html = _page(world)
    for head in ("When", "Action", "Backup file", "Result", "Detail", "By"):
        assert f">{head}</th>" in html
    assert html.count('data-label="Action"') == 4
    for chip in ("Succeeded", "Failed", "Refused"):
        assert f">{chip}</span>" in html
    assert "4,435 articles" in html
    assert "Google could not find the backup folder." in html       # plain-language backup failure
    assert "Another restore or check is already running." in html
    assert "Nothing was changed.</td>" not in html                  # lead-in the Result chip already says


def test_history_survives_a_restore_that_replaces_the_database(world):
    """The audit file is beside the database, not in it: replacing library.db
    drops the backup rows made after the snapshot but keeps checks and restores."""
    db = world["db"]
    rs.audit(db, user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore", result="finished",
             message="Restore finished: 4,435 articles from x.", started_at=_iso(5), articles=4435)
    os.remove(db)
    Library(db).close()   # a fresh database, like an older snapshot with no later backups
    html = _page(world)
    assert ">Restore</td>" in html and ">Succeeded</span>" in html
    assert "No off-site backups recorded yet" not in html


def test_history_is_limited_to_fifty_rows(world):
    db = world["db"]
    for i in range(60):
        rs.audit(db, user="admin", file_id="f1", file_name="f.db", mode="check", result="checked",
                 message="x", articles=1)
    assert _page(world).count('data-label="When"') == 50


def test_an_old_audit_line_without_the_new_keys_still_parses():
    line = {"time": _iso(1), "user": "admin", "file_id": "f1", "file_name": "f.db", "mode": "restore",
            "result": "finished", "message": "Restore finished: 4,435 articles from f.db. Kept as x."}
    rows = backup_history.history_rows([], [line], lambda e: e)
    assert rows[0]["detail"] == "4,435 articles" and rows[0]["result"] == "Succeeded"


def test_an_interrupted_run_appears_once_as_interrupted():
    rows = backup_history.history_rows([], [], lambda e: e, {}, {"mode": "restore", "updated_at": _iso(3),
                                                                  "file_name": "f.db", "user": "1"})
    assert [r["result"] for r in rows] == ["Interrupted"]


def test_unreadable_audit_lines_are_skipped(world):
    db = world["db"]
    with open(rs.paths(db)["audit"], "w") as f:
        f.write("not json\n" + json.dumps({"time": _iso(1), "mode": "check", "result": "checked"}) + "\n")
    assert len(rs.read_audit(db)) == 1


# --- C: restore marker on the Drive list ------------------------------------------

def test_drive_list_marks_the_backup_a_restore_used(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore", result="finished", message="x")
    data = world["admin"].get("/admin/library-backup/drive-list").json()
    by = {f["id"]: f["restored"] for f in data["files"]}
    assert by["f1"] == "" and by["f2"] != ""


def test_a_check_or_a_failed_restore_never_marks_a_backup(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="checked", message="x")
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="restore", result="failed", message="x")
    data = world["admin"].get("/admin/library-backup/drive-list").json()
    assert all(f["restored"] == "" for f in data["files"])


def test_marker_falls_back_to_the_file_name_and_takes_the_latest():
    old = {"time": "2026-10-01T10:00:00+00:00", "mode": "restore", "result": "finished", "file_id": "gone", "file_name": "a.db"}
    new = {"time": "2026-10-05T11:30:00+00:00", "mode": "restore", "result": "finished", "file_id": "", "file_name": "a.db"}
    by_id, by_name = backup_history.last_restore_times([old, new])
    assert backup_history.restored_at({"id": "other", "name": "a.db"}, by_id, by_name) == "2026-10-05 11:30"


def test_the_list_script_renders_the_marker(world):
    assert "Restored '+f.restored+' UTC" in _page(world)


# --- audit written by the real script carries the new keys ------------------------

def test_reporter_audit_records_start_time_and_article_count(tmp_path):
    from scripts import restore_from_drive as r
    dest = str(tmp_path / "library.db")
    rep = r.Reporter(dest, "check", "admin", "job1")
    rep.snap = {"id": "f1", "name": "a.db"}
    rep.begin()
    rep.articles = 4435
    rep.end("checked", "Dry run finished: a.db is a valid backup with 4,435 articles. Nothing was changed.")
    rec = rs.read_audit(dest)[-1]
    assert rec["articles"] == 4435 and rec["started_at"] and rec["job_id"] == "job1"


# --- D: phone layout (Chromium) ---------------------------------------------------

def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    for path in [None] + glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        try:
            return pw, (pw.chromium.launch() if path is None else pw.chromium.launch(executable_path=path))
        except Exception:
            continue
    pw.stop()
    return None


def _serve(page, client, origin="http://t.test"):
    def handler(route):
        r = client.get(route.request.url.replace(origin, ""))
        route.fulfill(status=r.status_code, content_type=r.headers.get("content-type", "text/html"), body=r.content)
    page.route("**/*", handler)


def test_both_tables_are_labelled_cards_without_stray_lines_on_a_phone(world):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, b = launched
    try:
        rs.audit(world["db"], user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore",
                 result="finished", message="x", articles=1)
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        pg = ctx.new_page()
        _serve(pg, world["admin"])
        pg.goto("http://t.test/admin/library-backup")
        pg.wait_for_selector(".drive-list-table td")
        m = pg.evaluate("""()=>{const out={};
          for (const sel of ['.drive-list-table','.backup-log-table']){
            const t=document.querySelector(sel); const tds=[...t.querySelectorAll('tbody td')];
            const first=t.querySelector('tbody tr');
            out[sel]={borders:[...new Set(tds.map(td=>getComputedStyle(td).borderTopWidth))],
              unlabelled:tds.filter(td=>!td.getAttribute('data-label')&&!td.colSpan>1).length,
              lastRowBorder:getComputedStyle(t.querySelector('tbody tr:last-child')).borderBottomWidth,
              display:getComputedStyle(first).display};}
          out.overflow=document.documentElement.scrollWidth>document.documentElement.clientWidth;
          out.restored=!!document.querySelector('.dl-restored');
          return out;}""")
        assert not m["overflow"]
        assert m["restored"]
        for sel in ('.drive-list-table', '.backup-log-table'):
            assert m[sel]["borders"] == ["0px"], m          # no line between every cell
            assert m[sel]["unlabelled"] == 0, m              # each field says what it is
            assert m[sel]["lastRowBorder"] == "0px", m       # no doubled line at the frame's foot
            assert m[sel]["display"] == "block"
    finally:
        b.close()
        pw.stop()


def test_page_does_not_overflow_with_a_long_finished_panel(world):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, b = launched
    try:
        db = world["db"]
        rs.write_status(db, state="finished", mode="restore", started_at=_iso(3), finished_at=_iso(1), post_applied=True,
                        message="Restore finished: 4,435 articles from library-20261007-090000.db. The previous database "
                                "is kept as /data/library.db.pre-restore-20261007-101010.")
        rs.log_line(db, "Kept the current database as /data/library.db.pre-restore-20261007-101010-and-a-very-long-unbroken-token-xxxxxxxxxxxxxxxx.")
        pg = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        _serve(pg, world["admin"])
        pg.goto("http://t.test/admin/library-backup")
        pg.wait_for_selector("#restore-panel")
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        b.close()
        pw.stop()
