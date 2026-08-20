"""Password hashing and JWT signing for chat logins."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import bcrypt
import jwt

from kb.config import get_settings
from kb.db.models import UserRole

#: bcrypt silently ignores bytes past 72; truncating first makes that limit explicit
#: rather than a surprise for a long-but-legitimate passphrase.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(truncated, password_hash.encode("utf-8"))


class InvalidTokenError(Exception):
    """Raised for any unusable token: expired, malformed, or badly signed."""


@dataclass(frozen=True)
class TokenPayload:
    user_id: uuid.UUID
    role: UserRole


def create_access_token(user_id: uuid.UUID, role: UserRole) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm]
        )
        return TokenPayload(user_id=uuid.UUID(payload["sub"]), role=UserRole(payload["role"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError("Invalid or expired token.") from exc
