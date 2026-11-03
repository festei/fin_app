"""Alert channels as the domain sees them: a name, and whether it is muted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fin_domain.shared import ensure_utc

IN_APP = "in_app"  # the always-on channel: the alert row itself is the feed


@dataclass
class ChannelState:
    channel: str
    muted_until: datetime | None = None

    def is_muted(self, now: datetime) -> bool:
        return self.muted_until is not None and ensure_utc(self.muted_until) > ensure_utc(now)

    def mute_until(self, until: datetime) -> None:
        self.muted_until = ensure_utc(until, "muted_until")

    def unmute(self) -> None:
        self.muted_until = None
