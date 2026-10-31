"""Repository functions. Idempotent writes (re-running an ingestion never duplicates rows)
and point-in-time reads (``as_of`` never sees data that was not yet public)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_core.db.models import BondYield, FundamentalFact, PriceBar, Security


def _insert(session: Session, model: type):
    name = session.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover
        raise NotImplementedError(f"unsupported database dialect: {name}")
    return insert(model)


def _chunks(rows: Sequence[dict[str, Any]], size: int = 2000) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


# --- securities -------------------------------------------------------------------------


def upsert_securities(session: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert new tickers; for existing ones update only the columns a row provides.

    A column that is absent from a row is left untouched on conflict, so syncing the universe
    (which has no ``cik``) never wipes a CIK that was resolved from the SEC. Passing a column
    explicitly, even as ``None``, does overwrite it. Rows are grouped by the columns they
    provide so each group is one executemany.
    """
    if not rows:
        return 0
    optional = ("cik", "name", "kind", "sector", "active")
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for r in rows:
        provided = tuple(c for c in optional if c in r)
        row = {"ticker": r["ticker"].upper(), **{c: r[c] for c in provided}}
        groups.setdefault(provided, []).append(row)
    for provided, group in groups.items():
        stmt = _insert(session, Security)
        if provided:
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker"], set_={c: stmt.excluded[c] for c in provided}
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["ticker"])
        session.execute(stmt, group)
    return len(rows)


def get_security(session: Session, ticker: str) -> Security | None:
    return session.scalar(select(Security).where(Security.ticker == ticker.upper()))


def list_securities(session: Session, *, active_only: bool = True) -> list[Security]:
    q = select(Security).order_by(Security.ticker)
    if active_only:
        q = q.where(Security.active.is_(True))
    return list(session.scalars(q))


# --- prices -----------------------------------------------------------------------------


def upsert_prices(session: Session, security_id: int, bars: Sequence[dict[str, Any]]) -> int:
    """``bars``: dicts with ts, open, high, low, close, volume. Later runs overwrite a bar."""
    if not bars:
        return 0
    rows = [{"security_id": security_id, **b} for b in bars]
    stmt = _insert(session, PriceBar)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "ts"],
        set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")},
    )
    for chunk in _chunks(rows):
        session.execute(stmt, list(chunk))
    return len(rows)


def latest_price(session: Session, security_id: int, as_of: datetime | None = None):
    q = select(PriceBar).where(PriceBar.security_id == security_id)
    if as_of is not None:
        q = q.where(PriceBar.ts <= as_of)
    return session.scalar(q.order_by(PriceBar.ts.desc()).limit(1))


# --- fundamentals -----------------------------------------------------------------------


def upsert_facts(session: Session, security_id: int, facts: Sequence[dict[str, Any]]) -> int:
    """Insert facts, ignoring ones already stored (same natural key). Returns rows offered."""
    if not facts:
        return 0
    rows = [{"security_id": security_id, **f} for f in facts]
    stmt = _insert(session, FundamentalFact).on_conflict_do_nothing(
        index_elements=[
            "security_id",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
        ]
    )
    for chunk in _chunks(rows):
        session.execute(stmt, list(chunk))
    return len(rows)


def facts_as_of(
    session: Session, security_id: int, as_of: date, concepts: Sequence[str] | None = None
) -> list[FundamentalFact]:
    """Point-in-time view: for every (tag, unit, period) the latest version FILED on or before
    ``as_of``. A restatement filed after ``as_of`` is invisible, so no look-ahead."""
    rn = (
        func.row_number()
        .over(
            partition_by=(
                FundamentalFact.tag,
                FundamentalFact.unit,
                FundamentalFact.period_start,
                FundamentalFact.period_end,
            ),
            order_by=(FundamentalFact.filed.desc(), FundamentalFact.id.desc()),
        )
        .label("rn")
    )
    inner = select(FundamentalFact.id, rn).where(
        FundamentalFact.security_id == security_id, FundamentalFact.filed <= as_of
    )
    if concepts:
        inner = inner.where(FundamentalFact.concept.in_(list(concepts)))
    sub = inner.subquery()
    q = (
        select(FundamentalFact)
        .join(sub, FundamentalFact.id == sub.c.id)
        .where(sub.c.rn == 1)
        .order_by(FundamentalFact.concept, FundamentalFact.period_end)
    )
    return list(session.scalars(q))


# --- bond yields ------------------------------------------------------------------------


def upsert_yields(session: Session, series_id: str, points: Sequence[tuple[date, Decimal]]) -> int:
    if not points:
        return 0
    rows = [{"series_id": series_id, "obs_date": d, "value": v} for d, v in points]
    stmt = _insert(session, BondYield)
    stmt = stmt.on_conflict_do_update(
        index_elements=["series_id", "obs_date"], set_={"value": stmt.excluded["value"]}
    )
    for chunk in _chunks(rows):
        session.execute(stmt, list(chunk))
    return len(rows)


def latest_yield(session: Session, series_id: str, as_of: date | None = None) -> BondYield | None:
    q = select(BondYield).where(BondYield.series_id == series_id)
    if as_of is not None:
        q = q.where(BondYield.obs_date <= as_of)
    return session.scalar(q.order_by(BondYield.obs_date.desc()).limit(1))
