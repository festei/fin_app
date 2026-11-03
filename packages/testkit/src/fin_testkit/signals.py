"""In-memory repositories and queries of the Signals & Alerts context. Same rules as the SQL ones
(the shared contract suite in ``packages/persistence/tests`` runs against both)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime

from fin_application.errors import ConcurrencyConflict
from fin_application.ports import OpportunityRow
from fin_domain.market_data import Security
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import (
    Alert,
    ChannelState,
    Delivery,
    LastAlert,
    ScoreEntry,
    Signal,
    StoredSignal,
)

Now = Callable[[], datetime]


class InMemoryScoreRepository:
    def __init__(self, scores: dict[int, ScoreEntry], securities: dict[Ticker, Security], now: Now):
        self.scores, self._securities, self._now = scores, securities, now

    def add(self, entry: ScoreEntry) -> ScoreEntry:
        if entry.ticker not in self._securities:
            raise NotFound("security", str(entry.ticker))
        entry.id = max(self.scores, default=0) + 1
        entry.computed_at = entry.computed_at or self._now()
        self.scores[entry.id] = deepcopy(entry)
        return entry

    def get(self, score_id: int) -> ScoreEntry | None:
        found = self.scores.get(score_id)
        return deepcopy(found) if found else None

    def _of(self, ticker: Ticker) -> list[ScoreEntry]:
        mine = [s for s in self.scores.values() if s.ticker == ticker]
        return sorted(mine, key=lambda s: (s.computed_at, s.id))

    def latest(self, ticker: Ticker) -> ScoreEntry | None:
        mine = self._of(ticker)
        return deepcopy(mine[-1]) if mine else None

    def latest_actionable(self, ticker: Ticker) -> ScoreEntry | None:
        mine = [s for s in self._of(ticker) if s.actionable]
        return deepcopy(mine[-1]) if mine else None

    def convictions_since_last_signal(
        self, ticker: Ticker, kinds: Sequence[str]
    ) -> list[float] | None:
        return _convictions_since(self.scores, self._signals, ticker, kinds)

    _signals: dict[int, StoredSignal]  # bound by the unit of work


def _convictions_since(scores, signals, ticker, kinds):
    last = max(
        (s for s in signals.values() if s.ticker == ticker and s.signal.kind in kinds),
        key=lambda s: s.id,
        default=None,
    )
    if last is None:
        return None
    later = [
        s for s in scores.values() if s.ticker == ticker and s.id > last.score_id and s.actionable
    ]
    return [s.conviction for s in sorted(later, key=lambda s: s.id)]


class InMemorySignalRepository:
    def __init__(self, signals: dict[int, StoredSignal], now: Now):
        self.signals, self._now = signals, now

    def add(self, ticker: Ticker, score_id: int, signal: Signal) -> StoredSignal:
        stored = StoredSignal(
            ticker=ticker,
            signal=signal,
            score_id=score_id,
            id=max(self.signals, default=0) + 1,
            created_at=self._now(),
        )
        self.signals[stored.id] = deepcopy(stored)
        return stored

    def get(self, signal_id: int) -> StoredSignal | None:
        found = self.signals.get(signal_id)
        return deepcopy(found) if found else None

    def pending_ids(self) -> list[int]:
        return sorted(i for i, s in self.signals.items() if s.is_pending)

    def save(self, stored: StoredSignal) -> None:
        if stored.id not in self.signals:
            raise LookupError(f"signal {stored.id!r} was never added")
        self.signals[stored.id] = deepcopy(stored)


class InMemoryAlertRepository:
    def __init__(self, alerts: dict[int, Alert]):
        self.alerts = alerts

    def add(self, alert: Alert) -> Alert:
        alert.id = max(self.alerts, default=0) + 1
        next_delivery = max((d.id for a in self.alerts.values() for d in a.deliveries), default=0)
        for offset, delivery in enumerate(alert.deliveries, start=1):
            delivery.id = next_delivery + offset
        self.alerts[alert.id] = deepcopy(alert)
        return alert

    def get_by_signal(self, signal_id: int) -> Alert | None:
        found = next((a for a in self.alerts.values() if a.signal_id == signal_id), None)
        return deepcopy(found) if found else None

    def last(self, ticker: Ticker, kind: str) -> LastAlert | None:
        mine = [a for a in self.alerts.values() if a.ticker == ticker and a.kind == kind]
        if not mine:
            return None
        newest = max(mine, key=lambda a: (a.created_at, a.id))
        return LastAlert(newest.created_at, newest.conviction)

    def open_deliveries(self, channel: str, max_attempts: int) -> list[tuple[Delivery, Alert]]:
        rows = [
            (d, a)
            for a in self.alerts.values()
            for d in a.deliveries
            if d.channel == channel and d.is_open and d.attempts < max_attempts
        ]
        return [(deepcopy(d), deepcopy(a)) for d, a in sorted(rows, key=lambda r: r[0].id)]

    def save_delivery(self, delivery: Delivery, *, expected_attempts: int | None = None) -> None:
        for alert in self.alerts.values():
            for i, held in enumerate(alert.deliveries):
                if held.id == delivery.id:
                    if expected_attempts is not None and not (
                        held.attempts == expected_attempts and held.is_open
                    ):
                        raise ConcurrencyConflict(f"delivery {delivery.id} changed meanwhile")
                    alert.deliveries[i] = deepcopy(delivery)
                    return
        raise LookupError(f"delivery {delivery.id!r} does not exist")

    def mark_read(self, alert_id: int, now: datetime) -> bool:
        alert = self.alerts.get(alert_id)
        if alert is None:
            return False
        alert.mark_read(now)
        return True


class InMemoryWatchlistRepository:
    def __init__(self, watched: set[Ticker], securities: dict[Ticker, Security]):
        self.watched, self._securities = watched, securities

    def add(self, ticker: Ticker) -> bool:
        if ticker not in self._securities:
            raise NotFound("security", str(ticker))
        if ticker in self.watched:
            return False
        self.watched.add(ticker)
        return True

    def remove(self, ticker: Ticker) -> bool:
        if ticker not in self.watched:
            return False
        self.watched.discard(ticker)
        return True

    def contains(self, ticker: Ticker) -> bool:
        return ticker in self.watched

    def tickers(self) -> list[str]:
        return sorted(str(t) for t in self.watched)


class InMemoryChannelStateRepository:
    def __init__(self, states: dict[str, ChannelState]):
        self.states = states

    def get(self, channel: str) -> ChannelState:
        return replace(self.states.get(channel) or ChannelState(channel))

    def save(self, state: ChannelState) -> None:
        self.states[state.channel] = replace(state)


class InMemoryBotCursorStore:
    def __init__(self, cursors: dict[str, int]):
        self.cursors = cursors

    def load(self, channel: str) -> int | None:
        return self.cursors.get(channel)

    def save(self, channel: str, value: int) -> None:
        self.cursors[channel] = value


class InMemoryOpportunityQuery:
    def __init__(self, scores: dict[int, ScoreEntry], securities: dict[Ticker, Security]):
        self._scores, self._securities = scores, securities

    def top(self, limit: int = 20, newer_than: datetime | None = None) -> list[OpportunityRow]:
        latest: dict[Ticker, ScoreEntry] = {}
        for s in sorted(self._scores.values(), key=lambda s: (s.computed_at, s.id)):
            if s.actionable:
                latest[s.ticker] = s
        rows = []
        for ticker, s in latest.items():
            sec = self._securities.get(ticker)
            if sec is None or not sec.active:
                continue
            if newer_than is not None and s.computed_at < newer_than:
                continue
            rows.append(
                OpportunityRow(ticker, sec.name, sec.sector, s.conviction, s.computed_at, s.details)
            )
        rows.sort(key=lambda r: (-r.conviction, str(r.ticker)))
        return rows[:limit]


class InMemoryAlertFeedQuery:
    def __init__(self, alerts: dict[int, Alert]):
        self._alerts = alerts

    def page(
        self, limit: int = 50, before_id: int | None = None, unread_only: bool = False
    ) -> list[Alert]:
        rows = sorted(self._alerts.values(), key=lambda a: -a.id)
        if before_id is not None:
            rows = [a for a in rows if a.id < before_id]
        if unread_only:
            rows = [a for a in rows if a.read_at is None]
        return [deepcopy(a) for a in rows[:limit]]
