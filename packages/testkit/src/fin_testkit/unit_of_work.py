from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self

from fin_domain.market_data import Fact, PriceBar, Security
from fin_domain.shared import JobRun, Ticker
from fin_testkit.job_runs import InMemoryJobRunRepository
from fin_testkit.market_data import (
    InMemoryFactRepository,
    InMemoryPriceRepository,
    InMemorySecurityRepository,
    InMemoryYieldRepository,
)


@dataclass
class InMemoryState:
    job_runs: dict[int, JobRun] = field(default_factory=dict)
    securities: dict[Ticker, Security] = field(default_factory=dict)
    prices: dict[Ticker, dict[datetime, PriceBar]] = field(default_factory=dict)
    facts: dict[Ticker, dict[tuple, Fact]] = field(default_factory=dict)
    yields: dict[str, dict[date, Decimal]] = field(default_factory=dict)


class InMemoryStore:
    """The committed state shared by every unit of work of one test."""

    def __init__(self) -> None:
        self.state = InMemoryState()

    # convenient views for assertions
    @property
    def job_runs(self) -> dict[int, JobRun]:
        return self.state.job_runs

    @property
    def securities(self) -> dict[Ticker, Security]:
        return self.state.securities

    @property
    def prices(self) -> dict[Ticker, dict[datetime, PriceBar]]:
        return self.state.prices

    @property
    def facts(self) -> dict[Ticker, dict[tuple, Fact]]:
        return self.state.facts

    @property
    def yields(self) -> dict[str, dict[date, Decimal]]:
        return self.state.yields


class InMemoryUnitOfWork:
    """Works on a copy of the store; the copy replaces the store only if the block ends cleanly."""

    def __init__(self, store: InMemoryStore):
        self._store = store
        self._bind(deepcopy(store.state))

    def _bind(self, work: InMemoryState) -> None:
        self._work = work
        self.job_runs = InMemoryJobRunRepository(work.job_runs)
        self.securities = InMemorySecurityRepository(work.securities)
        self.prices = InMemoryPriceRepository(work.prices, work.securities)
        self.facts = InMemoryFactRepository(work.facts, work.securities)
        self.yields = InMemoryYieldRepository(work.yields)

    def __enter__(self) -> Self:
        self._bind(deepcopy(self._store.state))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._store.state = self._work


def in_memory_uow_factory(store: InMemoryStore):
    return lambda: InMemoryUnitOfWork(store)
