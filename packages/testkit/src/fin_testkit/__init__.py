"""In-memory fakes of the application ports. For tests only; no production package imports this."""

from fin_testkit.clock import FakeClock
from fin_testkit.job_runs import InMemoryJobRunRepository
from fin_testkit.market_data import (
    InMemoryFactRepository,
    InMemoryPriceRepository,
    InMemorySecurityRepository,
    InMemoryYieldRepository,
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
    "FakeClock",
    "FakeFundamentalsSource",
    "FakePriceSource",
    "FakeUniverse",
    "FakeYieldSource",
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
