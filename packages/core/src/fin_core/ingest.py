"""Ingestion jobs: fundamentals from EDGAR and bond series from FRED into the database.

Each job records an ``ingestion_runs`` row (ok / partial / failed) so a stalled or failing
pipeline is visible. A failing ticker or series does not stop the others; a rate-limit
response aborts the run so we stop hammering the provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select

from fin_core.db import repo
from fin_core.db.models import BondYield, IngestionRun, Security
from fin_core.db.session import session_scope
from fin_core.providers.base import ProviderError, RateLimitError
from fin_core.providers.edgar import EdgarClient, normalize_facts
from fin_core.providers.fred import FredClient

# FRED revises some series after first publication; re-read the last few days each run.
YIELD_OVERLAP_DAYS = 7


@dataclass
class RunSummary:
    source: str
    status: str = "ok"
    rows: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # ticker/series -> reason


def _start_run(engine: Engine, source: str) -> int:
    with session_scope(engine) as s:
        run = IngestionRun(source=source, status="running")
        s.add(run)
        s.flush()
        return run.id


def _finish_run(engine: Engine, run_id: int, summary: RunSummary) -> None:
    error = "; ".join(f"{k}: {v}" for k, v in summary.failed.items())[:500] or None
    with session_scope(engine) as s:
        run = s.get(IngestionRun, run_id)
        run.status, run.rows, run.error = summary.status, summary.rows, error
        run.finished_at = datetime.now(UTC)


def _status(summary: RunSummary, total: int, aborted: bool) -> str:
    if aborted or (summary.failed and len(summary.failed) >= total):
        return "failed"
    return "partial" if summary.failed else "ok"


def ingest_fundamentals(
    engine: Engine, edgar: EdgarClient, tickers: list[str] | None = None
) -> RunSummary:
    """Resolve missing CIKs, then store all periodic-filing facts for each stock."""
    summary = RunSummary("edgar")
    run_id = _start_run(engine, "edgar")
    aborted = False
    wanted = {t.upper() for t in tickers} if tickers else None
    try:
        with session_scope(engine) as s:
            stocks = [
                (x.id, x.ticker, x.cik)
                for x in repo.list_securities(s)
                if x.kind == "stock" and (wanted is None or x.ticker in wanted)
            ]

        if any(cik is None for _, _, cik in stocks):
            mapping = edgar.ticker_map()
            updates = []
            for i, (sid, ticker, cik) in enumerate(stocks):
                if cik is None and ticker in mapping:
                    updates.append((sid, mapping[ticker]))
                    stocks[i] = (sid, ticker, mapping[ticker]["cik"])
            with session_scope(engine) as s:
                for sid, info in updates:
                    sec = s.get(Security, sid)
                    sec.cik = info["cik"]
                    sec.name = sec.name or info["name"]

        for sid, ticker, cik in stocks:
            if cik is None:
                summary.failed[ticker] = "no CIK (not in SEC ticker list)"
                continue
            try:
                facts = normalize_facts(edgar.company_facts(cik))
                with session_scope(engine) as s:
                    summary.rows += repo.upsert_facts(s, sid, facts)
            except RateLimitError as exc:
                summary.failed[ticker] = str(exc)
                aborted = True
                break
            except ProviderError as exc:
                summary.failed[ticker] = str(exc)
        summary.status = _status(summary, len(stocks), aborted)
    except ProviderError as exc:  # e.g. ticker map unavailable
        summary.failed["*"] = str(exc)
        summary.status = "failed"
    finally:
        _finish_run(engine, run_id, summary)
    return summary


def ingest_yields(
    engine: Engine, fred: FredClient, series_ids: list[str], start: date | None = None
) -> RunSummary:
    """Fetch FRED series incrementally (from the last stored date minus an overlap)."""
    summary = RunSummary("fred")
    run_id = _start_run(engine, "fred")
    aborted = False
    try:
        for series_id in series_ids:
            try:
                since = start
                if since is None:
                    with session_scope(engine) as s:
                        last: Any = s.scalar(
                            select(BondYield.obs_date)
                            .where(BondYield.series_id == series_id)
                            .order_by(BondYield.obs_date.desc())
                            .limit(1)
                        )
                    since = last - timedelta(days=YIELD_OVERLAP_DAYS) if last else None
                points = fred.observations(series_id, since)
                with session_scope(engine) as s:
                    summary.rows += repo.upsert_yields(s, series_id, points)
            except RateLimitError as exc:
                summary.failed[series_id] = str(exc)
                aborted = True
                break
            except ProviderError as exc:
                summary.failed[series_id] = str(exc)
        summary.status = _status(summary, len(series_ids), aborted)
    finally:
        _finish_run(engine, run_id, summary)
    return summary
