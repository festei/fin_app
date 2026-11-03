"""Shared kernel: the small vocabulary every bounded context may use."""

from fin_domain.shared.errors import DomainError, InvariantViolation, NotFound
from fin_domain.shared.events import DomainEvent
from fin_domain.shared.job_run import (
    STALE_AFTER,
    JobRun,
    RunStatus,
    derive_status,
    summarise_failures,
)
from fin_domain.shared.ticker import Ticker
from fin_domain.shared.time import ensure_utc

__all__ = [
    "STALE_AFTER",
    "DomainError",
    "DomainEvent",
    "InvariantViolation",
    "JobRun",
    "NotFound",
    "RunStatus",
    "Ticker",
    "derive_status",
    "ensure_utc",
    "summarise_failures",
]
