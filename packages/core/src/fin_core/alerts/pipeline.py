"""Signals -> alerts -> deliveries.

Alert *creation* is channel-independent and policy-driven; *delivery* happens right after
creation, to the channels registered at that moment. A channel switched on later therefore never
receives earlier alerts, and a failed delivery is retried from its own row.
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
    lines.append(f"Use /why {sec.ticker} for the full breakdown.")
    return title[:200], "\n".join(lines)


@dataclass
class AlertOutcome:
    signal_id: int
    ticker: str
    alert_id: int | None
    suppressed: str | None  # reason, when no alert was created


def create_alert(
    session: Session, signal_id: int, policy: AlertPolicy, now: datetime
) -> tuple[AlertRecord | None, str, str]:
    """Apply the policy to one signal. Returns ``(alert|None, reason, ticker)``."""
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
    return alert, reason, sec.ticker


def _record(session: Session, alert_id: int, channel: str, status: str, error: str | None, now):
    delivery = signals_repo.get_delivery(session, alert_id, channel)
    if delivery is None:
        from fin_core.db.models import AlertDelivery

        delivery = AlertDelivery(alert_id=alert_id, channel=channel, status=status, attempts=0)
        session.add(delivery)
    delivery.attempts += 1 if status != "skipped" else 0
    delivery.status = status
    delivery.error = error[:500] if error else None
    delivery.delivered_at = now if status == "sent" else None
    session.flush()


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


def _muted(session: Session, channel: str, now: datetime) -> bool:
    until = signals_repo.muted_until(session, channel)
    if until is None:
        return False
    return (until if until.tzinfo else until.replace(tzinfo=UTC)) > now


def deliver(
    session: Session, alert: AlertRecord, ticker: str, channels: list[AlertChannel], now: datetime
) -> None:
    """Send a freshly created alert through every registered channel, recording each outcome.
    One channel failing never stops the others. Muted channels are skipped (the alert stays in
    the feed)."""
    for channel in channels:
        if signals_repo.get_delivery(session, alert.id, channel.name) is not None:
            continue
        if channel.name != IN_APP and _muted(session, channel.name, now):
            _record(session, alert.id, channel.name, "skipped", "channel muted", now)
            continue
        try:
            channel.send(_message(alert, ticker))
            _record(session, alert.id, channel.name, "sent", None, now)
        except ChannelError as exc:
            log.warning("delivery of alert %s via %s failed: %s", alert.id, channel.name, exc)
            _record(session, alert.id, channel.name, "failed", str(exc), now)


def retry_failed(
    engine: Engine, channels: list[AlertChannel], policy: AlertPolicy, now: datetime | None = None
) -> int:
    """Retry failed deliveries (bounded by ``max_delivery_attempts``). Returns the number sent."""
    now = now or datetime.now(UTC)
    sent = 0
    for channel in channels:
        if channel.name == IN_APP:
            continue
        with session_scope(engine) as s:
            pending = signals_repo.failed_deliveries(s, channel.name, policy.max_delivery_attempts)
            for _delivery, alert in pending:
                if _muted(s, channel.name, now):
                    break
                ticker = s.get(Security, alert.security_id).ticker
                try:
                    channel.send(_message(alert, ticker))
                except ChannelError as exc:
                    _record(s, alert.id, channel.name, "failed", str(exc), now)
                else:
                    _record(s, alert.id, channel.name, "sent", None, now)
                    sent += 1
    return sent


def process_signals(
    engine: Engine,
    signal_ids: list[int],
    policy: AlertPolicy,
    channels: list[AlertChannel],
    now: datetime | None = None,
) -> list[AlertOutcome]:
    """Turn new signals into alerts and deliver them. One transaction per signal."""
    now = now or datetime.now(UTC)
    outcomes: list[AlertOutcome] = []
    for signal_id in signal_ids:
        with session_scope(engine) as s:
            alert, reason, ticker = create_alert(s, signal_id, policy, now)
            if alert is None:
                log.info("signal %s (%s) not alerted: %s", signal_id, ticker, reason)
                outcomes.append(AlertOutcome(signal_id, ticker, None, reason))
                continue
            if reason != "already alerted":
                deliver(s, alert, ticker, channels, now)
            outcomes.append(AlertOutcome(signal_id, ticker, alert.id, None))
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
        "body": alert.body,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "read": alert.read_at is not None,
        "deliveries": {d.channel: d.status for d in deliveries if d.alert_id == alert.id},
    }
