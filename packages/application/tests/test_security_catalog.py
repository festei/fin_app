"""Searching the universe, price history and score history, on in-memory fakes."""

from __future__ import annotations

from datetime import timedelta

import pytest
from scenario import NOW, World

from fin_application.securities import SecurityCatalog
from fin_domain.market_data import PriceBar, SecurityUpdate
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import ScoreEntry, Signal

AAA = Ticker("AAA")


def catalog(w: World) -> SecurityCatalog:
    return SecurityCatalog(w.uow_factory, w.clock)


def bars(w: World, ticker: Ticker, n: int):
    with w.uow_factory() as uow:
        uow.prices.upsert(
            ticker,
            [
                PriceBar(
                    ts=NOW - timedelta(days=n - i),
                    close=float(i),
                    high=float(i) + 1,
                    low=float(i) - 1,
                )
                for i in range(n)
            ],
        )


def test_search_flags_what_is_on_the_watchlist():
    w = World()
    w.seed("AAA", name="Alpha")
    w.seed("BBB", name="Beta")
    with w.uow_factory() as uow:
        uow.watchlist.add(Ticker("BBB"))
    rows = catalog(w).search(limit=10)
    assert [(str(r.security.ticker), r.watched) for r in rows] == [("AAA", False), ("BBB", True)]


def test_prices_are_oldest_first_within_the_window_and_thinned_into_candles():
    w = World()
    with w.uow_factory() as uow:  # no seeded bar: this test brings its own prices
        uow.securities.upsert([SecurityUpdate(AAA)])
    bars(w, AAA, 30)
    cat = catalog(w)
    assert [b.close for b in cat.prices("aaa", days=5)] == [
        25.0,
        26.0,
        27.0,
        28.0,
        29.0,
    ]  # five days back, inclusive
    thinned = cat.prices("AAA", days=365, max_points=10)
    assert len(thinned) <= 10 and thinned[-1].close == 29.0  # the last candle ends on the last bar
    assert max(b.high for b in thinned) == 30.0  # the true extreme survives the merge


def test_unknown_tickers_are_not_found_for_prices_and_scores():
    w = World()
    for read in (catalog(w).prices, catalog(w).score_history):
        with pytest.raises(NotFound):
            read("ZZZ")
        with pytest.raises(NotFound):
            read("!!")


def add_runs(w: World, n: int):
    with w.uow_factory() as uow:
        for i in range(n):
            moment = NOW - timedelta(days=n - i)
            score = uow.scores.add(
                ScoreEntry(AAA, moment, 50.0 + i, False, False, "x", computed_at=moment)
            )
            uow.signals.add(AAA, score.id, Signal("AAA", "buy", 50.0 + i, moment, ""))


def test_signals_outside_the_returned_score_window_are_dropped():
    w = World()
    w.seed("AAA")
    add_runs(w, 6)
    history = catalog(w).score_history("AAA", days=365, limit=3)
    assert [p.conviction for p in history.scores] == [53.0, 54.0, 55.0]  # the newest three
    # the limit cut off the older scores, so the signals they produced are not markers either
    assert [s.conviction for s in history.signals] == [53.0, 54.0, 55.0]


def test_without_a_cut_every_signal_in_the_period_is_a_marker():
    w = World()
    w.seed("AAA")
    add_runs(w, 4)
    history = catalog(w).score_history("AAA", days=365, limit=100)
    assert len(history.scores) == len(history.signals) == 4
    assert [p.conviction for p in history.scores] == sorted(p.conviction for p in history.scores)


def test_the_period_is_counted_back_from_the_clock():
    w = World()
    w.seed("AAA")
    add_runs(w, 6)  # run times: 6, 5, ... 1 days before NOW
    w.clock._now = NOW + timedelta(days=2)
    history = catalog(w).score_history("AAA", days=4, limit=100)  # since = NOW - 2 days
    assert [p.conviction for p in history.scores] == [54.0, 55.0]
