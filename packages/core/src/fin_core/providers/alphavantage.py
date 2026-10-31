"""Alpha Vantage fundamentals provider (fallback source).

Ported from the old Kedro nodes. Fixes the earlier bug where the balance sheet and
cash-flow outputs were swapped: each statement is now keyed by what it really is.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd

_FUNCTIONS = {
    "income": "INCOME_STATEMENT",
    "balance": "BALANCE_SHEET",
    "cashflow": "CASH_FLOW",
}


class AlphaVantageProvider:
    URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self._key = api_key or os.environ.get("FINANCE_API", "")
        self._client = client or httpx.Client(timeout=30)

    def statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for key, function in _FUNCTIONS.items():
            resp = self._client.get(
                self.URL, params={"function": function, "symbol": ticker, "apikey": self._key}
            )
            resp.raise_for_status()
            frame = pd.DataFrame(resp.json().get("annualReports", []))
            frame["ticker"] = ticker
            out[key] = frame
        return out
