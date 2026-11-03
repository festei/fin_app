"""Legacy entry point for engines and sessions: the environment lookups live here until the
composition root (``fin_bootstrap.settings``) replaces them; the rest is ``fin_persistence``."""

from __future__ import annotations

import os

from fin_persistence.session import make_engine as _make_engine
from fin_persistence.session import postgres_url, session_scope

__all__ = ["database_configured", "database_url", "make_engine", "session_scope"]


def database_url() -> str:
    """``DATABASE_URL`` wins; otherwise build it from the POSTGRES_* variables."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER")
    db = os.environ.get("POSTGRES_DB")
    if not (user and db):
        raise RuntimeError("Database not configured: set DATABASE_URL or POSTGRES_USER/POSTGRES_DB")
    return postgres_url(
        user,
        db,
        os.environ.get("POSTGRES_PASSWORD", ""),
        os.environ.get("POSTGRES_HOST", "localhost"),
        os.environ.get("POSTGRES_PORT", "5432"),
    )


def database_configured() -> bool:
    """True when a database is configured via DATABASE_URL or POSTGRES_USER + POSTGRES_DB."""
    env = os.environ
    return bool(env.get("DATABASE_URL") or (env.get("POSTGRES_USER") and env.get("POSTGRES_DB")))


def make_engine(url: str | None = None, connect_timeout: int = 10):
    return _make_engine(url or database_url(), connect_timeout)
