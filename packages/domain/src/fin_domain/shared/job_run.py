"""A recorded execution of a batch job (ingestion, scoring, sandbox), so a stalled or failing
pipeline is visible.

The rules were spread over ``ingest.py``; they are stated here once:

* a run is ``running`` from the moment it starts and ends in exactly one of ``ok``, ``partial`` or
  ``failed``; a finished run never changes again;
* a failing item makes the run ``partial``; every item failing, or the job aborting, makes it
  ``failed`` (``derive_status``);
* a run that is still ``running`` long after it started belongs to a process that died, and is
  failed by whoever notices (``fail_stale``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from fin_domain.shared.errors import InvariantViolation
from fin_domain.shared.time import ensure_utc

# A run still "running" after this long belongs to a process that died.
STALE_AFTER = timedelta(hours=6)
MAX_SOURCE_LENGTH = 32
MAX_ERROR_LENGTH = 500
STALE_ERROR = "stale: the process ended before finishing this run"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


def derive_status(failures: int, total: int, aborted: bool) -> RunStatus:
    """``failed`` if the job aborted or every item failed, ``partial`` if some did, else ``ok``."""
    if aborted or (failures > 0 and failures >= total):
        return RunStatus.FAILED
    return RunStatus.PARTIAL if failures else RunStatus.OK


def summarise_failures(failures: Mapping[str, str]) -> str | None:
    """The run's ``error`` text: ``item: reason`` pairs, cut to the stored length."""
    return (
        "; ".join(f"{item}: {reason}" for item, reason in failures.items())[:MAX_ERROR_LENGTH]
        or None
    )


@dataclass
class JobRun:
    source: str
    started_at: datetime
    id: int | None = None
    status: RunStatus = RunStatus.RUNNING
    rows: int = 0
    error: str | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source or len(self.source) > MAX_SOURCE_LENGTH:
            raise InvariantViolation(f"source must be 1-{MAX_SOURCE_LENGTH} characters")
        self.started_at = ensure_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            self.finished_at = ensure_utc(self.finished_at, "finished_at")

    @classmethod
    def start(cls, source: str, now: datetime) -> JobRun:
        return cls(source=source, started_at=now)

    @property
    def is_running(self) -> bool:
        return self.status is RunStatus.RUNNING

    def is_stale(self, now: datetime, older_than: timedelta = STALE_AFTER) -> bool:
        return self.is_running and self.started_at < ensure_utc(now) - older_than

    def finish(
        self, status: RunStatus, rows: int, failures: Mapping[str, str], now: datetime
    ) -> None:
        self._require_running()
        if status is RunStatus.RUNNING:
            raise InvariantViolation("a run cannot finish as 'running'")
        if rows < 0:
            raise InvariantViolation("rows cannot be negative")
        self.status, self.rows = status, rows
        self.error = summarise_failures(failures)
        self.finished_at = ensure_utc(now, "finished_at")

    def fail_stale(self, now: datetime) -> None:
        self._require_running()
        self.status = RunStatus.FAILED
        self.error = STALE_ERROR
        self.finished_at = ensure_utc(now, "finished_at")

    def _require_running(self) -> None:
        if not self.is_running:
            raise InvariantViolation(f"run is already {self.status.value}; it cannot change again")
