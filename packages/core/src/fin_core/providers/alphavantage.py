"""Alpha Vantage fundamentals provider (fallback source).

Ported from the old Kedro nodes. Fixes the earlier bug where the balance sheet and
cash-flow outputs were swapped: each statement is now keyed by what it really is.

Notes on failure modes:
* Alpha Vantage reports rate limits and bad requests as HTTP 200 with a ``Note``,
  ``Information`` or ``Error Message`` field, so those are checked explicitly and raised.
* The API only accepts the key as a query parameter, so it travels in the URL. We therefore
  never include the URL in raised errors and keep httpx's INFO request logging off.
"""

from __future__ import annotations

import logging
import os

import httpx
import pandas as pd

from fin_core.providers.base import ProviderError, RateLimitError

# httpx logs every request URL at INFO, which would write the API key to the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

_FUNCTIONS = {
    "income": "INCOME_STATEMENT",
    "balance": "BALANCE_SHEET",
    "cashflow": "CASH_FLOW",
}


class AlphaVantageProvider:
    URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self._key = api_key or os.environ.get("FINANCE_API", "")
        if not self._key:
            raise ProviderError("Alpha Vantage API key missing: pass api_key or set FINANCE_API")
        self._client = client or httpx.Client(timeout=30)

    def _fetch(self, function: str, ticker: str) -> dict:
        try:
            resp = self._client.get(
                self.URL, params={"function": function, "symbol": ticker, "apikey": self._key}
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            # str(exc) contains the request URL and therefore the key: report status only.
            raise ProviderError(
                f"Alpha Vantage {function} for {ticker}: HTTP {exc.response.status_code}"
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                f"Alpha Vantage {function} for {ticker}: {type(exc).__name__}"
            ) from None

        if not isinstance(body, dict):
            raise ProviderError(f"Alpha Vantage {function} for {ticker}: unexpected response")
        for field in ("Note", "Information"):
            if field in body:
                raise RateLimitError(f"Alpha Vantage {function} for {ticker}: {body[field]}")
        if "Error Message" in body:
            raise ProviderError(f"Alpha Vantage {function} for {ticker}: {body['Error Message']}")
        if "annualReports" not in body:
            raise ProviderError(f"Alpha Vantage {function} for {ticker}: no annualReports in reply")
        return body

    def statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for key, function in _FUNCTIONS.items():
            frame = pd.DataFrame(self._fetch(function, ticker)["annualReports"])
            frame["ticker"] = ticker
            out[key] = frame
        return out
