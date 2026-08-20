"""Signup, login, and the free-run quota gate on POST /chat.

Runs against a real Postgres (the users table needs actual unique-constraint and
atomic-update behaviour), so it is marked ``integration`` like the rest of the
DB-backed suite and skips automatically when Postgres is unreachable.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from kb.agent import catalog
from kb.agent.events import TextDelta, TurnDone
from kb.agent.service import ChatService
from kb.auth.bootstrap import upsert_admin_user
from kb.auth.security import hash_password
from kb.db.repositories import UserRepository
from kb.db.session import get_engine, session_scope

pytestmark = pytest.mark.integration


class StubProvider:
    """A model that never calls a tool: quota tests only care about the gate."""

    async def run(self, *, model, system, messages, tools, execute_tool):
        yield TextDelta(text="ok")
        yield TurnDone(stop_reason="end_turn")


def services_available() -> bool:
    from sqlalchemy import text

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    if not services_available():
        pytest.skip("Postgres is not reachable")
    monkeypatch.setattr(catalog, "provider_api_key", lambda provider: "operator-key")
    from kb.api.main import create_app
    from kb.api.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router, "_service", ChatService(provider_factory=lambda provider, key: StubProvider())
    )
    return TestClient(create_app())


def unique_signup() -> dict:
    tag = uuid.uuid4().hex[:8]
    return {
        "email": f"user-{tag}@example.com",
        "username": f"user{tag}",
        "password": "correcthorse",
    }


def test_signup_returns_a_token_and_seeds_free_runs(client: TestClient) -> None:
    body = unique_signup()
    response = client.post("/auth/signup", json=body)
    assert response.status_code == 201
    data = response.json()
    assert data["access_token"]
    assert data["user"]["email"] == body["email"]
    assert data["user"]["role"] == "user"
    assert data["user"]["free_runs_remaining"] == 3


def test_duplicate_email_is_rejected(client: TestClient) -> None:
    body = unique_signup()
    client.post("/auth/signup", json=body)
    other = unique_signup()
    other["email"] = body["email"]
    response = client.post("/auth/signup", json=other)
    assert response.status_code == 409


def test_login_with_wrong_password_is_rejected(client: TestClient) -> None:
    body = unique_signup()
    client.post("/auth/signup", json=body)
    response = client.post(
        "/auth/login", json={"identifier": body["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_succeeds_with_email_or_username(client: TestClient) -> None:
    body = unique_signup()
    client.post("/auth/signup", json=body)
    for identifier in (body["email"], body["username"]):
        response = client.post(
            "/auth/login", json={"identifier": identifier, "password": body["password"]}
        )
        assert response.status_code == 200


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_chat_requires_login(client: TestClient) -> None:
    response = client.post(
        "/chat", json={"model": "claude-opus-5", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 401


def test_free_runs_are_exhausted_then_a_personal_key_still_works(client: TestClient) -> None:
    body = unique_signup()
    token = client.post("/auth/signup", json=body).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"model": "claude-opus-5", "messages": [{"role": "user", "content": "hi"}]}

    for _ in range(3):
        response = client.post("/chat", json=payload, headers=headers)
        assert response.status_code == 200

    exhausted = client.post("/chat", json=payload, headers=headers)
    assert exhausted.status_code == 402

    me = client.get("/auth/me", headers=headers).json()
    assert me["free_runs_remaining"] == 0

    with_own_key = client.post("/chat", json={**payload, "api_key": "sk-personal"}, headers=headers)
    assert with_own_key.status_code == 200

    me_after = client.get("/auth/me", headers=headers).json()
    assert me_after["free_runs_remaining"] == 0, "a personal key must never touch the counter"


def test_admin_has_unlimited_free_runs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tag = uuid.uuid4().hex[:8]
    email = f"admin-{tag}@example.com"
    with session_scope() as session:
        UserRepository(session).upsert_admin(
            email=email, username=f"admin{tag}", password_hash=hash_password("adminpass")
        )
    token = client.post("/auth/login", json={"identifier": email, "password": "adminpass"}).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"model": "claude-opus-5", "messages": [{"role": "user", "content": "hi"}]}

    for _ in range(5):
        response = client.post("/chat", json=payload, headers=headers)
        assert response.status_code == 200

    me = client.get("/auth/me", headers=headers).json()
    assert me["role"] == "admin"


def test_admin_bootstrap_is_idempotent_and_rotates_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not services_available():
        pytest.skip("Postgres is not reachable")
    email = f"boot-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("ADMIN_EMAIL", email)
    monkeypatch.setenv("ADMIN_PASSWORD", "first-password")
    from kb.config import get_settings

    get_settings.cache_clear()
    try:
        upsert_admin_user()
        upsert_admin_user()
        with session_scope() as session:
            admin = UserRepository(session).get_by_email(email)
            assert admin is not None
            first_hash = admin.password_hash

        monkeypatch.setenv("ADMIN_PASSWORD", "second-password")
        get_settings.cache_clear()
        upsert_admin_user()
        with session_scope() as session:
            rows = list(
                session.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": email})
            )
            assert rows[0][0] == 1
            admin = UserRepository(session).get_by_email(email)
            assert admin is not None
            assert admin.password_hash != first_hash
    finally:
        get_settings.cache_clear()
        with session_scope() as session:
            admin = UserRepository(session).get_by_email(email)
            if admin is not None:
                session.delete(admin)
