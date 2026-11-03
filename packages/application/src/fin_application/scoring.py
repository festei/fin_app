"""Use case: score a security from the market data we hold. Stores nothing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fin_application.ports import (
    ScoringConfigSource,
    ScoringEngine,
    UnitOfWork,
    UnitOfWorkFactory,
)
from fin_domain.scoring import Assessment, SecurityMetrics, calculate_metrics, resolve_as_of
from fin_domain.shared import InvariantViolation, NotFound, Ticker
from fin_domain.signals import BUY_KINDS, SELL_KINDS, Signal, buy_armed, decide_signal, sell_armed


@dataclass(frozen=True)
class Baseline:
    """What the signal decision compares against: the last actionable stored conviction and
    whether hysteresis allows another buy / sell. The defaults mean "no history"."""

    previous_conviction: float | None = None
    buy_armed: bool = True
    sell_armed: bool = True


@dataclass
class LiveScore:
    metrics: SecurityMetrics
    assessment: Assessment
    signal: Signal | None
    explanation: str  # the assessment's, plus why a signal was withheld if it was


def score(
    metrics: SecurityMetrics,
    engine: ScoringEngine,
    config: Mapping[str, Any],
    baseline: Baseline | None = None,
) -> LiveScore:
    """Assess ``metrics`` and decide whether that is a signal."""
    baseline = baseline or Baseline()
    assessment = engine.assess(metrics, config)
    decision = decide_signal(
        assessment,
        previous_conviction=baseline.previous_conviction,
        buy_armed=baseline.buy_armed,
        sell_armed=baseline.sell_armed,
    )
    return LiveScore(metrics, assessment, decision.signal, decision.explanation)


def baseline_from_history(uow: UnitOfWork, ticker: Ticker) -> Baseline:
    """What the next run compares itself against, from the stored scores: the last actionable
    conviction, and whether hysteresis allows another buy / sell."""
    previous = uow.scores.latest_actionable(ticker)
    return Baseline(
        previous_conviction=previous.conviction if previous else None,
        buy_armed=buy_armed(uow.scores.convictions_since_last_signal(ticker, BUY_KINDS)),
        sell_armed=sell_armed(uow.scores.convictions_since_last_signal(ticker, SELL_KINDS)),
    )


def parse_ticker(raw: str) -> Ticker:
    """``Ticker``, or ``NotFound``: something that is not a symbol is not a security we hold."""
    try:
        return Ticker(raw)
    except InvariantViolation:
        raise NotFound("security", raw.upper()) from None


def load_metrics(uow: UnitOfWork, ticker: Ticker, as_of: date | datetime) -> SecurityMetrics:
    """Point-in-time metrics from stored data. Raises ``NotFound`` for a ticker we do not hold."""
    as_of_dt, as_of_date = resolve_as_of(as_of)
    security = uow.securities.get(ticker)
    if security is None:
        raise NotFound("security", str(ticker))
    facts = uow.facts.filed_up_to(ticker, as_of_date)
    bar = uow.prices.latest(ticker, as_of_dt)
    return calculate_metrics(security, facts, bar, as_of)


class ScoreSecurityLive:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        engine: ScoringEngine,
        config: ScoringConfigSource,
    ):
        self._uow_factory = uow_factory
        self._engine = engine
        self._config = config

    def execute(
        self,
        ticker: str,
        as_of: date | datetime,
        baseline: Baseline | None = None,
        *,
        use_stored_baseline: bool = False,
    ) -> LiveScore:
        """Point-in-time metrics from stored data, then the assessment and decision. Stores
        nothing. With ``use_stored_baseline`` (and no explicit ``baseline``) the signal is judged
        against the stored scores and hysteresis, exactly as the scheduled run would. Raises
        ``NotFound`` for a ticker we do not hold."""
        symbol = parse_ticker(ticker)
        with self._uow_factory() as uow:
            metrics = load_metrics(uow, symbol, as_of)
            if baseline is None and use_stored_baseline:
                baseline = baseline_from_history(uow, symbol)
        return score(metrics, self._engine, self._config.load(), baseline)
