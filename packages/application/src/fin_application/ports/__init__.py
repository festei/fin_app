"""Driven ports: what the core needs from the outside world. Adapters implement them."""

from fin_application.ports.clock import Clock
from fin_application.ports.job_runs import JobRunRepository
from fin_application.ports.market_data import (
    CompanyListing,
    FactRepository,
    FundamentalsSource,
    PriceRepository,
    PriceSource,
    SecurityRepository,
    UniverseSource,
    YieldRepository,
    YieldSource,
)
from fin_application.ports.scoring import ScoringConfigSource, ScoringEngine
from fin_application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "Clock",
    "CompanyListing",
    "FactRepository",
    "FundamentalsSource",
    "JobRunRepository",
    "PriceRepository",
    "PriceSource",
    "ScoringConfigSource",
    "ScoringEngine",
    "SecurityRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "UniverseSource",
    "YieldRepository",
    "YieldSource",
]
