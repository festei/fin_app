"""SQL repositories of the Market Data context: domain objects in and out, keyed by ``Ticker``.

The statements live in ``repo.py`` (dialect-aware upserts); this module resolves tickers to ids
and maps rows to domain objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_domain.market_data import (
    Fact,
    PriceBar,
    Security,
    SecurityKind,
    SecurityUpdate,
    YieldPoint,
)
from fin_domain.shared import NotFound, Ticker
from fin_persistence import repo
from fin_persistence.job_runs import aware
from fin_persistence.tables import BondYield, FundamentalFact
from fin_persistence.tables import PriceBar as PriceBarRow
from fin_persistence.tables import Security as SecurityRow


def _security_row(session: Session, ticker: Ticker) -> SecurityRow:
    row = repo.get_security(session, str(ticker))
    if row is None:
        raise NotFound("security", str(ticker))
    return row


def _security(row: SecurityRow) -> Security:
    return Security(
        ticker=Ticker(row.ticker),
        kind=SecurityKind(row.kind),
        active=row.active,
        name=row.name,
        cik=row.cik,
        sector=row.sector,
    )


class SqlSecurityRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert(self, updates: Sequence[SecurityUpdate]) -> int:
        rows = [
            {
                "ticker": str(u.ticker),
                **{
                    k: (v.value if isinstance(v, SecurityKind) else v) for k, v in u.changes.items()
                },
            }
            for u in updates
        ]
        return repo.upsert_securities(self._session, rows)

    def get(self, ticker: Ticker) -> Security | None:
        row = repo.get_security(self._session, str(ticker))
        return _security(row) if row else None

    def list(self, *, active_only: bool = True) -> list[Security]:
        return [_security(r) for r in repo.list_securities(self._session, active_only=active_only)]

    def save(self, security: Security) -> None:
        row = repo.get_security(self._session, str(security.ticker))
        if row is None:
            raise LookupError(f"security {security.ticker} does not exist")
        row.kind = security.kind.value
        row.active, row.name, row.cik, row.sector = (
            security.active,
            security.name,
            security.cik,
            security.sector,
        )


class SqlPriceRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert(self, ticker: Ticker, bars: Sequence[PriceBar]) -> int:
        sid = _security_row(self._session, ticker).id
        rows = [
            {
                "ts": b.ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        return repo.upsert_prices(self._session, sid, rows)

    def latest(self, ticker: Ticker, as_of: datetime | None = None) -> PriceBar | None:
        sid = _security_row(self._session, ticker).id
        row = repo.latest_price(self._session, sid, as_of)
        if row is None:
            return None
        return PriceBar(
            ts=aware(row.ts),
            close=row.close,
            open=row.open,
            high=row.high,
            low=row.low,
            volume=row.volume,
        )

    def tickers_with_bars(self) -> set[Ticker]:
        with_bars = select(PriceBarRow.security_id).distinct()
        rows = self._session.scalars(
            select(SecurityRow.ticker).where(SecurityRow.id.in_(with_bars))
        )
        return {Ticker(t) for t in rows}

    def closes_since(self, ticker: Ticker, since: datetime) -> Mapping[datetime, float]:
        sid = _security_row(self._session, ticker).id
        rows = self._session.execute(
            select(PriceBarRow.ts, PriceBarRow.close).where(
                PriceBarRow.security_id == sid, PriceBarRow.ts >= since
            )
        )
        return {aware(ts): close for ts, close in rows}


class SqlFactRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, ticker: Ticker, facts: Sequence[Fact]) -> int:
        sid = _security_row(self._session, ticker).id
        rows = [{k: v for k, v in asdict(f).items() if k != "sequence"} for f in facts]
        return repo.upsert_facts(self._session, sid, rows)

    def filed_up_to(
        self, ticker: Ticker, as_of: date, concepts: Sequence[str] | None = None
    ) -> list[Fact]:
        sid = _security_row(self._session, ticker).id
        q = select(FundamentalFact).where(
            FundamentalFact.security_id == sid, FundamentalFact.filed <= as_of
        )
        if concepts:
            q = q.where(FundamentalFact.concept.in_(list(concepts)))
        return [
            Fact(
                concept=r.concept,
                tag=r.tag,
                unit=r.unit,
                period_start=r.period_start,
                period_end=r.period_end,
                instant=r.instant,
                fiscal_year=r.fiscal_year,
                fiscal_period=r.fiscal_period,
                form=r.form,
                filed=r.filed,
                accession=r.accession,
                value=r.value if isinstance(r.value, Decimal) else Decimal(str(r.value)),
                sequence=r.id,
            )
            for r in self._session.scalars(q.order_by(FundamentalFact.id))
        ]


class SqlYieldRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert(self, series_id: str, points: Sequence[YieldPoint]) -> int:
        return repo.upsert_yields(self._session, series_id, [(p.on, p.value) for p in points])

    def latest_date(self, series_id: str) -> date | None:
        return self._session.scalar(
            select(func.max(BondYield.obs_date)).where(BondYield.series_id == series_id)
        )

    def latest(self, series_id: str, as_of: date | None = None) -> YieldPoint | None:
        row = repo.latest_yield(self._session, series_id, as_of)
        return YieldPoint(row.obs_date, row.value) if row else None
