import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { usePlaceOrder, usePortfolios } from "../api/hooks";
import { money } from "../lib/format";

/** Place a simulated order. It fills at the first daily close AFTER today (no look-ahead). */
export function OrderTicket({
  ticker,
  portfolioId,
  price,
}: {
  ticker: string;
  portfolioId?: number; // fixed portfolio (portfolio page) or chosen here (security page)
  price?: number | null;
}) {
  const portfolios = usePortfolios();
  const manual = (portfolios.data ?? []).filter((p) => p.mode === "manual");
  const [chosen, setChosen] = useState<number | undefined>(portfolioId);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [amount, setAmount] = useState("");
  const [sellAll, setSellAll] = useState(false);

  useEffect(() => {
    if (portfolioId == null && chosen == null && manual.length > 0) setChosen(manual[0].id);
  }, [portfolioId, chosen, manual]);

  const target = portfolioId ?? chosen;
  const place = usePlaceOrder(target ?? 0);

  if (portfolioId == null && portfolios.isSuccess && manual.length === 0) {
    return (
      <section className="card">
        <h3>Simulated order</h3>
        <p className="muted">
          You need a manual sandbox portfolio first. <Link to="/sandbox">Create one</Link>.
        </p>
      </section>
    );
  }

  const value = Number(amount);
  const valid = sellAll || (Number.isFinite(value) && value > 0);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (target == null || !valid) return;
    place.mutate(
      {
        ticker,
        side,
        ...(side === "sell" && sellAll ? {} : { notional: value }),
      },
      {
        onSuccess: () => {
          setAmount("");
          setSellAll(false);
        },
      },
    );
  };

  return (
    <form className="card order-ticket" onSubmit={submit} aria-label="Simulated order">
      <h3>Simulated order · {ticker}</h3>
      {portfolioId == null && (
        <label>
          Portfolio
          <select value={chosen ?? ""} onChange={(e) => setChosen(Number(e.target.value))}>
            {manual.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({money(p.cash)} cash)
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="segmented" role="group" aria-label="Side">
        {(["buy", "sell"] as const).map((s) => (
          <button
            key={s}
            type="button"
            className={side === s ? `active ${s}` : ""}
            aria-pressed={side === s}
            onClick={() => setSide(s)}
          >
            {s === "buy" ? "Buy" : "Sell"}
          </button>
        ))}
      </div>
      {side === "sell" && (
        <label className="check">
          <input type="checkbox" checked={sellAll} onChange={(e) => setSellAll(e.target.checked)} />
          Sell the whole position
        </label>
      )}
      {!(side === "sell" && sellAll) && (
        <label>
          Amount (USD)
          <input
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="e.g. 20"
            aria-label="Amount in USD"
          />
        </label>
      )}
      <p className="muted small">
        Fills at the first daily close after today{price != null ? ` (last close ${money(price)})` : ""}
        , fractional shares allowed.
      </p>
      <button type="submit" className="primary" disabled={!valid || place.isPending || target == null}>
        {place.isPending ? "Placing…" : `Place ${side} order`}
      </button>
      {place.isError && (
        <p className="error" role="alert">
          {place.error.message}
        </p>
      )}
      {place.isSuccess && (
        <p className="ok" role="status">
          Order #{place.data.id} queued ({place.data.status}).
        </p>
      )}
    </form>
  );
}
