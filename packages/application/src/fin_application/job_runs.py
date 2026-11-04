"""Use case: record that a batch job ran, and how it went.

This is the bookkeeping behind ``ingestion_runs``. A run starts ``running``; whoever started it
must finish it, and a run whose process died is failed by the next one to start.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from fin_application.ports import Clock, UnitOfWorkFactory
from fin_domain.shared import STALE_AFTER, InvariantViolation, JobRun, NotFound, RunStatus


class JobRunTracker:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def fail_stale(self, older_than: timedelta = STALE_AFTER) -> int:
        """Fail runs stuck in ``running`` because their process died. Returns how many."""
        now = self._clock.now()
        with self._uow_factory() as uow:
            stale = uow.job_runs.find_running_started_before(now - older_than)
            for run in stale:
                run.fail_stale(now)
                uow.job_runs.save(run)
        return len(stale)

    def begin(self, source: str) -> int:
        """Start a run (failing stale ones first) and return its id."""
        self.fail_stale()
        with self._uow_factory() as uow:
            run = uow.job_runs.add(JobRun.start(source, self._clock.now()))
        if run.id is None:
            raise InvariantViolation("the repository did not assign an id to the new run")
        return run.id

    def finish(
        self, run_id: int, status: RunStatus, rows: int, failures: Mapping[str, str]
    ) -> None:
        with self._uow_factory() as uow:
            run = uow.job_runs.get(run_id)
            if run is None:
                raise NotFound("job run", run_id)
            run.finish(status, rows, failures, self._clock.now())
            uow.job_runs.save(run)
