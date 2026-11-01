"""Signals -> alerts -> deliveries.

Alert *creation* is channel-independent and policy-driven. In the same transaction it writes one
delivery row per channel registered at that moment (the in-app feed is delivered by the row
itself); sending happens afterwards, outside any transaction, with an atomic claim on each row.
So a channel switched on later never receives earlier alerts, a crash cannot lose an alert or
send it twice, retries back off, and two processes calling ``send_due`` cannot double-send.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from fin_core.alerts.base import IN_APP, AlertChannel, AlertMessage, ChannelError
from fin_core.alerts.policy import AlertPolicy
from fin_core.db import signals_repo
from fin_core.db.models import AlertRecord, ScoreRecord, Security, SignalRecord
from fin_core.db.session import session_scope

log = logging.getLogger(__name__)
_AGREE_AT = 70.0  # a philosophy "agrees" with a buy when it scores at least this
KIND_LABEL = {"buy": "BUY", "sell": "SELL", "emerging": "EMERGING"}


# The last line of an alert points at a Telegram bot command; the in-app feed leaves it out.
TELEGRAM_HINT = "Use /why"


def build_message_text(
    sec: Security, signal: SignalRecord, score: ScoreRecord | None
) -> tuple[str, str]:
    """``(title, body)`` as plain text, short enough for a phone notification."""
    label = KIND_LABEL.get(signal.kind, signal.kind.upper())
    title = f"{sec.ticker}: {label} (conviction {signal.conviction:.0f})"
    details = (score.details if score else None) or {}
    lines: list[str] = []

    philosophies = details.get("philosophies", {})
    evaluated = {n: p for n, p in philosophies.items() if p.get("evaluated", True)}
    if evaluated:
        lines.append(
            "Scores: " + ", ".join(f"{n} {p['score']:.0f}" for n, p in sorted(evaluated.items()))
        )
    agree = {n: p for n, p in evaluated.items() if p["score"] >= _AGREE_AT}
    if signal.kind != "sell" and agree:
        strongest = [
            c["name"] for p in agree.values() for c in p.get("criteria", []) if c.get("passed")
        ][:3]
        lines.append(f"Agree: {', '.join(sorted(agree))}")
        if strongest:
            lines.append("Passing: " + "; ".join(strongest))
    if signal.kind == "sell":
        reasons = details.get("guardrail_reasons") or []
        if reasons:
            lines.append("Capped by guardrails: " + "; ".join(reasons))

    metrics = details.get("metrics", {})
    if metrics.get("price") is not None:
        extra = f", P/E {metrics['pe']:.1f}" if metrics.get("pe") else ""
        extra += f", P/B {metrics['pb']:.1f}" if metrics.get("pb") else ""
        lines.append(f"Price {metrics['price']:.2f} ({metrics.get('price_date')}){extra}")
    for note in details.get("guardrail_notes") or []:
        lines.append(f"Note: {note}")
    for warning in details.get("warnings") or []:
        lines.append(f"Data: {warning}")
    lines.append(f"{TELEGRAM_HINT} {sec.ticker} for the full breakdown.")
    return title[:200], "\n".join(lines)


@dataclass
class AlertOutcome:
    signal_id: int
    ticker: str
    alert_id: int | None
    suppressed: str | None  # reason, when no alert was created
    error: str | None = None  # the decision itself failed; it is retried on the next run


def _muted(session: Session, channel: str, now: datetime) -> bool:
    until = signals_repo.muted_until(session, channel)
    if until is None:
        return False
    return (until if until.tzinfo else until.replace(tzinfo=UTC)) > now


def _message(alert: AlertRecord, ticker: str) -> AlertMessage:
    return AlertMessage(
        alert_id=alert.id,
        ticker=ticker,
        kind=alert.kind,
        severity=alert.severity,
        conviction=alert.conviction,
        title=alert.title,
        body=alert.body,
    )


def create_alert(
    session: Session,
    signal_id: int,
    policy: AlertPolicy,
    now: datetime,
    channels: list[AlertChannel],
) -> tuple[AlertRecord | None, str, str]:
    """Apply the policy to one signal. Returns ``(alert|None, reason, ticker)``.

    A new alert comes with its delivery rows, all in the caller's transaction: the in-app feed is
    ``sent`` immediately, a muted channel is ``skipped``, every other channel is ``pending``.
    """
    signal = session.get(SignalRecord, signal_id)
    sec = session.get(Security, signal.security_id)
    existing = session.scalar(select(AlertRecord).where(AlertRecord.signal_id == signal_id))
    if existing is not None:
        return existing, "already alerted", sec.ticker
    watched = signals_repo.is_watched(session, sec.id)
    ok, reason = policy.decide(
        kind=signal.kind,
        conviction=signal.conviction,
        watched=watched,
        last_alert=signals_repo.last_alert(session, sec.id, signal.kind),
        now=now,
    )
    if not ok:
        return None, reason, sec.ticker
    title, body = build_message_text(sec, signal, session.get(ScoreRecord, signal.score_id))
    alert = signals_repo.insert_alert(
        session,
        security_id=sec.id,
        signal_id=signal.id,
        kind=signal.kind,
        severity=policy.severity_for(signal.kind, watched),
        conviction=signal.conviction,
        title=title,
        body=body,
        created_at=now,  # the policy clock, so cooldowns are consistent and testable
    )
    for channel in channels:
        if channel.name == IN_APP:
            signals_repo.add_delivery(session, alert.id, IN_APP, "sent", attempts=1, when=now)
        elif _muted(session, channel.name, now):
            signals_repo.add_delivery(
                session, alert.id, channel.name, "skipped", error="channel muted"
            )
        else:
            signals_repo.add_delivery(session, alert.id, channel.name, "pending")
    return alert, reason, sec.ticker


def _attempt(channel: AlertChannel, message: AlertMessage) -> tuple[str, str | None]:
    """One send. Never raises: a ChannelError is recorded with its (credential-free) message, any
    other exception by type only, so a buggy channel cannot take the run down or leak details."""
    try:
        channel.send(message)
        return "sent", None
    except ChannelError as exc:
        log.warning("delivery of alert %s via %s failed: %s", message.alert_id, channel.name, exc)
        return "failed", str(exc)
    except Exception as exc:
        log.warning(
            "delivery of alert %s via %s failed unexpectedly (%s)",
            message.alert_id,
            channel.name,
            type(exc).__name__,
        )
        return "failed", f"unexpected {type(exc).__name__}"


def send_due(
    engine: Engine,
    channels: list[AlertChannel],
    policy: AlertPolicy,
    now: datetime | None = None,
    alert_ids: set[int] | None = None,
) -> int:
    """Send every delivery that is due (new, or failed/stale and past its backoff). Returns how
    many were sent.

    Safe to call from several processes at once: each send is preceded by an atomic claim, so a
    delivery is attempted by exactly one of them. HTTP happens outside any DB transaction, and
    every outcome is recorded in its own short one. A channel muted *now* has its owed deliveries
    skipped (an old failed alert should not arrive after the user asked for quiet).
    """
    now = now or datetime.now(UTC)
    sent = 0
    for channel in channels:
        if channel.name == IN_APP:
            continue
        with session_scope(engine) as s:
            muted = _muted(s, channel.name, now)
            due: list[tuple[int, int, int]] = []
            for delivery, alert in signals_repo.open_deliveries(
                s, channel.name, policy.max_delivery_attempts
            ):
                if alert_ids is not None and alert.id not in alert_ids:
                    continue
                if muted:
                    signals_repo.skip_delivery(s, delivery.id, "channel muted")
                elif policy.is_due(
                    status=delivery.status,
                    attempts=delivery.attempts,
                    last_attempt_at=delivery.last_attempt_at,
                    now=now,
                ):
                    due.append((delivery.id, delivery.attempts, alert.id))
        for delivery_id, attempts, alert_id in due:
            with session_scope(engine) as s:
                if not signals_repo.claim_delivery(s, delivery_id, attempts, now):
                    continue  # another process took this attempt
                alert = s.get(AlertRecord, alert_id)
                message = _message(alert, s.get(Security, alert.security_id).ticker)
            status, error = _attempt(channel, message)
            with session_scope(engine) as s:
                signals_repo.finish_delivery(s, delivery_id, status, error, now)
            sent += status == "sent"
    return sent


# The worker's periodic retry and the scoring run both just drain what is due.
retry_failed = send_due


def process_signals(
    engine: Engine,
    signal_ids: list[int],
    policy: AlertPolicy,
    channels: list[AlertChannel],
    now: datetime | None = None,
) -> list[AlertOutcome]:
    """Turn signals into alerts and deliver them.

    One transaction per signal decides (and marks the signal processed), so a failure on one never
    loses the others; a signal whose decision failed stays unprocessed and is picked up again by
    the next run (``signals_repo.pending_signal_ids``). Delivery runs after all decisions are
    committed.
    """
    now = now or datetime.now(UTC)
    outcomes: list[AlertOutcome] = []
    created: set[int] = set()
    for signal_id in signal_ids:
        ticker = "?"
        try:
            with session_scope(engine) as s:
                alert, reason, ticker = create_alert(s, signal_id, policy, now, channels)
                signals_repo.mark_signal_processed(s, signal_id, now, None if alert else reason)
                alert_id = alert.id if alert else None
            if alert_id is not None and reason != "already alerted":
                created.add(alert_id)
            if alert_id is None:
                log.info("signal %s (%s) not alerted: %s", signal_id, ticker, reason)
            outcomes.append(AlertOutcome(signal_id, ticker, alert_id, None if alert_id else reason))
        except Exception as exc:
            log.exception("could not process signal %s", signal_id)
            outcomes.append(
                AlertOutcome(
                    signal_id, ticker, None, None, error=f"{type(exc).__name__}: {exc}"[:200]
                )
            )
    try:
        send_due(engine, channels, policy, now, alert_ids=created)
    except Exception:
        # deliveries stay pending/failed in the database and are retried by the next send_due
        log.exception("delivery pass failed")
    return outcomes


def feed_item(alert: AlertRecord, ticker: str, deliveries: list[Any]) -> dict[str, Any]:
    """JSON-able view of an alert for the API."""
    return {
        "id": alert.id,
        "ticker": ticker,
        "kind": alert.kind,
        "severity": alert.severity,
        "conviction": alert.conviction,
        "title": alert.title,
        "body": "\n".join(
            line for line in alert.body.split("\n") if not line.startswith(TELEGRAM_HINT)
        ),
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "read": alert.read_at is not None,
        "deliveries": {d.channel: d.status for d in deliveries if d.alert_id == alert.id},
    }
