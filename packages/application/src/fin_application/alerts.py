"""Use cases: turn signals into alerts, and deliver them.

Creating an alert is channel-independent and policy-driven; in the same transaction it writes one
delivery per channel registered at that moment, so a channel enabled later never replays old
alerts, and a crash cannot lose an alert. Sending happens afterwards, outside any transaction,
with a claim on each delivery first (compare-and-set on the attempts), so two processes cannot
send the same one twice. A failed delivery is retried with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from fin_application.errors import ChannelError, ConcurrencyConflict
from fin_application.ports import (
    AlertChannel,
    AlertMessage,
    AlertPolicySource,
    Clock,
    UnitOfWork,
    UnitOfWorkFactory,
)
from fin_domain.shared import NotFound
from fin_domain.signals import IN_APP, Alert, AlertPolicy, compose_alert

log = logging.getLogger(__name__)

ChannelsProvider = Callable[[], Sequence[AlertChannel]]


class InAppChannel:
    """The alert row itself is the in-app feed (API/UI read it); nothing is pushed anywhere."""

    name = IN_APP

    def send(self, message: AlertMessage) -> None:
        return None


@dataclass
class AlertOutcome:
    signal_id: int
    ticker: str
    alert_id: int | None
    suppressed: str | None  # reason, when no alert was created
    error: str | None = None  # the decision itself failed; it is retried on the next run


def attempt(channel: AlertChannel, message: AlertMessage) -> tuple[bool, str | None]:
    """One send: ``(sent, error)``. Never raises: a ``ChannelError`` is recorded with its
    (credential-free) message, any other exception by type only, so a buggy channel cannot take
    the run down or leak details."""
    try:
        channel.send(message)
        return True, None
    except ChannelError as exc:
        log.warning("delivery of alert %s via %s failed: %s", message.alert_id, channel.name, exc)
        return False, str(exc)
    except Exception as exc:
        log.warning(
            "delivery of alert %s via %s failed unexpectedly (%s)",
            message.alert_id,
            channel.name,
            type(exc).__name__,
        )
        return False, f"unexpected {type(exc).__name__}"


class DeliverDueAlerts:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        policy: AlertPolicySource,
        channels: ChannelsProvider,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._policy = policy
        self._channels = channels

    def execute(
        self,
        now: datetime | None = None,
        alert_ids: Collection[int] | None = None,
        *,
        channels: Sequence[AlertChannel] | None = None,
        policy: AlertPolicy | None = None,
    ) -> int:
        """Send every delivery that is due (new, or failed/stale and past its backoff). Returns
        how many were sent. A channel muted *now* has its owed deliveries skipped (an old failed
        alert should not arrive after the user asked for quiet)."""
        now = now or self._clock.now()
        policy = policy or self._policy.load()
        sent = 0
        for channel in channels if channels is not None else self._channels():
            if channel.name == IN_APP:
                continue
            with self._uow_factory() as uow:
                muted = uow.channels.get(channel.name).is_muted(now)
                due = []
                for delivery, alert in uow.alerts.open_deliveries(
                    channel.name, policy.max_delivery_attempts
                ):
                    if alert_ids is not None and alert.id not in alert_ids:
                        continue
                    if muted:
                        delivery.skip("channel muted")
                        uow.alerts.save_delivery(delivery)
                    elif policy.is_due(
                        status=delivery.status.value,
                        attempts=delivery.attempts,
                        last_attempt_at=delivery.last_attempt_at,
                        now=now,
                    ):
                        due.append((delivery, alert))
            for delivery, alert in due:
                held = delivery.attempts
                with self._uow_factory() as uow:
                    delivery.claim(now)
                    try:
                        uow.alerts.save_delivery(delivery, expected_attempts=held)
                    except ConcurrencyConflict:
                        continue  # another process took this attempt
                ok, error = attempt(channel, AlertMessage.from_alert(alert))
                if ok:
                    delivery.mark_sent(now)
                else:
                    delivery.mark_failed(error or "failed")
                with self._uow_factory() as uow:
                    uow.alerts.save_delivery(delivery)
                sent += ok
        return sent


class ProcessSignals:
    """Decide, per signal and in its own transaction, whether it becomes an alert; mark it
    processed either way. A signal whose decision failed stays unprocessed and is picked up by
    the next run, so one failure never loses the others."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        policy: AlertPolicySource,
        channels: ChannelsProvider,
        deliver: DeliverDueAlerts,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._policy = policy
        self._channels = channels
        self._deliver = deliver

    def create_alert(
        self,
        uow: UnitOfWork,
        signal_id: int,
        policy: AlertPolicy,
        now: datetime,
        channels: Sequence[AlertChannel],
    ) -> tuple[Alert | None, str, str]:
        """Apply the policy to one signal: ``(alert|None, reason, ticker)``. A new alert comes
        with its deliveries, in the caller's transaction."""
        stored = uow.signals.get(signal_id)
        if stored is None:
            raise NotFound("signal", signal_id)
        ticker = str(stored.ticker)
        existing = uow.alerts.get_by_signal(signal_id)
        if existing is not None:
            return existing, "already alerted", ticker
        watched = uow.watchlist.contains(stored.ticker)
        ok, reason = policy.decide(
            kind=stored.signal.kind,
            conviction=stored.signal.conviction,
            watched=watched,
            last_alert=uow.alerts.last(stored.ticker, stored.signal.kind),
            now=now,
        )
        if not ok:
            return None, reason, ticker
        entry = uow.scores.get(stored.score_id)
        title, body = compose_alert(stored.ticker, stored.signal, entry.details if entry else None)
        alert = Alert(
            ticker=stored.ticker,
            kind=stored.signal.kind,
            severity=policy.severity_for(stored.signal.kind, watched),
            conviction=stored.signal.conviction,
            title=title,
            body=body,
            created_at=now,  # the policy clock, so cooldowns are consistent and testable
            signal_id=signal_id,
        )
        names = [c.name for c in channels]
        muted = {n for n in names if uow.channels.get(n).is_muted(now)}
        alert.open_deliveries_for(names, muted, now)
        return uow.alerts.add(alert), reason, ticker

    def execute(
        self,
        signal_ids: Sequence[int],
        now: datetime | None = None,
        *,
        channels: Sequence[AlertChannel] | None = None,
    ) -> list[AlertOutcome]:
        now = now or self._clock.now()
        policy = self._policy.load()
        channels = list(channels if channels is not None else self._channels())
        outcomes: list[AlertOutcome] = []
        created: set[int] = set()
        for signal_id in signal_ids:
            ticker = "?"
            try:
                with self._uow_factory() as uow:
                    alert, reason, ticker = self.create_alert(uow, signal_id, policy, now, channels)
                    stored = uow.signals.get(signal_id)
                    stored.mark_processed(now, None if alert else reason)
                    uow.signals.save(stored)
                    alert_id = alert.id if alert else None
                if alert_id is not None and reason != "already alerted":
                    created.add(alert_id)
                if alert_id is None:
                    log.info("signal %s (%s) not alerted: %s", signal_id, ticker, reason)
                outcomes.append(
                    AlertOutcome(signal_id, ticker, alert_id, None if alert_id else reason)
                )
            except Exception as exc:
                log.exception("could not process signal %s", signal_id)
                outcomes.append(
                    AlertOutcome(
                        signal_id, ticker, None, None, error=f"{type(exc).__name__}: {exc}"[:200]
                    )
                )
        try:
            self._deliver.execute(now, created, channels=channels, policy=policy)
        except Exception:
            # deliveries stay pending/failed in storage and are retried by the next delivery pass
            log.exception("delivery pass failed")
        return outcomes
