from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fin_core import __version__
from fin_core.alerts.pipeline import feed_item
from fin_core.db import repo, signals_repo
from fin_core.db.models import (
    Portfolio,
    PortfolioSnapshot,
    SandboxOrder,
    Security,
)
from fin_core.db.session import database_configured, database_url, make_engine, session_scope
from fin_core.features import FeatureFlags
from fin_core.graph import build_graph, load_config
from fin_core.metrics import UnknownSecurityError
from fin_core.models import ScoreResult, SecurityMetrics, Signal
from fin_core.sandbox import engine as sandbox
from fin_core.scoring import score_security

log = logging.getLogger("fin_api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not os.environ.get("API_TOKEN"):
        log.warning("API_TOKEN is not set: state-changing endpoints are disabled (503)")
    yield
    dispose_engines()  # close pooled database connections on shutdown


app = FastAPI(title="fin-app API", version=__version__, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without echoing the rejected input. FastAPI's default body repeats it, and a value
    like ``Infinity``/``NaN`` cannot be encoded as JSON, which turned a rejected request into a
    500."""
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


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


# --- paper-trading sandbox ---------------------------------------------------------------------


class PositionOut(BaseModel):
    ticker: str
    name: str | None
    quantity: float
    avg_cost: float
    price: float | None  # latest stored close; None if the stock has no bar
    market_value: float
    pnl: float
    pnl_pct: float | None


class PortfolioOut(BaseModel):
    id: int
    name: str
    mode: str  # manual | strategy
    initial_cash: float
    cash: float
    positions_value: float
    total_value: float
    return_pct: float
    fee_bps: float
    strategy_config: dict | None
    created_at: str | None


class PortfolioDetail(PortfolioOut):
    positions: list[PositionOut]


class OrderOut(BaseModel):
    id: int
    ticker: str
    side: str
    quantity: float | None
    notional: float | None
    status: str  # pending | filled | rejected | cancelled
    created_at: str
    filled_at: str | None
    fill_quantity: float | None
    fill_price: float | None
    fee: float | None
    reason: str | None
    signal_id: int | None


class SnapshotOut(BaseModel):
    as_of: str
    cash: float
    positions_value: float
    total_value: float


class NewPortfolio(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mode: str
    cash: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    fee_bps: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    strategy: dict | None = None  # overrides of config/sandbox.yml (strategy portfolios)


class NewOrder(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    side: str
    quantity: float | None = Field(default=None, gt=0, le=1e9, allow_inf_nan=False)
    notional: float | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)


def _f(x: Decimal | float | None) -> float | None:
    return None if x is None else float(x)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _portfolio(s: Session, portfolio_id: int) -> Portfolio:
    p = s.get(Portfolio, portfolio_id)
    if p is None:
        raise HTTPException(404, detail=f"unknown portfolio {portfolio_id}")
    return p


def _view(s: Session, p: Portfolio, now: datetime) -> dict:
    invested = sandbox.positions_value(s, p, now)
    total = Decimal(p.cash) + invested
    initial = Decimal(p.initial_cash)
    return {
        "id": p.id,
        "name": p.name,
        "mode": p.mode,
        "initial_cash": float(initial),
        "cash": float(p.cash),
        "positions_value": float(invested),
        "total_value": float(total),
        "return_pct": float((total - initial) / initial * 100),
        "fee_bps": float(p.fee_bps),
        "strategy_config": p.strategy_config,
        "created_at": _iso(p.created_at),
    }


def _order_out(o: SandboxOrder, ticker: str) -> OrderOut:
    return OrderOut(
        id=o.id,
        ticker=ticker,
        side=o.side,
        quantity=_f(o.quantity),
        notional=_f(o.notional),
        status=o.status,
        created_at=_iso(o.created_at) or "",
        filled_at=_iso(o.filled_at),
        fill_quantity=_f(o.fill_quantity),
        fill_price=_f(o.fill_price),
        fee=_f(o.fee),
        reason=o.reason,
        signal_id=o.signal_id,
    )


@app.get("/api/portfolios", response_model=list[PortfolioOut])
def list_portfolios() -> list[dict]:
    now = datetime.now(UTC)
    with _db() as s:
        return [_view(s, p, now) for p in s.scalars(select(Portfolio).order_by(Portfolio.id))]


@app.post(
    "/api/portfolios",
    response_model=PortfolioOut,
    status_code=201,
    dependencies=[Depends(require_write_token)],
)
def create_portfolio(body: NewPortfolio) -> dict:
    with _db() as s:
        try:
            p = sandbox.create_portfolio(
                s,
                body.name,
                mode=body.mode,
                cash=Decimal(str(body.cash)),
                fee_bps=body.fee_bps,
                strategy=body.strategy,
            )
        except sandbox.OrderError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        return _view(s, p, datetime.now(UTC))


@app.get("/api/portfolios/{portfolio_id}", response_model=PortfolioDetail)
def get_portfolio(portfolio_id: int) -> dict:
    now = datetime.now(UTC)
    with _db() as s:
        p = _portfolio(s, portfolio_id)
        rows = []
        for h in sandbox.holdings(s, p.id).values():
            sec = s.get(Security, h.security_id)
            price = sandbox.last_close(s, h.security_id, now)
            value = h.quantity * price if price is not None else h.cost
            rows.append(
                PositionOut(
                    ticker=sec.ticker,
                    name=sec.name,
                    quantity=float(h.quantity),
                    avg_cost=float(h.avg_cost),
                    price=_f(price),
                    market_value=float(value),
                    pnl=float(value - h.cost),
                    pnl_pct=float((value - h.cost) / h.cost * 100) if h.cost else None,
                )
            )
        rows.sort(key=lambda r: r.ticker)
        return {**_view(s, p, now), "positions": rows}


@app.get("/api/portfolios/{portfolio_id}/history", response_model=list[SnapshotOut])
def portfolio_history(portfolio_id: int) -> list[SnapshotOut]:
    """The equity curve: one value row per day the sandbox step ran."""
    with _db() as s:
        _portfolio(s, portfolio_id)
        rows = s.scalars(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == portfolio_id)
            .order_by(PortfolioSnapshot.as_of)
        )
        return [
            SnapshotOut(
                as_of=r.as_of.isoformat(),
                cash=float(r.cash),
                positions_value=float(r.positions_value),
                total_value=float(r.total_value),
            )
            for r in rows
        ]


@app.get("/api/portfolios/{portfolio_id}/orders", response_model=list[OrderOut])
def portfolio_orders(
    portfolio_id: int, status: str | None = None, limit: int = Query(100, ge=1, le=500)
) -> list[OrderOut]:
    with _db() as s:
        _portfolio(s, portfolio_id)
        q = select(SandboxOrder).where(SandboxOrder.portfolio_id == portfolio_id)
        if status:
            q = q.where(SandboxOrder.status == status)
        rows = s.scalars(q.order_by(SandboxOrder.id.desc()).limit(limit)).all()
        ids = {o.security_id for o in rows}
        tickers = (
            {x.id: x.ticker for x in s.scalars(select(Security).where(Security.id.in_(ids)))}
            if ids
            else {}
        )
        return [_order_out(o, tickers[o.security_id]) for o in rows]


@app.post(
    "/api/portfolios/{portfolio_id}/orders",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(require_write_token)],
)
def place_order(portfolio_id: int, body: NewOrder) -> OrderOut:
    """Queue a simulated order; it fills at the first daily close after today (see
    ``fin_core.sandbox.engine``). Manual portfolios only."""
    with _db() as s:
        try:
            o = sandbox.place_order(
                s,
                portfolio_id,
                body.ticker,
                body.side,
                quantity=None if body.quantity is None else Decimal(str(body.quantity)),
                notional=None if body.notional is None else Decimal(str(body.notional)),
            )
        except sandbox.UnknownError as exc:
            raise HTTPException(404, detail=str(exc)) from None
        except sandbox.OrderError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        return _order_out(o, s.get(Security, o.security_id).ticker)


@app.delete(
    "/api/portfolios/{portfolio_id}/orders/{order_id}",
    dependencies=[Depends(require_write_token)],
)
def cancel_order(portfolio_id: int, order_id: int) -> dict[str, object]:
    with _db() as s:
        _portfolio(s, portfolio_id)
        if not sandbox.cancel_order(s, portfolio_id, order_id, "cancelled by the user"):
            raise HTTPException(404, detail=f"no pending order {order_id} in this portfolio")
    return {"id": order_id, "cancelled": True}
