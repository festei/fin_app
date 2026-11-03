"""The paper-trading use cases on in-memory fakes. Bars are daily closes at 21:00 UTC; an order
decided on day D fills at the first bar on a LATER day."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from scenario import World

from fin_application.sandbox import (
    CancelOrder,
    CreatePortfolio,
    PlaceOrder,
    PortfolioReader,
    RunSandbox,
)
from fin_domain.market_data import PriceBar, SecurityUpdate
from fin_domain.sandbox import OrderError, OrderStatus
from fin_domain.shared import NotFound, RunStatus, Ticker
from fin_domain.signals import ScoreEntry, Signal
from fin_testkit import FixedSandboxConfig, InMemoryPortfolioRepository, InMemoryUnitOfWork

D = Decimal
AAA, BBB = Ticker("AAA"), Ticker("BBB")


def at(day: int, hour: int = 21) -> datetime:
    return datetime(2025, 3, day, hour, tzinfo=UTC)


class Sandbox(World):
    def __init__(self):
        super().__init__()
        self.clock._now = at(10, 15)
        with self.uow_factory() as uow:
            uow.securities.upsert(
                [SecurityUpdate(AAA, {"name": "Alpha"}), SecurityUpdate(BBB, {"name": "Beta"})]
            )
            uow.securities.upsert([SecurityUpdate(Ticker("OLD"), {"active": False})])
        defaults = FixedSandboxConfig()
        self.create = CreatePortfolio(self.uow_factory, defaults, self.clock)
        self.place = PlaceOrder(self.uow_factory, self.clock)
        self.cancel = CancelOrder(self.uow_factory)
        self.run = RunSandbox(self.uow_factory, self.tracker, self.clock)
        self.reader = PortfolioReader(self.uow_factory, self.clock)

    def prices(self, ticker: Ticker, closes: dict[int, float]) -> None:
        with self.uow_factory() as uow:
            uow.prices.upsert(ticker, [PriceBar(ts=at(d), close=c) for d, c in closes.items()])

    def signal(self, ticker: Ticker, kind: str, day: int) -> int:
        with self.uow_factory() as uow:
            score = uow.scores.add(
                ScoreEntry(ticker, at(day), 80.0, False, False, "x", computed_at=at(day))
            )
            return uow.signals.add(
                ticker, score.id, Signal(str(ticker), kind, 80.0, at(day), "")
            ).id

    def orders(self, pid: int):
        with self.uow_factory() as uow:
            return list(reversed(uow.portfolios.orders(pid)))


@pytest.fixture
def sb():
    return Sandbox()


# --- creating and viewing -------------------------------------------------------------------------


def test_a_new_portfolio_is_valued_at_its_starting_cash(sb):
    view = sb.create.execute("Mine", mode="manual", cash=1000)
    assert (view.portfolio.id, view.invested, view.total, view.return_pct) == (
        1,
        D(0),
        D(1000),
        D(0),
    )
    assert view.portfolio.created_at == at(10, 15) and view.portfolio.strategy is None
    assert [v.portfolio.id for v in sb.reader.list()] == [1]


def test_a_strategy_portfolio_starts_from_the_defaults_and_keeps_its_overrides(sb):
    view = sb.create.execute("S", mode="strategy", cash=100, strategy={"max_positions": 2})
    assert (
        view.portfolio.strategy.max_positions == 2 and view.portfolio.strategy.position_pct == 0.2
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(mode="manual", cash=0),
        dict(mode="nope", cash=5),
        dict(mode="manual", cash=float("nan")),
        dict(mode="strategy", cash=5, strategy={"bogus": 1}),
        dict(mode="manual", cash=5, fee_bps=5000),
    ],
)
def test_invalid_portfolios_are_refused_and_nothing_is_stored(sb, kwargs):
    with pytest.raises(OrderError):
        sb.create.execute("x", **kwargs)
    assert sb.store.portfolios == {}


def test_a_strategy_ignores_signals_from_before_it_existed(sb):
    sb.prices(AAA, {10: 10.0})
    sb.signal(AAA, "buy", 10)  # history
    pid = sb.create.execute("S", mode="strategy", cash=100).portfolio.id
    sb.run.execute(at(10, 22))
    assert sb.orders(pid) == []
    assert sb.store.portfolios[pid].last_signal_id == 1  # it starts after the newest signal


# --- placing and cancelling orders ----------------------------------------------------------------


def test_an_order_is_queued_with_the_time_it_was_decided(sb):
    pid = sb.create.execute("Mine", mode="manual", cash=100).portfolio.id
    order = sb.place.execute(pid, "aaa", "buy", notional=50)
    assert (order.id, str(order.ticker), order.status, order.created_at) == (
        1, "AAA", OrderStatus.PENDING, at(10, 15),
    )  # fmt: skip
    assert sb.place.execute(pid, "AAA", "buy", quantity=1, now=at(9)).created_at == at(9)


def test_unknown_targets_and_wrong_modes_are_refused(sb):
    manual = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    strategy = sb.create.execute("s", mode="strategy", cash=100).portfolio.id
    with pytest.raises(NotFound) as unknown_ticker:
        sb.place.execute(manual, "ZZZ", "buy", quantity=1)
    assert (unknown_ticker.value.entity, unknown_ticker.value.key) == ("ticker", "ZZZ")
    with pytest.raises(NotFound) as unknown_portfolio:
        sb.place.execute(999, "AAA", "buy", quantity=1)
    assert (unknown_portfolio.value.entity, unknown_portfolio.value.key) == ("portfolio", 999)
    with pytest.raises(OrderError, match="trades from signals"):
        sb.place.execute(strategy, "AAA", "buy", quantity=1)
    with pytest.raises(NotFound):  # an inactive security cannot be traded either
        sb.place.execute(manual, "OLD", "buy", quantity=1)
    with pytest.raises(NotFound):  # and something that is not a symbol at all
        sb.place.execute(manual, "!!", "buy", quantity=1)


def test_the_mode_is_checked_before_the_ticker(sb):
    strategy = sb.create.execute("s", mode="strategy", cash=100).portfolio.id
    with pytest.raises(OrderError, match="trades from signals"):
        sb.place.execute(strategy, "ZZZ", "buy", quantity=1)


def test_a_refused_order_leaves_the_portfolio_untouched(sb):
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    with pytest.raises(OrderError):
        sb.place.execute(pid, "AAA", "buy")  # no size
    assert sb.orders(pid) == []


def test_pending_orders_can_be_cancelled_only_once(sb):
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    oid = sb.place.execute(pid, "AAA", "buy", quantity=1).id
    assert sb.cancel.execute(pid, oid) is True
    assert sb.cancel.execute(pid, oid) is False
    assert sb.cancel.execute(pid, 999) is False
    assert sb.orders(pid)[0].reason == "cancelled by the user"
    with pytest.raises(NotFound):
        sb.cancel.execute(999, oid)


# --- the scheduled step ---------------------------------------------------------------------------


def test_an_order_waits_for_a_later_close_then_the_step_fills_it(sb):
    sb.prices(AAA, {10: 10.0, 11: 12.5})
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    sb.place.execute(pid, "AAA", "buy", notional=50)  # decided on the 10th
    sb.run.execute(at(10, 23))
    assert sb.orders(pid)[0].status is OrderStatus.PENDING  # the 10th's close is not available
    summary = sb.run.execute(at(12, 6))
    assert (summary.status, summary.rows) == ("ok", 1)
    (order,) = sb.orders(pid)
    assert (order.status, order.fill_price, order.fill_quantity) == (
        OrderStatus.FILLED,
        D("12.5"),
        D("4"),
    )
    view, positions = sb.reader.detail(pid)
    assert view.portfolio.cash == D("50") and [(str(p.ticker), p.name) for p in positions] == [
        ("AAA", "Alpha")
    ]


def test_a_bar_after_the_step_time_is_not_used(sb):
    sb.prices(AAA, {10: 10.0, 12: 20.0})
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    sb.place.execute(pid, "AAA", "buy", notional=50)
    sb.run.execute(at(11, 23))  # the 12th has not happened yet
    assert sb.orders(pid)[0].status is OrderStatus.PENDING
    sb.run.execute(at(12, 23))
    assert sb.orders(pid)[0].status is OrderStatus.FILLED


def test_a_strategy_buys_on_a_signal_and_sells_on_a_sell_signal(sb):
    sb.prices(AAA, {10: 10.0, 11: 10.0, 12: 12.0, 13: 12.0})
    pid = sb.create.execute("S", mode="strategy", cash=100).portfolio.id  # 20% per position
    buy = sb.signal(AAA, "buy", 10)
    sb.run.execute(at(10, 22))
    assert [(o.side.value, o.notional, o.status.value, o.signal_id) for o in sb.orders(pid)] == [
        ("buy", D("20.0000"), "pending", buy)
    ]
    sb.run.execute(at(10, 23))  # each signal is looked at once
    assert len(sb.orders(pid)) == 1
    sb.run.execute(at(11, 23))
    view, positions = sb.reader.detail(pid)
    assert view.portfolio.cash == D("80.0000") and positions[0].position.holding.quantity == D("2")

    sell = sb.signal(AAA, "sell", 12)
    sb.run.execute(at(12, 22))
    sb.run.execute(at(13, 23))
    view, positions = sb.reader.detail(pid)
    assert view.portfolio.cash == D("104") and positions == []  # sold 2 at 12
    assert [o.side.value for o in sb.orders(pid)] == ["buy", "sell"] and sb.orders(pid)[
        1
    ].signal_id == sell


def test_a_skipped_signal_is_not_reconsidered_when_cash_returns(sb):
    sb.prices(AAA, {10: 10.0})
    sb.prices(BBB, {10: 10.0, 11: 10.0})
    pid = sb.create.execute(
        "S", mode="strategy", cash=100, strategy={"max_positions": 1}
    ).portfolio.id
    sb.signal(AAA, "buy", 10)
    sb.signal(BBB, "buy", 10)  # no slot left: skipped for good
    sb.run.execute(at(10, 22))
    assert [str(o.ticker) for o in sb.orders(pid)] == ["AAA"]
    sb.run.execute(at(11, 22))
    assert [str(o.ticker) for o in sb.orders(pid)] == ["AAA"]


def test_the_snapshot_is_one_row_per_day_and_values_cash_plus_positions(sb):
    sb.prices(AAA, {11: 10.0, 12: 15.0})
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    sb.place.execute(pid, "AAA", "buy", quantity=4)
    sb.run.execute(at(11, 23))
    sb.run.execute(at(12, 22))
    sb.run.execute(at(12, 23))  # the same day again: replaces, never duplicates
    rows = sb.reader.history(pid)
    assert [(r.as_of.isoformat(), r.cash, r.positions_value, r.total_value) for r in rows] == [
        ("2025-03-11", D("60"), D("40"), D("100")),
        ("2025-03-12", D("60"), D("60"), D("120")),
    ]


def test_an_empty_sandbox_is_an_ok_no_op(sb):
    summary = sb.run.execute(at(11))
    assert (summary.status, summary.rows) == ("ok", 0)
    (run,) = [r for r in sb.store.job_runs.values() if r.source == "sandbox"]
    assert run.status is RunStatus.OK


def test_the_step_defaults_to_now_by_the_clock(sb):
    sb.prices(AAA, {10: 10.0})
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    sb.run.execute()
    assert sb.reader.history(pid)[0].as_of == date(2025, 3, 10)


def test_one_failing_portfolio_neither_blocks_nor_rolls_back_the_others(sb):
    sb.prices(AAA, {10: 10.0, 11: 10.0})
    good = sb.create.execute("good", mode="manual", cash=100).portfolio.id
    broken = sb.create.execute("broken", mode="manual", cash=100).portfolio.id
    sb.place.execute(good, "AAA", "buy", notional=40)

    class Flaky(InMemoryPortfolioRepository):
        def save_snapshot(self, portfolio_id, snapshot):
            if portfolio_id == broken:
                raise RuntimeError("boom")
            super().save_snapshot(portfolio_id, snapshot)

    class FlakyUow(InMemoryUnitOfWork):
        def __enter__(self):
            super().__enter__()
            self.portfolios = Flaky(
                self._work.portfolios, self._work.snapshots, self._work.securities
            )
            return self

    flaky = RunRebuilt(sb, lambda: FlakyUow(sb.store))
    summary = flaky.execute(at(11, 23))
    assert (summary.status, summary.rows) == ("partial", 1)
    assert f"portfolio {broken}" in summary.failed
    assert sb.reader.detail(good)[0].portfolio.cash == D("60.0000")  # still processed
    assert sb.reader.history(broken) == []  # and the broken one left nothing behind
    assert sb.run.execute(at(11, 23)).status == "ok"


def RunRebuilt(sb, factory):  # noqa: N802 - a use case built on a different unit-of-work factory
    return RunSandbox(factory, sb.tracker, sb.clock)


def test_a_step_on_a_portfolio_that_vanished_is_a_recorded_failure(sb):
    class Ghost(InMemoryPortfolioRepository):
        def ids(self):
            return [*super().ids(), 99]

    class GhostUow(InMemoryUnitOfWork):
        def __enter__(self):
            super().__enter__()
            self.portfolios = Ghost(
                self._work.portfolios, self._work.snapshots, self._work.securities
            )
            return self

    summary = RunRebuilt(sb, lambda: GhostUow(sb.store)).execute(at(11))
    assert summary.status == "failed" and "portfolio 99" in summary.failed


# --- reading --------------------------------------------------------------------------------------


def test_positions_show_cost_value_and_gain_and_are_sorted_by_ticker(sb):
    sb.prices(AAA, {11: 10.0, 12: 15.0})
    sb.prices(BBB, {11: 20.0})
    pid = sb.create.execute("m", mode="manual", cash=1000).portfolio.id
    sb.place.execute(pid, "BBB", "buy", quantity=1)
    sb.place.execute(pid, "AAA", "buy", quantity=4)
    sb.run.execute(at(12, 23))
    sb.clock._now = at(13, 9)  # the views value positions at the clock's "now"
    view, positions = sb.reader.detail(pid)
    assert [str(p.ticker) for p in positions] == ["AAA", "BBB"]
    aaa, bbb = positions
    assert (aaa.position.price, aaa.position.value) == (D("15"), D("60"))
    assert (aaa.pnl, aaa.pnl_pct) == (D("20"), D("50"))  # bought 4 at 10, now worth 15
    assert (bbb.pnl, bbb.pnl_pct) == (D("0"), D("0"))
    assert view.invested == D("80.0000") and view.portfolio.cash == D("940")
    assert view.total == D("1020") and view.return_pct == D("2")


def test_a_position_is_valued_at_cost_while_the_clock_is_before_any_bar(sb):
    sb.prices(AAA, {11: 10.0})
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    sb.place.execute(pid, "AAA", "buy", quantity=4)
    sb.run.execute(at(11, 23))
    _, (aaa,) = sb.reader.detail(pid)  # the clock still says the 10th: no bar at or before it
    assert aaa.position.price is None and aaa.position.value == aaa.position.holding.cost


def test_orders_come_newest_first_with_a_status_filter(sb):
    pid = sb.create.execute("m", mode="manual", cash=100).portfolio.id
    for _ in range(3):
        sb.place.execute(pid, "AAA", "buy", quantity=1)
    sb.cancel.execute(pid, 2)
    assert [o.id for o in sb.reader.orders(pid)] == [3, 2, 1]
    assert [o.id for o in sb.reader.orders(pid, "cancelled")] == [2]
    assert [o.id for o in sb.reader.orders(pid, limit=1)] == [3]


def test_reading_an_unknown_portfolio_is_not_found(sb):
    for read in (sb.reader.detail, sb.reader.history, sb.reader.orders):
        with pytest.raises(NotFound):
            read(999)
