from __future__ import annotations

from typing import Protocol

import pandas as pd


class PriceProvider(Protocol):
    """Swap implementations (yfinance, Polygon, Alpaca, ...) without touching callers."""

    def history(self, ticker: str, period: str = "max") -> pd.DataFrame: ...

    def last_price(self, ticker: str) -> float | None: ...


class FundamentalsProvider(Protocol):
    def statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        """Return annual ``income``, ``balance`` and ``cashflow`` frames."""
        ...
