"""scripts/restore_from_drive.py (RUNBOOK.md section 1, Path C) and the
upload-limit note on /admin/library-backup. Scratch files and a fake Drive
client only: nothing here touches the network or a real database."""
import importlib
import os
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup
from linklib.db import Library
from scripts import restore_from_drive as rfd


def _make_db(path, titles):
    from linklib.db import Article
    lib = Library(path)
    for i, t in enumerate(titles):
        lib.upsert(Article(url=f"https://x.test/{os.path.basename(path)}/{i}", title=t))
    lib.close()


def _count(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "unused.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))

    good = str(tmp_path / "good.db")
    _make_db(good, ["a", "b", "c", "d", "e"])
    snap = backup.snapshot_to_file(good)
    corrupt = str(tmp_path / "corrupt.bin")
    with open(corrupt, "wb") as f:
        f.write(b"this is not a database" * 100)
    notlib = str(tmp_path / "notlib.db")
    sqlite3.connect(notlib).execute("CREATE TABLE t(x)").connection.commit()

    files = {"f-new": {"id": "f-new", "name": "library-20261005-090000.db", "createdTime": "2026-10-05T09:00:00Z",
                       "size": str(os.path.getsize(snap)), "src": snap},
             "f-bad": {"id": "f-bad", "name": "library-20261004-090000.db", "createdTime": "2026-10-04T09:00:00Z",
                       "size": str(os.path.getsize(corrupt)), "src": corrupt},
             "f-notlib": {"id": "f-notlib", "name": "library-20261003-090000.db", "createdTime": "2026-10-03T09:00:00Z",
                          "size": str(os.path.getsize(notlib)), "src": notlib}}

    monkeypatch.setattr(backup, "_access_token", lambda: "tok")
    monkeypatch.setattr(backup, "find_backup_folders", lambda t: [{"id": "folder1", "name": backup.FOLDER_NAME}])
    monkeypatch.setattr(backup, "list_snapshots",
                        lambda t, folder: [{k: v for k, v in f.items() if k != "src"} for f in files.values()])

    def fake_download(token, file_id, dest, chunk=1 << 20):
        import hashlib, shutil
        shutil.copyfile(files[file_id]["src"], dest)
        return os.path.getsize(dest), hashlib.md5(open(dest, "rb").read()).hexdigest()
    monkeypatch.setattr(backup, "download_snapshot", fake_download)

    live = str(tmp_path / "live.db")
    _make_db(live, ["old1", "old2"])
    # a stale sidecar like a crashed WAL-mode app would leave
    open(live + "-wal", "wb").write(b"")
    open(live + "-shm", "wb").write(b"")
    return {"tmp": tmp_path, "live": live, "good": good, "files": files}


def _run(*args):
    return rfd.main(list(args))


def _siblings(tmp):
    # restore-status.json, restore.log and restore-audit.jsonl are the restore's
    # own records (Phase 2 of #714); they are not part of what these tests guard.
    return sorted(p.name for p in tmp.iterdir() if not p.name.startswith("restore-") and p.name != "restore.log")


def test_refuses_existing_destination_without_flag(world, capsys):
    before = open(world["live"], "rb").read()
    with pytest.raises(SystemExit) as e:
        _run("--db", world["live"])
    assert "--yes-replace-live" in str(e.value)
    assert open(world["live"], "rb").read() == before
    assert not [n for n in _siblings(world["tmp"]) if "pre-restore" in n]


def test_dry_run_writes_nothing(world, capsys):
    before = open(world["live"], "rb").read()
    names = _siblings(world["tmp"])
    assert _run("--db", world["live"], "--dry-run") == 0
    assert "Dry run" in capsys.readouterr().out
    assert open(world["live"], "rb").read() == before
    assert _siblings(world["tmp"]) == names  # temp download removed, no pre-restore copy


def test_corrupt_snapshot_refused_destination_untouched(world):
    before = open(world["live"], "rb").read()
    with pytest.raises(SystemExit) as e:
        _run("--db", world["live"], "--snapshot", "f-bad", "--yes-replace-live")
    assert "refused" in str(e.value).lower()
    assert open(world["live"], "rb").read() == before
    assert not [n for n in _siblings(world["tmp"]) if "pre-restore" in n or n.startswith(".restore-")]


def test_non_library_database_refused(world):
    before = open(world["live"], "rb").read()
    with pytest.raises(SystemExit) as e:
        _run("--db", world["live"], "--snapshot", "library-20261003-090000.db", "--yes-replace-live")
    assert "refused" in str(e.value).lower()
    assert open(world["live"], "rb").read() == before


def test_good_snapshot_swaps_in_and_keeps_pre_restore_copy(world, capsys):
    assert _count(world["live"]) == 2
    old_bytes = open(world["live"], "rb").read()
    assert _run("--db", world["live"], "--yes-replace-live") == 0
    assert _count(world["live"]) == 5
    assert not os.path.exists(world["live"] + "-wal")
    assert not os.path.exists(world["live"] + "-shm")
    pre = [n for n in _siblings(world["tmp"]) if ".pre-restore-" in n and not n.endswith(("-wal", "-shm"))]
    assert len(pre) == 1
    pre_path = str(world["tmp"] / pre[0])
    assert _count(pre_path) == 2
    assert open(pre_path, "rb").read()  # real file, still readable
    assert not [n for n in _siblings(world["tmp"]) if n.startswith(".restore-")]
    out = capsys.readouterr().out
    assert "5 articles" in out and "Back up to Drive now" in out
    assert old_bytes  # (the pre-restore copy was taken from this file)


def test_missing_destination_needs_no_flag(world):
    dest = str(world["tmp"] / "scratch.db")
    assert _run("--db", dest) == 0
    assert _count(dest) == 5


def test_list_shows_snapshots_and_picks_newest_by_default(world, capsys):
    assert _run("--db", world["live"], "--list") == 0
    out = capsys.readouterr().out
    assert "library-20261005-090000.db" in out and "library-20261004-090000.db" in out
    assert rfd.pick(backup.list_snapshots("t", "f"), None)["id"] == "f-new"


def test_not_enough_space_refuses_before_download(world, monkeypatch):
    called = []
    monkeypatch.setattr(backup, "download_snapshot", lambda *a, **k: called.append(1))
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    before = open(world["live"], "rb").read()
    with pytest.raises(SystemExit) as e:
        _run("--db", world["live"], "--yes-replace-live")
    assert "Not enough free space" in str(e.value)
    assert not called
    assert open(world["live"], "rb").read() == before


def test_folder_found_by_name_when_db_is_gone(world):
    # No DB to read the folder id from and no env override: search by name.
    tok = "tok"
    assert rfd.resolve_folder(tok, str(world["tmp"] / "nothing.db"), None) == "folder1"
    assert rfd.resolve_folder(tok, str(world["tmp"] / "nothing.db"), "explicit") == "explicit"


def test_script_runs_as_subprocess_with_no_pythonpath():
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "scripts/restore_from_drive.py", "--help"], cwd=str(pathlib.Path(__file__).resolve().parents[1]),
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- upload-limit note on /admin/library-backup -------------------------------

