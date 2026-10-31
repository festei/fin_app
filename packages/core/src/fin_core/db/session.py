"""Engine/session creation. Config via env only (12-factor)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    """``DATABASE_URL`` wins; otherwise build it from the POSTGRES_* variables."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER")
    db = os.environ.get("POSTGRES_DB")
    if not (user and db):
        raise RuntimeError("Database not configured: set DATABASE_URL or POSTGRES_USER/POSTGRES_DB")
    password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg://{quote(user, safe='')}:{password}@{host}:{port}/{db}"


def database_configured() -> bool:
    """True when a database is configured via DATABASE_URL or POSTGRES_USER + POSTGRES_DB."""
    env = os.environ
    return bool(env.get("DATABASE_URL") or (env.get("POSTGRES_USER") and env.get("POSTGRES_DB")))


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
