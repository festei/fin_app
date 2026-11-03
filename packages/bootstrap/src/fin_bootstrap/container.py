"""The composition root: builds the adapters once and hands the use cases to whoever drives the
application (HTTP app, worker, flows). It is the only code allowed to know every adapter."""

from __future__ import annotations

from sqlalchemy import Engine

from fin_application.job_runs import JobRunTracker
from fin_application.ports import Clock, UnitOfWorkFactory
from fin_bootstrap.clock import SystemClock
from fin_bootstrap.settings import Settings
from fin_persistence import SqlUnitOfWork, make_engine


class DatabaseNotConfigured(RuntimeError):
    """Something needed the database but ``DATABASE_URL``/``POSTGRES_*`` were not set."""


class Container:
    def __init__(self, settings: Settings, *, clock: Clock | None = None):
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._engine: Engine | None = None

    @classmethod
    def from_environ(cls) -> Container:
        return cls(Settings.from_environ())

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            if self.settings.database_url is None:
                raise DatabaseNotConfigured("set DATABASE_URL or POSTGRES_USER/POSTGRES_DB")
            self._engine = make_engine(self.settings.database_url, self.settings.db_connect_timeout)
        return self._engine

    @property
    def uow_factory(self) -> UnitOfWorkFactory:
        engine = self.engine
        return lambda: SqlUnitOfWork(engine)

    @property
    def job_runs(self) -> JobRunTracker:
        return JobRunTracker(self.uow_factory, self.clock)

    def dispose(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
