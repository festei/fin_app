"""The composition root: builds the adapters once and hands the use cases to whoever drives the
application (HTTP app, worker, flows). It is the only code allowed to know every adapter."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from sqlalchemy import Engine

from fin_application.alerts import DeliverDueAlerts, ProcessSignals
from fin_application.bot import BotCommands
from fin_application.ingestion import IngestFundamentals, IngestPrices, IngestYields, SyncUniverse
from fin_application.job_runs import JobRunTracker
from fin_application.ports import (
    AlertChannel,
    AlertPolicySource,
    Clock,
    FundamentalsSource,
    PriceSource,
    SandboxConfigSource,
    ScoringConfigSource,
    ScoringEngine,
    SingleInstanceGuard,
    UnitOfWorkFactory,
    UniverseSource,
    YieldSource,
)
from fin_application.sandbox import (
    CancelOrder,
    CreatePortfolio,
    PlaceOrder,
    PortfolioReader,
    RunSandbox,
)
from fin_application.scoring import ScoreSecurityLive
from fin_application.scoring_run import RunScoring
from fin_application.securities import SecurityCatalog
from fin_application.signals import ScoreUniverse
from fin_application.watchlist import AlertFeed, ChannelService, Opportunities, WatchlistService
from fin_bootstrap.channels import build_channels
from fin_bootstrap.clock import SystemClock
from fin_bootstrap.settings import Settings
from fin_config import (
    ConfigLocator,
    FeatureFlags,
    YamlAlertPolicy,
    YamlSandboxConfig,
    YamlScoringConfig,
    YamlUniverseSource,
)
from fin_langgraph import LangGraphScoringEngine
from fin_persistence import SqlUnitOfWork, make_engine
from fin_persistence.leader_lock import PgAdvisoryLeaderLock
from fin_persistence.session import ping
from fin_providers import (
    EdgarClient,
    EdgarFundamentalsSource,
    FredClient,
    FredYieldSource,
    YahooPriceSource,
)
from fin_telegram import TelegramBot, TelegramChannel


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
        alert_policy: AlertPolicySource | None = None,
        sandbox_config: SandboxConfigSource | None = None,
        flags: FeatureFlags | None = None,
        http_client: httpx.Client | None = None,
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
        self._alert_policy = alert_policy
        self._sandbox_config = sandbox_config
        self._flags = flags
        self._http_client = http_client

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

    def check_database(self) -> None:
        """Raise ``StorageUnavailable`` unless the database answers."""
        ping(self.engine)

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

    @property
    def flags(self) -> FeatureFlags:
        if self._flags is None:
            self._flags = FeatureFlags.load()
        return self._flags

    @property
    def alert_policy(self) -> AlertPolicySource:
        if self._alert_policy is None:
            self._alert_policy = YamlAlertPolicy(ConfigLocator(self.settings.config_dir))
        return self._alert_policy

    @property
    def sandbox_config(self) -> SandboxConfigSource:
        if self._sandbox_config is None:
            self._sandbox_config = YamlSandboxConfig(ConfigLocator(self.settings.config_dir))
        return self._sandbox_config

    def channels(self) -> list[AlertChannel]:
        """The in-app feed, plus Telegram if its flag is on. Raises ``ChannelConfigError`` when
        a switched-on channel lacks its credentials."""
        return build_channels(self.flags, self.settings, self._http_client)

    def telegram_bot(self, channels: Sequence[AlertChannel] | None = None) -> TelegramBot | None:
        """The command bot, or ``None`` when Telegram is off. Pass the channels already built so
        the bot shares the channel's client."""
        channels = self.channels() if channels is None else channels
        channel = next((c for c in channels if isinstance(c, TelegramChannel)), None)
        return TelegramBot(channel.client, self.bot_commands) if channel else None

    @property
    def leader_lock(self) -> SingleInstanceGuard:
        return PgAdvisoryLeaderLock(self.engine)

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

    @property
    def score_universe(self) -> ScoreUniverse:
        return ScoreUniverse(self.uow_factory, self.scoring_engine, self.scoring_config, self.clock)

    @property
    def deliver_due_alerts(self) -> DeliverDueAlerts:
        return DeliverDueAlerts(self.uow_factory, self.clock, self.alert_policy, self.channels)

    @property
    def process_signals(self) -> ProcessSignals:
        return ProcessSignals(
            self.uow_factory,
            self.clock,
            self.alert_policy,
            self.channels,
            self.deliver_due_alerts,
        )

    @property
    def run_scoring(self) -> RunScoring:
        return RunScoring(
            self.uow_factory,
            self.job_runs,
            self.clock,
            self.channels,
            self.score_universe,
            self.process_signals,
            self.deliver_due_alerts,
        )

    @property
    def watchlist(self) -> WatchlistService:
        return WatchlistService(self.uow_factory)

    @property
    def channel_service(self) -> ChannelService:
        return ChannelService(self.uow_factory, self.clock)

    @property
    def alert_feed(self) -> AlertFeed:
        return AlertFeed(self.uow_factory, self.clock)

    @property
    def opportunities(self) -> Opportunities:
        return Opportunities(self.uow_factory)

    @property
    def bot_commands(self) -> BotCommands:
        return BotCommands(
            self.uow_factory, self.watchlist, self.channel_service, self.opportunities
        )

    @property
    def create_portfolio(self) -> CreatePortfolio:
        return CreatePortfolio(self.uow_factory, self.sandbox_config, self.clock)

    @property
    def place_order(self) -> PlaceOrder:
        return PlaceOrder(self.uow_factory, self.clock)

    @property
    def cancel_order(self) -> CancelOrder:
        return CancelOrder(self.uow_factory)

    @property
    def run_sandbox(self) -> RunSandbox:
        return RunSandbox(self.uow_factory, self.job_runs, self.clock)

    @property
    def portfolio_reader(self) -> PortfolioReader:
        return PortfolioReader(self.uow_factory, self.clock)

    @property
    def security_catalog(self) -> SecurityCatalog:
        return SecurityCatalog(self.uow_factory, self.clock)
