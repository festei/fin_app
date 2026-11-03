"""Signals & Alerts context: when a conviction becomes a signal, and what happens to it."""

from fin_domain.signals.decider import (
    BUY_REARM_BELOW,
    BUY_THRESHOLD,
    SELL_REARM_ABOVE,
    SELL_THRESHOLD,
    Decision,
    Signal,
    SignalKind,
    decide_signal,
)

__all__ = [
    "BUY_REARM_BELOW",
    "BUY_THRESHOLD",
    "SELL_REARM_ABOVE",
    "SELL_THRESHOLD",
    "Decision",
    "Signal",
    "SignalKind",
    "decide_signal",
]
