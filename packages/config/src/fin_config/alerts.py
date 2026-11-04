"""The ``AlertPolicySource`` port, read from ``config/alerts.yml``."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

from fin_config.locator import ConfigLocator
from fin_domain.shared import InvariantViolation
from fin_domain.signals import AlertPolicy


def load_alert_policy(
    path: Path | None = None, locator: ConfigLocator | None = None
) -> AlertPolicy:
    """The policy from ``alerts.yml``; a field the policy does not know is an error, so a typo in
    the file cannot silently fall back to a default."""
    path = path or (locator or ConfigLocator()).path("alerts.yml")
    raw = dict((yaml.safe_load(path.read_text()) or {}).get("alerts") or {})
    unknown = set(raw) - {f.name for f in fields(AlertPolicy)}
    if unknown:
        raise InvariantViolation(f"unknown alert policy fields: {sorted(unknown)}")
    if "retry_backoff_minutes" in raw:
        raw["retry_backoff_minutes"] = tuple(raw["retry_backoff_minutes"])
    return AlertPolicy(**raw)


class YamlAlertPolicy:
    def __init__(self, locator: ConfigLocator | None = None, path: Path | None = None):
        self._locator = locator
        self._path = path

    def load(self) -> AlertPolicy:
        return load_alert_policy(self._path, self._locator)
