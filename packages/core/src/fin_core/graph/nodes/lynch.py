"""Peter Lynch-style "growth at a reasonable price" as a deterministic node.

Lynch first sorts a company into a category and then asks a category-specific question:

* fast grower (EPS growth >= 20%/yr): is the growth-adjusted P/E (PEG) at most 1?
* stalwart (10-20%/yr): PEG at most ~1.2 and a P/E that is not stretched
* slow grower (< 10%/yr): bought for income, so it needs a decent dividend yield and a low P/E
* cyclical / turnaround: a PEG is meaningless, so the node **abstains** and names the reason
* asset plays need asset values and are not modelled

The category comes from the fiscal-year EPS history (``cagr`` of the 3-year-smoothed EPS), so it
needs ``min_history_years`` of data. PEG = latest P/E / (growth % + dividend yield %), i.e.
dividend-adjusted as Lynch suggests. Criteria common to all categories: low debt, and
inventories not growing faster than sales (omitted as not applicable when no inventory is
reported).
"""

from __future__ import annotations

from typing import Any

from fin_core.graph.data import cagr, previous_year, recent_years, series_by_year
from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "lynch"
FAST, STALWART, SLOW = "fast grower", "stalwart", "slow grower"


def _abstain(why: str) -> ScoreResult:
    return ScoreResult(
        philosophy=NAME, score=0.0, evaluated=False, explanation=f"Not evaluated: {why}."
    )


def classify(eps: list[tuple[Any, float]], cfg: dict[str, Any]) -> tuple[str, float | None]:
    """``(category, annual_growth)``. Categories: fast grower, stalwart, slow grower, cyclical,
    turnaround, or unknown (growth not computable)."""
    values = [v for _, v in eps]
    if len(values) >= 2 and values[-1] > 0 and any(v <= 0 for v in values[-4:-1]):
        return "turnaround", None
    drops = sum(
        1
        for prev, cur in zip(values, values[1:], strict=False)
        if prev > 0 and (cur - prev) / prev <= -cfg["cyclical_drop"]
    )
    if drops >= cfg["cyclical_min_drops"]:
        return "cyclical", None
    growth = cagr(eps, cfg["window_years"], cfg["min_history_years"])
    if growth is None:
        return "unknown", None
    if growth >= cfg["fast_growth"]:
        return FAST, growth
    if growth >= cfg["stalwart_growth"]:
        return STALWART, growth
    return SLOW, growth


def _inventory_vs_sales(m: SecurityMetrics, cfg: dict[str, Any]) -> Criterion | None:
    inv, rev = series_by_year(m, "InventoryNet"), series_by_year(m, "Revenue")
    if not inv:
        return None  # no inventory reported: not applicable
    fy = max(inv)
    prev = previous_year(inv, fy)
    if prev is None or prev not in rev or fy not in rev or inv[prev] <= 0 or rev[prev] <= 0:
        return None
    inv_growth, rev_growth = inv[fy] / inv[prev] - 1, rev[fy] / rev[prev] - 1
    limit = rev_growth + cfg["max_inventory_excess"]
    return Criterion(
        name="Inventory not outgrowing sales",
        passed=inv_growth <= limit,
        actual=round(inv_growth, 4),
        threshold=round(limit, 4),
        detail=f"inventory growth {inv_growth:.1%} vs sales growth {rev_growth:.1%}",
    )


