"""All five philosophies together in the graph, plus registry/config consistency."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fin_core.graph import build_graph, load_config
from fin_core.graph.nodes import PHILOSOPHY_NODES
from fin_core.models import AnnualPoint, SecurityMetrics

CFG = load_config()
YEARS = list(range(2015, 2025))


def series(fn):
    return [
        AnnualPoint(period_end=date(y, 12, 31), value=float(fn(i))) for i, y in enumerate(YEARS)
    ]


def last_year(value):
    return [AnnualPoint(period_end=date(2024, 12, 31), value=float(value))]


def complete_company(**overrides) -> SecurityMetrics:
    """A growing, profitable company with every input the five philosophies need."""
    revenue = lambda i: 1000 * 1.12**i  # noqa: E731
    annual = {
        "EpsDiluted": series(lambda i: 1.12**i),
        "DividendsPerShare": last_year(0.5),
        "Revenue": series(revenue),
        "NetIncome": series(lambda i: 100 * 1.12**i),
        "OperatingIncome": series(lambda i: (0.20 + 0.004 * i) * revenue(i)),
        "GrossProfit": series(lambda i: (0.40 + 0.004 * i) * revenue(i)),
        "ResearchAndDevelopmentExpense": series(lambda i: 0.05 * revenue(i)),
        "StockholdersEquity": series(lambda i: 500),
        "DepreciationAmortization": series(lambda i: 30),
        "Capex": series(lambda i: 40),
        "Assets": last_year(1500),
        "AssetsCurrent": last_year(500),
        "LiabilitiesCurrent": last_year(300),
        "Cash": last_year(100),
        "LongTermDebt": last_year(200),
        "LongTermDebtCurrent": last_year(50),
        "PropertyPlantAndEquipmentNet": last_year(400),
        "SharesOutstanding": [
            AnnualPoint(period_end=date(2016, 1, 31), value=100.0),
            AnnualPoint(period_end=date(2025, 1, 31), value=101.0),
        ],
    }
    base = dict(
        ticker="ALL", as_of=datetime(2025, 3, 10, tzinfo=UTC), price=10.0, pe=14.0, pb=1.2,
        current_ratio=2.5, debt_to_equity=0.3, dividend_years=20, earnings_growth=0.05,
        annual=annual,
    )  # fmt: skip
    base.update(overrides)
    return SecurityMetrics(**base)


def run(metrics):
    return build_graph().invoke({"metrics": metrics, "config": CFG, "previous_conviction": None})


def test_registry_and_config_agree():
    assert set(PHILOSOPHY_NODES) == {"graham", "buffett", "lynch", "greenblatt", "fisher"}
    assert set(PHILOSOPHY_NODES) == set(CFG["weights"])
    for name in PHILOSOPHY_NODES:
        assert CFG[name]["impl"] == "deterministic"


def test_all_five_philosophies_evaluate_and_combine_with_equal_weights():
    out = run(complete_company())
    results = out["philosophy_results"]
    assert set(results) == set(PHILOSOPHY_NODES)
    assert all(r.evaluated for r in results.values()), {
        n: r.explanation for n, r in results.items() if not r.evaluated
    }
    mean = sum(r.score for r in results.values()) / 5
    assert out["conviction"] == pytest.approx(round(mean, 1), abs=0.05)
    assert "Not evaluated" not in out["explanation"]
    for name in PHILOSOPHY_NODES:
        assert f"{name} " in out["explanation"]
    # sanity on the individual verdicts for this company (see the per-node tests for the maths)
    assert results["lynch"].score == 100.0  # stalwart, PEG 14 / (12 + 5) = 0.82
    assert results["greenblatt"].score == 100.0
    assert results["fisher"].score == 100.0
    assert out["signal"].kind == "emerging"


def test_philosophies_with_no_history_abstain_and_are_named():
    bare = SecurityMetrics(
        ticker="NEW", as_of=datetime(2025, 3, 10, tzinfo=UTC), pe=10.0, pb=1.2, current_ratio=2.5,
        debt_to_equity=0.4, dividend_years=20, earnings_growth=0.05,
    )  # fmt: skip
    out = run(bare)
    abstained = {n for n, r in out["philosophy_results"].items() if not r.evaluated}
    assert abstained == {"buffett", "lynch", "greenblatt", "fisher"}
    assert out["conviction"] == 100.0  # graham alone
    assert (
        "Not evaluated (insufficient data): buffett, fisher, greenblatt, lynch"
        in out["explanation"]
    )


def test_a_financial_company_skips_greenblatt_only():
    out = run(complete_company(sector="Financials"))
    assert out["philosophy_results"]["greenblatt"].evaluated is False
    assert out["philosophy_results"]["lynch"].evaluated is True
