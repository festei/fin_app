"""Feature flags: ``config/features.yml`` defaults, overridable by env vars.

Env var for flag ``telegram_enabled`` is ``FEATURE_TELEGRAM_ENABLED``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_TRUE = {"1", "true", "yes", "on"}


def _default_path() -> Path:
    env = os.environ.get("FEATURES_FILE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "features.yml"
        if candidate.exists():
            return candidate
    return Path("config/features.yml")


class FeatureFlags:
    def __init__(self, defaults: dict[str, bool] | None = None):
        self._defaults = defaults or {}

    @classmethod
    def load(cls, path: Path | None = None) -> FeatureFlags:
        path = path or _default_path()
        data: dict[str, bool] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text()) or {}
            data = {k: bool(v) for k, v in (raw.get("features") or {}).items()}
        return cls(data)

    def is_enabled(self, name: str) -> bool:
        env = os.environ.get(f"FEATURE_{name.upper()}")
        if env is not None:
            return env.strip().lower() in _TRUE
        return self._defaults.get(name, False)

    def as_dict(self) -> dict[str, bool]:
        names = set(self._defaults) | {
            k.removeprefix("FEATURE_").lower() for k in os.environ if k.startswith("FEATURE_")
        }
        return {n: self.is_enabled(n) for n in sorted(names)}
