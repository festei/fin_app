"""Greenblatt node. Base company, hand-computed:

EBIT (operating income) 180 / 190 / 200 for 2022-2024; price 10 x 100 shares = market cap 1000
cash 100, total debt 250 (200 noncurrent + 50 due within a year, the 50 also sits in
current liabilities)
  enterprise value = 1000 + 250 - 100 = 1150       earnings yield = 200 / 1150 = 0.1739
current assets 500, current liabilities 300, net fixed assets 400
  net working capital = (500 - 100) - (300 - 50) = 150   capital = 150 + 400 = 550
  return on capital = 200 / 550 = 0.3636
=> earnings yield >= 10%, ROC >= 25%, EBIT positive 3 years: 3 of 3
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fin_core.db import repo
from fin_core.db.session import session_scope
from fin_core.graph import load_config
from fin_core.graph.nodes.greenblatt import score_greenblatt
from fin_core.models import AnnualPoint, ScoreResult, SecurityMetrics
from fin_core.providers.edgar_concepts import CONCEPT_TAGS
from fin_core.scoring import score_security

CFG = load_config()["greenblatt"]
FY22, FY23, FY24 = date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 31)


def one(value, fy=FY24):
    return [AnnualPoint(period_end=fy, value=float(value))]


def company(**overrides) -> SecurityMetrics:
    annual = {
        "OperatingIncome": [
            AnnualPoint(period_end=y, value=float(v))
            for y, v in ((FY22, 180), (FY23, 190), (FY24, 200))
        ],
        "Assets": one(1500),
        "AssetsCurrent": one(500),
        "LiabilitiesCurrent": one(300),
        "Cash": one(100),
        "TotalDebt": one(250),  # 200 noncurrent + 50 due within a year
        "LongTermDebtCurrent": one(50),
        "PropertyPlantAndEquipmentNet": one(400),
        "SharesOutstanding": [AnnualPoint(period_end=date(2025, 1, 31), value=100.0)],
    }
    annual.update(overrides.pop("annual", {}))
    base = dict(ticker="G", as_of=datetime(2025, 3, 10, tzinfo=UTC), price=10.0, annual=annual)
    base.update(overrides)
    return SecurityMetrics(**base)


def criterion(r: ScoreResult, name: str):
    return next(c for c in r.criteria if c.name == name)


EY, ROC, STEADY = "Earnings yield", "Return on capital", "Profitable in each recent year"


def test_base_company_passes_all_three():
    r = score_greenblatt(company(), CFG)
    assert r.evaluated and r.score == 100.0 and len(r.criteria) == 3
    assert criterion(r, EY).actual == pytest.approx(200 / 1150, abs=1e-4)
    assert criterion(r, ROC).actual == pytest.approx(200 / 550, abs=1e-4)
    assert "not a cross-sectional rank" in r.explanation


def test_an_expensive_price_fails_only_the_earnings_yield():
    r = score_greenblatt(company(price=20.0), CFG)  # EV = 2000 + 150 = 2150 -> yield 9.3%
    assert {c.name for c in r.failed_criteria} == {EY}
    assert criterion(r, EY).actual == pytest.approx(200 / 2150, abs=1e-4)
    assert r.score == pytest.approx(100 * 2 / 3, abs=0.1)


def test_a_capital_hungry_business_fails_the_return_on_capital():
    r = score_greenblatt(company(annual={"PropertyPlantAndEquipmentNet": one(1400)}), CFG)
    assert criterion(r, ROC).actual == pytest.approx(200 / 1550, abs=1e-4)  # 12.9% < 25%
    assert {c.name for c in r.failed_criteria} == {ROC}


def test_negative_invested_capital_is_a_capital_light_pass():
    # NWC = (300 - 100) - (500 - 0) = -300; capital = -300 + 100 = -200
    annual = {
        "AssetsCurrent": one(300), "LiabilitiesCurrent": one(500),
        "LongTermDebtCurrent": one(0), "PropertyPlantAndEquipmentNet": one(100),
    }  # fmt: skip
    c = criterion(score_greenblatt(company(annual=annual), CFG), ROC)
    assert c.passed and "capital-light" in c.detail


def test_missing_fixed_assets_leave_return_on_capital_without_data():
    r = score_greenblatt(company(annual={"PropertyPlantAndEquipmentNet": []}), CFG)
    c = criterion(r, ROC)
    assert not c.passed and c.detail == "no data: missing net fixed assets"
    assert criterion(r, EY).passed  # the other criteria are unaffected


def test_missing_cash_is_zero_when_the_balance_sheet_exists_and_says_so():
    r = score_greenblatt(company(annual={"Cash": []}), CFG)
    ey = criterion(r, EY)  # EV = 1000 + 250 - 0 = 1250
    assert ey.actual == pytest.approx(200 / 1250, abs=1e-4)
    assert "no cash reported, treated as 0" in ey.detail


def test_without_any_balance_sheet_the_yield_has_no_data():
    bare = company(annual={k: [] for k in (
        "Assets", "AssetsCurrent", "LiabilitiesCurrent", "Cash", "TotalDebt",
        "LongTermDebtCurrent", "PropertyPlantAndEquipmentNet")})  # fmt: skip
    r = score_greenblatt(bare, CFG)
    assert criterion(r, EY).detail.startswith("no data")
    assert criterion(r, ROC).detail.startswith("no data")


def test_a_cash_rich_company_has_no_positive_enterprise_value():
    r = score_greenblatt(company(price=1.0, annual={"Cash": one(5000)}), CFG)  # 100 + 250 - 5000
    assert not criterion(r, EY).passed
    assert "enterprise value is not positive" in criterion(r, EY).detail


def test_one_loss_year_breaks_the_profit_record():
    ebit = [
        AnnualPoint(period_end=y, value=float(v)) for y, v in ((FY22, 180), (FY23, -5), (FY24, 200))
    ]
    r = score_greenblatt(company(annual={"OperatingIncome": ebit}), CFG)
    assert {c.name for c in r.failed_criteria} == {STEADY}
    short = score_greenblatt(company(annual={"OperatingIncome": one(200)}), CFG)
    assert criterion(short, STEADY).detail.startswith("no data: only 1 years")


@pytest.mark.parametrize("sector", ["Financials", "Utilities"])
def test_excluded_sectors_abstain(sector):
    r = score_greenblatt(company(sector=sector), CFG)
    assert r.evaluated is False and sector in r.explanation


def test_other_structural_abstentions():
    assert score_greenblatt(company(multi_class=True), CFG).evaluated is False
    assert score_greenblatt(company(price=None), CFG).evaluated is False
    assert score_greenblatt(company(annual={"OperatingIncome": []}), CFG).evaluated is False
    assert score_greenblatt(company(annual={"SharesOutstanding": []}), CFG).evaluated is False


# --- end to end: the new concepts flow from stored facts into the node ------------------------


def fact(concept, value, end, *, unit="USD", instant=False, filed=None, tag=None):
    return dict(
        concept=concept, tag=tag or CONCEPT_TAGS[concept][0], unit=unit,
        period_start=end if instant else date(end.year, 1, 1), period_end=end, instant=instant,
        fiscal_year=end.year, fiscal_period="FY", form="10-K",
        filed=filed or date(end.year + 1, 2, 15), accession=f"{concept[:9]}-{end.year}",
        value=Decimal(str(value)),
    )  # fmt: skip


def test_greenblatt_scores_a_company_from_stored_facts(raw_engine):
    rows = [fact("OperatingIncome", v, y) for y, v in ((FY22, 180), (FY23, 190), (FY24, 200))]
    rows += [fact(c, v, FY24, instant=True) for c, v in (
        ("Assets", 1500), ("AssetsCurrent", 500), ("LiabilitiesCurrent", 300), ("Cash", 100),
        ("LongTermDebt", 200), ("LongTermDebtCurrent", 50), ("PropertyPlantAndEquipmentNet", 400),
        ("InventoryNet", 80))]  # fmt: skip
    rows.append(fact("SharesOutstanding", 100, date(2025, 1, 31), unit="shares", instant=True,
                     filed=date(2025, 2, 15)))  # fmt: skip
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "GB"}])
        sid = repo.get_security(s, "GB").id
        repo.upsert_facts(s, sid, rows)
        repo.upsert_prices(s, sid, [dict(ts=datetime(2025, 3, 3, 21, tzinfo=UTC), open=10.0,
                                         high=10.0, low=10.0, close=10.0, volume=1)])  # fmt: skip
        out = score_security(s, "GB", date(2025, 3, 10))
    g = out["philosophy_results"]["greenblatt"]
    assert g.evaluated and g.score == 100.0
    assert criterion(g, EY).actual == pytest.approx(200 / 1150, abs=1e-4)
    # the four concepts added for these nodes were classified and reached the metrics
    for concept in ("InventoryNet", "PropertyPlantAndEquipmentNet", "LongTermDebtCurrent"):
        assert out["metrics"].annual[concept], concept


@pytest.mark.parametrize(
    "tag_index, reported",
    [(0, 200), (1, 250)],  # noncurrent tag (200 + 50 current) or total tag (250, includes it)
)
def test_current_debt_is_never_counted_twice(raw_engine, tag_index, reported):
    rows = [fact("OperatingIncome", v, y) for y, v in ((FY22, 180), (FY23, 190), (FY24, 200))]
    rows += [fact(c, v, FY24, instant=True) for c, v in (
        ("Assets", 1500), ("AssetsCurrent", 500), ("LiabilitiesCurrent", 300), ("Cash", 100),
        ("LongTermDebtCurrent", 50), ("PropertyPlantAndEquipmentNet", 400))]  # fmt: skip
    rows.append(fact("LongTermDebt", reported, FY24, instant=True,
                     tag=CONCEPT_TAGS["LongTermDebt"][tag_index]))  # fmt: skip
    rows.append(fact("SharesOutstanding", 100, date(2025, 1, 31), unit="shares", instant=True,
                     filed=date(2025, 2, 15)))  # fmt: skip
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": f"DC{tag_index}"}])
        sid = repo.get_security(s, f"DC{tag_index}").id
        repo.upsert_facts(s, sid, rows)
        repo.upsert_prices(s, sid, [dict(ts=datetime(2025, 3, 3, 21, tzinfo=UTC), open=10.0,
                                         high=10.0, low=10.0, close=10.0, volume=1)])  # fmt: skip
        out = score_security(s, f"DC{tag_index}", date(2025, 3, 10))
    g = out["philosophy_results"]["greenblatt"]
    # EV = 1000 + 250 - 100 = 1150 either way (300 would mean the 50 was counted twice)
    assert criterion(g, EY).actual == pytest.approx(200 / 1150, abs=1e-4)
    assert out["metrics"].annual["TotalDebt"][-1].value == 250.0
