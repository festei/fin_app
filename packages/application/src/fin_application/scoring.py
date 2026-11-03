"""Use case: score a security from the market data we hold. Stores nothing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fin_application.ports import ScoringConfigSource, ScoringEngine, UnitOfWorkFactory
from fin_domain.scoring import Assessment, SecurityMetrics, calculate_metrics, resolve_as_of
from fin_domain.shared import InvariantViolation, NotFound, Ticker
from fin_domain.signals import Signal, decide_signal


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
        self, ticker: str, as_of: date | datetime, baseline: Baseline | None = None
    ) -> LiveScore:
        """Point-in-time metrics from stored data, then the assessment and decision. Raises
        ``NotFound`` for a ticker we do not hold."""
        try:
            symbol = Ticker(ticker)
        except InvariantViolation:
            raise NotFound("security", ticker.upper()) from None
        as_of_dt, as_of_date = resolve_as_of(as_of)
        with self._uow_factory() as uow:
            security = uow.securities.get(symbol)
            if security is None:
                raise NotFound("security", str(symbol))
            facts = uow.facts.filed_up_to(symbol, as_of_date)
            bar = uow.prices.latest(symbol, as_of_dt)
        metrics = calculate_metrics(security, facts, bar, as_of)
        return score(metrics, self._engine, self._config.load(), baseline)
