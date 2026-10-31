"""Tables for phase 1. Portable across Postgres (production) and SQLite (fast unit tests).

Design notes
* ``fundamental_facts`` keeps every filed version of a fact (restatements included) with its
  ``filed`` date, so queries can be made point-in-time: only what was public at ``as_of``.
* Instant facts (balance-sheet items) have ``period_start == period_end`` and ``instant=True``,
  which keeps the natural key free of NULLs so de-duplication works on every database.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# BigInteger autoincrement primary keys need Integer on SQLite.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
# JSONB on Postgres (indexable, compact), plain JSON on SQLite (tests)
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    cik: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="stock")
    sector: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceBar(Base):
    __tablename__ = "price_bars"

    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class FundamentalFact(Base):
    __tablename__ = "fundamental_facts"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
            name="uq_fact_natural_key",
        ),
        Index("ix_fact_lookup", "security_id", "concept", "period_end"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    concept: Mapped[str] = mapped_column(String(64), nullable=False)  # our normalized name
    tag: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. us-gaap/Revenues
    unit: Mapped[str] = mapped_column(String(16), nullable=False)  # USD, shares, USD/shares
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    instant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(4))  # FY, Q1..Q4
    form: Mapped[str] = mapped_column(String(16), nullable=False)  # 10-K, 10-Q, 10-K/A, ...
    filed: Mapped[date] = mapped_column(Date, nullable=False)
    accession: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)


class BondYield(Base):
    __tablename__ = "bond_yields"

    series_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. DGS10
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)  # percent


class IngestionRun(Base):
    """One row per ingestion job, so a stalled or failing pipeline is visible."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500))


class ScoreRecord(Base):
    """One scoring run for one security. Append-only history: the *state machine* behind signals.

    ``conviction`` is NULL when no philosophy could be evaluated. A row is an *actionable*
    baseline for the next transition only if conviction is set and no signal blockers applied
    (``signal_blocked`` False); otherwise a run on stale data would swallow the later buy.
    """

    __tablename__ = "scores"
    __table_args__ = (Index("ix_scores_security_computed", "security_id", "computed_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    conviction: Mapped[float | None] = mapped_column(Float)
    all_abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signal_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    score_id: Mapped[int] = mapped_column(
        ForeignKey("scores.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertRecord(Base):
    """An alert-worthy signal. Channel-independent; deliveries are tracked separately."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_security_kind", "security_id", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)  # high | normal
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertDelivery(Base):
    """Delivery of one alert through one channel. Rows exist only for channels that were
    registered when the alert was created, so enabling a channel later never replays history."""

    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel", name="uq_delivery_alert_channel"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # sent | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500))  # never contains credentials
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChannelState(Base):
    __tablename__ = "channel_state"

    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
