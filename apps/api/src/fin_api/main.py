from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from importlib.metadata import version
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fin_application.errors import StorageUnavailable
from fin_application.scoring import Baseline
from fin_application.scoring import score as score_metrics
from fin_bootstrap import Container, FeatureFlags, Settings
from fin_domain.sandbox import OrderError
from fin_domain.scoring import ScoreResult, SecurityMetrics
from fin_domain.shared import NotFound
from fin_domain.signals import Signal

log = logging.getLogger("fin_api")
__version__ = version("fin-api")


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
def _container() -> Container:
    """Wiring for the scoring engine and its thresholds; the database is still reached through
    this module's own session helpers until the HTTP layer moves onto the container."""
    return Container.from_environ()


def _scoring_engine():
    return _container().scoring_engine


@lru_cache
def _config():
    return dict(_container().scoring_config.load())


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


_containers: dict[str, Container] = {}


def _container_for(url: str) -> Container:
    """The use cases for one database URL. They share the scoring engine and thresholds."""
    if url not in _containers:
        _containers[url] = Container(
            Settings(database_url=url, db_connect_timeout=3),  # a probe must not hang
            scoring_engine=_scoring_engine(),
            scoring_config=_container().scoring_config,
        )
    return _containers[url]


def dispose_engines() -> None:
    """Close every pooled connection and forget the containers (shutdown, and between tests)."""
    for container in _containers.values():
        container.dispose()
    _containers.clear()


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness: config and graph load, and the database answers if one is configured.

    With no database configured (e.g. a bare dev run) the API is still ready and says so;
    with one configured, an unreachable database makes the service not ready (503).
    """
    _config()
    _scoring_engine()
    settings = Settings.from_environ()
    if not settings.database_configured:
        return {"status": "ready", "db": "not configured"}
    try:
        _container_for(settings.database_url).check_database()
    except StorageUnavailable as exc:  # only the kind of error: the original can hold details
        raise HTTPException(503, detail=str(exc)) from None
    return {"status": "ready", "db": "ok"}


@app.get("/api/features")
def features() -> dict[str, bool]:
    """Read-only view of feature flags, shown on the settings page."""
    return FeatureFlags.load().as_dict()


@app.post("/api/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    live = score_metrics(
        req.metrics,
        _scoring_engine(),
        _config(),
        Baseline(previous_conviction=req.previous_conviction),
    )
    a = live.assessment
    return ScoreResponse(
        conviction=a.conviction,
        all_abstained=a.all_abstained,
        explanation=live.explanation,
        philosophy_results=a.philosophy_results,
        guardrail_results=a.guardrail_results,
        signal=live.signal,
    )


@app.get("/api/securities/{ticker}/score", response_model=TickerScoreResponse)
def score_ticker(
    ticker: str, as_of: date | None = None, previous_conviction: float | None = None
) -> TickerScoreResponse:
    """Score a stored security from point-in-time metrics (default: as of today, UTC).

    Needs a configured database with ingested fundamentals and prices. Data gaps are not
    errors: they show up as ``metrics.warnings`` and as failed criteria, and while the data is
    too poor to act on (``metrics.signal_blockers``) no signal is emitted. A future ``as_of`` is
    rejected (422). This scores live and stores nothing. For "today" (no ``as_of``) the signal is
    judged against the stored baseline and hysteresis, exactly as the scheduled run would, so
    ``signal`` is only set when a transition has happened since the last stored score (and not on
    every visit to a stock that is already high); pass ``previous_conviction`` to override the
    baseline, or ``as_of`` for a what-if without one.
    """
    today = datetime.now(UTC).date()
    if as_of is not None and as_of > today:
        raise HTTPException(422, detail=f"as_of {as_of} is in the future (today is {today} UTC)")
    try:
        with _app() as app_:
            live = app_.score_security_live.execute(
                ticker,
                as_of or today,
                Baseline(previous_conviction=previous_conviction)
                if previous_conviction is not None
                else None,
                use_stored_baseline=previous_conviction is None and as_of is None,
            )
    except NotFound:
        raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}") from None
    except HTTPException:
        raise
    a = live.assessment
    return TickerScoreResponse(
        conviction=a.conviction,
        all_abstained=a.all_abstained,
        explanation=live.explanation,
        philosophy_results=a.philosophy_results,
        guardrail_results=a.guardrail_results,
        signal=live.signal,
        metrics=live.metrics,
    )


def _f(x: Decimal | float | None) -> float | None:
    return None if x is None else float(x)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _not_found(exc: NotFound) -> HTTPException:
    return HTTPException(404, detail=f"unknown {exc.entity} {exc.key}")


# --- alerts, opportunities, watchlist, channels --------------------------------------------------


@contextmanager
def _app() -> Iterator[Container]:
    """The use cases for the configured database, or 503 (not configured / unreachable) like the
    other endpoints."""
    settings = Settings.from_environ()
    if not settings.database_configured:
        raise HTTPException(503, detail="database not configured")
    try:
        yield _container_for(settings.database_url)
    except StorageUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from None


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
    with _app() as app_:
        rows = app_.alert_feed.page(limit, before_id, unread_only)
    items = [
        AlertOut(
            id=a.id,
            ticker=str(a.ticker),
            kind=a.kind,
            severity=a.severity,
            conviction=a.conviction,
            title=a.title,
            body=a.visible_body,
            created_at=a.created_at.isoformat(),
            read=a.read_at is not None,
            deliveries={d.channel: d.status.value for d in a.deliveries},
        )
        for a in rows
    ]
    return AlertsPage(
        items=items, next_before_id=rows[-1].id if rows and len(rows) == limit else None
    )


@app.post("/api/alerts/{alert_id}/read", dependencies=[Depends(require_write_token)])
def mark_read(alert_id: int) -> dict[str, object]:
    with _app() as app_:
        try:
            app_.alert_feed.mark_read(alert_id)
        except NotFound:
            raise HTTPException(404, detail=f"unknown alert {alert_id}") from None
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
    with _app() as app_:
        rows = app_.opportunities.top(limit, newer_than)
    return [
        OpportunityOut(
            ticker=str(r.ticker),
            name=r.name,
            sector=r.sector,
            conviction=r.conviction,
            computed_at=r.computed_at.isoformat() if r.computed_at else None,
            philosophies={n: p.score for n, p in r.details.evaluated().items()},
            price=r.details.metrics.price,
            price_date=(
                r.details.metrics.price_date.isoformat() if r.details.metrics.price_date else None
            ),
        )
        for r in rows
    ]


@app.get("/api/watchlist")
def get_watchlist() -> dict[str, list[str]]:
    with _app() as app_:
        return {"tickers": app_.watchlist.tickers()}


@app.put("/api/watchlist/{ticker}", dependencies=[Depends(require_write_token)])
def watch(ticker: str) -> dict[str, object]:
    with _app() as app_:
        try:
            symbol, added = app_.watchlist.add(ticker)
        except NotFound:
            raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}") from None
    return {"ticker": str(symbol), "added": added}


@app.delete("/api/watchlist/{ticker}", dependencies=[Depends(require_write_token)])
def unwatch(ticker: str) -> dict[str, object]:
    with _app() as app_:
        try:
            symbol, removed = app_.watchlist.remove(ticker)
        except NotFound:
            raise HTTPException(404, detail=f"unknown ticker {ticker.upper()}") from None
    return {"ticker": str(symbol), "removed": removed}


@app.get("/api/channels")
def channels() -> list[dict[str, object]]:
    """Which alert channels are active, from the feature flags (Telegram is off by default)."""
    muted: dict[str, str | None] = {}
    if Settings.from_environ().database_configured:
        try:
            with _app() as app_:
                until = app_.channel_service.state("telegram").muted_until
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


# --- securities: search, price history, score history ---------------------------------------------


class SecurityOut(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    kind: str  # stock | etf
    watched: bool


class BarOut(BaseModel):
    ts: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None


class ScorePoint(BaseModel):
    computed_at: str | None  # when the job ran
    as_of: str  # the scoring date (end of day, UTC): plot against this
    conviction: float | None  # None = nothing could be evaluated (not a score of 0)
    signal_blocked: bool  # computed on stale or missing data; no signal was possible
    all_abstained: bool


class SignalPoint(BaseModel):
    kind: str
    conviction: float
    as_of: str


class ScoreHistory(BaseModel):
    scores: list[ScorePoint]
    signals: list[SignalPoint]  # transitions, for chart markers


@app.get("/api/securities", response_model=list[SecurityOut])
def list_securities(
    q: str | None = Query(None, max_length=50),
    sector: str | None = Query(None, max_length=100),
    kind: Literal["stock", "etf"] | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[SecurityOut]:
    """Search the active universe by ticker prefix or name fragment (case-insensitive, matched
    literally: ``%``, ``_`` and ``\\`` are ordinary characters), optionally by sector or kind.
    Inactive securities are not listed, but their history stays readable by ticker."""
    with _app() as app_:
        rows = app_.security_catalog.search(q, sector, kind, limit)
    return [
        SecurityOut(
            ticker=str(r.security.ticker),
            name=r.security.name,
            sector=r.security.sector,
            kind=r.security.kind.value,
            watched=r.watched,
        )
        for r in rows
    ]


@app.get("/api/securities/{ticker}/prices", response_model=list[BarOut])
def security_prices(
    ticker: str,
    days: int = Query(365, ge=1, le=20000),
    max_points: int = Query(1000, ge=10, le=5000),
) -> list[BarOut]:
    """Daily bars for the last ``days`` days, oldest first. With more than ``max_points`` bars,
    consecutive bars are merged into candles (first open, max high, min low, last close, summed
    volume), so the result is valid for candlestick and high/low charts as well as close lines.
    Stored prices are split/dividend-adjusted. Inactive securities are still served."""
    with _app() as app_:
        try:
            bars = app_.security_catalog.prices(ticker, days, max_points)
        except NotFound as exc:
            raise _not_found(exc) from None
    return [
        BarOut(
            ts=b.ts.isoformat(),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
    ]


@app.get("/api/securities/{ticker}/scores", response_model=ScoreHistory)
def security_scores(
    ticker: str, days: int = Query(365, ge=1, le=20000), limit: int = Query(1000, ge=1, le=5000)
) -> ScoreHistory:
    """Stored scores (oldest first) and the signals they produced, for the conviction chart.
    Blocked and all-abstained runs are included but flagged, so a gap is visible as a gap. Plot
    against ``as_of`` (the scoring date), not ``computed_at`` (when the job ran). Inactive
    securities are still served."""
    with _app() as app_:
        try:
            history = app_.security_catalog.score_history(ticker, days, limit)
        except NotFound as exc:
            raise _not_found(exc) from None
    return ScoreHistory(
        scores=[
            ScorePoint(
                computed_at=_iso(r.computed_at),
                as_of=r.as_of.isoformat(),
                conviction=r.conviction,
                signal_blocked=r.signal_blocked,
                all_abstained=r.all_abstained,
            )
            for r in history.scores
        ],
        signals=[
            SignalPoint(kind=x.kind, conviction=x.conviction, as_of=x.as_of.isoformat())
            for x in history.signals
        ],
    )


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


def _view(v) -> dict:
    p = v.portfolio
    return {
        "id": p.id,
        "name": p.name,
        "mode": p.mode.value,
        "initial_cash": float(p.initial_cash),
        "cash": float(p.cash),
        "positions_value": float(v.invested),
        "total_value": float(v.total),
        "return_pct": float(v.return_pct),
        "fee_bps": float(p.fee_bps),
        "strategy_config": p.strategy.to_json() if p.strategy else None,
        "created_at": _iso(p.created_at),
    }


def _order_out(o) -> OrderOut:
    return OrderOut(
        id=o.id,
        ticker=str(o.ticker),
        side=o.side.value,
        quantity=_f(o.quantity),
        notional=_f(o.notional),
        status=o.status.value,
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
    with _app() as app_:
        return [_view(v) for v in app_.portfolio_reader.list()]


@app.post(
    "/api/portfolios",
    response_model=PortfolioOut,
    status_code=201,
    dependencies=[Depends(require_write_token)],
)
def create_portfolio(body: NewPortfolio) -> dict:
    with _app() as app_:
        try:
            view = app_.create_portfolio.execute(
                body.name,
                mode=body.mode,
                cash=Decimal(str(body.cash)),
                fee_bps=body.fee_bps,
                strategy=body.strategy,
            )
        except OrderError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        return _view(view)


@app.get("/api/portfolios/{portfolio_id}", response_model=PortfolioDetail)
def get_portfolio(portfolio_id: int) -> dict:
    with _app() as app_:
        try:
            view, positions = app_.portfolio_reader.detail(portfolio_id)
        except NotFound as exc:
            raise _not_found(exc) from None
    rows = [
        PositionOut(
            ticker=str(v.ticker),
            name=v.name,
            quantity=float(v.position.holding.quantity),
            avg_cost=float(v.position.holding.avg_cost),
            price=_f(v.position.price),
            market_value=float(v.position.value),
            pnl=float(v.pnl),
            pnl_pct=_f(v.pnl_pct),
        )
        for v in positions
    ]
    return {**_view(view), "positions": rows}


@app.get("/api/portfolios/{portfolio_id}/history", response_model=list[SnapshotOut])
def portfolio_history(portfolio_id: int) -> list[SnapshotOut]:
    """The equity curve: one value row per day the sandbox step ran."""
    with _app() as app_:
        try:
            rows = app_.portfolio_reader.history(portfolio_id)
        except NotFound as exc:
            raise _not_found(exc) from None
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
    with _app() as app_:
        try:
            orders = app_.portfolio_reader.orders(portfolio_id, status, limit)
        except NotFound as exc:
            raise _not_found(exc) from None
    return [_order_out(o) for o in orders]


@app.post(
    "/api/portfolios/{portfolio_id}/orders",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(require_write_token)],
)
def place_order(portfolio_id: int, body: NewOrder) -> OrderOut:
    """Queue a simulated order; it fills at the first daily close after today (see
    ``fin_domain.sandbox.Portfolio``). Manual portfolios only."""
    with _app() as app_:
        try:
            order = app_.place_order.execute(
                portfolio_id,
                body.ticker,
                body.side,
                quantity=None if body.quantity is None else Decimal(str(body.quantity)),
                notional=None if body.notional is None else Decimal(str(body.notional)),
            )
        except NotFound as exc:
            raise _not_found(exc) from None
        except OrderError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        return _order_out(order)


@app.delete(
    "/api/portfolios/{portfolio_id}/orders/{order_id}",
    dependencies=[Depends(require_write_token)],
)
def cancel_order(portfolio_id: int, order_id: int) -> dict[str, object]:
    with _app() as app_:
        try:
            cancelled = app_.cancel_order.execute(portfolio_id, order_id)
        except NotFound as exc:
            raise _not_found(exc) from None
        if not cancelled:
            raise HTTPException(404, detail=f"no pending order {order_id} in this portfolio")
    return {"id": order_id, "cancelled": True}
