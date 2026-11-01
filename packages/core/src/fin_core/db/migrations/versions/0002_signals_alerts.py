"""scores, signals, alerts, alert_deliveries, watchlist, channel_state

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

BigIntPK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("conviction", sa.Float),
        sa.Column("all_abstained", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("signal_blocked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("explanation", sa.Text, nullable=False, server_default=""),
        sa.Column("details", JsonType, nullable=False),
    )
    op.create_index("ix_scores_security_computed", "scores", ["security_id", "computed_at"])

    op.create_table(
        "signals",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "score_id", BigIntPK, sa.ForeignKey("scores.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("conviction", sa.Float, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        _now(),
    )

    op.create_table(
        "alerts",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_id", BigIntPK, sa.ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("conviction", sa.Float, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        _now(),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alerts_security_kind", "alerts", ["security_id", "kind", "created_at"])

    op.create_table(
        "alert_deliveries",
        sa.Column("id", BigIntPK, primary_key=True, autoincrement=True),
        sa.Column(
            "alert_id", BigIntPK, sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String(500)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("alert_id", "channel", name="uq_delivery_alert_channel"),
    )

    op.create_table(
        "watchlist",
        sa.Column(
            "security_id",
            sa.Integer,
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "channel_state",
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column("muted_until", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("channel_state")
    op.drop_table("watchlist")
    op.drop_table("alert_deliveries")
    op.drop_index("ix_alerts_security_kind", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("signals")
    op.drop_index("ix_scores_security_computed", table_name="scores")
    op.drop_table("scores")
