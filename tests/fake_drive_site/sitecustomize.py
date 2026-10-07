"""Test-only. Loaded by a real child interpreter (PYTHONPATH) when
FAKE_DRIVE_JSON points at a file listing fake Drive snapshots. It replaces the
four linklib.backup network functions the restore script uses, so a real
subprocess can run the real script without Google Drive."""
import hashlib
import json
import os
import shutil

_cfg = os.environ.get("FAKE_DRIVE_JSON")
if _cfg and os.path.exists(_cfg):
    from linklib import backup
    _files = json.load(open(_cfg))
    backup._access_token = lambda: "tok"
    backup.find_backup_folders = lambda t: [{"id": "folder1", "name": backup.FOLDER_NAME}]
    backup.list_snapshots = lambda t, folder: [
        {"id": f["id"], "name": f["name"], "createdTime": f["createdTime"], "size": str(f["size"])}
        for f in _files]

    def _download(token, file_id, dest, chunk=1 << 20):
        src = next(f["src"] for f in _files if f["id"] == file_id)
        shutil.copyfile(src, dest)
        return os.path.getsize(dest), hashlib.md5(open(dest, "rb").read()).hexdigest()
    backup.download_snapshot = _download
