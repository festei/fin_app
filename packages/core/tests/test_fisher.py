"""Fisher node. Base company (2015-2024), hand-computed:

revenue 1000 * 1.12**i, net income 100 * 1.12**i  -> smoothed growth 12%/yr (both >= 10%)
operating margin 20% + 0.4 points per year        -> last-3 average 23.2%, first-3 average 20.4%
gross margin 40% + 0.4 points per year            -> same +2.8 point improvement
R&D 5% of revenue (>= 3%), shares 100 -> 101 over 9 years (0.11%/yr dilution)
=> all 7 criteria pass
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fin_core.graph import load_config
from fin_core.graph.nodes.fisher import score_fisher
from fin_core.models import AnnualPoint, ScoreResult, SecurityMetrics

CFG = load_config()["fisher"]
YEARS = list(range(2015, 2025))


def series(fn, years=YEARS):
    return [
        AnnualPoint(period_end=date(y, 12, 31), value=float(fn(i))) for i, y in enumerate(years)
    ]


def shares(first: float, last: float, first_date=date(2016, 1, 31), last_date=date(2025, 1, 31)):
    return [
        AnnualPoint(period_end=first_date, value=first),
        AnnualPoint(period_end=last_date, value=last),
    ]


def revenue(i):
    return 1000 * 1.12**i


def company(**overrides) -> SecurityMetrics:
    annual = {
        "Revenue": series(revenue),
        "NetIncome": series(lambda i: 100 * 1.12**i),
        "OperatingIncome": series(lambda i: (0.20 + 0.004 * i) * revenue(i)),
        "GrossProfit": series(lambda i: (0.40 + 0.004 * i) * revenue(i)),
        "ResearchAndDevelopmentExpense": series(lambda i: 0.05 * revenue(i)),
        "SharesOutstanding": shares(100, 101),
    }
    annual.update(overrides.pop("annual", {}))
    return SecurityMetrics(
        ticker="F", as_of=datetime(2025, 3, 10, tzinfo=UTC), annual=annual, **overrides
    )


def criterion(r: ScoreResult, name: str):
    return next(c for c in r.criteria if c.name == name)


def failed(r: ScoreResult) -> set[str]:
    return {c.name for c in r.failed_criteria}


SALES, PROFIT, RD = "Sustained sales growth", "Sustained profit growth", "R&D intensity"
LEVEL, TREND = "Worthwhile operating margin", "Operating margin not deteriorating"
GROSS, DILUTION = "Gross margin not deteriorating", "No heavy dilution"


def test_base_company_passes_every_criterion():
    r = score_fisher(company(), CFG)
    assert r.evaluated and r.score == 100.0 and len(r.criteria) == 7
    assert criterion(r, SALES).actual == pytest.approx(0.12, abs=2e-3)
    assert criterion(r, PROFIT).actual == pytest.approx(0.12, abs=2e-3)
    assert criterion(r, RD).actual == pytest.approx(0.05, abs=1e-6)
    assert criterion(r, LEVEL).actual == pytest.approx(0.232, abs=1e-4)
    assert criterion(r, TREND).actual == pytest.approx(0.028, abs=1e-4)
    assert criterion(r, GROSS).actual == pytest.approx(0.028, abs=1e-4)
    assert criterion(r, DILUTION).actual == pytest.approx(1.01 ** (1 / 9.0) - 1, abs=2e-4)
    assert "qualitative points are not scored" in r.evidence[0]


def test_no_reported_rd_fails_as_no_data_and_costs_a_point():
    r = score_fisher(company(annual={"ResearchAndDevelopmentExpense": []}), CFG)
    c = criterion(r, RD)
    assert not c.passed and c.detail == "no data: no R&D expense reported"
    assert failed(r) == {RD} and r.score == pytest.approx(100 * 6 / 7, abs=0.1)


def test_gross_margin_is_not_applicable_without_gross_profit():
    r = score_fisher(company(annual={"GrossProfit": []}), CFG)
    assert GROSS not in {c.name for c in r.criteria}
    assert len(r.criteria) == 6 and r.score == 100.0


def test_flat_sales_and_shrinking_profit_fail_the_growth_criteria():
    r = score_fisher(
        company(
            annual={"Revenue": series(lambda i: 1000), "NetIncome": series(lambda i: 100 * 0.95**i)}
        ),
        CFG,
    )
    assert {SALES, PROFIT} <= failed(r)
    assert criterion(r, SALES).actual == pytest.approx(0.0, abs=1e-9)
    assert criterion(r, PROFIT).actual == pytest.approx(-0.05, abs=2e-3)


def test_losses_make_profit_growth_not_computable():
    r = score_fisher(company(annual={"NetIncome": series(lambda i: -10)}), CFG)
    c = criterion(r, PROFIT)
    assert not c.passed and c.detail.startswith("no data")


def test_falling_margins_fail_the_trend_but_may_still_pass_the_level():
    # margin 30% -> 21%: last-3 average 22.0%, first-3 average 29.0%
    falling = series(lambda i: (0.30 - 0.01 * i) * revenue(i))
    r = score_fisher(company(annual={"OperatingIncome": falling}), CFG)
    assert failed(r) == {TREND}
    assert criterion(r, TREND).actual == pytest.approx(-0.07, abs=1e-4)
    assert criterion(r, LEVEL).passed


def test_a_thin_margin_fails_the_level():
    thin = series(lambda i: (0.05 + 0.001 * i) * revenue(i))  # last-3 average 5.8%
    r = score_fisher(company(annual={"OperatingIncome": thin}), CFG)
    assert LEVEL in failed(r) and TREND not in failed(r)
    assert criterion(r, LEVEL).actual == pytest.approx(0.058, abs=1e-4)


def test_heavy_dilution_fails():
    r = score_fisher(company(annual={"SharesOutstanding": shares(100, 130)}), CFG)
    c = criterion(r, DILUTION)  # 1.3 ** (1 / 9.0) - 1 = 2.96% a year
    assert not c.passed and c.actual == pytest.approx(1.3 ** (1 / 9.0) - 1, abs=2e-4)
    assert failed(r) == {DILUTION}


def test_dilution_needs_share_counts_spanning_two_years():
    one = score_fisher(company(annual={"SharesOutstanding": shares(100, 100)[:1]}), CFG)
    assert criterion(one, DILUTION).detail.startswith("no data")
    close = shares(100, 101, date(2024, 1, 31), date(2025, 1, 31))  # only a year apart
    assert criterion(
        score_fisher(company(annual={"SharesOutstanding": close}), CFG), DILUTION
    ).detail.startswith("no data")


def test_too_little_history_abstains():
    r = score_fisher(
        company(annual={"Revenue": series(revenue, YEARS[-4:])}),
        CFG,
    )
    assert r.evaluated is False and "found 4" in r.explanation
    assert (
        score_fisher(
            SecurityMetrics(ticker="N", as_of=datetime(2025, 3, 10, tzinfo=UTC)), CFG
        ).evaluated
        is False
    )
