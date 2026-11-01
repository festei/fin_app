import { useState } from "react";
import { Link } from "react-router-dom";
import { useCreatePortfolio, usePortfolios } from "../api/hooks";
import type { PortfolioMode } from "../api/types";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { money, pct } from "../lib/format";

function NewPortfolioForm() {
  const create = useCreatePortfolio();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<PortfolioMode>("strategy");
  const [cash, setCash] = useState("100");
  const amount = Number(cash);
  const valid = name.trim().length > 0 && Number.isFinite(amount) && amount > 0;

  return (
    <form
      className="card form-row"
      aria-label="New portfolio"
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        create.mutate(
          { name: name.trim(), mode, cash: amount },
          { onSuccess: () => setName("") },
        );
      }}
    >
      <h3>New portfolio</h3>
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My $100" />
      </label>
      <label>
        Starting cash (USD)
        <input inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} />
      </label>
      <label>
        Mode
        <select value={mode} onChange={(e) => setMode(e.target.value as PortfolioMode)}>
          <option value="strategy">Strategy: trades from signals</option>
          <option value="manual">Manual: you place the orders</option>
        </select>
      </label>
      <button type="submit" className="primary" disabled={!valid || create.isPending}>
        {create.isPending ? "Creating…" : "Create"}
      </button>
      {create.isError && (
        <p className="error" role="alert">
          {create.error.message}
        </p>
      )}
    </form>
  );
}

export function Sandbox() {
  const { data, isLoading, error } = usePortfolios();
  return (
    <>
      <div className="page-head">
        <h1>Sandbox</h1>
      </div>
      <p className="muted">
        Simulate what a small portfolio would have done. Orders fill at the first daily close after
        the day they are placed; strategy portfolios buy on buy/emerging signals and sell on sell
        signals. Simulation only.
      </p>
      <NewPortfolioForm />
      {isLoading && <Loading what="Loading portfolios" />}
      {error && <ErrorBox error={error} />}
      {data && data.length === 0 && <Empty>No portfolios yet. Create one above.</Empty>}
      <div className="grid cards">
        {data?.map((p) => (
          <Link key={p.id} to={`/sandbox/${p.id}`} className="card portfolio-card">
            <div className="card-head">
              <strong>{p.name}</strong>
              <span className="pill">{p.mode}</span>
            </div>
            <div className="big">{money(p.total_value)}</div>
            <div className={p.return_pct >= 0 ? "pos" : "neg"}>{pct(p.return_pct, 2)}</div>
            <div className="muted small">
              {money(p.cash)} cash · {money(p.positions_value)} invested
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
