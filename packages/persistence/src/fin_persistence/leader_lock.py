"""The ``SingleInstanceGuard`` port as a Postgres session-level advisory lock."""

from __future__ import annotations

import logging

from sqlalchemy import Connection, Engine, text

from fin_application.errors import InstanceAlreadyRunning

log = logging.getLogger("fin_worker")

LEADER_LOCK_KEY = 7_301_001  # arbitrary, fixed: identifies "the fin-app worker" in pg_locks


class PgAdvisoryLeaderLock:
    """A Postgres session-level advisory lock held on a dedicated connection. Other databases
    (SQLite in dev/tests) have no cross-process advisory locks, so it is a no-op there.

    The lock lives and dies with that connection: if the database restarts or the connection is
    dropped, the lock is gone while this process keeps running, and a second instance could start.
    ``ensure`` therefore re-checks it every loop and re-acquires it, or raises
    ``InstanceAlreadyRunning`` if another instance took over meanwhile.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self._conn: Connection | None = None

    @property
    def active(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    def _take(self) -> Connection:
        conn = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK_KEY}).scalar()
        if not got:
            conn.close()
            raise InstanceAlreadyRunning("another worker instance holds the leader lock")
        return conn

    def acquire(self) -> None:
        if self.active:
            self._conn = self._take()

    def _discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass  # the connection is already dead

    def ensure(self) -> bool:
        """``True`` if the lock was still held. ``False`` if it had been lost and was re-acquired.
        Raises ``InstanceAlreadyRunning`` if it was lost and another instance now holds it."""
        if not self.active:
            return True
        if self._conn is not None:
            try:
                held = self._conn.execute(
                    text(
                        "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 "
                        "AND objid = :k AND pid = pg_backend_pid()"
                    ),
                    {"k": LEADER_LOCK_KEY},
                ).scalar()
                if held:
                    return True
            except Exception:
                pass  # connection broken: fall through and re-acquire
        self._discard()
        self._conn = self._take()  # raises if someone else holds it now
        log.warning("the leader lock had been lost; re-acquired it")
        return False

    def release(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LEADER_LOCK_KEY})
        except Exception:
            pass
        finally:
            self._discard()
