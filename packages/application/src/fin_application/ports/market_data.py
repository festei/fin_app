"""Ports of the Market Data context: where securities, prices, facts and rates are stored, and
where they come from. Everything is keyed by ``Ticker`` or a series id; database ids stay inside
the adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from fin_domain.market_data import (
    BondSeries,
    Fact,
    PriceBar,
    Security,
    SecurityUpdate,
    YieldPoint,
)
from fin_domain.shared import Ticker

# --- storage ------------------------------------------------------------------------------------


class SecurityRepository(Protocol):
    def upsert(self, updates: Sequence[SecurityUpdate]) -> int:
        """Insert new securities; on existing ones touch only the fields each update provides."""
        ...

    def get(self, ticker: Ticker) -> Security | None: ...

    def list(self, *, active_only: bool = True) -> list[Security]:
        """Ordered by ticker."""
        ...

    def save(self, security: Security) -> None:
        """Store the changed state of a security that exists."""
        ...


class PriceRepository(Protocol):
    def upsert(self, ticker: Ticker, bars: Sequence[PriceBar]) -> int:
        """Store bars; a later run overwrites a bar of the same day. Returns bars offered."""
        ...

    def latest(self, ticker: Ticker, as_of: datetime | None = None) -> PriceBar | None: ...

    def tickers_with_bars(self) -> set[Ticker]: ...

    def close_at(self, ticker: Ticker, ts: datetime) -> float | None:
        """The stored close of the bar at exactly ``ts`` (``None`` if there is none)."""
        ...

    def first_bar_between(self, ticker: Ticker, start: datetime, end: datetime) -> PriceBar | None:
        """The first bar with ``start <= ts <= end``."""
        ...

    def closes_since(self, ticker: Ticker, since: datetime) -> Mapping[datetime, float]:
        """Stored closes of the bars at or after ``since``, by timestamp."""
        ...


class FactRepository(Protocol):
    def add(self, ticker: Ticker, facts: Sequence[Fact]) -> int:
        """Store facts, ignoring ones already held (same natural key). Returns facts offered."""
        ...

    def filed_up_to(
        self, ticker: Ticker, as_of: date, concepts: Sequence[str] | None = None
    ) -> list[Fact]:
        """Every stored version filed on or before ``as_of``. Choosing among versions is the
        domain's job (``fin_domain.market_data.known_as_of``)."""
        ...


class YieldRepository(Protocol):
    def upsert(self, series_id: str, points: Sequence[YieldPoint]) -> int: ...

    def latest_date(self, series_id: str) -> date | None: ...

    def latest(self, series_id: str, as_of: date | None = None) -> YieldPoint | None: ...


# --- sources ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyListing:
    """What the SEC knows about a ticker."""

    cik: str
    name: str


class FundamentalsSource(Protocol):
    def ticker_directory(self) -> Mapping[Ticker, CompanyListing]:
        """Raises ``ProviderError`` when the directory is unavailable."""
        ...

    def facts(self, cik: str) -> list[Fact]:
        """All periodic-filing facts of a company. Raises ``ProviderError`` (``RateLimitError``
        and ``AccessDeniedError`` when continuing is pointless)."""
        ...


class PriceSource(Protocol):
    def history(self, ticker: Ticker, period: str) -> list[PriceBar]:
        """Daily bars covering ``period`` (e.g. ``"1mo"``, ``"max"``); empty if none."""
        ...


class YieldSource(Protocol):
    def observations(self, series_id: str, since: date | None) -> list[YieldPoint]: ...


class UniverseSource(Protocol):
    def securities(self) -> list[SecurityUpdate]:
        """The tracked universe, validated."""
        ...

    def bond_series(self) -> list[BondSeries]:
        """The rate series tracked for bonds, validated."""
        ...
