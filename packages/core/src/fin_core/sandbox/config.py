"""Sandbox defaults from ``config/sandbox.yml`` (unknown fields are rejected)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


def _default_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "sandbox.yml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("config/sandbox.yml not found")


@dataclass(frozen=True)
class SandboxConfig:
    position_pct: float = 0.20
    max_positions: int = 5
    min_cash_pct: float = 0.05
    buy_kinds: tuple[str, ...] = field(default_factory=lambda: ("buy", "emerging"))
    fee_bps: float = 0.0

    def __post_init__(self) -> None:
        if not 0 < self.position_pct <= 1:
            raise ValueError("position_pct must be in (0, 1]")
        if not 0 <= self.min_cash_pct < 1:
            raise ValueError("min_cash_pct must be in [0, 1)")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if self.fee_bps < 0:
            raise ValueError("fee_bps must not be negative")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SandboxConfig:
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown sandbox fields: {sorted(unknown)}")
        raw = dict(raw)
        if "buy_kinds" in raw:
            raw["buy_kinds"] = tuple(raw["buy_kinds"])
        return cls(**raw)

    @classmethod
    def load(cls, path: Path | None = None) -> SandboxConfig:
        raw = (yaml.safe_load((path or _default_path()).read_text()) or {}).get("sandbox") or {}
        return cls.from_dict(raw)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["buy_kinds"] = list(self.buy_kinds)
        return d
