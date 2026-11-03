"""``ingestion_runs`` <-> ``JobRun``. The table keeps its name; it also holds scoring and sandbox
runs, told apart by ``source``."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_domain.shared import JobRun, RunStatus
from fin_persistence.tables import IngestionRun


def aware(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive; they were stored as UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def to_domain(row: IngestionRun) -> JobRun:
    return JobRun(
        id=row.id,
        source=row.source,
        started_at=aware(row.started_at),
        status=RunStatus(row.status),
        rows=row.rows,
        error=row.error,
        finished_at=aware(row.finished_at) if row.finished_at else None,
    )


def _copy_state(run: JobRun, row: IngestionRun) -> None:
    row.source, row.started_at = run.source, run.started_at
    row.status, row.rows, row.error = run.status.value, run.rows, run.error
    row.finished_at = run.finished_at


class SqlJobRunRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, run: JobRun) -> JobRun:
        row = IngestionRun()
        _copy_state(run, row)
        self._session.add(row)
        self._session.flush()
        run.id = row.id
        return run

    def get(self, run_id: int) -> JobRun | None:
        row = self._session.get(IngestionRun, run_id)
        return to_domain(row) if row else None

    def save(self, run: JobRun) -> None:
        row = self._session.get(IngestionRun, run.id) if run.id is not None else None
        if row is None:
            raise LookupError(f"job run {run.id!r} was never added")
        _copy_state(run, row)

    def find_running_started_before(self, moment: datetime) -> list[JobRun]:
        rows = self._session.scalars(
            select(IngestionRun).where(
                IngestionRun.status == RunStatus.RUNNING.value, IngestionRun.started_at < moment
            )
        )
        return [to_domain(r) for r in rows]
