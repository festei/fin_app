"""Helpers shared by the Prefect flows."""

from __future__ import annotations

from dataclasses import asdict

from prefect import get_run_logger


def check_summary(summary) -> dict:
    """Log a run summary and raise if the run failed outright, so Prefect marks the flow failed
    (and can alert). Partial runs complete; failures are in the logs and ``ingestion_runs``."""
    log = get_run_logger()
    log.info(
        "%s run %s: %d rows, %d failures",
        summary.source,
        summary.status,
        summary.rows,
        len(summary.failed),
    )
    if summary.signals or summary.alerts:
        log.info("%d new signals, %d new alerts", summary.signals, summary.alerts)
    if summary.rebackfilled:
        log.info(
            "price history re-fetched after a restatement: %s", ", ".join(summary.rebackfilled)
        )
    for key, reason in summary.failed.items():
        log.warning("%s failed: %s", key, reason)
    if summary.status == "failed":
        raise RuntimeError(f"{summary.source} run failed: {summary.failed}")
    return asdict(summary)
