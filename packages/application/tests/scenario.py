"""A complete in-memory world for the scoring, alert and delivery scenarios.

Not a ``conftest.py`` on purpose: pytest registers every conftest as the top-level module
``conftest``, and a core test imports ``bar`` from the core one by that name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fin_application.alerts import DeliverDueAlerts, InAppChannel, ProcessSignals
from fin_application.job_runs import JobRunTracker
from fin_application.scoring_run import RunScoring
from fin_application.signals import ScoreUniverse
from fin_domain.market_data import CONCEPT_TAGS, Fact, PriceBar, SecurityUpdate
from fin_domain.shared import Ticker
from fin_domain.signals import AlertPolicy
from fin_testkit import (
    FakeClock,
    FakeScoringConfig,
    FakeScoringEngine,
    FixedPolicy,
    InMemoryStore,
    in_memory_uow_factory,
)

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
AS_OF = date(2025, 3, 10)


class World:
    """Everything wired on fakes. ``seed`` adds a stock with fresh data (so signals are not
    withheld); ``engine.conviction`` / ``engine.per_ticker`` script what the scorers conclude."""

    def __init__(self, *, policy: AlertPolicy | None = None, conviction: float | None = 80.0):
        self.clock = FakeClock(NOW)
        self.store = InMemoryStore(self.clock)
        self.uow_factory = in_memory_uow_factory(self.store)
        self.engine = FakeScoringEngine(conviction)
        self.config = FakeScoringConfig({"weights": {}})
        self.policy = FixedPolicy(policy)
        self.in_app = InAppChannel()
        self.channels = [self.in_app]
        self.tracker = JobRunTracker(self.uow_factory, self.clock)

    # --- builders -----------------------------------------------------------------------------
    @property
    def universe(self) -> ScoreUniverse:
        return ScoreUniverse(self.uow_factory, self.engine, self.config, self.clock)

    @property
    def deliver(self) -> DeliverDueAlerts:
        return DeliverDueAlerts(self.uow_factory, self.clock, self.policy, lambda: self.channels)

    @property
    def process(self) -> ProcessSignals:
        return ProcessSignals(
            self.uow_factory, self.clock, self.policy, lambda: self.channels, self.deliver
        )

    @property
    def run_scoring(self) -> RunScoring:
        return RunScoring(
            self.uow_factory,
            self.tracker,
            self.clock,
            lambda: self.channels,
            self.universe,
            self.process,
            self.deliver,
        )

    # --- data ---------------------------------------------------------------------------------
    def seed(self, ticker: str, *, price_date: date = date(2025, 3, 7), **update) -> Ticker:
        symbol = Ticker(ticker)
        with self.uow_factory() as uow:
            uow.securities.upsert([SecurityUpdate(symbol, update)])
            uow.facts.add(symbol, [eps_fact(symbol)])
            uow.prices.upsert(
                symbol,
                [
                    PriceBar(
                        ts=datetime.combine(price_date, datetime.min.time(), tzinfo=UTC), close=25.0
                    )
                ],
            )
        return symbol

    def score(self, as_of: date = AS_OF, tickers=None):
        return self.universe.execute(as_of, tickers)

    def new_signal_ids(self, tickers=None, as_of: date = AS_OF) -> list[int]:
        return [sid for sid, _, _ in self.score(as_of, tickers).signals]


def eps_fact(ticker: Ticker) -> Fact:
    return Fact(
        concept="EpsDiluted",
        tag=CONCEPT_TAGS["EpsDiluted"][0],
        unit="USD/shares",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        instant=False,
        fiscal_year=2024,
        fiscal_period="FY",
        form="10-K",
        filed=date(2025, 2, 15),
        accession=f"eps-{ticker}",
        value=Decimal("2.5"),
    )
