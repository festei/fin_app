"""Database fixtures for the persistence tests.

Deliberately not a ``conftest.py``: pytest registers every ``conftest.py`` as the top-level module
``conftest``, and a core test imports ``bar`` from the core one by that name.
"""

import os

import pytest
from sqlalchemy import text

from fin_persistence import SqlUnitOfWork, make_engine, migrate
from fin_testkit import InMemoryStore, in_memory_uow_factory


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def migrated_engine(request, tmp_path):
    """A migrated, empty database: SQLite always, real Postgres when TEST_DATABASE_URL is set."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("TEST_DATABASE_URL not set")
    engine = make_engine(url)
    if request.param == "postgres":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield engine
    engine.dispose()


@pytest.fixture(
    params=["memory", "sql-sqlite", pytest.param("sql-postgres", marks=pytest.mark.postgres)]
)
def uow_factory(request, tmp_path):
    """The same unit-of-work contract against the in-memory fake and the real SQL adapter."""
    if request.param == "memory":
        yield in_memory_uow_factory(InMemoryStore())
        return
    if request.param == "sql-sqlite":
        url = f"sqlite:///{tmp_path / 'contract.db'}"
    else:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("TEST_DATABASE_URL not set")
    engine = make_engine(url)
    if request.param == "sql-postgres":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield lambda: SqlUnitOfWork(engine)
    engine.dispose()
