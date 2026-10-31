from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fin_core import __version__
from fin_core.alerts.pipeline import feed_item
from fin_core.db import repo, signals_repo
from fin_core.db.models import Security
from fin_core.db.session import database_configured, database_url, make_engine, session_scope
from fin_core.features import FeatureFlags
from fin_core.graph import build_graph, load_config
from fin_core.metrics import UnknownSecurityError
from fin_core.models import ScoreResult, SecurityMetrics, Signal
from fin_core.scoring import score_security

log = logging.getLogger("fin_api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not os.environ.get("API_TOKEN"):
        log.warning("API_TOKEN is not set: state-changing endpoints are disabled (503)")
    yield
    dispose_engines()  # close pooled database connections on shutdown


app = FastAPI(title="fin-app API", version=__version__, lifespan=lifespan)


@lru_cache
def _graph():
    return build_graph()


@lru_cache
def _config():
    return load_config()


class ScoreRequest(BaseModel):
    metrics: SecurityMetrics
    previous_conviction: float | None = None


class ScoreResponse(BaseModel):
    # None when no philosophy could be evaluated (all_abstained): "no view", not a score of 0.
    # Store it as such; a stored 0.0 would later look like a collapse or a false `buy`.
    conviction: float | None
    all_abstained: bool = False
    explanation: str
    philosophy_results: dict[str, ScoreResult]
    guardrail_results: dict[str, ScoreResult] = Field(default_factory=dict)
    signal: Signal | None


class TickerScoreResponse(ScoreResponse):
    metrics: SecurityMetrics  # what was scored, including data-quality warnings


def require_write_token(authorization: str | None = Header(default=None)) -> None:
    """Guard for state-changing endpoints: ``Authorization: Bearer <API_TOKEN>``.

    Fails closed: with no ``API_TOKEN`` configured the endpoints are disabled (503) rather than
    open. The watchlist decides which sell alerts are sent at all, so it must not be writable by
    whoever can reach the port. Read endpoints are not covered; keep the API behind the proxy's
    own auth until the UI has a login.
    """
    expected = os.environ.get("API_TOKEN")
    if not expected:
        raise HTTPException(503, detail="write endpoints are disabled: API_TOKEN is not configured")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            401, detail="missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"}
        )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


_engines: dict[str, Engine] = {}


def _engine(url: str) -> Engine:
    """One engine (connection pool) per database URL, created lazily."""
    if url not in _engines:
        _engines[url] = make_engine(url, connect_timeout=3)  # a probe must not hang
    return _engines[url]


def dispose_engines() -> None:
    """Close every pooled connection and forget the engines (shutdown, and between tests)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness: config and graph load, and the database answers if one is configured.

    With no database configured (e.g. a bare dev run) the API is still ready and says so;
    with one configured, an unreachable database makes the service not ready (503).
    """
    _config()
    _graph()
    if not database_configured():
        return {"status": "ready", "db": "not configured"}
    try:
        with _engine(database_url()).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Only the exception type: the message can contain connection details.
        raise HTTPException(503, detail=f"database unavailable ({type(exc).__name__})") from None
    return {"status": "ready", "db": "ok"}


@app.get("/api/features")
def features() -> dict[str, bool]:
    """Read-only view of feature flags, shown on the settings page."""
    return FeatureFlags.load().as_dict()


@app.post("/api/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    out = _graph().invoke(
        {
            "metrics": req.metrics,
            "config": _config(),
            "previous_conviction": req.previous_conviction,
        }
    )
    return ScoreResponse(
        conviction=out["conviction"],
        all_abstained=out.get("all_abstained", False),
        explanation=out["explanation"],
        philosophy_results=out["philosophy_results"],
        guardrail_results=out.get("guardrail_results", {}),
        signal=out.get("signal"),
    )


@app.get("/api/securities/{ticker}/score", response_model=TickerScoreResponse)
def score_ticker(
    ticker: str, as_of: date | None = None, previous_conviction: float | None = None
) -> TickerScoreResponse:
    """Score a stored security from point-in-time metrics (default: as of today, UTC).

    Needs a configured database with ingested fundamentals and prices. Data gaps are not
    errors: they show up as ``metrics.warnings`` and as failed criteria, and while the data is
    too poor to act on (``metrics.signal_blockers``) no signal is emitted. A future ``as_of`` is
    rejected (422). ``previous_conviction`` is supplied by the caller only until signals and
    their history are persisted.
    """
    if not database_configured():
        raise HTTPException(503, detail="database not configured")
    today = datetime.now(UTC).date()
    if as_of is not None and as_of > today:
        raise HTTPException(422, detail=f"as_of {as_of} is in the future (today is {today} UTC)")
    try:
        with session_scope(_engine(database_url())) as session:
            out = score_security(
                session,
                ticker,
                as_of or today,
                previous_conviction=previous_conviction,
                graph=_graph(),
                config=_config(),
            )
    except UnknownSecurityError:
        raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}") from None
    except SQLAlchemyError as exc:
        # Same answer as /readyz for an unavailable database; only the type, no connection details.
        raise HTTPException(503, detail=f"database unavailable ({type(exc).__name__})") from None
    return TickerScoreResponse(
        conviction=out["conviction"],
        all_abstained=out.get("all_abstained", False),
        explanation=out["explanation"],
        philosophy_results=out["philosophy_results"],
        guardrail_results=out.get("guardrail_results", {}),
        signal=out.get("signal"),
        metrics=out["metrics"],
    )


