"""Driven adapters: market-data vendors (EDGAR, FRED, Yahoo Finance, Alpha Vantage)."""

from fin_providers.edgar import EdgarClient
from fin_providers.frames import bars_from_frame
from fin_providers.fred import FredClient
from fin_providers.sources import EdgarFundamentalsSource, FredYieldSource, YahooPriceSource
from fin_providers.yfinance_provider import YFinancePriceProvider

__all__ = [
    "EdgarClient",
    "EdgarFundamentalsSource",
    "FredClient",
    "FredYieldSource",
    "YFinancePriceProvider",
    "YahooPriceSource",
    "bars_from_frame",
]
