"""The SQL implementation of the ``UnitOfWork`` port: one session, one transaction."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from fin_persistence.catalog import SqlScoreHistoryQuery, SqlSecuritySearchQuery
from fin_persistence.job_runs import SqlJobRunRepository
from fin_persistence.market_data import (
    SqlFactRepository,
    SqlPriceRepository,
    SqlSecurityRepository,
    SqlYieldRepository,
)
from fin_persistence.sandbox import SqlPortfolioRepository
from fin_persistence.signals import (
    SqlAlertFeedQuery,
    SqlAlertRepository,
    SqlBotCursorStore,
    SqlChannelStateRepository,
    SqlOpportunityQuery,
    SqlScoreRepository,
    SqlSignalRepository,
    SqlWatchlistRepository,
)
from fin_persistence.storage_errors import translate_storage_error


class SqlUnitOfWork:
    """Commits when the ``with`` block ends normally, rolls back if it raises, always closes."""

    job_runs: SqlJobRunRepository
    securities: SqlSecurityRepository
    prices: SqlPriceRepository
    facts: SqlFactRepository
    yields: SqlYieldRepository
    scores: SqlScoreRepository
    signals: SqlSignalRepository
    alerts: SqlAlertRepository
    watchlist: SqlWatchlistRepository
    channels: SqlChannelStateRepository
    cursors: SqlBotCursorStore
    opportunities: SqlOpportunityQuery
    alert_feed: SqlAlertFeedQuery
    portfolios: SqlPortfolioRepository
    security_search: SqlSecuritySearchQuery
    score_history: SqlScoreHistoryQuery

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
        self.securities = SqlSecurityRepository(self._session)
        self.prices = SqlPriceRepository(self._session)
        self.facts = SqlFactRepository(self._session)
        self.yields = SqlYieldRepository(self._session)
        self.scores = SqlScoreRepository(self._session)
        self.signals = SqlSignalRepository(self._session)
        self.alerts = SqlAlertRepository(self._session)
        self.watchlist = SqlWatchlistRepository(self._session)
        self.channels = SqlChannelStateRepository(self._session)
        self.cursors = SqlBotCursorStore(self._session)
        self.opportunities = SqlOpportunityQuery(self._session)
        self.alert_feed = SqlAlertFeedQuery(self._session)
        self.portfolios = SqlPortfolioRepository(self._session)
        self.security_search = SqlSecuritySearchQuery(self._session)
        self.score_history = SqlScoreHistoryQuery(self._session)
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
        except BaseException as failure:
            session.rollback()
            if isinstance(failure, SQLAlchemyError):
                raise translate_storage_error(failure) from failure
            raise
        else:
            if isinstance(exc, SQLAlchemyError):
                raise translate_storage_error(exc) from exc
        finally:
            session.close()
            self._session = None
