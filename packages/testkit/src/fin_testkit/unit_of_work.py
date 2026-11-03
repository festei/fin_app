from __future__ import annotations

from copy import deepcopy
from types import TracebackType
from typing import Self

from fin_domain.shared import JobRun
from fin_testkit.job_runs import InMemoryJobRunRepository


class InMemoryStore:
    """The committed state shared by every unit of work of one test."""

    def __init__(self) -> None:
        self.job_runs: dict[int, JobRun] = {}


class InMemoryUnitOfWork:
    """Works on a copy of the store; the copy replaces the store only if the block ends cleanly."""

    def __init__(self, store: InMemoryStore):
        self._store = store
        self.job_runs = InMemoryJobRunRepository()

    def __enter__(self) -> Self:
        self.job_runs = InMemoryJobRunRepository(deepcopy(self._store.job_runs))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._store.job_runs = self.job_runs.runs


def in_memory_uow_factory(store: InMemoryStore):
    return lambda: InMemoryUnitOfWork(store)
