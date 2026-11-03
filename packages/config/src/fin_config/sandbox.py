"""The ``SandboxConfigSource`` port, read from ``config/sandbox.yml`` (unknown fields are
rejected): the defaults new portfolios start from."""

from __future__ import annotations

from pathlib import Path

import yaml

from fin_config.locator import ConfigLocator
from fin_domain.sandbox import StrategyRules


def load_strategy_defaults(
    path: Path | None = None, locator: ConfigLocator | None = None
) -> StrategyRules:
    path = path or (locator or ConfigLocator()).path("sandbox.yml")
    raw = (yaml.safe_load(path.read_text()) or {}).get("sandbox") or {}
    return StrategyRules.from_dict(raw)


class YamlSandboxConfig:
    def __init__(self, locator: ConfigLocator | None = None, path: Path | None = None):
        self._locator = locator
        self._path = path

    def load(self) -> StrategyRules:
        return load_strategy_defaults(self._path, self._locator)
