"""SQL read side for the securities pages: the searchable universe and the score history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from fin_application.ports import ScorePointRow, SignalPointRow
from fin_domain.market_data import Security
from fin_domain.shared import Ticker
from fin_persistence.job_runs import aware
from fin_persistence.market_data import _security
from fin_persistence.tables import ScoreRecord, SignalRecord
from fin_persistence.tables import Security as SecurityRow


def _like_escape(text_: str) -> str:
    """Make user text match literally inside LIKE: escape the escape character, % and _."""
    return text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlSecuritySearchQuery:
    def __init__(self, session: Session):
        self._session = session

    def search(
        self,
        q: str | None = None,
        sector: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Security]:
        query = select(SecurityRow).where(SecurityRow.active.is_(True))
        if q and q.strip():
            like = _like_escape(q.strip())
            query = query.where(
                or_(
                    SecurityRow.ticker.ilike(f"{like}%", escape="\\"),
                    SecurityRow.name.ilike(f"%{like}%", escape="\\"),
                )
            )
        if sector:
            query = query.where(SecurityRow.sector == sector)
        if kind:
            query = query.where(SecurityRow.kind == kind)
        rows = self._session.scalars(query.order_by(SecurityRow.ticker).limit(limit))
        return [_security(r) for r in rows]


class SqlScoreHistoryQuery:
    def __init__(self, session: Session):
        self._session = session

    def _sid(self, ticker: Ticker) -> int | None:
        return self._session.scalar(select(SecurityRow.id).where(SecurityRow.ticker == str(ticker)))

    def scores(self, ticker: Ticker, since: datetime, limit: int) -> list[ScorePointRow]:
        sid = self._sid(ticker)
        if sid is None:
            return []
        rows = self._session.scalars(
            select(ScoreRecord)
            .where(ScoreRecord.security_id == sid, ScoreRecord.computed_at >= since)
            .order_by(ScoreRecord.computed_at.desc(), ScoreRecord.id.desc())
            .limit(limit)
        ).all()[::-1]
        return [
            ScorePointRow(
                computed_at=aware(r.computed_at) if r.computed_at else None,
                as_of=aware(r.as_of),
                conviction=r.conviction,
                signal_blocked=r.signal_blocked,
                all_abstained=r.all_abstained,
            )
            for r in rows
        ]

    def signals(self, ticker: Ticker, since: datetime) -> list[SignalPointRow]:
        sid = self._sid(ticker)
        if sid is None:
            return []
        rows = self._session.scalars(
            select(SignalRecord)
            .where(SignalRecord.security_id == sid, SignalRecord.as_of >= since)
            .order_by(SignalRecord.id)
        )
        return [SignalPointRow(r.kind, r.conviction, aware(r.as_of)) for r in rows]
