import tempfile

import pytest

from linklib.db import Library
from linklib.passwords import verify_password
from scripts import reset_user_password as rup


@pytest.fixture
def db():
    path = tempfile.mktemp(suffix=".db")
    lib = Library(path)
    lib.create_user("bmw", "oldpassword1", role="admin")
    lib.close()
    return path


def _hash(path, u):
    lib = Library(path)
    try:
        return lib.conn.execute("SELECT password_hash FROM users WHERE username=?", (u,)).fetchone()[0]
    finally:
        lib.close()


def test_preview_writes_nothing_and_never_prompts(db, monkeypatch, capsys):
    monkeypatch.setattr(rup.getpass, "getpass", lambda *_: pytest.fail("prompted in preview"))
    before = _hash(db, "bmw")
    assert rup.main(["--db", db, "--username", "bmw"]) == 0
    assert _hash(db, "bmw") == before
    assert "Preview only" in capsys.readouterr().out


def test_apply_resets_via_hidden_prompt_and_verifies(db, monkeypatch):
    monkeypatch.setattr(rup.getpass, "getpass", lambda *_: "freshpassword1")
    assert rup.main(["--db", db, "--username", "bmw", "--apply"]) == 0
    assert verify_password("freshpassword1", _hash(db, "bmw"))


def test_mismatch_and_short_password_write_nothing(db, monkeypatch):
    answers = iter(["freshpassword1", "different"])
    monkeypatch.setattr(rup.getpass, "getpass", lambda *_: next(answers))
    before = _hash(db, "bmw")
    assert rup.main(["--db", db, "--username", "bmw", "--apply"]) == 1
    monkeypatch.setattr(rup.getpass, "getpass", lambda *_: "short")
    assert rup.main(["--db", db, "--username", "bmw", "--apply"]) == 1
    assert _hash(db, "bmw") == before


def test_create_admin_with_generated_password_prints_it_once(db, capsys):
    assert rup.main(["--db", db, "--username", "newadmin", "--create-admin", "--apply", "--generate"]) == 0
    out = capsys.readouterr().out
    pw = out.split("(shown once): ")[1].strip()
    lib = Library(db)
    try:
        assert lib.get_user("newadmin")["role"] == "admin"
    finally:
        lib.close()
    assert verify_password(pw, _hash(db, "newadmin"))


def test_unknown_user_without_create_flag_is_refused(db):
    assert rup.main(["--db", db, "--username", "ghost", "--apply", "--generate"]) == 1


def test_no_password_command_line_argument():
    with pytest.raises(SystemExit):
        rup.main(["--db", "x", "--username", "bmw", "--password", "p"])
