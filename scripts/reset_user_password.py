#!/usr/bin/env python3
"""Reset a user's password, or create an admin account (recovery path).

The shared-secret "break-glass" login was retired (issue #627), so a lost
admin password is recovered here, on the container, via `railway ssh`.
Always pass the absolute DB path (`--db /data/library.db`): a relative path
silently resolves against whatever directory the shell is in.

Preview (default, writes nothing, asks for no password):
    python -m scripts.reset_user_password --db /data/library.db --username bmw

Reset an existing user (prompts for the new password twice, hidden):
    python -m scripts.reset_user_password --db /data/library.db --username bmw --apply

Generate a password instead of typing one (printed once, never logged):
    python -m scripts.reset_user_password --db /data/library.db --username bmw --apply --generate

Create an admin that does not exist yet:
    python -m scripts.reset_user_password --db /data/library.db --username bmw --create-admin --apply

The password is never accepted as a command-line argument (it would land in
shell history). After writing, the script reads the row back and checks the
new password verifies against what was stored.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.passwords import verify_password

MIN_LEN = 8


def _read_back_ok(lib: Library, username: str, password: str) -> bool:
    row = lib.conn.execute(
        "SELECT password_hash, active FROM users WHERE username=?", (username,)
    ).fetchone()
    return bool(row and row["active"] and verify_password(password, row["password_hash"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reset a user's password or create an admin.")
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--username", required=True)
    ap.add_argument("--create-admin", action="store_true",
                    help="Create the account as an admin if it does not exist")
    ap.add_argument("--generate", action="store_true",
                    help="Generate a random password and print it once")
    ap.add_argument("--apply", action="store_true", help="Write the change (default is preview)")
    args = ap.parse_args(argv)

    username = args.username.strip().lower()
    db_path = resolve_db_path(args.db)
    print(f"Database: {os.path.abspath(db_path)}")
    lib = Library(db_path)
    try:
        user = lib.get_user(username)
        if user is None and not args.create_admin:
            print(f"No user {username!r}. Pass --create-admin to create it.")
            return 1
        if user is None:
            print(f"Would CREATE admin {username!r}.")
        else:
            state = "active" if user["active"] else "INACTIVE (will be reactivated)"
            print(f"Would RESET the password for {username!r} (role={user['role']}, {state}).")
        if not args.apply:
            print("Preview only. Re-run with --apply to write.")
            return 0

        if args.generate:
            password = secrets.token_urlsafe(16)
        else:
            password = getpass.getpass("New password: ")
            if password != getpass.getpass("Repeat it: "):
                print("Passwords did not match. Nothing written.")
                return 1
        if len(password) < MIN_LEN:
            print(f"Password must be at least {MIN_LEN} characters. Nothing written.")
            return 1

        if user is None:
            lib.create_user(username, password, role="admin", password_change_recommended=False)
        else:
            lib.set_user_password(user["id"], password)
            lib.set_password_change_recommended(user["id"], False)
            lib.conn.execute("UPDATE users SET active=1 WHERE id=?", (user["id"],))
            lib.conn.commit()

        if not _read_back_ok(lib, username, password):
            print("READ-BACK FAILED: the stored password does not verify. Investigate before relying on it.")
            return 2
        print(f"Done. {username!r} verified by read-back.")
        if args.generate:
            print(f"Generated password (shown once): {password}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
