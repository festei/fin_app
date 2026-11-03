"""One contract for the sandbox repository and the price/signal queries it relies on, run against
the in-memory fakes and the SQL adapter (SQLite, and Postgres when TEST_DATABASE_URL is set)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db_fixtures import uow_factory  # noqa: F401 - pytest fixture

from fin_domain.market_data import PriceBar, SecurityUpdate
from fin_domain.sandbox import (
    OrderStatus,
    Portfolio,
    PortfolioMode,
    PortfolioSnapshot,
    StrategyRules,
)
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import ScoreEntry, Signal

D = Decimal
AAA, BBB = Ticker("AAA"), Ticker("BBB")
T0 = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)


def at(day: int, hour: int = 21) -> datetime:
    return datetime(2025, 3, day, hour, tzinfo=UTC)


@pytest.fixture
def seeded(uow_factory):  # noqa: F811
    with uow_factory() as uow:
        uow.securities.upsert([SecurityUpdate(AAA), SecurityUpdate(BBB)])
    return uow_factory


def new_portfolio(mode="manual", cash=1000, **kw) -> Portfolio:
    return Portfolio.open("test", mode=mode, cash=cash, defaults=StrategyRules(), now=T0, **kw)


def add(factory, **kw) -> Portfolio:
    with factory() as uow:
        return uow.portfolios.add(new_portfolio(**kw))


# --- portfolios ---------------------------------------------------------------------------------


def test_a_portfolio_comes_back_as_it_was_stored(seeded):
    manual = add(seeded, fee_bps=25)
    strategy = add(seeded, mode="strategy", strategy={"max_positions": 3}, last_signal_id=7)
    assert (manual.id, strategy.id) == (1, 2)
    with seeded() as uow:
        got_manual, got_strategy = uow.portfolios.get(1), uow.portfolios.get(2)
    assert (got_manual.name, got_manual.mode, got_manual.strategy) == (
        "test",
        PortfolioMode.MANUAL,
        None,
    )
    assert got_manual.initial_cash == got_manual.cash == D("1000") and got_manual.fee_bps == D("25")
    assert got_manual.created_at == T0 and got_manual.created_at.utcoffset() == timedelta(0)
    assert (
        got_strategy.strategy == StrategyRules(max_positions=3) and got_strategy.last_signal_id == 7
    )
    assert got_manual.orders == []


def test_unknown_portfolios_are_none_or_not_found(seeded):
    with seeded() as uow:
        assert uow.portfolios.get(99) is None
        with pytest.raises(NotFound):
            uow.portfolios.load_exclusive(99)


def test_portfolios_are_listed_in_id_order(seeded):
    add(seeded), add(seeded), add(seeded)
    with seeded() as uow:
        assert uow.portfolios.ids() == [1, 2, 3]
        assert [p.id for p in uow.portfolios.list()] == [1, 2, 3]


def test_saving_stores_cash_the_signal_cursor_and_new_orders_with_ids(seeded):
    p = add(seeded)
    with seeded() as uow:
        p = uow.portfolios.load_exclusive(p.id)
        p.cash, p.last_signal_id = D("940.0000"), 12
        order = p.place_order(AAA, "buy", T0, notional=60)
        assert order.id is None
        uow.portfolios.save(p)
        assert order.id is not None
    with seeded() as uow:
        got = uow.portfolios.get(p.id)
    assert (got.cash, got.last_signal_id) == (D("940"), 12)
    (o,) = got.orders
    assert (o.ticker, o.side.value, o.notional, o.status, o.created_at) == (
        AAA, "buy", D("60"), OrderStatus.PENDING, T0,
    )  # fmt: skip


def test_a_changed_order_is_written_and_a_fill_round_trips(seeded):
    p = add(seeded)
    with seeded() as uow:
        p = uow.portfolios.get(p.id)
        p.place_order(AAA, "buy", T0, quantity=4)
        p.place_order(BBB, "buy", T0, quantity=1)
        uow.portfolios.save(p)
    with seeded() as uow:
        p = uow.portfolios.load_exclusive(p.id)
        first, second = p.orders
        first.fill(D("4"), D("12.5"), D("0.0500"), at(11))
        second.cancel("changed my mind")
        uow.portfolios.save(p)
    with seeded() as uow:
        first, second = uow.portfolios.get(p.id).orders
    assert (first.status, first.fill_quantity, first.fill_price, first.fee) == (
        OrderStatus.FILLED, D("4"), D("12.5"), D("0.05"),
    )  # fmt: skip
    assert first.filled_at == at(11) and first.filled_at.utcoffset() == timedelta(0)
    assert (second.status, second.reason) == (OrderStatus.CANCELLED, "changed my mind")


def test_orders_are_listed_newest_first_with_a_status_filter_and_a_limit(seeded):
    p = add(seeded)
    with seeded() as uow:
        p = uow.portfolios.get(p.id)
        for t in (AAA, BBB, AAA):
            p.place_order(t, "buy", T0, quantity=1)
        uow.portfolios.save(p)  # now they have ids
        p.cancel_order(2, "no")
        uow.portfolios.save(p)
    with seeded() as uow:
        assert [o.id for o in uow.portfolios.orders(p.id)] == [3, 2, 1]
        assert [o.id for o in uow.portfolios.orders(p.id, limit=2)] == [3, 2]
        assert [o.id for o in uow.portfolios.orders(p.id, status="cancelled")] == [2]
        assert [str(o.ticker) for o in uow.portfolios.orders(p.id, status="pending")] == [
            "AAA",
            "AAA",
        ]


def test_saving_a_portfolio_that_was_never_added_fails(seeded):
    ghost = new_portfolio()
    ghost.id = 99
    with seeded() as uow, pytest.raises(LookupError):
        uow.portfolios.save(ghost)


def test_an_order_for_an_unknown_security_is_refused(seeded):
    p = add(seeded)
    with seeded() as uow:
        p = uow.portfolios.get(p.id)
        p.place_order(Ticker("ZZZ"), "buy", T0, quantity=1)
        with pytest.raises(NotFound):
            uow.portfolios.save(p)


# --- snapshots ----------------------------------------------------------------------------------


def test_a_snapshot_is_one_row_per_day_and_the_history_is_oldest_first(seeded):
    p = add(seeded)
    with seeded() as uow:
        uow.portfolios.save_snapshot(
            p.id, PortfolioSnapshot(date(2025, 3, 12), D("60"), D("60"), D("120"))
        )
        uow.portfolios.save_snapshot(
            p.id, PortfolioSnapshot(date(2025, 3, 11), D("100"), D("0"), D("100"))
        )
        uow.portfolios.save_snapshot(
            p.id, PortfolioSnapshot(date(2025, 3, 12), D("70"), D("60"), D("130"))
        )
    with seeded() as uow:
        rows = uow.portfolios.history(p.id)
        assert uow.portfolios.history(99) == []
    assert [(r.as_of.isoformat(), r.total_value) for r in rows] == [
        ("2025-03-11", D("100")), ("2025-03-12", D("130")),
    ]  # fmt: skip


# --- the price and signal queries -----------------------------------------------------------------


def bars(factory, ticker, closes: dict[int, float]):
    with factory() as uow:
        uow.prices.upsert(ticker, [PriceBar(ts=at(d), close=c) for d, c in closes.items()])


def test_close_at_is_the_stored_close_of_exactly_that_bar(seeded):
    bars(seeded, AAA, {10: 10.0, 11: 12.5})
    with seeded() as uow:
        assert uow.prices.close_at(AAA, at(11)) == 12.5
        assert uow.prices.close_at(AAA, at(12)) is None
        assert uow.prices.close_at(AAA, at(11, 20)) is None  # not the same instant


def test_the_first_bar_in_a_window_is_the_earliest_one_inclusive_at_both_ends(seeded):
    bars(seeded, AAA, {10: 10.0, 12: 12.0, 14: 14.0})
    with seeded() as uow:
        assert uow.prices.first_bar_between(AAA, at(11, 0), at(20)).close == 12.0
        assert uow.prices.first_bar_between(AAA, at(12), at(12)).close == 12.0  # inclusive
        assert uow.prices.first_bar_between(AAA, at(11, 0), at(11, 23)) is None
        assert uow.prices.first_bar_between(AAA, at(15), at(20)) is None
        assert uow.prices.first_bar_between(BBB, at(1), at(20)) is None


def test_signals_after_an_id_come_oldest_first_and_the_latest_id_is_zero_when_empty(seeded):
    with seeded() as uow:
        assert uow.signals.latest_id() == 0 and uow.signals.after(0) == []
        score = uow.scores.add(ScoreEntry(AAA, T0, 80.0, False, False, "x", computed_at=T0))
        ids = [
            uow.signals.add(t, score.id, Signal(str(t), "buy", 80.0, T0, "")).id
            for t in (AAA, BBB, AAA)
        ]
    with seeded() as uow:
        assert uow.signals.latest_id() == ids[-1]
        assert [s.id for s in uow.signals.after(0)] == ids
        assert [s.id for s in uow.signals.after(ids[0])] == ids[1:]
        assert uow.signals.after(ids[-1]) == []
        assert [str(s.ticker) for s in uow.signals.after(ids[0])] == ["BBB", "AAA"]
