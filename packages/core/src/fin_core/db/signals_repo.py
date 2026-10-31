"""Repository functions for scores, signals, alerts, deliveries, the watchlist and channel state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
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


def opportunities(
    session: Session, limit: int = 20, newer_than: datetime | None = None
) -> list[tuple[Security, ScoreRecord]]:
    """Latest actionable score per active security, best conviction first. With ``newer_than``
    only scores computed at or after it are listed, so an old view from before a long data
    outage cannot sit in the ranking."""
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
        .where(ScoreRecord.computed_at >= newer_than if newer_than is not None else True)
        .order_by(ScoreRecord.conviction.desc(), Security.ticker)
        .limit(limit)
    )
    return [(sec, score) for sec, score in session.execute(q).all()]


def pending_signal_ids(session: Session) -> list[int]:
    """Signals whose alert decision is still owed (an interrupted run left them unprocessed)."""
    q = select(SignalRecord.id).where(SignalRecord.processed_at.is_(None)).order_by(SignalRecord.id)
    return list(session.scalars(q))


def mark_signal_processed(
    session: Session, signal_id: int, when: datetime, suppressed_reason: str | None = None
) -> None:
    signal = session.get(SignalRecord, signal_id)
    signal.processed_at = when
    signal.suppressed_reason = (suppressed_reason or None) and suppressed_reason[:200]
    session.flush()


def armed_state(
    session: Session, security_id: int, *, buy_rearm_below: float, sell_rearm_above: float
) -> tuple[bool, bool]:
    """``(buy_armed, sell_armed)``: hysteresis against flapping around a threshold.

    After a buy (or emerging) signal, another buy needs conviction to have fallen below
    ``buy_rearm_below`` since; after a sell, another sell needs it to have recovered to at least
    ``sell_rearm_above``. Only actionable scores count. With no earlier signal of that kind the
    side is armed.
    """

    def armed(kinds: tuple[str, ...], rearmed) -> bool:
        last = session.scalar(
            select(SignalRecord)
            .where(SignalRecord.security_id == security_id, SignalRecord.kind.in_(kinds))
            .order_by(SignalRecord.id.desc())
            .limit(1)
        )
        if last is None:
            return True
        crossed = session.scalar(
            select(func.count())
            .select_from(ScoreRecord)
            .where(
                ScoreRecord.security_id == security_id,
                ScoreRecord.id > last.score_id,
                ScoreRecord.conviction.is_not(None),
                ScoreRecord.signal_blocked.is_(False),
                rearmed,
            )
        )
        return bool(crossed)

    return (
        armed(("buy", "emerging"), ScoreRecord.conviction < buy_rearm_below),
        armed(("sell",), ScoreRecord.conviction >= sell_rearm_above),
    )


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


def open_deliveries(
    session: Session, channel: str, max_attempts: int
) -> list[tuple[AlertDelivery, AlertRecord]]:
    """Deliveries still owed on a channel: ``pending`` (new, or an attempt that may have died) and
    ``failed``, with attempts left. Whether one is *due* (backoff) is decided by the caller."""
    q = (
        select(AlertDelivery, AlertRecord)
        .join(AlertRecord, AlertRecord.id == AlertDelivery.alert_id)
        .where(
            AlertDelivery.channel == channel,
            AlertDelivery.status.in_(("pending", "failed")),
            AlertDelivery.attempts < max_attempts,
        )
        .order_by(AlertDelivery.id)
    )
    return [(d, a) for d, a in session.execute(q).all()]


def add_delivery(
    session: Session,
    alert_id: int,
    channel: str,
    status: str,
    *,
    attempts: int = 0,
    error: str | None = None,
    when: datetime | None = None,
) -> AlertDelivery:
    row = AlertDelivery(
        alert_id=alert_id,
        channel=channel,
        status=status,
        attempts=attempts,
        error=error,
        last_attempt_at=when if attempts else None,
        delivered_at=when if status == "sent" else None,
    )
    session.add(row)
    session.flush()
    return row


def claim_delivery(
    session: Session, delivery_id: int, expected_attempts: int, now: datetime
) -> bool:
    """Atomically take one delivery attempt. The compare-and-set on ``attempts`` means that of
    several processes that saw the same row, exactly one wins; the others must not send."""
    result = session.execute(
        update(AlertDelivery)
        .where(
            AlertDelivery.id == delivery_id,
            AlertDelivery.attempts == expected_attempts,
            AlertDelivery.status.in_(("pending", "failed")),
        )
        .values(attempts=expected_attempts + 1, status="pending", last_attempt_at=now)
    )
    return result.rowcount == 1


def finish_delivery(
    session: Session, delivery_id: int, status: str, error: str | None, now: datetime
) -> None:
    session.execute(
        update(AlertDelivery)
        .where(AlertDelivery.id == delivery_id)
        .values(
            status=status,
            error=error[:500] if error else None,
            delivered_at=now if status == "sent" else None,
        )
    )


def skip_delivery(session: Session, delivery_id: int, reason: str) -> None:
    session.execute(
        update(AlertDelivery)
        .where(AlertDelivery.id == delivery_id)
        .values(status="skipped", error=reason[:500])
    )


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


def update_cursor(session: Session, channel: str) -> int | None:
    state = session.get(ChannelState, channel)
    return state.update_cursor if state else None


def set_update_cursor(session: Session, channel: str, value: int) -> None:
    state = session.get(ChannelState, channel)
    if state is None:
        session.add(ChannelState(channel=channel, update_cursor=value))
    else:
        state.update_cursor = value
    session.flush()
