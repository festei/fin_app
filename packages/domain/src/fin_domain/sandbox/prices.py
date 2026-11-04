"""The prices a portfolio decision needs, looked up beforehand.

The domain never reads storage. The ``Portfolio`` says which prices it will need
(``price_requests``), the application fetches exactly those, and the answer comes back as a
``PriceBook``. Build a fresh one before each step: what a step needs depends on what the previous
step did.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from fin_domain.market_data import PriceBar
from fin_domain.shared import Ticker


@dataclass(frozen=True)
class PriceRequests:
    last_close_for: frozenset[Ticker] = frozenset()  # the latest close at or before the as-of time
    close_at: frozenset[tuple[Ticker, datetime]] = frozenset()  # the stored close of an exact bar
    first_bar_from: frozenset[tuple[Ticker, datetime]] = frozenset()  # first bar at or after


@dataclass(frozen=True)
class PriceBook:
    last_closes: Mapping[Ticker, Decimal] = field(default_factory=dict)
    bar_closes: Mapping[tuple[Ticker, datetime], Decimal] = field(default_factory=dict)
    first_bars: Mapping[tuple[Ticker, datetime], PriceBar] = field(default_factory=dict)

    def last_close(self, ticker: Ticker) -> Decimal | None:
        return self.last_closes.get(ticker)

    def close_at(self, ticker: Ticker, ts: datetime) -> Decimal | None:
        return self.bar_closes.get((ticker, ts))

    def first_bar(self, ticker: Ticker, not_before: datetime) -> PriceBar | None:
        return self.first_bars.get((ticker, not_before))
