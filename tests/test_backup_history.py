"""/admin/library-backup: one table, a row per backup file in Drive plus a row per
failure, a result panel that expires, and the phone layout.

Drive is the base. Each file row takes its article count from backup_log (inside
the database) and its latest check and restore from restore-audit.jsonl (on the
volume, so it survives a restore). Chromium tests skip where no Chromium exists,
as in CI."""
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


def test_failed_panel_expires_too_and_the_failure_stays_on_the_files_row(world):
    db = world["db"]
    rs.write_status(db, state="failed", mode="check", message="Boom.", started_at=_iso(200), finished_at=_iso(190))
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="failed",
             message="The check failed: md5 does not match Drive's checksum.")
    assert 'id="restore-panel"' not in _page(world)
    _, rows = _rows(world)
    assert [r for r in rows if r["kind"] == "failure"] == []
    c = _file(rows, "f1")["check"]
    assert "md5 does not match Drive" in c["reason"] and c["result"] == "Failed"


def test_a_running_job_always_shows(world):
    db = world["db"]
    rs.write_status(db, state="running", mode="restore", pid=os.getpid(), started_at=_iso(200), stage="downloading")
    assert 'data-state="running"' in _page(world)


def test_the_window_is_one_named_constant(world):
    assert world["app"]._RESULT_PANEL_MINUTES == 30


# --- B: the one table ---------------------------------------------------------------

def _rows(w):
    data = w["admin"].get("/admin/library-backup/drive-list").json()
    return data, data["rows"]


def _file(rows, fid):
    return next(r for r in rows if r["kind"] == "file" and r["id"] == fid)


def test_the_history_table_is_gone_and_there_is_one_table(world):
    html = _page(world)
    assert "Backup and restore history" not in html
    assert "backup-log-table" not in html
    assert html.count("<h2") >= 1 and "Backups in Drive" in html
    assert html.count("bk-stack") >= 1 and "drive-list" in html


def test_a_file_row_carries_articles_last_check_and_restore(world):
    db = world["db"]
    lib = Library(db)
    lib.record_backup_attempt("success", filename=FILES[0]["name"], drive_file_id="f1", size_bytes=1, row_count=4435)
    lib.close()
    rs.audit(db, time=_iso(30), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="checked", message="x")
    rs.audit(db, time=_iso(10), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="checked", message="x")
    rs.audit(db, time=_iso(20), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="restore", result="finished", message="x")
    _, rows = _rows(world)
    f1, f2 = _file(rows, "f1"), _file(rows, "f2")
    assert f1["articles"] == 4435
    assert f1["check"]["result"] == "Succeeded" and f1["restored"]["by"] == "admin"
    assert f2["articles"] is None and f2["check"] is None and f2["restored"] is None
    assert [r["kind"] for r in rows] == ["file", "file"]      # successes make no row of their own


def test_the_latest_check_wins_and_a_check_never_counts_as_a_restore(world):
    db = world["db"]
    rs.audit(db, time=_iso(60), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="checked", message="x")
    rs.audit(db, time=_iso(5), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="failed", message="The check failed: md5 does not match.")
    _, rows = _rows(world)
    f1 = _file(rows, "f1")
    assert f1["check"]["result"] == "Failed" and f1["restored"] is None


def test_matching_falls_back_to_the_file_name(world):
    db = world["db"]
    lib = Library(db)
    lib.record_backup_attempt("success", filename=FILES[1]["name"], drive_file_id="", size_bytes=1, row_count=77)
    lib.close()
    rs.audit(db, user="admin", file_id="", file_name=FILES[1]["name"], mode="restore", result="finished", message="x")
    _, rows = _rows(world)
    f2 = _file(rows, "f2")
    assert f2["articles"] == 77 and f2["restored"] is not None


def test_failures_get_one_row_each_with_plain_reasons(world):
    db = world["db"]
    lib = Library(db)
    lib.record_backup_attempt("failure", error="HTTP 404")
    lib.close()
    rs.audit(db, user="admin", file_id="gone1", file_name="old1.db", mode="check", result="failed",
             message="The check failed: md5 does not match Drive's checksum. Nothing was changed.")
    rs.audit(db, user="admin", file_id="gone2", file_name="old2.db", mode="restore", result="refused",
             message="Another restore or check is already running. Nothing was changed.")
    _, rows = _rows(world)
    fails = [r for r in rows if r["kind"] == "failure"]
    assert sorted((r["action"], r["result"]) for r in fails) == [("Backup", "Failed"), ("Check", "Failed"), ("Restore", "Refused")]
    by = {r["action"]: r for r in fails}
    assert "Google could not find the backup folder." in by["Backup"]["detail"]
    assert by["Check"]["detail"] == "md5 does not match Drive's checksum."      # lead-in and tail dropped
    assert by["Restore"]["detail"] == "Another restore or check is already running."


