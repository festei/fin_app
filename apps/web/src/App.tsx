import { useQuery } from "@tanstack/react-query";
import { fetchFeatures } from "./api";

export function App() {
  const features = useQuery({ queryKey: ["features"], queryFn: fetchFeatures });

  return (
    <main className="shell">
      <h1>fin-app</h1>
      <p className="muted">
        Opportunity scanner. Screener, security detail, bonds and sandbox arrive in later phases.
      </p>
      <section>
        <h2>Feature flags</h2>
        {features.isLoading && <p>Loading…</p>}
        {features.isError && <p role="alert">API unreachable.</p>}
        {features.data && (
          <ul>
            {Object.entries(features.data).map(([name, on]) => (
              <li key={name}>
                {name}: <strong>{on ? "on" : "off"}</strong>
              </li>
            ))}
          </ul>
        )}
      </section>
      <footer className="muted">Not investment advice.</footer>
    </main>
  );
}
