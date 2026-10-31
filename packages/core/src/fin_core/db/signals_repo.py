"""Repository functions for scores, signals, alerts, deliveries, the watchlist and channel state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_core.db.models import (
    AlertDelivery,
    AlertRecord,
    ChannelState,
    ScoreRecord,
    Security,
    SignalRecord,
    WatchlistEntry,
)

# --- scores and signals ---------------------------------------------------------------------


def latest_actionable_score(session: Session, security_id: int) -> ScoreRecord | None:
    """The baseline for the next transition: the newest score that has a conviction and was not
    computed on blocked (stale/missing) data."""
    return session.scalar(
        select(ScoreRecord)
        .where(
            ScoreRecord.security_id == security_id,
            ScoreRecord.conviction.is_not(None),
            ScoreRecord.signal_blocked.is_(False),
        )
        .order_by(ScoreRecord.computed_at.desc(), ScoreRecord.id.desc())
        .limit(1)
    )


def insert_score(
    session: Session,
    *,
    security_id: int,
    as_of: datetime,
    conviction: float | None,
    all_abstained: bool,
    signal_blocked: bool,
    explanation: str,
    details: dict[str, Any],
) -> ScoreRecord:
    row = ScoreRecord(
        security_id=security_id,
        as_of=as_of,
        conviction=conviction,
        all_abstained=all_abstained,
        signal_blocked=signal_blocked,
        explanation=explanation,
        details=details,
    )
    session.add(row)
    session.flush()
    return row


def insert_signal(
    session: Session,
    *,
    security_id: int,
    score_id: int,
    kind: str,
    conviction: float,
    as_of: datetime,
    reason: str,
) -> SignalRecord:
    row = SignalRecord(
        security_id=security_id,
        score_id=score_id,
        kind=kind,
        conviction=conviction,
        as_of=as_of,
        reason=reason,
    )
    session.add(row)
    session.flush()
    return row


def latest_score(session: Session, security_id: int) -> ScoreRecord | None:
    """Newest score of any kind (for explaining the current state, e.g. Telegram /why)."""
    return session.scalar(
        select(ScoreRecord)
        .where(ScoreRecord.security_id == security_id)
        .order_by(ScoreRecord.computed_at.desc(), ScoreRecord.id.desc())
        .limit(1)
    )


def opportunities(session: Session, limit: int = 20) -> list[tuple[Security, ScoreRecord]]:
    """Latest actionable score per active security, best conviction first."""
    rn = (
        func.row_number()
        .over(
            partition_by=ScoreRecord.security_id,
            order_by=(ScoreRecord.computed_at.desc(), ScoreRecord.id.desc()),
        )
        .label("rn")
    )
    inner = (
        select(ScoreRecord.id, rn)
        .where(ScoreRecord.conviction.is_not(None), ScoreRecord.signal_blocked.is_(False))
        .subquery()
    )
    q = (
        select(Security, ScoreRecord)
        .join(ScoreRecord, ScoreRecord.security_id == Security.id)
        .join(inner, inner.c.id == ScoreRecord.id)
        .where(inner.c.rn == 1, Security.active.is_(True))
        .order_by(ScoreRecord.conviction.desc(), Security.ticker)
        .limit(limit)
    )
    return [(sec, score) for sec, score in session.execute(q).all()]


# --- watchlist ------------------------------------------------------------------------------


def watchlist_add(session: Session, security_id: int) -> bool:
    """True if newly added, False if it was already on the list."""
    if session.get(WatchlistEntry, security_id) is not None:
        return False
    session.add(WatchlistEntry(security_id=security_id))
    session.flush()
    return True


def watchlist_remove(session: Session, security_id: int) -> bool:
    result = session.execute(
        delete(WatchlistEntry).where(WatchlistEntry.security_id == security_id)
    )
    return result.rowcount > 0


def is_watched(session: Session, security_id: int) -> bool:
    return session.get(WatchlistEntry, security_id) is not None


def watchlist_tickers(session: Session) -> list[str]:
    q = (
        select(Security.ticker)
        .join(WatchlistEntry, WatchlistEntry.security_id == Security.id)
        .order_by(Security.ticker)
    )
    return list(session.scalars(q))


# --- alerts ---------------------------------------------------------------------------------


def last_alert(session: Session, security_id: int, kind: str) -> AlertRecord | None:
    return session.scalar(
        select(AlertRecord)
        .where(AlertRecord.security_id == security_id, AlertRecord.kind == kind)
        .order_by(AlertRecord.created_at.desc(), AlertRecord.id.desc())
        .limit(1)
    )


def insert_alert(session: Session, **fields: Any) -> AlertRecord:
    row = AlertRecord(**fields)
    session.add(row)
    session.flush()
    return row


def list_alerts(
    session: Session, *, limit: int = 50, before_id: int | None = None, unread_only: bool = False
) -> list[AlertRecord]:
    q = select(AlertRecord).order_by(AlertRecord.id.desc()).limit(limit)
    if before_id is not None:
        q = q.where(AlertRecord.id < before_id)
    if unread_only:
        q = q.where(AlertRecord.read_at.is_(None))
    return list(session.scalars(q))


def mark_alert_read(session: Session, alert_id: int, when: datetime) -> bool:
    alert = session.get(AlertRecord, alert_id)
    if alert is None:
        return False
    if alert.read_at is None:
        alert.read_at = when
    return True


# --- deliveries and channel state -----------------------------------------------------------


def get_delivery(session: Session, alert_id: int, channel: str) -> AlertDelivery | None:
    return session.scalar(
        select(AlertDelivery).where(
            AlertDelivery.alert_id == alert_id, AlertDelivery.channel == channel
        )
    )


def deliveries_for(session: Session, alert_ids: Sequence[int]) -> list[AlertDelivery]:
    if not alert_ids:
        return []
    return list(
        session.scalars(select(AlertDelivery).where(AlertDelivery.alert_id.in_(list(alert_ids))))
    )


def failed_deliveries(
    session: Session, channel: str, max_attempts: int
) -> list[tuple[AlertDelivery, AlertRecord]]:
    q = (
        select(AlertDelivery, AlertRecord)
        .join(AlertRecord, AlertRecord.id == AlertDelivery.alert_id)
        .where(
            AlertDelivery.channel == channel,
            AlertDelivery.status == "failed",
            AlertDelivery.attempts < max_attempts,
        )
        .order_by(AlertDelivery.id)
    )
    return [(d, a) for d, a in session.execute(q).all()]


def muted_until(session: Session, channel: str) -> datetime | None:
    state = session.get(ChannelState, channel)
    return state.muted_until if state else None


def set_muted_until(session: Session, channel: str, until: datetime | None) -> None:
    state = session.get(ChannelState, channel)
    if state is None:
        session.add(ChannelState(channel=channel, muted_until=until))
    else:
        state.muted_until = until
    session.flush()
