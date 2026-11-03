"""The tables moved to ``fin_persistence.tables``; this keeps the old import path working until
the modules that use it are migrated."""

from fin_persistence.tables import (
    AlertDelivery,
    AlertRecord,
    Base,
    BigIntPK,
    BondYield,
    ChannelState,
    FundamentalFact,
    IngestionRun,
    JsonType,
    Portfolio,
    PortfolioSnapshot,
    PriceBar,
    SandboxOrder,
    ScoreRecord,
    Security,
    SignalRecord,
    WatchlistEntry,
)

__all__ = [
    "AlertDelivery",
    "AlertRecord",
    "Base",
    "BigIntPK",
    "BondYield",
    "ChannelState",
    "FundamentalFact",
    "IngestionRun",
    "JsonType",
    "Portfolio",
    "PortfolioSnapshot",
    "PriceBar",
    "SandboxOrder",
    "ScoreRecord",
    "Security",
    "SignalRecord",
    "WatchlistEntry",
]
