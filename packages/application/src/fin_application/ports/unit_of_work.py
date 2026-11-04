"""One transaction. Everything done through the repositories of a unit of work commits together
when the ``with`` block ends normally and is rolled back if it raises.

Each use case opens as many units of work as it needs, one per transaction it wants, so today's
per-security and per-signal transactions stay per-security and per-signal.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from fin_application.ports.job_runs import JobRunRepository
from fin_application.ports.market_data import (
    FactRepository,
    PriceRepository,
    SecurityRepository,
    SecuritySearchQuery,
    YieldRepository,
)
from fin_application.ports.sandbox import PortfolioRepository
from fin_application.ports.signals import (
    AlertFeedQuery,
    AlertRepository,
    BotCursorStore,
    ChannelStateRepository,
    OpportunityQuery,
    ScoreHistoryQuery,
    ScoreRepository,
    SignalRepository,
    WatchlistRepository,
)


class UnitOfWork(Protocol):
    job_runs: JobRunRepository
    securities: SecurityRepository
    prices: PriceRepository
    facts: FactRepository
    yields: YieldRepository
    scores: ScoreRepository
    signals: SignalRepository
    alerts: AlertRepository
    watchlist: WatchlistRepository
    channels: ChannelStateRepository
    cursors: BotCursorStore
    opportunities: OpportunityQuery
    alert_feed: AlertFeedQuery
    portfolios: PortfolioRepository
    security_search: SecuritySearchQuery
    score_history: ScoreHistoryQuery

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
