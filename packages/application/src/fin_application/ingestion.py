"""Use cases: bring market data in. Each records itself as a job run.

Failure semantics, shared by all of them: a failing ticker or series, including a storage error
while writing it, does not stop the others (the run ends ``partial``); a rate-limit or
access-denied answer aborts the run so we stop hammering the provider; a run is ``failed``
until it completes normally.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from fin_application.errors import AccessDeniedError, ProviderError, RateLimitError
from fin_application.job_runs import JobRunTracker
from fin_application.ports import (
    FundamentalsSource,
    PriceSource,
    UnitOfWorkFactory,
    UniverseSource,
    YieldSource,
)
from fin_application.runs import RunSummary, describe, run_status, tracked_run
from fin_domain.market_data import is_restated
from fin_domain.shared import Ticker

# FRED revises some series after first publication; re-read the last few days each run.
YIELD_OVERLAP_DAYS = 7

# Errors after which continuing is pointless (we would only be blocked again).
_ABORT = (RateLimitError, AccessDeniedError)


def _reason(exc: Exception) -> str:
    return str(exc) if isinstance(exc, ProviderError) else describe(exc)


def _wanted(tickers: Sequence[str] | None) -> set[Ticker] | None:
    return {Ticker(t) for t in tickers} if tickers else None


class SyncUniverse:
    """Upsert the configured universe. Does not deactivate tickers removed from the file."""

    def __init__(self, uow_factory: UnitOfWorkFactory, universe: UniverseSource):
        self._uow_factory = uow_factory
        self._universe = universe

    def execute(self) -> int:
        updates = self._universe.securities()
        with self._uow_factory() as uow:
            return uow.securities.upsert(updates)


class IngestFundamentals:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tracker: JobRunTracker,
        source: FundamentalsSource,
    ):
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._source = source

    def execute(self, tickers: Sequence[str] | None = None) -> RunSummary:
        """Resolve missing CIKs, then store all periodic-filing facts for each stock."""
        wanted = _wanted(tickers)
        with tracked_run(self._tracker, "edgar") as summary:
            with self._uow_factory() as uow:
                stocks = [
                    s
                    for s in uow.securities.list()
                    if s.has_fundamentals and (wanted is None or s.ticker in wanted)
                ]
            try:
                if any(s.cik is None for s in stocks):
                    directory = self._source.ticker_directory()
                    with self._uow_factory() as uow:
                        for sec in stocks:
                            listing = directory.get(sec.ticker)
                            if sec.cik is None and listing is not None:
                                sec.resolve_identity(listing.cik, listing.name)
                                uow.securities.save(sec)
            except ProviderError as exc:  # directory unavailable: nothing can be resolved
                summary.failed["*"] = str(exc)
                summary.status = "failed"
                return summary

            aborted = False
            for sec in stocks:
                key = str(sec.ticker)
                if sec.cik is None:
                    summary.failed[key] = "no CIK (not in SEC ticker list)"
                    continue
                try:
                    facts = self._source.facts(sec.cik)
                    with self._uow_factory() as uow:
                        summary.rows += uow.facts.add(sec.ticker, facts)
                except _ABORT as exc:
                    summary.failed[key] = str(exc)
                    aborted = True
                    break
                except Exception as exc:  # provider errors and storage errors alike
                    summary.failed[key] = _reason(exc)
            summary.status = run_status(summary, len(stocks), aborted)
        return summary


class IngestYields:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tracker: JobRunTracker,
        source: YieldSource,
        universe: UniverseSource,
    ):
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._source = source
        self._universe = universe

    def execute(
        self, series_ids: Sequence[str] | None = None, start: date | None = None
    ) -> RunSummary:
        """Fetch series incrementally (from the last stored date minus an overlap). Without
        ``series_ids`` the configured bond series are fetched."""
        ids = list(series_ids) if series_ids else [s.id for s in self._universe.bond_series()]
        with tracked_run(self._tracker, "fred") as summary:
            aborted = False
            for series_id in ids:
                try:
                    since = start
                    if since is None:
                        with self._uow_factory() as uow:
                            last = uow.yields.latest_date(series_id)
                        since = last - timedelta(days=YIELD_OVERLAP_DAYS) if last else None
                    points = self._source.observations(series_id, since)
                    with self._uow_factory() as uow:
                        summary.rows += uow.yields.upsert(series_id, points)
                except _ABORT as exc:
                    summary.failed[series_id] = str(exc)
                    aborted = True
                    break
                except Exception as exc:
                    summary.failed[series_id] = _reason(exc)
            summary.status = run_status(summary, len(ids), aborted)
        return summary


class IngestPrices:
    def __init__(self, uow_factory: UnitOfWorkFactory, tracker: JobRunTracker, source: PriceSource):
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._source = source

    def execute(
        self,
        tickers: Sequence[str] | None = None,
        *,
        backfill_period: str = "max",
        refresh_period: str = "1mo",
    ) -> RunSummary:
        """Store price bars for every active security (stocks and bond ETFs).

        A security with no stored bars is back-filled (``backfill_period``); otherwise only a
        recent window is re-read (``refresh_period``). If that window shows the source restated
        prices we already hold (split/dividend adjustment), the whole history is re-fetched so
        the series stays on one adjustment basis; those tickers are listed in ``rebackfilled``.

        NOTE: stored prices are the source's adjusted prices, not as-traded prices. That is fine
        for charts and current valuation, but a point-in-time backtest needs as-traded prices
        (raw OHLC plus adjustment factors), which is planned with the backtester.
        """
        wanted = _wanted(tickers)
        with tracked_run(self._tracker, "prices") as summary:
            with self._uow_factory() as uow:
                targets = [
                    s.ticker for s in uow.securities.list() if wanted is None or s.ticker in wanted
                ]
                has_bars = uow.prices.tickers_with_bars()
            aborted = False
            for ticker in targets:
                known = ticker in has_bars
                key = str(ticker)
                try:
                    bars = self._source.history(
                        ticker, refresh_period if known else backfill_period
                    )
                    if not bars:
                        summary.failed[key] = "no price data returned"
                        continue
                    if known:
                        with self._uow_factory() as uow:
                            stored = uow.prices.closes_since(ticker, min(b.ts for b in bars))
                        if is_restated(stored, bars):
                            full = self._source.history(ticker, backfill_period)
                            if full:
                                bars = full
                                summary.rebackfilled.append(key)
                    with self._uow_factory() as uow:
                        summary.rows += uow.prices.upsert(ticker, bars)
                except _ABORT as exc:
                    summary.failed[key] = str(exc)
                    aborted = True
                    break
                except Exception as exc:  # providers raise assorted errors; isolate per ticker
                    summary.failed[key] = _reason(exc)
            summary.status = run_status(summary, len(targets), aborted)
        return summary
