"""Where the ``config/*.yml`` files are.

Five modules used to walk up their own parent directories looking for ``config/<name>``. This is
the one place that does it. The lookup order is:

1. an explicit ``config_dir`` given to the locator;
2. the ``FIN_CONFIG_DIR`` environment variable;
3. the nearest ``config/`` directory above this package (the repository checkout, or ``/app`` in
   the images, where ``config/`` sits next to the virtualenv).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

CONFIG_DIR_ENV = "FIN_CONFIG_DIR"


class ConfigLocator:
    def __init__(
        self,
        config_dir: Path | None = None,
        *,
        search_from: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self._config_dir = config_dir
        self._search_from = (search_from or Path(__file__)).resolve()
        self._environ = os.environ if environ is None else environ

    def find(self, name: str) -> Path | None:
        """The path of ``name`` (e.g. ``"alerts.yml"``), or ``None`` if it is not there."""
        explicit = self._config_dir or self._environment_dir()
        if explicit is not None:
            candidate = explicit / name
            return candidate if candidate.exists() else None
        for parent in self._search_from.parents:
            candidate = parent / "config" / name
            if candidate.exists():
                return candidate
        return None

    def path(self, name: str) -> Path:
        """Like ``find`` but a missing file is an error."""
        found = self.find(name)
        if found is None:
            raise FileNotFoundError(f"config/{name} not found")
        return found

    def _environment_dir(self) -> Path | None:
        value = self._environ.get(CONFIG_DIR_ENV)
        return Path(value) if value else None
