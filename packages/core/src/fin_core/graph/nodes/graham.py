"""Benjamin Graham (defensive investor) criteria as a deterministic node.

Pure function of metrics + thresholds, so it is cheap, reproducible and safe
for point-in-time backtests. An LLM implementation can later replace this node
as long as it returns the same ``ScoreResult``.
"""

from __future__ import annotations

from typing import Any

from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "graham"


def _lt(name: str, actual: float | None, limit: float) -> Criterion:
    if actual is None:
        return Criterion(name=name, passed=False, threshold=limit, detail="no data")
    return Criterion(name=name, passed=actual < limit, actual=actual, threshold=limit)


def _ge(name: str, actual: float | None, limit: float) -> Criterion:
    if actual is None:
        return Criterion(name=name, passed=False, threshold=limit, detail="no data")
    return Criterion(name=name, passed=actual >= limit, actual=actual, threshold=limit)


def score_graham(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    pe_pb = m.pe * m.pb if m.pe is not None and m.pb is not None else None
    criteria = [
        _lt("P/E below limit", m.pe, cfg["max_pe"]),
        _lt("P/B below limit", m.pb, cfg["max_pb"]),
        _lt("P/E x P/B below limit", pe_pb, cfg["max_pe_times_pb"]),
        _ge("Current ratio at least", m.current_ratio, cfg["min_current_ratio"]),
        _lt("Debt/equity below limit", m.debt_to_equity, cfg["max_debt_to_equity"]),
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
