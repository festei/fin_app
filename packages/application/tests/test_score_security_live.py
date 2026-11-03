from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fin_application.scoring import Baseline, ScoreSecurityLive, score
from fin_domain.market_data import CONCEPT_TAGS, Fact, PriceBar, SecurityUpdate
from fin_domain.scoring import SecurityMetrics
from fin_domain.shared import NotFound, Ticker
from fin_testkit import (
    FakeScoringConfig,
    FakeScoringEngine,
    InMemoryStore,
    in_memory_uow_factory,
)

ACME = Ticker("ACME")
FY24 = date(2024, 12, 31)


def eps_fact(value, *, filed):
    return Fact(
        concept="EpsDiluted",
        tag=CONCEPT_TAGS["EpsDiluted"][0],
        unit="USD/shares",
        period_start=date(2024, 1, 1),
        period_end=FY24,
        instant=False,
        fiscal_year=2024,
        fiscal_period="FY",
        form="10-K",
        filed=filed,
        accession=f"eps-{filed}",
        value=Decimal(str(value)),
    )


@pytest.fixture
def world():
    store = InMemoryStore()
    factory = in_memory_uow_factory(store)
    with factory() as uow:
        uow.securities.upsert([SecurityUpdate(ACME, {"sector": "Industrials"})])
        uow.facts.add(
            ACME,
            [eps_fact(2.0, filed=date(2025, 2, 15)), eps_fact(4.0, filed=date(2025, 9, 1))],
        )
        uow.prices.upsert(ACME, [PriceBar(ts=datetime(2025, 2, 28, 5, tzinfo=UTC), close=25.0)])
    engine = FakeScoringEngine(conviction=80.0)
    return ScoreSecurityLive(factory, engine, FakeScoringConfig({"weights": {"x": 1}})), engine


def test_stored_data_becomes_point_in_time_metrics_for_the_engine(world):
    live, engine = world
    result = live.execute("acme", date(2025, 3, 1))
    ((metrics, config),) = engine.calls
    assert (metrics.ticker, metrics.sector, metrics.eps, metrics.pe) == (
        "ACME",
        "Industrials",
        2.0,
        12.5,
    )
    assert config == {"weights": {"x": 1}}
    assert result.assessment.conviction == 80.0


def test_a_restatement_filed_after_as_of_is_not_seen(world):
    live, engine = world
    live.execute("ACME", date(2025, 3, 1))
    assert engine.calls[-1][0].eps == 2.0


def test_the_first_real_score_above_the_line_is_an_emerging_signal(world):
    live, _ = world
    result = live.execute("ACME", date(2025, 3, 1))
    assert result.signal.kind == "emerging" and result.signal.conviction == 80.0


def test_the_baseline_decides_between_buy_and_nothing(world):
    live, _ = world
    assert (
        live.execute("ACME", date(2025, 3, 1), Baseline(previous_conviction=60)).signal.kind
        == "buy"
    )
    assert live.execute("ACME", date(2025, 3, 1), Baseline(previous_conviction=75)).signal is None
    assert live.execute("ACME", date(2025, 3, 1), Baseline(60, buy_armed=False)).signal is None


def test_stale_data_withholds_the_signal_but_still_reports_the_score(world):
    live, _ = world
    result = live.execute("ACME", date(2026, 9, 1))  # no recent price, year-old fundamentals
    assert result.assessment.conviction == 80.0 and result.signal is None
    assert "Signal withheld" in result.explanation


def test_an_unknown_or_malformed_ticker_is_not_found(world):
    live, _ = world
    for bad in ("ZZZ", "!!", ""):
        with pytest.raises(NotFound):
            live.execute(bad, date(2025, 3, 1))


def test_score_composes_assessment_and_decision_without_storage():
    metrics = SecurityMetrics(ticker="X", as_of=datetime(2025, 3, 1, tzinfo=UTC), price=1.0)
    live = score(metrics, FakeScoringEngine(30.0), {}, Baseline(previous_conviction=60))
    assert live.signal.kind == "sell" and live.metrics is metrics
    assert timedelta(0) == timedelta(0)
