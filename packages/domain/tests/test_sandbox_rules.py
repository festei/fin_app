"""The paper-trading rules. Prices are made up and hand-checkable.

Bars are daily closes at 21:00 UTC. An order decided on day D fills at the first bar on a LATER
day, never at D's own close. ``Market`` stands in for storage: it answers the price requests a
portfolio makes, exactly as the application does from the database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fin_domain.market_data import PriceBar
from fin_domain.sandbox import (
    Order,
    OrderError,
    OrderStatus,
    Portfolio,
    PriceBook,
    StrategyRules,
    day_after,
)
from fin_domain.shared import Ticker
from fin_domain.signals import Signal, StoredSignal

D = Decimal
AAA, BBB, CCC = Ticker("AAA"), Ticker("BBB"), Ticker("CCC")


def at(day: int, hour: int = 15) -> datetime:
    return datetime(2025, 3, day, hour, tzinfo=UTC)


class Market:
    def __init__(self):
        self.closes: dict[Ticker, dict[datetime, float]] = {}

    def prices(self, ticker: Ticker, closes: dict[int, float]) -> None:
        self.closes.setdefault(ticker, {}).update({at(d, 21): c for d, c in closes.items()})

    def book(self, p: Portfolio, as_of: datetime, extra=()) -> PriceBook:
        req = p.price_requests(list(extra))

        def upto(t):
            return sorted((ts, c) for ts, c in self.closes.get(t, {}).items() if ts <= as_of)

        return PriceBook(
            last_closes={t: D(str(upto(t)[-1][1])) for t in req.last_close_for if upto(t)},
            bar_closes={
                (t, ts): D(str(self.closes[t][ts]))
                for t, ts in req.close_at
                if ts in self.closes.get(t, {})
            },
            first_bars={
                (t, start): PriceBar(ts=ts, close=c)
                for t, start in req.first_bar_from
                for ts, c in upto(t)[:0] or [x for x in upto(t) if x[0] >= start][:1]
            },
        )

    def settle(self, p: Portfolio, as_of: datetime) -> list[Order]:
        return p.settle(self.book(p, as_of), as_of)

    def state(self, p: Portfolio, as_of: datetime | None = None):
        held = {
            h.ticker: (h.quantity, h.avg_cost)
            for h in p.holdings(self.book(p, as_of or at(28))).values()
        }
        return p.cash, held


def portfolio(mode="manual", cash=100, **kw) -> Portfolio:
    return Portfolio.open("test", mode=mode, cash=cash, defaults=StrategyRules(), now=at(1), **kw)


@pytest.fixture
def m():
    return Market()


# --- fills -------------------------------------------------------------------------------------


def test_an_order_never_fills_at_the_close_of_its_own_day(m):
    m.prices(AAA, {10: 10.0})
    p = portfolio()
    order = p.place_order(AAA, "buy", at(10), notional=50)
    assert m.settle(p, at(11)) == []  # the 10th's close is not available to an order of the 10th
    assert order.status is OrderStatus.PENDING and p.cash == D(100)


def test_it_fills_at_the_next_daily_close_and_updates_cash_and_position(m):
    m.prices(AAA, {10: 10.0, 11: 12.5})
    p = portfolio()
    order = p.place_order(AAA, "buy", at(10), notional=50)
    assert m.settle(p, at(12)) == [order]
    assert (order.status, order.fill_price, order.fill_quantity) == (
        OrderStatus.FILLED, D("12.5"), D("4"),
    )  # fmt: skip
    assert m.state(p) == (D("50.0000"), {AAA: (D("4"), D("12.5"))})
    assert m.settle(p, at(13)) == []  # a filled order is not filled again


def test_a_bar_after_as_of_is_not_used(m):
    m.prices(AAA, {10: 10.0, 12: 20.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), notional=50)
    assert m.settle(p, at(11)) == []  # the 12th has not happened yet
    assert len(m.settle(p, at(12, 23))) == 1


def test_fractional_shares_round_down_and_fees_are_charged(m):
    m.prices(AAA, {11: 30.0})
    p = portfolio(fee_bps=100)  # 1%
    big = p.place_order(AAA, "buy", at(10), notional=100)
    # 100/30 = 3.333333 shares; cost 99.9999 + 1.00 fee > 100 cash: rejected
    m.settle(p, at(12))
    assert big.status is OrderStatus.REJECTED
    p2 = portfolio(fee_bps=100)
    o2 = p2.place_order(AAA, "buy", at(10), quantity=3)
    m.settle(p2, at(12))
    assert (o2.status, o2.fee) == (OrderStatus.FILLED, D("0.9000"))
    assert p2.cash == D("100") - D("90.0000") - D("0.9000")


def test_insufficient_cash_and_oversold_orders_are_rejected_with_a_reason(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio(cash=50)
    big = p.place_order(AAA, "buy", at(10), quantity=6)
    nothing = p.place_order(AAA, "sell", at(10), quantity=1)
    m.settle(p, at(12))
    assert "insufficient cash" in big.reason and nothing.reason == "nothing to sell"
    assert m.state(p) == (D("50"), {})


def test_selling_part_then_all_of_a_position(m):
    m.prices(AAA, {11: 10.0, 13: 20.0, 15: 25.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), quantity=5)
    m.settle(p, at(11, 23))
    p.place_order(AAA, "sell", at(12), quantity=2)
    m.settle(p, at(13, 23))
    cash, held = m.state(p)
    assert cash == D("50") + D("40") and held[AAA][0] == D("3")
    p.place_order(AAA, "sell", at(14))  # no size: close the whole position
    m.settle(p, at(15, 23))
    assert m.state(p) == (D("90") + D("75"), {})


def test_selling_by_notional_never_sells_more_than_is_held(m):
    m.prices(AAA, {11: 10.0, 13: 10.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), quantity=2)
    m.settle(p, at(11, 23))
    p.place_order(AAA, "sell", at(12), notional=1000)
    m.settle(p, at(13, 23))
    assert m.state(p) == (D("100"), {})


def test_buying_more_averages_the_cost(m):
    m.prices(AAA, {11: 10.0, 13: 20.0})
    p = portfolio(cash=1000)
    p.place_order(AAA, "buy", at(10), quantity=10)
    p.place_order(AAA, "buy", at(12), quantity=10)
    m.settle(p, at(13, 23))
    assert m.state(p)[1][AAA] == (D("20"), D("15"))


def test_pending_orders_can_be_cancelled_only_once(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio()
    p.id = 7
    order = p.place_order(AAA, "buy", at(10), quantity=1)
    order.id = 1
    assert p.cancel_order(1, "changed my mind") is True
    assert p.cancel_order(1, "again") is False
    assert p.cancel_order(999, "x") is False
    assert m.settle(p, at(12)) == []


def test_buy_fees_are_part_of_the_cost_basis(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio(fee_bps=100)
    p.place_order(AAA, "buy", at(10), quantity=5)  # 50 + 0.50 fee
    m.settle(p, at(11, 23))
    assert m.state(p)[1][AAA] == (D("5"), D("10.1"))


def test_a_sale_on_the_same_close_funds_a_purchase(m):
    m.prices(AAA, {11: 10.0, 13: 10.0})
    m.prices(BBB, {13: 10.0})
    p = portfolio(cash=100)
    p.place_order(AAA, "buy", at(10), quantity=10)  # spends it all on the 11th
    m.settle(p, at(11, 23))
    buy = p.place_order(BBB, "buy", at(12), quantity=10)  # needs the sale's cash
    sell = p.place_order(AAA, "sell", at(12))
    done = m.settle(p, at(13, 23))
    assert done == [sell, buy]  # sells go first at the same close
    assert buy.status is OrderStatus.FILLED


# --- corporate actions --------------------------------------------------------------------------


def test_a_two_for_one_split_is_neither_a_loss_nor_a_gain(m):
    m.prices(AAA, {11: 100.0})
    p = portfolio(cash=1000)
    p.place_order(AAA, "buy", at(10), quantity=1)
    m.settle(p, at(11, 23))
    assert m.state(p)[1][AAA] == (D("1"), D("100"))
    # 2:1 split: the source re-adjusts the old bar to 50 and the market trades at 55
    m.prices(AAA, {11: 50.0, 12: 55.0})
    assert m.state(p)[1][AAA] == (D("2"), D("50"))  # two shares at an average cost of 50
    assert p.positions_value(m.book(p, at(12, 23))) == D("110.0000")  # 2 x 55, a gain of 10


def test_a_reverse_split_and_selling_after_a_split(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio(cash=1000)
    p.place_order(AAA, "buy", at(10), quantity=10)
    m.settle(p, at(11, 23))
    m.prices(AAA, {11: 50.0, 12: 50.0})  # 1:5 reverse split
    assert m.state(p)[1][AAA] == (D("2"), D("50"))
    p.place_order(AAA, "sell", at(11))  # sell it all after the split
    m.settle(p, at(12, 23))
    cash, held = m.state(p)
    assert held == {} and cash == D("900") + D("100")  # 2 shares x 50, no phantom gain or loss


def test_a_partial_sale_after_a_split_keeps_the_right_remainder(m):
    m.prices(AAA, {11: 100.0})
    p = portfolio(cash=1000)
    p.place_order(AAA, "buy", at(10), quantity=4)
    m.settle(p, at(11, 23))
    m.prices(AAA, {11: 50.0, 12: 50.0})
    p.place_order(AAA, "sell", at(11), quantity=3)  # of the 8 post-split shares
    m.settle(p, at(12, 23))
    assert m.state(p)[1][AAA] == (D("5"), D("50"))


# --- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(side="buy"),  # no size
        dict(side="buy", quantity=1, notional=5),  # both
        dict(side="buy", quantity=0),
        dict(side="sell", notional=-1),
        dict(side="hold", quantity=1),
    ],
)
def test_invalid_orders_are_refused(kwargs):
    with pytest.raises(OrderError):
        portfolio().place_order(AAA, now=at(10), **kwargs)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 10**15, 0])
def test_non_finite_and_oversized_amounts_are_refused(bad):
    with pytest.raises(OrderError):
        portfolio().place_order(AAA, "buy", at(10), quantity=bad)
    with pytest.raises(OrderError):
        portfolio(cash=bad)


def test_a_strategy_portfolio_takes_no_manual_orders():
    with pytest.raises(OrderError, match="trades from signals"):
        portfolio("strategy").place_order(AAA, "buy", at(10), quantity=1)


def test_portfolios_are_validated_when_opened():
    with pytest.raises(OrderError):
        portfolio(cash=0)
    with pytest.raises(OrderError, match="mode must be one of"):
        portfolio("nope", cash=5)
    with pytest.raises(OrderError, match="invalid strategy"):
        portfolio("strategy", cash=5, strategy={"bogus": 1})
    with pytest.raises(OrderError, match="name must not be empty"):
        Portfolio.open("  ", mode="manual", cash=5, defaults=StrategyRules(), now=at(1))
    with pytest.raises(OrderError, match="fee_bps"):
        portfolio(fee_bps=5000)


def test_a_new_strategy_portfolio_starts_from_the_defaults_plus_its_overrides():
    p = portfolio("strategy", strategy={"max_positions": 2, "position_pct": 0.6})
    assert p.strategy == StrategyRules(position_pct=0.6, max_positions=2)
    assert portfolio("manual").strategy is None


@pytest.mark.parametrize("kinds", [["sell"], "buy", [], ["buy", "x"], 5])
def test_config_rejects_bad_buy_kinds(kinds):
    with pytest.raises(ValueError):
        StrategyRules.from_dict({"buy_kinds": kinds})


def test_unknown_strategy_fields_are_rejected_and_the_json_shape_is_stable():
    with pytest.raises(ValueError, match="unknown sandbox fields"):
        StrategyRules.from_dict({"colour": "red"})
    assert StrategyRules().to_json() == {
        "position_pct": 0.2, "max_positions": 5, "min_cash_pct": 0.05,
        "buy_kinds": ["buy", "emerging"], "fee_bps": 0.0,
    }  # fmt: skip


def test_the_day_after_is_midnight_utc():
    assert day_after(at(10, 23)) == datetime(2025, 3, 11, tzinfo=UTC)


# --- strategy -----------------------------------------------------------------------------------

_ids = iter(range(1, 1000))


def sig(ticker, kind, day) -> StoredSignal:
    return StoredSignal(
        ticker, Signal(str(ticker), kind, 80.0, at(day, 21), ""), score_id=1, id=next(_ids)
    )


def strategy(m, p, signals, as_of):
    book = m.book(p, as_of, extra=[s.ticker for s in signals])
    return p.apply_signals(signals, book)


def test_a_strategy_buys_a_share_of_the_portfolio_and_sells_on_a_sell_signal(m):
    m.prices(AAA, {10: 10.0, 11: 10.0, 12: 12.0, 13: 12.0})
    p = portfolio("strategy")  # 100 cash, 20% per position
    buy = sig(AAA, "buy", 10)
    created = strategy(m, p, [buy], at(10, 22))
    assert [(o.side.value, o.notional, o.status.value, o.signal_id) for o in created] == [
        ("buy", D("20.0000"), "pending", buy.id)
    ]
    assert p.last_signal_id == buy.id
    m.settle(p, at(11, 23))
    cash, held = m.state(p)
    assert cash == D("80.0000") and held[AAA][0] == D("2")
    sell = sig(AAA, "sell", 12)
    strategy(m, p, [sell], at(12, 22))
    m.settle(p, at(13, 23))
    assert m.state(p) == (D("80") + D("24"), {})  # sold 2 at 12
    assert [o.side.value for o in p.orders] == ["buy", "sell"] and p.orders[1].signal_id == sell.id


def test_a_manual_portfolio_ignores_signals_and_does_not_advance(m):
    m.prices(AAA, {10: 10.0})
    p = portfolio("manual")
    assert strategy(m, p, [sig(AAA, "buy", 10)], at(10, 22)) == [] and p.last_signal_id == 0


def test_a_strategy_respects_max_positions_cash_reserve_and_duplicates(m):
    for t in (AAA, BBB, CCC):
        m.prices(t, {10: 10.0})
    p = portfolio("strategy", strategy={"max_positions": 2, "position_pct": 0.6})
    signals = [sig(t, "buy", 10) for t in (AAA, BBB, CCC)] + [sig(AAA, "buy", 10)]  # a repeat
    strategy(m, p, signals, at(10, 22))
    # 60% of 100, then only (100 - 60 - 5% reserve) = 35 is left; the third stock has no slot
    assert [o.notional for o in p.orders] == [D("60.0000"), D("35.0000")]


def test_a_sell_signal_cancels_a_pending_buy(m):
    m.prices(AAA, {10: 10.0})
    p = portfolio("strategy")
    strategy(m, p, [sig(AAA, "buy", 10), sig(AAA, "sell", 10)], at(10, 22))
    assert [o.status.value for o in p.orders] == ["cancelled"]
    assert p.orders[0].reason == "superseded by a sell signal"


def test_emerging_signals_open_positions_and_unpriced_stocks_are_skipped(m):
    m.prices(AAA, {10: 10.0})  # BBB has no price at all
    p = portfolio("strategy")
    strategy(m, p, [sig(BBB, "emerging", 10), sig(AAA, "emerging", 10)], at(10, 22))
    assert [(o.ticker, o.status.value) for o in p.orders] == [(AAA, "pending")]
    assert p.last_signal_id  # the skipped signal was still looked at, once


def test_a_buy_kind_can_be_switched_off(m):
    m.prices(AAA, {10: 10.0})
    p = portfolio("strategy", strategy={"buy_kinds": ["buy"]})
    assert strategy(m, p, [sig(AAA, "emerging", 10)], at(10, 22)) == []


# --- snapshots ----------------------------------------------------------------------------------


def test_snapshots_value_cash_plus_positions(m):
    m.prices(AAA, {11: 10.0, 12: 15.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), quantity=4)
    m.settle(p, at(11, 23))
    snap = p.snapshot(m.book(p, at(12, 22)), at(12, 22))
    assert (snap.as_of.isoformat(), snap.cash, snap.positions_value, snap.total_value) == (
        "2025-03-12", D("60"), D("60"), D("120"),
    )  # fmt: skip
    again = p.snapshot(m.book(p, at(12, 23)), at(12, 23))
    assert again.as_of == snap.as_of  # the same day, so a stored row would be overwritten


def test_a_position_without_a_price_is_valued_at_cost(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), quantity=4)
    m.settle(p, at(11, 23))
    empty_book = PriceBook()
    (pos,) = p.valued_positions(empty_book)
    assert pos.price is None and pos.value == pos.holding.cost == D("40.0000")


def test_the_price_requests_name_exactly_what_each_step_needs(m):
    m.prices(AAA, {11: 10.0})
    p = portfolio()
    p.place_order(AAA, "buy", at(10), quantity=1)
    req = p.price_requests(extra_tickers=[BBB])
    assert req.first_bar_from == {(AAA, day_after(at(10)))} and req.close_at == set()
    assert req.last_close_for == {BBB}  # nothing filled yet
    m.settle(p, at(11, 23))
    req = p.price_requests()
    assert req.close_at == {(AAA, at(11, 21))} and req.last_close_for == {AAA}
    assert req.first_bar_from == set()
