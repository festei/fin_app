from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from fin_domain.shared import JobRun


class InMemoryJobRunRepository:
    """Behaves like the SQL repository: it hands out copies, so a change only counts once saved."""

    def __init__(self, runs: dict[int, JobRun] | None = None):
        self.runs: dict[int, JobRun] = runs if runs is not None else {}

    def add(self, run: JobRun) -> JobRun:
        run.id = max(self.runs, default=0) + 1
        self.runs[run.id] = deepcopy(run)
        return run

    def get(self, run_id: int) -> JobRun | None:
        found = self.runs.get(run_id)
        return deepcopy(found) if found else None

    def save(self, run: JobRun) -> None:
        if run.id is None or run.id not in self.runs:
            raise KeyError(f"run {run.id!r} was never added")
        self.runs[run.id] = deepcopy(run)

    def find_running_started_before(self, moment: datetime) -> list[JobRun]:
        return [deepcopy(r) for r in self.runs.values() if r.is_running and r.started_at < moment]
