"""Time in the domain is always timezone-aware UTC and always passed in, never read from a clock."""

from __future__ import annotations

from datetime import UTC, datetime

from fin_domain.shared.errors import InvariantViolation


def ensure_utc(moment: datetime, what: str = "timestamp") -> datetime:
    """``moment`` in UTC. A naive datetime is rejected: its zone would be a guess.

    Adapters turn what a database hands back (SQLite returns naive UTC) into aware values before
    it reaches the domain.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise InvariantViolation(f"{what} must be timezone-aware, got a naive datetime")
    return moment.astimezone(UTC)
