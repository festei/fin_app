"""In-memory sandbox repository. Same rules as the SQL one (the shared contract suite in
``packages/persistence/tests`` runs against both)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from fin_domain.market_data import Security
from fin_domain.sandbox import Order, Portfolio, PortfolioSnapshot
from fin_domain.shared import NotFound, Ticker


class InMemoryPortfolioRepository:
    def __init__(
        self,
        portfolios: dict[int, Portfolio],
        snapshots: dict[tuple[int, date], PortfolioSnapshot],
        securities: dict[Ticker, Security],
    ):
        self.portfolios, self.snapshots, self._securities = portfolios, snapshots, securities

    def add(self, portfolio: Portfolio) -> Portfolio:
        portfolio.id = max(self.portfolios, default=0) + 1
        self._number_orders(portfolio)
        self.portfolios[portfolio.id] = deepcopy(portfolio)
        return portfolio

    def _number_orders(self, portfolio: Portfolio) -> None:
        next_id = max((o.id for p in self.portfolios.values() for o in p.orders), default=0)
        for order in portfolio.orders:
            if order.id is None:
                if order.ticker not in self._securities:
                    raise NotFound("security", str(order.ticker))
                next_id += 1
                order.id = next_id

    def get(self, portfolio_id: int) -> Portfolio | None:
        found = self.portfolios.get(portfolio_id)
        return deepcopy(found) if found else None

    def load_exclusive(self, portfolio_id: int) -> Portfolio:
        found = self.get(portfolio_id)
        if found is None:
            raise NotFound("portfolio", portfolio_id)
        return found

    def list(self) -> list[Portfolio]:
        return [deepcopy(self.portfolios[i]) for i in sorted(self.portfolios)]

    def ids(self) -> list[int]:
        return sorted(self.portfolios)

    def save(self, portfolio: Portfolio) -> None:
        if portfolio.id not in self.portfolios:
            raise LookupError(f"portfolio {portfolio.id!r} was never added")
        self._number_orders(portfolio)
        self.portfolios[portfolio.id] = deepcopy(portfolio)

    def orders(self, portfolio_id: int, status: str | None = None, limit: int = 100) -> list[Order]:
        rows = [
            o for o in self.portfolios[portfolio_id].orders if status is None or o.status == status
        ]
        return [deepcopy(o) for o in sorted(rows, key=lambda o: -o.id)[:limit]]

    def save_snapshot(self, portfolio_id: int, snapshot: PortfolioSnapshot) -> None:
        self.snapshots[(portfolio_id, snapshot.as_of)] = snapshot

    def history(self, portfolio_id: int) -> list[PortfolioSnapshot]:
        return [s for (pid, _), s in sorted(self.snapshots.items()) if pid == portfolio_id]


class FixedSandboxConfig:
    """A ``SandboxConfigSource`` that always answers with the defaults it was given."""

    def __init__(self, rules=None):
        from fin_domain.sandbox import StrategyRules

        self.rules = rules or StrategyRules()

    def load(self):
        return self.rules
