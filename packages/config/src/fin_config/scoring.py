"""The ``ScoringConfigSource`` port, read from ``config/philosophies.yml``.

Thresholds and weights per philosophy are reasoned starting points, not calibrated; they live in
the file so they can be tuned without touching code.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from fin_config.locator import ConfigLocator
from fin_domain.scoring import validate_config


def load_scoring_config(path: Path | None = None, locator: ConfigLocator | None = None) -> dict:
    """The parsed ``philosophies.yml``, checked against the registered philosophies."""
    path = path or (locator or ConfigLocator()).path("philosophies.yml")
    config = yaml.safe_load(path.read_text())
    validate_config(config)
    return config


class YamlScoringConfig:
    def __init__(self, locator: ConfigLocator | None = None, path: Path | None = None):
        self._locator = locator
        self._path = path

    def load(self) -> Mapping[str, Any]:
        return load_scoring_config(self._path, self._locator)
