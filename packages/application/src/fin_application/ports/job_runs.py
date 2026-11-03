"""Storage of job runs."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fin_domain.shared import JobRun


class JobRunRepository(Protocol):
    def add(self, run: JobRun) -> JobRun:
        """Store a new run and return it with its ``id`` set."""
        ...

    def get(self, run_id: int) -> JobRun | None: ...

    def save(self, run: JobRun) -> None:
        """Store the changed state of a run that was added before."""
        ...

    def find_running_started_before(self, moment: datetime) -> list[JobRun]:
        """Runs still ``running`` that started before ``moment``."""
        ...
