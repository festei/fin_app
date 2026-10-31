"""Philip Fisher-style growth quality: the quantifiable subset of his "15 points".

Scored here: sustained sales and profit growth, R&D effort, a worthwhile and not-deteriorating
operating margin, gross margin trend, and growth financed without heavy dilution. Roughly nine
of Fisher's points are qualitative (management integrity and depth, labor and executive
relations, ...) and are NOT scored; they await a future LLM-based node. The explanation says so.

Missing R&D expense fails the R&D criterion as "no data" (Fisher stresses innovation, so
companies with no reported R&D score lower on this lens). The gross-margin criterion is omitted
as not applicable when no gross profit is reported. Abstains with too little revenue history.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from fin_core.graph.data import cagr, recent_years, series_by_year
from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "fisher"


def _no_data(name: str, threshold: float | None, why: str) -> Criterion:
    return Criterion(name=name, passed=False, threshold=threshold, detail=f"no data: {why}")


def _growth(m, concept, name, floor, cfg) -> Criterion:
    pts = sorted(series_by_year(m, concept).items())
    g = cagr(pts, cfg["window_years"], cfg["min_history_years"])
    if g is None:
        return _no_data(name, floor, f"{concept} growth not computable from the history")
    return Criterion(
        name=name,
        passed=g >= floor,
        actual=round(g, 4),
        threshold=floor,
        detail=f"smoothed {concept} growth per year",
    )


def _margin_series(num, den, window):
    years = recent_years([y for y in num if den.get(y, 0) > 0], window)
    return [(y, num[y] / den[y]) for y in years]


def _trend(name, margins, cfg) -> Criterion:
    if len(margins) < cfg["min_history_years"]:
        return _no_data(name, None, f"only {len(margins)} years")
    first, last = mean(v for _, v in margins[:3]), mean(v for _, v in margins[-3:])
    return Criterion(
        name=name,
        passed=last >= first,
        actual=round(last - first, 4),
        threshold=0.0,
        detail=f"average of last 3 years minus first 3 ({last:.1%} vs {first:.1%})",
    )


def _dilution(m: SecurityMetrics, cfg) -> Criterion:
    name, limit = "No heavy dilution", cfg["max_annual_dilution"]
    pts = sorted((p.period_end, p.value) for p in m.annual.get("SharesOutstanding", []))
    pts = [p for p in pts if (pts[-1][0] - p[0]).days <= cfg["window_years"] * 360]
    if len(pts) < 2 or (pts[-1][0] - pts[0][0]).days < 2 * 365 or pts[0][1] <= 0:
        return _no_data(name, limit, "needs share counts spanning at least 2 years")
    years = (pts[-1][0] - pts[0][0]).days / 365.25
    rate = (pts[-1][1] / pts[0][1]) ** (1 / years) - 1
    return Criterion(
        name=name,
        passed=rate <= limit,
        actual=round(rate, 4),
        threshold=limit,
        detail=f"share-count growth per year over {years:.1f} years",
    )


def score_fisher(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    window = cfg["window_years"]
    rev, oi, gp = (series_by_year(m, c) for c in ("Revenue", "OperatingIncome", "GrossProfit"))
    rd = series_by_year(m, "ResearchAndDevelopmentExpense")
    if len(recent_years(list(rev), window)) < cfg["min_history_years"]:
        n = len(recent_years(list(rev), window))
        return ScoreResult(
            philosophy=NAME,
            score=0.0,
            evaluated=False,
            explanation=(
                f"Not evaluated: needs {cfg['min_history_years']} years of revenue, found {n}."
            ),
        )

    op_margins = _margin_series(oi, rev, window)
    if op_margins:
        latest = mean(v for _, v in op_margins[-3:])
        margin = Criterion(
            name="Worthwhile operating margin",
            passed=latest >= cfg["min_operating_margin"],
            actual=round(latest, 4),
            threshold=cfg["min_operating_margin"],
            detail="average operating margin of the last 3 fiscal years",
        )
    else:
        margin = _no_data("Worthwhile operating margin", cfg["min_operating_margin"], "no margins")

    rd_years = [y for y in sorted(rd) if rev.get(y, 0) > 0][-3:]
    if rd_years:
        intensity = mean(rd[y] / rev[y] for y in rd_years)
        rd_criterion = Criterion(
            name="R&D intensity",
            passed=intensity >= cfg["min_rd_to_revenue"],
            actual=round(intensity, 4),
            threshold=cfg["min_rd_to_revenue"],
            detail=f"R&D / revenue, average of the last {len(rd_years)} years",
        )
    else:
        rd_criterion = _no_data(
            "R&D intensity", cfg["min_rd_to_revenue"], "no R&D expense reported"
        )

    criteria = [
        _growth(m, "Revenue", "Sustained sales growth", cfg["min_revenue_growth"], cfg),
        _growth(m, "NetIncome", "Sustained profit growth", cfg["min_profit_growth"], cfg),
        rd_criterion,
        margin,
        _trend("Operating margin not deteriorating", op_margins, cfg),
    ]
    gross = _margin_series(gp, rev, window)
    if gross:  # omitted as not applicable when gross profit is not reported
        criteria.append(_trend("Gross margin not deteriorating", gross, cfg))
    criteria.append(_dilution(m, cfg))

    passed = sum(c.passed for c in criteria)
    return ScoreResult(
        philosophy=NAME,
        score=round(100.0 * passed / len(criteria), 1),
        criteria=criteria,
        evidence=["Quantitative subset only: Fisher's qualitative points are not scored."],
        explanation=f"Passes {passed} of {len(criteria)} quantifiable Fisher criteria.",
    )


def fisher_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]["fisher"]
    if cfg.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError("LLM-backed Fisher node is not implemented yet")
    return {"philosophy_results": {NAME: score_fisher(state["metrics"], cfg)}}
