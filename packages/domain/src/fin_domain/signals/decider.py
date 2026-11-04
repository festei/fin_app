"""When does a conviction become a signal?

A signal is emitted only when conviction crosses a threshold relative to the previous (stored)
conviction, never on every run. Hysteresis stops a conviction flapping around a line from
signalling on every crossing: after a buy the next buy needs conviction to have fallen below
``BUY_REARM_BELOW``; after a sell the next sell needs it to have recovered to ``SELL_REARM_ABOVE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fin_domain.scoring import Assessment

BUY_THRESHOLD = 70.0
SELL_THRESHOLD = 50.0
BUY_REARM_BELOW = 65.0
SELL_REARM_ABOVE = 55.0

SignalKind = Literal["buy", "sell", "watch", "emerging"]


@dataclass
class Signal:
    ticker: str
    kind: SignalKind
    conviction: float
    as_of: datetime
    reason: str = ""


@dataclass(frozen=True)
class Decision:
    signal: Signal | None
    explanation: str  # the assessment's, plus why a signal was withheld, if it was


def decide_signal(
    assessment: Assessment,
    *,
    previous_conviction: float | None = None,
    buy_armed: bool = True,
    sell_armed: bool = True,
) -> Decision:
    """``previous_conviction`` is the last *actionable* stored conviction (``None`` if there is
    none: the next real score is then ``emerging``, not ``buy``)."""
    explanation = assessment.explanation
    if assessment.all_abstained or assessment.conviction is None:
        return Decision(None, explanation)  # nothing was evaluated: absence of data, not a view
    now = assessment.conviction
    metrics = assessment.metrics
    kind: SignalKind | None = None
    crossed_up = now >= BUY_THRESHOLD and (
        previous_conviction is None or previous_conviction < BUY_THRESHOLD
    )
    crossed_down = (
        previous_conviction is not None
        and previous_conviction >= SELL_THRESHOLD
        and now < SELL_THRESHOLD
    )
    if crossed_up and buy_armed:
        kind = "emerging" if previous_conviction is None else "buy"
    elif crossed_down and sell_armed:
        kind = "sell"
    if metrics.signal_blockers:
        # Stale or missing price/fundamentals must not produce a buy/sell: say why instead.
        note = f"Signal withheld: {'; '.join(metrics.signal_blockers)}."
        return Decision(None, f"{explanation} {note}".strip())
    if kind is None:
        return Decision(None, explanation)
    return Decision(
        Signal(
            ticker=metrics.ticker,
            kind=kind,
            conviction=now,
            as_of=metrics.as_of,
            reason=explanation,
        ),
        explanation,
    )
