"""Ports of the Trading Sandbox context."""

from __future__ import annotations

from typing import Protocol

from fin_domain.sandbox import Order, Portfolio, PortfolioSnapshot, StrategyRules


class PortfolioRepository(Protocol):
    """A portfolio is loaded with all its orders: holdings are derived from the filled ones."""

    def add(self, portfolio: Portfolio) -> Portfolio:
        """Store a new portfolio and return it with its ``id``."""
        ...

    def get(self, portfolio_id: int) -> Portfolio | None: ...

    def load_exclusive(self, portfolio_id: int) -> Portfolio:
        """Load for the rest of the transaction holding a lock (``FOR UPDATE`` on Postgres), so
        concurrent runs queue up instead of duplicating orders. ``NotFound`` if there is none."""
        ...

    def list(self) -> list[Portfolio]:
        """Ordered by id."""
        ...

    def ids(self) -> list[int]: ...

    def save(self, portfolio: Portfolio) -> None:
        """Store the portfolio's cash and signal cursor, insert its new orders (assigning their
        ids) and write the ones that changed."""
        ...

    def orders(self, portfolio_id: int, status: str | None = None, limit: int = 100) -> list[Order]:
        """Newest first."""
        ...

    def save_snapshot(self, portfolio_id: int, snapshot: PortfolioSnapshot) -> None:
        """One row per portfolio and day: a second snapshot on the same day overwrites it."""
        ...

    def history(self, portfolio_id: int) -> list[PortfolioSnapshot]:
        """Oldest first: the equity curve."""
        ...


class SandboxConfigSource(Protocol):
    def load(self) -> StrategyRules:
        """The defaults new portfolios start from."""
        ...
