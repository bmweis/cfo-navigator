"""Password hashing with the stdlib only — no bcrypt/argon dependency.

Uses scrypt (memory-hard) with a per-password random salt. Stored format:
    scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
so the work factors travel with the hash and can be raised later without
breaking existing rows.
"""
from __future__ import annotations

import hashlib
import hmac
import os

# scrypt parameters: ~16 MB, a reasonable interactive-login cost.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a stored hash. False on any malformed input."""
    try:
        algo, n, r, p, salt_hex, hash_hex = (stored or "").split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False
