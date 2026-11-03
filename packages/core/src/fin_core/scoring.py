"""Legacy bridge: score one stored security inside a caller's SQL session and hand back the
dict the signals engine and the HTTP app were written against.

The scoring itself is ``fin_domain.scoring`` (rules), ``fin_langgraph`` (engine) and
``fin_application.scoring`` (orchestration); this module only loads the inputs through the
session and reshapes the answer. It goes away with ``signals/engine.py`` in step 4.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_application.ports import ScoringEngine
from fin_application.scoring import Baseline, LiveScore, score
from fin_config import load_scoring_config
from fin_domain.scoring import SecurityMetrics, calculate_metrics, resolve_as_of
from fin_domain.shared import InvariantViolation, NotFound, Ticker
from fin_langgraph import LangGraphScoringEngine
from fin_persistence.market_data import (
    SqlFactRepository,
    SqlPriceRepository,
    SqlSecurityRepository,
)


def to_state(live: LiveScore) -> dict[str, Any]:
    """The old graph-state shape."""
    a = live.assessment
    return {
        "metrics": live.metrics,
        "philosophy_results": a.philosophy_results,
        "guardrail_results": a.guardrail_results,
        "guardrail_veto": bool(a.guardrail_reasons),
        "guardrail_reasons": a.guardrail_reasons,
        "guardrail_notes": a.guardrail_notes,
        "conviction": a.conviction,
        "all_abstained": a.all_abstained,
        "explanation": live.explanation,
        "signal": live.signal,
    }


def run_state(
    metrics: SecurityMetrics,
    config: Mapping[str, Any],
    previous_conviction: float | None = None,
    *,
    buy_armed: bool = True,
    sell_armed: bool = True,
    scoring_engine: ScoringEngine | None = None,
) -> dict[str, Any]:
    """Score given metrics (no database) and return the old graph-state dict."""
    live = score(
        metrics,
        scoring_engine or LangGraphScoringEngine(),
        config,
        Baseline(previous_conviction, buy_armed, sell_armed),
    )
    return to_state(live)


def metrics_for(session: Session, ticker: str, as_of: date | datetime) -> SecurityMetrics:
    """Point-in-time metrics for a stored security. Raises ``NotFound`` for an unknown ticker."""
    try:
        symbol = Ticker(ticker)
    except InvariantViolation:
        raise NotFound("security", ticker.upper()) from None
    security = SqlSecurityRepository(session).get(symbol)
    if security is None:
        raise NotFound("security", str(symbol))
    as_of_dt, as_of_date = resolve_as_of(as_of)
    facts = SqlFactRepository(session).filed_up_to(symbol, as_of_date)
    bar = SqlPriceRepository(session).latest(symbol, as_of_dt)
    return calculate_metrics(security, facts, bar, as_of)


def score_security(
    session: Session,
    ticker: str,
    as_of: date | datetime,
    *,
    previous_conviction: float | None = None,
    buy_armed: bool = True,
    sell_armed: bool = True,
    scoring_engine: ScoringEngine | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Point-in-time metrics from the database, then assessment and signal decision. The result
    includes the ``metrics`` that were scored (with their data-quality ``warnings``). Raises
    ``NotFound`` for a ticker we do not hold."""
    return run_state(
        metrics_for(session, ticker, as_of),
        config or load_scoring_config(),
        previous_conviction,
        buy_armed=buy_armed,
        sell_armed=sell_armed,
        scoring_engine=scoring_engine,
    )
