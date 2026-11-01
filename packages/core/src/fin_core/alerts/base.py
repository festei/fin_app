"""Shared alert types: the message, the channel protocol and the errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

IN_APP = "in_app"  # the always-on channel: the alert row itself is the feed


class ChannelError(RuntimeError):
    """Delivery through a channel failed. Never contains credentials (tokens, URLs with keys)."""


class ChannelConfigError(RuntimeError):
    """A channel is switched on but not configured. Raised at startup, not at first alert."""


@dataclass(frozen=True)
class AlertMessage:
    alert_id: int
    ticker: str
    kind: str
    severity: str
    conviction: float
    title: str
    body: str

    def text(self) -> str:
        return f"{self.title}\n\n{self.body}"


class AlertChannel(Protocol):
    """Where alerts go. ``name`` keys the delivery record and the mute state."""

    name: str

    def send(self, message: AlertMessage) -> None:
        """Deliver or raise ``ChannelError``."""
        ...
