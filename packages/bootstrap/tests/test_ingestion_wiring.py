"""The ingestion use cases wired by the real Container onto a real database (SQLite), with the
vendors replaced by fakes: proves the adapters and the use cases fit together."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from fin_application.ports import CompanyListing
from fin_bootstrap import Container, Settings
from fin_domain.market_data import CONCEPT_TAGS, Fact, PriceBar, YieldPoint, known_as_of
from fin_domain.shared import Ticker
from fin_persistence import migrate
from fin_testkit import FakeFundamentalsSource, FakePriceSource, FakeYieldSource

IBM = Ticker("IBM")


@pytest.fixture
def container(tmp_path):
    url = f"sqlite:///{tmp_path / 'wiring.db'}"
    migrate.upgrade(url)
    c = Container(Settings(database_url=url))
    yield c
    c.dispose()


def fact(value, filed, accession):
    return Fact(
        concept="Revenue",
        tag=CONCEPT_TAGS["Revenue"][0],
        unit="USD",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        instant=False,
        fiscal_year=2023,
        fiscal_period="FY",
        form="10-K",
        filed=filed,
        accession=accession,
        value=Decimal(value),
    )


def count(container, table):
    with container.engine.connect() as conn:
        return conn.execute(text(f"select count(*) from {table}")).scalar_one()


def test_the_real_universe_syncs_idempotently_and_never_wipes_a_cik(container):
    first = container.sync_universe.execute()
    assert first > 10
    with container.uow_factory() as uow:  # as EDGAR resolves it
        ibm = uow.securities.get(IBM)
        ibm.resolve_identity("0000051143", "ignored")
        uow.securities.save(ibm)
    assert container.sync_universe.execute() == first  # the daily sync: the file has no cik
    assert count(container, "securities") == first
    with container.uow_factory() as uow:
        assert uow.securities.get(IBM).cik == "0000051143"
        assert uow.securities.get(Ticker("TLT")).kind.value == "etf"


def test_fundamentals_flow_through_to_point_in_time_reads(container):
    container.sync_universe.execute()
    container._fundamentals_source = FakeFundamentalsSource(
        directory={IBM: CompanyListing("0000051143", "International Business Machines")},
        facts={
            "0000051143": [
                fact("1000", date(2024, 2, 1), "orig"),
                fact("900", date(2024, 8, 1), "rest"),
            ]
        },
    )
    summary = container.ingest_fundamentals.execute(["IBM"])
    again = container.ingest_fundamentals.execute(["ibm"])
    assert (summary.status, again.status) == ("ok", "ok")
    assert count(container, "fundamental_facts") == 2  # the rerun added nothing
    with container.uow_factory() as uow:
        stored = uow.facts.filed_up_to(IBM, date(2030, 1, 1))
    assert [f.value for f in known_as_of(stored, date(2024, 3, 1))] == [Decimal("1000")]
    assert [f.value for f in known_as_of(stored, date(2024, 9, 1))] == [Decimal("900")]
    assert count(container, "ingestion_runs") == 2


def test_prices_and_yields_flow_to_the_database(container):
    container.sync_universe.execute()
    ts = datetime(2025, 1, 2, 5, tzinfo=UTC)
    container._price_source = FakePriceSource(
        {IBM: [PriceBar(ts=ts + timedelta(days=i), close=100.0 + i) for i in range(3)]}
    )
    container._yield_source = FakeYieldSource(
        {"DGS10": [YieldPoint(date(2025, 1, 2), Decimal("4.57"))]}
    )
    prices = container.ingest_prices.execute(["IBM"])
    yields = container.ingest_yields.execute(["DGS10"])
    assert (prices.status, prices.rows, yields.status, yields.rows) == ("ok", 3, "ok", 1)
    with container.uow_factory() as uow:
        assert uow.prices.latest(IBM).close == 102.0
        assert uow.yields.latest("DGS10").value == Decimal("4.57")


def test_sources_are_built_only_when_needed():
    c = Container(Settings())  # no SEC contact, no FRED key, no database
    assert c.universe is not None  # the config-backed pieces need nothing from the environment
    c.dispose()
