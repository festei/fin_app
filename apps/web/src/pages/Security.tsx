import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { usePrices, useScore, useScoreHistory } from "../api/hooks";
import type { ScoreResult } from "../api/types";
import { ApiError } from "../api/client";
import { LineChart, PriceChart, type Marker } from "../components/Charts";
import { Conviction } from "../components/Conviction";
import { OrderTicket } from "../components/OrderTicket";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { WatchButton } from "../components/WatchButton";
import { day, label, money, tone } from "../lib/format";

const RANGES = [
  ["3M", 92],
  ["1Y", 365],
  ["5Y", 1825],
] as const;

function Philosophy({ result }: { result: ScoreResult }) {
  if (!result.evaluated) {
    return (
      <details className="card philosophy">
        <summary>
          <strong>{label(result.philosophy)}</strong> <span className="muted">not evaluated</span>
        </summary>
        <p className="muted">{result.explanation}</p>
      </details>
    );
  }
  const passed = result.criteria.filter((c) => c.passed).length;
  return (
    <details className="card philosophy">
      <summary>
        <strong>{label(result.philosophy)}</strong>{" "}
        <span className={`chip tone-${tone(result.score)}`}>{result.score.toFixed(0)}</span>{" "}
        <span className="muted">
          {passed}/{result.criteria.length} criteria
        </span>
      </summary>
      <ul className="criteria">
        {result.criteria.map((c) => (
          <li key={c.name} className={c.passed ? "pass" : "fail"}>
            <span aria-label={c.passed ? "passed" : "failed"}>{c.passed ? "✓" : "✗"}</span> {c.name}
            {c.actual != null && (
              <span className="muted">
                {" "}
                · {c.actual}
                {c.threshold != null ? ` (limit ${c.threshold})` : ""}
              </span>
            )}
            {c.detail && <div className="muted small">{c.detail}</div>}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function SecurityPage() {
  const ticker = (useParams().ticker ?? "").toUpperCase();
  const [range, setRange] = useState<number>(365);
  const score = useScore(ticker);
  const prices = usePrices(ticker, range);
  const history = useScoreHistory(ticker);

  const convictionPoints = useMemo(
    () =>
      (history.data?.scores ?? [])
        .filter((p) => p.conviction != null && !p.signal_blocked)
        .map((p) => ({ time: p.as_of, value: p.conviction as number })),
    [history.data],
  );
  const markers = useMemo<Marker[]>(
    () =>
      (history.data?.signals ?? []).map((s) => ({
        time: s.as_of,
        text: s.kind,
        tone: s.kind === "sell" ? "bad" : "good",
      })),
    [history.data],
  );
  const convictionRange = useMemo<[number, number]>(() => [0, 100], []);
  const refLines = useMemo(
    () => [
      { value: 70, title: "buy" },
      { value: 50, title: "sell" },
    ],
    [],
  );

  if (score.isLoading) return <Loading what={`Scoring ${ticker}`} />;
  if (score.error instanceof ApiError && score.error.status === 404) {
    return <Empty>Unknown ticker {ticker}. Use the search box to find a security.</Empty>;
  }
  if (score.error) return <ErrorBox error={score.error} />;
  const s = score.data;
  if (!s) return null;
  const m = s.metrics;
  // evaluated philosophies first (best score first), those that abstained last
  const byRelevance = (a: ScoreResult, b: ScoreResult) =>
    Number(b.evaluated) - Number(a.evaluated) || b.score - a.score;
  const philosophies = Object.values(s.philosophy_results).sort(byRelevance);
  const guardrails = Object.values(s.guardrail_results).sort(byRelevance);

  return (
    <>
      <div className="page-head">
        <h1>
          {ticker} <span className="muted small">{m.sector ?? ""}</span>
        </h1>
        <WatchButton ticker={ticker} />
      </div>

      <div className="grid two">
        <section className="card">
          <div className="stat-row">
            <div>
              <div className="muted small">Conviction</div>
              <div className="big">
                <Conviction value={s.conviction} />
              </div>
            </div>
            <div>
              <div className="muted small">Last close</div>
              <div className="big">{money(m.price)}</div>
              <div className="muted small">{day(m.price_date)}</div>
            </div>
            <div>
              <div className="muted small">P/E (3y avg) · P/B</div>
              <div className="big">
                {m.pe_avg3?.toFixed(1) ?? "–"} · {m.pb?.toFixed(1) ?? "–"}
              </div>
            </div>
          </div>
          {s.signal && (
            <p className={`banner tone-${s.signal.kind === "sell" ? "bad" : "good"}`}>
              Signal: <strong>{s.signal.kind}</strong> at {s.signal.conviction.toFixed(0)}
            </p>
          )}
          {m.signal_blockers.length > 0 && (
            <p className="banner tone-warn">
              Signals withheld: {m.signal_blockers.join("; ")}
            </p>
          )}
          <p className="muted small">{s.explanation}</p>
        </section>
        <OrderTicket ticker={ticker} price={m.price} />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Price</h2>
          <div className="segmented" role="group" aria-label="Range">
            {RANGES.map(([text, days]) => (
              <button
                key={text}
                type="button"
                className={range === days ? "active" : ""}
                aria-pressed={range === days}
                onClick={() => setRange(days)}
              >
                {text}
              </button>
            ))}
          </div>
        </div>
        {prices.isLoading && <Loading what="Loading prices" />}
        {prices.error && <ErrorBox error={prices.error} />}
        {prices.data && prices.data.length === 0 && <Empty>No price history stored.</Empty>}
        {prices.data && prices.data.length > 0 && <PriceChart bars={prices.data} />}
        <p className="muted small">Daily bars, adjusted for splits and dividends.</p>
      </section>

      <section className="card">
        <h2>Conviction history</h2>
        {history.isLoading && <Loading what="Loading history" />}
        {convictionPoints.length === 0 && !history.isLoading && (
          <Empty>No stored scores yet.</Empty>
        )}
        {convictionPoints.length > 0 && (
          <LineChart
            points={convictionPoints}
            markers={markers}
            lines={refLines}
            range={convictionRange}
          />
        )}
      </section>

      <h2>Why this score</h2>
      {philosophies.map((r) => (
        <Philosophy key={r.philosophy} result={r} />
      ))}
      {guardrails.length > 0 && <h3>Guardrails</h3>}
      {guardrails.map((r) => (
        <Philosophy key={r.philosophy} result={r} />
      ))}

      {m.warnings.length > 0 && (
        <section className="card">
          <h3>Data notes</h3>
          <ul>
            {m.warnings.map((w) => (
              <li key={w} className="muted">
                {w}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
