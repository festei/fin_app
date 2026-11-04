"""Programmatic Alembic runner. Migrations live inside the package so non-editable images ship
them. The URL is always explicit; the command-line entry point (which reads the environment) is
``fin_bootstrap.migrate``."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def current(url: str) -> None:
    command.current(alembic_config(url))
