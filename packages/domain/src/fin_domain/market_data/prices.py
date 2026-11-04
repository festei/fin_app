"""Market prices and rates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from fin_domain.shared import InvariantViolation, ensure_utc


@dataclass(frozen=True)
class PriceBar:
    """One daily bar. Prices are the source's split/dividend-adjusted prices, not as traded."""

    ts: datetime
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", ensure_utc(self.ts, "ts"))
        if self.volume is not None and self.volume < 0:
            raise InvariantViolation("volume cannot be negative")


@dataclass(frozen=True)
class YieldPoint:
    """One observation of a rate series, in percent."""

    on: date
    value: Decimal


class BarLike(Protocol):
    ts: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None


def aggregate_candles(bars: Sequence[BarLike], max_points: int) -> list[BarLike | Candle]:
    """At most ``max_points`` bars: with more, every ``step`` consecutive bars merge into one
    candle (first open, highest high, lowest low, last close, summed volume, timestamp of the
    last bar). Buckets are counted from the newest bar, so the latest candle ends on the latest
    bar and a candlestick or band chart keeps the true extremes. Works on anything bar-shaped,
    and leaves timestamps as given."""
    if max_points < 1:
        raise InvariantViolation("max_points must be at least 1")
    if len(bars) <= max_points:
        return list(bars)
    step = -(-len(bars) // max_points)
    out: list[BarLike | Candle] = []
    for end in range(len(bars), 0, -step):
        group = bars[max(0, end - step) : end]
        highs = [b.high for b in group if b.high is not None]
        lows = [b.low for b in group if b.low is not None]
        volumes = [b.volume for b in group if b.volume is not None]
        out.append(
            Candle(
                ts=group[-1].ts,
                open=group[0].open,
                high=max(highs) if highs else None,
                low=min(lows) if lows else None,
                close=group[-1].close,
                volume=sum(volumes) if volumes else None,
            )
        )
    return out[::-1]
