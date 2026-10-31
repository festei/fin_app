"""Buffett/Munger-style quality-at-a-fair-price criteria as a deterministic node.

Works from the fiscal-year history in ``SecurityMetrics.annual`` (oldest first), so it needs
several years of data: with fewer than ``min_history_years`` it *abstains* (``evaluated=False``)
instead of scoring zero. Criteria that lack their inputs fail with "no data".

Criteria (thresholds in ``config/philosophies.yml``):
1. durable return on equity: ROE above a floor in most of the last ~10 years
2. high operating margin on average, and 3. no collapsed year
4. earnings that are positive in nearly every year
5. debt repayable from earnings within a few years
6. low capital intensity (capex relative to net income)
7. margin of safety: market cap versus an "earnings power" value of owner earnings

Deliberately simplified (documented, not hidden): owner earnings are net income + D&A - capex
(the working-capital term is not modelled), and intrinsic value is owner earnings capitalised at
a fixed discount rate (optionally with growth), not a forecast. "Retained earnings must create
at least $1 of market value per $1 retained" needs market-value history and is not scored yet.
"""

from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "buffett"


def _series(m: SecurityMetrics, concept: str) -> dict[date, float]:
    return {p.period_end: p.value for p in m.annual.get(concept, [])}


def _recent(years: list[date], window_years: int) -> list[date]:
    """The fiscal years within about ``window_years`` calendar years of the newest one."""
    if not years:
        return []
    years = sorted(years)
    return [y for y in years if (years[-1] - y).days <= window_years * 360]


def _no_data(name: str, threshold: float | None, why: str) -> Criterion:
    return Criterion(name=name, passed=False, threshold=threshold, detail=f"no data: {why}")


def _roe(m, cfg, window) -> Criterion:
    name = "Durable return on equity"
    ni, eq = _series(m, "NetIncome"), _series(m, "StockholdersEquity")
    years = _recent([y for y in ni if y in eq], window)
    if len(years) < cfg["min_history_years"]:
        return _no_data(name, cfg["min_roe_year_share"], f"only {len(years)} years with equity")
    meets = sum(1 for y in years if eq[y] > 0 and ni[y] / eq[y] >= cfg["min_roe"])
    share = meets / len(years)
    return Criterion(
        name=name,
        passed=share >= cfg["min_roe_year_share"],
        actual=round(share, 3),
        threshold=cfg["min_roe_year_share"],
        detail=f"{meets} of {len(years)} years with ROE >= {cfg['min_roe']:.0%}",
    )


def _margins(m, cfg, window) -> list[Criterion]:
    avg_name, worst_name = "High operating margin", "No collapsed operating margin"
    oi, rev = _series(m, "OperatingIncome"), _series(m, "Revenue")
    years = _recent([y for y in oi if rev.get(y, 0) > 0], window)
    if len(years) < cfg["min_history_years"]:
        why = f"only {len(years)} years of operating income and revenue"
        return [
            _no_data(avg_name, cfg["min_avg_operating_margin"], why),
            _no_data(worst_name, cfg["min_worst_operating_margin"], why),
        ]
    margins = [oi[y] / rev[y] for y in years]
    avg, worst = mean(margins), min(margins)
    return [
        Criterion(
            name=avg_name,
            passed=avg >= cfg["min_avg_operating_margin"],
            actual=round(avg, 4),
            threshold=cfg["min_avg_operating_margin"],
            detail=f"average over {len(years)} years",
        ),
        Criterion(
            name=worst_name,
            passed=worst >= cfg["min_worst_operating_margin"],
            actual=round(worst, 4),
            threshold=cfg["min_worst_operating_margin"],
            detail=f"worst of {len(years)} years",
        ),
    ]


def _positive_earnings(m, cfg, window) -> Criterion:
    name = "Consistently positive earnings"
    ni = _series(m, "NetIncome")
    years = _recent(list(ni), window)
    if len(years) < cfg["min_history_years"]:
        return _no_data(name, cfg["min_positive_earnings_share"], f"only {len(years)} years")
    positive = sum(1 for y in years if ni[y] > 0)
    share = positive / len(years)
    return Criterion(
        name=name,
        passed=share >= cfg["min_positive_earnings_share"],
        actual=round(share, 3),
        threshold=cfg["min_positive_earnings_share"],
        detail=f"{positive} of {len(years)} years with positive net income",
    )


