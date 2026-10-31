"""One scheduled scoring run: score the universe, store scores and signals, create alerts, deliver
them, and finish anything an earlier run left undone. Recorded in ``ingestion_runs`` like the
ingestion jobs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine

from fin_core.alerts.base import AlertChannel
from fin_core.alerts.channels import build_channels
from fin_core.alerts.pipeline import process_signals, send_due
from fin_core.alerts.policy import AlertPolicy
from fin_core.db import signals_repo
from fin_core.db.session import session_scope
from fin_core.ingest import RunSummary, tracked_run
from fin_core.signals import score_universe


def run_scoring(
    engine: Engine,
    *,
    channels: list[AlertChannel] | None = None,
    policy: AlertPolicy | None = None,
    tickers: list[str] | None = None,
    as_of: date | datetime | None = None,
    graph: Any = None,
    config: dict[str, Any] | None = None,
) -> RunSummary:
    """Status: ``failed`` if every security failed (or the channel configuration is invalid, which
    raises), ``partial`` if anything failed (a security, or the alert decision for a signal), else
    ``ok``. One failure never blocks the others, and a signal whose alert decision failed is
    retried by the next run because it stays unprocessed."""
    with tracked_run(engine, "scoring") as summary:
        # Invalid channel configuration (e.g. Telegram on without credentials) fails loudly here,
        # recorded as a failed run, rather than at the first alert.
        channels = channels if channels is not None else build_channels()
        policy = policy or AlertPolicy.load()
        now = datetime.now(UTC)
        batch = score_universe(
            engine, as_of or now.date(), tickers=tickers, graph=graph, config=config
        )
        # Every signal still owed an alert decision: this run's new ones AND any left behind by
        # an earlier run that was interrupted (already-decided signals are never re-evaluated).
        with session_scope(engine) as s:
            owed = signals_repo.pending_signal_ids(s)
        outcomes = process_signals(engine, owed, policy, channels, now)
        send_due(engine, channels, policy, now)  # drain retries that are due
        summary.rows = batch.scored
        summary.signals = len(batch.signals)
        summary.alerts = sum(1 for o in outcomes if o.alert_id is not None)
        summary.failed.update(batch.failed)
        summary.failed.update({f"signal {o.signal_id}": o.error for o in outcomes if o.error})
        securities = batch.scored + len(batch.failed)
        if securities and len(batch.failed) >= securities:
            summary.status = "failed"
        else:
            summary.status = "partial" if summary.failed else "ok"
    return summary
