"""SQL repository of the Trading Sandbox: portfolios with their orders, and the value snapshots."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_domain.sandbox import (
    Order,
    OrderSide,
    OrderStatus,
    Portfolio,
    PortfolioMode,
    PortfolioSnapshot,
    StrategyRules,
)
from fin_domain.shared import NotFound, Ticker
from fin_persistence.job_runs import aware
from fin_persistence.tables import Portfolio as PortfolioRow
from fin_persistence.tables import PortfolioSnapshot as SnapshotRow
from fin_persistence.tables import SandboxOrder as OrderRow
from fin_persistence.tables import Security as SecurityRow


def _order(row: OrderRow, ticker: str) -> Order:
    return Order(
        ticker=Ticker(ticker),
        side=OrderSide(row.side),
        created_at=aware(row.created_at),
        quantity=row.quantity,
        notional=row.notional,
        status=OrderStatus(row.status),
        signal_id=row.signal_id,
        filled_at=aware(row.filled_at) if row.filled_at else None,
        fill_quantity=row.fill_quantity,
        fill_price=row.fill_price,
        fee=row.fee,
        reason=row.reason,
        id=row.id,
    )


def _state(o: Order) -> tuple:
    """What can change about an order after it was placed."""
    return (o.status, o.reason, o.filled_at, o.fill_quantity, o.fill_price, o.fee)


class SqlPortfolioRepository:
    def __init__(self, session: Session):
        self._session = session
        self._loaded: dict[
            int, tuple
        ] = {}  # order id -> state when loaded: only changes are written

    # --- reading --------------------------------------------------------------------------------

    def _orders(self, portfolio_id: int) -> list[Order]:
        rows = self._session.execute(
            select(OrderRow, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == OrderRow.security_id)
            .where(OrderRow.portfolio_id == portfolio_id)
            .order_by(OrderRow.id)
        ).all()
        orders = [_order(r, t) for r, t in rows]
        self._loaded.update({o.id: _state(o) for o in orders})
        return orders

    def _portfolio(self, row: PortfolioRow) -> Portfolio:
        return Portfolio(
            name=row.name,
            mode=PortfolioMode(row.mode),
            initial_cash=row.initial_cash,
            cash=row.cash,
            fee_bps=row.fee_bps,
            created_at=aware(row.created_at),
            strategy=StrategyRules.from_dict(row.strategy_config or {})
            if row.mode == PortfolioMode.STRATEGY.value
            else None,
            last_signal_id=row.last_signal_id,
            orders=self._orders(row.id),
            id=row.id,
        )

    def get(self, portfolio_id: int) -> Portfolio | None:
        row = self._session.get(PortfolioRow, portfolio_id)
        return self._portfolio(row) if row else None

    def load_exclusive(self, portfolio_id: int) -> Portfolio:
        row = self._session.scalar(
            select(PortfolioRow)
            .where(PortfolioRow.id == portfolio_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("portfolio", portfolio_id)
        return self._portfolio(row)

    def list(self) -> list[Portfolio]:
        rows = self._session.scalars(select(PortfolioRow).order_by(PortfolioRow.id))
        return [self._portfolio(r) for r in rows]

    def ids(self) -> list[int]:
        return list(self._session.scalars(select(PortfolioRow.id).order_by(PortfolioRow.id)))

    def orders(self, portfolio_id: int, status: str | None = None, limit: int = 100) -> list[Order]:
        q = (
            select(OrderRow, SecurityRow.ticker)
            .join(SecurityRow, SecurityRow.id == OrderRow.security_id)
            .where(OrderRow.portfolio_id == portfolio_id)
        )
        if status:
            q = q.where(OrderRow.status == status)
        rows = self._session.execute(q.order_by(OrderRow.id.desc()).limit(limit)).all()
        return [_order(r, t) for r, t in rows]

    def history(self, portfolio_id: int) -> list[PortfolioSnapshot]:
        rows = self._session.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.portfolio_id == portfolio_id)
            .order_by(SnapshotRow.as_of)
        )
        return [PortfolioSnapshot(r.as_of, r.cash, r.positions_value, r.total_value) for r in rows]

    # --- writing --------------------------------------------------------------------------------

    def add(self, portfolio: Portfolio) -> Portfolio:
        row = PortfolioRow(
            name=portfolio.name,
            mode=portfolio.mode.value,
            initial_cash=portfolio.initial_cash,
            cash=portfolio.cash,
            fee_bps=portfolio.fee_bps,
            strategy_config=portfolio.strategy.to_json() if portfolio.strategy else None,
            last_signal_id=portfolio.last_signal_id,
            created_at=portfolio.created_at,
        )
        self._session.add(row)
        self._session.flush()
        portfolio.id = row.id
        self._write_orders(portfolio)
        return portfolio

    def _write_orders(self, portfolio: Portfolio) -> None:
        for order in portfolio.orders:
            if order.id is None:
                sid = self._session.scalar(
                    select(SecurityRow.id).where(SecurityRow.ticker == str(order.ticker))
                )
                if sid is None:
                    raise NotFound("security", str(order.ticker))
                row = OrderRow(
                    portfolio_id=portfolio.id,
                    security_id=sid,
                    side=order.side.value,
                    quantity=order.quantity,
                    notional=order.notional,
                    status=order.status.value,
                    created_at=order.created_at,
                    signal_id=order.signal_id,
                    filled_at=order.filled_at,
                    fill_quantity=order.fill_quantity,
                    fill_price=order.fill_price,
                    fee=order.fee,
                    reason=order.reason,
                )
                self._session.add(row)
                self._session.flush()
                order.id = row.id
                self._loaded[row.id] = _state(order)
            elif self._loaded.get(order.id) != _state(order):
                row = self._session.get(OrderRow, order.id)
                row.status = order.status.value
                row.reason = order.reason
                row.filled_at = order.filled_at
                row.fill_quantity, row.fill_price, row.fee = (
                    order.fill_quantity,
                    order.fill_price,
                    order.fee,
                )
                self._loaded[order.id] = _state(order)
        self._session.flush()

    def save(self, portfolio: Portfolio) -> None:
        row = self._session.get(PortfolioRow, portfolio.id) if portfolio.id is not None else None
        if row is None:
            raise LookupError(f"portfolio {portfolio.id!r} was never added")
        row.cash = portfolio.cash
        row.last_signal_id = portfolio.last_signal_id
        self._write_orders(portfolio)

    def save_snapshot(self, portfolio_id: int, snapshot: PortfolioSnapshot) -> None:
        row = self._session.get(SnapshotRow, (portfolio_id, snapshot.as_of))
        if row is None:
            row = SnapshotRow(portfolio_id=portfolio_id, as_of=snapshot.as_of)
            self._session.add(row)
        row.cash = snapshot.cash
        row.positions_value = snapshot.positions_value
        row.total_value = snapshot.total_value
        self._session.flush()
