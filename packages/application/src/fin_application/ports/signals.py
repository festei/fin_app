"""Storage ports of the Signals & Alerts context, keyed by ``Ticker``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fin_domain.shared import Ticker
from fin_domain.signals import (
    Alert,
    ChannelState,
    Delivery,
    LastAlert,
    ScoreDetails,
    ScoreEntry,
    Signal,
    StoredSignal,
)


class ScoreRepository(Protocol):
    """Scoring runs are append-only. Reads for a ticker we do not hold answer "nothing"."""

    def add(self, entry: ScoreEntry) -> ScoreEntry:
        """Store a run and return it with its ``id`` and ``computed_at``."""
        ...

    def get(self, score_id: int) -> ScoreEntry | None: ...

    def latest(self, ticker: Ticker) -> ScoreEntry | None:
        """Newest run of any kind (for explaining the current state)."""
        ...

    def latest_actionable(self, ticker: Ticker) -> ScoreEntry | None:
        """The baseline for the next transition (``ScoreEntry.actionable``)."""
        ...

    def convictions_since_last_signal(
        self, ticker: Ticker, kinds: Sequence[str]
    ) -> list[float] | None:
        """Actionable convictions stored after the newest signal of one of ``kinds``, oldest
        first; ``None`` if there has never been such a signal."""
        ...


class SignalRepository(Protocol):
    def add(self, ticker: Ticker, score_id: int, signal: Signal) -> StoredSignal: ...

    def get(self, signal_id: int) -> StoredSignal | None: ...

    def after(self, signal_id: int) -> list[StoredSignal]:
        """Signals stored after ``signal_id``, oldest first."""
        ...

    def latest_id(self) -> int:
        """The newest signal's id, or 0 if there is none."""
        ...

    def pending_ids(self) -> list[int]:
        """Signals whose alert decision is still owed (an interrupted run left them)."""
        ...

    def save(self, stored: StoredSignal) -> None:
        """Store the alert-decision state (``processed_at``, ``suppressed_reason``)."""
        ...


class AlertRepository(Protocol):
    def add(self, alert: Alert) -> Alert:
        """Store an alert with its deliveries, in the caller's transaction."""
        ...

    def get_by_signal(self, signal_id: int) -> Alert | None: ...

    def last(self, ticker: Ticker, kind: str) -> LastAlert | None: ...

    def open_deliveries(self, channel: str, max_attempts: int) -> list[tuple[Delivery, Alert]]:
        """Deliveries still owed on a channel (pending or failed) with attempts left. Whether one
        is due (backoff) is the policy's call."""
        ...

    def save_delivery(self, delivery: Delivery, *, expected_attempts: int | None = None) -> None:
        """Store a delivery's state. With ``expected_attempts`` the write only happens if the
        stored delivery still has that many attempts and is open (compare-and-set); otherwise
        ``ConcurrencyConflict`` is raised and nothing changes."""
        ...

    def mark_read(self, alert_id: int, now: datetime) -> bool:
        """``False`` if there is no such alert."""
        ...


class WatchlistRepository(Protocol):
    def add(self, ticker: Ticker) -> bool:
        """``True`` if newly added."""
        ...

    def remove(self, ticker: Ticker) -> bool: ...

    def contains(self, ticker: Ticker) -> bool: ...

    def tickers(self) -> list[str]: ...


class ChannelStateRepository(Protocol):
    def get(self, channel: str) -> ChannelState:
        """Never ``None``: a channel nobody has touched is simply not muted."""
        ...

    def save(self, state: ChannelState) -> None: ...


class BotCursorStore(Protocol):
    """Where a chat bot remembers how far it has read, so a restart never replays a command."""

    def load(self, channel: str) -> int | None: ...

    def save(self, channel: str, value: int) -> None: ...


# --- read models --------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpportunityRow:
    ticker: Ticker
    name: str | None
    sector: str | None
    conviction: float
    computed_at: datetime | None
    details: ScoreDetails


class OpportunityQuery(Protocol):
    def top(self, limit: int = 20, newer_than: datetime | None = None) -> list[OpportunityRow]:
        """Latest actionable score per active security, best conviction first. With
        ``newer_than`` only scores computed at or after it, so an old view from before a long
        data outage cannot sit in the ranking."""
        ...


class AlertFeedQuery(Protocol):
    def page(
        self, limit: int = 50, before_id: int | None = None, unread_only: bool = False
    ) -> list[Alert]:
        """Newest first, each with its deliveries."""
        ...
