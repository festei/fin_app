"""The paper-trading engine: orders, fills, strategy, valuation, snapshots.

Holdings are not stored: they are derived from the filled orders and restated to today's share
basis. Prices are the source's split/dividend-adjusted prices, and the source restates old bars
when a split happens, so each fill is scaled by (price it filled at / what that bar's close is
now). After a 2:1 split a holding of 1 share bought at 100 becomes 2 shares at an average cost of
50, so a split is neither a loss nor a gain (and dividends show up as slightly more shares, like
reinvested dividends).

Fill model (no look-ahead, honest for daily data): an order decided on day D fills at the close of
the first stored daily bar on a day after D, at the source's adjusted price. Until such a bar
exists it stays ``pending``. Cash and holdings are checked at fill time; an order that cannot be
afforded or covered is ``rejected`` with a reason. Amounts are Decimals, shares may be fractional.

All functions work inside the caller's session (one transaction per portfolio in ``run``).
Not investment advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from fin_core.db import repo
from fin_core.db.models import (
    Portfolio,
    PortfolioSnapshot,
    PriceBar,
    SandboxOrder,
    Security,
    SignalRecord,
)
from fin_core.sandbox.config import SandboxConfig

Q6 = Decimal("0.000001")
Q4 = Decimal("0.0001")
MIN_NOTIONAL = Decimal("0.01")
MAX_QUANTITY = Decimal(10) ** 9  # far below the Numeric(18, 6) column limit
MAX_NOTIONAL = Decimal(10) ** 12
MAX_CASH = Decimal(10) ** 12
MODES = ("manual", "strategy")


class OrderError(ValueError):
    """An invalid request (the API maps it to 422/404)."""


class UnknownError(OrderError):
    """A referenced portfolio, order or security does not exist."""


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _dec(x: Any) -> Decimal:
    try:
        d = x if isinstance(x, Decimal) else Decimal(str(x))
    except ArithmeticError:
        raise OrderError(f"not a number: {x!r}") from None
    if not d.is_finite():
        raise OrderError(f"not a finite number: {x!r}")
    return d


def _day_after(dt: datetime) -> datetime:
    """Midnight UTC of the day after ``dt``: bars on or after this fill an order decided at dt."""
    d = _utc(dt).astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(d, time.min, tzinfo=UTC)


# --- portfolios and orders ---------------------------------------------------------------------


def create_portfolio(
    session: Session,
    name: str,
    *,
    mode: str,
    cash: Decimal | float | int | str,
    fee_bps: float | None = None,
    strategy: dict[str, Any] | None = None,
    defaults: SandboxConfig | None = None,
) -> Portfolio:
    if mode not in MODES:
        raise OrderError(f"mode must be one of {MODES}")
    cash = _dec(cash)
    if not 0 < cash <= MAX_CASH:
        raise OrderError(f"starting cash must be in (0, {MAX_CASH}]")
    if not name.strip():
        raise OrderError("name must not be empty")
    defaults = defaults or SandboxConfig.load()
    config = None
    if mode == "strategy":
        merged = {**defaults.to_json(), **(strategy or {})}
        try:
            config = SandboxConfig.from_dict(merged).to_json()
        except (ValueError, TypeError) as exc:
            raise OrderError(f"invalid strategy configuration: {exc}") from None
    fee = _dec(defaults.fee_bps if fee_bps is None else fee_bps)
    if not 0 <= fee <= 1000:
        raise OrderError("fee_bps must be between 0 and 1000")
    last_signal = session.scalar(select(SignalRecord.id).order_by(SignalRecord.id.desc()).limit(1))
    portfolio = Portfolio(
        name=name.strip(),
        mode=mode,
        initial_cash=cash,
        cash=cash,
        fee_bps=fee,
        strategy_config=config,
        last_signal_id=last_signal or 0,  # a strategy never trades history
    )
    session.add(portfolio)
    session.flush()
    return portfolio


def lock_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    """Load a portfolio holding a row lock for the rest of the transaction (``FOR UPDATE`` on
    Postgres, a no-op on SQLite). Concurrent runs of the step queue up here, so the second one
    sees the first one's ``last_signal_id`` and orders instead of duplicating them."""
    portfolio = session.scalar(
        select(Portfolio)
        .where(Portfolio.id == portfolio_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if portfolio is None:
        raise UnknownError(f"unknown portfolio {portfolio_id}")
    return portfolio


def _add_order(
    session: Session,
    portfolio: Portfolio,
    security: Security,
    side: str,
    created_at: datetime,
    *,
    quantity: Decimal | None = None,
    notional: Decimal | None = None,
    signal_id: int | None = None,
) -> SandboxOrder:
    order = SandboxOrder(
        portfolio_id=portfolio.id,
        security_id=security.id,
        side=side,
        quantity=quantity,
        notional=notional,
        status="pending",
        created_at=created_at,
        signal_id=signal_id,
    )
    session.add(order)
    session.flush()
    return order


def place_order(
    session: Session,
    portfolio_id: int,
    ticker: str,
    side: str,
    *,
    quantity: Decimal | float | str | None = None,
    notional: Decimal | float | str | None = None,
    now: datetime | None = None,
) -> SandboxOrder:
    """A user's order. Only ``manual`` portfolios accept these; strategy portfolios trade from
    signals."""
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise UnknownError(f"unknown portfolio {portfolio_id}")
    if portfolio.mode != "manual":
        raise OrderError("this portfolio trades from signals; manual orders need a manual one")
    security = repo.get_security(session, ticker)
    if security is None or not security.active:
        raise UnknownError(f"unknown ticker {ticker.upper()}")
    if side not in ("buy", "sell"):
        raise OrderError("side must be 'buy' or 'sell'")
    qty = _dec(quantity) if quantity is not None else None
    amount = _dec(notional) if notional is not None else None
    if (qty is not None and not 0 < qty <= MAX_QUANTITY) or (
        amount is not None and not 0 < amount <= MAX_NOTIONAL
    ):
        raise OrderError(
            f"quantity must be in (0, {MAX_QUANTITY}] and notional in (0, {MAX_NOTIONAL}]"
        )
    if qty is not None and amount is not None:
        raise OrderError("give a quantity or a notional amount, not both")
    if side == "buy" and qty is None and amount is None:
        raise OrderError("a buy needs a quantity or a notional amount")
    return _add_order(
        session,
        portfolio,
        security,
        side,
        now or datetime.now(UTC),
        quantity=qty.quantize(Q6, ROUND_DOWN) if qty is not None else None,
        notional=amount.quantize(Q4, ROUND_DOWN) if amount is not None else None,
    )


def cancel_order(session: Session, portfolio_id: int, order_id: int, reason: str) -> bool:
    """Cancel a pending order. False if it is unknown or no longer pending."""
    order = session.get(SandboxOrder, order_id)
    if order is None or order.portfolio_id != portfolio_id or order.status != "pending":
        return False
    order.status, order.reason = "cancelled", reason[:200]
    session.flush()
    return True


# --- holdings and valuation --------------------------------------------------------------------


@dataclass(frozen=True)
class Holding:
    security_id: int
    quantity: Decimal  # in today's share basis
    cost: Decimal  # total spent on what is still held, buy fees included
    avg_cost: Decimal  # per share of today's basis


DUST = Decimal("0.000001")


def holdings(session: Session, portfolio_id: int) -> dict[int, Holding]:
    """What the portfolio holds now, derived from its filled orders (average-cost accounting).

    Every fill is restated to today's share basis by ``fill price / current stored close of the
    bar it filled at``: the price source re-adjusts history after a split (or dividend), so that
    ratio is the cumulative adjustment since the fill. With no corporate action it is exactly 1.
    """
    rows = session.execute(
        select(SandboxOrder, PriceBar.close)
        .outerjoin(
            PriceBar,
            and_(
                PriceBar.security_id == SandboxOrder.security_id,
                PriceBar.ts == SandboxOrder.filled_at,
            ),
        )
        .where(SandboxOrder.portfolio_id == portfolio_id, SandboxOrder.status == "filled")
        .order_by(SandboxOrder.filled_at, SandboxOrder.id)
    ).all()
    state: dict[int, list[Decimal]] = {}  # security -> [quantity, cost]
    for order, current_close in rows:
        price, filled = _dec(order.fill_price), _dec(order.fill_quantity)
        factor = price / _dec(current_close) if current_close else Decimal(1)
        qty = filled * factor
        held = state.setdefault(order.security_id, [Decimal(0), Decimal(0)])
        if order.side == "buy":
            held[0] += qty
            held[1] += filled * price + _dec(order.fee or 0)
        else:
            qty = min(qty, held[0])
            if held[0] > 0:
                held[1] -= held[1] * qty / held[0]
            held[0] -= qty
    out: dict[int, Holding] = {}
    for security_id, (qty, cost) in state.items():
        if qty > DUST:
            out[security_id] = Holding(
                security_id, qty.quantize(Q6), cost.quantize(Q4), (cost / qty).quantize(Q6)
            )
    return out


def last_close(session: Session, security_id: int, as_of: datetime) -> Decimal | None:
    bar = repo.latest_price(session, security_id, as_of)
    return _dec(bar.close) if bar is not None else None


def positions_value(session: Session, portfolio: Portfolio, as_of: datetime) -> Decimal:
    """Holdings at the latest close at or before ``as_of`` (cost when a security has no bar)."""
    total = Decimal(0)
    for h in holdings(session, portfolio.id).values():
        price = last_close(session, h.security_id, as_of)
        total += h.quantity * price if price is not None else h.cost
    return total.quantize(Q4)


# --- settling orders ---------------------------------------------------------------------------


def _fill_bar(session: Session, order: SandboxOrder, as_of: datetime) -> PriceBar | None:
    return session.scalar(
        select(PriceBar)
        .where(
            PriceBar.security_id == order.security_id,
            PriceBar.ts >= _day_after(order.created_at),
            PriceBar.ts <= _utc(as_of),
        )
        .order_by(PriceBar.ts)
        .limit(1)
    )


def _reject(order: SandboxOrder, reason: str) -> None:
    order.status, order.reason = "rejected", reason[:200]


def _fill(
    session: Session, portfolio: Portfolio, order: SandboxOrder, price: Decimal, ts: datetime
) -> None:
    fee_rate = _dec(portfolio.fee_bps) / Decimal(10000)
    held_row = holdings(session, portfolio.id).get(order.security_id)
    held = held_row.quantity if held_row else Decimal(0)
    if order.side == "buy":
        qty = (
            _dec(order.quantity)
            if order.quantity is not None
            else (_dec(order.notional) / price).quantize(Q6, ROUND_DOWN)
        )
        if qty <= 0:
            return _reject(order, "amount too small for one share fraction")
        fee = (qty * price * fee_rate).quantize(Q4)
        cost = (qty * price).quantize(Q4) + fee
        if cost > _dec(portfolio.cash):
            return _reject(order, f"insufficient cash: need {cost}, have {portfolio.cash}")
        portfolio.cash = _dec(portfolio.cash) - cost
    else:
        if held <= 0:
            return _reject(order, "nothing to sell")
        if order.quantity is not None:
            qty = _dec(order.quantity)
        elif order.notional is not None:
            qty = min((_dec(order.notional) / price).quantize(Q6, ROUND_DOWN), held)
        else:
            qty = held
        if qty <= 0:
            return _reject(order, "amount too small for one share fraction")
        if qty > held:
            return _reject(order, f"cannot sell {qty}, only {held} held")
        fee = (qty * price * fee_rate).quantize(Q4)
        portfolio.cash = _dec(portfolio.cash) + (qty * price).quantize(Q4) - fee
    order.status = "filled"
    order.filled_at, order.fill_quantity, order.fill_price, order.fee = ts, qty, price, fee


def settle(session: Session, portfolio: Portfolio, as_of: datetime) -> list[SandboxOrder]:
    """Fill every pending order whose fill bar exists by ``as_of``. Returns the orders that left
    ``pending`` (filled or rejected) in the order they were processed.

    Processing order is by fill bar, and within the same bar sells before buys, so the proceeds of
    a sale can fund a purchase that fills at the same close.
    """
    pending = session.scalars(
        select(SandboxOrder)
        .where(SandboxOrder.portfolio_id == portfolio.id, SandboxOrder.status == "pending")
        .order_by(SandboxOrder.id)
    ).all()
    ready = []
    for order in pending:
        bar = _fill_bar(session, order, as_of)
        if bar is not None:
            ready.append((_utc(bar.ts), order.side != "sell", order.id, order, bar))
    ready.sort(key=lambda r: r[:3])
    done = []
    for ts, _, _, order, bar in ready:
        _fill(session, portfolio, order, _dec(bar.close), ts)
        done.append(order)
        session.flush()  # holdings() reads the filled orders
    return done


# --- strategy ----------------------------------------------------------------------------------


def run_strategy(session: Session, portfolio: Portfolio, as_of: datetime) -> list[SandboxOrder]:
    """Turn signals created since the last run into orders (strategy portfolios only).

    ``buy``/``emerging`` opens a position (``position_pct`` of total value, subject to
    ``max_positions``, ``min_cash_pct`` and cash not already committed to pending buys); ``sell``
    closes it (and cancels a pending buy of the same stock). Every signal is looked at once.
    """
    if portfolio.mode != "strategy":
        return []
    cfg = SandboxConfig.from_dict(portfolio.strategy_config or {})
    signals = session.scalars(
        select(SignalRecord)
        .where(SignalRecord.id > portfolio.last_signal_id)
        .order_by(SignalRecord.id)
    ).all()
    created: list[SandboxOrder] = []
    for signal in signals:
        portfolio.last_signal_id = signal.id
        security = session.get(Security, signal.security_id)
        held = holdings(session, portfolio.id)
        pos = held.get(signal.security_id)
        pending = list(
            session.scalars(
                select(SandboxOrder).where(
                    SandboxOrder.portfolio_id == portfolio.id, SandboxOrder.status == "pending"
                )
            )
        )
        if signal.kind in cfg.buy_kinds:
            if pos is not None or any(
                o.security_id == signal.security_id and o.side == "buy" for o in pending
            ):
                continue
            slots = len(held) + sum(o.side == "buy" for o in pending)
            if slots >= cfg.max_positions:
                continue
            if last_close(session, signal.security_id, as_of) is None:
                continue  # nothing to trade at yet
            total = _dec(portfolio.cash) + positions_value(session, portfolio, as_of)
            reserved = sum((_dec(o.notional or 0) for o in pending if o.side == "buy"), Decimal(0))
            spendable = _dec(portfolio.cash) - reserved - total * Decimal(str(cfg.min_cash_pct))
            notional = min(total * Decimal(str(cfg.position_pct)), spendable).quantize(
                Q4, ROUND_DOWN
            )
            if notional < MIN_NOTIONAL:
                continue
            created.append(
                _add_order(
                    session,
                    portfolio,
                    security,
                    "buy",
                    _utc(signal.as_of),
                    notional=notional,
                    signal_id=signal.id,
                )
            )
        elif signal.kind == "sell":
            for o in pending:
                if o.security_id == signal.security_id and o.side == "buy":
                    cancel_order(session, portfolio.id, o.id, "superseded by a sell signal")
            if pos is not None and not any(
                o.security_id == signal.security_id and o.side == "sell" for o in pending
            ):
                created.append(
                    _add_order(
                        session,
                        portfolio,
                        security,
                        "sell",
                        _utc(signal.as_of),
                        signal_id=signal.id,
                    )
                )
    session.flush()
    return created


# --- snapshots ---------------------------------------------------------------------------------


def snapshot(session: Session, portfolio: Portfolio, as_of: datetime) -> PortfolioSnapshot:
    """The day's value row (idempotent: a second run on the same day overwrites it)."""
    day: date = _utc(as_of).astimezone(UTC).date()
    invested = positions_value(session, portfolio, as_of)
    cash = _dec(portfolio.cash).quantize(Q4)
    row = session.get(PortfolioSnapshot, (portfolio.id, day))
    if row is None:
        row = PortfolioSnapshot(portfolio_id=portfolio.id, as_of=day)
        session.add(row)
    row.cash, row.positions_value, row.total_value = cash, invested, cash + invested
    session.flush()
    return row
