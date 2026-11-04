"""The paper-trading portfolio: orders, fills, strategy, valuation, snapshots.

Holdings are not stored: they are derived from the filled orders and restated to today's share
basis. Prices are the source's split/dividend-adjusted prices, and the source restates old bars
when a split happens, so each fill is scaled by (price it filled at / what that bar's close is
now). After a 2:1 split a holding of 1 share bought at 100 becomes 2 shares at an average cost of
50, so a split is neither a loss nor a gain (and dividends show up as slightly more shares, like
reinvested dividends).

Fill model (no look-ahead, honest for daily data): an order decided on day D fills at the close of
the first stored daily bar on a day after D, at the source's adjusted price. Until such a bar
exists it stays ``pending``. Cash and holdings are checked at fill time; an order that cannot be
afforded or covered is ``rejected`` with a reason.

Everything here is a rule over values handed in (the ``PriceBook``, the signals, the time). Not
investment advice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from fin_domain.sandbox.orders import (
    DUST,
    MAX_CASH,
    MAX_NOTIONAL,
    MAX_QUANTITY,
    MIN_NOTIONAL,
    Q4,
    Q6,
    Holding,
    Order,
    OrderError,
    OrderSide,
    OrderStatus,
    PositionValue,
    day_after,
    to_decimal,
)
from fin_domain.sandbox.prices import PriceBook, PriceRequests
from fin_domain.sandbox.strategy import StrategyRules
from fin_domain.shared import Ticker, ensure_utc
from fin_domain.signals import StoredSignal


class PortfolioMode(StrEnum):
    MANUAL = "manual"
    STRATEGY = "strategy"


@dataclass(frozen=True)
class PortfolioSnapshot:
    """One value row per portfolio and day (the equity curve)."""

    as_of: date
    cash: Decimal
    positions_value: Decimal
    total_value: Decimal


@dataclass
class Portfolio:
    name: str
    mode: PortfolioMode
    initial_cash: Decimal
    cash: Decimal
    fee_bps: Decimal
    created_at: datetime
    strategy: StrategyRules | None = None  # strategy portfolios only
    last_signal_id: int = 0  # a strategy looks at each signal once
    orders: list[Order] = field(default_factory=list)
    id: int | None = None

    # --- opening --------------------------------------------------------------------------------

    @classmethod
    def open(
        cls,
        name: str,
        *,
        mode: str,
        cash: object,
        defaults: StrategyRules,
        now: datetime,
        fee_bps: object | None = None,
        strategy: dict | None = None,
        last_signal_id: int = 0,
    ) -> Portfolio:
        try:
            kind = PortfolioMode(mode)
        except ValueError:
            raise OrderError(
                f"mode must be one of {tuple(m.value for m in PortfolioMode)}"
            ) from None
        amount = to_decimal(cash)
        if not 0 < amount <= MAX_CASH:
            raise OrderError(f"starting cash must be in (0, {MAX_CASH}]")
        if not name.strip():
            raise OrderError("name must not be empty")
        rules = None
        if kind is PortfolioMode.STRATEGY:
            try:
                rules = StrategyRules.from_dict({**defaults.to_json(), **(strategy or {})})
            except (ValueError, TypeError) as exc:
                raise OrderError(f"invalid strategy configuration: {exc}") from None
        fee = to_decimal(defaults.fee_bps if fee_bps is None else fee_bps)
        if not 0 <= fee <= 1000:
            raise OrderError("fee_bps must be between 0 and 1000")
        return cls(
            name=name.strip(),
            mode=kind,
            initial_cash=amount,
            cash=amount,
            fee_bps=fee,
            created_at=ensure_utc(now),
            strategy=rules,
            last_signal_id=last_signal_id,  # a strategy never trades history
        )

    # --- orders ---------------------------------------------------------------------------------

    def ensure_manual(self) -> None:
        if self.mode is not PortfolioMode.MANUAL:
            raise OrderError("this portfolio trades from signals; manual orders need a manual one")

    def _add_order(self, order: Order) -> Order:
        self.orders.append(order)
        return order

    def place_order(
        self,
        ticker: Ticker,
        side: str,
        now: datetime,
        *,
        quantity: object | None = None,
        notional: object | None = None,
    ) -> Order:
        """A user's order. Only ``manual`` portfolios accept these."""
        self.ensure_manual()
        try:
            order_side = OrderSide(side)
        except ValueError:
            raise OrderError("side must be 'buy' or 'sell'") from None
        qty = to_decimal(quantity) if quantity is not None else None
        amount = to_decimal(notional) if notional is not None else None
        if (qty is not None and not 0 < qty <= MAX_QUANTITY) or (
            amount is not None and not 0 < amount <= MAX_NOTIONAL
        ):
            raise OrderError(
                f"quantity must be in (0, {MAX_QUANTITY}] and notional in (0, {MAX_NOTIONAL}]"
            )
        if qty is not None and amount is not None:
            raise OrderError("give a quantity or a notional amount, not both")
        if order_side is OrderSide.BUY and qty is None and amount is None:
            raise OrderError("a buy needs a quantity or a notional amount")
        return self._add_order(
            Order(
                ticker,
                order_side,
                now,
                quantity=qty.quantize(Q6, ROUND_DOWN) if qty is not None else None,
                notional=amount.quantize(Q4, ROUND_DOWN) if amount is not None else None,
            )
        )

    def cancel_order(self, order_id: int, reason: str) -> bool:
        """Cancel a pending order. ``False`` if it is not this portfolio's or no longer pending."""
        order = next((o for o in self.orders if o.id == order_id), None)
        if order is None or not order.is_pending:
            return False
        order.cancel(reason)
        return True

    # --- the prices a decision will need ---------------------------------------------------------

    def price_requests(self, extra_tickers: Sequence[Ticker] = ()) -> PriceRequests:
        filled = [o for o in self.orders if o.status is OrderStatus.FILLED]
        return PriceRequests(
            last_close_for=frozenset({o.ticker for o in filled} | set(extra_tickers)),
            close_at=frozenset((o.ticker, o.filled_at) for o in filled if o.filled_at),
            first_bar_from=frozenset(
                (o.ticker, day_after(o.created_at)) for o in self.orders if o.is_pending
            ),
        )

    # --- holdings and valuation -------------------------------------------------------------------

    def holdings(self, book: PriceBook) -> dict[Ticker, Holding]:
        """What the portfolio holds now, derived from its filled orders (average-cost accounting).

        Every fill is restated to today's share basis by ``fill price / current stored close of
        the bar it filled at``: the price source re-adjusts history after a split (or dividend),
        so that ratio is the cumulative adjustment since the fill. With no corporate action (or
        when the fill happened just now) it is exactly 1.
        """
        indexed = [(o, i) for i, o in enumerate(self.orders) if o.status is OrderStatus.FILLED]
        indexed.sort(key=lambda oi: (oi[0].filled_at, oi[1]))
        state: dict[Ticker, list[Decimal]] = {}  # ticker -> [quantity, cost]
        for order, _ in indexed:
            price, filled = to_decimal(order.fill_price), to_decimal(order.fill_quantity)
            current = book.close_at(order.ticker, order.filled_at)
            factor = price / current if current else Decimal(1)
            qty = filled * factor
            held = state.setdefault(order.ticker, [Decimal(0), Decimal(0)])
            if order.side is OrderSide.BUY:
                held[0] += qty
                held[1] += filled * price + to_decimal(order.fee or 0)
            else:
                qty = min(qty, held[0])
                if held[0] > 0:
                    held[1] -= held[1] * qty / held[0]
                held[0] -= qty
        out: dict[Ticker, Holding] = {}
        for ticker, (qty, cost) in state.items():
            if qty > DUST:
                out[ticker] = Holding(
                    ticker, qty.quantize(Q6), cost.quantize(Q4), (cost / qty).quantize(Q6)
                )
        return out

    def valued_positions(self, book: PriceBook) -> list[PositionValue]:
        """Holdings at the latest close at or before the as-of time (cost when there is no bar)."""
        out = []
        for h in self.holdings(book).values():
            price = book.last_close(h.ticker)
            out.append(PositionValue(h, price, h.quantity * price if price is not None else h.cost))
        return out

    def positions_value(self, book: PriceBook) -> Decimal:
        total = sum((p.value for p in self.valued_positions(book)), Decimal(0))
        return total.quantize(Q4)

    # --- settling orders --------------------------------------------------------------------------

    def _fill(self, order: Order, price: Decimal, ts: datetime, book: PriceBook) -> None:
        fee_rate = to_decimal(self.fee_bps) / Decimal(10000)
        held_row = self.holdings(book).get(order.ticker)
        held = held_row.quantity if held_row else Decimal(0)
        if order.side is OrderSide.BUY:
            qty = (
                to_decimal(order.quantity)
                if order.quantity is not None
                else (to_decimal(order.notional) / price).quantize(Q6, ROUND_DOWN)
            )
            if qty <= 0:
                return order.reject("amount too small for one share fraction")
            fee = (qty * price * fee_rate).quantize(Q4)
            cost = (qty * price).quantize(Q4) + fee
            if cost > to_decimal(self.cash):
                return order.reject(f"insufficient cash: need {cost}, have {self.cash}")
            self.cash = to_decimal(self.cash) - cost
        else:
            if held <= 0:
                return order.reject("nothing to sell")
            if order.quantity is not None:
                qty = to_decimal(order.quantity)
            elif order.notional is not None:
                qty = min((to_decimal(order.notional) / price).quantize(Q6, ROUND_DOWN), held)
            else:
                qty = held
            if qty <= 0:
                return order.reject("amount too small for one share fraction")
            if qty > held:
                return order.reject(f"cannot sell {qty}, only {held} held")
            fee = (qty * price * fee_rate).quantize(Q4)
            self.cash = to_decimal(self.cash) + (qty * price).quantize(Q4) - fee
        order.fill(qty, price, fee, ts)

    def settle(self, book: PriceBook, as_of: datetime) -> list[Order]:
        """Fill every pending order whose fill bar exists by ``as_of`` (the book holds the first
        bar at or after each order's day after). Returns the orders that left ``pending`` (filled
        or rejected) in the order they were processed.

        Processing order is by fill bar, and within the same bar sells before buys, so the
        proceeds of a sale can fund a purchase that fills at the same close.
        """
        ready = []
        for index, order in enumerate(self.orders):
            if not order.is_pending:
                continue
            bar = book.first_bar(order.ticker, day_after(order.created_at))
            if bar is not None and bar.ts <= ensure_utc(as_of):
                ready.append((bar.ts, order.side is not OrderSide.SELL, index, order, bar))
        ready.sort(key=lambda r: r[:3])
        done = []
        for ts, _, _, order, bar in ready:
            self._fill(order, to_decimal(bar.close), ts, book)
            done.append(order)
        return done

    # --- strategy ---------------------------------------------------------------------------------

    def apply_signals(self, signals: Sequence[StoredSignal], book: PriceBook) -> list[Order]:
        """Turn signals created since the last run into orders (strategy portfolios only).

        ``buy``/``emerging`` opens a position (``position_pct`` of total value, subject to
        ``max_positions``, ``min_cash_pct`` and cash not already committed to pending buys);
        ``sell`` closes it (and cancels a pending buy of the same stock). Every signal is looked
        at once: a buy skipped for lack of cash, slots or a price is not reconsidered.
        """
        if self.mode is not PortfolioMode.STRATEGY:
            return []
        cfg = self.strategy or StrategyRules()
        created: list[Order] = []
        for stored in signals:
            self.last_signal_id = stored.id
            ticker, kind = stored.ticker, stored.signal.kind
            held = self.holdings(book)
            pos = held.get(ticker)
            pending = [o for o in self.orders if o.is_pending]
            if kind in cfg.buy_kinds:
                if pos is not None or any(
                    o.ticker == ticker and o.side is OrderSide.BUY for o in pending
                ):
                    continue
                slots = len(held) + sum(o.side is OrderSide.BUY for o in pending)
                if slots >= cfg.max_positions:
                    continue
                if book.last_close(ticker) is None:
                    continue  # nothing to trade at yet
                total = to_decimal(self.cash) + self.positions_value(book)
                reserved = sum(
                    (to_decimal(o.notional or 0) for o in pending if o.side is OrderSide.BUY),
                    Decimal(0),
                )
                spendable = (
                    to_decimal(self.cash) - reserved - total * Decimal(str(cfg.min_cash_pct))
                )
                notional = min(total * Decimal(str(cfg.position_pct)), spendable).quantize(
                    Q4, ROUND_DOWN
                )
                if notional < MIN_NOTIONAL:
                    continue
                created.append(
                    self._add_order(
                        Order(
                            ticker,
                            OrderSide.BUY,
                            stored.signal.as_of.astimezone(UTC),
                            notional=notional,
                            signal_id=stored.id,
                        )
                    )
                )
            elif kind == "sell":
                for o in pending:
                    if o.ticker == ticker and o.side is OrderSide.BUY:
                        o.cancel("superseded by a sell signal")
                if pos is not None and not any(
                    o.ticker == ticker and o.side is OrderSide.SELL for o in pending
                ):
                    created.append(
                        self._add_order(
                            Order(
                                ticker,
                                OrderSide.SELL,
                                stored.signal.as_of.astimezone(UTC),
                                signal_id=stored.id,
                            )
                        )
                    )
        return created

    # --- snapshots --------------------------------------------------------------------------------

    def snapshot(self, book: PriceBook, as_of: datetime) -> PortfolioSnapshot:
        """The day's value row."""
        day = ensure_utc(as_of).astimezone(UTC).date()
        invested = self.positions_value(book)
        cash = to_decimal(self.cash).quantize(Q4)
        return PortfolioSnapshot(day, cash, invested, cash + invested)
