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
    max_delivery_attempts: int = 3
    severity: dict[str, str] = field(
        default_factory=lambda: {"buy": "high", "sell": "high", "emerging": "normal"}
    )

    @classmethod
    def load(cls, path: Path | None = None) -> AlertPolicy:
        raw = (yaml.safe_load((path or _default_path()).read_text()) or {}).get("alerts") or {}
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown alert policy fields: {sorted(unknown)}")
        return cls(**raw)

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
