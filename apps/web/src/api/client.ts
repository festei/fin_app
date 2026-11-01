import type {
  AlertsPage,
  Bar,
  Channel,
  Features,
  NewOrder,
  NewPortfolio,
  Opportunity,
  Order,
  Portfolio,
  PortfolioDetail,
  ScoreHistory,
  SecurityItem,
  Snapshot,
  TickerScore,
} from "./types";

/** A failed API call. `detail` is the backend's message when it sent one. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

function describe(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{loc, msg, type}]
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : "")).join("; ");
  }
  return `request failed (${status})`;
}

/**
 * The browser never holds the write token: the proxy (nginx in production, Vite in development)
 * adds `Authorization` to /api requests, so no credentials appear here.
 */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // X-Requested-With: the proxy refuses state-changing requests without it, which a cross-site
  // form post cannot send (CSRF guard; see deploy/nginx.conf.template).
  const headers: Record<string, string> = { Accept: "application/json", "X-Requested-With": "fin-app" };
  if (init.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as object) } });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, describe(detail, res.status));
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });
const enc = encodeURIComponent;

export const api = {
  features: () => request<Features>("/api/features"),
  channels: () => request<Channel[]>("/api/channels"),
  opportunities: (limit = 50) => request<Opportunity[]>(`/api/opportunities?limit=${limit}`),
  watchlist: () => request<{ tickers: string[] }>("/api/watchlist"),
  watch: (ticker: string) =>
    request<{ ticker: string; added: boolean }>(`/api/watchlist/${enc(ticker)}`, { method: "PUT" }),
  unwatch: (ticker: string) =>
    request<{ ticker: string; removed: boolean }>(`/api/watchlist/${enc(ticker)}`, {
      method: "DELETE",
    }),
  searchSecurities: (q: string, limit = 8) =>
    request<SecurityItem[]>(`/api/securities?q=${enc(q)}&limit=${limit}`),
  score: (ticker: string) => request<TickerScore>(`/api/securities/${enc(ticker)}/score`),
  prices: (ticker: string, days: number) =>
    request<Bar[]>(`/api/securities/${enc(ticker)}/prices?days=${days}&max_points=600`),
  scoreHistory: (ticker: string, days = 730) =>
    request<ScoreHistory>(`/api/securities/${enc(ticker)}/scores?days=${days}`),
  alerts: (opts: { beforeId?: number; unreadOnly?: boolean } = {}) => {
    const p = new URLSearchParams({ limit: "30" });
    if (opts.beforeId) p.set("before_id", String(opts.beforeId));
    if (opts.unreadOnly) p.set("unread_only", "true");
    return request<AlertsPage>(`/api/alerts?${p}`);
  },
  markRead: (id: number) =>
    request<{ id: number; read: boolean }>(`/api/alerts/${id}/read`, { method: "POST" }),
  portfolios: () => request<Portfolio[]>("/api/portfolios"),
  portfolio: (id: number) => request<PortfolioDetail>(`/api/portfolios/${id}`),
  history: (id: number) => request<Snapshot[]>(`/api/portfolios/${id}/history`),
  orders: (id: number) => request<Order[]>(`/api/portfolios/${id}/orders?limit=100`),
  createPortfolio: (body: NewPortfolio) =>
    request<Portfolio>("/api/portfolios", { method: "POST", ...json(body) }),
  placeOrder: (portfolioId: number, body: NewOrder) =>
    request<Order>(`/api/portfolios/${portfolioId}/orders`, { method: "POST", ...json(body) }),
  cancelOrder: (portfolioId: number, orderId: number) =>
    request<{ id: number; cancelled: boolean }>(`/api/portfolios/${portfolioId}/orders/${orderId}`, {
      method: "DELETE",
    }),
};
