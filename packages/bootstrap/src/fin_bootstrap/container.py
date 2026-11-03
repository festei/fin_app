"""The composition root: builds the adapters once and hands the use cases to whoever drives the
application (HTTP app, worker, flows). It is the only code allowed to know every adapter."""

from __future__ import annotations

from sqlalchemy import Engine

from fin_application.ingestion import IngestFundamentals, IngestPrices, IngestYields, SyncUniverse
from fin_application.job_runs import JobRunTracker
from fin_application.ports import (
    Clock,
    FundamentalsSource,
    PriceSource,
    ScoringConfigSource,
    ScoringEngine,
    UnitOfWorkFactory,
    UniverseSource,
    YieldSource,
)
from fin_application.scoring import ScoreSecurityLive
from fin_bootstrap.clock import SystemClock
from fin_bootstrap.settings import Settings
from fin_config import ConfigLocator, YamlScoringConfig, YamlUniverseSource
from fin_langgraph import LangGraphScoringEngine
from fin_persistence import SqlUnitOfWork, make_engine
from fin_providers import (
    EdgarClient,
    EdgarFundamentalsSource,
    FredClient,
    FredYieldSource,
    YahooPriceSource,
)


class DatabaseNotConfigured(RuntimeError):
    """Something needed the database but ``DATABASE_URL``/``POSTGRES_*`` were not set."""


class Container:
    """Sources are built on first use, so a process that never ingests (the HTTP app, the
    worker) needs no SEC contact details or FRED key. Tests pass their own sources."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        universe: UniverseSource | None = None,
        fundamentals_source: FundamentalsSource | None = None,
        yield_source: YieldSource | None = None,
        price_source: PriceSource | None = None,
        scoring_engine: ScoringEngine | None = None,
        scoring_config: ScoringConfigSource | None = None,
    ):
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._engine: Engine | None = None
        self._universe = universe
        self._fundamentals_source = fundamentals_source
        self._yield_source = yield_source
        self._price_source = price_source
        self._scoring_engine = scoring_engine
        self._scoring_config = scoring_config

    @classmethod
    def from_environ(cls) -> Container:
        return cls(Settings.from_environ())

    # --- infrastructure ---------------------------------------------------------------------

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

    def dispose(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # --- sources ----------------------------------------------------------------------------

    @property
    def universe(self) -> UniverseSource:
        if self._universe is None:
            self._universe = YamlUniverseSource(ConfigLocator(self.settings.config_dir))
        return self._universe

    @property
    def fundamentals_source(self) -> FundamentalsSource:
        if self._fundamentals_source is None:
            client = EdgarClient(user_agent=self.settings.sec_user_agent)
            self._fundamentals_source = EdgarFundamentalsSource(client)
        return self._fundamentals_source

    @property
    def yield_source(self) -> YieldSource:
        if self._yield_source is None:
            self._yield_source = FredYieldSource(FredClient(api_key=self.settings.fred_api_key))
        return self._yield_source

    @property
    def price_source(self) -> PriceSource:
        if self._price_source is None:
            self._price_source = YahooPriceSource()
        return self._price_source

    @property
    def scoring_engine(self) -> ScoringEngine:
        if self._scoring_engine is None:
            self._scoring_engine = LangGraphScoringEngine()  # compiled once, reused
        return self._scoring_engine

    @property
    def scoring_config(self) -> ScoringConfigSource:
        if self._scoring_config is None:
            self._scoring_config = YamlScoringConfig(ConfigLocator(self.settings.config_dir))
        return self._scoring_config

    # --- use cases --------------------------------------------------------------------------

    @property
    def job_runs(self) -> JobRunTracker:
        return JobRunTracker(self.uow_factory, self.clock)

    @property
    def sync_universe(self) -> SyncUniverse:
        return SyncUniverse(self.uow_factory, self.universe)

    @property
    def ingest_fundamentals(self) -> IngestFundamentals:
        return IngestFundamentals(self.uow_factory, self.job_runs, self.fundamentals_source)

    @property
    def ingest_yields(self) -> IngestYields:
        return IngestYields(self.uow_factory, self.job_runs, self.yield_source, self.universe)

    @property
    def ingest_prices(self) -> IngestPrices:
        return IngestPrices(self.uow_factory, self.job_runs, self.price_source)

    @property
    def score_security_live(self) -> ScoreSecurityLive:
        return ScoreSecurityLive(self.uow_factory, self.scoring_engine, self.scoring_config)
