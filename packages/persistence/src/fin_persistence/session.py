"""Engine and session creation. Takes explicit values; reading the environment is the composition
root's job (``fin_bootstrap.settings``)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def postgres_url(
    user: str, db: str, password: str = "", host: str = "localhost", port: str = "5432"
) -> str:
    """The SQLAlchemy URL for a Postgres server (psycopg 3), with credentials quoted."""
    return (
        f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{db}"
    )


log = logging.getLogger(__name__)


def make_engine(url: str, connect_timeout: int = 10) -> Engine:
    """``connect_timeout`` (seconds) stops an unreachable host from hanging callers; it only
    applies to server databases (SQLite has no network connection)."""
    connect_args = {} if url.startswith("sqlite") else {"connect_timeout": connect_timeout}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


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


def ping(engine: Engine) -> None:
    """Raise ``StorageUnavailable`` unless the database answers a trivial query."""
    from fin_application.errors import StorageUnavailable
    from fin_persistence.storage_errors import log_storage_error

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        # only the kind goes to the caller (the original can hold connection details); the
        # original goes to the log, as one line for an outage (a probe repeats every few seconds)
        log_storage_error(exc)
        raise StorageUnavailable(f"database unavailable ({type(exc).__name__})") from None
