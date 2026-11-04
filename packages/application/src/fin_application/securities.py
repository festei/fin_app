"""Use cases that read about securities: the searchable universe, price history for the charts,
and score history with its signal markers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from fin_application.ports import (
    Clock,
    ScorePointRow,
    SignalPointRow,
    UnitOfWork,
    UnitOfWorkFactory,
)
from fin_application.scoring import parse_ticker
from fin_domain.market_data import BarLike, Candle, PriceBar, Security, aggregate_candles
from fin_domain.shared import NotFound, Ticker


@dataclass(frozen=True)
class SecurityRow:
    security: Security
    watched: bool


@dataclass(frozen=True)
class ScoreHistory:
    scores: list[ScorePointRow]
    signals: list[SignalPointRow]  # transitions, for chart markers


def _known(uow: UnitOfWork, raw: str) -> Ticker:
    try:
        ticker = parse_ticker(raw)
    except NotFound:
        raise NotFound("ticker", raw.upper()) from None
    if uow.securities.get(ticker) is None:
        raise NotFound("ticker", str(ticker))
    return ticker


class SecurityCatalog:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def search(
        self,
        q: str | None = None,
        sector: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[SecurityRow]:
        """The active universe, each flagged if it is on the watchlist."""
        with self._uow_factory() as uow:
            found = uow.security_search.search(q, sector, kind, limit)
            watched = set(uow.watchlist.tickers())
        return [SecurityRow(s, str(s.ticker) in watched) for s in found]

    def prices(self, ticker: str, days: int = 365, max_points: int = 1000) -> Sequence[BarLike]:
        """Daily bars for the last ``days`` days, oldest first. With more than ``max_points``
        bars, consecutive bars are merged into candles (first open, max high, min low, last close,
        summed volume), so the result serves candlestick and high/low charts as well as close
        lines. Prices are split/dividend-adjusted. Inactive securities are still served."""
        since = self._clock.now() - timedelta(days=days)
        with self._uow_factory() as uow:
            symbol = _known(uow, ticker)
            bars: list[PriceBar] = uow.prices.bars_since(symbol, since)
        candles: list[BarLike | Candle] = aggregate_candles(bars, max_points)
        return candles

    def score_history(self, ticker: str, days: int = 365, limit: int = 1000) -> ScoreHistory:
        """Stored scores (oldest first) and the signals they produced, for the conviction chart.
        Blocked and all-abstained runs are included but flagged, so a gap is visible as a gap.
        Inactive securities are still served."""
        since = self._clock.now() - timedelta(days=days)
        with self._uow_factory() as uow:
            symbol = _known(uow, ticker)
            scores = uow.score_history.scores(symbol, since, limit)
            # markers only inside the returned window: if `limit` cut off older scores, so are
            # the signals they produced
            floor = scores[0].as_of if len(scores) == limit else since
            signals = uow.score_history.signals(symbol, floor)
        return ScoreHistory(scores, signals)
