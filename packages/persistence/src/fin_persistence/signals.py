"""SQL repositories and queries of the Signals & Alerts context.

Domain objects in and out, keyed by ``Ticker``; database ids and the dialect stay in here.
Datetimes leave this module aware (SQLite returns them naive; they are UTC).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from fin_application.errors import ConcurrencyConflict
from fin_application.ports import OpportunityRow
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import (
    Alert,
    ChannelState,
    Delivery,
    DeliveryStatus,
    LastAlert,
    ScoreEntry,
    Signal,
    StoredSignal,
)
from fin_persistence.job_runs import aware
from fin_persistence.score_codec import details_from_json, details_to_json
from fin_persistence.tables import (
    AlertDelivery,
    AlertRecord,
    ScoreRecord,
    SignalRecord,
    WatchlistEntry,
)
from fin_persistence.tables import ChannelState as ChannelRow
from fin_persistence.tables import Security as SecurityRow

MAX_ERROR = 500


def _security_id(session: Session, ticker: Ticker) -> int | None:
    return session.scalar(select(SecurityRow.id).where(SecurityRow.ticker == str(ticker)))


def _require_security_id(session: Session, ticker: Ticker) -> int:
    sid = _security_id(session, ticker)
    if sid is None:
        raise NotFound("security", str(ticker))
    return sid


def _score(row: ScoreRecord, ticker: str) -> ScoreEntry:
    return ScoreEntry(
        ticker=Ticker(ticker),
        as_of=aware(row.as_of),
        conviction=row.conviction,
        all_abstained=row.all_abstained,
        signal_blocked=row.signal_blocked,
        explanation=row.explanation,
        details=details_from_json(row.details),
        id=row.id,
        computed_at=aware(row.computed_at) if row.computed_at else None,
    )


class SqlScoreRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, entry: ScoreEntry) -> ScoreEntry:
        sid = _require_security_id(self._session, entry.ticker)
        row = ScoreRecord(
            security_id=sid,
            as_of=entry.as_of,
            conviction=entry.conviction,
            all_abstained=entry.all_abstained,
            signal_blocked=entry.signal_blocked,
            explanation=entry.explanation,
            details=details_to_json(entry.details),
        )
        if entry.computed_at is not None:
            row.computed_at = entry.computed_at
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        entry.id, entry.computed_at = row.id, aware(row.computed_at)
        return entry

    def get(self, score_id: int) -> ScoreEntry | None:
        found = self._session.execute(
            select(ScoreRecord, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == ScoreRecord.security_id)
            .where(ScoreRecord.id == score_id)
        ).first()
        return _score(*found) if found else None

    def _newest(self, ticker: Ticker, *where) -> ScoreEntry | None:
        sid = _security_id(self._session, ticker)
        if sid is None:
            return None
        row = self._session.scalar(
            select(ScoreRecord)
            .where(ScoreRecord.security_id == sid, *where)
            .order_by(ScoreRecord.computed_at.desc(), ScoreRecord.id.desc())
            .limit(1)
        )
        return _score(row, str(ticker)) if row else None

    def latest(self, ticker: Ticker) -> ScoreEntry | None:
        return self._newest(ticker)

    def latest_actionable(self, ticker: Ticker) -> ScoreEntry | None:
        return self._newest(
            ticker, ScoreRecord.conviction.is_not(None), ScoreRecord.signal_blocked.is_(False)
        )

    def convictions_since_last_signal(
        self, ticker: Ticker, kinds: Sequence[str]
    ) -> list[float] | None:
        sid = _security_id(self._session, ticker)
        if sid is None:
            return None
        last = self._session.scalar(
            select(SignalRecord)
            .where(SignalRecord.security_id == sid, SignalRecord.kind.in_(list(kinds)))
            .order_by(SignalRecord.id.desc())
            .limit(1)
        )
        if last is None:
            return None
        rows = self._session.scalars(
            select(ScoreRecord.conviction)
            .where(
                ScoreRecord.security_id == sid,
                ScoreRecord.id > last.score_id,
                ScoreRecord.conviction.is_not(None),
                ScoreRecord.signal_blocked.is_(False),
            )
            .order_by(ScoreRecord.id)
        )
        return list(rows)


class SqlSignalRepository:
    def __init__(self, session: Session):
        self._session = session

    def _stored(self, row: SignalRecord, ticker: str) -> StoredSignal:
        return StoredSignal(
            ticker=Ticker(ticker),
            signal=Signal(
                ticker=ticker,
                kind=row.kind,
                conviction=row.conviction,
                as_of=aware(row.as_of),
                reason=row.reason,
            ),
            score_id=row.score_id,
            id=row.id,
            created_at=aware(row.created_at) if row.created_at else None,
            processed_at=aware(row.processed_at) if row.processed_at else None,
            suppressed_reason=row.suppressed_reason,
        )

    def add(self, ticker: Ticker, score_id: int, signal: Signal) -> StoredSignal:
        row = SignalRecord(
            security_id=_require_security_id(self._session, ticker),
            score_id=score_id,
            kind=signal.kind,
            conviction=signal.conviction,
            as_of=signal.as_of,
            reason=signal.reason,
        )
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        return self._stored(row, str(ticker))

    def get(self, signal_id: int) -> StoredSignal | None:
        found = self._session.execute(
            select(SignalRecord, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == SignalRecord.security_id)
            .where(SignalRecord.id == signal_id)
        ).first()
        return self._stored(*found) if found else None

    def pending_ids(self) -> list[int]:
        q = select(SignalRecord.id).where(SignalRecord.processed_at.is_(None))
        return list(self._session.scalars(q.order_by(SignalRecord.id)))

    def save(self, stored: StoredSignal) -> None:
        row = self._session.get(SignalRecord, stored.id) if stored.id is not None else None
        if row is None:
            raise LookupError(f"signal {stored.id!r} was never added")
        row.processed_at = stored.processed_at
        row.suppressed_reason = stored.suppressed_reason
        self._session.flush()


def _delivery(row: AlertDelivery) -> Delivery:
    return Delivery(
        channel=row.channel,
        status=DeliveryStatus(row.status),
        attempts=row.attempts,
        error=row.error,
        last_attempt_at=aware(row.last_attempt_at) if row.last_attempt_at else None,
        delivered_at=aware(row.delivered_at) if row.delivered_at else None,
        id=row.id,
    )


def _alert(row: AlertRecord, ticker: str, deliveries: Sequence[AlertDelivery] = ()) -> Alert:
    return Alert(
        ticker=Ticker(ticker),
        kind=row.kind,
        severity=row.severity,
        conviction=row.conviction,
        title=row.title,
        body=row.body,
        created_at=aware(row.created_at),
        signal_id=row.signal_id,
        read_at=aware(row.read_at) if row.read_at else None,
        deliveries=[_delivery(d) for d in deliveries],
        id=row.id,
    )


class SqlAlertRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, alert: Alert) -> Alert:
        row = AlertRecord(
            security_id=_require_security_id(self._session, alert.ticker),
            signal_id=alert.signal_id,
            kind=alert.kind,
            severity=alert.severity,
            conviction=alert.conviction,
            title=alert.title,
            body=alert.body,
            created_at=alert.created_at,
        )
        self._session.add(row)
        self._session.flush()
        alert.id = row.id
        for d in alert.deliveries:
            drow = AlertDelivery(
                alert_id=row.id,
                channel=d.channel,
                status=d.status.value,
                attempts=d.attempts,
                error=d.error,
                last_attempt_at=d.last_attempt_at,
                delivered_at=d.delivered_at,
            )
            self._session.add(drow)
            self._session.flush()
            d.id = drow.id
        return alert

    def _with_deliveries(self, row: AlertRecord, ticker: str) -> Alert:
        deliveries = self._session.scalars(
            select(AlertDelivery).where(AlertDelivery.alert_id == row.id).order_by(AlertDelivery.id)
        )
        return _alert(row, ticker, list(deliveries))

    def get_by_signal(self, signal_id: int) -> Alert | None:
        found = self._session.execute(
            select(AlertRecord, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == AlertRecord.security_id)
            .where(AlertRecord.signal_id == signal_id)
        ).first()
        return self._with_deliveries(*found) if found else None

    def last(self, ticker: Ticker, kind: str) -> LastAlert | None:
        sid = _security_id(self._session, ticker)
        if sid is None:
            return None
        row = self._session.scalar(
            select(AlertRecord)
            .where(AlertRecord.security_id == sid, AlertRecord.kind == kind)
            .order_by(AlertRecord.created_at.desc(), AlertRecord.id.desc())
            .limit(1)
        )
        return LastAlert(aware(row.created_at), row.conviction) if row else None

    def open_deliveries(self, channel: str, max_attempts: int) -> list[tuple[Delivery, Alert]]:
        q = (
            select(AlertDelivery, AlertRecord, SecurityRow.ticker)
            .join(AlertRecord, AlertRecord.id == AlertDelivery.alert_id)
            .join(SecurityRow, SecurityRow.id == AlertRecord.security_id)
            .where(
                AlertDelivery.channel == channel,
                AlertDelivery.status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                ),
                AlertDelivery.attempts < max_attempts,
            )
            .order_by(AlertDelivery.id)
        )
        return [(_delivery(d), _alert(a, t, [d])) for d, a, t in self._session.execute(q).all()]

    def save_delivery(self, delivery: Delivery, *, expected_attempts: int | None = None) -> None:
        values = dict(
            status=delivery.status.value,
            attempts=delivery.attempts,
            error=delivery.error[:MAX_ERROR] if delivery.error else None,
            last_attempt_at=delivery.last_attempt_at,
            delivered_at=delivery.delivered_at,
        )
        stmt = update(AlertDelivery).where(AlertDelivery.id == delivery.id)
        if expected_attempts is not None:
            # the compare-and-set: of several processes that saw the same row, exactly one wins
            stmt = stmt.where(
                AlertDelivery.attempts == expected_attempts,
                AlertDelivery.status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                ),
            )
        result = self._session.execute(stmt.values(**values))
        if result.rowcount != 1:
            if expected_attempts is not None:
                raise ConcurrencyConflict(f"delivery {delivery.id} changed meanwhile")
            raise LookupError(f"delivery {delivery.id!r} does not exist")

    def mark_read(self, alert_id: int, now: datetime) -> bool:
        row = self._session.get(AlertRecord, alert_id)
        if row is None:
            return False
        if row.read_at is None:
            row.read_at = now
        return True


class SqlWatchlistRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, ticker: Ticker) -> bool:
        sid = _require_security_id(self._session, ticker)
        if self._session.get(WatchlistEntry, sid) is not None:
            return False
        self._session.add(WatchlistEntry(security_id=sid))
        self._session.flush()
        return True

    def remove(self, ticker: Ticker) -> bool:
        sid = _security_id(self._session, ticker)
        entry = self._session.get(WatchlistEntry, sid) if sid is not None else None
        if entry is None:
            return False
        self._session.delete(entry)
        self._session.flush()
        return True

    def contains(self, ticker: Ticker) -> bool:
        sid = _security_id(self._session, ticker)
        return sid is not None and self._session.get(WatchlistEntry, sid) is not None

    def tickers(self) -> list[str]:
        q = (
            select(SecurityRow.ticker)
            .join(WatchlistEntry, WatchlistEntry.security_id == SecurityRow.id)
            .order_by(SecurityRow.ticker)
        )
        return list(self._session.scalars(q))


class SqlChannelStateRepository:
    def __init__(self, session: Session):
        self._session = session

    def get(self, channel: str) -> ChannelState:
        row = self._session.get(ChannelRow, channel)
        return ChannelState(channel, aware(row.muted_until) if row and row.muted_until else None)

    def save(self, state: ChannelState) -> None:
        row = self._session.get(ChannelRow, state.channel)
        if row is None:
            self._session.add(ChannelRow(channel=state.channel, muted_until=state.muted_until))
        else:
            row.muted_until = state.muted_until
        self._session.flush()


class SqlBotCursorStore:
    def __init__(self, session: Session):
        self._session = session

    def load(self, channel: str) -> int | None:
        row = self._session.get(ChannelRow, channel)
        return row.update_cursor if row else None

    def save(self, channel: str, value: int) -> None:
        row = self._session.get(ChannelRow, channel)
        if row is None:
            self._session.add(ChannelRow(channel=channel, update_cursor=value))
        else:
            row.update_cursor = value
        self._session.flush()


class SqlOpportunityQuery:
    def __init__(self, session: Session):
        self._session = session

    def top(self, limit: int = 20, newer_than: datetime | None = None) -> list[OpportunityRow]:
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
            select(SecurityRow, ScoreRecord)
            .join(ScoreRecord, ScoreRecord.security_id == SecurityRow.id)
            .join(inner, inner.c.id == ScoreRecord.id)
            .where(inner.c.rn == 1, SecurityRow.active.is_(True))
            .where(ScoreRecord.computed_at >= newer_than if newer_than is not None else True)
            .order_by(ScoreRecord.conviction.desc(), SecurityRow.ticker)
            .limit(limit)
        )
        return [
            OpportunityRow(
                ticker=Ticker(sec.ticker),
                name=sec.name,
                sector=sec.sector,
                conviction=score.conviction,
                computed_at=aware(score.computed_at) if score.computed_at else None,
                details=details_from_json(score.details),
            )
            for sec, score in self._session.execute(q).all()
        ]


class SqlAlertFeedQuery:
    def __init__(self, session: Session):
        self._session = session

    def page(
        self, limit: int = 50, before_id: int | None = None, unread_only: bool = False
    ) -> list[Alert]:
        q = (
            select(AlertRecord, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == AlertRecord.security_id)
            .order_by(AlertRecord.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            q = q.where(AlertRecord.id < before_id)
        if unread_only:
            q = q.where(AlertRecord.read_at.is_(None))
        rows = self._session.execute(q).all()
        ids = [r.id for r, _ in rows]
        deliveries = (
            list(
                self._session.scalars(select(AlertDelivery).where(AlertDelivery.alert_id.in_(ids)))
            )
            if ids
            else []
        )
        return [_alert(r, t, [d for d in deliveries if d.alert_id == r.id]) for r, t in rows]
