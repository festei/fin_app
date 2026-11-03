"""The LangGraph engine (philosophies in parallel) must give exactly the domain's answer (one
after the other), on the shipped thresholds."""

from datetime import UTC, date, datetime

import pytest

from fin_config import load_scoring_config
from fin_domain.scoring import AnnualPoint, SecurityMetrics, assess
from fin_langgraph import PHILOSOPHY_NODES, LangGraphScoringEngine, build_graph

CONFIG = load_scoring_config()
NOW = datetime(2025, 3, 1, tzinfo=UTC)
YEARS = [date(y, 12, 31) for y in range(2013, 2025)]


def series(values):
    return [AnnualPoint(period_end=d, value=v) for d, v in zip(YEARS, values, strict=True)]


def growing(start, step):
    return series([start + step * i for i in range(len(YEARS))])


def rich(**overrides):
    annual = {
        "Revenue": growing(1000, 100),
        "GrossProfit": growing(400, 45),
        "OperatingIncome": growing(250, 30),
        "NetIncome": growing(180, 22),
        "EpsDiluted": growing(1.0, 0.15),
        "DividendsPerShare": growing(0.4, 0.05),
        "StockholdersEquity": growing(1200, 120),
        "Assets": growing(2500, 200),
        "AssetsCurrent": growing(900, 80),
        "Liabilities": growing(1300, 80),
        "LiabilitiesCurrent": growing(400, 30),
        "TotalDebt": growing(300, 5),
        "NetCashFromOperating": growing(220, 25),
        "Capex": growing(60, 3),
        "DepreciationAmortization": growing(50, 3),
        "RetainedEarnings": growing(800, 150),
        "ResearchAndDevelopmentExpense": growing(40, 6),
        "InventoryNet": growing(100, 8),
        "PropertyPlantAndEquipmentNet": growing(500, 20),
        "SharesOutstanding": [AnnualPoint(d.replace(day=15, month=2, year=d.year + 1), 100.0)
                              for d in YEARS],
    }  # fmt: skip
    fields = dict(
        ticker="RICH", as_of=NOW, price=25.0, price_date=date(2025, 2, 28), pe=10.0, pe_avg3=11.0,
        pb=1.6, current_ratio=2.3, debt_to_equity=0.2, roe=0.18, earnings_growth=0.12,
        dividend_years=12, eps=2.5, book_value_per_share=15.0, latest_fiscal_year_end=YEARS[-1],
        sector="Industrials", annual=annual,
    )  # fmt: skip
    return SecurityMetrics(**{**fields, **overrides})


CASES = {
    "nothing known": SecurityMetrics(ticker="NONE", as_of=NOW, signal_blockers=["no price"]),
    "scalars only": SecurityMetrics(
        ticker="SCALAR",
        as_of=NOW,
        price=20.0,
        pe=9.0,
        pb=1.1,
        current_ratio=2.5,
        debt_to_equity=0.3,
        dividend_years=15,
        earnings_growth=0.08,
    ),  # fmt: skip
    "a strong company": rich(),
    "a financial (several philosophies abstain)": rich(sector="Financials"),
    "a distressed company (guardrail veto)": rich(
        annual={
            **rich().annual,
            "NetIncome": growing(-50, -20),
            "NetCashFromOperating": growing(-30, -10),
            "StockholdersEquity": growing(100, -90),
            "RetainedEarnings": growing(-100, -150),
            "Liabilities": growing(2800, 200),
            "Assets": growing(2500, 100),
        }
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_both_engines_give_the_same_assessment(name):
    metrics = CASES[name]
    assert LangGraphScoringEngine().assess(metrics, CONFIG) == assess(metrics, CONFIG)


def test_the_cases_actually_exercise_abstention_and_the_veto():
    bare = assess(CASES["nothing known"], CONFIG)
    assert not bare.philosophy_results["buffett"].evaluated  # abstains: too little history
    assert bare.philosophy_results["graham"].evaluated  # Graham never abstains: it fails instead
    assert assess(CASES["a distressed company (guardrail veto)"], CONFIG).guardrail_reasons
    assert assess(CASES["a strong company"], CONFIG).conviction > 50


def test_one_node_per_registered_philosophy():
    assert set(PHILOSOPHY_NODES) == {"graham", "buffett", "lynch", "greenblatt", "fisher"}
    assert set(PHILOSOPHY_NODES) == set(CONFIG["weights"])


def test_the_compiled_graph_is_reusable():
    engine = LangGraphScoringEngine()
    first = engine.assess(CASES["a strong company"], CONFIG)
    assert engine.assess(CASES["a strong company"], CONFIG) == first


def test_an_llm_backed_philosophy_fails_loudly_until_it_exists():
    config = {**CONFIG, "graham": {**CONFIG["graham"], "impl": "llm"}}
    with pytest.raises(NotImplementedError, match="Graham"):
        build_graph().invoke({"metrics": CASES["scalars only"], "config": config})
