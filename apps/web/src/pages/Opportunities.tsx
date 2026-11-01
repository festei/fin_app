import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useOpportunities } from "../api/hooks";
import type { Opportunity } from "../api/types";
import { Conviction, PhilosophyChips } from "../components/Conviction";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { WatchButton } from "../components/WatchButton";
import { age, money } from "../lib/format";

type SortKey = "conviction" | "ticker" | "price" | "age";

export function Opportunities() {
  const { data, isLoading, error } = useOpportunities();
  const [sector, setSector] = useState("");
  const [sort, setSort] = useState<SortKey>("conviction");

  const sectors = useMemo(
    () => [...new Set((data ?? []).map((o) => o.sector).filter((s): s is string => !!s))].sort(),
    [data],
  );

  const rows = useMemo(() => {
    const list = (data ?? []).filter((o) => !sector || o.sector === sector);
    const by: Record<SortKey, (a: Opportunity, b: Opportunity) => number> = {
      conviction: (a, b) => b.conviction - a.conviction,
      ticker: (a, b) => a.ticker.localeCompare(b.ticker),
      price: (a, b) => (b.price ?? 0) - (a.price ?? 0),
      age: (a, b) => (b.computed_at ?? "").localeCompare(a.computed_at ?? ""),
    };
    return [...list].sort(by[sort]);
  }, [data, sector, sort]);

  return (
    <>
      <div className="page-head">
        <h1>Opportunities</h1>
        <div className="controls">
          <label>
            Sector
            <select value={sector} onChange={(e) => setSector(e.target.value)}>
              <option value="">All</option>
              {sectors.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Sort by
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="conviction">Conviction</option>
              <option value="ticker">Ticker</option>
              <option value="price">Price</option>
              <option value="age">Newest score</option>
            </select>
          </label>
        </div>
      </div>
      <p className="muted">
        Latest score per stock, ranked by conviction. 70+ is a buy candidate, below 50 is weak.
        Stale or unevaluable scores are left out.
      </p>
      {isLoading && <Loading what="Loading opportunities" />}
      {error && <ErrorBox error={error} />}
      {data && rows.length === 0 && <Empty>No scored securities yet. Run the daily pipeline.</Empty>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th aria-label="Watch" />
                <th>Security</th>
                <th>Conviction</th>
                <th className="col-phil">Philosophies</th>
                <th className="num">Price</th>
                <th className="num">Scored</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.ticker}>
                  <td>
                    <WatchButton ticker={o.ticker} compact />
                  </td>
                  <td>
                    <Link to={`/security/${o.ticker}`} className="ticker">
                      {o.ticker}
                    </Link>
                    <div className="muted small">
                      {o.name ?? ""}
                      {o.sector ? ` · ${o.sector}` : ""}
                    </div>
                  </td>
                  <td>
                    <Conviction value={o.conviction} />
                  </td>
                  <td className="col-phil">
                    <PhilosophyChips scores={o.philosophies} />
                  </td>
                  <td className="num">{money(o.price)}</td>
                  <td className="num muted">{age(o.computed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
