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

# Share of EDGAR listings with an unusable symbol at which the skip is logged as a WARNING. A
# heuristic, not a measured baseline: a handful of oddities is normal, 5% is a format change.
SKIPPED_SHARE_WARNING = 0.05


class EdgarFundamentalsSource:
    def __init__(self, client: EdgarClient):
        self._client = client

    def ticker_directory(self) -> Mapping[Ticker, CompanyListing]:
        directory: dict[Ticker, CompanyListing] = {}
        skipped: list[str] = []
        for symbol, info in self._client.ticker_map().items():
            try:
                directory[Ticker(symbol)] = CompanyListing(info["cik"], info["name"])
            except InvariantViolation:
                skipped.append(str(symbol))  # a symbol that is not a ticker we could ever hold
        if skipped:
            share = len(skipped) / (len(skipped) + len(directory))
            # The list always has a few oddities, so a handful is routine (INFO). A large share
            # means the symbol format changed, which must not go unnoticed: under Prefect only
            # WARNING and above from library loggers reach the run's log, so that case is one.
            level = logging.WARNING if share >= SKIPPED_SHARE_WARNING else logging.INFO
            log.log(
                level,
                "skipped %d of %d EDGAR listings (%.1f%%) whose symbol is not a valid ticker "
                "(e.g. %s)",
                len(skipped),
                len(skipped) + len(directory),
                share * 100,
                ", ".join(skipped[:5]),
            )
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
