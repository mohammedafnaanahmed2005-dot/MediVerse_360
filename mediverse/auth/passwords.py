"""Password hashing: PBKDF2-HMAC-SHA256 with per-user salt and a server-side pepper.

Stored format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.
Plaintext passwords are never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

ITERATIONS = 600_000
_MIN_LEN = 12


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _peppered(password: str, pepper: str) -> bytes:
    # HMAC the password with the pepper first so the pepper never sits next to the hash.
    return hmac.new(pepper.encode(), password.encode("utf-8"), hashlib.sha256).digest()


def hash_password(password: str, pepper: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", _peppered(password, pepper), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None, pepper: str) -> bool:
    if not stored or not password:
        # Still burn comparable CPU so timing does not reveal unknown usernames.
        hashlib.pbkdf2_hmac("sha256", b"x", b"0" * 16, ITERATIONS)
        return False
    try:
        algo, it, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", _peppered(password, pepper), base64.b64decode(salt_b64), int(it))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def password_policy_errors(password: str, username: str = "") -> list[str]:
    errs = []
    if len(password) < _MIN_LEN:
        errs.append(f"at least {_MIN_LEN} characters")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        errs.append("upper- and lower-case letters")
    if not re.search(r"[0-9]", password):
        errs.append("a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        errs.append("a symbol")
    if username and username.lower() in password.lower():
        errs.append("must not contain the username")
    return errs
