import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../App";

type Handler = unknown | ((init: RequestInit | undefined, url: string) => unknown);
export type Routes = Record<string, Handler>;

export interface Call {
  method: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
}

/**
 * Replace fetch with canned API answers. Keys are "METHOD /path" (the query string is ignored);
 * a value can be a function of (init, url). Unmatched requests fail the test loudly.
 */
export function mockApi(routes: Routes) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url.split("?")[0]}`;
    calls.push({
      method,
      url,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    });
    if (!(key in routes)) throw new Error(`unexpected request: ${method} ${url}`);
    const handler = routes[key];
    const value = typeof handler === "function" ? handler(init, url) : handler;
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), { status: 200 });
  });
  return calls;
}

export const fail = (status: number, detail: unknown) =>
  new Response(JSON.stringify({ detail }), { status });

export function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[path]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export const opportunity = (ticker: string, conviction: number, extra = {}) => ({
  ticker,
  name: `${ticker} Corp`,
  sector: "Technology",
  conviction,
  computed_at: new Date().toISOString(),
  philosophies: { graham: 60, lynch: 100 },
  price: 25,
  price_date: "2026-10-30",
  ...extra,
});
