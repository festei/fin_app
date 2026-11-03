"""Adapter-local protocols for the frame-based vendors. The errors a provider may raise are part of
the port contract and live in ``fin_application.errors``; they are re-exported here."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from fin_application.errors import AccessDeniedError, ProviderError, RateLimitError

__all__ = [
    "AccessDeniedError",
    "FundamentalsProvider",
    "PriceProvider",
    "ProviderError",
    "RateLimitError",
]


class PriceProvider(Protocol):
    """Swap implementations (yfinance, Polygon, Alpaca, ...) without touching callers."""

    def history(self, ticker: str, period: str = "max") -> pd.DataFrame: ...

    def last_price(self, ticker: str) -> float | None: ...


class FundamentalsProvider(Protocol):
    def statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        """Return annual ``income``, ``balance`` and ``cashflow`` frames."""
        ...