def _debt_payback(m, cfg) -> Criterion:
    name = "Debt repayable from earnings"
    limit = cfg["max_debt_payback_years"]
    ni, debt_s, assets = _series(m, "NetIncome"), _series(m, "LongTermDebt"), _series(m, "Assets")
    if not ni:
        return _no_data(name, limit, "no net income")
    fy = max(ni)
    debt, note = debt_s.get(fy), ""
    if debt is None:
        if fy not in assets:
            return _no_data(name, limit, f"no balance sheet for fiscal year {fy}")
        debt, note = 0.0, "; no long-term debt reported, treated as 0"
    if debt <= 0:
        return Criterion(
            name=name, passed=True, actual=0.0, threshold=limit, detail=f"no debt{note}"
        )
    if ni[fy] <= 0:
        return Criterion(
            name=name, passed=False, threshold=limit, detail="net income is not positive"
        )
    years = debt / ni[fy]
    return Criterion(
        name=name,
        passed=years <= limit,
        actual=round(years, 2),
        threshold=limit,
        detail=f"years of net income to repay debt (fiscal year {fy}){note}",
    )


def _capital_intensity(m, cfg, window) -> Criterion:
    name = "Low capital intensity"
    limit = cfg["max_capex_to_net_income"]
    ni, capex = _series(m, "NetIncome"), _series(m, "Capex")
    years = _recent([y for y in ni if y in capex], window)[-cfg["capex_years"] :]
    if len(years) < min(3, cfg["capex_years"]):
        return _no_data(name, limit, f"only {len(years)} years with capex")
    avg_ni = mean(ni[y] for y in years)
    if avg_ni <= 0:
        return Criterion(name=name, passed=False, threshold=limit, detail="net income not positive")
    ratio = mean(capex[y] for y in years) / avg_ni
    return Criterion(
        name=name,
        passed=ratio <= limit,
        actual=round(ratio, 3),
        threshold=limit,
        detail=f"average capex / average net income over {len(years)} years",
    )


def _margin_of_safety(m, cfg) -> tuple[Criterion, list[str]]:
    name = "Margin of safety on owner earnings"
    limit = cfg["min_margin_of_safety"]
    if m.multi_class:
        return _no_data(name, limit, "multiple share classes, market value unreliable"), []
    ni, da, capex = (
        _series(m, "NetIncome"),
        _series(m, "DepreciationAmortization"),
        _series(m, "Capex"),
    )
    n = cfg["owner_earnings_years"]
    years = sorted(set(ni) & set(da) & set(capex))[-n:]
    if len(years) < n:
        return _no_data(name, limit, f"owner earnings need {n} years of net income, D&A, capex"), []
    shares_pts = m.annual.get("SharesOutstanding", [])
    if m.price is None or not shares_pts or shares_pts[-1].value <= 0:
        return _no_data(name, limit, "no price or share count"), []

    discount, growth = cfg["discount_rate"], cfg["growth"]
    if discount <= growth:
        raise ValueError("buffett config: discount_rate must be greater than growth")
    owner_earnings = mean(ni[y] + da[y] - capex[y] for y in years)
    market_cap = m.price * shares_pts[-1].value
    evidence = [
        f"Owner earnings ({n}-year average): {owner_earnings:,.0f}",
        f"Market capitalisation: {market_cap:,.0f}",
    ]
    if owner_earnings <= 0:
        return Criterion(
            name=name, passed=False, threshold=limit, detail="owner earnings <= 0"
        ), evidence
    intrinsic = owner_earnings * (1 + growth) / (discount - growth)
    evidence.insert(
        1, f"Intrinsic value ({discount:.0%} discount, {growth:.0%} growth): {intrinsic:,.0f}"
    )
    margin = 1 - market_cap / intrinsic
    return (
        Criterion(
            name=name,
            passed=margin >= limit,
            actual=round(margin, 4),
            threshold=limit,
            detail="1 - market cap / intrinsic value",
        ),
        evidence,
    )


def score_buffett(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    window = cfg["window_years"]
    history = _recent(
        [y for y in _series(m, "NetIncome") if y in _series(m, "StockholdersEquity")], window
    )
    if len(history) < cfg["min_history_years"]:
        return ScoreResult(
            philosophy=NAME,
            score=0.0,
            evaluated=False,
            explanation=(
                f"Not evaluated: needs {cfg['min_history_years']} fiscal years of net income and "
                f"equity, found {len(history)}."
            ),
        )
    mos, evidence = _margin_of_safety(m, cfg)
    criteria = [
        _roe(m, cfg, window),
        *_margins(m, cfg, window),
        _positive_earnings(m, cfg, window),
        _debt_payback(m, cfg),
        _capital_intensity(m, cfg, window),
        mos,
    ]
    passed = sum(c.passed for c in criteria)
    return ScoreResult(
        philosophy=NAME,
        score=round(100.0 * passed / len(criteria), 1),
        criteria=criteria,
        evidence=evidence,
        explanation=f"Passes {passed} of {len(criteria)} Buffett-style quality and value criteria.",
    )


def buffett_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]["buffett"]
    if cfg.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError("LLM-backed Buffett node is not implemented yet")
    return {"philosophy_results": {NAME: score_buffett(state["metrics"], cfg)}}
