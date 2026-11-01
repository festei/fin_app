const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });

export const money = (x: number | null | undefined): string => (x == null ? "–" : usd.format(x));
export const qty = (x: number | null | undefined): string => (x == null ? "–" : num.format(x));

export function pct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "–";
  const s = x.toFixed(digits);
  return `${x > 0 ? "+" : ""}${s}%`;
}

export function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "–";
}

/** "3 days ago" style age for a timestamp, relative to `now`. */
export function age(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "–";
  const t = Date.parse(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "–";
  const hours = Math.max(0, (now - t) / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Buy line is 70 and sell line is 50 (config in the backend); colour the conviction by them. */
export function tone(conviction: number | null | undefined): "good" | "warn" | "bad" | "none" {
  if (conviction == null) return "none";
  if (conviction >= 70) return "good";
  if (conviction >= 50) return "warn";
  return "bad";
}

export const label = (name: string): string => name.charAt(0).toUpperCase() + name.slice(1);
