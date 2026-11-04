"""Command line for the database migrations.

``python -m fin_bootstrap.migrate [upgrade|downgrade|current] [revision]``

The database comes from the environment (``DATABASE_URL``, or ``POSTGRES_*``). The runner itself
is ``fin_persistence.migrate``, which always takes an explicit URL.
"""

from __future__ import annotations

import sys

from fin_bootstrap.settings import Settings
from fin_persistence import migrate


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    url = Settings.from_environ().database_url
    if url is None:
        raise SystemExit("Database not configured: set DATABASE_URL or POSTGRES_USER/POSTGRES_DB")
    action = args[0] if args else "upgrade"
    revision = args[1] if len(args) > 1 else None
    if action == "upgrade":
        migrate.upgrade(url, revision or "head")
    elif action == "downgrade":
        migrate.downgrade(url, revision or "-1")
    elif action == "current":
        migrate.current(url)
    else:
        raise SystemExit(f"unknown action {action!r}")


if __name__ == "__main__":
    main()
