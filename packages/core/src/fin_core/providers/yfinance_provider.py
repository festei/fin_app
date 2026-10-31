"""yfinance-backed price provider (ported from the old Kedro ``get_yf_data`` node)."""

from __future__ import annotations

import pandas as pd
import yfinance as yf

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class YFinancePriceProvider:
    def history(self, ticker: str, period: str = "max") -> pd.DataFrame:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            # Unknown/delisted ticker or throttled: yfinance returns an empty frame with no
            # OHLCV columns. Return the same shape so callers don't hit a KeyError.
            return pd.DataFrame(columns=["Date", *COLUMNS])
        return hist[COLUMNS].reset_index()

    def last_price(self, ticker: str) -> float | None:
        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
