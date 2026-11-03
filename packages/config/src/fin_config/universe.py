"""The ``UniverseSource`` port, read from ``config/universe.yml`` and ``config/bond_series.yml``.
Reading the files is this adapter's job; what a valid universe is lives in the domain."""

from __future__ import annotations

from pathlib import Path

import yaml

from fin_config.locator import ConfigLocator
from fin_domain.market_data import BondSeries, SecurityUpdate, bond_series, security_updates


class YamlUniverseSource:
    def __init__(
        self,
        locator: ConfigLocator | None = None,
        *,
        universe_path: Path | None = None,
        bond_series_path: Path | None = None,
    ):
        self._locator = locator or ConfigLocator()
        self._universe_path = universe_path
        self._bond_series_path = bond_series_path

    def securities(self) -> list[SecurityUpdate]:
        path = self._universe_path or self._locator.path("universe.yml")
        raw = yaml.safe_load(path.read_text()) or {}
        return security_updates(raw.get("securities") or [])

    def bond_series(self) -> list[BondSeries]:
        path = self._bond_series_path or self._locator.path("bond_series.yml")
        raw = yaml.safe_load(path.read_text()) or {}
        return bond_series(raw.get("series") or [])
