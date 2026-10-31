"""Quality and distress guardrails: Piotroski F-score and Altman Z''-score.

They are not philosophies: they do not add to the conviction. They *cap* it when a company looks
weak or distressed, so a cheap-looking value trap cannot earn a buy. Both abstain for
financial-sector companies (balance-sheet ratios mean something different for banks and
insurers) and when there is too little data.

Piotroski (9 binary signals over the latest two fiscal years). Simplifications, kept explicit:
ROA uses end-of-year assets for both years (the original uses beginning-of-year assets), and a
signal whose inputs are missing is *not counted* instead of failing. The score is only evaluated
when enough signals are available (``min_evaluable_signals``).

Altman Z'' (the four-factor variant for non-manufacturers; no sales term and no market value, so
it is robust to share-class problems):
    Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
    X1 working capital / assets, X2 retained earnings / assets, X3 EBIT / assets
    (EBIT approximated by operating income), X4 book equity / total liabilities.
Zones: above 2.6 safe, 1.1-2.6 grey, below 1.1 distress. Distress vetoes only when the
company is not visibly profitable and cash-generative (see ``guardrails_node``).

Beneish M-score (earnings manipulation) needs receivables, SG&A and PP&E and is not built yet.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fin_core.graph.data import previous_year, series_by_year, shares_for_year
from fin_core.models import Criterion, ScoreResult, SecurityMetrics

PIOTROSKI = "piotroski"
ALTMAN = "altman_z"


def _abstain(name: str, why: str) -> ScoreResult:
    return ScoreResult(
        philosophy=name, score=0.0, evaluated=False, explanation=f"Not evaluated: {why}."
    )


def _signal(name: str, ok: bool | None, detail: str, actual: float | None = None) -> Criterion:
    if ok is None:
        return Criterion(name=name, passed=False, detail=f"no data: {detail}")
    return Criterion(name=name, passed=ok, actual=actual, detail=detail)


def _ratio(num: dict[date, float], den: dict[date, float], y: date | None) -> float | None:
    if y is None or y not in num or y not in den or den[y] <= 0:
        return None
    return num[y] / den[y]


def piotroski(m: SecurityMetrics, cfg: dict[str, Any]) -> tuple[ScoreResult, int, int]:
    """Returns ``(result, signals_passed, signals_evaluable)``."""
    ni, ta = series_by_year(m, "NetIncome"), series_by_year(m, "Assets")
    fy = max((y for y in ni if y in ta), default=None)
    if fy is None:
        return _abstain(PIOTROSKI, "no fiscal year with net income and total assets"), 0, 0
    prev = previous_year(ta, fy)

    cfo = series_by_year(m, "NetCashFromOperating")
    ltd = series_by_year(m, "TotalDebt")
    ca, cl = series_by_year(m, "AssetsCurrent"), series_by_year(m, "LiabilitiesCurrent")
    gp, rev = series_by_year(m, "GrossProfit"), series_by_year(m, "Revenue")

    def debt(y: date | None) -> float | None:
        if y is None or y not in ta:
            return None
        return ltd.get(y, 0.0)  # balance sheet exists but no debt reported: zero (as in metrics)

    roa, roa_prev = _ratio(ni, ta, fy), _ratio(ni, ta, prev)
    cfo_ta = _ratio(cfo, ta, fy)
    lev = _ratio({y: debt(y) for y in (fy, prev) if debt(y) is not None}, ta, fy)
    lev_prev = _ratio({y: debt(y) for y in (fy, prev) if debt(y) is not None}, ta, prev)
    cr, cr_prev = _ratio(ca, cl, fy), _ratio(ca, cl, prev)
    gm, gm_prev = _ratio(gp, rev, fy), _ratio(gp, rev, prev)
    at, at_prev = _ratio(rev, ta, fy), _ratio(rev, ta, prev)
    sh, sh_prev = shares_for_year(m, fy), shares_for_year(m, prev) if prev else None

    def gt(a, b):
        return None if a is None or b is None else a > b

    signals = [
        _signal("Positive return on assets", None if roa is None else roa > 0, "ROA > 0", roa),
        _signal("Positive operating cash flow", None if fy not in cfo else cfo[fy] > 0, "CFO > 0"),
        _signal("Rising return on assets", gt(roa, roa_prev), "ROA above prior year", roa),
        _signal("Cash flow exceeds earnings", gt(cfo_ta, roa), "CFO / assets > ROA", cfo_ta),
        _signal(
            "Falling leverage",
            None if lev is None or lev_prev is None else (lev < lev_prev or lev == 0),
            "long-term debt / assets below prior year (or no debt)",
            lev,
        ),
        _signal("Rising current ratio", gt(cr, cr_prev), "current ratio above prior year", cr),
        _signal(
            "No new shares issued",
            None if sh is None or sh_prev is None else sh <= sh_prev,
            "cover-page share count not above prior year",
            sh,
        ),
        _signal("Rising gross margin", gt(gm, gm_prev), "gross margin above prior year", gm),
        _signal("Rising asset turnover", gt(at, at_prev), "revenue / assets above prior year", at),
    ]
    evaluable = sum(not s.detail.startswith("no data") for s in signals)
    passes = sum(s.passed for s in signals)
    minimum = cfg["piotroski"]["min_evaluable_signals"]
    if evaluable < minimum:
        result = _abstain(PIOTROSKI, f"only {evaluable} of 9 signals have data (need {minimum})")
        result.criteria = signals
        return result, passes, evaluable
    return (
        ScoreResult(
            philosophy=PIOTROSKI,
            score=round(100.0 * passes / evaluable, 1),
            criteria=signals,
            evidence=[f"F-score {passes} of {evaluable} evaluable signals (fiscal year {fy})"],
            explanation=f"Piotroski F-score {passes} of {evaluable} evaluable signals.",
        ),
        passes,
        evaluable,
    )


def altman(m: SecurityMetrics, cfg: dict[str, Any]) -> tuple[ScoreResult, float | None]:
    """Returns ``(result, z)``; ``z`` is None when it could not be computed."""
    needed = (
        "Assets",
        "AssetsCurrent",
        "LiabilitiesCurrent",
        "RetainedEarnings",
        "OperatingIncome",
        "StockholdersEquity",
        "Liabilities",
    )
    data = {c: series_by_year(m, c) for c in needed}
    common = set.intersection(*(set(d) for d in data.values()))
    if not common:
        missing = [c for c in needed if not data[c]]
        why = f"missing {', '.join(missing)}" if missing else "no fiscal year has every input"
        return _abstain(ALTMAN, why), None
    fy = max(common)
    ta, tl = data["Assets"][fy], data["Liabilities"][fy]
    if ta <= 0 or tl <= 0:
        return _abstain(ALTMAN, "total assets or liabilities not positive"), None

    x1 = (data["AssetsCurrent"][fy] - data["LiabilitiesCurrent"][fy]) / ta
    x2 = data["RetainedEarnings"][fy] / ta
    x3 = data["OperatingIncome"][fy] / ta
    x4 = data["StockholdersEquity"][fy] / tl
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

    distress, safe = cfg["altman"]["distress_below"], cfg["altman"]["safe_above"]
    zone = "distress" if z < distress else "safe" if z > safe else "grey"
    score = max(0.0, min(100.0, 100.0 * (z - distress) / (safe - distress)))
    return (
        ScoreResult(
            philosophy=ALTMAN,
            score=round(score, 1),
            criteria=[
                Criterion(
                    name="Outside the distress zone",
                    passed=z >= distress,
                    actual=round(z, 3),
                    threshold=distress,
                    detail=f"Altman Z'' in fiscal year {fy}",
                ),
                Criterion(
                    name="In the safe zone",
                    passed=z > safe,
                    actual=round(z, 3),
                    threshold=safe,
                    detail=f"Altman Z'' in fiscal year {fy}",
                ),
            ],
            evidence=[
                f"Z'' = {z:.2f} ({zone} zone)",
                f"X1 working capital/assets {x1:.3f}, X2 retained earnings/assets {x2:.3f}, "
                f"X3 EBIT/assets {x3:.3f}, X4 equity/liabilities {x4:.3f}",
            ],
            explanation=f"Altman Z'' {z:.2f}: {zone} zone.",
        ),
        z,
    )


def _profitable_and_cash_generative(m: SecurityMetrics) -> bool:
    """Latest fiscal year has positive net income AND positive operating cash flow."""
    ni, cfo = series_by_year(m, "NetIncome"), series_by_year(m, "NetCashFromOperating")
    common = [y for y in ni if y in cfo]
    return bool(common) and ni[max(common)] > 0 and cfo[max(common)] > 0


def guardrails_node(state: dict[str, Any]) -> dict[str, Any]:
    """Cap conviction (via ``guardrail_veto``) for very weak or distressed companies.

    Altman Z'' alone can be fooled: a profitable company that returns cash through large
    buybacks has negative retained earnings and equity, which pushes Z'' into the distress
    zone (McDonald's, Starbucks, ...). So with ``altman.require_corroboration`` the distress
    zone vetoes only if the company is NOT visibly profitable and cash-generative; otherwise
    it becomes a note. A very weak Piotroski score vetoes on its own. When health cannot be
    shown (no cash-flow data) the distress zone still vetoes.
    """
    cfg = state["config"].get("guardrails", {})
    m: SecurityMetrics = state["metrics"]
    empty = {"guardrail_veto": False, "guardrail_reasons": [], "guardrail_notes": []}
    if not cfg.get("enabled", True):
        return {**empty, "guardrail_results": {}}
    if m.sector in cfg.get("skip_sectors", []):
        why = f"not meaningful for the {m.sector} sector"
        return {
            **empty,
            "guardrail_results": {
                PIOTROSKI: _abstain(PIOTROSKI, why),
                ALTMAN: _abstain(ALTMAN, why),
            },
        }

    p_result, passes, evaluable = piotroski(m, cfg)
    a_result, z = altman(m, cfg)
    reasons: list[str] = []
    notes: list[str] = []

    weak = (
        p_result.evaluated
        and evaluable > 0
        and passes / evaluable <= cfg["piotroski"]["veto_share_at_or_below"]
    )
    if weak:
        reasons.append(f"Piotroski F-score {passes} of {evaluable} evaluable signals")

    if z is not None and z < cfg["altman"]["distress_below"]:
        exempt = (
            cfg["altman"].get("require_corroboration", True)
            and not weak
            and _profitable_and_cash_generative(m)
        )
        if exempt:
            note = (
                f"Altman Z'' {z:.2f} is in the distress zone but not corroborated: net income "
                "and operating cash flow are positive (typical of buyback-driven negative equity)"
            )
            notes.append(note)
            a_result.evidence.append(note)
        else:
            reasons.append(f"Altman Z'' {z:.2f} is in the distress zone")
    return {
        "guardrail_veto": bool(reasons),
        "guardrail_results": {PIOTROSKI: p_result, ALTMAN: a_result},
        "guardrail_reasons": reasons,
        "guardrail_notes": notes,
    }
