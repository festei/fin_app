"""What the persistence adapter tells its callers when the database fails, and what it logs.

Callers (the application, the apps) never see SQLAlchemy errors. They see two kinds:

* ``StorageUnavailable``: the connection could not be made or broke. Transient and repetitive
  (every probe and request fails the same way during an outage), so it is logged as one line.
* ``StorageError``: the database answered and refused the operation (a constraint, a missing
  table, bad data). A bug or a deployment problem, rare, and the one that needs a traceback.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from fin_application.errors import StorageError, StorageUnavailable

log = logging.getLogger("fin_persistence.storage")

# "Could not reach the database, or the connection broke." This is a deliberately broad bucket:
# SQLAlchemy also files lock timeouts, deadlocks and cancelled queries under OperationalError
# (Postgres reports them as such). They are retryable conditions, so "unavailable" (503) is the
# right answer for them too, but do not read this tuple as "cannot connect" only. Everything else
# SQLAlchemy raises (IntegrityError, ProgrammingError, DataError, ...) means the database
# answered and refused the operation. Note that a missing table is an OperationalError on SQLite
# and a ProgrammingError on Postgres (the Postgres case is the one production sees).
CONNECTIVITY_ERRORS = (OperationalError, InterfaceError)


def first_line(exc: BaseException, limit: int = 200) -> str:
    """The driver's own message, first line only: no SQL statement, no parameters."""
    lines = str(getattr(exc, "orig", None) or exc).strip().splitlines()
    return lines[0][:limit] if lines else ""


def log_storage_error(exc: BaseException) -> None:
    """Connectivity problems: one WARNING line, no traceback (the kind and the driver's first
    line are enough, and an outage repeats it for every probe and request). Anything else: an
    ERROR with the full traceback, because that is what someone will need to debug it."""
    if isinstance(exc, CONNECTIVITY_ERRORS):
        log.warning("database unavailable (%s): %s", type(exc).__name__, first_line(exc))
    else:
        log.error("storage error (%s)", type(exc).__name__, exc_info=exc)


def translate_storage_error(exc: SQLAlchemyError) -> StorageUnavailable | StorageError:
    """Log the original, then turn it into an application error.

    The original can hold the SQL, its parameters and connection details, so it goes to the log
    and never into the message that callers and API responses see.
    """
    log_storage_error(exc)
    if isinstance(exc, CONNECTIVITY_ERRORS):
        return StorageUnavailable(f"database unavailable ({type(exc).__name__})")
    return StorageError(f"{type(exc).__name__}: {first_line(exc)}".rstrip(": "))
