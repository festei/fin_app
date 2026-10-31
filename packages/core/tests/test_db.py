"""DB layer tests. Always run on SQLite; also on Postgres when TEST_DATABASE_URL is set
(e.g. TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/fin_test)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from fin_core.db import migrate, repo
from fin_core.db.models import Base, FundamentalFact
from fin_core.db.session import database_url, make_engine, session_scope

PG_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def engine(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        if not PG_URL:
            pytest.skip("TEST_DATABASE_URL not set")
        url = PG_URL
    eng = make_engine(url)
    if request.param == "postgres":  # start from an empty schema
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield eng
    eng.dispose()


def fact(**kw):
    base = dict(
        concept="Revenue",
        tag="us-gaap/Revenues",
        unit="USD",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        instant=False,
        fiscal_year=2023,
        fiscal_period="FY",
        form="10-K",
        filed=date(2024, 2, 1),
        accession="0000000000-24-000001",
        value=Decimal("1000"),
    )
    base.update(kw)
    return base


def test_migration_creates_all_tables_and_matches_models(engine):
    tables = set(inspect(engine).get_table_names())
    assert {
        "securities",
        "price_bars",
        "fundamental_facts",
        "bond_yields",
        "ingestion_runs",
    } <= tables
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"migration and models have drifted apart: {diff}"


def test_migration_downgrade_then_upgrade(engine):
    url = engine.url.render_as_string(hide_password=False)
    migrate.downgrade(url, "base")
    assert "securities" not in inspect(engine).get_table_names()
    migrate.upgrade(url)
    assert "securities" in inspect(engine).get_table_names()


def test_upsert_securities_is_idempotent_and_updates(engine):
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "ibm", "name": "IBM"}, {"ticker": "KO"}])
        repo.upsert_securities(
            s, [{"ticker": "IBM", "name": "Intl Business Machines", "cik": "51143"}]
        )
    with session_scope(engine) as s:
        assert [x.ticker for x in repo.list_securities(s)] == ["IBM", "KO"]
        ibm = repo.get_security(s, "ibm")
        assert (ibm.name, ibm.cik, ibm.kind, ibm.active) == (
            "Intl Business Machines",
            "51143",
            "stock",
            True,
        )


def test_facts_dedupe_on_natural_key(engine):
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "IBM"}])
        sid = repo.get_security(s, "IBM").id
        repo.upsert_facts(s, sid, [fact()])
        repo.upsert_facts(s, sid, [fact(), fact(value=Decimal("1"))])  # same key: ignored
        assert s.query(FundamentalFact).count() == 1
        assert s.query(FundamentalFact).one().value == Decimal("1000")


def test_facts_as_of_is_point_in_time_and_sees_restatements_only_after_filing(engine):
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "IBM"}])
        sid = repo.get_security(s, "IBM").id
        repo.upsert_facts(
            s,
            sid,
            [
                fact(value=Decimal("1000"), filed=date(2024, 2, 1), accession="A1"),
                # same period restated in a later filing
                fact(value=Decimal("900"), filed=date(2024, 8, 1), accession="A2", form="10-K/A"),
                # a later year
                fact(
                    period_start=date(2024, 1, 1),
                    period_end=date(2024, 12, 31),
                    value=Decimal("1200"),
                    filed=date(2025, 2, 1),
                    accession="A3",
                ),
            ],
        )

        def revenue(as_of):
            return {
                (f.period_end.year): f.value for f in repo.facts_as_of(s, sid, as_of, ["Revenue"])
            }

        assert revenue(date(2024, 1, 31)) == {}  # nothing public yet
        assert revenue(date(2024, 3, 1)) == {2023: Decimal("1000")}  # original number
        assert revenue(date(2024, 9, 1)) == {2023: Decimal("900")}  # restated version
        assert revenue(date(2025, 3, 1)) == {2023: Decimal("900"), 2024: Decimal("1200")}


def test_prices_upsert_overwrites_and_latest_price_respects_as_of(engine):
    t1, t2 = datetime(2025, 1, 2, 21, tzinfo=UTC), datetime(2025, 1, 3, 21, tzinfo=UTC)
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "IBM"}])
        sid = repo.get_security(s, "IBM").id
        bar = lambda ts, c: dict(ts=ts, open=c, high=c, low=c, close=c, volume=10)  # noqa: E731
        repo.upsert_prices(s, sid, [bar(t1, 100.0), bar(t2, 110.0)])
        repo.upsert_prices(s, sid, [bar(t2, 111.0)])  # corrected bar
        assert repo.latest_price(s, sid).close == 111.0
        assert repo.latest_price(s, sid, as_of=t1).close == 100.0
        assert repo.latest_price(s, sid, as_of=datetime(2025, 1, 1, tzinfo=UTC)) is None


def test_yields_upsert_and_latest(engine):
    with session_scope(engine) as s:
        repo.upsert_yields(
            s, "DGS10", [(date(2025, 1, 2), Decimal("4.5")), (date(2025, 1, 3), Decimal("4.6"))]
        )
        repo.upsert_yields(s, "DGS10", [(date(2025, 1, 3), Decimal("4.65"))])
        assert repo.latest_yield(s, "DGS10").value == Decimal("4.65")
        assert repo.latest_yield(s, "DGS10", date(2025, 1, 2)).value == Decimal("4.5")
        assert repo.latest_yield(s, "DGS2") is None


def test_database_url_from_postgres_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "fin")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("POSTGRES_DB", "findb")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    assert database_url() == "postgresql+psycopg://fin:p%40ss%2Fword@db:5432/findb"


def test_database_url_requires_configuration(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        database_url()
