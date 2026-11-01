"""One scheduled sandbox step: strategies read new signals, orders settle, values are snapshotted.
Recorded in ``ingestion_runs`` (source ``sandbox``) like the other jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import Engine, select

from fin_core.db.models import Portfolio
from fin_core.db.session import session_scope
from fin_core.ingest import RunSummary, run_status, tracked_run
from fin_core.sandbox import engine as sandbox

log = logging.getLogger(__name__)


def run_sandbox(engine: Engine, as_of: datetime | None = None) -> RunSummary:
    """Process every portfolio in its own transaction, so one failure never blocks the others.
    Run after prices and scoring (the daily pipeline does). Re-running the same day is harmless."""
    as_of = as_of or datetime.now(UTC)
    with tracked_run(engine, "sandbox") as summary:
        with session_scope(engine) as s:
            ids = list(s.scalars(select(Portfolio.id).order_by(Portfolio.id)))
        for pid in ids:
            try:
                with session_scope(engine) as s:
                    portfolio = s.get(Portfolio, pid)
                    sandbox.run_strategy(s, portfolio, as_of)
                    sandbox.settle(s, portfolio, as_of)
                    sandbox.snapshot(s, portfolio, as_of)
                summary.rows += 1
            except Exception as exc:
                log.exception("sandbox portfolio %s failed", pid)
                summary.failed[f"portfolio {pid}"] = f"{type(exc).__name__}: {exc}"[:200]
        summary.status = run_status(summary, len(ids), aborted=False)
    return summary
