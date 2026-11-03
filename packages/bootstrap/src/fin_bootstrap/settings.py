"""The one place that reads the environment.

Everything the application needs from its surroundings is read here, once, and handed to the
adapters that need it. Nothing else in the core or the adapters should call ``os.environ``;
the modules that still do belong to ``fin_core`` and go away with it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from fin_persistence.session import postgres_url


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    db_connect_timeout: int = 10

    @property
    def database_configured(self) -> bool:
        return self.database_url is not None

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """``DATABASE_URL`` wins; otherwise the URL is built from the ``POSTGRES_*`` variables
        (both need ``POSTGRES_USER`` and ``POSTGRES_DB``). No database is not an error here."""
        env = os.environ if environ is None else environ
        url = env.get("DATABASE_URL")
        if not url and env.get("POSTGRES_USER") and env.get("POSTGRES_DB"):
            url = postgres_url(
                env["POSTGRES_USER"],
                env["POSTGRES_DB"],
                env.get("POSTGRES_PASSWORD", ""),
                env.get("POSTGRES_HOST", "localhost"),
                env.get("POSTGRES_PORT", "5432"),
            )
        return cls(database_url=url or None)
