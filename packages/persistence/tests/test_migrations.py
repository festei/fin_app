"""The migrations ship with ``fin_persistence`` and still describe exactly the tables in code."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from db_fixtures import migrated_engine  # noqa: F401 - pytest fixture
from sqlalchemy import inspect

from fin_persistence import migrate
from fin_persistence.tables import Base

EXPECTED_TABLES = {
    "securities",
    "price_bars",
    "fundamental_facts",
    "bond_yields",
    "ingestion_runs",
    "scores",
    "signals",
    "alerts",
    "alert_deliveries",
    "watchlist",
    "channel_state",
    "portfolios",
    "sandbox_orders",
    "portfolio_snapshots",
}


def test_head_creates_every_table_and_matches_the_models(migrated_engine):
    assert EXPECTED_TABLES <= set(inspect(migrated_engine).get_table_names())
    with migrated_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"migrations and tables have drifted apart: {diff}"


def test_downgrade_to_base_then_upgrade_again(migrated_engine):
    url = migrated_engine.url.render_as_string(hide_password=False)
    migrate.downgrade(url, "base")
    assert "securities" not in inspect(migrated_engine).get_table_names()
    migrate.upgrade(url)
    assert "securities" in inspect(migrated_engine).get_table_names()
