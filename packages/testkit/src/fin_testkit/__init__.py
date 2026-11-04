"""In-memory fakes of the application ports. For tests only; no production package imports this."""

from fin_testkit.alerts import FakeChannel, FixedPolicy
from fin_testkit.clock import FakeClock
from fin_testkit.job_runs import InMemoryJobRunRepository
from fin_testkit.market_data import (
    InMemoryFactRepository,
    InMemoryPriceRepository,
    InMemorySecurityRepository,
    InMemorySecuritySearchQuery,
    InMemoryYieldRepository,
)
from fin_testkit.sandbox import FixedSandboxConfig, InMemoryPortfolioRepository
from fin_testkit.scoring import FakeScoringConfig, FakeScoringEngine
from fin_testkit.signals import (
    InMemoryAlertFeedQuery,
    InMemoryAlertRepository,
    InMemoryBotCursorStore,
    InMemoryChannelStateRepository,
    InMemoryOpportunityQuery,
    InMemoryScoreHistoryQuery,
    InMemoryScoreRepository,
    InMemorySignalRepository,
    InMemoryWatchlistRepository,
)
from fin_testkit.sources import (
    FakeFundamentalsSource,
    FakePriceSource,
    FakeUniverse,
    FakeYieldSource,
)
from fin_testkit.unit_of_work import (
    InMemoryState,
    InMemoryStore,
    InMemoryUnitOfWork,
    in_memory_uow_factory,
)

__all__ = [
    "FakeChannel",
    "FakeClock",
    "FakeFundamentalsSource",
    "FakePriceSource",
    "FakeScoringConfig",
    "FakeScoringEngine",
    "FakeUniverse",
    "FakeYieldSource",
    "FixedPolicy",
    "FixedSandboxConfig",
    "InMemoryAlertFeedQuery",
    "InMemoryAlertRepository",
    "InMemoryBotCursorStore",
    "InMemoryChannelStateRepository",
    "InMemoryOpportunityQuery",
    "InMemoryPortfolioRepository",
    "InMemoryScoreHistoryQuery",
    "InMemoryScoreRepository",
    "InMemorySecuritySearchQuery",
    "InMemorySignalRepository",
    "InMemoryWatchlistRepository",
    "InMemoryFactRepository",
    "InMemoryJobRunRepository",
    "InMemoryPriceRepository",
    "InMemorySecurityRepository",
    "InMemoryState",
    "InMemoryStore",
    "InMemoryUnitOfWork",
    "InMemoryYieldRepository",
    "in_memory_uow_factory",
]
