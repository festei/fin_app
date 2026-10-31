"""FRED provider: Treasury yields, curve spreads and credit-spread (OAS) series.

CONTRACT STATUS: implemented from the public FRED documentation and tested against synthetic
responses with the documented shape. NOT yet verified against the live API (the build sandbox
blocks the FRED hosts). Run the opt-in live tests where reachable:
    RUN_LIVE_TESTS=1 FRED_API_KEY=... uv run pytest -m live

Endpoint: GET https://api.stlouisfed.org/fred/series/observations
    params: series_id, api_key, file_type=json, observation_start=YYYY-MM-DD
    {"observations": [{"realtime_start": "...", "realtime_end": "...",
                       "date": "2025-01-02", "value": "4.57"}, ...]}
  * Values are strings in percent; a missing observation is the string ".".
  * Errors are HTTP 4xx with {"error_code": 400, "error_message": "..."}; the limit is
    120 requests/minute (HTTP 429).
  * The API key travels in the query string, so it is kept out of errors and httpx logs.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from fin_core.providers.base import ProviderError, RateLimitError

logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the API key

URL = "https://api.stlouisfed.org/fred/series/observations"


class FredClient:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self._key = api_key or os.environ.get("FRED_API_KEY", "")
        if not self._key:
            raise ProviderError("FRED API key missing: pass api_key or set FRED_API_KEY")
        self._client = client or httpx.Client(timeout=30)

    def observations(self, series_id: str, start: date | None = None) -> list[tuple[date, Decimal]]:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self._key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start.isoformat()
        try:
            resp = self._client.get(URL, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"FRED {series_id}: {type(exc).__name__}") from None

        if resp.status_code == 429:
            raise RateLimitError(f"FRED {series_id}: rate limited (HTTP 429)")
        if resp.status_code >= 400:
            message = ""
            try:
                message = str(resp.json().get("error_message", ""))
            except ValueError:
                pass
            raise ProviderError(f"FRED {series_id}: HTTP {resp.status_code} {message}".strip())
        try:
            body = resp.json()
            observations = body["observations"]
        except (ValueError, KeyError, TypeError):
            raise ProviderError(f"FRED {series_id}: unexpected response shape") from None

        out: list[tuple[date, Decimal]] = []
        for o in observations:
            raw = o.get("value")
            if raw in (None, "", "."):  # "." marks a missing observation (e.g. a holiday)
                continue
            try:
                out.append((date.fromisoformat(o["date"]), Decimal(raw)))
            except (KeyError, ValueError, InvalidOperation):
                continue
        return out
