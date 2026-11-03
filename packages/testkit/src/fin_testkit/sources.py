"""Scriptable stand-ins for the data sources. Give each a table of answers; an ``Exception``
value is raised instead of returned, so failure paths are one line to set up."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from fin_application.ports import CompanyListing
from fin_domain.market_data import BondSeries, Fact, PriceBar, SecurityUpdate, YieldPoint
from fin_domain.shared import Ticker


def _answer(value):
    if isinstance(value, Exception):
        raise value
    return value


class FakeFundamentalsSource:
    def __init__(
        self,
        directory: Mapping[Ticker, CompanyListing] | Exception | None = None,
        facts: Mapping[str, list[Fact] | Exception] | None = None,
    ):
        self.directory = directory if directory is not None else {}
        self.facts_by_cik = dict(facts or {})
        self.directory_calls = 0
        self.fact_calls: list[str] = []

    def ticker_directory(self) -> Mapping[Ticker, CompanyListing]:
        self.directory_calls += 1
        return _answer(self.directory)

    def facts(self, cik: str) -> list[Fact]:
        self.fact_calls.append(cik)
        return _answer(self.facts_by_cik.get(cik, []))


class FakePriceSource:
    """``histories[ticker]`` is a list of bars, an exception, or a function of the period."""

    def __init__(self, histories: Mapping[Ticker, object] | None = None):
        self.histories = dict(histories or {})
        self.calls: list[tuple[Ticker, str]] = []

    def history(self, ticker: Ticker, period: str) -> list[PriceBar]:
        self.calls.append((ticker, period))
        value = self.histories.get(ticker, [])
        if callable(value):
            value = value(period)
        return _answer(value)


class FakeYieldSource:
    def __init__(self, series: Mapping[str, list[YieldPoint] | Exception] | None = None):
        self.series = dict(series or {})
        self.calls: list[tuple[str, date | None]] = []

    def observations(self, series_id: str, since: date | None) -> list[YieldPoint]:
        self.calls.append((series_id, since))
        return _answer(self.series.get(series_id, []))


class FakeUniverse:
    def __init__(
        self, securities: list[SecurityUpdate] | None = None, series: list[BondSeries] | None = None
    ):
        self._securities = securities or []
        self._series = series or []

    def securities(self) -> list[SecurityUpdate]:
        return list(self._securities)

    def bond_series(self) -> list[BondSeries]:
        return list(self._series)