def test_a_failed_check_of_a_file_gone_from_drive_still_gets_a_failure_row_but_a_success_is_dropped(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="gone", file_name="old.db", mode="check", result="checked", message="x")
    rs.audit(db, user="admin", file_id="gone", file_name="old.db", mode="restore", result="finished", message="x")
    rs.audit(db, user="admin", file_id="gone", file_name="old.db", mode="check", result="failed", message="The check failed: nope.")
    _, rows = _rows(world)
    assert [(r["kind"], r.get("file"), r["result"] if r["kind"] == "failure" else "") for r in rows if r["kind"] == "failure"] == [("failure", "old.db", "Failed")]
    assert sum(1 for r in rows if r["kind"] == "file") == 2


def test_rows_are_ordered_by_time_with_failures_interleaved(world):
    db = world["db"]
    rs.audit(db, time="2026-10-06T21:00:00+00:00", user="admin", file_id="x", file_name="x.db", mode="restore", result="failed", message="The restore failed: boom.")
    _, rows = _rows(world)
    assert [r["sort"] for r in rows] == sorted((r["sort"] for r in rows), reverse=True)
    assert [r["kind"] for r in rows] == ["file", "failure", "file"]   # 10-07 09:00, 10-06 21:00, 10-06 09:00


def test_failure_rows_show_when_drive_cannot_be_reached(world, monkeypatch):
    monkeypatch.setattr(backup, "list_for_display", lambda db_path, timeout=10.0: {"ok": False, "files": [], "message": "Could not reach Google Drive just now."})
    lib = Library(world["db"])
    lib.record_backup_attempt("failure", error="HTTP 404")
    lib.close()
    data, rows = _rows(world)
    assert data["ok"] is False and [r["action"] for r in rows] == ["Backup"]


def test_only_the_newest_twenty_failures_show(world):
    for i in range(30):
        rs.audit(world["db"], user="admin", file_id="x", file_name="x.db", mode="check", result="failed", message="The check failed: n.")
    _, rows = _rows(world)
    assert sum(1 for r in rows if r["kind"] == "failure") == 20


