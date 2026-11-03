"""The SQL implementation of the ``UnitOfWork`` port: one session, one transaction."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from fin_persistence.job_runs import SqlJobRunRepository


class SqlUnitOfWork:
    """Commits when the ``with`` block ends normally, rolls back if it raises, always closes."""

    job_runs: SqlJobRunRepository

    def __init__(self, engine: Engine):
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("the unit of work is not open; use it in a 'with' block")
        return self._session

    def __enter__(self) -> Self:
        self._session = self._sessions()
        self.job_runs = SqlJobRunRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if exc_type is None:
                session.commit()
            else:
                session.rollback()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
