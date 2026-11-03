"""In-memory market-data repositories. They follow the same rules as the SQL ones (the shared
contract suite in ``packages/persistence/tests`` runs against both)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from fin_domain.market_data import Fact, PriceBar, Security, SecurityUpdate, YieldPoint
from fin_domain.shared import NotFound, Ticker


class InMemorySecurityRepository:
    def __init__(self, securities: dict[Ticker, Security]):
        self.securities = securities

    def upsert(self, updates: Sequence[SecurityUpdate]) -> int:
        for update in updates:
            held = self.securities.get(update.ticker)
            self.securities[update.ticker] = (
                held.merged_with(update) if held else Security.new(update)
            )
        return len(updates)

    def get(self, ticker: Ticker) -> Security | None:
        found = self.securities.get(ticker)
        return replace(found) if found else None

    def list(self, *, active_only: bool = True) -> list[Security]:
        return [
            replace(s)
            for _, s in sorted(self.securities.items(), key=lambda kv: str(kv[0]))
            if s.active or not active_only
        ]

    def save(self, security: Security) -> None:
        if security.ticker not in self.securities:
            raise LookupError(f"security {security.ticker} does not exist")
        self.securities[security.ticker] = replace(security)


class InMemoryPriceRepository:
    def __init__(self, bars: dict[Ticker, dict[datetime, PriceBar]], securities: dict):
        self.bars = bars
        self._securities = securities

    def _require(self, ticker: Ticker) -> None:
        if ticker not in self._securities:
            raise NotFound("security", str(ticker))

    def upsert(self, ticker: Ticker, bars: Sequence[PriceBar]) -> int:
        self._require(ticker)
        held = self.bars.setdefault(ticker, {})
        for bar in bars:
            held[bar.ts] = bar
        return len(bars)

    def latest(self, ticker: Ticker, as_of: datetime | None = None) -> PriceBar | None:
        held = [b for b in self.bars.get(ticker, {}).values() if as_of is None or b.ts <= as_of]
        return max(held, key=lambda b: b.ts, default=None)

    def tickers_with_bars(self) -> set[Ticker]:
        return {t for t, bars in self.bars.items() if bars}

    def close_at(self, ticker: Ticker, ts: datetime) -> float | None:
        bar = self.bars.get(ticker, {}).get(ts)
        return bar.close if bar else None

    def first_bar_between(self, ticker: Ticker, start: datetime, end: datetime) -> PriceBar | None:
        inside = [b for ts, b in self.bars.get(ticker, {}).items() if start <= ts <= end]
        return min(inside, key=lambda b: b.ts, default=None)

    def closes_since(self, ticker: Ticker, since: datetime) -> Mapping[datetime, float]:
        return {ts: b.close for ts, b in self.bars.get(ticker, {}).items() if ts >= since}


class InMemoryFactRepository:
    def __init__(self, facts: dict[Ticker, dict[tuple, Fact]], securities: dict):
        self.facts = facts
        self._securities = securities

    def add(self, ticker: Ticker, facts: Sequence[Fact]) -> int:
        if ticker not in self._securities:
            raise NotFound("security", str(ticker))
        held = self.facts.setdefault(ticker, {})
        for f in facts:
            key = (f.tag, f.unit, f.period_start, f.period_end, f.accession)
            if key not in held:
                held[key] = replace(f, sequence=len(held) + 1)
        return len(facts)

    def filed_up_to(
        self, ticker: Ticker, as_of: date, concepts: Sequence[str] | None = None
    ) -> list[Fact]:
        return [
            f
            for f in self.facts.get(ticker, {}).values()
            if f.filed <= as_of and (not concepts or f.concept in concepts)
        ]


class InMemoryYieldRepository:
    def __init__(self, yields: dict[str, dict[date, Decimal]]):
        self.yields = yields

    def upsert(self, series_id: str, points: Sequence[YieldPoint]) -> int:
        held = self.yields.setdefault(series_id, {})
        for p in points:
            held[p.on] = p.value
        return len(points)

    def latest_date(self, series_id: str) -> date | None:
        return max(self.yields.get(series_id, {}), default=None)

    def latest(self, series_id: str, as_of: date | None = None) -> YieldPoint | None:
        days = [d for d in self.yields.get(series_id, {}) if as_of is None or d <= as_of]
        if not days:
            return None
        day = max(days)
        return YieldPoint(day, self.yields[series_id][day])
