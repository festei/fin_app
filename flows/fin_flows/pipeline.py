"""The daily pipeline: ingestion in order, then scoring, then the sandbox, as ONE flow.

Scheduling these as separate cron entries only orders them by clock: a slow ingestion would still
be followed by a scoring run on old data. Chained here, scoring starts when ingestion has actually
finished. A failing step does not stop the later ones (isolated, like the per-ticker failures
inside each job), including scoring, which copes with stale data through its signal blockers; the
flow fails at the end naming every step that failed so Prefect can alert.
"""

from __future__ import annotations

from prefect import flow, get_run_logger

from fin_flows.ingestion import (
    ingest_fundamentals_flow,
    ingest_prices_flow,
    ingest_yields_flow,
    sync_universe_flow,
)
from fin_flows.sandbox import sandbox_step_flow
from fin_flows.scoring import score_universe_flow


def steps():
    # looked up at call time (not import time) so the order is easy to test and to override
    return [
        ("sync-universe", sync_universe_flow),
        ("ingest-prices", ingest_prices_flow),
        ("ingest-yields", ingest_yields_flow),
        ("ingest-fundamentals", ingest_fundamentals_flow),
        ("score-universe", score_universe_flow),
        ("sandbox-step", sandbox_step_flow),
    ]


@flow(name="daily-pipeline")
def daily_pipeline_flow() -> dict:
    log = get_run_logger()
    results: dict = {}
    failures: dict[str, str] = {}
    for name, step in steps():
        try:
            results[name] = step()
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"[:300]
            log.error("%s failed: %s", name, failures[name])
    if failures:
        raise RuntimeError(f"daily pipeline: {len(failures)} step(s) failed: {failures}")
    return results
