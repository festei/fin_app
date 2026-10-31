"""SEC EDGAR provider: ticker -> CIK map and XBRL "company facts" normalisation.

CONTRACT STATUS: implemented from the public SEC documentation and tested against synthetic
responses with the documented shape. NOT yet verified against the live API (the build sandbox
blocks sec.gov). Run the opt-in live contract tests where the hosts are reachable:
    RUN_LIVE_TESTS=1 SEC_USER_AGENT="Your Name you@example.com" uv run pytest -m live

Endpoints
* https://www.sec.gov/files/company_tickers.json
    {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {...}}
* https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   (CIK zero-padded to 10)
    {"cik": 320193, "entityName": "...", "facts": {"us-gaap": {"Revenues": {"units": {"USD":
      [{"start": "2022-10-01", "end": "2023-09-30", "val": 1, "accn": "0000320193-23-000106",
        "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03", "frame": "CY2023"}]}}}}}
  Instant facts (balance sheet) have no "start". The same period shows up again in later
  filings as a comparative, each with its own "accn"/"filed"; all versions are kept so queries
  can be point-in-time.
* SEC rules: a descriptive User-Agent with contact details is required, and at most
  10 requests/second.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from fin_core.providers.base import ProviderError, RateLimitError
from fin_core.providers.edgar_concepts import PERIODIC_FORMS, TAG_TO_CONCEPT

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


class EdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 0.12,  # ~8 req/s, under the SEC's 10 req/s limit
    ):
        self._ua = user_agent or os.environ.get("SEC_USER_AGENT", "")
        if "@" not in self._ua:
            raise ProviderError(
                "SEC requires a User-Agent with contact details: set SEC_USER_AGENT "
                "(e.g. 'Your Name you@example.com')"
            )
        self._client = client or httpx.Client(timeout=60)
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _get(self, url: str) -> Any:
        with self._lock:  # simple global throttle
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            resp = self._client.get(url, headers={"User-Agent": self._ua})
        except httpx.HTTPError as exc:
            raise ProviderError(f"EDGAR request failed: {type(exc).__name__}") from None
        if resp.status_code == 404:
            raise ProviderError(f"EDGAR: not found: {url}")
        if resp.status_code in (403, 429):
            raise RateLimitError(f"EDGAR: HTTP {resp.status_code} (blocked or rate limited)")
        if resp.status_code >= 400:
            raise ProviderError(f"EDGAR: HTTP {resp.status_code} for {url}")
        try:
            return resp.json()
        except ValueError:
            raise ProviderError(f"EDGAR: response was not JSON: {url}") from None

    def ticker_map(self) -> dict[str, dict[str, str]]:
        """``{"AAPL": {"cik": "0000320193", "name": "Apple Inc."}, ...}``"""
        data = self._get(TICKERS_URL)
        if not isinstance(data, dict):
            raise ProviderError("EDGAR: unexpected company_tickers.json shape")
        out: dict[str, dict[str, str]] = {}
        for row in data.values():
            try:
                out[str(row["ticker"]).upper()] = {
                    "cik": pad_cik(row["cik_str"]),
                    "name": str(row.get("title", "")),
                }
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed rows rather than failing the whole map
        if not out:
            raise ProviderError("EDGAR: company_tickers.json contained no tickers")
        return out

    def company_facts(self, cik: int | str) -> dict[str, Any]:
        data = self._get(FACTS_URL.format(cik=pad_cik(cik)))
        if not isinstance(data, dict) or "facts" not in data:
            raise ProviderError(f"EDGAR: unexpected companyfacts shape for CIK {cik}")
        return data


def normalize_facts(
    payload: dict[str, Any],
    *,
    forms: Iterable[str] = PERIODIC_FORMS,
    tag_to_concept: dict[str, str] = TAG_TO_CONCEPT,
) -> list[dict[str, Any]]:
    """Flatten a company-facts payload into rows for ``repo.upsert_facts``.

    Only mapped tags and periodic forms are kept. Rows missing required fields, or with
    non-numeric values, are skipped. Duplicate natural keys are collapsed.
    """
    wanted_forms = set(forms)
    rows: dict[tuple, dict[str, Any]] = {}
    for taxonomy, tags in (payload.get("facts") or {}).items():
        for tag_name, body in (tags or {}).items():
            full_tag = f"{taxonomy}/{tag_name}"
            concept = tag_to_concept.get(full_tag)
            if concept is None:
                continue
            for unit, entries in ((body or {}).get("units") or {}).items():
                for e in entries:
                    if e.get("form") not in wanted_forms:
                        continue
                    try:
                        end = date.fromisoformat(e["end"])
                        filed = date.fromisoformat(e["filed"])
                        start = date.fromisoformat(e["start"]) if e.get("start") else end
                        value = Decimal(str(e["val"]))
                        accession = str(e["accn"])
                    except (KeyError, ValueError, TypeError, InvalidOperation):
                        continue
                    if not value.is_finite():
                        continue
                    key = (full_tag, unit, start, end, accession)
                    rows[key] = {
                        "concept": concept,
                        "tag": full_tag,
                        "unit": unit,
                        "period_start": start,
                        "period_end": end,
                        "instant": not e.get("start"),
                        "fiscal_year": e.get("fy"),
                        "fiscal_period": e.get("fp"),
                        "form": e["form"],
                        "filed": filed,
                        "accession": accession,
                        "value": value,
                    }
    return list(rows.values())