def test_a_file_whose_database_log_row_a_restore_deleted_still_gets_its_buttons(world):
    """Restoring an older snapshot replaces library.db, so backup_log loses the newer rows.
    The newer file is still in Drive and must still be listed with Check and Restore."""
    db = world["db"]
    lib = Library(db)
    lib.record_backup_attempt("success", filename=FILES[0]["name"], drive_file_id="f1", size_bytes=1, row_count=4435)
    lib.close()
    rs.audit(db, user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore", result="finished", message="x")
    os.remove(db)
    Library(db).close()   # a fresh database, like an older snapshot with no later backups
    _, rows = _rows(world)
    f1 = _file(rows, "f1")
    assert f1["articles"] is None and f1["name"] == FILES[0]["name"]
    page = _page(world)
    assert "/admin/library-backup/check/" in page and "/admin/library-backup/restore/" in page
    assert "removes that number for backups made after it" in page


def test_an_old_audit_line_without_the_new_keys_still_parses():
    line = {"time": _iso(1), "user": "admin", "file_id": "f1", "file_name": "f.db", "mode": "restore",
            "result": "finished", "message": "Restore finished: 4,435 articles from f.db. Kept as x."}
    rows = backup_history.table_rows([{"id": "f1", "name": "f.db", "size": 1, "created": _iso(60)}], [], [line], lambda e: e)
    assert rows[0]["restored"]["when"]


def test_unreadable_audit_lines_are_skipped(world):
    db = world["db"]
    with open(rs.paths(db)["audit"], "w") as f:
        f.write("not json\n" + json.dumps({"time": _iso(1), "mode": "check", "result": "checked"}) + "\n")
    assert len(rs.read_audit(db)) == 1


# --- interrupted runs become a durable failure row, exactly once -----------------------

def _stale_running(db, job_id="job-stale"):
    rs.write_status(db, state="running", mode="restore", pid=2_000_000_000, started_at=_iso(120), job_id=job_id,
                    file_id="f1", file_name=FILES[0]["name"], user="1", stage="downloading")


def test_an_interrupted_restore_of_a_drive_file_shows_on_its_row_and_is_audited_exactly_once(world):
    db = world["db"]
    _stale_running(db)
    for _ in range(3):                                  # three page loads and three list loads
        _page(world)
        _rows(world)
    lines = [r for r in rs.read_audit(db) if r.get("result") == "interrupted"]
    assert len(lines) == 1 and lines[0]["job_id"] == "job-stale" and lines[0]["file_id"] == "f1"
    _, rows = _rows(world)
    assert [r for r in rows if r["kind"] == "failure"] == []        # f1 is in Drive: it shows on its own row
    assert _file(rows, "f1")["restored"]["result"] == "Interrupted"


def test_an_interrupted_run_is_kept_when_the_next_run_wipes_the_status(world):
    db = world["db"]
    _stale_running(db)
    assert rs.record_interrupted(db) is True
    rs.reset_status(db, state="running", pid=os.getpid(), job_id="job-next", started_at=_iso(0))   # the next run starts
    assert rs.record_interrupted(db) is False                                   # live process: nothing to record
    _, rows = _rows(world)
    assert _file(rows, "f1")["restored"]["result"] == "Interrupted"


def test_a_live_run_is_not_recorded_as_interrupted(world):
    db = world["db"]
    rs.write_status(db, state="running", mode="check", pid=os.getpid(), started_at=_iso(1), job_id="live")
    assert rs.record_interrupted(db) is False and rs.read_audit(db) == []


def test_starting_a_run_records_the_stale_one_first(world, monkeypatch):
    db = world["db"]
    _stale_running(db, "job-old")
    monkeypatch.setattr(world["app"].threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    r = world["admin"].post("/admin/library-backup/check/f1", follow_redirects=False)
    assert r.status_code == 303
    backup.release_exclusive()  # the stubbed thread never ran, so it would never release the backup lock
    lines = [x for x in rs.read_audit(db) if x.get("result") == "interrupted"]
    assert [x["job_id"] for x in lines] == ["job-old"]


# --- C: the Restored cell and the Drive failure message ---------------------------------

def test_a_failed_restore_shows_in_the_last_restore_cell_with_its_reason(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="restore", result="failed",
             message="The restore failed: disk is full. Nothing was changed.")
    _, rows = _rows(world)
    r = _file(rows, "f1")["restored"]
    assert (r["result"], r["reason"], r["by"]) == ("Failed", "disk is full.", "admin")


def test_the_script_renders_the_cells(world):
    page = _page(world)
    for head in ("Made (UTC)", "Backup", "Size", "Articles", "Last check", "Check date", "Last restore",
                 "Restore date", "Details", "Actions"):
        assert f"'{head}'" in page
    assert "Not checked" in page


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


def test_the_table_is_labelled_cards_without_stray_lines_on_a_phone(world):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, b = launched
    try:
        rs.audit(world["db"], user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore",
                 result="finished", message="x", articles=1)
        rs.audit(world["db"], user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check",
                 result="failed", message="The check failed: md5 does not match Drive's checksum.")
        rs.audit(world["db"], user="admin", file_id="gone", file_name="old.db", mode="check",
                 result="failed", message="The check failed: gone.")
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        pg = ctx.new_page()
        _serve(pg, world["admin"])
        pg.goto("http://t.test/admin/library-backup")
        pg.wait_for_selector(".bk-table td")
        m = pg.evaluate("""()=>{const t=document.querySelector('.bk-table'); const tds=[...t.querySelectorAll('tbody td')].filter(td=>getComputedStyle(td).display!=='none');
          const fail=t.querySelector('tr.bk-failure');
          return {borders:[...new Set(tds.map(td=>getComputedStyle(td).borderTopWidth))],
            unlabelled:tds.filter(td=>!td.getAttribute('data-label')).length,
            lastRowBorder:getComputedStyle(t.querySelector('tbody tr:last-child')).borderBottomWidth,
            display:getComputedStyle(t.querySelector('tbody tr')).display,
            failVisibleCells:[...fail.children].filter(td=>getComputedStyle(td).display!=='none').map(td=>td.getAttribute('data-label')),
            overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,
            restored:[...t.querySelectorAll('td[data-label="Restore date"]')].some(td=>td.textContent.includes('UTC'))};}""")
        assert not m["overflow"], m
        assert m["restored"], m
        assert m["borders"] == ["0px"], m          # no line between every cell
        assert m["unlabelled"] == 0, m              # each field says what it is
        assert m["lastRowBorder"] == "0px", m       # no doubled line at the frame's foot
        assert m["display"] == "block"
        assert m["failVisibleCells"] == ["Made", "Backup", "Last check", "Check date", "Details"], m   # the not-applicable cells are hidden
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


# --- a failure on a file in Drive belongs on that file's row -------------------------------

def test_a_failed_check_of_a_drive_file_shows_in_last_check_and_makes_no_row(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="failed",
             message="The check failed: md5 does not match Drive's checksum. Nothing was changed.")
    _, rows = _rows(world)
    c = _file(rows, "f1")["check"]
    assert (c["result"], c["reason"]) == ("Failed", "md5 does not match Drive's checksum.")
    assert [r for r in rows if r["kind"] == "failure"] == []


def test_a_refused_restore_shows_in_last_restore_and_makes_no_row(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="f2", file_name=FILES[1]["name"], mode="restore", result="refused",
             message="Another restore or check is already running. Nothing was changed.")
    _, rows = _rows(world)
    r = _file(rows, "f2")["restored"]
    assert (r["result"], r["reason"], r["by"]) == ("Refused", "Another restore or check is already running.", "admin")
    assert [x for x in rows if x["kind"] == "failure"] == []


def test_a_failed_backup_still_gets_its_own_row(world):
    lib = Library(world["db"])
    lib.record_backup_attempt("failure", error="HTTP 404")
    lib.close()
    _, rows = _rows(world)
    assert [(r["action"], r["result"]) for r in rows if r["kind"] == "failure"] == [("Backup", "Failed")]


def test_check_and_restore_attempts_on_a_file_no_longer_in_drive_still_get_rows(world):
    db = world["db"]
    rs.audit(db, user="admin", file_id="gone", file_name="old.db", mode="check", result="failed", message="The check failed: x.")
    rs.audit(db, user="admin", file_id="gone", file_name="old.db", mode="restore", result="refused", message="y")
    rs.audit(db, user="admin", file_id="", file_name="", mode="restore", result="failed", message="The restore failed: z.")  # unknown file
    _, rows = _rows(world)
    assert sorted((r["action"], r["result"]) for r in rows if r["kind"] == "failure") == \
        [("Check", "Failed"), ("Restore", "Failed"), ("Restore", "Refused")]


def test_a_file_with_a_good_restore_and_a_later_failed_check_shows_both(world):
    db = world["db"]
    rs.audit(db, time=_iso(30), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="restore", result="finished", message="x")
    rs.audit(db, time=_iso(5), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check", result="failed",
             message="The check failed: md5 does not match.")
    _, rows = _rows(world)
    f1 = _file(rows, "f1")
    assert f1["restored"]["result"] == "Succeeded" and f1["restored"]["reason"] == ""
    assert f1["check"]["result"] == "Failed" and f1["check"]["reason"]
    assert [r for r in rows if r["kind"] == "failure"] == []


def test_the_script_shows_the_reason_and_the_new_labels(world):
    page = _page(world)
    assert "'Last restore'" in page and "'Restored'" not in page and "Not used" in page
    assert "f.check.reason" in page and "f.restored.reason" in page


# --- ten columns: date, who and reason each in their own field -----------------------------

def _td(page, label):
    return page.locator(f'tr:not(.bk-failure) td[data-label="{label}"]')


def _phone_or_desktop(world, width, setup):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, b = launched
    try:
        setup()
        ctx = b.new_context(viewport={"width": width, "height": 900}, has_touch=width < 700, is_mobile=width < 700)
        pg = ctx.new_page()
        _serve(pg, world["admin"])
        pg.goto("http://t.test/admin/library-backup")
        pg.wait_for_selector(".bk-table td")
        return pw, b, pg
    except Exception:
        b.close()
        pw.stop()
        raise


def test_reason_appears_only_in_details_and_date_and_by_in_their_own_fields(world):
    def setup():
        rs.audit(world["db"], time=_iso(30), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="restore",
                 result="finished", message="x")
        rs.audit(world["db"], time=_iso(5), user="admin", file_id="f1", file_name=FILES[0]["name"], mode="check",
                 result="failed", message="The check failed: md5 does not match Drive's checksum. Nothing was changed.")
    pw, b, pg = _phone_or_desktop(world, 1280, setup)
    try:
        row = pg.locator("tr:not(.bk-failure)").filter(has_text=FILES[0]["name"])
        cell = lambda label: row.locator(f'td[data-label="{label}"]').inner_text()
        assert cell("Last check").strip() == "Failed"
        assert cell("Last restore").strip() == "Succeeded"
        assert "md5" not in cell("Last check") and "md5" not in cell("Check date")
        assert "UTC" in cell("Check date") and "by admin" in cell("Check date")
        assert "UTC" in cell("Restore date") and "by admin" in cell("Restore date")
        assert "by" not in cell("Last check") and "UTC" not in cell("Last check")
        details = cell("Details")
        assert "Check: md5 does not match Drive's checksum." in details
        assert "Restore:" not in details                    # a successful restore has nothing to explain
        for other in ("Made", "Backup", "Size", "Articles", "Last restore", "Restore date"):
            assert "md5" not in cell(other)
    finally:
        b.close()
        pw.stop()


def test_row_buttons_are_renamed_and_exactly_as_wide_on_desktop_and_phone(world):
    for width in (1280, 390):
        pw, b, pg = _phone_or_desktop(world, width, lambda: None)
        try:
            row = pg.locator("tbody tr:not(.bk-failure)").first
            cb = row.locator("button:has-text('Check backup')").bounding_box()
            rb = row.locator("a:has-text('Restore backup')").bounding_box()
            assert cb and rb, width
            assert cb["width"] == rb["width"], (width, cb, rb)
            assert abs(cb["height"] - rb["height"]) < 1, (width, cb, rb)
            assert pg.locator("text=Check this backup").count() == 0 and pg.locator("text=Restore from backup").count() == 0
        finally:
            b.close()
            pw.stop()


def test_a_check_line_carries_the_user_and_an_old_line_without_one_shows_a_dash(world):
    db = world["db"]
    rs.audit(db, time=_iso(5), user="7", file_id="f1", file_name=FILES[0]["name"], mode="check", result="finished", message="ok")
    rs.audit(db, time=_iso(5), file_id="f2", file_name=FILES[1]["name"], mode="check", result="finished", message="ok")  # no user key
    _, rows = _rows(world)
    assert _file(rows, "f1")["check"]["by"] == "7"
    assert _file(rows, "f2")["check"]["by"] == ""
    page = _page(world)
    assert "if(a.by)c.appendChild(el('div','bk-sub','by '+a.by))" in page   # nothing shown, not "by undefined"


def test_the_real_script_records_the_user_on_a_check_line(tmp_path):
    from scripts import restore_from_drive as r
    dest = str(tmp_path / "library.db")
    rep = r.Reporter(dest, "check", "42", "job1")
    rep.snap = {"id": "f1", "name": "x.db"}
    rep.end("checked", "Check passed.")
    last = rs.read_audit(dest)[-1]
    assert (last["mode"], last["user"]) == ("check", "42")


def test_failure_rows_use_the_same_columns(world):
    lib = Library(world["db"])
    lib.record_backup_attempt("failure", error="HTTP 404")
    lib.close()
    rs.audit(world["db"], user="admin", file_id="gone", file_name="old.db", mode="check", result="failed",
             message="The check failed: gone from Drive. Nothing was changed.")
    rs.audit(world["db"], user="admin", file_id="gone2", file_name="older.db", mode="restore", result="refused",
             message="Another restore is already running. Nothing was changed.")
    pw, b, pg = _phone_or_desktop(world, 1280, lambda: None)
    try:
        fails = pg.locator("tr.bk-failure")
        assert fails.count() == 3
        text = lambda r, label: r.locator(f'td[data-label="{label}"]').inner_text()
        by_file = {}
        for i in range(3):
            r = fails.nth(i)
            by_file[text(r, "Backup").strip().split("\n")[0]] = r
        chk = by_file["old.db"]
        assert text(chk, "Last check").strip() == "Failed" and "UTC" in text(chk, "Check date")
        assert text(chk, "Details").strip() == "Check: gone from Drive."
        assert text(chk, "Last restore").strip() == "\u2014"
        rst = by_file["older.db"]
        assert text(rst, "Last restore").strip() == "Refused" and "by admin" in text(rst, "Restore date")
        assert text(rst, "Details").strip() == "Restore: Another restore is already running."
        bk = by_file["Backup failed"]
        assert text(bk, "Details").strip().startswith("Backup:")
    finally:
        b.close()
        pw.stop()
