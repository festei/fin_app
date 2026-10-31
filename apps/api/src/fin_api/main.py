from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from fin_core import __version__
from fin_core.db.session import database_configured, database_url, make_engine, session_scope
from fin_core.features import FeatureFlags
from fin_core.graph import build_graph, load_config
from fin_core.metrics import UnknownSecurityError
from fin_core.models import ScoreResult, SecurityMetrics, Signal
from fin_core.scoring import score_security

app = FastAPI(title="fin-app API", version=__version__)


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
    conviction: float
    explanation: str
    philosophy_results: dict[str, ScoreResult]
    signal: Signal | None


class TickerScoreResponse(ScoreResponse):
    metrics: SecurityMetrics  # what was scored, including data-quality warnings


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@lru_cache
def _engine(url: str):
    return make_engine(url, connect_timeout=3)  # a probe must not hang


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
        explanation=out["explanation"],
        philosophy_results=out["philosophy_results"],
        signal=out.get("signal"),
    )


@app.get("/api/securities/{ticker}/score", response_model=TickerScoreResponse)
def score_ticker(
    ticker: str, as_of: date | None = None, previous_conviction: float | None = None
) -> TickerScoreResponse:
    """Score a stored security from point-in-time metrics (default: as of today, UTC).

    Needs a configured database with ingested fundamentals and prices. Data gaps are not
    errors: they show up as ``metrics.warnings`` and as failed criteria.
    """
    if not database_configured():
        raise HTTPException(503, detail="database not configured")
    try:
        with session_scope(_engine(database_url())) as session:
            out = score_security(
                session,
                ticker,
                as_of or datetime.now(UTC).date(),
                previous_conviction=previous_conviction,
                graph=_graph(),
                config=_config(),
            )
    except UnknownSecurityError:
        raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}") from None
    return TickerScoreResponse(
        conviction=out["conviction"],
        explanation=out["explanation"],
        philosophy_results=out["philosophy_results"],
        signal=out.get("signal"),
        metrics=out["metrics"],
    )
