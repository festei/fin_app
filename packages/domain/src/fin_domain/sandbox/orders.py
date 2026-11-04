"""Orders, holdings and the arithmetic of money in the sandbox.

Amounts are ``Decimal``, shares may be fractional. Not investment advice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from fin_domain.shared import InvariantViolation, Ticker, ensure_utc

Q6 = Decimal("0.000001")
Q4 = Decimal("0.0001")
MIN_NOTIONAL = Decimal("0.01")
MAX_QUANTITY = Decimal(10) ** 9  # far below the Numeric(18, 6) column limit
MAX_NOTIONAL = Decimal(10) ** 12
MAX_CASH = Decimal(10) ** 12
DUST = Decimal("0.000001")
MAX_REASON = 200


class OrderError(InvariantViolation):
    """An invalid request (the API maps it to 422)."""


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


def to_decimal(x: Any) -> Decimal:
    try:
        d = x if isinstance(x, Decimal) else Decimal(str(x))
    except ArithmeticError:
        raise OrderError(f"not a number: {x!r}") from None
    if not d.is_finite():
        raise OrderError(f"not a finite number: {x!r}")
    return d


def day_after(moment: datetime) -> datetime:
    """Midnight UTC of the day after ``moment``: bars on or after this fill an order decided at
    ``moment`` (an order never fills at the close of its own day)."""
    d = ensure_utc(moment).astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(d, time.min, tzinfo=UTC)


@dataclass
class Order:
    ticker: Ticker
    side: OrderSide
    created_at: datetime
    quantity: Decimal | None = None
    notional: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    signal_id: int | None = None  # the signal a strategy traded on (kept for attribution)
    filled_at: datetime | None = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    fee: Decimal | None = None
    reason: str | None = None  # why it was rejected or cancelled
    id: int | None = None

    def __post_init__(self) -> None:
        self.created_at = ensure_utc(self.created_at, "created_at")

    @property
    def is_pending(self) -> bool:
        return self.status is OrderStatus.PENDING

    def cancel(self, reason: str) -> None:
        self.status, self.reason = OrderStatus.CANCELLED, reason[:MAX_REASON]

    def reject(self, reason: str) -> None:
        self.status, self.reason = OrderStatus.REJECTED, reason[:MAX_REASON]

    def fill(self, quantity: Decimal, price: Decimal, fee: Decimal, at: datetime) -> None:
        self.status = OrderStatus.FILLED
        self.filled_at, self.fill_quantity, self.fill_price, self.fee = at, quantity, price, fee


@dataclass(frozen=True)
class Holding:
    ticker: Ticker
    quantity: Decimal  # in today's share basis
    cost: Decimal  # total spent on what is still held, buy fees included
    avg_cost: Decimal  # per share of today's basis


@dataclass(frozen=True)
class PositionValue:
    holding: Holding
    price: Decimal | None  # latest close at the valuation time; None if the stock has no bar
    value: Decimal  # at that price, or at cost when there is no price
