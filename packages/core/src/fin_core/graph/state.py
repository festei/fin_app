from __future__ import annotations

from typing import Annotated, Any, TypedDict

from fin_core.models import ScoreResult, SecurityMetrics, Signal


def merge_results(
    left: dict[str, ScoreResult] | None, right: dict[str, ScoreResult] | None
) -> dict[str, ScoreResult]:
    """Reducer so philosophy nodes running in parallel can write safely."""
    return {**(left or {}), **(right or {})}


class GraphState(TypedDict, total=False):
    metrics: SecurityMetrics
    config: dict[str, Any]
    previous_conviction: float | None
    buy_armed: bool  # hysteresis: a new buy needs conviction to have fallen below BUY_REARM_BELOW
    sell_armed: bool  # ... and a new sell needs it to have recovered to SELL_REARM_ABOVE
    philosophy_results: Annotated[dict[str, ScoreResult], merge_results]
    guardrail_veto: bool
    guardrail_results: dict[str, ScoreResult]
    guardrail_reasons: list[str]
    guardrail_notes: list[str]
    all_abstained: bool
    conviction: float | None  # None = nothing could be evaluated (not a score of 0)
    explanation: str
    signal: Signal | None
