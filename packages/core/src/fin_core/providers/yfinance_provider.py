"""yfinance-backed price provider (ported from the old Kedro ``get_yf_data`` node)."""

from __future__ import annotations

import pandas as pd
import yfinance as yf


class YFinancePriceProvider:
    def history(self, ticker: str, period: str = "max") -> pd.DataFrame:
        hist = yf.Ticker(ticker).history(period=period)
        return hist[["Open", "High", "Low", "Close", "Volume"]].reset_index()

    def last_price(self, ticker: str) -> float | None:
        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
