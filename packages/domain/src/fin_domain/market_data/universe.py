"""The tracked universe and the rate series, validated.

Reading the files is an adapter's job (``fin_config``); the rules about what a valid universe is
live here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fin_domain.market_data.security import SecurityKind, SecurityUpdate
from fin_domain.shared import InvariantViolation, Ticker

ALLOWED_FIELDS = frozenset({"ticker", "name", "sector", "kind", "cik", "active"})
SERIES_KINDS = frozenset({"yield", "spread"})


def security_updates(rows: Sequence[Mapping[str, Any]]) -> list[SecurityUpdate]:
    """One update per universe row. Unknown fields, a duplicate ticker or a bad ``kind`` are
    errors; fields a row leaves out stay out of the update."""
    seen: set[Ticker] = set()
    updates: list[SecurityUpdate] = []
    for row in rows:
        unknown = set(row) - ALLOWED_FIELDS
        if unknown:
            raise InvariantViolation(
                f"{row.get('ticker')}: unknown universe fields {sorted(unknown)}"
            )
        ticker = Ticker(str(row["ticker"]))
        if ticker in seen:
            raise InvariantViolation(f"duplicate ticker in universe: {ticker}")
        seen.add(ticker)
        if row.get("kind", SecurityKind.STOCK.value) not in {k.value for k in SecurityKind}:
            raise InvariantViolation(
                f"{ticker}: kind must be one of {sorted(k.value for k in SecurityKind)}"
            )
        updates.append(SecurityUpdate(ticker, {k: v for k, v in row.items() if k != "ticker"}))
    return updates


@dataclass(frozen=True)
class BondSeries:
    """A FRED series tracked for bonds. Values are percent."""

    id: str
    kind: str
    name: str = ""


def bond_series(rows: Sequence[Mapping[str, Any]]) -> list[BondSeries]:
    ids = [str(r["id"]) for r in rows]
    if len(set(ids)) != len(ids):
        raise InvariantViolation("duplicate series id in bond_series.yml")
    for r in rows:
        if r.get("kind") not in SERIES_KINDS:
            raise InvariantViolation(f"{r.get('id')}: kind must be 'yield' or 'spread'")
    return [BondSeries(str(r["id"]), str(r["kind"]), str(r.get("name", ""))) for r in rows]
