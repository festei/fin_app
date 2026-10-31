from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI
from pydantic import BaseModel

from fin_core import __version__
from fin_core.features import FeatureFlags
from fin_core.graph import build_graph, load_config
from fin_core.models import ScoreResult, SecurityMetrics, Signal

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness: config and graph load. Extended with a DB check in phase 1."""
    _config()
    _graph()
    return {"status": "ready"}


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
