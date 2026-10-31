"""Benjamin Graham (defensive investor) criteria as a deterministic node.

Pure function of metrics + thresholds, so it is cheap, reproducible and safe
for point-in-time backtests. An LLM implementation can later replace this node
as long as it returns the same ``ScoreResult``.
"""

from __future__ import annotations

from typing import Any

from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "graham"


def _positive_lt(
    name: str, actual: float | None, limit: float, *, allow_zero: bool = False
) -> Criterion:
    """Below ``limit`` AND meaningful.

    A negative P/E (loss-making) or negative P/B or debt/equity (negative equity) is a red
    flag, not "cheap" or "safe", so it fails instead of passing the ``< limit`` test.
    """
    if actual is None:
        return Criterion(name=name, passed=False, threshold=limit, detail="no data")
    meaningful = actual >= 0 if allow_zero else actual > 0
    if not meaningful:
        return Criterion(
            name=name,
            passed=False,
            actual=actual,
            threshold=limit,
            detail="non-positive: loss-making or negative equity",
        )
    return Criterion(name=name, passed=actual < limit, actual=actual, threshold=limit)


def _pe_times_pb(pe: float | None, pb: float | None, limit: float) -> Criterion:
    """Graham's P/E x P/B cap. Both factors must be positive: two negatives multiply to a
    positive product that would otherwise slip under the limit."""
    name = "P/E x P/B below limit"
    if pe is None or pb is None:
        return Criterion(name=name, passed=False, threshold=limit, detail="no data")
    if pe <= 0 or pb <= 0:
        return Criterion(
            name=name,
            passed=False,
            threshold=limit,
            detail="needs positive P/E and P/B: loss-making or negative equity",
        )
    return _positive_lt(name, pe * pb, limit)


def _ge(name: str, actual: float | None, limit: float) -> Criterion:
    if actual is None:
        return Criterion(name=name, passed=False, threshold=limit, detail="no data")
    return Criterion(name=name, passed=actual >= limit, actual=actual, threshold=limit)


def score_graham(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    criteria = [
        _positive_lt("P/E below limit", m.pe, cfg["max_pe"]),
        _positive_lt("P/B below limit", m.pb, cfg["max_pb"]),
        _pe_times_pb(m.pe, m.pb, cfg["max_pe_times_pb"]),
        _ge("Current ratio at least", m.current_ratio, cfg["min_current_ratio"]),
        _positive_lt(
            "Debt/equity below limit", m.debt_to_equity, cfg["max_debt_to_equity"], allow_zero=True
        ),
        _ge(
            "Dividend record (years)",
            float(m.dividend_years) if m.dividend_years is not None else None,
            float(cfg["min_dividend_years"]),
        ),
        _ge("Earnings growth at least", m.earnings_growth, cfg["min_earnings_growth"]),
    ]
    passed = sum(c.passed for c in criteria)
    score = 100.0 * passed / len(criteria)
    return ScoreResult(
        philosophy=NAME,
        score=round(score, 1),
        criteria=criteria,
        explanation=f"Passes {passed} of {len(criteria)} Graham defensive-investor criteria.",
    )


def graham_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]["graham"]
    if cfg.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError("LLM-backed Graham node is not implemented yet")
    return {"philosophy_results": {NAME: score_graham(state["metrics"], cfg)}}
