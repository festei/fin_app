"""The data-source ports (``fin_application.ports``) implemented on top of the vendor clients.

These are the anti-corruption layer: vendor shapes (EDGAR JSON, FRED strings, pandas frames) go
in, domain objects (``Fact``, ``YieldPoint``, ``PriceBar``) come out.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date

from fin_application.ports import CompanyListing
from fin_domain.market_data import Fact, PriceBar, YieldPoint
from fin_domain.shared import InvariantViolation, Ticker
from fin_providers.edgar import EdgarClient, normalize_facts
from fin_providers.frames import bars_from_frame
from fin_providers.fred import FredClient
from fin_providers.yfinance_provider import YFinancePriceProvider

log = logging.getLogger(__name__)


class EdgarFundamentalsSource:
    def __init__(self, client: EdgarClient):
        self._client = client

    def ticker_directory(self) -> Mapping[Ticker, CompanyListing]:
        directory: dict[Ticker, CompanyListing] = {}
        for symbol, info in self._client.ticker_map().items():
            try:
                directory[Ticker(symbol)] = CompanyListing(info["cik"], info["name"])
            except InvariantViolation:
                continue  # a listing whose symbol is not a ticker we could ever hold
        return directory

    def facts(self, cik: str) -> list[Fact]:
        facts: list[Fact] = []
        skipped = 0
        for row in normalize_facts(self._client.company_facts(cik)):
            try:
                facts.append(Fact(**row))
            except InvariantViolation:
                skipped += 1  # a figure the domain would not accept (e.g. a period backwards)
        if skipped:
            log.warning("skipped %d facts that broke a domain rule (CIK %s)", skipped, cik)
        return facts


class YahooPriceSource:
    def __init__(self, provider: YFinancePriceProvider | None = None):
        self._provider = provider or YFinancePriceProvider()

    def history(self, ticker: Ticker, period: str) -> list[PriceBar]:
        return bars_from_frame(self._provider.history(str(ticker), period=period))


class FredYieldSource:
    def __init__(self, client: FredClient):
        self._client = client

    def observations(self, series_id: str, since: date | None) -> list[YieldPoint]:
        return [YieldPoint(d, v) for d, v in self._client.observations(series_id, since)]
