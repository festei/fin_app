"""Driven ports: what the core needs from the outside world. Adapters implement them."""

from fin_application.ports.alerts import (
    AlertChannel,
    AlertMessage,
    AlertPolicySource,
    ChatBot,
    SingleInstanceGuard,
)
from fin_application.ports.clock import Clock
from fin_application.ports.job_runs import JobRunRepository
from fin_application.ports.market_data import (
    CompanyListing,
    FactRepository,
    FundamentalsSource,
    PriceRepository,
    PriceSource,
    SecurityRepository,
    SecuritySearchQuery,
    UniverseSource,
    YieldRepository,
    YieldSource,
)
from fin_application.ports.sandbox import PortfolioRepository, SandboxConfigSource
from fin_application.ports.scoring import ScoringConfigSource, ScoringEngine
from fin_application.ports.signals import (
    AlertFeedQuery,
    AlertRepository,
    BotCursorStore,
    ChannelStateRepository,
    OpportunityQuery,
    OpportunityRow,
    ScoreHistoryQuery,
    ScorePointRow,
    ScoreRepository,
    SignalPointRow,
    SignalRepository,
    WatchlistRepository,
)
from fin_application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AlertChannel",
    "AlertFeedQuery",
    "AlertMessage",
    "AlertPolicySource",
    "AlertRepository",
    "BotCursorStore",
    "ChannelStateRepository",
    "ChatBot",
    "Clock",
    "CompanyListing",
    "FactRepository",
    "FundamentalsSource",
    "JobRunRepository",
    "OpportunityQuery",
    "OpportunityRow",
    "PortfolioRepository",
    "PriceRepository",
    "PriceSource",
    "SandboxConfigSource",
    "ScoreHistoryQuery",
    "ScorePointRow",
    "ScoreRepository",
    "ScoringConfigSource",
    "ScoringEngine",
    "SecurityRepository",
    "SecuritySearchQuery",
    "SignalPointRow",
    "SignalRepository",
    "SingleInstanceGuard",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "UniverseSource",
    "WatchlistRepository",
    "YieldRepository",
    "YieldSource",
]
