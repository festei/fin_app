import { label, tone } from "../lib/format";

/** A 0-100 conviction as a number plus a bar, coloured against the buy (70) and sell (50) lines. */
export function Conviction({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">no view</span>;
  return (
    <span className={`conviction tone-${tone(value)}`} title={`conviction ${value.toFixed(1)}`}>
      <span className="bar" aria-hidden>
        <span className="fill" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
        <span className="line" style={{ left: "50%" }} />
        <span className="line" style={{ left: "70%" }} />
      </span>
      <strong>{value.toFixed(0)}</strong>
    </span>
  );
}

/** Per-philosophy scores as small chips, e.g. "Graham 57". */
export function PhilosophyChips({ scores }: { scores: Record<string, number> }) {
  const entries = Object.entries(scores);
  if (entries.length === 0) return <span className="muted">–</span>;
  return (
    <span className="chips">
      {entries.map(([name, score]) => (
        <span key={name} className={`chip tone-${tone(score)}`}>
          {label(name)} {score.toFixed(0)}
        </span>
      ))}
    </span>
  );
}
