"""The migrations ship with ``fin_persistence`` and still describe exactly the tables in code."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from db_fixtures import migrated_engine  # noqa: F401 - pytest fixture
from sqlalchemy import create_engine, inspect, text

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


def test_migration_0003_marks_existing_signals_processed(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrate.upgrade(url, "0002")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text("INSERT INTO securities (id, ticker, kind, active) VALUES (1, 'AAA', 'stock', 1)")
        )
        c.execute(
            text(
                "INSERT INTO scores (id, security_id, as_of, computed_at, conviction,"
                " all_abstained, signal_blocked, explanation, details) VALUES"
                " (1, 1, '2025-03-10', '2025-03-10 12:00:00', 80, 0, 0, 'x', '{}')"
            )
        )
        c.execute(
            text(
                "INSERT INTO signals (id, security_id, score_id, kind, conviction, as_of, reason,"
                " created_at) VALUES (1, 1, 1, 'buy', 80, '2025-03-10', '', '2025-03-10 12:00:00')"
            )
        )
    eng.dispose()
    migrate.upgrade(url)
    eng = create_engine(url)
    with eng.connect() as c:
        processed, reason = c.execute(
            text("SELECT processed_at, suppressed_reason FROM signals")
        ).one()
    eng.dispose()
    assert processed is not None and reason is None
