"""Row-level helpers for putting data into the database directly: used by tests and the demo
seeder. The application never uses these; it goes through the repositories (which also enforce
the domain's rules)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_persistence.tables import (
    AlertRecord,
    ChannelState,
    ScoreRecord,
    SignalRecord,
    WatchlistEntry,
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


def insert_alert(session: Session, **fields: Any) -> AlertRecord:
    row = AlertRecord(**fields)
    session.add(row)
    session.flush()
    return row


def watchlist_add(session: Session, security_id: int) -> bool:
    if session.get(WatchlistEntry, security_id) is not None:
        return False
    session.add(WatchlistEntry(security_id=security_id))
    session.flush()
    return True


def set_muted_until(session: Session, channel: str, until: datetime | None) -> None:
    state = session.get(ChannelState, channel)
    if state is None:
        session.add(ChannelState(channel=channel, muted_until=until))
    else:
        state.muted_until = until
    session.flush()
