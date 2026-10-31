"""Lynch node and the shared cagr helper. Hand-computed expectations:

cagr compares the mean of the first 3 and last 3 values of the (<= 10-year) window over the
distance between the middle years. For a series growing at g per year the two groups differ by
exactly (1+g)**7 over 7 years (10 points) or (1+g)**2 over 2 years (5 points), so cagr == g
(up to leap-day noise of ~0.02%).

EPS growing 25%/yr, P/E 20, no dividend  -> fast grower, PEG = 20 / 25   = 0.80  (<= 1.0)
with a 2.5% yield                        -> PEG = 20 / (25 + 2.5)         = 0.727
EPS growing 15%/yr, P/E 16               -> stalwart, PEG = 16 / 15       = 1.067 (<= 1.2)
EPS growing  5%/yr, yield 4%, P/E 12     -> slow grower: yield >= 3%, P/E <= 15
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fin_core.graph import load_config
from fin_core.graph.data import cagr
from fin_core.graph.nodes.lynch import classify, score_lynch
from fin_core.models import AnnualPoint, ScoreResult, SecurityMetrics

CFG = load_config()["lynch"]
YEARS = list(range(2015, 2025))


def FY(y: int) -> date:  # noqa: N802
    return date(y, 12, 31)


def pts(values, years=None) -> list[AnnualPoint]:
    years = years or YEARS[-len(values) :]
    return [
        AnnualPoint(period_end=FY(y), value=float(v)) for y, v in zip(years, values, strict=True)
    ]


def growing(rate: float, n: int = 10, start: float = 1.0) -> list[float]:
    return [start * (1 + rate) ** i for i in range(n)]


def company(eps, *, price=100.0, pe=20.0, dps=None, debt_to_equity=0.5, annual=None, **kw):
    a = {"EpsDiluted": pts(eps)}
    if dps is not None:
        a["DividendsPerShare"] = pts([dps], [2024])
    a.update(annual or {})
    return SecurityMetrics(
        ticker="L", as_of=datetime(2025, 3, 10, tzinfo=UTC), price=price, pe=pe,
        debt_to_equity=debt_to_equity, annual=a, **kw,
    )  # fmt: skip


def criterion(r: ScoreResult, name: str):
    return next(c for c in r.criteria if c.name == name)


def eps_points(values):
    return [(FY(y), v) for y, v in zip(YEARS[-len(values) :], values, strict=True)]


# --- cagr ----------------------------------------------------------------------------------


def test_cagr_recovers_the_growth_rate_of_a_geometric_series():
    assert cagr(eps_points(growing(0.10))) == pytest.approx(0.10, abs=2e-3)
    assert cagr(eps_points(growing(0.25))) == pytest.approx(0.25, abs=2e-3)
    assert cagr(eps_points(growing(-0.05))) == pytest.approx(-0.05, abs=2e-3)


def test_cagr_with_five_points_uses_overlapping_groups():
    # [100, 110, 121, 133.1, 146.41]: mean(first 3) = 110.33, mean(last 3) = 133.50, 2 years apart
    five = [(FY(y), v) for y, v in zip(range(2020, 2025), growing(0.10, 5, 100.0), strict=True)]
    assert cagr(five) == pytest.approx(0.10, abs=2e-3)


def test_cagr_needs_enough_points_and_positive_averages():
    assert cagr(eps_points(growing(0.1, 4))) is None
    assert cagr([]) is None
    assert cagr(eps_points([-1, -1, -1, 1, 1, 1])) is None  # non-positive base
    assert cagr(eps_points([1, 1, 1, -1, -1, -1])) is None  # non-positive end


def test_cagr_ignores_points_older_than_ten_years():
    old = [(FY(y), 1.0) for y in (2005, 2006, 2007)]  # flat history, then 10% growth
    assert cagr(old + eps_points(growing(0.10))) == pytest.approx(0.10, abs=2e-3)


# --- categories ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rate, category",
    [(0.22, "fast grower"), (0.18, "stalwart"), (0.08, "slow grower"), (-0.05, "slow grower")],
)
def test_category_follows_the_growth_rate(rate, category):
    got, growth = classify(eps_points(growing(rate)), CFG)
    assert got == category and growth == pytest.approx(rate, abs=2e-3)


def test_cyclical_earnings_are_detected_by_repeated_big_drops():
    eps = [2, 3, 1.5, 3, 4, 2, 3, 5, 2.5, 4]  # drops of 50% three times
    assert classify(eps_points(eps), CFG) == ("cyclical", None)


def test_a_recent_loss_followed_by_profit_is_a_turnaround():
    eps = [1, 1.1, 1.2, 1.3, 1.4, 1.5, -0.5, -0.2, 0.3, 0.8]
    assert classify(eps_points(eps), CFG) == ("turnaround", None)


# --- scoring -------------------------------------------------------------------------------


def test_fast_grower_with_a_low_peg_passes_everything():
    r = score_lynch(company(growing(0.25), pe=20.0), CFG)
    assert r.evaluated and r.score == 100.0 and len(r.criteria) == 3
    peg = criterion(r, "Growth-adjusted P/E (PEG)")
    assert peg.actual == pytest.approx(0.80, abs=2e-3) and peg.threshold == 1.0
    assert "fast grower" in r.explanation.lower()
    assert "EPS growth 25.0%/yr" in r.evidence[0] or "EPS growth 25.1%/yr" in r.evidence[0]


def test_a_high_pe_fails_the_peg():
    r = score_lynch(company(growing(0.25), pe=40.0), CFG)  # PEG 1.6
    assert {c.name for c in r.failed_criteria} == {"Growth-adjusted P/E (PEG)"}
    assert r.score == pytest.approx(100 * 2 / 3, abs=0.1)


def test_the_dividend_yield_lowers_the_peg():
    r = score_lynch(company(growing(0.25), pe=20.0, dps=2.5), CFG)  # yield 2.5% on price 100
    peg = criterion(r, "Growth-adjusted P/E (PEG)")
    assert peg.actual == pytest.approx(20 / 27.5, abs=3e-3)  # 0.727
    assert "yield 2.5%" in peg.detail


def test_stalwart_has_a_looser_peg_and_a_pe_ceiling():
    ok = score_lynch(company(growing(0.15), pe=16.0), CFG)  # PEG 1.067 <= 1.2, P/E 16 <= 25
    assert ok.score == 100.0 and len(ok.criteria) == 4
    pricey = score_lynch(company(growing(0.15), pe=20.0), CFG)  # PEG 1.333 > 1.2
    assert {c.name for c in pricey.failed_criteria} == {"Growth-adjusted P/E (PEG)"}
    stretched = score_lynch(company(growing(0.15), pe=30.0), CFG)  # also P/E above 25
    assert {"Growth-adjusted P/E (PEG)", "P/E not stretched"} <= {
        c.name for c in stretched.failed_criteria
    }


def test_slow_grower_is_judged_on_yield_and_pe_not_peg():
    r = score_lynch(company(growing(0.05), pe=12.0, dps=4.0), CFG)  # yield 4%
    assert {c.name for c in r.criteria} == {"Dividend yield for income", "Low P/E", "Low debt"}
    assert r.score == 100.0
    thin = score_lynch(company(growing(0.05), pe=12.0, dps=1.0), CFG)  # yield 1% < 3%
    assert {c.name for c in thin.failed_criteria} == {"Dividend yield for income"}


def test_eps_must_rise_in_most_years_for_growers():
    # mean(1.0, 1.3, 1.1) = 1.133 -> mean(2.5, 2.1, 3.2) = 2.6 over 7 years = 12.6%/yr: a stalwart
    # whose EPS nevertheless fell every other year (no fall is as big as the 20% cyclical test)
    zigzag = [1.0, 1.3, 1.1, 1.6, 1.4, 2.0, 1.7, 2.5, 2.1, 3.2]
    r = score_lynch(company(zigzag, pe=10.0), CFG)
    assert "stalwart" in r.explanation.lower()
    c = criterion(r, "EPS rising in most years")
    assert not c.passed and c.actual == pytest.approx(5 / 9, abs=1e-3)  # 5 increases in 9 steps


def test_debt_must_be_low_and_known():
    heavy = score_lynch(company(growing(0.25), debt_to_equity=1.5), CFG)
    assert not criterion(heavy, "Low debt").passed
    unknown = score_lynch(company(growing(0.25), debt_to_equity=None), CFG)
    assert (
        not criterion(unknown, "Low debt").passed
        and criterion(unknown, "Low debt").detail == "no data"
    )
    negative_equity = score_lynch(company(growing(0.25), debt_to_equity=-0.4), CFG)
    assert not criterion(negative_equity, "Low debt").passed


def test_inventory_may_not_outgrow_sales_by_more_than_the_allowance():
    def with_inventory(inv_now):
        annual = {
            "InventoryNet": pts([100, inv_now], [2023, 2024]),
            "Revenue": pts([1000, 1050], [2023, 2024]),  # sales +5% -> allowance 5% + 10% = 15%
        }
        return score_lynch(company(growing(0.25), annual=annual), CFG)

    ok = criterion(with_inventory(110), "Inventory not outgrowing sales")  # +10%
    assert ok.passed and ok.actual == pytest.approx(0.10)
    assert not criterion(with_inventory(130), "Inventory not outgrowing sales").passed  # +30%


def test_no_inventory_means_the_criterion_is_not_applicable():
    r = score_lynch(company(growing(0.25)), CFG)
    assert "Inventory not outgrowing sales" not in {c.name for c in r.criteria}


# --- abstentions ---------------------------------------------------------------------------


def test_cyclicals_and_turnarounds_abstain_and_say_why():
    cyc = score_lynch(company([2, 3, 1.5, 3, 4, 2, 3, 5, 2.5, 4]), CFG)
    assert cyc.evaluated is False and "cyclical" in cyc.explanation
    assert cyc.evidence == ["Category: cyclical"]
    turn = score_lynch(company([1, 1.1, 1.2, 1.3, 1.4, 1.5, -0.5, -0.2, 0.3, 0.8]), CFG)
    assert turn.evaluated is False and "turnaround" in turn.explanation


def test_too_little_history_or_no_pe_abstains():
    short = score_lynch(company(growing(0.2, 4)), CFG)
    assert short.evaluated is False and "found 4" in short.explanation
    no_pe = score_lynch(company(growing(0.25), pe=None), CFG)
    assert no_pe.evaluated is False and "no P/E" in no_pe.explanation
    no_price = score_lynch(company(growing(0.25), price=None), CFG)
    assert no_price.evaluated is False


def test_banks_and_insurers_skip_lynch():
    r = score_lynch(company(growing(0.25), sector="Financials"), CFG)
    assert r.evaluated is False and "Financials" in r.explanation
    assert score_lynch(company(growing(0.25), sector="Technology"), CFG).evaluated is True
