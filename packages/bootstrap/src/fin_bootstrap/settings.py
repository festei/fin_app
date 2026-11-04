"""The one place that reads the environment.

Everything the application needs from its surroundings is read here, once, and handed to the
adapters that need it. Nothing else in the core or the adapters should call ``os.environ``
(the vendor clients keep an env fallback for their own credentials).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fin_persistence.session import postgres_url


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    db_connect_timeout: int = 10
    config_dir: Path | None = None  # FIN_CONFIG_DIR
    sec_user_agent: str | None = None  # SEC_USER_AGENT (EDGAR requires contact details)
    fred_api_key: str | None = None  # FRED_API_KEY
    telegram_bot_token: str | None = None  # TELEGRAM_BOT_TOKEN (only used if the flag is on)
    telegram_chat_id: str | None = None  # TELEGRAM_CHAT_ID

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
        return cls(
            database_url=url or None,
            config_dir=Path(env["FIN_CONFIG_DIR"]) if env.get("FIN_CONFIG_DIR") else None,
            sec_user_agent=env.get("SEC_USER_AGENT") or None,
            fred_api_key=env.get("FRED_API_KEY") or None,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or None,
        )
