"""Buffett node tests. Base company ("quality"), values hand-computed:

net income 100..200 (2015-2024), equity 500, revenue 1000, operating income 250 (margin 25%),
D&A 30, capex 40, debt 200 in 2024, 100 shares at a price of 10.

* ROE = NI / 500 = 20%..40%, always above 15%
* debt payback = 200 / 200 = 1.0 year
* capex / net income (2020-2024) = 40 / mean(150, 160, 170, 180, 200) = 40 / 172 = 0.2326
* owner earnings 2022-2024 = NI - 10 = 160, 170, 190 -> mean 173.33
* intrinsic value = 173.33 / 0.10 = 1733.3; market cap = 10 * 100 = 1000
* margin of safety = 1 - 1000 / 1733.3 = 0.4231
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fin_core.db import repo
from fin_core.db.session import session_scope
from fin_core.graph import build_graph, load_config
from fin_core.graph.build import aggregate_node, decide_node
from fin_core.graph.nodes.buffett import score_buffett
from fin_core.models import AnnualPoint, ScoreResult, SecurityMetrics
from fin_core.providers.edgar_concepts import CONCEPT_TAGS
from fin_core.scoring import score_security

CFG = load_config()
BUFFETT_CFG = CFG["buffett"]
YEARS = list(range(2015, 2025))
NET_INCOME = [100, 110, 120, 130, 140, 150, 160, 170, 180, 200]
FY = lambda y: date(y, 12, 31)  # noqa: E731


def points(values: dict[int, float] | list[float], years=YEARS):
    items = values.items() if isinstance(values, dict) else zip(years, values, strict=True)
    return [AnnualPoint(period_end=FY(y), value=float(v)) for y, v in items]


def quality(**overrides) -> SecurityMetrics:
    annual = {
        "NetIncome": points(NET_INCOME),
        "StockholdersEquity": points([500] * 10),
        "Revenue": points([1000] * 10),
        "OperatingIncome": points([250] * 10),
        "DepreciationAmortization": points([30] * 10),
        "Capex": points([40] * 10),
        "LongTermDebt": points({2024: 200}),
        "Assets": points({2024: 2000}),
        "SharesOutstanding": [AnnualPoint(period_end=date(2025, 1, 31), value=100.0)],
    }
    annual.update(overrides.pop("annual", {}))
    base = dict(ticker="QUAL", as_of=datetime(2025, 3, 10, tzinfo=UTC), price=10.0, annual=annual)
    base.update(overrides)
    return SecurityMetrics(**base)


def criterion(result: ScoreResult, name: str):
    return next(c for c in result.criteria if c.name == name)


def failed(result: ScoreResult) -> set[str]:
    return {c.name for c in result.failed_criteria}


MOS = "Margin of safety on owner earnings"


def test_quality_company_passes_every_criterion():
    r = score_buffett(quality(), BUFFETT_CFG)
    assert r.evaluated and r.score == 100.0 and len(r.criteria) == 7
    assert criterion(r, MOS).actual == pytest.approx(0.4231, abs=1e-4)
    assert criterion(r, "Low capital intensity").actual == pytest.approx(40 / 172, abs=1e-3)
    assert criterion(r, "Debt repayable from earnings").actual == 1.0
    assert any("Owner earnings (3-year average): 173" in e for e in r.evidence)
    assert any("Intrinsic value" in e and "1,733" in e for e in r.evidence)
    assert any("Market capitalisation: 1,000" in e for e in r.evidence)


def test_expensive_price_fails_only_the_margin_of_safety():
    r = score_buffett(quality(price=15.0), BUFFETT_CFG)  # market cap 1500 -> margin 0.1346 < 0.25
    assert failed(r) == {MOS}
    assert criterion(r, MOS).actual == pytest.approx(0.1346, abs=1e-4)
    assert r.score == pytest.approx(100 * 6 / 7, abs=0.1)


def test_low_return_on_equity_fails_durability():
    r = score_buffett(quality(annual={"StockholdersEquity": points([1500] * 10)}), BUFFETT_CFG)
    c = criterion(r, "Durable return on equity")  # 200/1500 = 13% at best
    assert not c.passed and c.actual == 0.0


def test_one_collapsed_margin_year_fails_the_floor_but_not_the_average():
    oi = [250] * 10
    oi[4] = 20  # 2019: 2% margin
    r = score_buffett(quality(annual={"OperatingIncome": points(oi)}), BUFFETT_CFG)
    assert failed(r) == {"No collapsed operating margin"}
    assert criterion(r, "High operating margin").actual == pytest.approx(0.227)


def test_one_loss_year_is_tolerated_two_are_not():
    one = NET_INCOME[:]
    one[1] = -50
    r1 = score_buffett(quality(annual={"NetIncome": points(one)}), BUFFETT_CFG)
    assert criterion(r1, "Consistently positive earnings").passed  # 9 of 10 = 0.9

    two = one[:]
    two[2] = -50
    r2 = score_buffett(quality(annual={"NetIncome": points(two)}), BUFFETT_CFG)
    assert not criterion(r2, "Consistently positive earnings").passed  # 8 of 10 = 0.8
    assert criterion(r2, "Durable return on equity").passed  # 8 of 10 years still >= 15% ROE


def test_debt_payback_years():
    r = score_buffett(quality(annual={"LongTermDebt": points({2024: 1000})}), BUFFETT_CFG)
    c = criterion(r, "Debt repayable from earnings")
    assert not c.passed and c.actual == 5.0  # 1000 / 200 > 4

    loss = quality(annual={"NetIncome": points(NET_INCOME[:-1] + [-10])})
    c = criterion(score_buffett(loss, BUFFETT_CFG), "Debt repayable from earnings")
    assert not c.passed and "not positive" in c.detail


def test_missing_debt_is_zero_only_with_a_balance_sheet():
    with_bs = quality(annual={"LongTermDebt": []})
    c = criterion(score_buffett(with_bs, BUFFETT_CFG), "Debt repayable from earnings")
    assert c.passed and "treated as 0" in c.detail

    no_bs = quality(annual={"LongTermDebt": [], "Assets": []})
    c = criterion(score_buffett(no_bs, BUFFETT_CFG), "Debt repayable from earnings")
    assert not c.passed and c.detail.startswith("no data")


def test_capital_hungry_business_fails():
    r = score_buffett(quality(annual={"Capex": points([150] * 10)}), BUFFETT_CFG)
    c = criterion(r, "Low capital intensity")
    assert not c.passed and c.actual == pytest.approx(150 / 172, abs=1e-3)


def test_missing_inputs_fail_as_no_data_but_the_node_still_evaluates():
    r = score_buffett(quality(annual={"Capex": [], "DepreciationAmortization": []}), BUFFETT_CFG)
    assert r.evaluated
    for name in ("Low capital intensity", MOS):
        c = criterion(r, name)
        assert not c.passed and c.detail.startswith("no data")


def test_tiny_owner_earnings_give_a_hugely_negative_margin_of_safety():
    # capex 200: owner earnings 2022-2024 = 0, 10, 30 -> mean 13.33 -> intrinsic 133 vs cap 1000
    r = score_buffett(quality(annual={"Capex": points([200] * 10)}), BUFFETT_CFG)
    c = criterion(r, MOS)
    assert not c.passed and c.actual == pytest.approx(1 - 1000 / (40 / 3 / 0.10), abs=1e-3)


def test_negative_owner_earnings_fail_the_margin_of_safety():
    r = score_buffett(quality(annual={"Capex": points([300] * 10)}), BUFFETT_CFG)  # OE < 0
    assert not criterion(r, MOS).passed and "owner earnings <= 0" in criterion(r, MOS).detail


def test_multi_class_issuers_get_no_market_value_criterion():
    r = score_buffett(quality(multi_class=True), BUFFETT_CFG)
    c = criterion(r, MOS)
    assert not c.passed and "multiple share classes" in c.detail


def test_too_little_history_abstains_instead_of_scoring_zero():
    short = quality(
        annual={
            "NetIncome": points(NET_INCOME[-4:], YEARS[-4:]),
            "StockholdersEquity": points([500] * 4, YEARS[-4:]),
        }
    )
    r = score_buffett(short, BUFFETT_CFG)
    assert r.evaluated is False and r.criteria == []
    assert "needs 5 fiscal years" in r.explanation and "found 4" in r.explanation


def test_discount_rate_must_exceed_growth():
    bad = {**BUFFETT_CFG, "growth": 0.10}
    with pytest.raises(ValueError, match="discount_rate must be greater than growth"):
        score_buffett(quality(), bad)


def test_growth_raises_intrinsic_value():
    flat = criterion(score_buffett(quality(), BUFFETT_CFG), MOS).actual
    grown = criterion(score_buffett(quality(), {**BUFFETT_CFG, "growth": 0.03}), MOS).actual
    assert grown > flat  # (1+g)/(r-g) > 1/r


# --- aggregation ---------------------------------------------------------------------------

GRAHAM_PASSING = dict(
    pe=10.0, pb=1.2, current_ratio=2.5, debt_to_equity=0.4, dividend_years=20, earnings_growth=0.05
)


def run(metrics, previous=None):
    return build_graph().invoke(
        {"metrics": metrics, "config": CFG, "previous_conviction": previous}
    )


def test_graph_combines_graham_and_buffett():
    out = run(quality(**GRAHAM_PASSING))
    assert set(out["philosophy_results"]) == {"graham", "buffett"}
    assert out["conviction"] == 100.0 and out["signal"].kind == "emerging"


def test_graph_equal_weights_average_the_two_philosophies():
    out = run(quality(price=15.0, **GRAHAM_PASSING))  # buffett 85.7, graham 100
    assert out["conviction"] == pytest.approx((100 + 100 * 6 / 7) / 2, abs=0.1)


def test_abstaining_philosophy_is_left_out_of_the_weighting():
    out = run(
        SecurityMetrics(ticker="NEW", as_of=datetime(2025, 3, 10, tzinfo=UTC), **GRAHAM_PASSING)
    )
    assert out["philosophy_results"]["buffett"].evaluated is False
    assert out["conviction"] == 100.0  # graham alone, not (100 + 0) / 2
    assert "Not evaluated (insufficient data): buffett" in out["explanation"]
    assert out["signal"].kind == "emerging"


def test_nothing_evaluated_means_no_conviction_and_no_signal():
    state = {
        "philosophy_results": {"a": ScoreResult(philosophy="a", score=0, evaluated=False)},
        "config": {"weights": {}},
    }
    agg = aggregate_node(state)
    assert agg["conviction"] == 0.0 and agg["all_abstained"] is True
    assert "No philosophy could be evaluated" in agg["explanation"]
    # a 0 caused by missing data must never look like a collapse and trigger a sell
    decided = decide_node({**state, **agg, "previous_conviction": 90.0, "metrics": quality()})
    assert decided["signal"] is None


def test_real_config_has_a_buffett_section_and_weight():
    assert CFG["weights"]["buffett"] > 0
    assert CFG["buffett"]["impl"] == "deterministic"


# --- end to end: stored facts -> metrics -> Buffett -----------------------------------------


def fact(concept, value, end, *, unit="USD", instant=False, filed=None):
    tag = CONCEPT_TAGS[concept][0]
    filed = filed or date(end.year + 1, 2, 15)
    return dict(
        concept=concept,
        tag=tag,
        unit=unit,
        period_start=end if instant else date(end.year, 1, 1),
        period_end=end,
        instant=instant,
        fiscal_year=end.year,
        fiscal_period="FY",
        form="10-K",
        filed=filed,
        accession=f"{concept[:6]}-{end.year}",
        value=Decimal(str(value)),
    )


def test_buffett_scores_a_company_from_stored_facts(raw_engine):
    rows = []
    for year, ni in zip(YEARS, NET_INCOME, strict=True):
        end = FY(year)
        rows += [
            fact("NetIncome", ni, end),
            fact("StockholdersEquity", 500, end, instant=True),
            fact("Revenue", 1000, end),
            fact("OperatingIncome", 250, end),
            fact("DepreciationAmortization", 30, end),
            fact("Capex", 40, end),
        ]
    rows += [
        fact("LongTermDebt", 200, FY(2024), instant=True),
        fact("Assets", 2000, FY(2024), instant=True),
        fact(
            "SharesOutstanding",
            100,
            date(2025, 1, 31),
            unit="shares",
            instant=True,
            filed=date(2025, 2, 15),
        ),
    ]
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "QUAL"}])
        sid = repo.get_security(s, "QUAL").id
        repo.upsert_facts(s, sid, rows)
        repo.upsert_prices(
            s,
            sid,
            [
                dict(
                    ts=datetime(2025, 3, 3, 21, tzinfo=UTC),
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=1,
                )
            ],
        )
        out = score_security(s, "QUAL", date(2025, 3, 10))
    buffett = out["philosophy_results"]["buffett"]
    assert buffett.evaluated and buffett.score == 100.0
    assert criterion(buffett, MOS).actual == pytest.approx(0.4231, abs=1e-4)
    assert len(out["metrics"].annual["Capex"]) == 10
