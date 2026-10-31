"""Ingestion jobs: fundamentals from EDGAR, prices, and bond series from FRED into the database.

Each job records an ``ingestion_runs`` row (ok / partial / failed) so a stalled or failing
pipeline is visible.

* A run is "failed" until it reaches normal completion, so a crash can never be recorded as ok.
* A failing ticker or series, including a database error while writing it, does not stop the
  others (``partial``). A rate-limit or access-denied response aborts the run so we stop
  hammering the provider.
* Rows left ``running`` by a process that died are marked failed by the next run
  (``fail_stale_runs``).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Engine, select

from fin_core.db import repo
from fin_core.db.models import BondYield, IngestionRun, PriceBar, Security
from fin_core.db.session import session_scope
from fin_core.providers.base import (
    AccessDeniedError,
    PriceProvider,
    ProviderError,
    RateLimitError,
)
from fin_core.providers.edgar import EdgarClient, normalize_facts
from fin_core.providers.fred import FredClient

# FRED revises some series after first publication; re-read the last few days each run.
YIELD_OVERLAP_DAYS = 7
# A run still "running" after this long belongs to a process that died.
STALE_RUN_AFTER = timedelta(hours=6)
# Relative close difference above which a stored bar counts as restated by the price source.
RESTATEMENT_TOLERANCE = 1e-5

# Errors after which continuing is pointless (we would only be blocked again).
_ABORT = (RateLimitError, AccessDeniedError)


@dataclass
class RunSummary:
    source: str
    status: str = "failed"  # pessimistic: only normal completion may change it
    rows: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # ticker/series -> reason
    rebackfilled: list[str] = field(default_factory=list)  # tickers whose history was rewritten


def _describe(exc: BaseException) -> str:
    if isinstance(exc, Exception):
        return f"{type(exc).__name__}: {exc}"[:200]
    return f"interrupted ({type(exc).__name__})"


def fail_stale_runs(engine: Engine, older_than: timedelta = STALE_RUN_AFTER) -> int:
    """Mark runs stuck in ``running`` (their process died) as failed. Returns how many."""
    now = datetime.now(UTC)
    with session_scope(engine) as s:
        stale = list(
            s.scalars(
                select(IngestionRun).where(
                    IngestionRun.status == "running", IngestionRun.started_at < now - older_than
                )
            )
        )
        for run in stale:
            run.status = "failed"
            run.finished_at = now
            run.error = "stale: the process ended before finishing this run"
        return len(stale)


def _finish_run(engine: Engine, run_id: int, summary: RunSummary) -> None:
    error = "; ".join(f"{k}: {v}" for k, v in summary.failed.items())[:500] or None
    with session_scope(engine) as s:
        run = s.get(IngestionRun, run_id)
        run.status, run.rows, run.error = summary.status, summary.rows, error
        run.finished_at = datetime.now(UTC)


@contextmanager
def _tracked(engine: Engine, source: str) -> Iterator[RunSummary]:
    """Record the run. The summary starts "failed"; an exception that escapes the job is
    recorded (and re-raised) so it is never reported as healthy."""
    fail_stale_runs(engine)
    with session_scope(engine) as s:
        run = IngestionRun(source=source, status="running")
        s.add(run)
        s.flush()
        run_id = run.id
    summary = RunSummary(source)
    try:
        yield summary
    except BaseException as exc:
        summary.status = "failed"
        summary.failed.setdefault("*", _describe(exc))
        raise
    finally:
        _finish_run(engine, run_id, summary)


def _status(summary: RunSummary, total: int, aborted: bool) -> str:
    if aborted or (summary.failed and len(summary.failed) >= total):
        return "failed"
    return "partial" if summary.failed else "ok"


def ingest_fundamentals(
    engine: Engine, edgar: EdgarClient, tickers: list[str] | None = None
) -> RunSummary:
    """Resolve missing CIKs, then store all periodic-filing facts for each stock."""
    wanted = {t.upper() for t in tickers} if tickers else None
    with _tracked(engine, "edgar") as summary:
        with session_scope(engine) as s:
            stocks = [
                (x.id, x.ticker, x.cik)
                for x in repo.list_securities(s)
                if x.kind == "stock" and (wanted is None or x.ticker in wanted)
            ]
        try:
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
        except ProviderError as exc:  # ticker map unavailable: nothing can be resolved
            summary.failed["*"] = str(exc)
            summary.status = "failed"
            return summary

        aborted = False
        for sid, ticker, cik in stocks:
            if cik is None:
                summary.failed[ticker] = "no CIK (not in SEC ticker list)"
                continue
            try:
                facts = normalize_facts(edgar.company_facts(cik))
                with session_scope(engine) as s:
                    summary.rows += repo.upsert_facts(s, sid, facts)
            except _ABORT as exc:
                summary.failed[ticker] = str(exc)
                aborted = True
                break
            except Exception as exc:  # provider errors and database errors alike
                summary.failed[ticker] = (
                    _describe(exc) if not isinstance(exc, ProviderError) else str(exc)
                )
        summary.status = _status(summary, len(stocks), aborted)
    return summary


def ingest_yields(
    engine: Engine, fred: FredClient, series_ids: list[str], start: date | None = None
) -> RunSummary:
    """Fetch FRED series incrementally (from the last stored date minus an overlap)."""
    with _tracked(engine, "fred") as summary:
        aborted = False
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
            except _ABORT as exc:
                summary.failed[series_id] = str(exc)
                aborted = True
                break
            except Exception as exc:
                summary.failed[series_id] = (
                    _describe(exc) if not isinstance(exc, ProviderError) else str(exc)
                )
        summary.status = _status(summary, len(series_ids), aborted)
    return summary


def _num(x: Any) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def bars_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Provider frame (first column = timestamp, then Open/High/Low/Close/Volume) -> bar dicts.

    Timestamps are converted to UTC (naive ones are taken as UTC). Rows without a close are
    dropped, other missing values become NULL.
    """
    if frame.empty:
        return []
    ts_col = frame.columns[0]
    bars: list[dict[str, Any]] = []
    for rec in frame.to_dict("records"):
        close = _num(rec.get("Close"))
        if close is None:
            continue
        ts = pd.Timestamp(rec[ts_col])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        volume = _num(rec.get("Volume"))
        bars.append(
            {
                "ts": ts.to_pydatetime(),
                "open": _num(rec.get("Open")),
                "high": _num(rec.get("High")),
                "low": _num(rec.get("Low")),
                "close": close,
                "volume": int(volume) if volume is not None else None,
            }
        )
    return bars


