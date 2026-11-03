"""Bridge for the modules that still take an ``Engine``: job-run bookkeeping on top of the
application's ``JobRunTracker``. It goes away when scoring (step 4) and the sandbox (step 5)
move to use cases that receive a tracker."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine

from fin_application.job_runs import JobRunTracker
from fin_application.runs import RunSummary, run_status
from fin_application.runs import tracked_run as _tracked_run
from fin_persistence import SqlUnitOfWork

__all__ = ["RunSummary", "run_status", "tracked_run"]


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@contextmanager
def tracked_run(engine: Engine, source: str) -> Iterator[RunSummary]:
    tracker = JobRunTracker(lambda: SqlUnitOfWork(engine), _UtcClock())
    with _tracked_run(tracker, source) as summary:
        yield summary
