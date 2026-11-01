"""The paper-trading engine. Prices are made up and hand-checkable.

Bars are daily closes (conftest ``bar`` puts them at 21:00 UTC). An order decided on day D fills
at the first bar on a LATER day, never at D's own close.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from conftest import bar
from sqlalchemy import select

from fin_core.db import migrate, repo, signals_repo
from fin_core.db.models import (
    Portfolio,
    PortfolioSnapshot,
    PriceBar,
    SandboxOrder,
    Security,
)
from fin_core.db.session import make_engine, session_scope
from fin_core.sandbox import engine as sb
from fin_core.sandbox.config import SandboxConfig
from fin_core.sandbox.run import run_sandbox

D = Decimal


def at(day: int, hour: int = 15) -> datetime:
    return datetime(2025, 3, day, hour, tzinfo=UTC)


@pytest.fixture
def db(raw_engine):
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}])
    return raw_engine


def prices(engine, ticker, closes: dict[int, float]):
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        repo.upsert_prices(s, sid, [bar(date(2025, 3, d), c) for d, c in closes.items()])


def portfolio(engine, **kw):
    kw = {"mode": "manual", "cash": 100, **kw}
    with session_scope(engine) as s:
        return sb.create_portfolio(s, "test", defaults=SandboxConfig(), **kw).id


def order(engine, pid, ticker, side, now, **kw):
    with session_scope(engine) as s:
        return sb.place_order(s, pid, ticker, side, now=now, **kw).id


def settle(engine, pid, as_of):
    with session_scope(engine) as s:
        return [o.id for o in sb.settle(s, s.get(Portfolio, pid), as_of)]


def state(engine, pid):
    with session_scope(engine) as s:
        p = s.get(Portfolio, pid)
        held = {
            s.get(Security, h.security_id).ticker: (h.quantity, h.avg_cost)
            for h in sb.holdings(s, pid).values()
        }
        return p.cash, held


# --- fills -------------------------------------------------------------------------------------


def test_an_order_never_fills_at_the_close_of_its_own_day(db):
    prices(db, "AAA", {10: 10.0})
    pid = portfolio(db)
    oid = order(db, pid, "AAA", "buy", at(10), notional=50)
    assert (
        settle(db, pid, at(11)) == []
    )  # the 10th's close is not available to an order of the 10th
    with session_scope(db) as s:
        assert s.get(SandboxOrder, oid).status == "pending"
    assert state(db, pid)[0] == D(100)


def test_it_fills_at_the_next_daily_close_and_updates_cash_and_position(db):
    prices(db, "AAA", {10: 10.0, 11: 12.5})
    pid = portfolio(db)
    oid = order(db, pid, "AAA", "buy", at(10), notional=50)
    assert settle(db, pid, at(12)) == [oid]
    with session_scope(db) as s:
        o = s.get(SandboxOrder, oid)
        assert (o.status, o.fill_price, o.fill_quantity) == ("filled", D("12.5"), D("4"))
    cash, held = state(db, pid)
    assert cash == D("50.0000") and held == {"AAA": (D("4"), D("12.5"))}
    assert settle(db, pid, at(13)) == []  # a filled order is not filled again


def test_a_bar_after_as_of_is_not_used(db):
    prices(db, "AAA", {10: 10.0, 12: 20.0})
    pid = portfolio(db)
    order(db, pid, "AAA", "buy", at(10), notional=50)
    assert settle(db, pid, at(11)) == []  # the 12th has not happened yet
    assert len(settle(db, pid, at(12, 23))) == 1


def test_fractional_shares_round_down_and_fees_are_charged(db):
    prices(db, "AAA", {11: 30.0})
    pid = portfolio(db, fee_bps=100)  # 1%
    oid = order(db, pid, "AAA", "buy", at(10), notional=100)
    # 100/30 = 3.333333 shares; cost 99.9999 + 1.00 fee > 100 cash: rejected
    settle(db, pid, at(12))
    with session_scope(db) as s:
        assert s.get(SandboxOrder, oid).status == "rejected"
    pid2 = portfolio(db, fee_bps=100)
    oid2 = order(db, pid2, "AAA", "buy", at(10), quantity=3)
    settle(db, pid2, at(12))
    with session_scope(db) as s:
        o = s.get(SandboxOrder, oid2)
        assert (o.status, o.fee) == ("filled", D("0.9000"))
    assert state(db, pid2)[0] == D("100") - D("90.0000") - D("0.9000")


def test_insufficient_cash_and_oversold_orders_are_rejected_with_a_reason(db):
    prices(db, "AAA", {11: 10.0})
    pid = portfolio(db, cash=50)
    big = order(db, pid, "AAA", "buy", at(10), quantity=6)
    nothing = order(db, pid, "AAA", "sell", at(10), quantity=1)
    settle(db, pid, at(12))
    with session_scope(db) as s:
        assert "insufficient cash" in s.get(SandboxOrder, big).reason
        assert s.get(SandboxOrder, nothing).reason == "nothing to sell"
    assert state(db, pid) == (D("50"), {})


def test_selling_part_then_all_of_a_position(db):
    prices(db, "AAA", {11: 10.0, 13: 20.0, 15: 25.0})
    pid = portfolio(db)
    order(db, pid, "AAA", "buy", at(10), quantity=5)
    settle(db, pid, at(11, 23))
    order(db, pid, "AAA", "sell", at(12), quantity=2)
    settle(db, pid, at(13, 23))
    cash, held = state(db, pid)
    assert cash == D("50") + D("40") and held["AAA"][0] == D("3")
    order(db, pid, "AAA", "sell", at(14))  # no size: close the whole position
    settle(db, pid, at(15, 23))
    assert state(db, pid) == (D("90") + D("75"), {})


def test_buying_more_averages_the_cost(db):
    prices(db, "AAA", {11: 10.0, 13: 20.0})
    pid = portfolio(db, cash=1000)
    order(db, pid, "AAA", "buy", at(10), quantity=10)
    order(db, pid, "AAA", "buy", at(12), quantity=10)
    settle(db, pid, at(13, 23))
    assert state(db, pid)[1]["AAA"] == (D("20"), D("15"))


def test_pending_orders_can_be_cancelled_only_once(db):
    prices(db, "AAA", {11: 10.0})
    pid = portfolio(db)
    oid = order(db, pid, "AAA", "buy", at(10), quantity=1)
    with session_scope(db) as s:
        assert sb.cancel_order(s, pid, oid, "changed my mind") is True
        assert sb.cancel_order(s, pid, oid, "again") is False
        assert sb.cancel_order(s, pid + 99, oid, "x") is False
    assert settle(db, pid, at(12)) == []


# --- validation --------------------------------------------------------------------------------


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
def test_invalid_orders_are_refused(db, kwargs):
    pid = portfolio(db)
    with session_scope(db) as s, pytest.raises(sb.OrderError):
        sb.place_order(s, pid, "AAA", **kwargs)


def test_unknown_targets_and_wrong_modes_are_refused(db):
    manual, strategy = portfolio(db), portfolio(db, mode="strategy")
    with session_scope(db) as s:
        with pytest.raises(sb.UnknownError):
            sb.place_order(s, manual, "ZZZ", "buy", quantity=1)
        with pytest.raises(sb.UnknownError):
            sb.place_order(s, 999, "AAA", "buy", quantity=1)
        with pytest.raises(sb.OrderError, match="trades from signals"):
            sb.place_order(s, strategy, "AAA", "buy", quantity=1)
        with pytest.raises(sb.OrderError):
            sb.create_portfolio(s, "x", mode="manual", cash=0, defaults=SandboxConfig())
        with pytest.raises(sb.OrderError):
            sb.create_portfolio(s, "x", mode="nope", cash=5, defaults=SandboxConfig())
        with pytest.raises(sb.OrderError, match="invalid strategy"):
            sb.create_portfolio(
                s, "x", mode="strategy", cash=5, strategy={"bogus": 1}, defaults=SandboxConfig()
            )


# --- strategy ----------------------------------------------------------------------------------


def signal(engine, ticker, kind, day):
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        score = signals_repo.insert_score(
            s, security_id=sid, as_of=at(day, 21), conviction=80.0, all_abstained=False,
            signal_blocked=False, explanation="", details={},
        )  # fmt: skip
        return signals_repo.insert_signal(
            s, security_id=sid, score_id=score.id, kind=kind, conviction=80.0, as_of=at(day, 21),
            reason="",
        ).id  # fmt: skip


def run_strategy(engine, pid, as_of):
    with session_scope(engine) as s:
        return [o.id for o in sb.run_strategy(s, s.get(Portfolio, pid), as_of)]


def orders(engine, pid):
    with session_scope(engine) as s:
        rows = s.scalars(select(SandboxOrder).where(SandboxOrder.portfolio_id == pid))
        return [(o.side, o.notional, o.status, o.signal_id) for o in rows.all()]


def test_a_strategy_ignores_signals_from_before_it_existed(db):
    prices(db, "AAA", {10: 10.0})
    signal(db, "AAA", "buy", 10)
    pid = portfolio(db, mode="strategy")
    assert run_strategy(db, pid, at(10, 22)) == []


def test_a_strategy_buys_a_share_of_the_portfolio_and_sells_on_a_sell_signal(db):
    prices(db, "AAA", {10: 10.0, 11: 10.0, 12: 12.0, 13: 12.0})
    pid = portfolio(db, mode="strategy")  # 100 cash, 20% per position
    buy = signal(db, "AAA", "buy", 10)
    assert len(run_strategy(db, pid, at(10, 22))) == 1
    assert orders(db, pid) == [("buy", D("20.0000"), "pending", buy)]
    assert run_strategy(db, pid, at(10, 23)) == []  # each signal is looked at once
    settle(db, pid, at(11, 23))
    cash, held = state(db, pid)
    assert cash == D("80.0000") and held["AAA"][0] == D("2")

    sell = signal(db, "AAA", "sell", 12)
    run_strategy(db, pid, at(12, 22))
    settle(db, pid, at(13, 23))
    assert state(db, pid) == (D("80") + D("24"), {})  # sold 2 at 12
    assert [o[0] for o in orders(db, pid)] == ["buy", "sell"] and orders(db, pid)[1][3] == sell


def test_a_strategy_respects_max_positions_cash_reserve_and_duplicates(db):
    for t in ("AAA", "BBB", "CCC"):
        prices(db, t, {10: 10.0})
    pid = portfolio(db, mode="strategy", strategy={"max_positions": 2, "position_pct": 0.6})
    for t in ("AAA", "BBB", "CCC"):
        signal(db, t, "buy", 10)
    signal(db, "AAA", "buy", 10)  # a repeat for a stock already pending
    run_strategy(db, pid, at(10, 22))
    got = orders(db, pid)
    # 60% of 100, then only (100 - 60 - 5% reserve) = 35 is left; the third stock has no slot
    assert [o[1] for o in got] == [D("60.0000"), D("35.0000")]


def test_a_sell_signal_cancels_a_pending_buy(db):
    prices(db, "AAA", {10: 10.0})
    pid = portfolio(db, mode="strategy")
    signal(db, "AAA", "buy", 10)
    signal(db, "AAA", "sell", 10)
    run_strategy(db, pid, at(10, 22))
    assert [o[2] for o in orders(db, pid)] == ["cancelled"]


def test_emerging_signals_open_positions_and_unpriced_stocks_are_skipped(db):
    prices(db, "AAA", {10: 10.0})  # BBB has no price at all
    pid = portfolio(db, mode="strategy")
    signal(db, "BBB", "emerging", 10)
    signal(db, "AAA", "emerging", 10)
    run_strategy(db, pid, at(10, 22))
    assert [(o[0], o[2]) for o in orders(db, pid)] == [("buy", "pending")]


# --- snapshots and the scheduled step ----------------------------------------------------------


def test_snapshots_value_cash_plus_positions_and_are_idempotent_per_day(db):
    prices(db, "AAA", {11: 10.0, 12: 15.0})
    pid = portfolio(db)
    order(db, pid, "AAA", "buy", at(10), quantity=4)
    settle(db, pid, at(11, 23))
    with session_scope(db) as s:
        p = s.get(Portfolio, pid)
        first = sb.snapshot(s, p, at(12, 22))
        assert (first.cash, first.positions_value, first.total_value) == (
            D("60"), D("60"), D("120"),
        )  # fmt: skip
        sb.snapshot(s, p, at(12, 23))
    with session_scope(db) as s:
        assert len(s.scalars(select(PortfolioSnapshot)).all()) == 1


def test_run_sandbox_processes_every_portfolio_and_isolates_failures(db, monkeypatch):
    prices(db, "AAA", {10: 10.0, 11: 10.0})
    good, broken = portfolio(db), portfolio(db)
    order(db, good, "AAA", "buy", at(10), notional=40)
    real = sb.snapshot

    def flaky(session, p, as_of):
        if p.id == broken:
            raise RuntimeError("boom")
        return real(session, p, as_of)

    monkeypatch.setattr(sb, "snapshot", flaky)
    summary = run_sandbox(db, at(11, 23))
    assert (summary.status, summary.rows) == ("partial", 1)
    assert f"portfolio {broken}" in summary.failed
    assert state(db, good)[0] == D("60.0000")  # the good portfolio was still processed
    monkeypatch.setattr(sb, "snapshot", real)
    assert run_sandbox(db, at(11, 23)).status == "ok"


def test_an_empty_sandbox_is_an_ok_no_op(db):
    assert run_sandbox(db, at(11)).status == "ok"


def test_migration_0004_round_trips(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrate.upgrade(url)
    migrate.downgrade(url, "0003")
    migrate.upgrade(url)
    make_engine(url).dispose()


# --- corporate actions, ordering, locking ------------------------------------------------------


def restate(engine, ticker, closes: dict[int, float]):
    """Overwrite stored closes, as the price source does when it re-adjusts history."""
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        for d, c in closes.items():
            row = s.get(PriceBar, (sid, bar(date(2025, 3, d), c)["ts"]))
            row.close = c


def test_a_two_for_one_split_is_neither_a_loss_nor_a_gain(db):
    prices(db, "AAA", {11: 100.0})
    pid = portfolio(db, cash=1000)
    order(db, pid, "AAA", "buy", at(10), quantity=1)
    settle(db, pid, at(11, 23))
    assert state(db, pid)[1]["AAA"] == (D("1"), D("100"))
    # 2:1 split: the source re-adjusts the old bar to 50 and the market trades at 55
    restate(db, "AAA", {11: 50.0})
    prices(db, "AAA", {12: 55.0})
    cash, held = state(db, pid)
    assert held["AAA"] == (D("2"), D("50"))  # two shares at an average cost of 50
    with session_scope(db) as s:
        p = s.get(Portfolio, pid)
        assert sb.positions_value(s, p, at(12, 23)) == D("110.0000")  # 2 x 55, a gain of 10


def test_a_reverse_split_and_selling_after_a_split(db):
    prices(db, "AAA", {11: 10.0})
    pid = portfolio(db, cash=1000)
    order(db, pid, "AAA", "buy", at(10), quantity=10)
    settle(db, pid, at(11, 23))
    restate(db, "AAA", {11: 50.0})  # 1:5 reverse split
    prices(db, "AAA", {12: 50.0})
    assert state(db, pid)[1]["AAA"] == (D("2"), D("50"))
    order(db, pid, "AAA", "sell", at(11))  # sell it all after the split
    settle(db, pid, at(12, 23))
    cash, held = state(db, pid)
    assert held == {} and cash == D("900") + D("100")  # 2 shares x 50, no phantom gain or loss


def test_a_partial_sale_after_a_split_keeps_the_right_remainder(db):
    prices(db, "AAA", {11: 100.0})
    pid = portfolio(db, cash=1000)
    order(db, pid, "AAA", "buy", at(10), quantity=4)
    settle(db, pid, at(11, 23))
    restate(db, "AAA", {11: 50.0})
    prices(db, "AAA", {12: 50.0})
    order(db, pid, "AAA", "sell", at(11), quantity=3)  # of the 8 post-split shares
    settle(db, pid, at(12, 23))
    assert state(db, pid)[1]["AAA"] == (D("5"), D("50"))


def test_buy_fees_are_part_of_the_cost_basis(db):
    prices(db, "AAA", {11: 10.0})
    pid = portfolio(db, fee_bps=100)
    order(db, pid, "AAA", "buy", at(10), quantity=5)  # 50 + 0.50 fee
    settle(db, pid, at(11, 23))
    assert state(db, pid)[1]["AAA"] == (D("5"), D("10.1"))


def test_a_sale_on_the_same_close_funds_a_purchase(db):
    prices(db, "AAA", {11: 10.0, 13: 10.0})
    prices(db, "BBB", {13: 10.0})
    pid = portfolio(db, cash=100)
    order(db, pid, "AAA", "buy", at(10), quantity=10)  # spends it all on the 11th
    settle(db, pid, at(11, 23))
    buy = order(db, pid, "BBB", "buy", at(12), quantity=10)  # needs the sale's cash
    sell = order(db, pid, "AAA", "sell", at(12))
    done = settle(db, pid, at(13, 23))
    assert done == [sell, buy]  # sells go first at the same close
    with session_scope(db) as s:
        assert s.get(SandboxOrder, buy).status == "filled"


def test_overlapping_runs_do_not_duplicate_strategy_orders(db):
    if db.dialect.name != "postgresql":
        pytest.skip("needs real row locks")
    import threading

    prices(db, "AAA", {10: 10.0})
    pid = portfolio(db, mode="strategy")
    signal(db, "AAA", "buy", 10)
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        run_sandbox(db, at(10, 22))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(orders(db, pid)) == 1


def test_locking_an_unknown_portfolio_fails_cleanly(db):
    with session_scope(db) as s, pytest.raises(sb.UnknownError):
        sb.lock_portfolio(s, 999)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 10**15, 0])
def test_non_finite_and_oversized_amounts_are_refused_by_the_engine(db, bad):
    pid = portfolio(db)
    with session_scope(db) as s, pytest.raises(sb.OrderError):
        sb.place_order(s, pid, "AAA", "buy", quantity=bad)
    with session_scope(db) as s, pytest.raises(sb.OrderError):
        sb.create_portfolio(s, "x", mode="manual", cash=bad, defaults=SandboxConfig())


@pytest.mark.parametrize("kinds", [["sell"], "buy", [], ["buy", "x"], 5])
def test_config_rejects_bad_buy_kinds(kinds):
    with pytest.raises(ValueError):
        SandboxConfig.from_dict({"buy_kinds": kinds})
