"""Joel Greenblatt's "magic formula": cheap (earnings yield) and good (return on capital).

The original ranks every stock in a universe on both measures and buys the best combined rank.
This node scores ONE stock against absolute thresholds (``min_earnings_yield``,
``min_return_on_capital``), so it works for a single ticker and without stored scores. It is a
proxy for the ranking, not the ranking itself.

* earnings yield = EBIT / enterprise value, EBIT = operating income,
  EV = market cap + total debt - cash (total debt already includes the portion due within a year)
* return on capital = EBIT / (net working capital + net fixed assets), with net working capital
  = (current assets - cash) - (current liabilities - debt due within a year)

Missing cash or debt is treated as zero only when that year's balance sheet exists (noted in the
criterion). Missing fixed assets leave the return-on-capital criterion without data rather than
inflating it. Non-positive invested capital means a capital-light business and passes. The node
abstains for the sectors the original excludes (financials, utilities), for multi-class issuers
(market value unreliable), and without a price, share count or operating income.
"""

from __future__ import annotations

from typing import Any

from fin_core.graph.data import recent_years, series_by_year
from fin_core.models import Criterion, ScoreResult, SecurityMetrics

NAME = "greenblatt"


def _abstain(why: str) -> ScoreResult:
    return ScoreResult(
        philosophy=NAME, score=0.0, evaluated=False, explanation=f"Not evaluated: {why}."
    )


def _no_data(name: str, threshold: float | None, why: str) -> Criterion:
    return Criterion(name=name, passed=False, threshold=threshold, detail=f"no data: {why}")


def score_greenblatt(m: SecurityMetrics, cfg: dict[str, Any]) -> ScoreResult:
    if m.sector in cfg.get("skip_sectors", []):
        return _abstain(f"the formula excludes the {m.sector} sector")
    if m.multi_class:
        return _abstain("multiple share classes make the market value unreliable")
    ebit_by_year = series_by_year(m, "OperatingIncome")
    shares = m.annual.get("SharesOutstanding", [])
    if not ebit_by_year:
        return _abstain("no operating income")
    if m.price is None or not shares or shares[-1].value <= 0:
        return _abstain("no price or share count")

    fy = max(ebit_by_year)
    ebit = ebit_by_year[fy]
    ca, cl = series_by_year(m, "AssetsCurrent"), series_by_year(m, "LiabilitiesCurrent")
    assets, ppe = series_by_year(m, "Assets"), series_by_year(m, "PropertyPlantAndEquipmentNet")
    balance_sheet = fy in ca or fy in assets
    notes: list[str] = []

    def stock(concept: str, label: str) -> float | None:
        values = series_by_year(m, concept)
        if fy in values:
            return values[fy]
        if balance_sheet:
            notes.append(f"no {label} reported, treated as 0")
            return 0.0
        return None

    cash = stock("Cash", "cash")
    debt = stock("TotalDebt", "debt")  # already includes the portion due within a year
    debt_cur = stock("LongTermDebtCurrent", "current debt")  # only to adjust working capital

    # --- earnings yield -------------------------------------------------------------------
    ey_name, roc_name = "Earnings yield", "Return on capital"
    market_cap = m.price * shares[-1].value
    if cash is None or debt is None:
        ey = _no_data(ey_name, cfg["min_earnings_yield"], f"no balance sheet for {fy}")
    else:
        ev = market_cap + debt - cash
        if ev <= 0:
            ey = Criterion(
                name=ey_name,
                passed=False,
                threshold=cfg["min_earnings_yield"],
                detail="enterprise value is not positive (cash exceeds market cap plus debt)",
            )
        else:
            ey = Criterion(
                name=ey_name,
                passed=ebit / ev >= cfg["min_earnings_yield"],
                actual=round(ebit / ev, 4),
                threshold=cfg["min_earnings_yield"],
                detail=f"EBIT / enterprise value ({ev:,.0f})" + "".join(f"; {n}" for n in notes),
            )

    # --- return on capital ----------------------------------------------------------------
    if fy not in ca or fy not in cl or fy not in ppe or cash is None or debt_cur is None:
        missing = [n for n, s in (("current assets", ca), ("current liabilities", cl),
                                  ("net fixed assets", ppe)) if fy not in s]  # fmt: skip
        roc = _no_data(roc_name, cfg["min_return_on_capital"], f"missing {', '.join(missing)}")
    else:
        capital = (ca[fy] - cash) - (cl[fy] - debt_cur) + ppe[fy]
        if capital <= 0:
            roc = Criterion(
                name=roc_name,
                passed=ebit > 0,
                threshold=cfg["min_return_on_capital"],
                detail=f"no net operating capital ({capital:,.0f}): capital-light business",
            )
        else:
            roc = Criterion(
                name=roc_name,
                passed=ebit / capital >= cfg["min_return_on_capital"],
                actual=round(ebit / capital, 4),
                threshold=cfg["min_return_on_capital"],
                detail=f"EBIT / invested capital ({capital:,.0f})",
            )

    # --- steady profits -------------------------------------------------------------------
    n = cfg["profit_years"]
    last = recent_years(list(ebit_by_year), 10)[-n:]
    if len(last) < n:
        steady = _no_data("Profitable in each recent year", None, f"only {len(last)} years of EBIT")
    else:
        steady = Criterion(
            name="Profitable in each recent year",
            passed=all(ebit_by_year[y] > 0 for y in last),
            detail=f"EBIT positive in each of the last {n} fiscal years",
        )

    criteria = [ey, roc, steady]
    passed = sum(c.passed for c in criteria)
    return ScoreResult(
        philosophy=NAME,
        score=round(100.0 * passed / len(criteria), 1),
        criteria=criteria,
        evidence=[f"Market capitalisation: {market_cap:,.0f}; fiscal year {fy}"],
        explanation=(
            f"Passes {passed} of {len(criteria)} magic-formula criteria "
            "(absolute thresholds, not a cross-sectional rank)."
        ),
    )


def greenblatt_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]["greenblatt"]
    if cfg.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError("LLM-backed Greenblatt node is not implemented yet")
    return {"philosophy_results": {NAME: score_greenblatt(state["metrics"], cfg)}}
