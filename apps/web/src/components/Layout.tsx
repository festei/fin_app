import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useSearch } from "../api/hooks";

function SearchBox() {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const results = useSearch(q.trim());
  const open = q.trim().length > 0;

  const go = (ticker: string) => {
    setQ("");
    navigate(`/security/${ticker}`);
  };

  return (
    <div className="search">
      <input
        type="search"
        aria-label="Search securities"
        placeholder="Search ticker or name"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results.data?.[0]) go(results.data[0].ticker);
          if (e.key === "Escape") setQ("");
        }}
      />
      {open && results.data && (
        <ul className="search-results" role="listbox">
          {results.data.length === 0 && <li className="muted">No match</li>}
          {results.data.map((s) => (
            <li key={s.ticker} role="option" aria-selected={false}>
              <button type="button" onClick={() => go(s.ticker)}>
                <strong>{s.ticker}</strong> <span className="muted">{s.name ?? ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const NAV = [
  ["/", "Opportunities"],
  ["/alerts", "Alerts"],
  ["/sandbox", "Sandbox"],
  ["/settings", "Settings"],
] as const;

export function Layout() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          fin-app
        </Link>
        <nav aria-label="Main">
          {NAV.map(([to, text]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {text}
            </NavLink>
          ))}
        </nav>
        <SearchBox />
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="foot muted">
        Prices are end-of-day and adjusted. Scores are a screening aid, simulations are not real
        trades. Not investment advice.
      </footer>
    </div>
  );
}
