// Shapes returned by the FastAPI backend (apps/api/src/fin_api/main.py).

export type Features = Record<string, boolean>;

export interface Criterion {
  name: string;
  passed: boolean;
  actual: number | null;
  threshold: number | null;
  detail: string;
}

export interface ScoreResult {
  philosophy: string;
  score: number;
  criteria: Criterion[];
  explanation: string;
  evidence: string[];
  impl: "deterministic" | "llm";
  evaluated: boolean;
}

export interface Signal {
  ticker: string;
  kind: "buy" | "sell" | "watch" | "emerging";
  conviction: number;
  as_of: string;
  reason: string;
}

export interface SecurityMetrics {
  ticker: string;
  as_of: string;
  price: number | null;
  price_date: string | null;
  pe: number | null;
  pe_avg3: number | null;
  pb: number | null;
  sector: string | null;
  warnings: string[];
  signal_blockers: string[];
}

export interface TickerScore {
  conviction: number | null;
  all_abstained: boolean;
  explanation: string;
  philosophy_results: Record<string, ScoreResult>;
  guardrail_results: Record<string, ScoreResult>;
  signal: Signal | null;
  metrics: SecurityMetrics;
}

export interface Opportunity {
  ticker: string;
  name: string | null;
  sector: string | null;
  conviction: number;
  computed_at: string | null;
  philosophies: Record<string, number>;
  price: number | null;
  price_date: string | null;
}

export interface SecurityItem {
  ticker: string;
  name: string | null;
  sector: string | null;
  kind: "stock" | "etf";
  watched: boolean;
}

export interface Bar {
  ts: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

export interface ScorePoint {
  computed_at: string | null;
  as_of: string;
  conviction: number | null;
  signal_blocked: boolean;
  all_abstained: boolean;
}

export interface SignalPoint {
  kind: string;
  conviction: number;
  as_of: string;
}

export interface ScoreHistory {
  scores: ScorePoint[];
  signals: SignalPoint[];
}

export interface AlertItem {
  id: number;
  ticker: string;
  kind: string;
  severity: string;
  conviction: number;
  title: string;
  body: string;
  created_at: string | null;
  read: boolean;
  deliveries: Record<string, string>;
}

export interface AlertsPage {
  items: AlertItem[];
  next_before_id: number | null;
}

export interface Channel {
  name: string;
  enabled: boolean;
  muted_until: string | null;
}

export type PortfolioMode = "manual" | "strategy";

export interface Portfolio {
  id: number;
  name: string;
  mode: PortfolioMode;
  initial_cash: number;
  cash: number;
  positions_value: number;
  total_value: number;
  return_pct: number;
  fee_bps: number;
  strategy_config: Record<string, unknown> | null;
  created_at: string | null;
}

export interface Position {
  ticker: string;
  name: string | null;
  quantity: number;
  avg_cost: number;
  price: number | null;
  market_value: number;
  pnl: number;
  pnl_pct: number | null;
}

export interface PortfolioDetail extends Portfolio {
  positions: Position[];
}

export interface Order {
  id: number;
  ticker: string;
  side: "buy" | "sell";
  quantity: number | null;
  notional: number | null;
  status: "pending" | "filled" | "rejected" | "cancelled";
  created_at: string;
  filled_at: string | null;
  fill_quantity: number | null;
  fill_price: number | null;
  fee: number | null;
  reason: string | null;
  signal_id: number | null;
}

export interface Snapshot {
  as_of: string;
  cash: number;
  positions_value: number;
  total_value: number;
}

export interface NewOrder {
  ticker: string;
  side: "buy" | "sell";
  quantity?: number;
  notional?: number;
}

export interface NewPortfolio {
  name: string;
  mode: PortfolioMode;
  cash: number;
}
