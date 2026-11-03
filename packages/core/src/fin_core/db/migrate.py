"""Command line for the migrations (``python -m fin_core.db.migrate [upgrade|downgrade|current]
[rev]``). The runner itself is ``fin_persistence.migrate``; this adds the ``DATABASE_URL`` default
until the composition root takes it over."""

from __future__ import annotations

import sys

from alembic.config import Config

from fin_core.db.session import database_url
from fin_persistence import migrate as _migrate


def alembic_config(url: str | None = None) -> Config:
    return _migrate.alembic_config(url or database_url())


def upgrade(url: str | None = None, revision: str = "head") -> None:
    _migrate.upgrade(url or database_url(), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    _migrate.downgrade(url or database_url(), revision)


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else "upgrade"
    rev = args[1] if len(args) > 1 else None
    if action == "upgrade":
        upgrade(revision=rev or "head")
    elif action == "downgrade":
        downgrade(revision=rev or "-1")
    elif action == "current":
        _migrate.current(database_url())
    else:
        raise SystemExit(f"unknown action {action!r}")


if __name__ == "__main__":
    main()
