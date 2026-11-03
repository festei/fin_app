"""Alerts: which signals become alerts, what they say, and how a delivery progresses.

An ``Alert`` is created together with one ``Delivery`` per channel registered at that moment, so
a channel enabled later never replays old alerts (no row exists for it). A delivery moves
``pending -> sent`` or ``pending -> failed -> (retried) -> sent``; the number of attempts is both
the retry budget and the version that makes claiming a delivery safe between processes.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from fin_domain.shared import InvariantViolation, Ticker, ensure_utc
from fin_domain.signals.channels import IN_APP
from fin_domain.signals.decider import Signal
from fin_domain.signals.scores import ScoreDetails

KIND_LABEL = {"buy": "BUY", "sell": "SELL", "emerging": "EMERGING"}
COMMAND_HINT = "Use /why"  # the last line points at a Telegram bot command; the feed hides it
AGREE_AT = 70.0  # a philosophy "agrees" with a buy when it scores at least this
MAX_ERROR = 500


@dataclass(frozen=True)
class LastAlert:
    """The most recent alert of one kind for one security: what the cooldown compares against."""

    created_at: datetime
    conviction: float


@dataclass(frozen=True)
class AlertPolicy:
    cooldown_hours: float = 24.0
    material_change: float = 10.0
    sell_only_for_watchlist: bool = True
    # Delivery retries: attempt n+1 waits retry_backoff_minutes[n-1] (the last value repeats), so an
    # outage of several hours is survived instead of burning all attempts within minutes.
    max_delivery_attempts: int = 6
    retry_backoff_minutes: tuple[float, ...] = (2, 10, 30, 120, 360)
    # an attempt still "pending" after this long belongs to a process that died mid-send
    pending_timeout_minutes: float = 10.0
    severity: dict[str, str] = field(
        default_factory=lambda: {"buy": "high", "sell": "high", "emerging": "normal"}
    )

    def is_due(
        self, *, status: str, attempts: int, last_attempt_at: datetime | None, now: datetime
    ) -> bool:
        """Whether a delivery that is still owed (pending or failed) may be attempted now."""
        if attempts >= self.max_delivery_attempts:
            return False
        if last_attempt_at is None:
            return True  # created with the alert, never tried
        if status == "pending":
            wait = timedelta(minutes=self.pending_timeout_minutes)
        else:
            steps = self.retry_backoff_minutes or (0,)
            wait = timedelta(minutes=steps[min(max(attempts, 1) - 1, len(steps) - 1)])
        return ensure_utc(now) - ensure_utc(last_attempt_at) >= wait

    def severity_for(self, kind: str, watched: bool) -> str:
        return "high" if watched else self.severity.get(kind, "normal")

    def decide(
        self,
        *,
        kind: str,
        conviction: float,
        watched: bool,
        last_alert: LastAlert | None,
        now: datetime,
    ) -> tuple[bool, str]:
        """``(alert?, reason)``. The reason is recorded in logs even when suppressed."""
        if kind == "sell" and self.sell_only_for_watchlist and not watched:
            return False, "sell on a stock that is not on the watchlist"
        if last_alert is not None:
            age = ensure_utc(now) - ensure_utc(last_alert.created_at)
            if age < timedelta(hours=self.cooldown_hours):
                moved = abs(conviction - last_alert.conviction)
                if moved < self.material_change:
                    return (
                        False,
                        f"within the {self.cooldown_hours:g}h cooldown (moved {moved:.1f})",
                    )
                return True, f"conviction moved {moved:.1f} points since the last alert"
        return True, "new transition"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Delivery:
    channel: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    error: str | None = None
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    id: int | None = None

    @property
    def is_open(self) -> bool:
        """Still owed: new, or failed with attempts to spare (the budget is the policy's)."""
        return self.status in (DeliveryStatus.PENDING, DeliveryStatus.FAILED)

    def claim(self, now: datetime) -> None:
        """Take one attempt. Whoever persists this change first (compare-and-set on the number
        of attempts held before) owns the attempt; everyone else must not send."""
        if not self.is_open:
            raise InvariantViolation(f"a {self.status.value} delivery cannot be claimed")
        self.attempts += 1
        self.status = DeliveryStatus.PENDING
        self.last_attempt_at = ensure_utc(now)

    def mark_sent(self, now: datetime) -> None:
        self.status, self.error = DeliveryStatus.SENT, None
        self.delivered_at = ensure_utc(now)

    def mark_failed(self, error: str) -> None:
        self.status, self.error = DeliveryStatus.FAILED, error[:MAX_ERROR]
        self.delivered_at = None

    def skip(self, reason: str) -> None:
        self.status, self.error = DeliveryStatus.SKIPPED, reason[:MAX_ERROR]


@dataclass
class Alert:
    ticker: Ticker
    kind: str
    severity: str
    conviction: float
    title: str
    body: str
    created_at: datetime
    signal_id: int  # every alert comes from exactly one signal
    read_at: datetime | None = None
    deliveries: list[Delivery] = field(default_factory=list)
    id: int | None = None

    def __post_init__(self) -> None:
        self.created_at = ensure_utc(self.created_at, "created_at")

    def delivery(self, channel: str) -> Delivery | None:
        return next((d for d in self.deliveries if d.channel == channel), None)

    def mark_read(self, now: datetime) -> None:
        if self.read_at is None:
            self.read_at = ensure_utc(now)

    @property
    def visible_body(self) -> str:
        """The body without the line that only makes sense in a chat."""
        return "\n".join(
            line for line in self.body.split("\n") if not line.startswith(COMMAND_HINT)
        )

    def open_deliveries_for(
        self, channels: Collection[str], muted: Collection[str], now: datetime
    ) -> None:
        """One delivery per channel registered now: the in-app feed is ``sent`` immediately (the
        alert row is the feed), a muted channel is ``skipped``, every other one ``pending``."""
        for name in channels:
            if name == IN_APP:
                self.deliveries.append(
                    Delivery(
                        IN_APP,
                        DeliveryStatus.SENT,
                        attempts=1,
                        last_attempt_at=ensure_utc(now),
                        delivered_at=ensure_utc(now),
                    )
                )
            elif name in muted:
                self.deliveries.append(
                    Delivery(name, DeliveryStatus.SKIPPED, error="channel muted")
                )
            else:
                self.deliveries.append(Delivery(name))


def compose_alert(ticker: Ticker, signal: Signal, details: ScoreDetails | None) -> tuple[str, str]:
    """``(title, body)`` as plain text, short enough for a phone notification."""
    label = KIND_LABEL.get(signal.kind, signal.kind.upper())
    title = f"{ticker}: {label} (conviction {signal.conviction:.0f})"
    details = details or ScoreDetails()
    lines: list[str] = []

    evaluated = details.evaluated()
    if evaluated:
        lines.append(
            "Scores: " + ", ".join(f"{n} {r.score:.0f}" for n, r in sorted(evaluated.items()))
        )
    agree = {n: r for n, r in evaluated.items() if r.score >= AGREE_AT}
    if signal.kind != "sell" and agree:
        strongest = [c.name for r in agree.values() for c in r.criteria if c.passed][:3]
        lines.append(f"Agree: {', '.join(sorted(agree))}")
        if strongest:
            lines.append("Passing: " + "; ".join(strongest))
    if signal.kind == "sell" and details.guardrail_reasons:
        lines.append("Capped by guardrails: " + "; ".join(details.guardrail_reasons))

    m = details.metrics
    if m.price is not None:
        extra = f", P/E {m.pe:.1f}" if m.pe else ""
        extra += f", P/B {m.pb:.1f}" if m.pb else ""
        when = m.price_date.isoformat() if m.price_date else None
        lines.append(f"Price {m.price:.2f} ({when}){extra}")
    lines += [f"Note: {note}" for note in details.guardrail_notes]
    lines += [f"Data: {warning}" for warning in details.warnings]
    lines.append(f"{COMMAND_HINT} {ticker} for the full breakdown.")
    return title[:200], "\n".join(lines)
