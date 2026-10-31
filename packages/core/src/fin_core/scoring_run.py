"""One scheduled scoring run: score the universe, store scores and signals, create alerts, deliver
them, retry earlier failures. Recorded in ``ingestion_runs`` like the ingestion jobs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine

from fin_core.alerts.base import AlertChannel
from fin_core.alerts.channels import build_channels
from fin_core.alerts.pipeline import process_signals, retry_failed
from fin_core.alerts.policy import AlertPolicy
from fin_core.ingest import RunSummary, run_status, tracked_run
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
    raises), ``partial`` if some did, else ``ok``. A failing security never blocks the others."""
    with tracked_run(engine, "scoring") as summary:
        # Invalid channel configuration (e.g. Telegram on without credentials) fails loudly here,
        # recorded as a failed run, rather than at the first alert.
        channels = channels if channels is not None else build_channels()
        policy = policy or AlertPolicy.load()
        now = datetime.now(UTC)
        batch = score_universe(
            engine, as_of or now.date(), tickers=tickers, graph=graph, config=config
        )
        outcomes = process_signals(
            engine, [signal_id for signal_id, _, _ in batch.signals], policy, channels, now
        )
        retry_failed(engine, channels, policy, now)
        summary.rows = batch.scored
        summary.signals = len(batch.signals)
        summary.alerts = sum(1 for o in outcomes if o.alert_id is not None)
        summary.failed.update(batch.failed)
        summary.status = run_status(summary, batch.scored + len(batch.failed), aborted=False)
    return summary
