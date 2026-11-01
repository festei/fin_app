import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCancelOrder, useOrders, usePortfolio, usePortfolioHistory } from "../api/hooks";
import { ApiError } from "../api/client";
import { LineChart } from "../components/Charts";
import { OrderTicket } from "../components/OrderTicket";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { day, money, pct, qty } from "../lib/format";

export function PortfolioPage() {
  const id = Number(useParams().id);
  const portfolio = usePortfolio(id);
  const history = usePortfolioHistory(id);
  const orders = useOrders(id);
  const cancel = useCancelOrder(id);

  const points = useMemo(
    () => (history.data ?? []).map((s) => ({ time: s.as_of, value: s.total_value })),
    [history.data],
  );

  if (portfolio.isLoading) return <Loading what="Loading portfolio" />;
  if (portfolio.error instanceof ApiError && portfolio.error.status === 404) {
    return <Empty>Unknown portfolio. <Link to="/sandbox">Back to the sandbox</Link>.</Empty>;
  }
  if (portfolio.error) return <ErrorBox error={portfolio.error} />;
  const p = portfolio.data;
  if (!p) return null;

  return (
    <>
      <div className="page-head">
        <h1>
          {p.name} <span className="pill">{p.mode}</span>
        </h1>
        <Link to="/sandbox">All portfolios</Link>
      </div>

      <section className="card stat-row">
        <div>
          <div className="muted small">Total value</div>
          <div className="big">{money(p.total_value)}</div>
        </div>
        <div>
          <div className="muted small">Return</div>
          <div className={`big ${p.return_pct >= 0 ? "pos" : "neg"}`}>{pct(p.return_pct, 2)}</div>
        </div>
        <div>
          <div className="muted small">Cash</div>
          <div className="big">{money(p.cash)}</div>
        </div>
        <div>
          <div className="muted small">Started with</div>
          <div className="big">{money(p.initial_cash)}</div>
        </div>
      </section>

      <section className="card">
        <h2>Equity curve</h2>
        {points.length < 2 ? (
          <Empty>
            The curve fills in as the daily sandbox step records each day's value
            {points.length === 1 ? " (one day so far)" : ""}.
          </Empty>
        ) : (
          <LineChart points={points} />
        )}
      </section>

      <div className="grid two">
        <section className="card">
          <h2>Positions</h2>
          {p.positions.length === 0 ? (
            <Empty>Nothing held.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Security</th>
                    <th className="num">Shares</th>
                    <th className="num">Avg cost</th>
                    <th className="num">Last</th>
                    <th className="num">Value</th>
                    <th className="num">P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {p.positions.map((pos) => (
                    <tr key={pos.ticker}>
                      <td>
                        <Link to={`/security/${pos.ticker}`} className="ticker">
                          {pos.ticker}
                        </Link>
                      </td>
                      <td className="num">{qty(pos.quantity)}</td>
                      <td className="num">{money(pos.avg_cost)}</td>
                      <td className="num">{money(pos.price)}</td>
                      <td className="num">{money(pos.market_value)}</td>
                      <td className={`num ${pos.pnl >= 0 ? "pos" : "neg"}`}>
                        {money(pos.pnl)} <span className="small">{pct(pos.pnl_pct)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        {p.mode === "manual" ? (
          <TicketWithTicker portfolioId={p.id} />
        ) : (
          <section className="card">
            <h3>Strategy</h3>
            <p className="muted small">
              This portfolio trades from signals; it ignores manual orders. Settings:
            </p>
            <pre className="small">{JSON.stringify(p.strategy_config, null, 2)}</pre>
          </section>
        )}
      </div>

      <section className="card">
        <h2>Orders</h2>
        {orders.isLoading && <Loading what="Loading orders" />}
        {orders.data && orders.data.length === 0 && <Empty>No orders yet.</Empty>}
        {orders.data && orders.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Placed</th>
                  <th>Order</th>
                  <th>Status</th>
                  <th className="num">Fill</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {orders.data.map((o) => (
                  <tr key={o.id}>
                    <td>{day(o.created_at)}</td>
                    <td>
                      <span className={`pill side-${o.side}`}>{o.side}</span>{" "}
                      <Link to={`/security/${o.ticker}`} className="ticker">
                        {o.ticker}
                      </Link>{" "}
                      <span className="muted small">
                        {o.quantity != null
                          ? `${qty(o.quantity)} sh`
                          : o.notional != null
                            ? money(o.notional)
                            : "all"}
                        {o.signal_id != null ? " · from signal" : ""}
                      </span>
                    </td>
                    <td>
                      <span className={`pill status-${o.status}`}>{o.status}</span>
                      {o.reason && <div className="muted small">{o.reason}</div>}
                    </td>
                    <td className="num">
                      {o.status === "filled"
                        ? `${qty(o.fill_quantity)} @ ${money(o.fill_price)} · ${day(o.filled_at)}`
                        : "–"}
                    </td>
                    <td>
                      {o.status === "pending" && (
                        <button
                          type="button"
                          onClick={() => cancel.mutate(o.id)}
                          disabled={cancel.isPending}
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

/** On the portfolio page the user types the ticker, then the shared order form takes over. */
function TicketWithTicker({ portfolioId }: { portfolioId: number }) {
  const [ticker, setTicker] = useState("");
  return (
    <div>
      <label className="card">
        Ticker to trade
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="e.g. AAPL"
          aria-label="Ticker to trade"
        />
      </label>
      {ticker.trim() && <OrderTicket ticker={ticker.trim()} portfolioId={portfolioId} />}
    </div>
  );
}
