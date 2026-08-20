"""Signup and login for the chat endpoint's free-trial-then-bring-your-own-key model."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from kb.api.schemas import AuthOut, UserOut
from kb.auth.deps import get_current_user
from kb.auth.security import create_access_token, hash_password, verify_password
from kb.config import get_settings
from kb.db.models import User
from kb.db.repositories import UserRepository
from kb.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    identifier: str = Field(description="Email or username.", min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, db: Session = Depends(get_db)) -> AuthOut:
    repo = UserRepository(db)
    if repo.get_by_email(body.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered."
        )
    if repo.get_by_username(body.username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken.")

    user = repo.create(
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        free_runs_remaining=get_settings().free_runs_per_user,
    )
    token = create_access_token(user.id, user.role)
    return AuthOut(access_token=token, user=UserOut.from_user(user))


@router.post("/login", response_model=AuthOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> AuthOut:
    repo = UserRepository(db)
    identifier = body.identifier.strip().lower()
    user = repo.get_by_email(identifier) or repo.get_by_username(body.identifier.strip())
    if user is None or not verify_password(body.password, user.password_hash):
        # Same message either way: don't reveal whether the account exists.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    token = create_access_token(user.id, user.role)
    return AuthOut(access_token=token, user=UserOut.from_user(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(user)
