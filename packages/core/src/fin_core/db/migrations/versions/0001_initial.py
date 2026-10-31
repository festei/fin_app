"""initial schema: securities, price_bars, fundamental_facts, bond_yields, ingestion_runs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BigIntPK = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "securities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False, unique=True),
        sa.Column("cik", sa.String(10)),
        sa.Column("name", sa.String(200)),
        sa.Column("kind", sa.String(16), nullable=False, server_default="stock"),
        sa.Column("sector", sa.String(100)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "price_bars",
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", sa.Float),
        sa.Column("high", sa.Float),
        sa.Column("low", sa.Float),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger),
    )
    op.create_table(
        "fundamental_facts",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("concept", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("instant", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("fiscal_year", sa.Integer),
        sa.Column("fiscal_period", sa.String(4)),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("filed", sa.Date, nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("value", sa.Numeric(30, 6), nullable=False),
        sa.UniqueConstraint(
            "security_id",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
            name="uq_fact_natural_key",
        ),
    )
    op.create_index("ix_fact_lookup", "fundamental_facts", ["security_id", "concept", "period_end"])
    op.create_table(
        "bond_yields",
        sa.Column("series_id", sa.String(32), primary_key=True),
        sa.Column("obs_date", sa.Date, primary_key=True),
        sa.Column("value", sa.Numeric(12, 6), nullable=False),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String(500)),
    )

    # Optional optimisation: turn price_bars into a TimescaleDB hypertable when the extension
    # is available. Plain Postgres (e.g. managed offerings without Timescale) works unchanged.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        available = bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
        if available:
            op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            op.execute(
                "SELECT create_hypertable('price_bars', 'ts', if_not_exists => TRUE, "
                "migrate_data => TRUE)"
            )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("bond_yields")
    op.drop_index("ix_fact_lookup", table_name="fundamental_facts")
    op.drop_table("fundamental_facts")
    op.drop_table("price_bars")
    op.drop_table("securities")
