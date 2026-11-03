"""What a batch job reports about itself, and the wrapper that records it.

A run is "failed" until it reaches normal completion, so a crash can never be recorded as ok.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from fin_application.job_runs import JobRunTracker
from fin_domain.shared import RunStatus, derive_status


@dataclass
class RunSummary:
    source: str
    status: str = "failed"  # pessimistic: only normal completion may change it
    rows: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # ticker/series -> reason
    rebackfilled: list[str] = field(default_factory=list)  # tickers whose history was rewritten
    signals: int = 0  # scoring runs: new signals created
    alerts: int = 0  # scoring runs: new alerts created


def describe(exc: BaseException) -> str:
    if isinstance(exc, Exception):
        return f"{type(exc).__name__}: {exc}"[:200]
    return f"interrupted ({type(exc).__name__})"


def run_status(summary: RunSummary, total: int, aborted: bool) -> str:
    return derive_status(len(summary.failed), total, aborted).value


@contextmanager
def tracked_run(tracker: JobRunTracker, source: str) -> Iterator[RunSummary]:
    """Record the run. The summary starts "failed"; an exception that escapes the job is
    recorded (and re-raised) so it is never reported as healthy."""
    run_id = tracker.begin(source)
    summary = RunSummary(source)
    try:
        yield summary
    except BaseException as exc:
        summary.status = "failed"
        summary.failed.setdefault("*", describe(exc))
        raise
    finally:
        tracker.finish(run_id, RunStatus(summary.status), summary.rows, summary.failed)
