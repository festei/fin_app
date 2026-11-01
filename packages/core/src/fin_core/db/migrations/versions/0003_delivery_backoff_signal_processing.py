"""delivery backoff, signal processing marker, persisted Telegram offset

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("signals") as batch:
        batch.add_column(sa.Column("processed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("suppressed_reason", sa.String(200)))
    with op.batch_alter_table("alert_deliveries") as batch:
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("channel_state") as batch:
        batch.add_column(sa.Column("update_cursor", sa.BigInteger))
    # Signals that exist already were handled by the old pipeline (alerted or suppressed).
    # Without this the new "finish unprocessed signals" sweep would re-evaluate them and could
    # alert on stale sells.
    op.execute("UPDATE signals SET processed_at = created_at WHERE processed_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("channel_state") as batch:
        batch.drop_column("update_cursor")
    with op.batch_alter_table("alert_deliveries") as batch:
        batch.drop_column("last_attempt_at")
    with op.batch_alter_table("signals") as batch:
        batch.drop_column("suppressed_reason")
        batch.drop_column("processed_at")
