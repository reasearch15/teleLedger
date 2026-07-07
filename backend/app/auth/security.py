from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("telegram-ledger-dummy-password")
_USERNAME_PATTERN = re.compile(r"\A[a-z0-9_.-]{3,64}\Z")
_JWT_ALGORITHM = "HS256"
_STAFF_COLORS = (
    "#2563EB",
    "#7C3AED",
    "#EA580C",
    "#0D9488",
    "#DB2777",
    "#4F46E5",
    "#059669",
    "#B45309",
)


class InvalidSessionTokenError(Exception):
    """Raised when an authentication cookie cannot be trusted."""


def normalize_username(username: str) -> str:
    """Normalize and validate a local username."""
    normalized = username.strip().lower()
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "Username must be 3-64 characters using letters, numbers, '.', '_' or '-'"
        )
    return normalized


def staff_color_for_username(username: str) -> str:
    """Return a deterministic, permanent display color for an account."""
    digest = sha256(normalize_username(username).encode("utf-8")).digest()
    return _STAFF_COLORS[digest[0] % len(_STAFF_COLORS)]


def validate_new_password(password: str) -> str:
    """Apply the local account password policy."""
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must be between 12 and 128 characters")
    return password


def hash_password(password: str) -> str:
    """Hash a policy-compliant password with the recommended Argon2 parameters."""
    return _PASSWORD_HASH.hash(validate_new_password(password))


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Verify a password and return a replacement hash when parameters changed."""
    try:
        return _PASSWORD_HASH.verify_and_update(password, password_hash)
    except UnknownHashError:
        return False, None


def run_dummy_password_check(password: str) -> None:
    """Reduce account-enumeration timing differences for unknown usernames."""
    _PASSWORD_HASH.verify(password, _DUMMY_PASSWORD_HASH)


def create_session_token(
    user_id: int,
    secret_key: str,
    lifetime_minutes: int,
) -> str:
    """Create a signed, short-lived local session token."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "session",
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=_JWT_ALGORITHM)


def decode_session_token(token: str, secret_key: str) -> int:
    """Validate a signed session token and return its user ID."""
    try:
        payload = cast(
            dict[str, object],
            jwt.decode(
                token,
                secret_key,
                algorithms=[_JWT_ALGORITHM],
                options={"require": ["sub", "type", "iat", "exp"]},
            ),
        )
        if payload.get("type") != "session":
            raise InvalidSessionTokenError("Unexpected token type")
        subject = payload.get("sub")
        if not isinstance(subject, str):
            raise InvalidSessionTokenError("Invalid token subject")
        user_id = int(subject)
        if user_id <= 0:
            raise InvalidSessionTokenError("Invalid token subject")
        return user_id
    except (InvalidTokenError, TypeError, ValueError) as error:
        raise InvalidSessionTokenError("Invalid or expired session") from error
