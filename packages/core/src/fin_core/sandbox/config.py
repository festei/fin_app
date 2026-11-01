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


BUYABLE_KINDS = ("buy", "emerging")  # signal kinds that may open a position


@dataclass(frozen=True)
class SandboxConfig:
    position_pct: float = 0.20
    max_positions: int = 5
    min_cash_pct: float = 0.05
    buy_kinds: tuple[str, ...] = field(default_factory=lambda: ("buy", "emerging"))
    fee_bps: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.buy_kinds, str) or not self.buy_kinds:
            raise ValueError("buy_kinds must be a non-empty list")
        bad = set(self.buy_kinds) - set(BUYABLE_KINDS)
        if bad:
            raise ValueError(f"buy_kinds may only contain {list(BUYABLE_KINDS)}, not {sorted(bad)}")
        if not 0 < self.position_pct <= 1:
            raise ValueError("position_pct must be in (0, 1]")
        if not 0 <= self.min_cash_pct < 1:
            raise ValueError("min_cash_pct must be in [0, 1)")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if not 0 <= self.fee_bps <= 1000:
            raise ValueError("fee_bps must be between 0 and 1000")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SandboxConfig:
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown sandbox fields: {sorted(unknown)}")
        raw = dict(raw)
        if "buy_kinds" in raw:
            kinds = raw["buy_kinds"]
            if isinstance(kinds, str) or not isinstance(kinds, list | tuple):
                raise ValueError("buy_kinds must be a list")
            raw["buy_kinds"] = tuple(kinds)
        for key in ("position_pct", "min_cash_pct", "fee_bps"):
            if key in raw:
                raw[key] = float(raw[key])
        if "max_positions" in raw:
            raw["max_positions"] = int(raw["max_positions"])
        return cls(**raw)

    @classmethod
    def load(cls, path: Path | None = None) -> SandboxConfig:
        raw = (yaml.safe_load((path or _default_path()).read_text()) or {}).get("sandbox") or {}
        return cls.from_dict(raw)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["buy_kinds"] = list(self.buy_kinds)
        return d
