"""Password hashing and session tokens — standard library only.

No bcrypt, argon2 or passlib dependency: PBKDF2-HMAC-SHA256 is in ``hashlib``,
is a NIST-recommended password KDF, and with a per-user random salt and a high
iteration count is a defensible choice for this system's threat model. Keeping
it stdlib matches the rest of the codebase (e.g. the watchlist screen uses
``difflib`` rather than a fuzzy-match package) and means the auth layer has no
install-time surprises on a minimal container.

The upgrade path is honest and open: the stored hash carries its algorithm and
iteration count as a prefix (``pbkdf2_sha256$…``), so a future move to argon2 or
a higher iteration count can re-hash on next successful login without a
migration. :func:`needs_rehash` reports when a stored hash is below the current
policy.

Session tokens are 256 bits of ``secrets`` randomness. Only their SHA-256 is
ever stored (see :class:`crr.workflow.models.UserSession`): the raw token is
handed to the browser once and never written down, so a dump of the sessions
table cannot be replayed as a login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: Current work factor. OWASP's floor for PBKDF2-HMAC-SHA256 is well below this;
#: chosen to stay comfortably ahead while remaining fast enough for an
#: interactive login on modest hardware.
DEFAULT_ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return a self-describing hash string: ``pbkdf2_sha256$iters$salt$hash``."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash string.

    Returns False rather than raising on any malformed stored value, so a
    corrupt row can never authenticate and never crashes the login path."""
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def needs_rehash(stored: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """True if a stored hash was made with a weaker algorithm or fewer
    iterations than current policy, so a successful login can upgrade it."""
    try:
        algo, iters_s, _salt, _hash = stored.split("$")
    except (ValueError, AttributeError):
        return True
    return algo != _ALGO or int(iters_s) < iterations


def new_session_token() -> str:
    """A fresh 256-bit URL-safe bearer token. Show once, store only its hash."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 of a raw token — what actually goes in the sessions table."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
