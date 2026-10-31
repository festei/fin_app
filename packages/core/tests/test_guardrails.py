"""Guardrail tests (Piotroski F-score, Altman Z''). Hand-computed base company:

            2023    2024
assets      1000    1100        ROA   .05 -> .0727 (rising)         CFO / assets .1091 > ROA
net income    50      80        leverage (debt / assets) .30 -> .2273 (falling)
op. cash     90     120        current ratio 1.333 -> 1.667 (rising)
LT debt     300     250        gross margin .40 -> .438 (rising)
cur. assets 400     500        asset turnover 1.0 -> 1.1 (rising)
cur. liab.  300     300        shares (cover page) 100 -> 98 (no new shares)
revenue    1000    1210        => F-score 9 of 9
gross profit 400    530
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fin_core.db import repo
from fin_core.db.session import session_scope
from fin_core.graph import build_graph, load_config
from fin_core.graph.guardrails import altman, guardrails_node, piotroski
from fin_core.models import AnnualPoint, SecurityMetrics
from fin_core.providers.edgar_concepts import CONCEPT_TAGS
from fin_core.scoring import score_security

CFG = load_config()
G = CFG["guardrails"]
FY23, FY24 = date(2023, 12, 31), date(2024, 12, 31)


def pts(a: float, b: float, y1=FY23, y2=FY24):
    return [AnnualPoint(period_end=y1, value=float(a)), AnnualPoint(period_end=y2, value=float(b))]


def healthy(**overrides) -> SecurityMetrics:
    annual = {
        "Assets": pts(1000, 1100),
        "NetIncome": pts(50, 80),
        "NetCashFromOperating": pts(90, 120),
        "LongTermDebt": pts(300, 250),
        "AssetsCurrent": pts(400, 500),
        "LiabilitiesCurrent": pts(300, 300),
        "Revenue": pts(1000, 1210),
        "GrossProfit": pts(400, 530),
        "SharesOutstanding": pts(100, 98, date(2024, 1, 31), date(2025, 1, 31)),
    }
    annual.update(overrides.pop("annual", {}))
    return SecurityMetrics(
        ticker="TEST", as_of=datetime(2025, 3, 10, tzinfo=UTC), annual=annual, **overrides
    )


# A collapsing company: every one of the nine Piotroski signals fails.
WEAK_ANNUAL = {
    "NetIncome": pts(50, -10),
    "NetCashFromOperating": pts(90, -20),
    "LongTermDebt": pts(300, 500),
    "AssetsCurrent": pts(400, 200),
    "Revenue": pts(1000, 900),
    "GrossProfit": pts(400, 100),
    "SharesOutstanding": pts(100, 120, date(2024, 1, 31), date(2025, 1, 31)),
}


def names(criteria):
    return {c.name for c in criteria}


def failed(result):
    return names(result.failed_criteria)


def test_healthy_company_scores_nine_of_nine():
    r, passes, evaluable = piotroski(healthy(), G)
    assert r.evaluated and (passes, evaluable) == (9, 9) and r.score == 100.0
    assert len(r.criteria) == 9 and "F-score 9 of 9" in r.evidence[0]


def test_loss_this_year_fails_roa_and_its_trend_only():
    r, passes, _ = piotroski(healthy(annual={"NetIncome": pts(50, -10)}), G)
    assert failed(r) == {"Positive return on assets", "Rising return on assets"}
    assert passes == 7 and r.score == pytest.approx(77.8, abs=0.1)


def test_new_shares_and_rising_leverage_each_cost_a_point():
    shares = healthy(
        annual={"SharesOutstanding": pts(100, 120, date(2024, 1, 31), date(2025, 1, 31))}
    )
    assert failed(piotroski(shares, G)[0]) == {"No new shares issued"}
    levered = healthy(annual={"LongTermDebt": pts(300, 500)})  # .30 -> .4545
    assert failed(piotroski(levered, G)[0]) == {"Falling leverage"}


def test_no_debt_in_either_year_counts_as_falling_leverage():
    r, passes, _ = piotroski(healthy(annual={"LongTermDebt": []}), G)
    assert passes == 9 and "Falling leverage" not in failed(r)


def test_signals_without_data_are_not_counted_against_the_company():
    r, passes, evaluable = piotroski(healthy(annual={"GrossProfit": []}), G)
    assert (passes, evaluable) == (8, 8) and r.score == 100.0
    gm = next(c for c in r.criteria if c.name == "Rising gross margin")
    assert not gm.passed and gm.detail.startswith("no data")


def test_too_few_evaluable_signals_abstains():
    only_current_year = healthy(
        annual={k: [v[-1]] for k, v in healthy().annual.items() if k != "SharesOutstanding"}
    )
    r, _, evaluable = piotroski(only_current_year, G)
    assert evaluable == 3 and r.evaluated is False
    assert "only 3 of 9 signals have data" in r.explanation


def test_collapsing_company_gets_a_very_low_f_score():
    r, passes, evaluable = piotroski(healthy(annual=WEAK_ANNUAL), G)
    assert (passes, evaluable) == (0, 9) and r.score == 0.0


# --- Altman Z'' ----------------------------------------------------------------------------


def balance(ta, ca, cl, re, ebit, eq, tl):
    return {
        "Assets": pts(ta, ta), "AssetsCurrent": pts(ca, ca), "LiabilitiesCurrent": pts(cl, cl),
        "RetainedEarnings": pts(re, re), "OperatingIncome": pts(ebit, ebit),
        "StockholdersEquity": pts(eq, eq), "Liabilities": pts(tl, tl),
    }  # fmt: skip


def altman_of(**kw):
    return altman(SecurityMetrics(ticker="T", as_of=datetime(2025, 3, 10, tzinfo=UTC), **kw), G)


def test_altman_safe_zone():
    # X1 .2, X2 .3, X3 .15, X4 1.0 -> 1.312 + 0.978 + 1.008 + 1.05 = 4.348
    r, z = altman_of(annual=balance(1000, 400, 200, 300, 150, 500, 500))
    assert z == pytest.approx(4.348, abs=1e-3)
    assert r.score == 100.0 and r.criteria[0].passed and r.criteria[1].passed
    assert "safe zone" in r.evidence[0]


def test_altman_grey_zone_maps_linearly_to_the_score():
    # X1 .05, X2 .1, X3 .06, X4 300/700 -> 0.328 + 0.326 + 0.4032 + 0.45 = 1.5072
    r, z = altman_of(annual=balance(1000, 300, 250, 100, 60, 300, 700))
    assert z == pytest.approx(1.5072, abs=1e-4)
    assert r.score == pytest.approx(27.1, abs=0.1)  # (1.5072 - 1.1) / 1.5
    assert r.criteria[0].passed and not r.criteria[1].passed


def test_altman_distress_zone():
    # X1 -.3, X2 -.2, X3 -.05, X4 100/900 -> -1.968 - 0.652 - 0.336 + 0.1167 = -2.8393
    r, z = altman_of(annual=balance(1000, 100, 400, -200, -50, 100, 900))
    assert z == pytest.approx(-2.8393, abs=1e-3)
    assert r.score == 0.0 and not r.criteria[0].passed


def test_altman_abstains_without_all_inputs():
    annual = balance(1000, 400, 200, 300, 150, 500, 500)
    del annual["RetainedEarnings"]
    r, z = altman_of(annual=annual)
    assert z is None and r.evaluated is False and "missing RetainedEarnings" in r.explanation
    r, z = altman_of(annual=balance(1000, 400, 200, 300, 150, 500, 0))  # no liabilities
    assert z is None and r.evaluated is False


# --- the node and the graph ---------------------------------------------------------------


def test_node_vetoes_on_distress_or_a_very_weak_f_score():
    distressed = healthy(annual=balance(1000, 100, 400, -200, -50, 100, 900))
    out = guardrails_node({"metrics": distressed, "config": CFG})
    assert out["guardrail_veto"] and any("distress zone" in r for r in out["guardrail_reasons"])

    weak = healthy(annual=WEAK_ANNUAL)
    out = guardrails_node({"metrics": weak, "config": CFG})
    assert out["guardrail_veto"] and "Piotroski F-score 0 of 9" in out["guardrail_reasons"][0]

    assert guardrails_node({"metrics": healthy(), "config": CFG})["guardrail_veto"] is False


def test_financials_and_missing_data_never_veto():
    distressed = healthy(sector="Financials", annual=balance(1000, 100, 400, -200, -50, 100, 900))
    out = guardrails_node({"metrics": distressed, "config": CFG})
    assert out["guardrail_veto"] is False
    assert all(not r.evaluated for r in out["guardrail_results"].values())
    assert "Financials" in out["guardrail_results"]["altman_z"].explanation

    bare = SecurityMetrics(ticker="X", as_of=datetime(2025, 3, 10, tzinfo=UTC))
    assert guardrails_node({"metrics": bare, "config": CFG})["guardrail_veto"] is False


def test_guardrails_can_be_disabled():
    cfg = {**CFG, "guardrails": {**G, "enabled": False}}
    out = guardrails_node({"metrics": healthy(), "config": cfg})
    assert out == {"guardrail_veto": False, "guardrail_results": {}, "guardrail_reasons": []}


GRAHAM_PASSING = dict(
    pe=10.0, pb=1.2, current_ratio=2.5, debt_to_equity=0.4, dividend_years=20, earnings_growth=0.05
)


def run(metrics, previous=None):
    return build_graph().invoke(
        {"metrics": metrics, "config": CFG, "previous_conviction": previous}
    )


def test_distress_caps_an_otherwise_perfect_conviction_and_explains_why():
    metrics = healthy(annual=balance(1000, 100, 400, -200, -50, 100, 900), **GRAHAM_PASSING)
    out = run(metrics)
    assert out["philosophy_results"]["graham"].score == 100.0
    assert out["conviction"] == 49.0  # capped just below the sell threshold
    assert "Capped by guardrails: Altman Z'' -2.84 is in the distress zone" in out["explanation"]
    assert out["signal"] is None  # no buy for a value trap
    assert run(metrics, previous=90.0)["signal"].kind == "sell"  # and an existing view is cut
    assert set(out["guardrail_results"]) == {"piotroski", "altman_z"}


def test_a_healthy_company_is_not_capped():
    out = run(healthy(annual=balance(1000, 400, 200, 300, 150, 500, 500), **GRAHAM_PASSING))
    assert out["conviction"] == 100.0 and "Capped" not in out["explanation"]


def test_api_returns_the_guardrail_details():
    from fastapi.testclient import TestClient

    from fin_api.main import app

    body = {"metrics": {"ticker": "T", "as_of": "2025-03-10T00:00:00", **GRAHAM_PASSING}}
    data = TestClient(app).post("/api/score", json=body).json()
    assert set(data["guardrail_results"]) == {"piotroski", "altman_z"}
    assert data["guardrail_results"]["altman_z"]["evaluated"] is False  # no balance-sheet history


# --- end to end from stored facts ---------------------------------------------------------


def fact(concept, value, end, *, unit="USD", instant=False, filed=None):
    tag = CONCEPT_TAGS[concept][0]
    return dict(
        concept=concept, tag=tag, unit=unit,
        period_start=end if instant else date(end.year, 1, 1), period_end=end, instant=instant,
        fiscal_year=end.year, fiscal_period="FY", form="10-K",
        filed=filed or date(end.year + 1, 2, 15),
        accession=f"{concept[:7]}-{end.year}", value=Decimal(str(value)),
    )  # fmt: skip


def test_guardrails_score_a_company_from_stored_facts(raw_engine):
    flows = {"NetIncome": (50, 80), "NetCashFromOperating": (90, 120), "Revenue": (1000, 1210),
             "GrossProfit": (400, 530), "OperatingIncome": (150, 160)}  # fmt: skip
    stocks = {"Assets": (1000, 1100), "LongTermDebt": (300, 250), "AssetsCurrent": (400, 500),
              "LiabilitiesCurrent": (300, 300), "RetainedEarnings": (300, 380),
              "StockholdersEquity": (500, 600), "Liabilities": (500, 500)}  # fmt: skip
    rows = []
    for concept, (a, b) in flows.items():
        rows += [fact(concept, a, FY23), fact(concept, b, FY24)]
    for concept, (a, b) in stocks.items():
        rows += [fact(concept, a, FY23, instant=True), fact(concept, b, FY24, instant=True)]
    rows += [
        # cover-page counts are filed with the annual report, shortly after they are dated
        fact(
            "SharesOutstanding",
            100,
            date(2024, 1, 31),
            unit="shares",
            instant=True,
            filed=date(2024, 2, 15),
        ),
        fact(
            "SharesOutstanding",
            98,
            date(2025, 1, 31),
            unit="shares",
            instant=True,
            filed=date(2025, 2, 15),
        ),
    ]
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "GOOD"}])
        sid = repo.get_security(s, "GOOD").id
        repo.upsert_facts(s, sid, rows)
        repo.upsert_prices(s, sid, [dict(ts=datetime(2025, 3, 3, 21, tzinfo=UTC), open=10.0,
                                         high=10.0, low=10.0, close=10.0, volume=1)])  # fmt: skip
        out = score_security(s, "GOOD", date(2025, 3, 10))
    f_score = out["guardrail_results"]["piotroski"]
    assert f_score.evaluated and "F-score 9 of 9" in f_score.evidence[0]
    z = out["guardrail_results"]["altman_z"]
    assert z.evaluated and z.criteria[0].passed  # outside the distress zone
    assert out["conviction"] is not None
