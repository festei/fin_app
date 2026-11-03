"""The old graph-shaped entry points (``build_graph().invoke(state)``, ``decide_node(state)``),
rebuilt on the new code so the scenario tests written against them keep running unchanged.

Test support only. Not a ``conftest.py`` on purpose: pytest registers every conftest as the
top-level module ``conftest``, and a core test imports ``bar`` from the core one by that name.
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_application.ports import ScoringEngine
from fin_application.scoring import Baseline, LiveScore, score
from fin_config import load_scoring_config
from fin_domain.scoring import Assessment, SecurityMetrics, calculate_metrics, resolve_as_of
from fin_domain.shared import InvariantViolation, NotFound, Ticker
from fin_domain.signals import decide_signal
from fin_langgraph import LangGraphScoringEngine
from fin_persistence.market_data import (
    SqlFactRepository,
    SqlPriceRepository,
    SqlSecurityRepository,
)


class _Graph:
    def invoke(self, state):
        return run_state(
            state["metrics"],
            state["config"],
            state.get("previous_conviction"),
            buy_armed=state.get("buy_armed", True),
            sell_armed=state.get("sell_armed", True),
        )


def build_graph():
    return _Graph()


def decide_node(state):
    explanation = state.get("explanation", "")
    assessment = Assessment(
        metrics=state["metrics"],
        conviction=state.get("conviction"),
        all_abstained=state.get("all_abstained", False),
        explanation=explanation,
        philosophy_results={},
    )
    decision = decide_signal(
        assessment,
        previous_conviction=state.get("previous_conviction"),
        buy_armed=state.get("buy_armed", True),
        sell_armed=state.get("sell_armed", True),
    )
    out = {"signal": decision.signal}
    if decision.explanation != explanation:
        out["explanation"] = decision.explanation
    return out


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