def _naive_utc(dt: datetime) -> datetime:
    """SQLite returns timestamps without tzinfo; compare everything as naive UTC."""
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def restated(engine: Engine, security_id: int, bars: list[dict[str, Any]]) -> bool:
    """True if the source now reports different closes than we stored for the same bars.

    Yahoo's default prices are adjusted for splits and dividends as of fetch time, so after a
    corporate action every earlier bar changes. Overwriting only the recent window would leave
    the older history on the old basis (a discontinuity); the caller re-back-fills instead.
    """
    if not bars:
        return False
    first = min(b["ts"] for b in bars)
    with session_scope(engine) as s:
        rows = s.execute(
            select(PriceBar.ts, PriceBar.close).where(
                PriceBar.security_id == security_id, PriceBar.ts >= first
            )
        ).all()
    stored = {_naive_utc(ts): close for ts, close in rows}
    for b in bars:
        old = stored.get(_naive_utc(b["ts"]))
        if old is not None and abs(b["close"] - old) > RESTATEMENT_TOLERANCE * max(abs(old), 1e-9):
            return True
    return False


def ingest_prices(
    engine: Engine,
    provider: PriceProvider,
    tickers: list[str] | None = None,
    *,
    backfill_period: str = "max",
    refresh_period: str = "1mo",
) -> RunSummary:
    """Store price bars for every active security (stocks and bond ETFs).

    A security with no stored bars is back-filled (``backfill_period``); otherwise only a
    recent window is re-read (``refresh_period``). If that window shows the source restated
    prices we already hold (split/dividend adjustment), the whole history is re-fetched so the
    series stays on one adjustment basis; those tickers are listed in ``rebackfilled``.

    NOTE: stored prices are the source's adjusted prices, not as-traded prices. That is fine for
    charts and current valuation, but a point-in-time backtest needs as-traded prices (raw OHLC
    plus adjustment factors), which is planned with the backtester.
    """
    wanted = {t.upper() for t in tickers} if tickers else None
    with _tracked(engine, "prices") as summary:
        with session_scope(engine) as s:
            targets = [
                (x.id, x.ticker)
                for x in repo.list_securities(s)
                if wanted is None or x.ticker in wanted
            ]
            has_bars = set(s.scalars(select(PriceBar.security_id).distinct()))
        aborted = False
        for sid, ticker in targets:
            known = sid in has_bars
            try:
                bars = bars_from_frame(
                    provider.history(ticker, period=refresh_period if known else backfill_period)
                )
                if not bars:
                    summary.failed[ticker] = "no price data returned"
                    continue
                if known and restated(engine, sid, bars):
                    full = bars_from_frame(provider.history(ticker, period=backfill_period))
                    if full:
                        bars = full
                        summary.rebackfilled.append(ticker)
                with session_scope(engine) as s:
                    summary.rows += repo.upsert_prices(s, sid, bars)
            except _ABORT as exc:
                summary.failed[ticker] = str(exc)
                aborted = True
                break
            except Exception as exc:  # yfinance raises assorted errors; isolate per ticker
                summary.failed[ticker] = (
                    _describe(exc) if not isinstance(exc, ProviderError) else str(exc)
                )
        summary.status = _status(summary, len(targets), aborted)
    return summary
