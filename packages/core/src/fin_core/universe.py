"""The tracked universe, defined in ``config/universe.yml`` and synced into ``securities``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from fin_core.db import repo

_ALLOWED = {"ticker", "name", "sector", "kind", "cik", "active"}
_KINDS = {"stock", "etf"}


def _default_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "universe.yml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("config/universe.yml not found")


def load_universe(path: Path | None = None) -> list[dict[str, Any]]:
    raw = yaml.safe_load((path or _default_path()).read_text()) or {}
    rows = raw.get("securities") or []
    seen: set[str] = set()
    for r in rows:
        unknown = set(r) - _ALLOWED
        if unknown:
            raise ValueError(f"{r.get('ticker')}: unknown universe fields {sorted(unknown)}")
        ticker = str(r["ticker"]).upper()
        if ticker in seen:
            raise ValueError(f"duplicate ticker in universe: {ticker}")
        seen.add(ticker)
        if r.get("kind", "stock") not in _KINDS:
            raise ValueError(f"{ticker}: kind must be one of {sorted(_KINDS)}")
    return rows


def sync_universe(session: Session, path: Path | None = None) -> int:
    """Upsert the configured universe. Does not deactivate tickers removed from the file."""
    return repo.upsert_securities(session, load_universe(path))
