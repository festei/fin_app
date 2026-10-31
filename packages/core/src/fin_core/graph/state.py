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
    philosophy_results: Annotated[dict[str, ScoreResult], merge_results]
    guardrail_veto: bool
    all_abstained: bool
    conviction: float
    explanation: str
    signal: Signal | None
