#!/usr/bin/env python3
"""Mint or revoke an MCP server API token (/mcp, Phase 1).

Every token resolves to a real users.id + that user's current role — see
CLAUDE.md's MCP section for why (the legacy flat LINKLIB_SAVE_TOKEN carries
no identity, which would silently bypass Ask/matchmaker dollar caps if it
were reused here). There's no admin UI for this yet; it's meant to be run
by hand via `railway ssh`, with the absolute DB path
(`--db /data/library.db` — a relative path silently resolves against
whatever directory the shell happens to be in and fails with no error, per
resolve_db_path's own docstring).

Mint:
    python -m scripts.mint_api_token --db /data/library.db --username bmw --label "claude-mcp"

Prints the plaintext token exactly once. Only its sha256 hash is ever
stored — copy it somewhere safe immediately; there is no way to recover it
later, only to mint a new one.

List existing tokens (labels/owners/usage, never the token itself):
    python -m scripts.mint_api_token --db /data/library.db --list

Revoke:
    python -m scripts.mint_api_token --db /data/library.db --revoke 3
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Mint, list, or revoke MCP API tokens.")
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--username", default=None, help="Mint a token for this existing user")
    ap.add_argument("--label", default="", help="Human-readable label for the minted token")
    ap.add_argument("--revoke", type=int, default=None, metavar="TOKEN_ID", help="Revoke a token by id")
    ap.add_argument("--list", action="store_true", help="List existing tokens (no secrets shown)")
    args = ap.parse_args()

    if not args.username and args.revoke is None and not args.list:
        ap.error("pass --username to mint, --revoke TOKEN_ID to revoke, or --list to list")

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        if args.list:
            rows = lib.list_api_tokens()
            if not rows:
                print("No tokens.")
                return 0
            for r in rows:
                status = "revoked" if r["revoked_at"] else "active"
                print(f"#{r['id']}  {r['username']:<20} {status:<8} "
                      f"label={r['label']!r} created={r['created_at']} "
                      f"last_used={r['last_used_at'] or 'never'}")
            return 0

        if args.revoke is not None:
            ok = lib.revoke_api_token(args.revoke)
            print(f"Revoked token #{args.revoke}." if ok else f"No active token #{args.revoke} found.")
            return 0

        user = lib.get_user(args.username)
        if not user:
            print(f"No such user: {args.username!r}", file=sys.stderr)
            return 1
        if not user["active"]:
            print(f"Warning: user {args.username!r} is not active — the minted token "
                  f"will fail verification until the account is reactivated.", file=sys.stderr)

        token_id, plaintext = lib.create_api_token(user["id"], label=args.label)

        # Write-then-read-back verification per CLAUDE.md's one-off-fix
        # discipline — confirms the row actually landed before telling the
        # operator the plaintext is real and usable.
        rows = lib.list_api_tokens(user_id=user["id"])
        row = next((r for r in rows if r["id"] == token_id), None)
        assert row is not None, "token row did not read back after insert"

        print(f"Minted token #{token_id} for user {args.username!r} (role={user['role']}).")
        print(f"Label: {args.label or '(none)'}")
        print()
        print("Plaintext token (copy this now — it will never be shown again):")
        print(plaintext)
        print()
        print("Connector header value: " + f"Bearer {plaintext}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
