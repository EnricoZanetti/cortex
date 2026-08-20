"""Upserts the operator's admin login on API startup, from ADMIN_EMAIL/ADMIN_PASSWORD."""

from __future__ import annotations

from kb.auth.security import hash_password
from kb.config import get_settings
from kb.db.repositories import UserRepository
from kb.db.session import session_scope
from kb.logging import get_logger

logger = get_logger(__name__)


def upsert_admin_user() -> None:
    """Create or update the bootstrapped admin account, if ADMIN_EMAIL/PASSWORD are set.

    Called once from the API's lifespan, not per-request. Safe to call on every
    startup: it is a no-op when the env vars are unset, and idempotent otherwise, so
    rotating ADMIN_PASSWORD and restarting is how the operator changes it.
    """
    settings = get_settings()
    if settings.admin_email is None or settings.admin_password is None:
        logger.debug("admin_bootstrap_skipped", reason="ADMIN_EMAIL/ADMIN_PASSWORD not set")
        return

    email = settings.admin_email.get_secret_value()
    password = settings.admin_password.get_secret_value()
    with session_scope() as session:
        UserRepository(session).upsert_admin(
            email=email, username="admin", password_hash=hash_password(password)
        )
    logger.info("admin_bootstrap_upserted", email=email)
