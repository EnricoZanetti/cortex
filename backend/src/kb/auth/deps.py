"""FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from kb.auth.security import InvalidTokenError, decode_access_token
from kb.db.models import User, UserRole
from kb.db.repositories import UserRepository
from kb.db.session import get_db

BEARER_PREFIX = "bearer "


def _extract_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith(BEARER_PREFIX):
        token = authorization[len(BEARER_PREFIX) :].strip()
        return token or None
    return None


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials. Send 'Authorization: Bearer <token>'.",
        )
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = UserRepository(db).get_by_id(payload.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists."
        )
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user
