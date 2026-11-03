"""Use cases: paper-trading portfolios. Not investment advice.

The portfolio is a domain object that asks for the prices it needs (``price_requests``); the
loader below answers from storage. Each domain step gets a book built from the state the previous
step left behind.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fin_application.job_runs import JobRunTracker
from fin_application.ports import Clock, SandboxConfigSource, UnitOfWork, UnitOfWorkFactory
from fin_application.runs import RunSummary, run_status, tracked_run
from fin_application.scoring import parse_ticker
from fin_domain.sandbox import (
    Order,
    Portfolio,
    PortfolioMode,
    PortfolioSnapshot,
    PositionValue,
    PriceBook,
    to_decimal,
)
from fin_domain.shared import NotFound, Ticker

log = logging.getLogger(__name__)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_price_book(
    uow: UnitOfWork, portfolio: Portfolio, as_of: datetime, extra: Sequence[Ticker] = ()
) -> PriceBook:
    """The prices ``portfolio`` needs right now, as of ``as_of`` (nothing later is looked at)."""
    as_of = _aware(as_of)
    req = portfolio.price_requests(extra)
    last = {}
    for ticker in req.last_close_for:
        bar = uow.prices.latest(ticker, as_of)
        if bar is not None:
            last[ticker] = to_decimal(bar.close)
    closes = {}
    for ticker, ts in req.close_at:
        close = uow.prices.close_at(ticker, ts)
        if close is not None:
            closes[(ticker, ts)] = to_decimal(close)
    firsts = {}
    for ticker, start in req.first_bar_from:
        bar = uow.prices.first_bar_between(ticker, start, as_of)
        if bar is not None:
            firsts[(ticker, start)] = bar
    return PriceBook(last_closes=last, bar_closes=closes, first_bars=firsts)


# --- read models ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioView:
    portfolio: Portfolio
    invested: Decimal  # positions at the latest close
    total: Decimal  # cash + invested
    return_pct: Decimal


@dataclass(frozen=True)
class PositionView:
    ticker: Ticker
    name: str | None
    position: PositionValue

    @property
    def pnl(self) -> Decimal:
        return self.position.value - self.position.holding.cost

    @property
    def pnl_pct(self) -> Decimal | None:
        cost = self.position.holding.cost
        return self.pnl / cost * 100 if cost else None


def _view(uow: UnitOfWork, p: Portfolio, now: datetime) -> PortfolioView:
    invested = p.positions_value(load_price_book(uow, p, now))
    total = Decimal(p.cash) + invested
    initial = Decimal(p.initial_cash)
    return PortfolioView(p, invested, total, (total - initial) / initial * 100)


class PortfolioReader:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def list(self) -> list[PortfolioView]:
        now = self._clock.now()
        with self._uow_factory() as uow:
            return [_view(uow, p, now) for p in uow.portfolios.list()]

    def detail(self, portfolio_id: int) -> tuple[PortfolioView, list[PositionView]]:
        """The portfolio and its positions (sorted by ticker). ``NotFound`` if there is none."""
        now = self._clock.now()
        with self._uow_factory() as uow:
            p = _get(uow, portfolio_id)
            book = load_price_book(uow, p, now)
            names = {}
            for pos in p.valued_positions(book):
                sec = uow.securities.get(pos.holding.ticker)
                names[pos.holding.ticker] = sec.name if sec else None
            positions = [
                PositionView(pos.holding.ticker, names[pos.holding.ticker], pos)
                for pos in p.valued_positions(book)
            ]
            positions.sort(key=lambda v: str(v.ticker))
            return _view(uow, p, now), positions

    def history(self, portfolio_id: int) -> list[PortfolioSnapshot]:
        with self._uow_factory() as uow:
            _get(uow, portfolio_id)
            return uow.portfolios.history(portfolio_id)

    def orders(self, portfolio_id: int, status: str | None = None, limit: int = 100) -> list[Order]:
        with self._uow_factory() as uow:
            _get(uow, portfolio_id)
            return uow.portfolios.orders(portfolio_id, status, limit)


def _get(uow: UnitOfWork, portfolio_id: int) -> Portfolio:
    p = uow.portfolios.get(portfolio_id)
    if p is None:
        raise NotFound("portfolio", portfolio_id)
    return p


# --- commands ---------------------------------------------------------------------------------


class CreatePortfolio:
    def __init__(self, uow_factory: UnitOfWorkFactory, defaults: SandboxConfigSource, clock: Clock):
        self._uow_factory = uow_factory
        self._defaults = defaults
        self._clock = clock

    def execute(
        self,
        name: str,
        *,
        mode: str,
        cash: object,
        fee_bps: object | None = None,
        strategy: dict | None = None,
    ) -> PortfolioView:
        """A new portfolio. A strategy never trades history: it starts after the newest signal."""
        now = self._clock.now()
        with self._uow_factory() as uow:
            portfolio = Portfolio.open(
                name,
                mode=mode,
                cash=cash,
                fee_bps=fee_bps,
                strategy=strategy,
                defaults=self._defaults.load(),
                now=now,
                last_signal_id=uow.signals.latest_id(),
            )
            uow.portfolios.add(portfolio)
            return _view(uow, portfolio, now)


class PlaceOrder:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self,
        portfolio_id: int,
        ticker: str,
        side: str,
        *,
        quantity: object | None = None,
        notional: object | None = None,
        now: datetime | None = None,
    ) -> Order:
        """Queue a simulated order; it fills at the first daily close after today. Manual
        portfolios only. ``NotFound`` for an unknown portfolio or ticker."""
        with self._uow_factory() as uow:
            portfolio = _get(uow, portfolio_id)
            portfolio.ensure_manual()
            try:
                symbol = parse_ticker(ticker)
            except NotFound:
                raise NotFound("ticker", ticker.upper()) from None
            security = uow.securities.get(symbol)
            if security is None or not security.active:
                raise NotFound("ticker", str(symbol))
            order = portfolio.place_order(
                symbol, side, now or self._clock.now(), quantity=quantity, notional=notional
            )
            uow.portfolios.save(portfolio)
            return order


class CancelOrder:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def execute(
        self, portfolio_id: int, order_id: int, reason: str = "cancelled by the user"
    ) -> bool:
        """``False`` if the order is unknown or no longer pending; ``NotFound`` for the
        portfolio."""
        with self._uow_factory() as uow:
            portfolio = _get(uow, portfolio_id)
            cancelled = portfolio.cancel_order(order_id, reason)
            if cancelled:
                uow.portfolios.save(portfolio)
            return cancelled


class RunSandbox:
    """One scheduled sandbox step: strategies read new signals, orders settle, values are
    snapshotted. Every portfolio in its own transaction, so one failure never blocks the others.
    Run after prices and scoring. Re-running the same day is harmless."""

    def __init__(self, uow_factory: UnitOfWorkFactory, tracker: JobRunTracker, clock: Clock):
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._clock = clock

    def execute(self, as_of: datetime | None = None) -> RunSummary:
        as_of = _aware(as_of or self._clock.now())
        with tracked_run(self._tracker, "sandbox") as summary:
            with self._uow_factory() as uow:
                ids = uow.portfolios.ids()
            for pid in ids:
                try:
                    with self._uow_factory() as uow:
                        self._step(uow, pid, as_of)
                    summary.rows += 1
                except Exception as exc:
                    log.exception("sandbox portfolio %s failed", pid)
                    summary.failed[f"portfolio {pid}"] = f"{type(exc).__name__}: {exc}"[:200]
            summary.status = run_status(summary, len(ids), aborted=False)
        return summary

    @staticmethod
    def _step(uow: UnitOfWork, portfolio_id: int, as_of: datetime) -> None:
        portfolio = uow.portfolios.load_exclusive(portfolio_id)  # serialises overlapping runs
        if portfolio.mode is PortfolioMode.STRATEGY:
            signals = uow.signals.after(portfolio.last_signal_id)
            book = load_price_book(uow, portfolio, as_of, [s.ticker for s in signals])
            portfolio.apply_signals(signals, book)
        portfolio.settle(load_price_book(uow, portfolio, as_of), as_of)
        snapshot = portfolio.snapshot(load_price_book(uow, portfolio, as_of), as_of)
        uow.portfolios.save(portfolio)
        uow.portfolios.save_snapshot(portfolio_id, snapshot)