# --- alerts, opportunities, watchlist, channels --------------------------------------------------


@contextmanager
def _db() -> Iterator[Session]:
    """A database session, or 503 (not configured / unreachable) like the other endpoints."""
    if not database_configured():
        raise HTTPException(503, detail="database not configured")
    try:
        with session_scope(_engine(database_url())) as session:
            yield session
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail=f"database unavailable ({type(exc).__name__})") from None


class AlertOut(BaseModel):
    id: int
    ticker: str
    kind: str
    severity: str
    conviction: float
    title: str
    body: str
    created_at: str | None
    read: bool
    deliveries: dict[str, str]  # channel -> sent | failed | skipped


class AlertsPage(BaseModel):
    items: list[AlertOut]
    next_before_id: int | None  # pass as `before_id` for the next (older) page; None = the end


@app.get("/api/alerts", response_model=AlertsPage)
def list_alerts(
    limit: int = Query(50, ge=1, le=200), before_id: int | None = None, unread_only: bool = False
) -> AlertsPage:
    """The in-app alert feed, newest first. Always populated, whatever other channels are on."""
    with _db() as s:
        rows = signals_repo.list_alerts(
            s, limit=limit, before_id=before_id, unread_only=unread_only
        )
        ids = {r.security_id for r in rows}
        tickers = (
            {x.id: x.ticker for x in s.scalars(select(Security).where(Security.id.in_(ids)))}
            if ids
            else {}
        )
        deliveries = signals_repo.deliveries_for(s, [r.id for r in rows])
        items = [feed_item(r, tickers[r.security_id], deliveries) for r in rows]
    return AlertsPage(
        items=items, next_before_id=rows[-1].id if rows and len(rows) == limit else None
    )


@app.post("/api/alerts/{alert_id}/read", dependencies=[Depends(require_write_token)])
def mark_read(alert_id: int) -> dict[str, object]:
    with _db() as s:
        if not signals_repo.mark_alert_read(s, alert_id, datetime.now(UTC)):
            raise HTTPException(404, detail=f"unknown alert {alert_id}")
    return {"id": alert_id, "read": True}


class OpportunityOut(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    conviction: float
    computed_at: str | None
    philosophies: dict[str, float]  # evaluated philosophies only
    price: float | None
    price_date: str | None


@app.get("/api/opportunities", response_model=list[OpportunityOut])
def opportunities(
    limit: int = Query(20, ge=1, le=200), max_age_days: int = Query(10, ge=1, le=3650)
) -> list[OpportunityOut]:
    """Securities ranked by the conviction of their latest actionable score. Stale or
    unevaluated runs are excluded, and so is any score older than ``max_age_days`` (default 10,
    which spans weekends and holidays), so a view from before a long data outage cannot sit in
    the ranking."""
    newer_than = datetime.now(UTC) - timedelta(days=max_age_days)
    with _db() as s:
        out = []
        for sec, score in signals_repo.opportunities(s, limit=limit, newer_than=newer_than):
            d = score.details or {}
            metrics = d.get("metrics", {})
            out.append(
                OpportunityOut(
                    ticker=sec.ticker,
                    name=sec.name,
                    sector=sec.sector,
                    conviction=score.conviction,
                    computed_at=score.computed_at.isoformat() if score.computed_at else None,
                    philosophies={
                        n: p["score"]
                        for n, p in (d.get("philosophies") or {}).items()
                        if p.get("evaluated", True)
                    },
                    price=metrics.get("price"),
                    price_date=metrics.get("price_date"),
                )
            )
    return out


@app.get("/api/watchlist")
def get_watchlist() -> dict[str, list[str]]:
    with _db() as s:
        return {"tickers": signals_repo.watchlist_tickers(s)}


@app.put("/api/watchlist/{ticker}", dependencies=[Depends(require_write_token)])
def watch(ticker: str) -> dict[str, object]:
    with _db() as s:
        sec = repo.get_security(s, ticker)
        if sec is None:
            raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}")
        return {"ticker": sec.ticker, "added": signals_repo.watchlist_add(s, sec.id)}


@app.delete("/api/watchlist/{ticker}", dependencies=[Depends(require_write_token)])
def unwatch(ticker: str) -> dict[str, object]:
    with _db() as s:
        sec = repo.get_security(s, ticker)
        if sec is None:
            raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}")
        return {"ticker": sec.ticker, "removed": signals_repo.watchlist_remove(s, sec.id)}


@app.get("/api/channels")
def channels() -> list[dict[str, object]]:
    """Which alert channels are active, from the feature flags (Telegram is off by default)."""
    muted: dict[str, str | None] = {}
    if database_configured():
        try:
            with _db() as s:
                until = signals_repo.muted_until(s, "telegram")
                muted["telegram"] = until.isoformat() if until else None
        except HTTPException:
            pass  # the flag view still works with the database down
    flags = FeatureFlags.load()
    return [
        {"name": "in_app", "enabled": True, "muted_until": None},
        {
            "name": "telegram",
            "enabled": flags.is_enabled("telegram_enabled"),
            "muted_until": muted.get("telegram"),
        },
    ]
