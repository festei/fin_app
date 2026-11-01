"""paper-trading sandbox: portfolios, orders, positions, daily snapshots

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

BigIntPK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("initial_cash", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash", sa.Numeric(18, 4), nullable=False),
        sa.Column("fee_bps", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("strategy_config", JsonType),
        sa.Column("last_signal_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "sandbox_orders",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "portfolio_id",
            sa.Integer,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6)),
        sa.Column("notional", sa.Numeric(18, 4)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_id", sa.BigInteger),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("fill_quantity", sa.Numeric(18, 6)),
        sa.Column("fill_price", sa.Numeric(18, 6)),
        sa.Column("fee", sa.Numeric(18, 4)),
        sa.Column("reason", sa.String(200)),
    )
    op.create_index(
        "ix_sandbox_orders_portfolio_status", "sandbox_orders", ["portfolio_id", "status"]
    )
    op.create_table(
        "sandbox_positions",
        sa.Column(
            "portfolio_id",
            sa.Integer,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_cost", sa.Numeric(18, 6), nullable=False),
    )
    op.create_table(
        "portfolio_snapshots",
        sa.Column(
            "portfolio_id",
            sa.Integer,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", sa.Date, primary_key=True),
        sa.Column("cash", sa.Numeric(18, 4), nullable=False),
        sa.Column("positions_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_value", sa.Numeric(18, 4), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("portfolio_snapshots")
    op.drop_table("sandbox_positions")
    op.drop_index("ix_sandbox_orders_portfolio_status", table_name="sandbox_orders")
    op.drop_table("sandbox_orders")
    op.drop_table("portfolios")
