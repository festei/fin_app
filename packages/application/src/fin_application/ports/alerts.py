"""Ports of the Signals & Alerts context that are not storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fin_domain.signals import Alert, AlertPolicy


@dataclass(frozen=True)
class AlertMessage:
    """What a channel is asked to deliver."""

    alert_id: int
    ticker: str
    kind: str
    severity: str
    conviction: float
    title: str
    body: str

    def text(self) -> str:
        return f"{self.title}\n\n{self.body}"

    @classmethod
    def from_alert(cls, alert: Alert) -> AlertMessage:
        if alert.id is None:
            raise ValueError("only a stored alert can be delivered")
        return cls(
            alert_id=alert.id,
            ticker=str(alert.ticker),
            kind=alert.kind,
            severity=alert.severity,
            conviction=alert.conviction,
            title=alert.title,
            body=alert.body,
        )


class AlertChannel(Protocol):
    """Where alerts go. ``name`` keys the delivery record and the mute state."""

    name: str

    def send(self, message: AlertMessage) -> None:
        """Deliver or raise ``ChannelError``."""
        ...


class AlertPolicySource(Protocol):
    def load(self) -> AlertPolicy: ...


class SingleInstanceGuard(Protocol):
    """At most one holder at a time across processes (the worker must not run twice)."""

    def acquire(self) -> None:
        """Raises if another instance holds it."""
        ...

    def ensure(self) -> bool:
        """``True`` if still held; ``False`` if it was lost and re-acquired. Raises if another
        instance took over meanwhile."""
        ...

    def release(self) -> None: ...