def score_lynch(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    if m.sector in cfg.get("skip_sectors", []):
        return _abstain(f"debt and growth criteria do not fit the {m.sector} sector")
    eps_all = sorted((p.period_end, p.value) for p in m.annual.get("EpsDiluted", []))
    years = recent_years([d for d, _ in eps_all], cfg["window_years"])
    eps = [(d, v) for d, v in eps_all if d in years]
    if len(eps) < cfg["min_history_years"]:
        return _abstain(
            f"needs {cfg['min_history_years']} fiscal years of diluted EPS, found {len(eps)}"
        )
    if m.pe is None or m.price is None or m.price <= 0:
        return _abstain("no P/E (missing price or latest-year EPS)")

    category, growth = classify(eps, cfg)
    if category in ("cyclical", "turnaround", "unknown"):
        reason = {
            "cyclical": "cyclical earnings: a growth-adjusted P/E is not meaningful",
            "turnaround": "turnaround: judged on balance-sheet survival, not growth",
            "unknown": "earnings growth cannot be computed from the history",
        }[category]
        result = _abstain(reason)
        result.evidence = [f"Category: {category}"]
        return result

    dps = series_by_year(m, "DividendsPerShare")
    latest_dps = dps[max(dps)] if dps else 0.0
    dividend_yield = latest_dps / m.price
    criteria: list[Criterion] = []
    assert growth is not None

    if category == SLOW:
        criteria += [
            Criterion(
                name="Dividend yield for income",
                passed=dividend_yield >= cfg["min_yield_slow"],
                actual=round(dividend_yield, 4),
                threshold=cfg["min_yield_slow"],
                detail="latest-year dividend per share / price",
            ),
            Criterion(
                name="Low P/E",
                passed=0 < m.pe <= cfg["max_pe_slow"],
                actual=round(m.pe, 2),
                threshold=cfg["max_pe_slow"],
                detail="latest-year P/E",
            ),
        ]
    else:
        ceiling = cfg["max_peg_fast"] if category == FAST else cfg["max_peg_stalwart"]
        adjusted_growth = (growth + dividend_yield) * 100
        peg = m.pe / adjusted_growth if adjusted_growth > 0 else None
        criteria.append(
            Criterion(
                name="Growth-adjusted P/E (PEG)",
                passed=peg is not None and 0 < peg <= ceiling,
                actual=None if peg is None else round(peg, 3),
                threshold=ceiling,
                detail=(
                    f"P/E {m.pe:.1f} / (growth {growth:.1%} + yield {dividend_yield:.1%}) "
                    f"for a {category}"
                ),
            )
        )
        if category == STALWART:
            criteria.append(
                Criterion(
                    name="P/E not stretched",
                    passed=0 < m.pe <= cfg["max_pe_stalwart"],
                    actual=round(m.pe, 2),
                    threshold=cfg["max_pe_stalwart"],
                    detail="latest-year P/E",
                )
            )
        steps = list(zip(eps, eps[1:], strict=False))
        rising = sum(1 for (_, a), (_, b) in steps if b > a) / len(steps)
        criteria.append(
            Criterion(
                name="EPS rising in most years",
                passed=rising >= cfg["min_rising_eps_share"],
                actual=round(rising, 3),
                threshold=cfg["min_rising_eps_share"],
                detail=f"share of year-over-year EPS increases over {len(steps)} steps",
            )
        )

    d_e = m.debt_to_equity
    criteria.append(
        Criterion(
            name="Low debt",
            passed=d_e is not None and 0 <= d_e <= cfg["max_debt_to_equity"],
            actual=None if d_e is None else round(d_e, 3),
            threshold=cfg["max_debt_to_equity"],
            detail="no data" if d_e is None else "long-term debt / equity",
        )
    )
    if (inv := _inventory_vs_sales(m, cfg)) is not None:
        criteria.append(inv)

    passed = sum(c.passed for c in criteria)
    return ScoreResult(
        philosophy=NAME,
        score=round(100.0 * passed / len(criteria), 1),
        criteria=criteria,
        evidence=[f"Category: {category}, EPS growth {growth:.1%}/yr"],
        explanation=f"{category.capitalize()}: passes {passed} of {len(criteria)} Lynch criteria.",
    )


def lynch_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]["lynch"]
    if cfg.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError("LLM-backed Lynch node is not implemented yet")
    return {"philosophy_results": {NAME: score_lynch(state["metrics"], cfg)}}
