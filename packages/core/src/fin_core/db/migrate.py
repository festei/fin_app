"""Programmatic Alembic runner. Migrations live inside the package so non-editable
images ship them.  CLI:  python -m fin_core.db.migrate [upgrade|downgrade|current] [rev]"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from fin_core.db.session import database_url


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or database_url()).replace("%", "%%"))
    return cfg


def upgrade(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else "upgrade"
    rev = args[1] if len(args) > 1 else None
    if action == "upgrade":
        upgrade(revision=rev or "head")
    elif action == "downgrade":
        downgrade(revision=rev or "-1")
    elif action == "current":
        command.current(alembic_config())
    else:
        raise SystemExit(f"unknown action {action!r}")


if __name__ == "__main__":
    main()
