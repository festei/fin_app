"""Alert policy from ``config/alerts.yml``: which signals become alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from fin_core.db.models import AlertRecord


def _default_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "alerts.yml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("config/alerts.yml not found")


def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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

    @classmethod
    def load(cls, path: Path | None = None) -> AlertPolicy:
        raw = (yaml.safe_load((path or _default_path()).read_text()) or {}).get("alerts") or {}
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown alert policy fields: {sorted(unknown)}")
        if "retry_backoff_minutes" in raw:
            raw["retry_backoff_minutes"] = tuple(raw["retry_backoff_minutes"])
        return cls(**raw)

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
        return now - _utc(last_attempt_at) >= wait

    def severity_for(self, kind: str, watched: bool) -> str:
        return "high" if watched else self.severity.get(kind, "normal")

    def decide(
        self,
        *,
        kind: str,
        conviction: float,
        watched: bool,
        last_alert: AlertRecord | None,
        now: datetime,
    ) -> tuple[bool, str]:
        """``(alert?, reason)``. The reason is recorded in logs even when suppressed."""
        if kind == "sell" and self.sell_only_for_watchlist and not watched:
            return False, "sell on a stock that is not on the watchlist"
        if last_alert is not None:
            age = now - _utc(last_alert.created_at)
            if age < timedelta(hours=self.cooldown_hours):
                moved = abs(conviction - last_alert.conviction)
                if moved < self.material_change:
                    return (
                        False,
                        f"within the {self.cooldown_hours:g}h cooldown (moved {moved:.1f})",
                    )
                return True, f"conviction moved {moved:.1f} points since the last alert"
        return True, "new transition"