@pytest.fixture
def app_module(monkeypatch, tmp_path):
    db = str(tmp_path / "app.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    return appmod


# --- the Drive helpers added to linklib/backup.py -----------------------------

class _FakeResp:
    def __init__(self, payload=None, body=b""):
        self._p, self._b = payload, body
    def raise_for_status(self): pass
    def json(self): return self._p
    def iter_content(self, n): 
        for i in range(0, len(self._b), n):
            yield self._b[i:i + n]
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_download_snapshot_streams_and_hashes(tmp_path, monkeypatch):
    import hashlib
    body = os.urandom(3_000_000)
    seen = {}
    def fake_get(url, **kw):
        seen.update(url=url, **kw)
        return _FakeResp(body=body)
    monkeypatch.setattr(backup.requests, "get", fake_get)
    dest = str(tmp_path / "d.bin")
    n, md5 = backup.download_snapshot("tok", "FID", dest)
    assert n == len(body) and md5 == hashlib.md5(body).hexdigest()
    assert open(dest, "rb").read() == body
    assert seen["url"].endswith("/files/FID") and seen["params"] == {"alt": "media"} and seen["stream"] is True


def test_list_and_find_use_expected_queries(monkeypatch):
    calls = []
    def fake_get(url, **kw):
        calls.append(kw["params"])
        return _FakeResp({"files": []})
    monkeypatch.setattr(backup.requests, "get", fake_get)
    backup.list_snapshots("t", "F1")
    backup.find_backup_folders("t")
    assert "'F1' in parents" in calls[0]["q"] and "size" in calls[0]["fields"] and "md5Checksum" in calls[0]["fields"]
    assert calls[1]["q"].startswith("name='CFO Navigator") and "folder" in calls[1]["q"]
